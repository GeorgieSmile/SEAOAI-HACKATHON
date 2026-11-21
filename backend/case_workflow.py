from collections.abc import Awaitable, Callable, Mapping
from hashlib import sha256
import json
from types import MappingProxyType
from typing import Literal
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError

from .ai import QuestionGenerationError, validate_question_result
from .case_models import (
    CaseOperation, CaseRecord, CaseReference, CaseStage, CaseView, ContinueCase, OperationBinding,
    ResetCase, RetryCase, ReviewCase, StartCase,
)
from .contracts import (
    AnsweredQuestion, AnswerSubmission, IssuedQuestion, IssuedRound, ListingSnapshot, QuestionInput,
    QuestionResult, ReviewInput,
)
from .report_models import ReviewFailed, ReviewResult
from .state import Store


QuestionGenerator = Callable[[QuestionInput], Awaitable[QuestionResult]]
ReviewImages = Mapping[str, tuple[str, bytes]]
ReviewGenerator = Callable[[ReviewInput, ReviewImages], Awaitable[ReviewResult]]
UserCommand = StartCase | ContinueCase | ReviewCase
REVIEW_RESULT = TypeAdapter(ReviewResult)
INVALID_REVIEW = "The review could not be validated. Your answers are saved."


class CaseCommandError(Exception):
    def __init__(
        self, code: str, message: str, case: CaseView | None = None, *, status: int = 200,
    ):
        super().__init__(message)
        self.code = code
        self.case = case
        self.status = status


def _invalid(message: str, case: CaseView | None) -> CaseCommandError:
    return CaseCommandError("invalid_input", message, case, status=422)


def _obsolete(case: CaseView | None) -> CaseCommandError:
    return CaseCommandError("invalid_input", "This request belongs to an earlier case state.", case, status=409)


def _interrupted(stage: CaseStage) -> str:
    action = "Review generation" if stage == "review" else "Question preparation"
    return f"{action} was interrupted. Retry to continue from your saved answers."


def _matches(case: CaseRecord | None, reference: CaseReference | None) -> bool:
    if case is None or reference is None:
        return case is None and reference is None
    return case.view.case_id == reference.case_id and case.view.revision == reference.revision


def _fingerprint(command: UserCommand) -> str:
    payload = command.model_dump(mode="json", exclude={"operation_id"})
    if submission := payload.get("submission"):
        submission["answers"].sort(key=lambda answer: answer["question_id"])
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _input_fields(snapshot: ListingSnapshot, case: CaseView) -> dict:
    answers = []
    for round in case.rounds:
        if round.answers is not None:
            answers.extend(
                AnsweredQuestion(question_id=question.question_id, question_text=question.prompt, answer_text=answer.answer_text)
                for question, answer in zip(round.questions, round.answers, strict=True)
            )
    return {
        "case_id": case.case_id, "revision": case.revision, "snapshot": snapshot,
        "selected_option_ids": case.selected_option_ids, "buyer_context": case.buyer_context, "answers": answers,
    }


def _apply_submission(case: CaseView, submission: AnswerSubmission) -> CaseView:
    for index, round in enumerate(case.rounds):
        if round.round_id == submission.round_id:
            break
    else:
        raise _invalid("This question round was not issued for the current case.", case)

    by_id = {answer.question_id: answer for answer in submission.answers}
    if len(by_id) != len(submission.answers) or by_id.keys() != {item.question_id for item in round.questions}:
        raise _invalid("Send one answer for every question in the issued round.", case)

    answers = [by_id[question.question_id] for question in round.questions]
    if answers == round.answers:
        return case
    updated = round.model_copy(update={"answers": answers})
    return case.model_copy(update={
        "revision": case.revision + 1, "rounds": [*case.rounds[:index], updated], "reason": None, "report": None,
    })


def _operation_result(case: CaseView) -> CaseView:
    operation = case.last_operation
    if operation.status == "running":
        raise CaseCommandError("invalid_input", "This operation is still running.", case, status=409)
    if operation.status == "failed":
        raise CaseCommandError(operation.code or "analysis_failed", operation.message or _interrupted(operation.stage), case)
    return case


def _failure(record: CaseRecord, code: str, message: str) -> CaseRecord:
    operation = record.view.last_operation.model_copy(update={"status": "failed", "code": code, "message": message})
    case = record.view.model_copy(update={"phase": "failed", "reason": None, "last_operation": operation})
    return record.model_copy(update={"view": case})


class CaseWorkflow:
    def __init__(self, store: Store, generate_questions: QuestionGenerator, generate_review: ReviewGenerator):
        self.store = store
        self.generate_questions = generate_questions
        self.generate_review = generate_review

    def recover_interrupted(self) -> None:
        generation, _, record = self.store.workspace()
        if record is not None and record.view.last_operation.status == "running":
            interrupted = _failure(record, "analysis_failed", _interrupted(record.view.last_operation.stage))
            self._save(generation, record, interrupted)

    def reset(self, command: ResetCase) -> None:
        generation, snapshot, current = self.store.workspace()
        snapshot_id = snapshot.snapshot_id if snapshot is not None else None
        if command.snapshot_id != snapshot_id or not _matches(current, command.previous_case):
            raise _obsolete(current.view if current is not None else None)
        if not self.store.reset(generation):
            raise _obsolete(None)

    async def start(self, command: StartCase) -> CaseView:
        generation, snapshot, current = self.store.workspace()
        previous_view = current.view if current is not None else None
        if replay := self._replay(current, command, "start"):
            return replay
        if snapshot is None or snapshot.snapshot_id != command.snapshot_id or not _matches(current, command.previous_case):
            raise _obsolete(previous_view)

        selected = command.selected_option_ids
        if len(selected) != len(set(selected)) or not {option.option_id for option in snapshot.options}.issuperset(selected):
            raise _invalid("Select one to three distinct options from the active listing.", previous_view)
        if current is not None and current.view.selected_option_ids == selected and current.view.buyer_context == command.buyer_context:
            self._save(generation, current, current, command=command, stage="start")
            return _operation_result(current.view)

        case = CaseView(
            case_id=str(uuid4()), revision=0, snapshot_id=snapshot.snapshot_id, selected_option_ids=selected,
            buyer_context=command.buyer_context, rounds=[], phase="preparing_questions",
            last_operation=CaseOperation(operation_id=str(command.operation_id), stage="start", status="running"),
        )
        record = CaseRecord(view=case, question_input=QuestionInput(**_input_fields(snapshot, case), round_number=1))
        self._save(generation, current, record, command=command, stage="start")
        return await self._run_questions(generation, record)

    async def submit(self, command: ContinueCase | ReviewCase, stage: Literal["continue", "review"]) -> CaseView:
        generation, snapshot, current = self.store.workspace()
        if replay := self._replay(current, command, stage):
            return replay
        if current is None or snapshot is None or not _matches(current, command):
            raise _obsolete(current.view if current is not None else None)

        previous = current.view
        case = _apply_submission(previous, command.submission) if command.submission is not None else previous
        unchanged_submission = command.submission is not None and case is previous
        open_round = bool(case.rounds and case.rounds[-1].answers is None)
        if unchanged_submission and (stage == "continue" or open_round):
            self._save(generation, current, current, command=command, stage=stage)
            return _operation_result(previous)

        operation_id = str(command.operation_id)
        if stage == "review":
            if open_round:
                raise _invalid("Submit the open question round before requesting the review.", previous)
            if case is previous and previous.last_operation.stage == "review" and (
                previous.report is not None or previous.last_operation.status != "ready"
            ):
                self._save(generation, current, current, command=command, stage=stage)
                return _operation_result(previous)
            review_input = ReviewInput(**_input_fields(snapshot, case))
            images = self._review_images(review_input)
            case = case.model_copy(update={
                "phase": "generating_review",
                "last_operation": CaseOperation(operation_id=operation_id, stage="review", status="running"),
            })
            record = CaseRecord(view=case, review_input=review_input)
            self._save(generation, current, record, command=command, stage=stage)
            return await self._run_review(generation, record, images)

        if len(case.rounds) == 3:
            case = case.model_copy(update={
                "phase": "ready_for_review", "reason": "You have reached the three-round question limit.",
                "last_operation": CaseOperation(operation_id=operation_id, stage="continue", status="ready"),
            })
            self._save(generation, current, CaseRecord(view=case), command=command, stage=stage)
            return case

        case = case.model_copy(update={
            "phase": "preparing_questions", "reason": None,
            "last_operation": CaseOperation(operation_id=operation_id, stage="continue", status="running"),
        })
        question_input = QuestionInput(**_input_fields(snapshot, case), round_number=len(case.rounds) + 1)
        record = CaseRecord(view=case, question_input=question_input)
        self._save(generation, current, record, command=command, stage=stage)
        return await self._run_questions(generation, record)

    async def retry(self, command: RetryCase) -> CaseView:
        generation, _, record = self.store.workspace()
        if record is None or not _matches(record, command) or record.view.last_operation.operation_id != str(command.operation_id):
            raise _obsolete(record.view if record is not None else None)
        case = record.view
        if case.last_operation.status != "failed":
            return _operation_result(case)

        operation = case.last_operation.model_copy(update={"status": "running", "code": None, "message": None})
        if record.review_input is not None and operation.stage == "review":
            images = self._review_images(record.review_input)
            pending = record.model_copy(update={"view": case.model_copy(update={
                "phase": "generating_review", "last_operation": operation,
            })})
            self._save(generation, record, pending)
            return await self._run_review(generation, pending, images)

        if record.question_input is None:
            return _operation_result(case)
        pending_view = case.model_copy(update={"phase": "preparing_questions", "reason": None, "last_operation": operation})
        pending = record.model_copy(update={"view": pending_view})
        self._save(generation, record, pending)
        return await self._run_questions(generation, pending)

    def _review_images(self, inputs: ReviewInput) -> ReviewImages:
        sources = {source.source_id: source for source in inputs.snapshot.sources}
        options = {option.option_id: option for option in inputs.snapshot.options}
        images = {}
        for option_id in inputs.selected_option_ids:
            source_id = options[option_id].image_source_id
            if source_id is None or sources[source_id].availability != "available" or source_id in images:
                continue
            if image := self.store.image(inputs.snapshot.snapshot_id, source_id):
                images[source_id] = image
        return MappingProxyType(images)

    def _replay(self, current: CaseRecord | None, command: UserCommand, stage: CaseStage) -> CaseView | None:
        binding = self.store.operation_binding(str(command.operation_id))
        if binding is None:
            return None
        view = current.view if current is not None else None
        if binding.stage != stage or binding.fingerprint != _fingerprint(command):
            raise _invalid("Reuse an operation ID only with its original command and payload.", view)
        if (
            view is None or view.case_id != binding.case_id or view.revision != binding.revision
            or view.last_operation.operation_id != binding.result_operation_id
        ):
            raise _obsolete(view)
        return _operation_result(view)

    def _save(
        self, generation: int, previous: CaseRecord | None, record: CaseRecord,
        *, command: UserCommand | None = None, stage: CaseStage | None = None,
    ) -> None:
        binding = None
        if command is not None and stage is not None:
            binding = OperationBinding(
                operation_id=str(command.operation_id), stage=stage, fingerprint=_fingerprint(command),
                case_id=record.view.case_id, revision=record.view.revision,
                result_operation_id=record.view.last_operation.operation_id,
            )
        if not self.store.save_case(generation, previous, record, binding=binding):
            raise _obsolete(None)

    async def _run_questions(self, generation: int, record: CaseRecord) -> CaseView:
        question_input = record.question_input
        assert question_input is not None
        settled = None
        try:
            raw = await self.generate_questions(question_input.model_copy(deep=True))
            result = QuestionResult.model_validate(raw.model_dump())
            validate_question_result(question_input, result)
            case = record.view
            operation = case.last_operation.model_copy(update={"status": "ready"})
            rounds = case.rounds
            if result.questions:
                round = IssuedRound(
                    round_id=str(uuid4()), round_number=question_input.round_number, reason=result.reason,
                    questions=[IssuedQuestion(question_id=str(uuid4()), **item.model_dump()) for item in result.questions],
                    answers=None,
                )
                rounds = [*rounds, round]
            case = case.model_copy(update={
                "phase": "awaiting_answers" if result.questions else "ready_for_review",
                "rounds": rounds, "reason": None if result.questions else result.reason, "last_operation": operation,
            })
            settled = CaseRecord(view=case)
        except QuestionGenerationError as error:
            settled = _failure(record, error.code, error.message)
        except (AttributeError, TypeError, ValidationError, ValueError):
            settled = _failure(record, "invalid_questions", "The question response could not be validated.")
        except Exception:
            settled = _failure(record, "analysis_failed", "Questions could not be prepared. Your answers are saved.")
        finally:
            if settled is None:
                settled = _failure(record, "analysis_failed", _interrupted(record.view.last_operation.stage))
            published = self.store.save_case(generation, record, settled)

        if not published:
            raise _obsolete(None)
        return _operation_result(settled.view)

    async def _run_review(self, generation: int, record: CaseRecord, images: ReviewImages) -> CaseView:
        inputs = record.review_input
        assert inputs is not None
        settled = None
        try:
            raw = await self.generate_review(inputs.model_copy(deep=True), images)
            result = REVIEW_RESULT.validate_python(raw.model_dump())
            if isinstance(result, ReviewFailed):
                if (result.case_id, result.revision) != (inputs.case_id, inputs.revision):
                    raise ValueError("The review result does not match the submitted case.")
                settled = _failure(record, result.code, result.message)
            else:
                report = result.report
                if (report.case_id, report.revision, report.snapshot_id, report.selected_option_ids) != (
                    inputs.case_id, inputs.revision, inputs.snapshot.snapshot_id, inputs.selected_option_ids,
                ):
                    raise ValueError("The review does not match the submitted evidence.")
                operation = record.view.last_operation.model_copy(update={"status": "ready"})
                settled = CaseRecord(view=record.view.model_copy(update={
                    "phase": "reviewed", "report": report, "last_operation": operation,
                }))
        except (AttributeError, TypeError, ValidationError, ValueError):
            settled = _failure(record, "invalid_review", INVALID_REVIEW)
        except Exception:
            settled = _failure(record, "analysis_failed", "The review could not be generated. Your answers are saved.")
        finally:
            if settled is None:
                settled = _failure(record, "analysis_failed", _interrupted(record.view.last_operation.stage))
            published = self.store.save_case(generation, record, settled)

        if not published:
            raise _obsolete(None)
        return _operation_result(settled.view)
