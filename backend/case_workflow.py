from collections.abc import Awaitable, Callable, Mapping
from hashlib import sha256
import json
import sqlite3
from types import MappingProxyType
from typing import Literal
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError

from .ai import QuestionGenerationError, validate_question_result
from .case_models import (
    CaseOperation, CaseRecord, CaseReference, CaseStage, CaseView, ContinueCase, OperationBinding,
    ResetCase, RetryCase, ReviewCase, SavedReview, StartCase,
)
from .contracts import (
    AnsweredQuestion, AnswerSubmission, IssuedRound, ListingSnapshot, QuestionInput,
    QuestionResult, ReviewInput,
)
from .report import assemble_review, review_sent_evidence
from .report_models import PurchaseReview, ReviewFailed, ReviewResult, SentReviewEvidence
from .requirements import (
    issue_requirement_plan, legacy_context_rows, recover_direct_legacy_requirement, resolve_requirements,
    validate_submission_effect,
)
from .state import Store


QuestionGenerator = Callable[[QuestionInput], Awaitable[QuestionResult]]
ReviewImages = Mapping[str, tuple[str, bytes]]
ReviewGenerator = Callable[[ReviewInput, ReviewImages], Awaitable[ReviewResult]]
UserCommand = StartCase | ContinueCase | ReviewCase
REVIEW_RESULT = TypeAdapter(ReviewResult)
INVALID_REVIEW = "The review could not be validated. Your answers are saved."
REGENERATE_REVIEW = "This saved review needs to be regenerated. Your answers are saved."
LEGACY_REPORT_FIELDS = frozenset({
    "case_id", "revision", "snapshot_id", "selected_option_ids", "buyer_requirements", "open_preferences",
    "comparisons", "findings", "coverage", "limitations", "supplier_draft",
})


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
                AnsweredQuestion(
                    question_id=question.question_id, question_text=question.prompt,
                    answer_text=answer.answer_text, buyer_facet=question.buyer_facet,
                    requirement_effect=answer.requirement_effect,
                )
                for question, answer in zip(round.questions, round.answers, strict=True)
            )
    return {
        "case_id": case.case_id, "revision": case.revision, "snapshot": snapshot,
        "selected_option_ids": case.selected_option_ids, "buyer_context": case.buyer_context, "answers": answers,
        "requirements": case.requirements,
    }


def _answer_scopes(case: CaseView) -> dict[str, list[str]]:
    return {
        question.question_id: question.option_ids
        for round in case.rounds if round.answers is not None
        for question in round.questions
    }


def _review_input(snapshot: ListingSnapshot, case: CaseView) -> ReviewInput:
    return ReviewInput(**_input_fields(snapshot, case), answer_option_ids=_answer_scopes(case))


def _question_input(snapshot: ListingSnapshot, case: CaseView, round_number: int) -> QuestionInput:
    fields = _input_fields(snapshot, case)
    if round_number == 1:
        fields["requirements"] = None
    return QuestionInput(
        **fields, round_number=round_number,
        answer_option_ids=_answer_scopes(case) if fields["requirements"] is not None else None,
    )


def _resolved_view(record: CaseRecord, case: CaseView) -> CaseView:
    if record.initial_requirements is None:
        return case
    return case.model_copy(update={
        "requirements": resolve_requirements(case.case_id, record.initial_requirements, case.rounds),
    })


def _upgrade_legacy_case(record: CaseRecord) -> CaseRecord:
    case = record.view
    initial = legacy_context_rows(case.case_id, case.buyer_context, case.selected_option_ids)
    upgraded = record.model_copy(update={"requirement_version": 1, "initial_requirements": initial})
    case = _resolved_view(upgraded, case)
    pending = record.question_input
    if pending is not None and pending.round_number > 1:
        pending = _question_input(pending.snapshot, case, pending.round_number)
    return upgraded.model_copy(update={"view": case, "question_input": pending})


def _safe_legacy_review(
    upgraded: CaseRecord, inputs: ReviewInput, restored: PurchaseReview, saved: SavedReview,
) -> tuple[CaseRecord, SavedReview] | None:
    content = saved.content
    if content.open_preferences:
        return None
    if not inputs.buyer_context.strip() and not inputs.answers and not content.buyer_requirements:
        return upgraded, saved
    if len(content.buyer_requirements) != 1 or len(restored.buyer_requirements) != 1:
        return None

    selection = content.buyer_requirements[0]
    prior = restored.buyer_requirements[0]
    if selection.input_ref == "context":
        if inputs.answers or selection.quote != inputs.buyer_context.strip() or prior.option_ids != inputs.selected_option_ids:
            return None
        start = inputs.buyer_context.index(selection.quote)
        facet = None
    else:
        if inputs.buyer_context.strip() or len(inputs.answers) != 1:
            return None
        answer = inputs.answers[0]
        if (
            selection.input_ref != f"answer:{answer.question_id}" or selection.quote != answer.answer_text
            or prior.question_text != answer.question_text or prior.option_ids != inputs.answer_option_ids[answer.question_id]
        ):
            return None
        start = None
        facet = answer.buyer_facet

    requirement = recover_direct_legacy_requirement(
        case_id=inputs.case_id, input_ref=selection.input_ref, quote=selection.quote,
        question_text=prior.question_text, option_ids=prior.option_ids, topic=selection.topic,
        buyer_facet=facet, context_start=start,
    )
    if requirement is None:
        return None
    updated = upgraded.model_copy(update={"initial_requirements": [requirement]})
    updated = updated.model_copy(update={"view": _resolved_view(updated, updated.view)})
    remapped = content.model_copy(update={
        "buyer_requirements": [],
        "findings": [finding.model_copy(update={
            "requirement_ids": [requirement.requirement_id if item == prior.requirement_id else item for item in finding.requirement_ids],
        }) for finding in content.findings],
    })
    return updated, saved.model_copy(update={"content": remapped})


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
    try:
        for question, answer in zip(round.questions, answers, strict=True):
            validate_submission_effect(question, answer)
    except ValueError as error:
        raise _invalid(str(error), case) from error
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
    case = record.view.model_copy(update={"phase": "failed", "report": None, "reason": None, "last_operation": operation})
    return record.model_copy(update={"view": case, "saved_review": None})


def _legacy_report_matches(report: object, inputs: ReviewInput) -> bool:
    if not isinstance(report, dict) or report.keys() != LEGACY_REPORT_FIELDS:
        return False
    return (
        _report_binding_matches(report, inputs)
        and isinstance(report["comparisons"], list) and len(report["comparisons"]) > 0
        and all(isinstance(row, dict) and row.keys() == {"label", "values"} for row in report["comparisons"])
        and isinstance(report["findings"], list)
        and all(isinstance(row, dict) and "claim_ids" not in row for row in report["findings"])
    )


def _report_binding_matches(report: dict, inputs: ReviewInput) -> bool:
    return (report.get("case_id"), report.get("revision"), report.get("snapshot_id"), report.get("selected_option_ids")) == (
        inputs.case_id, inputs.revision, inputs.snapshot.snapshot_id, inputs.selected_option_ids,
    )


def _review_digest(
    inputs: ReviewInput, operation_id: str, images: ReviewImages, scope: SentReviewEvidence, *,
    version: int,
) -> str:
    if scope != review_sent_evidence(inputs, images):
        raise ValueError("The sent evidence does not match the case request.")
    # Include the source binding and bytes of each attachment, not just its snapshot locator.
    attachments = []
    for source_id in scope.attached_image_ids:
        mime, payload = images[source_id]
        attachments.append((source_id, mime, sha256(payload).hexdigest()))
    excluded = {"requirements"} if version < 5 else set()
    if version == 2:
        excluded.add("answer_option_ids")
    input_fields = inputs.model_dump(mode="json", exclude=excluded)
    material = {
        "input": input_fields, "operation_id": operation_id,
        "scope": scope.model_dump(mode="json"), "attachments": attachments,
    }
    if version >= 4:
        material["version"] = version
    return sha256(json.dumps(material, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class CaseWorkflow:
    def __init__(self, store: Store, generate_questions: QuestionGenerator, generate_review: ReviewGenerator):
        self.store = store
        self.generate_questions = generate_questions
        self.generate_review = generate_review

    def recover_saved_case(self) -> None:
        generation, snapshot, record, report, saved_review = self.store.workspace_for_recovery()
        if record is None:
            return
        case = record.view
        operation = case.last_operation
        legacy = record.requirement_version is None
        try:
            upgraded = _upgrade_legacy_case(record) if legacy else record
            resolved = _resolved_view(upgraded, upgraded.view)
        except (KeyError, ValueError) as error:
            raise sqlite3.DatabaseError("The saved buyer decisions could not be matched to the interview.") from error
        if not legacy and resolved.requirements != case.requirements:
            raise sqlite3.DatabaseError("The saved buyer decisions do not match the retained interview.")
        if operation.status == "running":
            interrupted = _failure(upgraded, "analysis_failed", _interrupted(operation.stage))
            self._save(generation, record, interrupted)
            return
        if case.phase != "reviewed":
            if legacy or report is not None or saved_review is not None:
                self._save(generation, record, upgraded)
            return
        if snapshot is None or case.snapshot_id != snapshot.snapshot_id or operation.stage != "review":
            raise sqlite3.DatabaseError("The saved review has no matching case snapshot or operation.")
        inputs = _review_input(snapshot, case)
        if operation.status == "ready" and saved_review is None and _legacy_report_matches(report, inputs):
            if upgraded.initial_requirements is None:
                upgraded = _upgrade_legacy_case(upgraded)
            failed = _failure(upgraded, "review_regeneration_required", REGENERATE_REVIEW)
            self._save(generation, record, failed)
            return

        try:
            if operation.status != "ready":
                raise ValueError("The saved review operation has not completed successfully.")
            validated = SavedReview.model_validate_json(json.dumps(saved_review))
            if (validated.version == 5) != (inputs.requirements is not None):
                raise ValueError("The saved review has a mismatched requirement contract version.")
            images = self._review_images(inputs)
            digest = _review_digest(inputs, operation.operation_id, images, validated.sent_evidence, version=validated.version)
            if validated.input_digest != digest:
                raise ValueError("The saved review belongs to different case input or evidence.")
            rebuilt = assemble_review(inputs, validated.content, sent_evidence=validated.sent_evidence)
            if validated.version < 4:
                if (
                    not isinstance(report, dict) or report.keys() != LEGACY_REPORT_FIELDS | {"contract_version", "claims"}
                    or report["contract_version"] != 2 or not _report_binding_matches(report, inputs)
                ):
                    raise ValueError("The saved report does not match the earlier case projection.")
            elif rebuilt != PurchaseReview.model_validate(report):
                raise ValueError("The saved report does not match its validated claims.")
            if legacy:
                migration = _safe_legacy_review(upgraded, inputs, rebuilt, validated)
                if migration is None:
                    failed = _failure(upgraded, "review_regeneration_required", REGENERATE_REVIEW)
                    self._save(generation, record, failed)
                    return
                upgraded, validated = migration
                inputs = _review_input(snapshot, upgraded.view)
                rebuilt = assemble_review(inputs, validated.content, sent_evidence=validated.sent_evidence)
        except ValueError:
            failed = _failure(upgraded, "invalid_review", INVALID_REVIEW)
            self._save(generation, record, failed)
            return
        if legacy or validated.version < 4:
            version = 5 if legacy else 4
            migrated = validated.model_copy(update={
                "version": version,
                "input_digest": _review_digest(inputs, operation.operation_id, images, validated.sent_evidence, version=version),
            })
            self._save(generation, record, upgraded.model_copy(update={
                "view": upgraded.view.model_copy(update={"report": rebuilt}), "saved_review": migrated,
            }))

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
        record = CaseRecord(
            view=case, requirement_version=1,
            question_input=_question_input(snapshot, case, round_number=1),
        )
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
            if current.initial_requirements is None and previous.phase == "failed" and previous.last_operation.stage == "start":
                working = _upgrade_legacy_case(current.model_copy(update={"view": case}))
            else:
                working = current
            case = _resolved_view(working, case)
            review_input = _review_input(snapshot, case)
            images = self._review_images(review_input)
            case = case.model_copy(update={
                "phase": "generating_review",
                "last_operation": CaseOperation(operation_id=operation_id, stage="review", status="running"),
            })
            record = working.model_copy(update={
                "view": case, "question_input": None, "review_input": review_input, "saved_review": None,
            })
            self._save(generation, current, record, command=command, stage=stage)
            return await self._run_review(generation, record, images)

        case = _resolved_view(current, case)
        if len(case.rounds) == 3:
            case = case.model_copy(update={
                "phase": "ready_for_review", "reason": "You have reached the three-round question limit.",
                "last_operation": CaseOperation(operation_id=operation_id, stage="continue", status="ready"),
            })
            settled = current.model_copy(update={
                "view": case, "question_input": None, "review_input": None, "saved_review": None,
            })
            self._save(generation, current, settled, command=command, stage=stage)
            return case

        case = case.model_copy(update={
            "phase": "preparing_questions", "reason": None,
            "last_operation": CaseOperation(operation_id=operation_id, stage="continue", status="running"),
        })
        question_input = _question_input(snapshot, case, round_number=len(case.rounds) + 1)
        record = current.model_copy(update={
            "view": case, "question_input": question_input, "review_input": None, "saved_review": None,
        })
        self._save(generation, current, record, command=command, stage=stage)
        return await self._run_questions(generation, record)

    async def retry(self, command: RetryCase) -> CaseView:
        generation, snapshot, record = self.store.workspace()
        if record is None or not _matches(record, command) or record.view.last_operation.operation_id != str(command.operation_id):
            raise _obsolete(record.view if record is not None else None)
        case = record.view
        if case.last_operation.status != "failed":
            return _operation_result(case)

        operation = case.last_operation.model_copy(update={"status": "running", "code": None, "message": None})
        if operation.stage == "review":
            if snapshot is None or snapshot.snapshot_id != case.snapshot_id:
                raise _obsolete(case)
            review_input = _review_input(snapshot, case)
            images = self._review_images(review_input)
            pending = record.model_copy(update={
                "question_input": None, "review_input": review_input, "saved_review": None,
                "view": case.model_copy(update={"phase": "generating_review", "last_operation": operation}),
            })
            self._save(generation, record, pending)
            return await self._run_review(generation, pending, images)

        if record.question_input is None:
            return _operation_result(case)
        if snapshot is None or snapshot.snapshot_id != case.snapshot_id:
            raise _obsolete(case)
        inputs = _question_input(snapshot, case, record.question_input.round_number)
        pending_view = case.model_copy(update={"phase": "preparing_questions", "reason": None, "last_operation": operation})
        pending = record.model_copy(update={"view": pending_view, "question_input": inputs})
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
            initial, questions = issue_requirement_plan(question_input, result, record.initial_requirements)
            case = record.view
            operation = case.last_operation.model_copy(update={"status": "ready"})
            rounds = case.rounds
            if questions:
                round = IssuedRound(
                    round_id=str(uuid4()), round_number=question_input.round_number, reason=result.reason,
                    questions=questions, answers=None,
                )
                rounds = [*rounds, round]
            case = case.model_copy(update={
                "phase": "awaiting_answers" if questions else "ready_for_review",
                "rounds": rounds, "reason": None if questions else result.reason, "last_operation": operation,
            })
            settled = record.model_copy(update={"initial_requirements": initial, "question_input": None})
            case = _resolved_view(settled, case)
            settled = settled.model_copy(update={"view": case})
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
                operation = record.view.last_operation.model_copy(update={"status": "ready"})
                scope = result.sent_evidence
                version = 5 if inputs.requirements is not None else 4
                digest = _review_digest(inputs, operation.operation_id, images, scope, version=version)
                report = assemble_review(inputs, result.content, sent_evidence=scope)
                if report != result.report:
                    raise ValueError("The report does not match its validated claims.")
                settled = record.model_copy(update={
                    "view": record.view.model_copy(update={"phase": "reviewed", "report": report, "last_operation": operation}),
                    "review_input": None,
                    "saved_review": SavedReview(version=version, input_digest=digest, content=result.content, sent_evidence=scope),
                })
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
