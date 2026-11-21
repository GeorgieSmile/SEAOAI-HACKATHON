from base64 import b64encode
from collections.abc import Iterable, Mapping, Set
from dataclasses import dataclass
import json
import logging
import re
from unicodedata import normalize

from openai import AsyncOpenAI
from openai.types.responses import ResponseInputContentParam, ResponseInputParam
from pydantic import ValidationError

from .ai import (
    CLAUSE_BOUNDARY,
    OPTION_REFERENCES,
    PROMPT_DIRECTORY,
    _InvalidStructuredOutput,
    _ProviderFailure,
    _build_client,
    _named_option_references,
    _request_structured,
)
from .contracts import Option, ReviewInput, Source
from .intake import IMAGE_MIME
from .report_models import PurchaseReview, ReviewContent, ReviewFailed, ReviewReady, ReviewResult


REVIEW_PROMPT_VERSION = "purchase-review-v2"
REVIEW_PROMPT = (PROMPT_DIRECTORY / f"{REVIEW_PROMPT_VERSION}.txt").read_text(encoding="utf-8")
REVIEW_DEADLINE_SECONDS = 90
REVIEW_MAX_OUTPUT_TOKENS = 12_288
DETAIL_IMAGE_LIMITATION = "Product-detail images were not reviewed in this version."
COVERAGE_GROUPS = {"product_match", "intended_use", "supporting_evidence"}
PRIVATE_BUSINESS_DETAILS = re.compile(
    r"\b(?:margins?|profit(?:s|ability)?|mark[ -]?ups?|revenue|resale pric(?:e|ing)|"
    r"(?:internal|private) (?:notes?|costs?|budgets?)|target costs?|cost (?:ceilings?|targets?)|unit economics)\b"
    r"|กำไร|ราคาขายต่อ|ต้นทุนเป้าหมาย|利润|毛利|转售价格",
    re.IGNORECASE,
)
LOGGER = logging.getLogger(__name__)


def build_review_client() -> AsyncOpenAI | None:
    """Create the app-lifetime client only when live report generation is enabled."""
    return _build_client("OPENAI_LIVE_REVIEW")


def _selected_options(request: ReviewInput) -> list[Option]:
    options = {option.option_id: option for option in request.snapshot.options}
    return [options[option_id] for option_id in request.selected_option_ids]


def _review_limitations(request: ReviewInput, sent_source_ids: Set[str]) -> list[str]:
    selected = _selected_options(request)
    selected_names = {option.name_original for option in selected}
    excluded_prefixes = tuple(
        prefix
        for option in request.snapshot.options
        if option.name_original not in selected_names
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
        elif image.source_id not in sent_source_ids:
            limitations.append(f"The saved image for Option {reference} was not attached for review.")
    return [*limitations, DETAIL_IMAGE_LIMITATION]


@dataclass(frozen=True)
class ReviewEvidence:
    payload: ResponseInputParam
    text_ids: tuple[str, ...]
    attached_image_ids: tuple[str, ...]
    metadata_only_image_ids: tuple[str, ...]


def _prepare_evidence(
    request: ReviewInput, images: Mapping[str, tuple[str, bytes]],
) -> ReviewEvidence:
    selected_options = _selected_options(request)
    selected = set(request.selected_option_ids)
    image_inventory = [source for source in request.snapshot.sources if source.kind == "listing_image"]
    available_images = {source.source_id for source in image_inventory if source.availability == "available"}
    included_images: dict[tuple[str, bytes], list[str]] = {}
    attached_image_ids: list[str] = []
    for option in selected_options:
        image_id = option.image_source_id
        if image_id is None or image_id not in available_images or image_id in attached_image_ids:
            continue
        image = images.get(image_id)
        if image is not None and image[0] in IMAGE_MIME.values() and image[1]:
            included_images.setdefault(image, []).append(image_id)
            attached_image_ids.append(image_id)

    metadata_only = [source.source_id for source in image_inventory if source.source_id not in attached_image_ids]
    text_sources = [
        source for source in request.snapshot.sources
        if source.kind != "listing_image" and (not source.option_ids or selected.intersection(source.option_ids))
    ]
    text_ids = tuple(source.source_id for source in text_sources if source.availability == "available")
    data = {
        "selected_options": [
            {
                "option_id": option.option_id,
                "reference": reference,
                "name_original": option.name_original,
                "name_en": option.name_en,
                "image_source_id": option.image_source_id,
            }
            for reference, option in zip(OPTION_REFERENCES, selected_options)
        ],
        "buyer_context": request.buyer_context,
        "answers": [answer.model_dump() for answer in request.answers],
        "text_sources": [
            {
                "source_id": source.source_id,
                "kind": source.kind,
                "label": source.label,
                "option_ids": [option_id for option_id in source.option_ids if option_id in selected],
                "availability": source.availability,
                "original_text": source.original_text,
                "text_en": source.text_en,
            }
            for source in text_sources
        ],
        "image_inventory": [
            source.model_dump(include={"source_id", "label", "option_ids", "availability"})
            for source in image_inventory
        ],
        "attached_image_ids": attached_image_ids,
        "metadata_only_image_ids": metadata_only,
        "limitations": _review_limitations(request, set(attached_image_ids)),
    }
    content: list[ResponseInputContentParam] = [{"type": "input_text", "text": json.dumps(data, ensure_ascii=False)}]
    for (mime, image_bytes), source_ids in included_images.items():
        content.append({"type": "input_text", "text": f"Attached image source IDs: {', '.join(source_ids)}"})
        content.append({
            "type": "input_image", "detail": "auto",
            "image_url": f"data:{mime};base64,{b64encode(image_bytes).decode('ascii')}",
        })
    return ReviewEvidence(
        payload=[{"role": "user", "content": content}],
        text_ids=text_ids,
        attached_image_ids=tuple(attached_image_ids),
        metadata_only_image_ids=tuple(metadata_only),
    )


def _unique_ids(ids: Iterable[str], field: str) -> set[str]:
    unique = set()
    for identifier in ids:
        if identifier in unique:
            raise ValueError(f"Do not repeat IDs in {field}.")
        unique.add(identifier)
    return unique


def _validate_refs(ids: Iterable[str], allowed: Set[str], field: str) -> set[str]:
    refs = _unique_ids(ids, field)
    if not refs.issubset(allowed):
        raise ValueError(f"Use only valid IDs in {field}.")
    return refs


def _scoped_source(sources: Mapping[str, Source], source_id: str, option_ids: Set[str]) -> Source:
    source = sources.get(source_id)
    if source is None or (source.option_ids and option_ids.isdisjoint(source.option_ids)):
        raise ValueError("Use only sources whose scope matches the referenced options.")
    return source


def _normalized_prose(text: str) -> str:
    return " ".join(re.findall(r"\w+|%", normalize("NFKC", text).casefold()))


def _private_note_values(text: str) -> Iterable[str]:
    for clause in CLAUSE_BOUNDARY.split(normalize("NFKC", text)):
        if PRIVATE_BUSINESS_DETAILS.search(clause):
            _, delimiter, value = clause.partition(":")
            if delimiter and (value := _normalized_prose(value)):
                yield value


def _supplier_draft(request: ReviewInput, content: ReviewContent) -> str:
    questions = []
    buyer_context = _normalized_prose(request.buyer_context)
    private_values = {
        value
        for buyer_text in [request.buyer_context, *(answer.answer_text or "" for answer in request.answers)]
        for value in _private_note_values(buyer_text)
    }
    if len(buyer_context) >= 30:
        private_values.add(buyer_context)
    for finding in content.findings:
        if finding.supplier_question is None:
            continue
        question = " ".join(finding.supplier_question.split())
        normalized_question = f" {_normalized_prose(question)} "
        if PRIVATE_BUSINESS_DETAILS.search(normalize("NFKC", question)) or any(
            f" {value} " in normalized_question for value in private_values
        ):
            raise ValueError("Supplier questions must omit private business details and verbatim buyer context.")
        questions.append(question)

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


def _validate_option_language(text: str | None, allowed: set[str]) -> None:
    if text is not None and any(
        not aliases.issubset(allowed) for aliases, _, _, _ in _named_option_references(normalize("NFKC", text))
    ):
        raise ValueError("Name only the option letters covered by the referenced finding, source, or comparison.")


def assemble_review(
    request: ReviewInput, content: ReviewContent, *, sent_source_ids: Set[str],
) -> PurchaseReview:
    """Validate report references against the input and the sources actually supplied."""
    sources = {source.source_id: source for source in request.snapshot.sources}
    selected = set(request.selected_option_ids)
    aliases = dict(zip(request.selected_option_ids, OPTION_REFERENCES))
    available_sources = {
        source_id for source_id in sent_source_ids
        if source_id in sources and sources[source_id].availability == "available"
    }
    inputs = {"context": request.buyer_context, **{
        f"answer:{answer.question_id}": answer.answer_text for answer in request.answers
    }}
    requirement_ids = _unique_ids((row.requirement_id for row in content.buyer_requirements), "buyer requirements")
    for requirement in content.buyer_requirements:
        quote_source = inputs.get(requirement.input_ref)
        if not quote_source or requirement.quote not in quote_source:
            raise ValueError("Each requirement needs a verbatim quote from its non-null buyer input reference.")

    for comparison in content.comparisons:
        compared = _validate_refs((value.option_id for value in comparison.values), selected, "comparison values")
        if compared != selected:
            raise ValueError("Each comparison must contain every selected option once.")
        for value in comparison.values:
            _validate_option_language(value.summary, {aliases[value.option_id]})
            for source_id in _validate_refs(value.source_ids, available_sources, "comparison sources"):
                _scoped_source(sources, source_id, {value.option_id})

    finding_ids = _unique_ids((finding.finding_id for finding in content.findings), "findings")
    for finding in content.findings:
        scope = _validate_refs(finding.option_ids, selected, "finding options")
        finding_aliases = {aliases[option_id] for option_id in scope}
        for explanation in (
            finding.title, finding.interpretation, finding.impact, finding.priority_reason,
            finding.requested_evidence, finding.evidence_limits, finding.supplier_question,
        ):
            _validate_option_language(explanation, finding_aliases)
        _validate_refs(finding.requirement_ids, requirement_ids, "finding requirements")
        for citation in finding.citations:
            source = _scoped_source(sources, citation.source_id, scope)
            if citation.source_id not in available_sources:
                raise ValueError("Citations require available sources sent in the report request.")
            if source.kind == "listing_image":
                if citation.quote is not None or citation.image_observation is None:
                    raise ValueError("Use only an image observation for an image citation.")
                image_scope = scope.intersection(source.option_ids) if source.option_ids else scope
                _validate_option_language(citation.image_observation, {aliases[option_id] for option_id in image_scope})
            elif (
                citation.quote is None or citation.image_observation is not None
                or source.original_text is None or citation.quote not in source.original_text
            ):
                raise ValueError("For a text citation use an exact quote from original_text and no image observation.")
        for unknown in finding.unknowns:
            _validate_option_language(unknown.text, finding_aliases)
            for source_id in _unique_ids(unknown.source_ids, "unknown sources"):
                source = _scoped_source(sources, source_id, scope)
                if unknown.reason == "not_found" and source_id not in available_sources:
                    raise ValueError("Use not_found only for sources whose content was available and sent.")
                if unknown.reason == "not_reviewed" and source.availability != "available":
                    raise ValueError("For missing or unreadable sources, describe why they could not be read.")
                if unknown.reason == "unreadable" and source.availability == "available" and (
                    source.kind != "listing_image" or source_id not in available_sources
                ):
                    raise ValueError("An available source can be unreadable only when an attached image is illegible.")

    _unique_ids((row.preference_id for row in content.open_preferences), "open preferences")
    preference_findings = set()
    for preference in content.open_preferences:
        _validate_refs(preference.input_refs, inputs.keys(), "preference input references")
        preference_findings.update(_validate_refs(preference.finding_ids, finding_ids, "preference findings"))
    if any(finding.action_type == "decide_preference" and finding.finding_id not in preference_findings
           for finding in content.findings):
        raise ValueError("Link every decide_preference finding to an open preference.")

    if _unique_ids((row.group for row in content.coverage), "coverage groups") != COVERAGE_GROUPS:
        raise ValueError("Return all three coverage groups exactly once.")
    for row in content.coverage:
        _validate_refs(row.finding_ids, finding_ids, "coverage findings")

    option_order = {option_id: index for index, option_id in enumerate(request.selected_option_ids)}
    comparisons = [
        comparison.model_copy(update={"values": sorted(comparison.values, key=lambda value: option_order[value.option_id])})
        for comparison in content.comparisons
    ]
    limitations = list(dict.fromkeys([*content.limitations, *_review_limitations(request, sent_source_ids)]))
    return PurchaseReview(
        **content.model_dump(exclude={"comparisons", "limitations"}),
        case_id=request.case_id,
        revision=request.revision,
        snapshot_id=request.snapshot.snapshot_id,
        selected_option_ids=request.selected_option_ids,
        comparisons=comparisons,
        limitations=limitations,
        supplier_draft=_supplier_draft(request, content),
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
    LOGGER.info(
        "Review prompt %s text sources=%s attached images=%s metadata-only images=%s",
        REVIEW_PROMPT_VERSION, evidence.text_ids, evidence.attached_image_ids, evidence.metadata_only_image_ids,
    )
    sent_ids = set(evidence.text_ids).union(evidence.attached_image_ids)
    try:
        report = await _request_structured(
            client=client,
            payload=evidence.payload,
            prompt=REVIEW_PROMPT,
            schema_name="purchase_review",
            schema=ReviewContent,
            validate=lambda content: assemble_review(request, content, sent_source_ids=sent_ids),
            deadline_seconds=REVIEW_DEADLINE_SECONDS,
            max_output_tokens=REVIEW_MAX_OUTPUT_TOKENS,
        )
    except _InvalidStructuredOutput as error:
        LOGGER.info("Review prompt %s produced an invalid report", REVIEW_PROMPT_VERSION)
        return ReviewFailed(**binding, code="invalid_review", message=str(error))
    except _ProviderFailure as error:
        return ReviewFailed(**binding, code="analysis_failed", message=str(error))
    return ReviewReady(report=report)
