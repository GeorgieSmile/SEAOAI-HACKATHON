from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import Field, UUID4, model_validator

from .contracts import (
    AnswerSubmission, BriefChange, BriefProposal, ContractModel, IssuedRound, OrderClarification, OrderClarificationSelection,
    QuestionInput, ResolvedRequirement, ReviewInput,
)
from .report_models import PurchaseReview, ReviewSelection, SentReviewEvidence


Identifier = Annotated[str, Field(min_length=1, max_length=128)]
OperationId = Annotated[UUID4, Field(strict=False)]
CaseStage = Literal["start", "continue", "brief", "review"]
CasePhase = Literal[
    "preparing_questions", "awaiting_answers", "ready_for_review", "generating_review", "reviewed", "failed",
]


class CaseReference(ContractModel):
    case_id: Identifier
    revision: Annotated[int, Field(ge=0, le=2**63 - 1)]


class StartCase(ContractModel):
    operation_id: OperationId
    snapshot_id: Identifier
    previous_case: CaseReference | None
    selected_option_ids: Annotated[list[Identifier], Field(min_length=1, max_length=3)]
    buyer_context: Annotated[str, Field(max_length=2_000)]


class ContinueCase(CaseReference):
    operation_id: OperationId
    submission: AnswerSubmission


class PrepareBrief(CaseReference):
    operation_id: OperationId
    submission: AnswerSubmission | None
    order_clarification: OrderClarificationSelection | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def one_change_at_a_time(self) -> "PrepareBrief":
        if self.submission is not None and self.order_clarification is not None:
            raise ValueError("Submit one round or one order clarification at a time.")
        return self


class ConfirmBrief(CaseReference):
    operation_id: OperationId
    confirm: Literal[True]


class ResumeQuestions(CaseReference):
    operation_id: OperationId


class BriefCorrection(CaseReference):
    text: Annotated[str, Field(min_length=1, max_length=1_000)]
    option_id: Identifier | None = None
    resolves_change_id: Identifier | None = None


class BriefEdit(CaseReference):
    operation_id: OperationId
    correction: BriefCorrection | None = None
    proposal: BriefProposal | None = None
    undo_change_id: Identifier | None = None

    @model_validator(mode="after")
    def one_edit(self) -> "BriefEdit":
        if self.undo_change_id is None:
            if self.correction is None or self.proposal is None:
                raise ValueError("Preview a correction before saving it.")
            if (self.correction.case_id, self.correction.revision) != (self.case_id, self.revision):
                raise ValueError("The correction belongs to a different case revision.")
        elif self.correction is not None or self.proposal is not None:
            raise ValueError("Undo or save one correction at a time.")
        return self


class UndoBriefPreview(CaseReference):
    undo_change_id: Identifier


class BriefImpact(ContractModel):
    changed_requirements: list[ResolvedRequirement]
    removed_requirements: list[ResolvedRequirement]
    discarded_rounds: list[IssuedRound]


class BriefPreview(BriefImpact):
    proposal: BriefProposal | None
    question: str | None = None


class RetryCase(CaseReference):
    operation_id: OperationId
    retry_token: OperationId | None = None


class ResetCase(ContractModel):
    snapshot_id: Identifier | None
    previous_case: CaseReference | None


class CaseOperation(ContractModel):
    operation_id: str
    stage: CaseStage
    status: Literal["queued", "running", "ready", "failed"]
    code: str | None = None
    message: str | None = None
    retry_token: OperationId | None = Field(default=None, exclude_if=lambda token: token is None)


class CaseView(CaseReference):
    snapshot_id: Identifier
    selected_option_ids: list[Identifier]
    buyer_context: str
    rounds: list[IssuedRound]
    requirements: list[ResolvedRequirement] | None = Field(default=None, exclude_if=lambda rows: rows is None)
    order_clarifications: list[OrderClarification] = Field(default_factory=list, exclude_if=lambda rows: not rows)
    phase: CasePhase
    reason: str | None = None
    report: PurchaseReview | None = None
    confirmed_revision: int | None = Field(default=None, exclude_if=lambda revision: revision is None)
    brief_impact: BriefImpact | None = Field(default=None, exclude_if=lambda impact: impact is None)
    brief_changes: list[BriefChange] = Field(default_factory=list, exclude_if=lambda rows: not rows)
    can_resume_questions: bool = Field(default=False, exclude_if=lambda value: not value)
    last_operation: CaseOperation


ReviewDigest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class SavedReview(ContractModel):
    version: Literal[2, 3, 4, 5, 6, 7, 8]
    input_digest: ReviewDigest
    content: ReviewSelection
    sent_evidence: SentReviewEvidence


class CaseRecord(ContractModel):
    view: CaseView
    question_input: QuestionInput | None = None
    review_input: ReviewInput | None = None
    saved_review: SavedReview | None = None
    confirmed_review_digest: ReviewDigest | None = Field(default=None, exclude_if=lambda digest: digest is None)
    review_queue_order: Annotated[int, Field(ge=1)] | None = Field(default=None, exclude_if=lambda order: order is None)
    requirement_version: Literal[1] | None = Field(default=None, exclude_if=lambda version: version is None)
    order_conflict_version: Literal[1, 2, 3] | None = Field(default=None, exclude_if=lambda version: version is None)
    initial_requirements: list[ResolvedRequirement] | None = Field(default=None, exclude_if=lambda rows: rows is None)


@dataclass(frozen=True)
class OperationBinding:
    operation_id: str
    stage: CaseStage
    fingerprint: str
    case_id: str
    revision: int
    result_operation_id: str
