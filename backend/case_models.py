from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import Field, UUID4, model_validator

from .contracts import (
    AnswerSubmission, ContractModel, IssuedRound, OrderClarification, OrderClarificationSelection,
    QuestionInput, ResolvedRequirement, ReviewInput,
)
from .report_models import PurchaseReview, ReviewSelection, SentReviewEvidence


Identifier = Annotated[str, Field(min_length=1, max_length=128)]
OperationId = Annotated[UUID4, Field(strict=False)]
CaseStage = Literal["start", "continue", "review"]
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


class ReviewCase(CaseReference):
    operation_id: OperationId
    submission: AnswerSubmission | None
    order_clarification: OrderClarificationSelection | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def one_change_at_a_time(self) -> "ReviewCase":
        if self.submission is not None and self.order_clarification is not None:
            raise ValueError("Submit one round or one order clarification at a time.")
        return self


class RetryCase(CaseReference):
    operation_id: OperationId


class ResetCase(ContractModel):
    snapshot_id: Identifier | None
    previous_case: CaseReference | None


class CaseOperation(ContractModel):
    operation_id: str
    stage: CaseStage
    status: Literal["running", "ready", "failed"]
    code: str | None = None
    message: str | None = None


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
    last_operation: CaseOperation


class SavedReview(ContractModel):
    version: Literal[2, 3, 4, 5, 6, 7]
    input_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    content: ReviewSelection
    sent_evidence: SentReviewEvidence


class CaseRecord(ContractModel):
    view: CaseView
    question_input: QuestionInput | None = None
    review_input: ReviewInput | None = None
    saved_review: SavedReview | None = None
    requirement_version: Literal[1] | None = Field(default=None, exclude_if=lambda version: version is None)
    order_conflict_version: Literal[1, 2] | None = Field(default=None, exclude_if=lambda version: version is None)
    initial_requirements: list[ResolvedRequirement] | None = Field(default=None, exclude_if=lambda rows: rows is None)


@dataclass(frozen=True)
class OperationBinding:
    operation_id: str
    stage: CaseStage
    fingerprint: str
    case_id: str
    revision: int
    result_operation_id: str
