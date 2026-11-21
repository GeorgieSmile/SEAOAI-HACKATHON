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
from .contracts import Option, ReviewInput, SKU_NAME_SOURCE_LABELS, Source
from .intake import IMAGE_MIME
from .report_models import (
    BuyerRequirement,
    Citation,
    Comparison,
    ComparisonValue,
    Coverage,
    DisplayClaim,
    DisplayImageDetailClaim,
    DisplayImageMeasurementClaim,
    DisplayTextClaim,
    Finding,
    FindingSelection,
    ImageDetailClaim,
    ImageMeasurementClaim,
    OpenPreference,
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


REVIEW_PROMPT_VERSION = "purchase-review-v3"
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
COVERAGE_NOTES = {
    "reviewed": "The review considered the supplied material for this group. Product verification is separate.",
    "limited": "The saved evidence allows a partial assessment. The applicable checks still need confirmation.",
    "not_assessed": "The supplied buyer details or evidence did not support an assessment of this group.",
}
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
        "answers": [answer.model_dump() for answer in request.answers],
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
    if claim.quote != source.original_text:
        return DisplayTextClaim(**claim.model_dump(), display_text=f"{label} excerpt: “{claim.quote}”")
    wording = source.original_text
    if source.text_en is not None:
        label = f"{label} (source translation)"
        wording = source.text_en
    return DisplayTextClaim(**claim.model_dump(), display_text=f"{label}: {wording}")


def _check_claim_topic(claim: SourcedClaim, topic: str) -> None:
    if isinstance(claim, ImageMeasurementClaim) and UNIT_TOPICS[claim.unit] != topic:
        raise ValueError("Use a thumbnail measurement with its matching topic.")
    if isinstance(claim, ImageDetailClaim) and VISIBLE_DETAILS[claim.detail][0] != topic:
        raise ValueError("Use a thumbnail detail with its matching topic.")


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
    selection: UnknownSelection, finding: FindingSelection, sources: Mapping[str, Source], sent: set[str]
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
        if all(source.availability == "missing" for source in scoped):
            text = f"The referenced source is missing, so {topic} could not be checked there."
        else:
            text = f"The referenced evidence for {topic} could not be read."
    return Unknown(**selection.model_dump(), text=text)


def _render_next_step(finding: FindingSelection, differences: list[str]) -> tuple[str | None, str, str]:
    action = finding.supplier_action
    _, spec = TOPICS[finding.topic]
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
    question, requested_evidence, limits = _render_next_step(finding, differences)
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

    inputs = {"context": request.buyer_context, **{
        f"answer:{answer.question_id}": answer.answer_text for answer in request.answers
    }}
    _unique_ids((row.requirement_id for row in content.buyer_requirements), "buyer requirements")
    requirement_by_id = {row.requirement_id: row for row in content.buyer_requirements}
    requirements = []
    for row in content.buyer_requirements:
        quote_source = inputs.get(row.input_ref)
        if not quote_source or row.quote not in quote_source:
            raise ValueError("Each requirement needs a verbatim quote from its non-null buyer input reference.")
        requirements.append(BuyerRequirement(
            **row.model_dump(exclude={"topic"}), text=row.quote,
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
        values_by_id = {value.option_id: value for value in comparison.values}
        values = []
        for option_id in request.selected_option_ids:
            value = values_by_id[option_id]
            _validate_refs(value.claim_ids, claims, "comparison claims")
            references = [claims[claim_id] for claim_id in value.claim_ids]
            for claim in references:
                if claim.option_ids != [option_id]:
                    raise ValueError("A comparison cell can cite only claims owned by its exact SKU.")
                _check_claim_topic(claim, comparison.topic)
            values.append(ComparisonValue(
                **value.model_dump(),
                summary="\n".join(claim.display_text for claim in references) or "No SKU-specific evidence cited for this topic.",
                source_ids=list(dict.fromkeys(claim.source_id for claim in references)),
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
        _validate_refs(finding.claim_ids, claims, "finding claims")
        references = [claims[claim_id] for claim_id in finding.claim_ids]
        for claim in references:
            if claim.option_ids and not scope.issuperset(claim.option_ids):
                raise ValueError("A finding must cover the SKU that owns each SKU-specific claim.")
            if not claim.option_ids and (shared_scope := sources[claim.source_id].option_ids) and not scope.issubset(shared_scope):
                raise ValueError("A finding using scoped shared text must cover only options associated with that text.")
            _check_claim_topic(claim, finding.topic)
        if finding.kind == "requirement_unconfirmed" and not linked_requirements:
            raise ValueError("A requirement_unconfirmed finding must link to an actual buyer requirement.")
        if (finding.kind == "listing_conflict") != (finding.supplier_action == "reconcile_sources"):
            raise ValueError("Use reconcile_sources for a supported listing conflict.")
        if (finding.action_type == "decide_preference") != (finding.supplier_action is None):
            raise ValueError("Every supplier-facing finding must select a supplier action. Use null only for buyer decisions.")
        if finding.supplier_action in {"confirm_ready_made", "confirm_custom_order"} and not any(
            requirement_by_id[requirement_id].topic == "customization" for requirement_id in linked_requirements
        ):
            raise ValueError("A stock or customization question must link a buyer requirement about order type.")

        unknowns = [_render_unknown(unknown, finding, sources, sent) for unknown in finding.unknowns]
        differences = _conflicting_readings(finding, references, sources, aliases) if finding.kind == "listing_conflict" else []
        findings.append(_render_finding(finding, references, unknowns, differences))

    _unique_ids((row.preference_id for row in content.open_preferences), "open preferences")
    preference_findings = set()
    preferences = []
    for preference in content.open_preferences:
        _validate_refs(preference.input_refs, inputs, "preference input references")
        linked_findings = _validate_refs(preference.finding_ids, finding_ids, "preference findings")
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
        coverage=[Coverage(**row.model_dump(), note=COVERAGE_NOTES[row.status]) for row in content.coverage],
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
