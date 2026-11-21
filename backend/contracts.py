from collections import defaultdict
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator


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


class Answer(ContractModel):
    question_id: NonEmptyText
    answer_text: Annotated[str, Field(max_length=1_000)] | None

    @field_validator("answer_text")
    @classmethod
    def normalize_blank(cls, answer: str | None) -> str | None:
        return answer if answer is not None and answer.strip() else None


class AnsweredQuestion(Answer):
    question_text: NonEmptyText


class AnswerSubmission(ContractModel):
    round_id: NonEmptyText
    answers: Annotated[list[Answer], Field(min_length=1, max_length=5)]

    @model_validator(mode="after")
    def unique_answers(self) -> Self:
        if len({answer.question_id for answer in self.answers}) != len(self.answers):
            raise ValueError("Submit each question ID once")
        return self


class ReviewInput(ContractModel):
    case_id: NonEmptyText
    revision: Revision
    snapshot: ListingSnapshot
    selected_option_ids: SelectedOptionIds
    buyer_context: BuyerContext
    answers: Annotated[list[AnsweredQuestion], Field(max_length=15)]

    @model_validator(mode="after")
    def validate_case_evidence(self) -> Self:
        selected = set(self.selected_option_ids)
        if len(selected) != len(self.selected_option_ids):
            raise ValueError("The selected option IDs must be unique")
        if not selected.issubset(option.option_id for option in self.snapshot.options):
            raise ValueError("The selected options must be in the active snapshot")
        if len({answer.question_id for answer in self.answers}) != len(self.answers):
            raise ValueError("The answered question IDs must be unique")
        return self


class QuestionInput(ReviewInput):
    round_number: RoundNumber

    @model_validator(mode="after")
    def validate_round_history(self) -> Self:
        prior_rounds = self.round_number - 1
        if not prior_rounds <= len(self.answers) <= prior_rounds * 5:
            raise ValueError("The answer history must cover the earlier issued rounds")
        return self


class QuestionDraft(ContractModel):
    prompt: NonEmptyText
    why_ask: NonEmptyText
    option_ids: SelectedOptionIds
    source_ids: list[NonEmptyText]
    choices: list[NonEmptyText]


class IssuedQuestion(QuestionDraft):
    question_id: NonEmptyText


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
