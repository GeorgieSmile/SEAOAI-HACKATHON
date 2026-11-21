from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import Field, UUID4

from .contracts import AnswerSubmission, ContractModel, IssuedRound, QuestionInput, ReviewInput
from .report_models import PurchaseReview, ReviewContent, SentReviewEvidence


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
    phase: CasePhase
    reason: str | None = None
    report: PurchaseReview | None = None
    last_operation: CaseOperation


class SavedReview(ContractModel):
    version: Literal[2, 3]
    input_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    content: ReviewContent
    sent_evidence: SentReviewEvidence


class CaseRecord(ContractModel):
    view: CaseView
    question_input: QuestionInput | None = None
    review_input: ReviewInput | None = None
    saved_review: SavedReview | None = None


@dataclass(frozen=True)
class OperationBinding:
    operation_id: str
    stage: CaseStage
    fingerprint: str
    case_id: str
    revision: int
    result_operation_id: str
