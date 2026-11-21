from collections import defaultdict
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from .supplier_targets import DeliveryTarget, SupplierTarget


NonEmptyText = Annotated[str, StringConstraints(pattern=r"\S")]
IntakeErrorCode = Literal[
    "invalid_archive", "archive_limit", "unsupported_layout", "product_unreadable", "intake_failed"
]
SKU_NAME_SOURCE_LABELS = {"SKU name", "Original option name"}


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Source(ContractModel):
    source_id: NonEmptyText
    kind: Literal["listing_text", "listing_image", "official_guidance"]
    label: NonEmptyText
    option_ids: list[NonEmptyText]
    availability: Literal["available", "missing", "unreadable"]
    original_text: NonEmptyText | None
    text_en: NonEmptyText | None
    image_file: NonEmptyText | None
    url: NonEmptyText | None

    @model_validator(mode="after")
    def validate_content(self) -> Self:
        if len(self.option_ids) != len(set(self.option_ids)):
            raise ValueError("Source option IDs must be unique")

        available = self.availability == "available"
        if self.kind == "listing_image":
            if self.original_text is not None or self.text_en is not None:
                raise ValueError("Image sources cannot contain source text or a translation")
            if available != (self.image_file is not None):
                raise ValueError("Only available images can have a file locator")
        else:
            if self.image_file is not None:
                raise ValueError("Text sources cannot have a file locator")
            if available != (self.original_text is not None):
                raise ValueError("Only available text sources can have source text")
            if not available and self.text_en is not None:
                raise ValueError("Unavailable text cannot have a translation")
        return self


class Option(ContractModel):
    option_id: NonEmptyText
    name_original: NonEmptyText
    name_en: NonEmptyText | None
    image_source_id: NonEmptyText | None


class ListingSnapshot(ContractModel):
    linked_option_id: NonEmptyText | None = None
    snapshot_id: NonEmptyText
    title: NonEmptyText
    options: Annotated[list[Option], Field(min_length=1)]
    sources: list[Source]
    limitations: list[NonEmptyText]

    def option_name_sources(self) -> dict[str, Source]:
        by_option = defaultdict(list)
        for source in self.sources:
            if source.kind == "listing_text" and source.label in SKU_NAME_SOURCE_LABELS:
                if len(source.option_ids) != 1:
                    raise ValueError("A full-name listing source must refer to one option")
                by_option[source.option_ids[0]].append(source)

        name_sources = {}
        for option in self.options:
            matches = by_option[option.option_id]
            if len(matches) != 1 or matches[0].original_text != option.name_original:
                raise ValueError("Each option needs a single full-name listing source")
            name_sources[option.option_id] = matches[0]
        return name_sources

    @model_validator(mode="after")
    def validate_evidence_links(self) -> Self:
        option_ids = {option.option_id for option in self.options}
        if self.linked_option_id is not None and self.linked_option_id not in option_ids:
            raise ValueError("The linked SKU must belong to this listing.")
        sources_by_id = {source.source_id: source for source in self.sources}
        if len(option_ids) != len(self.options) or len(sources_by_id) != len(self.sources):
            raise ValueError("Snapshot option and source IDs must be unique")
        if any(not option_ids.issuperset(source.option_ids) for source in self.sources):
            raise ValueError("Source scope must refer to options in the snapshot")

        title_sources = [
            source
            for source in self.sources
            if source.kind == "listing_text" and source.label == "Product title"
        ]
        if (
            len(title_sources) != 1
            or title_sources[0].option_ids
            or title_sources[0].original_text != self.title
        ):
            raise ValueError("Snapshot title must match a shared Product title source")

        name_sources = self.option_name_sources()
        for option in self.options:
            if option.name_en != name_sources[option.option_id].text_en:
                raise ValueError("Option name must use the translation from its full-name source")

            if option.image_source_id is not None:
                image = sources_by_id.get(option.image_source_id)
                if image is None or image.kind != "listing_image" or option.option_id not in image.option_ids:
                    raise ValueError("Option image must be linked to a source scoped to that option")
        return self


class IntakeReady(ContractModel):
    status: Literal["ready"] = "ready"
    snapshot: ListingSnapshot


class IntakeFailed(ContractModel):
    status: Literal["failed"] = "failed"
    code: IntakeErrorCode
    message: NonEmptyText


IntakeResult = Annotated[IntakeReady | IntakeFailed, Field(discriminator="status")]


BuyerContext = Annotated[str, Field(max_length=2_000)]
Revision = Annotated[int, Field(ge=0)]
RoundNumber = Annotated[int, Field(ge=1, le=3)]
SelectedOptionIds = Annotated[list[NonEmptyText], Field(min_length=1, max_length=3)]
BuyerFacet = Literal["order_type", "decoration_method", "surface_finish", "capacity", "delivery_deadline", "other"]
ReviewTopic = Literal[
    "capacity", "finish", "materials", "temperature_use", "testing", "customization",
    "packaging", "dimensions", "weight", "sample", "sku_wording", "deadline", "other",
]
BUYER_FACET_TOPICS: dict[BuyerFacet, ReviewTopic] = {
    "order_type": "customization",
    "decoration_method": "finish",
    "surface_finish": "finish",
    "capacity": "capacity",
    "delivery_deadline": "deadline",
}
RequirementAction = Literal["keep", "replace", "waive", "add"]
RequirementStatus = Literal["active", "waived", "unresolved"]
OrderClarificationChoice = Literal["ready_made_only", "custom_orders_acceptable"]
ORDER_CONFLICT_PROMPT = "Should this option be ready-made only, or are custom orders acceptable?"
ORDER_CONFLICT_CHOICES: dict[OrderClarificationChoice, str] = {
    "ready_made_only": "Ready-made stock only",
    "custom_orders_acceptable": "Custom orders are acceptable",
}


class ContextRequirement(ContractModel):
    statement: NonEmptyText
    quote: NonEmptyText
    option_ids: SelectedOptionIds
    topic: ReviewTopic
    buyer_facet: BuyerFacet | None = None
    status: Literal["active", "unresolved"] = "active"
    supplier_target: SupplierTarget | None = None


class RequirementEffect(ContractModel):
    action: RequirementAction
    requirement_id: NonEmptyText | None

    @model_validator(mode="after")
    def validate_target(self) -> Self:
        if self.action == "add" and self.requirement_id is not None:
            raise ValueError("Adding creates a new requirement.")
        if self.action in {"keep", "replace"} and self.requirement_id is None:
            raise ValueError("Keeping or replacing needs an issued requirement ID.")
        return self


class ChoiceEffect(RequirementEffect):
    choice: Annotated[NonEmptyText, Field(max_length=1_000)]
    statement: NonEmptyText | None
    supplier_target: SupplierTarget | None = None

    @model_validator(mode="after")
    def validate_statement(self) -> Self:
        expected = self.choice if self.action in {"add", "replace"} else None
        if self.statement != expected:
            raise ValueError("Add and replace use the exact choice as their statement.")
        return self


class RequirementProvenance(ContractModel):
    input_ref: NonEmptyText
    quote: NonEmptyText | None
    question_text: NonEmptyText | None
    action: RequirementAction | Literal["unresolved"]
    option_ids: SelectedOptionIds
    context_start: Annotated[int, Field(ge=0)] | None = None


class BriefProposal(ContractModel):
    kind: Literal["ready_made", "custom", "custom_by_deadline", "deadline", "unclear"]
    option_ids: SelectedOptionIds
    date: DeliveryTarget | None

    @model_validator(mode="after")
    def valid_date_use(self) -> Self:
        if (self.kind == "deadline" and self.date is None) or (
            self.kind not in {"deadline", "custom_by_deadline"} and self.date is not None
        ):
            raise ValueError("Use an arrival date only for a deadline or a conditional custom order.")
        return self


class BriefChange(ContractModel):
    change_id: NonEmptyText
    revision: Revision
    after_round: Annotated[int, Field(ge=0, le=3)]
    text: Annotated[str, Field(min_length=1, max_length=1_000)]
    proposal: BriefProposal
    question: NonEmptyText | None = Field(default=None, exclude_if=lambda value: value is None)
    resolves_change_id: NonEmptyText | None = Field(default=None, exclude_if=lambda value: value is None)


class DeliveryCondition(ContractModel):
    follows_deadline: bool
    latest_arrival: DeliveryTarget | None


class ResolvedRequirement(ContractModel):
    requirement_id: NonEmptyText
    statement: NonEmptyText
    option_ids: SelectedOptionIds
    status: RequirementStatus
    topic: ReviewTopic
    buyer_facet: BuyerFacet | None = None
    supplier_target: SupplierTarget | None = None
    delivery_condition: DeliveryCondition | None = Field(default=None, exclude_if=lambda value: value is None)
    provenance: Annotated[list[RequirementProvenance], Field(min_length=1)]
    order_conflict: Literal[True] | None = Field(default=None, exclude_if=lambda conflict: conflict is None)


class RequirementTarget(ContractModel):
    requirement_id: NonEmptyText
    statement: NonEmptyText
    option_ids: SelectedOptionIds


class SubmittedAnswer(ContractModel):
    question_id: NonEmptyText
    answer_text: Annotated[str, Field(max_length=1_000)] | None
    requirement_effect: RequirementEffect | None = Field(default=None, exclude_if=lambda effect: effect is None)
    answer_mode: Literal["natural"] | None = Field(default=None, exclude_if=lambda mode: mode is None)

    @field_validator("answer_text")
    @classmethod
    def normalize_blank(cls, answer: str | None) -> str | None:
        return answer if answer is not None and answer.strip() else None

    @model_validator(mode="after")
    def effect_needs_answer(self) -> Self:
        if self.requirement_effect is not None and self.answer_text is None:
            raise ValueError("Submit a written answer to change a requirement.")
        if self.answer_mode == "natural" and (self.answer_text is None or self.requirement_effect is not None):
            raise ValueError("Submit the written answer on its own for interpretation.")
        return self


class RecordedNaturalAnswer(ContractModel):
    effect: RequirementEffect
    option_ids: SelectedOptionIds
    supplier_target: SupplierTarget | None
    delivery_condition: DeliveryCondition | None

    @model_validator(mode="after")
    def validate_terms(self) -> Self:
        if len(self.option_ids) != len(set(self.option_ids)):
            raise ValueError("An answer decision must use distinct options.")
        if self.effect.action in {"keep", "waive"} and (self.supplier_target or self.delivery_condition):
            raise ValueError("Keeping or waiving a decision does not add terms.")
        condition = self.delivery_condition
        if condition is not None and (
            self.supplier_target is not None or condition.follows_deadline == (condition.latest_arrival is not None)
        ):
            raise ValueError("A conditional order must use a specific arrival date or follow the case deadline.")
        return self


class Answer(SubmittedAnswer):
    natural_decision: RecordedNaturalAnswer | None = Field(default=None, exclude_if=lambda decision: decision is None)

    @model_validator(mode="after")
    def decision_needs_mode(self) -> Self:
        if self.natural_decision is not None and self.answer_mode != "natural":
            raise ValueError("A recorded decision needs a natural answer.")
        return self

    def effective_scope(self, issued_scope: list[str]) -> list[str]:
        return self.natural_decision.option_ids if self.natural_decision is not None else issued_scope


class AnsweredQuestion(Answer):
    question_text: NonEmptyText
    buyer_facet: BuyerFacet | None = Field(default=None, exclude_if=lambda facet: facet is None)


class AnswerSubmission(ContractModel):
    round_id: NonEmptyText
    answers: Annotated[list[SubmittedAnswer], Field(min_length=1, max_length=5)]

    @model_validator(mode="after")
    def unique_answers(self) -> Self:
        if len({answer.question_id for answer in self.answers}) != len(self.answers):
            raise ValueError("Submit each question ID once")
        return self


class OrderClarificationSelection(ContractModel):
    requirement_id: NonEmptyText
    option_id: NonEmptyText
    choice: OrderClarificationChoice


class OrderClarification(OrderClarificationSelection):
    clarification_id: NonEmptyText
    after_round: Annotated[int, Field(ge=0, le=3)]
    revision: Revision = Field(default=0, exclude_if=lambda value: value == 0)

    @property
    def answer_text(self) -> str:
        return ORDER_CONFLICT_CHOICES[self.choice]


class _CaseAnalysisInput(ContractModel):
    case_id: NonEmptyText
    revision: Revision
    snapshot: ListingSnapshot
    selected_option_ids: SelectedOptionIds
    buyer_context: BuyerContext
    answers: Annotated[list[AnsweredQuestion], Field(max_length=15)]
    requirements: list[ResolvedRequirement] | None = Field(default=None, exclude_if=lambda rows: rows is None)
    order_clarifications: list[OrderClarification] = Field(default_factory=list, exclude_if=lambda rows: not rows)
    brief_changes: list[BriefChange] = Field(default_factory=list, exclude_if=lambda rows: not rows)

    @model_validator(mode="after")
    def validate_case_evidence(self) -> Self:
        selected = set(self.selected_option_ids)
        if len(selected) != len(self.selected_option_ids):
            raise ValueError("The selected option IDs must be unique")
        if not selected.issubset(option.option_id for option in self.snapshot.options):
            raise ValueError("The selected options must be in the active snapshot")
        if len({answer.question_id for answer in self.answers}) != len(self.answers):
            raise ValueError("The answered question IDs must be unique")
        clarifications = {f"clarification:{row.clarification_id}": row for row in self.order_clarifications}
        if len(clarifications) != len(self.order_clarifications) or any(row.option_id not in selected for row in clarifications.values()):
            raise ValueError("Order clarifications must use distinct IDs and select one current option")
        brief_changes = {f"brief:{row.change_id}": row for row in self.brief_changes}
        if len(brief_changes) != len(self.brief_changes) or any(
            not selected.issuperset(row.proposal.option_ids)
            or len(set(row.proposal.option_ids)) != len(row.proposal.option_ids)
            for row in self.brief_changes
        ):
            raise ValueError("Brief changes must use distinct IDs and current options.")
        if self.requirements is not None:
            answers = {f"answer:{answer.question_id}": answer for answer in self.answers}
            ids = {row.requirement_id for row in self.requirements}
            if len(ids) != len(self.requirements):
                raise ValueError("Resolved requirement IDs must be unique")
            for row in self.requirements:
                if len(row.option_ids) != len(set(row.option_ids)) or not selected.issuperset(row.option_ids):
                    raise ValueError("Requirements must refer to distinct selected options")
                for entry in row.provenance:
                    scope = set(entry.option_ids)
                    if len(scope) != len(entry.option_ids) or not selected.issuperset(scope) or not scope.issuperset(row.option_ids):
                        raise ValueError("Requirement provenance must cover the resolved SKU scope")
                    if entry.input_ref == "context":
                        start, quote = entry.context_start, entry.quote
                        if start is None or quote is None or entry.question_text is not None or not self.buyer_context.startswith(quote, start):
                            raise ValueError("Requirement context provenance must quote its exact recorded span")
                    elif entry.input_ref.startswith("clarification:"):
                        choice = clarifications.get(entry.input_ref)
                        if (
                            choice is None or entry.quote != choice.answer_text or entry.question_text != ORDER_CONFLICT_PROMPT
                            or entry.option_ids != [choice.option_id] or entry.context_start is not None
                        ):
                            raise ValueError("Order clarification provenance must match the recorded buyer choice")
                    elif entry.input_ref.startswith("brief:"):
                        change = brief_changes.get(entry.input_ref)
                        if (
                            change is None or entry.quote != change.text or entry.option_ids != change.proposal.option_ids
                            or entry.question_text is not None or entry.context_start is not None
                        ):
                            raise ValueError("Brief provenance must match the saved correction and its option scope.")
                    else:
                        answer = answers.get(entry.input_ref)
                        if answer is None or answer.answer_text != entry.quote or answer.question_text != entry.question_text or entry.context_start is not None:
                            raise ValueError("Requirement answer provenance must match the issued question and exact answer")
        return self

    def _validate_answer_scope(self, scopes: dict[str, list[str]]) -> None:
        answers = {answer.question_id: answer for answer in self.answers}
        selected = set(self.selected_option_ids)
        if not answers.keys() >= scopes.keys():
            raise ValueError("Answer scope must refer to submitted question IDs.")
        for option_ids in scopes.values():
            if len(option_ids) != len(set(option_ids)) or not selected.issuperset(option_ids):
                raise ValueError("Answer scope must refer to distinct selected options.")
        for question_id, answer in answers.items():
            if answer.natural_decision is not None and not set(answer.natural_decision.option_ids).issubset(
                scopes.get(question_id, []),
            ):
                raise ValueError("An answer decision must stay within the issued question scope.")
        for row in self.requirements or []:
            for entry in row.provenance:
                if entry.input_ref.startswith("answer:"):
                    question_id = entry.input_ref[7:]
                    if answers[question_id].effective_scope(scopes.get(question_id, [])) != entry.option_ids:
                        raise ValueError("Requirement provenance must match the answer decision's option scope.")


class ReviewInput(_CaseAnalysisInput):
    answer_option_ids: dict[NonEmptyText, SelectedOptionIds] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_answer_scope(self) -> Self:
        self._validate_answer_scope(self.answer_option_ids)
        return self


class QuestionInput(_CaseAnalysisInput):
    round_number: RoundNumber
    answer_option_ids: dict[NonEmptyText, SelectedOptionIds] | None = Field(default=None, exclude_if=lambda scopes: scopes is None)

    @model_validator(mode="after")
    def validate_round_history(self) -> Self:
        prior_rounds = self.round_number - 1
        if not prior_rounds <= len(self.answers) <= prior_rounds * 5:
            raise ValueError("The answer history must cover the earlier issued rounds")
        if self.answer_option_ids is not None:
            self._validate_answer_scope(self.answer_option_ids)
        return self


class QuestionDraft(ContractModel):
    prompt: NonEmptyText
    why_ask: NonEmptyText
    option_ids: SelectedOptionIds
    source_ids: list[NonEmptyText]
    choices: list[NonEmptyText]
    buyer_facet: BuyerFacet | None = Field(default=None, exclude_if=lambda facet: facet is None)
    topic: ReviewTopic | None = Field(default=None, exclude_if=lambda topic: topic is None)
    requirement_ids: list[NonEmptyText] | None = Field(default=None, exclude_if=lambda ids: ids is None)
    choice_effects: list[ChoiceEffect] | None = Field(default=None, exclude_if=lambda effects: effects is None)


class IssuedQuestion(QuestionDraft):
    question_id: NonEmptyText
    requirement_targets: list[RequirementTarget] | None = Field(default=None, exclude_if=lambda rows: rows is None)


class IssuedRound(ContractModel):
    round_id: NonEmptyText
    round_number: RoundNumber
    reason: NonEmptyText
    questions: Annotated[list[IssuedQuestion], Field(min_length=1, max_length=5)]
    answers: Annotated[list[Answer], Field(min_length=1, max_length=5)] | None = None

    @model_validator(mode="after")
    def validate_issued_answers(self) -> Self:
        question_ids = [question.question_id for question in self.questions]
        if len(set(question_ids)) != len(question_ids):
            raise ValueError("Issued question IDs must be unique")
        if self.answers is not None and question_ids != [answer.question_id for answer in self.answers]:
            raise ValueError("Saved answers must match the issued questions in order")
        return self


class QuestionResult(ContractModel):
    reason: NonEmptyText
    questions: Annotated[list[QuestionDraft], Field(max_length=5)]
    context_requirements: Annotated[list[ContextRequirement], Field(max_length=30)] | None = None
