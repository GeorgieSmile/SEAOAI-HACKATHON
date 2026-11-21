from base64 import b64encode
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from itertools import combinations
import json
import logging
import re
from unicodedata import normalize

from openai import AsyncOpenAI
from openai.types.responses import ResponseInputContentParam, ResponseInputParam
from pydantic import ValidationError

from .ai import (
    OPTION_REFERENCES,
    PROMPT_DIRECTORY,
    _InvalidStructuredOutput,
    _ProviderFailure,
    _build_client,
    _request_structured,
)
from .contracts import BuyerFacet, Option, ReviewInput, SKU_NAME_SOURCE_LABELS, Source
from .intake import IMAGE_MIME
from .report_coverage import render_coverage, thumbnail_limitations
from .report_models import (
    BuyerRequirement,
    Citation,
    Comparison,
    ComparisonValue,
    DisplayClaim,
    DisplayImageDetailClaim,
    DisplayImageMeasurementClaim,
    DisplayTextClaim,
    Finding,
    FindingSelection,
    ImageDetailClaim,
    ImageMeasurementClaim,
    OpenPreference,
    PreferenceQuestion,
    PurchaseReview,
    ReviewContent,
    ReviewFailed,
    ReviewReady,
    ReviewResult,
    SentReviewEvidence,
    SourcedClaim,
    TextClaim,
    Unknown,
    UnknownSelection,
)


REVIEW_PROMPT_VERSION = "purchase-review-v4"
REVIEW_PROMPT = (PROMPT_DIRECTORY / f"{REVIEW_PROMPT_VERSION}.txt").read_text(encoding="utf-8")
REVIEW_DEADLINE_SECONDS = 90
REVIEW_MAX_OUTPUT_TOKENS = 12_288
DETAIL_IMAGE_LIMITATION = "Product-detail images were not reviewed in this version."
THUMBNAIL_LIMITATION = "Thumbnail observations cover supported labels and visible details. Verify the delivered specification separately."
COVERAGE_GROUPS = {"product_match", "intended_use", "supporting_evidence"}
TOPICS = {
    "capacity": ("Capacity", "the delivered capacity, its units, and whether it is usable or brim-full"),
    "finish": ("Decoration and finish", "the decoration process and finish"),
    "materials": ("Materials", "the materials of the product and its contact surfaces"),
    "temperature_use": ("Temperature and use", "the intended uses, supported temperature range, and restrictions"),
    "testing": ("Testing and documentation", "which tests or documents apply to the exact product and materials"),
    "customization": ("Stock and customization", "the available stock and any customization options"),
    "packaging": ("Packaging", "the delivered packaging and what is included"),
    "dimensions": ("Dimensions", "the delivered dimensions, units, and how they are measured"),
    "weight": ("Weight", "the delivered weight, units, and whether packaging is included"),
    "sample": ("Samples", "whether a sample of the exact option is available"),
    "sku_wording": ("SKU wording", "what is supplied under the exact listing name"),
    "deadline": ("Timing and deadline", "the expected ship date and delivery window"),
    "other": ("Delivered specification", "the specification of the exact item that would be delivered"),
}
UNIT_TOPICS = {
    "ml": "capacity", "L": "capacity", "fl oz": "capacity",
    "mm": "dimensions", "cm": "dimensions", "in": "dimensions",
    "g": "weight", "kg": "weight", "oz": "weight", "lb": "weight",
    "°C": "temperature_use", "°F": "temperature_use",
}
MEASUREMENT_FIELDS = {
    "capacity": "capacity", "容量": "capacity", "容积": "capacity",
    "dimensions": "dimensions", "尺寸": "dimensions",
    "weight": "weight", "重量": "weight",
    "temperature": "temperature_use", "温度": "temperature_use",
}
TEXT_READING = re.compile(
    r"(?:(?P<field>[^:]+):\s*)?(?P<value>-?\d+(?:\.\d+)?)\s*"
    r"(?P<unit>ml|l|fl\s*oz|mm|cm|in|g|kg|oz|lb|°c|°f)",
    re.IGNORECASE,
)
NUMBER_TOKEN = re.compile(
    r"[+\-−＋－]?(?:\d+(?:[.,．，٫٬\u00a0\u202f\u2009]\d+)*|[.．٫]\d+)(?:[eE][+\-−]?\d+)?"
)
UNIT_ALIASES = {unit.casefold().replace(" ", ""): unit for unit in UNIT_TOPICS}
UNIT_FACTORS = {
    "ml": ("ml", "1"), "L": ("ml", "1000"), "fl oz": ("fl oz", "1"),
    "mm": ("mm", "1"), "cm": ("mm", "10"), "in": ("mm", "25.4"),
    "g": ("g", "1"), "kg": ("g", "1000"), "oz": ("g", "28.349523125"), "lb": ("g", "453.59237"),
}
VISIBLE_DETAILS = {
    "letters_visible": ("finish", "Letter forms are visible"),
    "illustration_visible": ("finish", "An illustration is visible"),
    "straw_visible": ("other", "A straw is visible"),
    "lid_visible": ("other", "A lid is visible"),
    "handle_visible": ("other", "A handle is visible"),
    "retail_box_visible": ("packaging", "A box is visible"),
    "protective_insert_visible": ("packaging", "A protective insert is visible"),
    "markings_visible": ("sku_wording", "Markings are visible"),
}
BUYER_FACET_TOPICS: dict[BuyerFacet, str] = {
    "order_type": "customization",
    "decoration_method": "finish",
    "surface_finish": "finish",
    "capacity": "capacity",
    "delivery_deadline": "deadline",
}
FINISH_FACETS: tuple[BuyerFacet, ...] = ("decoration_method", "surface_finish")
SUPPLIER_TARGET_FACETS: dict[str, tuple[BuyerFacet, ...]] = {
    "capacity": ("capacity",),
    "finish": FINISH_FACETS,
}
BUYER_TARGETS: dict[BuyerFacet, dict[str, str]] = {
    "order_type": {
        "ready-made only": "ready_made",
        "ready-made stock only": "ready_made",
        "ready-made options only": "ready_made",
        "ready-made options are required": "ready_made",
        "i only want ready-made stock": "ready_made",
        "only ready-made": "ready_made",
        "custom orders are acceptable": "custom_order",
        "custom production is acceptable": "custom_order",
        "i can accept custom orders": "custom_order",
        "i would consider a custom order": "custom_order",
    },
    "decoration_method": {
        "screen printing is required": "screen_printing_required",
        "i require screen printing": "screen_printing_required",
        "i need screen printing": "screen_printing_required",
        "screen print only": "screen_printing_required",
        "screen printing only": "screen_printing_required",
        "screen printing is unacceptable": "screen_printing_avoided",
        "screen printing is not acceptable": "screen_printing_avoided",
        "i cannot accept screen printing": "screen_printing_avoided",
        "i do not accept screen printing": "screen_printing_avoided",
        "do not use screen printing": "screen_printing_avoided",
        "no screen printing": "screen_printing_avoided",
    },
}
FLEXIBLE_FINISH = {"any finish is acceptable", "any finish works", "no finish preference"}
FLEXIBLE_DECORATION_METHOD = {
    "any decoration method is acceptable", "any decoration process is acceptable", "screen printing is optional",
}
NEUTRAL_DECORATION_METHOD = {"i do not require screen printing", "screen printing is not required"}
REQUIRED_SURFACE_FINISH = re.compile(
    r"i (?:need|require) (?:a )?(?P<requested>matte|glossy) (?:finish|texture)"
    r"|(?:a )?(?P<stated>matte|glossy) (?:finish|texture) (?:is required|only)",
)
LEGACY_QUESTION_SCOPE = (
    r"(?: for (?:options? [abc](?:(?:, |/| and |, and )[abc])*|"
    r"this (?:option|order|purchase)|(?:all|these) (?:selected )?options))?\??"
)
LEGACY_DIRECT_QUESTIONS: tuple[tuple[tuple[BuyerFacet, ...], str], ...] = (
    (("order_type",),
     r"(?:would you consider (?:a custom order|custom production)|are custom orders acceptable|"
     r"do you accept custom orders|(?:would you prefer|do you require) ready-made(?: (?:stock|options))?(?: only)?|"
     r"has your position on custom orders changed)"),
    (("decoration_method",),
     r"(?:is screen printing required(?:, or are applied graphics acceptable)?|do you require screen printing|"
     r"are applied graphics acceptable|(?:what|which) decoration(?: (?:method|process))?"
     r"(?: do you (?:need|require|prefer)| is (?:required|acceptable)| works)?)"),
    (("surface_finish",),
     r"(?:what|which) (?:surface (?:finish|texture)|finish should the surface have)"
     r"(?: do you (?:need|require|prefer|want)| is (?:required|acceptable)| works)?"),
    (FINISH_FACETS,
     r"(?:what|which) finish(?: do you (?:need|require|prefer|want)| is (?:required|acceptable)| works)?"),
    (("capacity",),
     r"(?:do you (?:require|have|need) (?:an exact|a specific) capacity(?: requirement)?|"
     r"(?:what|which) capacity(?: do you (?:need|require|prefer|want)| is (?:required|acceptable)| works)?)"),
    (("delivery_deadline",),
     r"(?:what (?:is|would be) (?:your|the) (?:"
     r"(?:(?:required|revised|updated|new) )?delivery deadline|(?:required|latest acceptable|revised) delivery date)"
     r"|what delivery deadline should (?:we use|we plan for|the review consider)"
     r"|(?:by )?when do you need (?:delivery|(?:this|the) order delivered)"
     r"|when does (?:this|the) order need to (?:arrive|be delivered))"),
)
FINISH_SUBJECTS: dict[BuyerFacet, re.Pattern[str]] = {
    "decoration_method": re.compile(
        r"\b(?:screen[ -]?print(?:s|ed|ing)?|silk[ -]?(?:screen(?:ed|ing)?|mesh)|decals?|applied graphics?)\b",
    ),
    "surface_finish": re.compile(r"\b(?:matte|glossy|surface (?:finish|texture))\b"),
}
EXACT_BUYER_CAPACITY = re.compile(
    r"(?:(?:i (?:need|require) |exactly )(?P<first>[0-9]+(?:\.[0-9]+)?\s*(?:ml|l|fl oz))"
    r"|(?P<second>[0-9]+(?:\.[0-9]+)?\s*(?:ml|l|fl oz)) (?:is required|only))",
)
LOGGER = logging.getLogger(__name__)


def build_review_client() -> AsyncOpenAI | None:
    """Create the app-lifetime client only when live report generation is enabled."""
    return _build_client("OPENAI_LIVE_REVIEW")


def _selected_options(request: ReviewInput) -> list[Option]:
    options = {option.option_id: option for option in request.snapshot.options}
    return [options[option_id] for option_id in request.selected_option_ids]


def _review_limitations(request: ReviewInput, attached_image_ids: set[str]) -> list[str]:
    selected = _selected_options(request)
    selected_ids = set(request.selected_option_ids)
    excluded_prefixes = tuple(
        prefix
        for option in request.snapshot.options
        if option.option_id not in selected_ids
        for prefix in (f"{option.name_original} — ", f"{option.name_original} has no SKU-specific image")
    )
    limitations = [
        limitation for limitation in request.snapshot.limitations
        if not limitation.startswith(excluded_prefixes)
    ]
    sources = {source.source_id: source for source in request.snapshot.sources}
    for reference, option in zip(OPTION_REFERENCES, selected):
        image = sources.get(option.image_source_id) if option.image_source_id is not None else None
        if image is None:
            limitations.append(f"Option {reference} has no SKU-specific image reference.")
        elif image.availability != "available":
            limitations.append(f"The saved image for Option {reference} is {image.availability}.")
        elif image.source_id not in attached_image_ids:
            limitations.append(f"The saved image for Option {reference} was not attached for review.")
    if attached_image_ids:
        limitations.append(THUMBNAIL_LIMITATION)
    return list(dict.fromkeys([*limitations, DETAIL_IMAGE_LIMITATION]))


def _relevant_text_sources(request: ReviewInput) -> list[Source]:
    selected = set(request.selected_option_ids)
    return [
        source for source in request.snapshot.sources
        if source.kind != "listing_image" and (not source.option_ids or selected.intersection(source.option_ids))
    ]


def _available_mapped_image_ids(request: ReviewInput) -> tuple[str, ...]:
    available = {
        source.source_id for source in request.snapshot.sources
        if source.kind == "listing_image" and source.availability == "available"
    }
    return tuple(dict.fromkeys(
        option.image_source_id for option in _selected_options(request) if option.image_source_id in available
    ))


def review_sent_evidence(request: ReviewInput, images: Mapping[str, tuple[str, bytes]]) -> SentReviewEvidence:
    """Return the citable source IDs selected by the report request builder."""
    return SentReviewEvidence(
        text_ids=tuple(
            source.source_id for source in _relevant_text_sources(request) if source.availability == "available"
        ),
        attached_image_ids=tuple(
            image_id for image_id in _available_mapped_image_ids(request)
            if image_id in images and images[image_id][0] in IMAGE_MIME.values() and images[image_id][1]
        ),
    )


def _is_shared_source(source: Source) -> bool:
    return source.kind == "official_guidance" or source.label == "Shared selector value" or len(source.option_ids) != 1


@dataclass(frozen=True)
class ReviewEvidence:
    payload: ResponseInputParam
    sent_evidence: SentReviewEvidence
    metadata_only_image_ids: tuple[str, ...]


def _prepare_evidence(request: ReviewInput, images: Mapping[str, tuple[str, bytes]]) -> ReviewEvidence:
    selected = set(request.selected_option_ids)
    image_inventory = [source for source in request.snapshot.sources if source.kind == "listing_image"]
    sent = review_sent_evidence(request, images)
    attached_ids = set(sent.attached_image_ids)
    metadata_only = tuple(source.source_id for source in image_inventory if source.source_id not in attached_ids)
    data = {
        "selected_options": [
            {
                "option_id": option.option_id,
                "reference": reference,
                "name_original": option.name_original,
                "name_en": option.name_en,
                "image_source_id": option.image_source_id,
            }
            for reference, option in zip(OPTION_REFERENCES, _selected_options(request))
        ],
        "buyer_context": request.buyer_context,
        "answers": [
            {**answer.model_dump(), "option_ids": request.answer_option_ids.get(answer.question_id, [])}
            for answer in request.answers
        ],
        "text_sources": [
            {
                "source_id": source.source_id,
                "kind": source.kind,
                "label": source.label,
                "option_ids": [option_id for option_id in source.option_ids if option_id in selected],
                "claim_scope": "shared" if _is_shared_source(source) else "sku",
                "availability": source.availability,
                "original_text": source.original_text,
                "text_en": source.text_en,
            }
            for source in _relevant_text_sources(request)
        ],
        "image_inventory": [
            source.model_dump(include={"source_id", "label", "option_ids", "availability"})
            for source in image_inventory
        ],
        "attached_image_ids": sent.attached_image_ids,
        "metadata_only_image_ids": metadata_only,
        "limitations": _review_limitations(request, attached_ids),
    }
    content: list[ResponseInputContentParam] = [{"type": "input_text", "text": json.dumps(data, ensure_ascii=False)}]
    included_images: dict[tuple[str, bytes], list[str]] = {}
    for image_id in sent.attached_image_ids:
        included_images.setdefault(images[image_id], []).append(image_id)
    for (mime, image_bytes), source_ids in included_images.items():
        content.append({"type": "input_text", "text": f"Attached image source IDs: {', '.join(source_ids)}"})
        content.append({
            "type": "input_image", "detail": "auto",
            "image_url": f"data:{mime};base64,{b64encode(image_bytes).decode('ascii')}",
        })
    return ReviewEvidence(
        payload=[{"role": "user", "content": content}],
        sent_evidence=sent,
        metadata_only_image_ids=metadata_only,
    )


def _unique_ids(ids: Iterable[str], field: str) -> set[str]:
    values = list(ids)
    unique = set(values)
    if len(unique) != len(values):
        raise ValueError(f"Do not repeat IDs in {field}.")
    return unique


def _validate_refs(ids: Iterable[str], allowed: Iterable[str], field: str) -> set[str]:
    refs = _unique_ids(ids, field)
    if not refs.issubset(allowed):
        raise ValueError(f"Use only valid IDs in {field}.")
    return refs


def _measurement(claim: ImageMeasurementClaim) -> str:
    value = str(int(claim.value)) if claim.value.is_integer() else str(claim.value)
    return f"{value} {claim.unit}"


def _quote_keeps_whole_numbers(quote: str, original: str) -> bool:
    split_positions = {
        position for number in NUMBER_TOKEN.finditer(original)
        for position in range(number.start() + 1, number.end())
    }
    # The same words can occur more than once, with a valid occurrence after a truncated one.
    start = original.find(quote)
    while start != -1:
        if start not in split_positions and start + len(quote) not in split_positions:
            return True
        start = original.find(quote, start + 1)
    return False


def _render_claim(
    claim: SourcedClaim, source: Source, option_by_id: Mapping[str, Option], sent: SentReviewEvidence,
) -> DisplayClaim:
    if not isinstance(claim, TextClaim):
        (option_id,) = claim.option_ids
        option = option_by_id.get(option_id)
        if (
            source.kind != "listing_image" or source.source_id not in sent.attached_image_ids
            or option is None or option.image_source_id != source.source_id or source.option_ids != [option_id]
        ):
            raise ValueError("An image claim needs the attached thumbnail mapped exclusively to its selected SKU.")
        if isinstance(claim, ImageMeasurementClaim):
            if claim.value <= 0 and UNIT_TOPICS[claim.unit] != "temperature_use":
                raise ValueError("A thumbnail label for capacity, dimensions, or weight must be positive.")
            return DisplayImageMeasurementClaim(**claim.model_dump(), display_text=f"Thumbnail label: {_measurement(claim)}")
        return DisplayImageDetailClaim(**claim.model_dump(), display_text=f"Thumbnail detail: {VISIBLE_DETAILS[claim.detail][1]}.")

    if (
        source.kind == "listing_image" or source.source_id not in sent.text_ids
        or source.original_text is None or claim.quote not in source.original_text
    ):
        raise ValueError("A text claim needs an exact quote from an available text source sent for review.")
    expected_scope = [] if _is_shared_source(source) else source.option_ids
    if claim.option_ids != expected_scope or not set(claim.option_ids).issubset(option_by_id):
        raise ValueError("A text claim must use the owning SKU or an empty scope for shared text.")
    is_excerpt = claim.quote != source.original_text
    if is_excerpt:
        if source.label == "Shared selector value":
            raise ValueError("Quote the complete shared selector value.")
        if not _quote_keeps_whole_numbers(claim.quote, source.original_text):
            raise ValueError("Quote whole numbers from the source, including any sign and separators.")

    if source.kind == "official_guidance":
        label = "Official guidance"
    elif source.label == "Shared selector value":
        label = "Shared selector"
    elif _is_shared_source(source):
        label = "Shared listing text"
    elif source.label in SKU_NAME_SOURCE_LABELS:
        label = "SKU wording"
    else:
        label = "SKU listing text"
    if is_excerpt:
        if source.label not in SKU_NAME_SOURCE_LABELS and NUMBER_TOKEN.search(claim.quote):
            display_text = f"{label} (open source for context)"
        else:
            display_text = f"{label} excerpt: “{claim.quote}”"
        return DisplayTextClaim(**claim.model_dump(), display_text=display_text)
    wording = source.original_text
    if source.text_en is not None:
        label = f"{label} (source translation)"
        wording = source.text_en
    return DisplayTextClaim(**claim.model_dump(), display_text=f"{label}: {wording}")


def _image_claim_topic(claim: SourcedClaim) -> str | None:
    if isinstance(claim, ImageMeasurementClaim):
        return UNIT_TOPICS[claim.unit]
    if isinstance(claim, ImageDetailClaim):
        return VISIBLE_DETAILS[claim.detail][0]
    return None


def _check_claim_topic(claim: SourcedClaim, source: Source, topic: str) -> None:
    if (image_topic := _image_claim_topic(claim)) is not None and image_topic != topic:
        raise ValueError("Use a thumbnail observation with its matching topic.")
    if isinstance(claim, TextClaim) and source.original_text is not None:
        # Check the structured source, including when the selected excerpt is only part of a standalone field.
        reading = _reading(claim.model_copy(update={"quote": source.original_text}), source)
        if reading is not None and UNIT_TOPICS[reading.unit] != topic:
            raise ValueError("Use a standalone text measurement with its matching topic.")


@dataclass(frozen=True)
class _Reading:
    claim: SourcedClaim
    value: Decimal
    unit: str
    label: str

    def normalized(self) -> tuple[str, Decimal]:
        if self.unit == "°C":
            return "temperature", self.value * 9
        if self.unit == "°F":
            return "temperature", (self.value - 32) * 5
        unit, factor = UNIT_FACTORS[self.unit]
        return unit, self.value * Decimal(factor)

    def display(self) -> str:
        return f"{self.label} {self.value.normalize():f} {self.unit}"


def _reading(claim: SourcedClaim, source: Source) -> _Reading | None:
    if isinstance(claim, ImageMeasurementClaim):
        return _Reading(claim, Decimal(str(claim.value)), claim.unit, "thumbnail label")
    if not isinstance(claim, TextClaim) or source.kind != "listing_text" or source.label in SKU_NAME_SOURCE_LABELS:
        return None
    # A full standalone specification can be compared. A selected substring of arbitrary text
    # cannot turn a part number, negated statement, or other context into a measurement.
    if claim.quote != source.original_text:
        return None
    match = TEXT_READING.fullmatch(normalize("NFKC", claim.quote).strip())
    if match is None:
        return None
    unit = UNIT_ALIASES["".join(match["unit"].casefold().split())]
    if match["field"] is not None:
        topic = MEASUREMENT_FIELDS.get(match["field"].strip().casefold())
        if topic != UNIT_TOPICS[unit]:
            return None
    elif source.label != "Shared selector value":
        return None
    value = Decimal(match["value"])
    if (value <= 0 and UNIT_TOPICS[unit] != "temperature_use") or abs(value) > 1_000_000:
        return None
    if source.label == "Shared selector value":
        label = "shared selector"
    else:
        label = "saved SKU specification" if claim.option_ids else "shared saved specification"
    return _Reading(claim, value, unit, label)


def _conflicting_readings(
    finding: FindingSelection, claims: list[DisplayClaim], sources: Mapping[str, Source], aliases: Mapping[str, str],
) -> list[str]:
    readings = [
        reading for claim in claims
        if (reading := _reading(claim, sources[claim.source_id])) is not None and UNIT_TOPICS[reading.unit] == finding.topic
    ]
    differences = []
    for option_id, alias in aliases.items():
        if option_id not in finding.option_ids:
            continue
        local = [reading for reading in readings if not reading.claim.option_ids or option_id in reading.claim.option_ids]
        for first, second in combinations(local, 2):
            if first.claim.source_id == second.claim.source_id or not (first.claim.option_ids or second.claim.option_ids):
                continue
            first_unit, first_value = first.normalized()
            second_unit, second_value = second.normalized()
            if first_unit == second_unit and first_value != second_value:
                differences.append(f"Option {alias}: {first.display()} and {second.display()}.")
                break
        else:
            raise ValueError("A listing conflict requires comparable, different readings from separate sources for each affected SKU.")
    return differences


def _scoped_sources(source_ids: Iterable[str], sources: Mapping[str, Source], scope: set[str]) -> list[Source]:
    _validate_refs(source_ids, sources, "unknown sources")
    scoped = [sources[source_id] for source_id in source_ids]
    if any(source.option_ids and scope.isdisjoint(source.option_ids) for source in scoped):
        raise ValueError("Unknown sources must belong to an option covered by the finding.")
    return scoped


def _render_unknown(
    selection: UnknownSelection, finding: FindingSelection, sources: Mapping[str, Source], sent: set[str],
    observed_image_topics: set[tuple[str, str]],
) -> Unknown:
    scoped = _scoped_sources(selection.source_ids, sources, set(finding.option_ids))
    # A source-specific unknown can cover only part of the finding. Source links retain that narrower scope.
    topic = TOPICS[finding.topic][0].lower()
    if selection.reason == "not_found":
        if not set(selection.source_ids).issubset(sent):
            raise ValueError("Use not_found only for sources whose content was sent for review.")
        text = f"The reviewed material did not establish the answer for {topic}."
    elif selection.reason == "not_reviewed":
        if not scoped or any(source.availability != "available" or source.source_id in sent for source in scoped):
            raise ValueError("Use not_reviewed only for available sources whose content was not sent.")
        text = f"The referenced source was not included when reviewing {topic}."
    else:
        if not scoped or any(
            source.availability == "available" and (source.kind != "listing_image" or source.source_id not in sent)
            for source in scoped
        ):
            raise ValueError("Use unreadable for missing or unreadable sources, or for an attached image that is illegible.")
        if any((source.source_id, finding.topic) in observed_image_topics for source in scoped):
            raise ValueError("Do not report both a readable observation and unreadable evidence for the same thumbnail and topic.")
        if all(source.availability == "missing" for source in scoped):
            text = f"The referenced source is missing, so {topic} could not be checked there."
        else:
            text = f"The referenced evidence for {topic} could not be read."
    return Unknown(**selection.model_dump(), text=text)


@dataclass(frozen=True)
class _BuyerTarget:
    kind: str
    measurement: str | None = None


@dataclass(frozen=True)
class _BuyerPosition:
    facets: tuple[BuyerFacet, ...] = ()
    target: _BuyerTarget | None = None
    flexible: bool = False


@dataclass(frozen=True)
class _BuyerInput:
    text: str | None
    option_ids: list[str]
    position: _BuyerPosition
    question: PreferenceQuestion | None = None


def _buyer_inputs(request: ReviewInput) -> dict[str, _BuyerInput]:
    inputs = {"context": _BuyerInput(
        request.buyer_context, request.selected_option_ids, _buyer_position(request.buyer_context, None),
    )}
    for answer in request.answers:
        option_ids = request.answer_option_ids.get(answer.question_id, [])
        inputs[f"answer:{answer.question_id}"] = _BuyerInput(
            answer.answer_text,
            option_ids,
            _buyer_position(answer.answer_text, answer.buyer_facet, question_text=answer.question_text),
            PreferenceQuestion(
                question_id=answer.question_id, question_text=answer.question_text, option_ids=option_ids,
            ) if option_ids else None,
        )
    return inputs


def _normalize_buyer_text(text: str) -> str:
    return " ".join(normalize("NFKC", text).casefold().replace("’", "'").split()).removesuffix(".")


def _buyer_position(
    text: str | None, facet: BuyerFacet | None, *, question_text: str = "",
) -> _BuyerPosition:
    declared_facets = (facet,) if facet is not None and facet in BUYER_FACET_TOPICS else ()
    if not text or not text.strip():
        return _BuyerPosition(declared_facets)
    wording = _normalize_buyer_text(text)
    if wording in FLEXIBLE_FINISH:
        if facet is None:
            # A broad legacy waiver can affect either finish facet; withhold both directions.
            return _BuyerPosition(FINISH_FACETS, flexible=True)
        return _BuyerPosition(declared_facets if facet in FINISH_FACETS else (), flexible=True)
    if wording in FLEXIBLE_DECORATION_METHOD:
        return _BuyerPosition(("decoration_method",), flexible=True)
    if wording in NEUTRAL_DECORATION_METHOD:
        return _BuyerPosition(("decoration_method",))
    # Directional targets come only from complete, supported buyer statements.
    for target_facet, statements in BUYER_TARGETS.items():
        if target := statements.get(wording):
            return _BuyerPosition((target_facet,), _BuyerTarget(target))
    if (match := REQUIRED_SURFACE_FINISH.fullmatch(wording)) is not None:
        finish = match["requested"] or match["stated"]
        return _BuyerPosition(("surface_finish",), _BuyerTarget(f"{finish}_finish_required"))
    if (match := EXACT_BUYER_CAPACITY.fullmatch(wording)) is not None:
        reading = TEXT_READING.fullmatch(match["first"] or match["second"])
        assert reading is not None
        value = Decimal(reading["value"])
        if 0 < value <= 1_000_000:
            unit = UNIT_ALIASES["".join(reading["unit"].split())]
            return _BuyerPosition(("capacity",), _BuyerTarget("capacity_required", f"{value.normalize():f} {unit}"))
    if facet in (None, "other"):
        # Historical direct questions identify which decision to withhold, but supply no target.
        question = _normalize_buyer_text(question_text)
        for question_facets, pattern in LEGACY_DIRECT_QUESTIONS:
            if facet == "other" and question_facets != ("delivery_deadline",):
                continue
            if re.fullmatch(pattern + LEGACY_QUESTION_SCOPE, question):
                return _BuyerPosition(question_facets)
    return _BuyerPosition(declared_facets)


def _current_buyer_inputs(
    inputs: Mapping[str, _BuyerInput],
) -> dict[tuple[BuyerFacet, str], str]:
    current: dict[tuple[BuyerFacet, str], str] = {}
    for ref, entry in inputs.items():
        if not entry.text or not entry.text.strip():
            continue
        for current_facet in entry.position.facets:
            for option_id in entry.option_ids:
                current[current_facet, option_id] = ref
    return current


def _requirement_facets(source: _BuyerInput, topic: str) -> tuple[BuyerFacet, ...]:
    if source.position.facets:
        if any(BUYER_FACET_TOPICS[facet] != topic for facet in source.position.facets):
            raise ValueError("Use the topic that matches the buyer requirement's decision.")
        return source.position.facets
    if topic == "deadline":
        # The report topic also includes independent dates for shipment, samples and ordering.
        return ()
    if topic == "finish":
        wording = _normalize_buyer_text(source.text or "")
        # A subject can narrow which later answer makes an old quote stale. It carries no target.
        if subjects := tuple(facet for facet, pattern in FINISH_SUBJECTS.items() if pattern.search(wording)):
            return subjects
    return tuple(facet for facet, facet_topic in BUYER_FACET_TOPICS.items() if facet_topic == topic)


def _linked_target(
    current: Mapping[tuple[BuyerFacet, str], str], inputs: Mapping[str, _BuyerInput],
    facet: BuyerFacet, option_ids: list[str], input_refs: set[str],
) -> _BuyerTarget | None:
    targets = set()
    for option_id in option_ids:
        ref = current.get((facet, option_id))
        if ref is None or ref not in input_refs:
            return None
        target = inputs[ref].position.target
        if target is None:
            return None
        targets.add(target)
    return next(iter(targets)) if len(targets) == 1 else None


def _target_question(target: _BuyerTarget) -> str:
    if target.kind == "screen_printing_required":
        return "Can each named option be supplied with screen printing?"
    if target.kind == "screen_printing_avoided":
        return "Can each named option be supplied without screen printing? Please identify the alternative finish."
    if target.kind == "capacity_required":
        return f"Can each named option meet {target.measurement}, and is that capacity usable or brim-full?"
    if target.kind == "matte_finish_required":
        return "Can each named option be supplied with a matte finish?"
    if target.kind == "glossy_finish_required":
        return "Can each named option be supplied with a glossy finish?"
    return ""


def _render_next_step(topic: str, action: str | None, differences: list[str]) -> tuple[str | None, str, str]:
    _, spec = TOPICS[topic]
    seller_limit = "A written answer identifies the seller's stated specification. Verification requires evidence for the exact item."
    if action is None:
        return None, "Choose what matters for this purchase before asking the supplier.", "This decision describes the buying criteria."
    if action == "reconcile_sources":
        question = (
            f"Do these saved readings describe the same specification? {' '.join(differences)} "
            f"Please confirm {spec} for each named option and send evidence identifying that option."
        )
        return question, "Ask for the delivered specification, the explanation for different readings, and SKU-specific evidence.", seller_limit
    if action == "request_documentation":
        return (
            f"Could you confirm {spec} for each named option and send the applicable specification or test documents "
            "showing the exact model and materials?",
            "Ask for the specification or test documents identifying the model, material, and conditions covered.",
            "A document applies only to the identified sample, materials, and test conditions.",
        )
    if action == "confirm_ready_made":
        return (
            f"Please confirm {spec} for these exact options. Please answer for ready-made stock.",
            "Ask the supplier for the specification of each ready-made option.",
            seller_limit,
        )
    if action == "confirm_custom_order":
        return (
            f"Please confirm {spec} for these exact options. If a different specification requires a custom order, "
            "please describe what is available and give the lead time and minimum order quantity.",
            "Ask which specification is available from stock and which would require a custom order.",
            "Custom-order availability does not establish the specification of a ready-made SKU.",
        )
    if action == "request_sample":
        return (
            f"Could you send a sample of each named option and confirm {spec}?",
            "Ask for a sample labelled with the exact option name and its specification.",
            "A sample shows the inspected piece. Delivered batches may differ.",
        )
    if action == "request_close_up":
        return (
            f"Could you send a close-up photo of each named option and confirm {spec}?",
            "Ask for a close-up with the exact option name and readable markings.",
            "A photo can show visible details. It cannot establish material, use limits, or how a finish was made.",
        )
    return (
        f"Please confirm {spec} for each named option and identify it by its exact listing name.",
        "Ask for a written specification identifying the exact listing option.",
        seller_limit,
    )


def _render_finding(
    finding: FindingSelection, claims: list[DisplayClaim], unknowns: list[Unknown], differences: list[str],
    order: _BuyerTarget | None, product_targets: list[_BuyerTarget],
) -> Finding:
    label, _ = TOPICS[finding.topic]
    title = {
        "decide_preference": f"Decide what you need for {label.lower()}",
        "ask_supplier": f"Confirm {label.lower()} with the supplier",
        "request_evidence": f"Request evidence for {label.lower()}",
    }[finding.action_type]
    interpretations = {
        "listing_conflict": "Compare the cited seller readings and verify the delivered specification and measurement method.",
        "requirement_unconfirmed": "The cited evidence leaves the linked buyer requirement to confirm for these exact options.",
        "clarification_needed": "Confirm the specification with the supplier for the named options.",
        "evidence_gap": "Ask for evidence that identifies the exact options and the specification under review.",
    }
    action = finding.supplier_action
    if action in {"confirm_specification", "confirm_ready_made", "confirm_custom_order"}:
        action = {
            "ready_made": "confirm_ready_made",
            "custom_order": "confirm_custom_order",
        }.get(order.kind if order is not None else "", "confirm_specification")
    question, requested_evidence, limits = _render_next_step(finding.topic, action, differences)
    if question is not None and product_targets:
        question = " ".join([question, *(_target_question(target) for target in product_targets)])
    return Finding(
        **finding.model_dump(include={"finding_id", "option_ids", "kind", "action_type", "requirement_ids", "claim_ids"}),
        title=title,
        citations=[
            Citation(
                source_id=claim.source_id,
                quote=claim.quote if isinstance(claim, TextClaim) else None,
                image_observation=None if isinstance(claim, TextClaim) else claim.display_text,
            )
            for claim in claims
        ],
        interpretation=interpretations[finding.kind],
        impact=(
            f"Confirm {label.lower()} against the linked buyer requirement before choosing an option."
            if finding.requirement_ids else f"Knowing {label.lower()} helps compare what you would receive."
        ),
        priority_reason=(
            "This check may affect whether an option meets a stated buyer requirement."
            if finding.requirement_ids else "Checking this before ordering can resolve an open detail about the exact item."
        ),
        unknowns=unknowns,
        requested_evidence=requested_evidence,
        evidence_limits=limits,
        supplier_question=question,
    )


def _supplier_draft(request: ReviewInput, findings: list[Finding]) -> str:
    questions = []
    for finding in findings:
        if finding.supplier_question is None:
            continue
        letters = [letter for option_id, letter in zip(request.selected_option_ids, OPTION_REFERENCES)
                   if option_id in finding.option_ids]
        scope = "Option" if len(letters) == 1 else "Options"
        questions.append(f"[{scope} {', '.join(letters)}] {finding.supplier_question}")
    if not questions:
        return ""
    options = [
        f"{reference}. {' '.join(option.name_original.split())}"
        for reference, option in zip(OPTION_REFERENCES, _selected_options(request))
    ]
    return "\n".join([
        "Hello, I am reviewing these exact options:",
        *options,
        "",
        "Please identify the relevant option names in your reply.",
        "",
        "\n\n".join(f"{index}. {question}" for index, question in enumerate(questions, start=1)),
    ])


def assemble_review(
    request: ReviewInput, content: ReviewContent, *, sent_evidence: SentReviewEvidence,
) -> PurchaseReview:
    """Validate structured selections against the case, then render the public report."""
    if sent_evidence.text_ids != tuple(
        source.source_id for source in _relevant_text_sources(request) if source.availability == "available"
    ):
        raise ValueError("Sent text evidence must match the available text for the selected case.")
    _validate_refs(sent_evidence.attached_image_ids, _available_mapped_image_ids(request), "attached thumbnails")
    sources = {source.source_id: source for source in request.snapshot.sources}
    option_by_id = {option.option_id: option for option in _selected_options(request)}
    selected = set(option_by_id)
    aliases = dict(zip(request.selected_option_ids, OPTION_REFERENCES))
    sent = set(sent_evidence.text_ids) | set(sent_evidence.attached_image_ids)

    _unique_ids((claim.claim_id for claim in content.claims), "claims")
    claims = {}
    for claim in content.claims:
        if claim.source_id not in sent:
            raise ValueError("Claims must cite an available source actually sent for review.")
        claims[claim.claim_id] = _render_claim(claim, sources[claim.source_id], option_by_id, sent_evidence)
    observed_image_topics = {
        (claim.source_id, topic) for claim in claims.values()
        if (topic := _image_claim_topic(claim)) is not None
    }

    inputs = _buyer_inputs(request)
    current_inputs = _current_buyer_inputs(inputs)
    input_order = {ref: index for index, ref in enumerate(inputs)}
    _unique_ids((row.requirement_id for row in content.buyer_requirements), "buyer requirements")
    requirement_by_id = {row.requirement_id: row for row in content.buyer_requirements}
    requirements = []
    requirement_scopes = {}
    for row in content.buyer_requirements:
        source = inputs.get(row.input_ref)
        if source is None or not source.text or source.text.strip() != row.quote.strip():
            raise ValueError("Each requirement needs the full verbatim quote from its non-null buyer input reference.")
        if not source.option_ids:
            raise ValueError("Buyer answer requirements need their issued option scope.")
        if source.position.flexible:
            raise ValueError("An answer that explicitly allows any finish or method is not a requirement.")
        facets = _requirement_facets(source, row.topic)
        source_index = input_order[row.input_ref]
        scope = [
            option_id for option_id in request.selected_option_ids
            if option_id in source.option_ids
            and not any(
                input_order[current_inputs.get((facet, option_id), row.input_ref)] > source_index for facet in facets
            )
        ]
        if not scope:
            raise ValueError("Use the current buyer input for a requirement in its issued option scope.")
        requirement_scopes[row.requirement_id] = set(scope)
        requirements.append(BuyerRequirement(
            **row.model_dump(exclude={"topic", "quote"}),
            text=source.text.strip(), quote=source.text.strip(), option_ids=scope,
            question_text=source.question.question_text if source.question is not None else None,
        ))

    comparisons = []
    _unique_ids((comparison.topic for comparison in content.comparisons), "comparison topics")
    for comparison in content.comparisons:
        compared = _validate_refs((value.option_id for value in comparison.values), selected, "comparison values")
        if compared != selected:
            raise ValueError("Each comparison must contain every selected option once.")
        _validate_refs(comparison.shared_claim_ids, claims, "shared comparison claims")
        for claim_id in comparison.shared_claim_ids:
            claim = claims[claim_id]
            scope = sources[claim.source_id].option_ids
            if claim.option_ids or (scope and not selected.issubset(scope)):
                raise ValueError("A shared comparison claim must cover the entire comparison without claiming a SKU.")
            _check_claim_topic(claim, sources[claim.source_id], comparison.topic)
        values_by_id = {value.option_id: value for value in comparison.values}
        values = []
        for option_id in request.selected_option_ids:
            value = values_by_id[option_id]
            _validate_refs(value.claim_ids, claims, "comparison claims")
            references = [claims[claim_id] for claim_id in value.claim_ids]
            for claim in references:
                if claim.option_ids != [option_id]:
                    raise ValueError("A comparison cell can cite only claims owned by its exact SKU.")
                _check_claim_topic(claim, sources[claim.source_id], comparison.topic)
            numeric_topic = comparison.topic in UNIT_TOPICS.values()
            visual_topic = any(topic == comparison.topic for topic, _ in VISIBLE_DETAILS.values())
            limits = []
            if numeric_topic or visual_topic:
                option = option_by_id[option_id]
                aspect = f"{comparison.topic.replace('_', ' ')} label" if numeric_topic else "visible detail"
                unreadable = any(
                    finding.topic == comparison.topic and option_id in finding.option_ids
                    and any(u.reason == "unreadable" and option.image_source_id in u.source_ids for u in finding.unknowns)
                    for finding in content.findings
                )
                limits = thumbnail_limitations(option, aliases[option_id], aspect, sources, sent_evidence, unreadable)
            values.append(ComparisonValue(
                **value.model_dump(),
                summary="\n".join(claim.display_text for claim in references) or "No SKU-specific evidence cited for this topic.",
                source_ids=list(dict.fromkeys(claim.source_id for claim in references)),
                limitations=limits,
            ))
        comparisons.append(Comparison(
            label=TOPICS[comparison.topic][0], values=values, shared_claim_ids=comparison.shared_claim_ids,
        ))

    finding_ids = _unique_ids((finding.finding_id for finding in content.findings), "findings")
    finding_by_id = {finding.finding_id: finding for finding in content.findings}
    findings = []
    for finding in content.findings:
        scope = _validate_refs(finding.option_ids, selected, "finding options")
        linked_requirements = _validate_refs(finding.requirement_ids, requirement_by_id, "finding requirements")
        if any(not scope.issubset(requirement_scopes[requirement_id]) for requirement_id in linked_requirements):
            raise ValueError("A finding must stay within each linked buyer requirement's current option scope.")
        _validate_refs(finding.claim_ids, claims, "finding claims")
        references = [claims[claim_id] for claim_id in finding.claim_ids]
        for claim in references:
            if claim.option_ids and not scope.issuperset(claim.option_ids):
                raise ValueError("A finding must cover the SKU that owns each SKU-specific claim.")
            if not claim.option_ids and (shared_scope := sources[claim.source_id].option_ids) and not scope.issubset(shared_scope):
                raise ValueError("A finding using scoped shared text must cover only options associated with that text.")
            _check_claim_topic(claim, sources[claim.source_id], finding.topic)
        if finding.kind == "requirement_unconfirmed" and not linked_requirements:
            raise ValueError("A requirement_unconfirmed finding must link to an actual buyer requirement.")
        if (finding.kind == "listing_conflict") != (finding.supplier_action == "reconcile_sources"):
            raise ValueError("Use reconcile_sources for a supported listing conflict.")
        if (finding.action_type == "decide_preference") != (finding.supplier_action is None):
            raise ValueError("Every supplier-facing finding must select a supplier action. Use null only for buyer decisions.")
        order_refs = {
            requirement_by_id[requirement_id].input_ref for requirement_id in linked_requirements
            if requirement_by_id[requirement_id].topic == "customization"
        }
        if finding.supplier_action in {"confirm_ready_made", "confirm_custom_order"} and not order_refs:
            raise ValueError("A stock or customization question must link a buyer requirement about order type.")
        product_refs = {
            requirement_by_id[requirement_id].input_ref for requirement_id in linked_requirements
            if requirement_by_id[requirement_id].topic == finding.topic
        }
        order = _linked_target(current_inputs, inputs, "order_type", finding.option_ids, order_refs)
        product_targets = [
            target for facet in SUPPLIER_TARGET_FACETS.get(finding.topic, ())
            if (target := _linked_target(current_inputs, inputs, facet, finding.option_ids, product_refs)) is not None
        ]

        unknowns = [
            _render_unknown(unknown, finding, sources, sent, observed_image_topics) for unknown in finding.unknowns
        ]
        differences = _conflicting_readings(finding, references, sources, aliases) if finding.kind == "listing_conflict" else []
        findings.append(_render_finding(finding, references, unknowns, differences, order, product_targets))

    _unique_ids((row.preference_id for row in content.open_preferences), "open preferences")
    preference_findings = set()
    preferences = []
    for preference in content.open_preferences:
        _validate_refs(preference.input_refs, inputs, "preference input references")
        linked_findings = _validate_refs(preference.finding_ids, finding_ids, "preference findings")
        scope = selected.copy()
        questions = []
        for ref in preference.input_refs:
            entry = inputs[ref]
            if not entry.option_ids:
                raise ValueError("Buyer answer preferences need their issued option scope.")
            scope.intersection_update(entry.option_ids)
            if entry.question is not None:
                questions.append(entry.question)
        if not scope or any(
            not scope.issuperset(finding_by_id[finding_id].option_ids) for finding_id in linked_findings
        ):
            raise ValueError("An open preference and its findings must stay within its linked buyer input scopes.")
        if any(
            finding_by_id[finding_id].action_type == "decide_preference"
            and finding_by_id[finding_id].topic != preference.topic
            for finding_id in linked_findings
        ):
            raise ValueError("A buyer decision must link to an open preference about the same topic.")
        preference_findings.update(linked_findings)
        preferences.append(OpenPreference(
            **preference.model_dump(exclude={"topic"}),
            text=f"Your preference for {TOPICS[preference.topic][0].lower()} is still open.",
            option_ids=[option_id for option_id in request.selected_option_ids if option_id in scope],
            questions=questions,
        ))
    if any(finding.action_type == "decide_preference" and finding.finding_id not in preference_findings
           for finding in content.findings):
        raise ValueError("Link every decide_preference finding to an open preference.")

    if _unique_ids((row.group for row in content.coverage), "coverage groups") != COVERAGE_GROUPS:
        raise ValueError("Return all three coverage groups exactly once.")
    for row in content.coverage:
        _validate_refs(row.finding_ids, finding_ids, "coverage findings")

    return PurchaseReview(
        contract_version=content.contract_version,
        claims=list(claims.values()),
        buyer_requirements=requirements,
        open_preferences=preferences,
        comparisons=comparisons,
        findings=findings,
        coverage=[render_coverage(row, content, comparisons, aliases, sources, sent_evidence) for row in content.coverage],
        limitations=_review_limitations(request, set(sent_evidence.attached_image_ids)),
        case_id=request.case_id,
        revision=request.revision,
        snapshot_id=request.snapshot.snapshot_id,
        selected_option_ids=request.selected_option_ids,
        supplier_draft=_supplier_draft(request, findings),
    )


async def generate_review(
    request: ReviewInput, images: Mapping[str, tuple[str, bytes]], *, client: AsyncOpenAI | None = None,
) -> ReviewResult:
    binding = {"case_id": request.case_id, "revision": request.revision}
    try:
        request = ReviewInput.model_validate(request.model_dump())
    except ValidationError:
        return ReviewFailed(**binding, code="invalid_input", message="The purchase review input is invalid.")
    if client is None:
        return ReviewFailed(
            **binding, code="analysis_failed",
            message="Live review generation is unavailable. Check the server's OpenAI API configuration.",
        )

    evidence = _prepare_evidence(request, images)
    sent = evidence.sent_evidence
    LOGGER.info(
        "Review prompt %s text sources=%s attached images=%s metadata-only images=%s",
        REVIEW_PROMPT_VERSION, sent.text_ids, sent.attached_image_ids, evidence.metadata_only_image_ids,
    )

    def validate(content: ReviewContent) -> ReviewReady:
        return ReviewReady(content=content, sent_evidence=sent, report=assemble_review(request, content, sent_evidence=sent))

    try:
        return await _request_structured(
            client=client,
            payload=evidence.payload,
            prompt=REVIEW_PROMPT,
            schema_name="purchase_review_v2",
            schema=ReviewContent,
            validate=validate,
            deadline_seconds=REVIEW_DEADLINE_SECONDS,
            max_output_tokens=REVIEW_MAX_OUTPUT_TOKENS,
        )
    except _InvalidStructuredOutput as error:
        LOGGER.info("Review prompt %s produced an invalid report", REVIEW_PROMPT_VERSION)
        return ReviewFailed(**binding, code="invalid_review", message=str(error))
    except _ProviderFailure as error:
        return ReviewFailed(**binding, code="analysis_failed", message=str(error))
