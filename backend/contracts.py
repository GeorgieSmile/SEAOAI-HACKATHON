from collections import defaultdict
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


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
