import asyncio
from collections.abc import Awaitable, Callable, Coroutine
from contextlib import asynccontextmanager
from functools import partial
from itertools import count
import os
from pathlib import Path
import sqlite3
from typing import Annotated, Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from pydantic import Field, StrictStr, UUID4, ValidationError
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse, Response

from .ai import (
    TranslationConfigurationError, build_question_client, build_translation_client, generate_brief_proposal,
    generate_questions, translate_listing,
)
from .archive import IntakeError, MAX_ZIP_BYTES, MiB
from .case_models import (
    BriefCorrection, BriefEdit, ConfirmBrief, ContinueCase, PrepareBrief, ResetCase, ResumeQuestions, RetryCase,
    StartCase, UndoBriefPreview,
)
from .case_workflow import BriefGenerator, CaseCommandError, CaseWorkflow, QuestionGenerator, ReviewGenerator
from .contracts import ContractModel, IntakeFailed, IntakeReady, ListingSnapshot
from .intake import ParsedListing, parse_listing
from .link_intake import LINK_HINT, fetch_listing, parse_product_link
from .report import build_review_client, generate_review
from .state import Store


DEMO_ORIGIN = "http://127.0.0.1:5173"
MAX_IMPORT_BODY = 21 * MiB
DEFAULT_DATABASE = Path(__file__).resolve().parent / ".local" / "purchase.sqlite3"
Translator = Callable[[ListingSnapshot], Awaitable[ListingSnapshot]]
CASE_COMMANDS = frozenset({"/api/questions", "/api/questions/continue", "/api/reviews", "/api/case/retry", "/api/case/reset"})


class LinkRequest(ContractModel):
    url: Annotated[StrictStr, Field(max_length=512)]


class SelectionRequest(ContractModel):
    operation_id: Annotated[UUID4, Field(strict=False)]
    snapshot_id: Annotated[StrictStr, Field(min_length=1, max_length=128)]
    previous_case: None
    selected_option_ids: Annotated[list[Annotated[StrictStr, Field(min_length=1, max_length=128)]],
                                   Field(min_length=1, max_length=3)]
    buyer_context: Annotated[StrictStr, Field(max_length=2_000)]


def _intake_failure(code: str, message: str, status: int = 200) -> JSONResponse:
    failed = IntakeFailed.model_validate({"code": code, "message": message})
    return JSONResponse(failed.model_dump(), status_code=status)


def _invalid_request(message: str, status: int) -> JSONResponse:
    return JSONResponse({"status": "failed", "code": "invalid_input", "message": message}, status_code=status)


def _case_failure(error: CaseCommandError) -> JSONResponse:
    case = error.case
    return JSONResponse({
        "status": "failed", "case_id": case.case_id if case is not None else None,
        "revision": case.revision if case is not None else None, "code": error.code, "message": str(error),
    }, status_code=error.status)


def _obsolete_import() -> JSONResponse:
    return _intake_failure("intake_failed", "This import was replaced by a newer import.", 409)


async def _read_zip(request: Request) -> bytes:
    length = request.headers.getlist("content-length")
    if length:
        if len(length) != 1 or not length[0].isascii() or not length[0].isdigit() or len(length[0]) > 20:
            raise HTTPException(400, "Invalid request length.")
        if int(length[0]) > MAX_IMPORT_BODY:
            raise HTTPException(413, "The upload request exceeds 21 MiB.")

    total = 0

    async def receive_limited():
        nonlocal total
        message = await request.receive()
        if message["type"] == "http.request":
            total += len(message.get("body", b""))
            if total > MAX_IMPORT_BODY:
                raise HTTPException(413, "The upload request exceeds 21 MiB.")
        return message

    limited_request = Request(request.scope, receive_limited)
    async with limited_request.form(max_files=1, max_fields=0) as form:
        upload = form.get("file")
        if len(form) != 1 or not isinstance(upload, UploadFile):
            raise HTTPException(400, 'Upload one ZIP using the multipart "file" field.')
        payload = await upload.read(MAX_ZIP_BYTES + 1)
        if len(payload) > MAX_ZIP_BYTES:
            raise HTTPException(413, "The ZIP exceeds the 20 MiB upload limit.")
        return payload


def _translation(original: ListingSnapshot, output: ListingSnapshot) -> ListingSnapshot:
    verified = ListingSnapshot.model_validate(output.model_dump())
    if (
        verified.snapshot_id != original.snapshot_id
        or verified.linked_option_id != original.linked_option_id
        or verified.title != original.title
        or len(verified.options) != len(original.options)
        or len(verified.sources) != len(original.sources)
        or verified.limitations[:len(original.limitations)] != original.limitations
    ):
        raise ValueError("Translation changed the listing evidence.")

    for before, after in zip(original.options, verified.options):
        if before.model_dump(exclude={"name_en"}) != after.model_dump(exclude={"name_en"}):
            raise ValueError("Translation changed a SKU selector.")
    for before, after in zip(original.sources, verified.sources):
        exclude = {"text_en"} if before.kind == "listing_text" else set()
        if before.model_dump(exclude=exclude) != after.model_dump(exclude=exclude):
            raise ValueError("Translation changed a source.")
    return verified


def create_app(
    db_path: Path | str | None = None, *, translator: Translator | None = None,
    question_generator: QuestionGenerator | None = None, review_generator: ReviewGenerator | None = None,
    brief_generator: BriefGenerator | None = None,
) -> FastAPI:
    database = Path(db_path if db_path is not None else os.environ.get("PURCHASE_DATABASE_PATH", DEFAULT_DATABASE))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store = Store(database)
        app.state.store = store
        app.state.workspace_order = count(1)
        app.state.last_workspace_change = 0
        app.state.parser_lock = asyncio.Lock()
        app.state.parse_task = None
        client = None
        question_client = None
        review_client = None
        try:
            if translator is None:
                client = build_translation_client()
            app.state.translate = translator if translator is not None else partial(translate_listing, client=client)
            if question_generator is None or brief_generator is None:
                question_client = build_question_client()
            if review_generator is None:
                review_client = build_review_client()
            app.state.case_workflow = CaseWorkflow(
                store, question_generator if question_generator is not None else partial(generate_questions, client=question_client),
                review_generator if review_generator is not None else partial(generate_review, client=review_client),
                brief_generator if brief_generator is not None else partial(generate_brief_proposal, client=question_client),
            )
            app.state.case_workflow.recover_saved_case()
            yield
        finally:
            try:
                if app.state.parse_task is not None:
                    await asyncio.gather(app.state.parse_task, return_exceptions=True)
                await asyncio.gather(*(sdk.close() for sdk in (client, question_client, review_client) if sdk is not None))
            finally:
                store.close()

    application = FastAPI(lifespan=lifespan)
    application.add_middleware(
        CORSMiddleware, allow_origins=[DEMO_ORIGIN], allow_methods=["GET", "POST"], allow_headers=["Content-Type"],
    )

    @application.middleware("http")
    async def guard_origin(request: Request, call_next):
        origins = request.headers.getlist("origin")
        if request.method == "POST" and origins and origins != [DEMO_ORIGIN]:
            return _invalid_request("This request is from an unsupported origin.", 403)
        return await call_next(request)

    @application.exception_handler(RequestValidationError)
    async def invalid_shape(request: Request, error: RequestValidationError):
        if request.url.path in CASE_COMMANDS:
            _, _, record = application.state.store.workspace()
            return _case_failure(CaseCommandError(
                "invalid_input", "Check the request fields and try again.",
                record.view if record is not None else None, status=422,
            ))
        return _invalid_request("Check the request fields and try again.", 422)

    @application.exception_handler(CaseCommandError)
    async def case_failure(request: Request, error: CaseCommandError):
        return _case_failure(error)

    @application.exception_handler(sqlite3.Error)
    async def storage_failure(request: Request, error: sqlite3.Error):
        if request.url.path == "/api/imports":
            return _intake_failure("intake_failed", "The server could not save the import.", 500)
        if request.url.path in CASE_COMMANDS or request.url.path == "/api/case":
            return _case_failure(CaseCommandError(
                "storage_failed", "The server could not read or save the current case.", status=500,
            ))
        return JSONResponse(
            {"status": "failed", "code": "storage_failed", "message": "The server could not read the active import."},
            status_code=500,
        )

    async def parse_current(generation: int, parse: Callable[[], Coroutine[Any, Any, ParsedListing]]) -> ParsedListing | None:
        state = application.state
        await state.parser_lock.acquire()
        try:
            if not state.store.is_current(generation):
                state.parser_lock.release()
                return None
            task = asyncio.create_task(parse())
        except BaseException:
            state.parser_lock.release()
            raise
        state.parse_task = task

        def release_parser(finished: asyncio.Task) -> None:
            state.parse_task = None
            state.parser_lock.release()
            if not finished.cancelled():
                finished.exception()

        # A disconnected/cancelled request cannot free the parser slot while the
        # worker still owns its archive bytes and is decoding the archive.
        task.add_done_callback(release_parser)
        return await asyncio.shield(task)

    @application.post("/api/imports")
    async def import_listing(request: Request):
        state = application.state
        store = state.store
        order = next(state.workspace_order)
        is_link = request.headers.get("content-type", "").split(";")[0].strip().lower() == "application/json"
        if is_link:
            body = bytearray()
            async for chunk in request.stream():
                if len(body) + len(chunk) > 2048:
                    return _intake_failure("intake_failed", LINK_HINT, 413)
                body.extend(chunk)
            try:
                link = parse_product_link(LinkRequest.model_validate_json(body).url)
            except ValueError:
                return _intake_failure("intake_failed", LINK_HINT, 422)
        # A slow link body may finish validating after a newer import or a successful reset.
        if order < state.last_workspace_change:
            return _obsolete_import()
        state.last_workspace_change = order
        generation = store.begin_import()
        try:
            if is_link:
                parse = partial(fetch_listing, link)
            else:
                parse = partial(asyncio.to_thread, parse_listing, await _read_zip(request))
        except HTTPException as error:
            if error.status_code == 413:
                return _intake_failure("archive_limit", str(error.detail), 413)
            return _intake_failure("invalid_archive", 'Upload one ZIP using the multipart "file" field.', 400)

        try:
            parsed = await parse_current(generation, parse)
        except IntakeError as error:
            if not store.is_current(generation):
                return _obsolete_import()
            status = 413 if error.code == "archive_limit" else 200
            return _intake_failure(error.code, str(error), status)
        except (OSError, ValidationError, ValueError):
            return _intake_failure("intake_failed", "The server could not prepare the listing evidence.", 500)
        finally:
            del parse

        if parsed is None or not store.is_current(generation):
            return _obsolete_import()

        try:
            translated = await application.state.translate(parsed.snapshot.model_copy(deep=True))
            snapshot = _translation(parsed.snapshot, translated)
        except TranslationConfigurationError as error:
            if not store.is_current(generation):
                return _obsolete_import()
            return _intake_failure("intake_failed", str(error))
        except (ValidationError, TypeError, ValueError, AttributeError):
            return _intake_failure("intake_failed", "Translation could not be applied to the listing evidence.")

        if not store.publish_import(generation, snapshot, parsed.images):
            return _obsolete_import()
        return IntakeReady(snapshot=snapshot)

    @application.get("/api/case")
    async def active_case():
        _, snapshot, case = application.state.store.workspace()
        return JSONResponse(
            {"snapshot": snapshot.model_dump() if snapshot is not None else None,
             "case": case.view.model_dump() if case is not None else None},
            headers={"Cache-Control": "no-store"},
        )

    @application.get("/api/snapshots/{snapshot_id}/images/{source_id}")
    async def get_image(snapshot_id: str, source_id: str):
        image = application.state.store.image(snapshot_id, source_id)
        if image is None:
            return Response(status_code=404, headers={"Cache-Control": "no-store"})
        mime, payload = image
        return Response(payload, media_type=mime, headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})

    @application.post("/api/selections")
    async def confirm_selection(payload: SelectionRequest):
        snapshot = application.state.store.snapshot()
        if snapshot is None or snapshot.snapshot_id != payload.snapshot_id:
            return _invalid_request("This listing is no longer active. Import the listing again.", 409)

        selected = payload.selected_option_ids
        options = {option.option_id for option in snapshot.options}
        if len(set(selected)) != len(selected) or not options.issuperset(selected):
            return _invalid_request("Select one to three distinct options from the active listing.", 422)
        return {
            "status": "confirmed",
            "selection": {
                "snapshot_id": snapshot.snapshot_id,
                "selected_option_ids": selected,
                "buyer_context": payload.buyer_context,
            },
        }

    @application.post("/api/questions")
    async def start_questions(command: StartCase):
        return {"status": "ready", "case": await application.state.case_workflow.start(command)}

    @application.post("/api/questions/continue")
    async def continue_questions(command: ContinueCase):
        return {"status": "ready", "case": await application.state.case_workflow.submit(command)}

    @application.post("/api/questions/resume")
    async def resume_questions(command: ResumeQuestions):
        return {"status": "ready", "case": await application.state.case_workflow.resume_questions(command)}

    @application.post("/api/brief")
    async def prepare_brief(command: PrepareBrief):
        return {"status": "ready", "case": application.state.case_workflow.prepare_brief(command)}

    @application.post("/api/brief/preview")
    async def preview_brief(command: BriefCorrection):
        return await application.state.case_workflow.preview_correction(command)

    @application.post("/api/brief/preview-undo")
    async def preview_undo(command: UndoBriefPreview):
        return application.state.case_workflow.preview_undo(command)

    @application.post("/api/brief/preview-answers")
    async def preview_answers(command: PrepareBrief):
        return application.state.case_workflow.preview_answers(command)

    @application.post("/api/brief/edit")
    async def edit_brief(command: BriefEdit):
        return {"status": "ready", "case": application.state.case_workflow.edit_brief(command)}

    @application.post("/api/reviews")
    async def review_case(command: ConfirmBrief):
        return {"status": "ready", "case": await application.state.case_workflow.confirm_brief(command)}

    @application.post("/api/case/retry")
    async def retry_case(command: RetryCase):
        return {"status": "ready", "case": await application.state.case_workflow.retry(command)}

    @application.post("/api/case/reset")
    async def reset_case(command: ResetCase):
        state = application.state
        state.case_workflow.reset(command)
        state.last_workspace_change = next(state.workspace_order)
        return {"snapshot": None, "case": None}

    return application


app = create_app()
