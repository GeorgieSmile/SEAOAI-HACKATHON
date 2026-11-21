from typing import Annotated, Literal

from pydantic import Field

from .contracts import ContractModel, NonEmptyText, Revision, SelectedOptionIds


ReviewErrorCode = Literal["invalid_input", "analysis_failed", "invalid_review"]
CoverageGroup = Literal["product_match", "intended_use", "supporting_evidence"]
CoverageStatus = Literal["reviewed", "limited", "not_assessed"]
FindingKind = Literal["listing_conflict", "requirement_unconfirmed", "clarification_needed", "evidence_gap"]
FindingAction = Literal["decide_preference", "ask_supplier", "request_evidence"]
UnknownReason = Literal["not_found", "not_reviewed", "unreadable"]
ReviewTopic = Literal[
    "capacity", "finish", "materials", "temperature_use", "testing", "customization",
    "packaging", "dimensions", "weight", "sample", "sku_wording", "deadline", "other",
]
SupplierAction = Literal[
    "confirm_specification", "reconcile_sources", "request_documentation",
    "confirm_ready_made", "confirm_custom_order", "request_sample", "request_close_up",
]
MeasurementUnit = Literal["ml", "L", "fl oz", "mm", "cm", "in", "g", "kg", "oz", "lb", "°C", "°F"]
VisibleDetail = Literal[
    "letters_visible", "illustration_visible", "straw_visible", "lid_visible",
    "handle_visible", "retail_box_visible", "protective_insert_visible", "markings_visible",
]


# Provider and retained generation record. Display strings are never accepted here.
class TextClaim(ContractModel):
    claim_id: NonEmptyText
    option_ids: Annotated[list[NonEmptyText], Field(max_length=1)]
    source_id: NonEmptyText
    kind: Literal["text_excerpt"]
    quote: NonEmptyText


class ImageMeasurementClaim(ContractModel):
    claim_id: NonEmptyText
    option_ids: Annotated[list[NonEmptyText], Field(min_length=1, max_length=1)]
    source_id: NonEmptyText
    kind: Literal["image_measurement"]
    value: Annotated[float, Field(ge=-1_000_000, le=1_000_000)]
    unit: MeasurementUnit


class ImageDetailClaim(ContractModel):
    claim_id: NonEmptyText
    option_ids: Annotated[list[NonEmptyText], Field(min_length=1, max_length=1)]
    source_id: NonEmptyText
    kind: Literal["image_detail"]
    detail: VisibleDetail


SourcedClaim = TextClaim | ImageMeasurementClaim | ImageDetailClaim


class RequirementSelection(ContractModel):
    requirement_id: NonEmptyText
    topic: ReviewTopic
    input_ref: NonEmptyText
    quote: NonEmptyText


class PreferenceSelection(ContractModel):
    preference_id: NonEmptyText
    topic: ReviewTopic
    input_refs: list[NonEmptyText]
    finding_ids: list[NonEmptyText]


class ComparisonValueSelection(ContractModel):
    option_id: NonEmptyText
    claim_ids: list[NonEmptyText]


class ComparisonSelection(ContractModel):
    topic: ReviewTopic
    values: list[ComparisonValueSelection]
    shared_claim_ids: list[NonEmptyText]


class UnknownSelection(ContractModel):
    reason: UnknownReason
    source_ids: list[NonEmptyText]


class FindingSelection(ContractModel):
    finding_id: NonEmptyText
    topic: ReviewTopic
    option_ids: SelectedOptionIds
    kind: FindingKind
    action_type: FindingAction
    requirement_ids: list[NonEmptyText]
    claim_ids: list[NonEmptyText]
    unknowns: list[UnknownSelection]
    supplier_action: SupplierAction | None


class CoverageSelection(ContractModel):
    group: CoverageGroup
    status: CoverageStatus
    finding_ids: list[NonEmptyText]


class ReviewContent(ContractModel):
    contract_version: Literal[2]
    claims: list[SourcedClaim]
    buyer_requirements: list[RequirementSelection]
    open_preferences: list[PreferenceSelection]
    comparisons: Annotated[list[ComparisonSelection], Field(min_length=1)]
    findings: list[FindingSelection]
    coverage: Annotated[list[CoverageSelection], Field(min_length=3, max_length=3)]


class SentReviewEvidence(ContractModel):
    text_ids: tuple[NonEmptyText, ...]
    attached_image_ids: tuple[NonEmptyText, ...]


class DisplayTextClaim(TextClaim):
    display_text: NonEmptyText


class DisplayImageMeasurementClaim(ImageMeasurementClaim):
    display_text: NonEmptyText


class DisplayImageDetailClaim(ImageDetailClaim):
    display_text: NonEmptyText


DisplayClaim = DisplayTextClaim | DisplayImageMeasurementClaim | DisplayImageDetailClaim


# Public report. The backend generates the display text from validated selections.
class BuyerRequirement(ContractModel):
    requirement_id: NonEmptyText
    text: NonEmptyText
    input_ref: NonEmptyText
    quote: NonEmptyText
    option_ids: SelectedOptionIds
    question_text: NonEmptyText | None


class PreferenceQuestion(ContractModel):
    question_id: NonEmptyText
    question_text: NonEmptyText
    option_ids: SelectedOptionIds


class OpenPreference(ContractModel):
    preference_id: NonEmptyText
    text: NonEmptyText
    input_refs: list[NonEmptyText]
    finding_ids: list[NonEmptyText]
    option_ids: SelectedOptionIds
    questions: list[PreferenceQuestion]


class ComparisonValue(ComparisonValueSelection):
    summary: NonEmptyText
    source_ids: list[NonEmptyText]
    limitations: list[NonEmptyText]


class Comparison(ContractModel):
    label: NonEmptyText
    values: list[ComparisonValue]
    shared_claim_ids: list[NonEmptyText]


class Citation(ContractModel):
    source_id: NonEmptyText
    quote: NonEmptyText | None
    image_observation: NonEmptyText | None


class Unknown(UnknownSelection):
    text: NonEmptyText


class Finding(ContractModel):
    finding_id: NonEmptyText
    title: NonEmptyText
    option_ids: SelectedOptionIds
    kind: FindingKind
    action_type: FindingAction
    requirement_ids: list[NonEmptyText]
    claim_ids: list[NonEmptyText]
    citations: list[Citation]
    interpretation: NonEmptyText
    impact: NonEmptyText
    priority_reason: NonEmptyText
    unknowns: list[Unknown]
    requested_evidence: NonEmptyText
    evidence_limits: NonEmptyText
    supplier_question: NonEmptyText | None


class Coverage(CoverageSelection):
    note: NonEmptyText


class PurchaseReview(ContractModel):
    contract_version: Literal[2]
    claims: list[DisplayClaim]
    buyer_requirements: list[BuyerRequirement]
    open_preferences: list[OpenPreference]
    comparisons: Annotated[list[Comparison], Field(min_length=1)]
    findings: list[Finding]
    coverage: Annotated[list[Coverage], Field(min_length=3, max_length=3)]
    limitations: list[NonEmptyText]
    case_id: NonEmptyText
    revision: Revision
    snapshot_id: NonEmptyText
    selected_option_ids: SelectedOptionIds
    supplier_draft: str


class ReviewReady(ContractModel):
    status: Literal["ready"] = "ready"
    report: PurchaseReview
    content: ReviewContent
    sent_evidence: SentReviewEvidence


class ReviewFailed(ContractModel):
    status: Literal["failed"] = "failed"
    case_id: NonEmptyText
    revision: Revision
    code: ReviewErrorCode
    message: NonEmptyText


ReviewResult = Annotated[ReviewReady | ReviewFailed, Field(discriminator="status")]
