from typing import Annotated, Literal

from pydantic import Field

from .contracts import ContractModel, NonEmptyText, Revision, SelectedOptionIds


ReviewErrorCode = Literal["invalid_input", "analysis_failed", "invalid_review"]
CoverageGroup = Literal["product_match", "intended_use", "supporting_evidence"]


class BuyerRequirement(ContractModel):
    requirement_id: NonEmptyText
    text: NonEmptyText
    input_ref: NonEmptyText
    quote: NonEmptyText


class OpenPreference(ContractModel):
    preference_id: NonEmptyText
    text: NonEmptyText
    input_refs: list[NonEmptyText]
    finding_ids: list[NonEmptyText]


class ComparisonValue(ContractModel):
    option_id: NonEmptyText
    summary: NonEmptyText
    source_ids: list[NonEmptyText]


class Comparison(ContractModel):
    label: NonEmptyText
    values: list[ComparisonValue]


class Citation(ContractModel):
    source_id: NonEmptyText
    quote: NonEmptyText | None
    image_observation: NonEmptyText | None


class Unknown(ContractModel):
    text: NonEmptyText
    reason: Literal["not_found", "not_reviewed", "unreadable"]
    source_ids: list[NonEmptyText]


class Finding(ContractModel):
    finding_id: NonEmptyText
    title: NonEmptyText
    option_ids: SelectedOptionIds
    kind: Literal["listing_conflict", "requirement_unconfirmed", "clarification_needed", "evidence_gap"]
    action_type: Literal["decide_preference", "ask_supplier", "request_evidence"]
    requirement_ids: list[NonEmptyText]
    citations: list[Citation]
    interpretation: NonEmptyText
    impact: NonEmptyText
    priority_reason: NonEmptyText
    unknowns: list[Unknown]
    requested_evidence: NonEmptyText
    evidence_limits: NonEmptyText
    supplier_question: NonEmptyText | None


class Coverage(ContractModel):
    group: CoverageGroup
    status: Literal["reviewed", "limited", "not_assessed"]
    finding_ids: list[NonEmptyText]
    note: NonEmptyText


class ReviewContent(ContractModel):
    buyer_requirements: list[BuyerRequirement]
    open_preferences: list[OpenPreference]
    comparisons: Annotated[list[Comparison], Field(min_length=1)]
    findings: list[Finding]
    coverage: Annotated[list[Coverage], Field(min_length=3, max_length=3)]
    limitations: list[NonEmptyText]


class PurchaseReview(ReviewContent):
    case_id: NonEmptyText
    revision: Revision
    snapshot_id: NonEmptyText
    selected_option_ids: SelectedOptionIds
    supplier_draft: str


class ReviewReady(ContractModel):
    status: Literal["ready"] = "ready"
    report: PurchaseReview


class ReviewFailed(ContractModel):
    status: Literal["failed"] = "failed"
    case_id: NonEmptyText
    revision: Revision
    code: ReviewErrorCode
    message: NonEmptyText


ReviewResult = Annotated[ReviewReady | ReviewFailed, Field(discriminator="status")]
