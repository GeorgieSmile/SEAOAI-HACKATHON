from base64 import b64encode
from collections.abc import Iterable, Mapping, Set
from dataclasses import dataclass
import json
import logging
import re
from unicodedata import decimal, normalize

from openai import AsyncOpenAI
from openai.types.responses import ResponseInputContentParam, ResponseInputParam
from pydantic import ValidationError

from .ai import (
    CLAUSE_BOUNDARY,
    NAMED_OPTIONS,
    OPTION_NAME_AFTER,
    OPTION_NAME_BEFORE,
    OPTION_REFERENCES,
    PROMPT_DIRECTORY,
    SENTENCE_BOUNDARY,
    _InvalidStructuredOutput,
    _ProviderFailure,
    _build_client,
    _named_option_references,
    _request_structured,
)
from .contracts import Option, ReviewInput, Source
from .intake import IMAGE_MIME
from .report_models import Finding, PurchaseReview, ReviewContent, ReviewFailed, ReviewReady, ReviewResult


REVIEW_PROMPT_VERSION = "purchase-review-v2"
REVIEW_PROMPT = (PROMPT_DIRECTORY / f"{REVIEW_PROMPT_VERSION}.txt").read_text(encoding="utf-8")
REVIEW_DEADLINE_SECONDS = 90
REVIEW_MAX_OUTPUT_TOKENS = 12_288
DETAIL_IMAGE_LIMITATION = "Product-detail images were not reviewed in this version."
COVERAGE_GROUPS = {"product_match", "intended_use", "supporting_evidence"}
NAME_PREDICATE = re.compile(
    r"\b(?:includes?|contains?|uses?|reads?|says?|mentions?|shows?|has|have|is|are)\b", re.IGNORECASE,
)
NAME_INQUIRY = re.compile(
    r"\b(?:whether|if)\b|^\s*(?:does|do|is|are|has|have|what|which|how)\b", re.IGNORECASE,
)
NAME_NEGATION = re.compile(r"\b(?:not|never)\b|n['’]t\b", re.IGNORECASE)
NAME_COORDINATION = re.compile(r"\s*(,\s*(?:(?:and|or|nor|but)\b)?|\b(?:and|or|nor|but)\b|[/&、])\s*", re.IGNORECASE)
NAME_COORDINATED_PREDICATE = re.compile(
    r"^\s*(?:(?:do(?:es)?(?:n['’]t|\s+not)?|never)\s+)?" + NAME_PREDICATE.pattern, re.IGNORECASE,
)
HAN_CHARACTER = re.compile(r"[\u3400-\u9fff\U00020000-\U000323af]")
NAME_TERM_AFTER = re.compile(
    r"^\s*(?:(?P<modifier>not|no|neither|both|rather)\s+)?"
    r"(?:(?:the|a|an)\s+)?(?:(?:Chinese|original)\s+)?(?:\b(?:characters?|phrases?|terms?|words?|wording|text)\s+)?"
    r"[\"“'‘`]*\s*(?P<term>[\w-]+)[\"”'’`]*(?:\s+(?:wording|terms?|text|labels?)\b)?",
    re.IGNORECASE,
)
NAME_ANNOTATION = re.compile(r"\([^()]*\)")
NAME_SUBJECT_PREDICATE = re.compile(
    r"^\s*(?:(?:only|exclusively)\s+)?(?:appears?|occurs?|does|do|did|can|could|may|might|must|will|would|should)\b",
    re.IGNORECASE,
)
NAME_TERM_BEFORE = re.compile(
    r"(?P<term>[\w-]+)[\"”'’`]*\s+(?:(?:SKU|listing)\s+)?(?:names?|wording|labels?)\s+(?:for|of)\s*$",
    re.IGNORECASE,
)
NAME_TERM_IN_NAME = re.compile(
    r"(?P<term>[\w-]+)[\"”'’`]*\s+"
    r"(?:(?P<before_predicate>only|exclusively)\s+)?"
    r"(?P<predicate>(?:do(?:es)?(?:n['’]t|\s+not)\s+)?(?:appears?|occurs?)|is|are)\s+"
    r"(?:(?P<qualifier>not\s+only|not|never|only|exclusively)\s+)?in\s+"
    r"(?:(?:the\s+)?(?:(?:SKU|listing)\s+)?(?:names?|wording|labels?)\s+(?:for|of)\s+)?$",
    re.IGNORECASE,
)
NAME_ONLY_BEFORE = re.compile(r"\b(?P<negated>not\s+)?(?:only|exclusively)\s+$", re.IGNORECASE)
NAME_ONLY_AFTER = re.compile(
    r"^\s*(?:(?:['’]s\s+)?(?:(?:SKU|listing)\s+)?(?:names?|wording|labels?)\s+)?"
    r"(?:alone\s+|(?:is|are)\s+the\s+only\s+(?:ones?|names?)\s+(?:that\s+)?)",
    re.IGNORECASE,
)
NAME_COMPARISON_SCOPE = re.compile(r"^\s*(?:among|between|of)\s+", re.IGNORECASE)
NAME_SCOPE_EXCEPTION = re.compile(r"\b(?:except(?:\s+for)?|other\s+than)\s+", re.IGNORECASE)
PRIVATE_BUSINESS_DETAILS = re.compile(
    r"\b(?:margins?|profit(?:s|ability)?|mark[ -]?ups?|revenue|resale pric(?:e|ing)|"
    r"(?:internal|private) (?:notes?|costs?|budgets?)|target costs?|cost (?:ceilings?|targets?)|unit economics)\b"
    r"|กำไร|ราคาขายต่อ|ต้นทุนเป้าหมาย|利润|毛利|转售价格",
    re.IGNORECASE,
)
PRIVATE_NOTE = re.compile(r"\b(?:internal|private|confidential) notes?\b", re.IGNORECASE)
NOTE_IDENTIFIER = re.compile(r"\b(?:[A-Za-z]{2,}[-_]\d[\w-]*|[A-Z][a-z]+(?:[ \t]+[A-Z][a-z]+)+(?![-_]\d))\b")
BUSINESS_FIGURE = re.compile(
    r"(?:[$€£¥฿]|\b(?:THB|USD|EUR|GBP|CNY|RMB|JPY)\b)\s*\d+(?:[.,]\d+)*"
    r"|\b\d+(?:[.,]\d+)*\s*(?:%|\b(?:percent|THB|USD|EUR|GBP|CNY|RMB|JPY)\b|บาท|元)",
    re.IGNORECASE,
)
EMAIL_ADDRESS = re.compile(r"[\w.!#$%&'*+/=?^`{|}~+-]+@(?:[\w-]+\.)+[^\W\d_]{2,63}\b")
PHONE_NUMBER = re.compile(r"(?<!\w)(?:\+[ \t]*)?(?:\(\d{1,4}\)|\d)(?:[ .()\-‐‑–]*\d){6,14}(?!\w)")
PHONE_INTRO = re.compile(
    r"(?:\b(?:phone|mobile|tel|call|text|contact|reach|whatsapp|(?:internal|private|confidential) notes?)\b"
    r"|โทร|เบอร์|电话)[^\d.!?;]{0,25}$",
    re.IGNORECASE,
)
PRODUCT_NUMBER_LABEL = (
    r"(?:upc(?:-[ae])?|ean(?:-?(?:8|13))?|gtin(?:-?(?:8|12|13|14))?|isbn(?:-?(?:10|13))?|"
    r"bar[ -]?code|sku|asin|model|serial|"
    r"(?:product|part|item)\s+(?:code|id|number)|dimensions?|measurements?)"
)
PRODUCT_NUMBER_INTRO = re.compile(
    rf"\b{PRODUCT_NUMBER_LABEL}\b(?:\s+(?:code|id|number|no|is)\b\.?)*[\s:#=(\[\-]*$", re.IGNORECASE,
)
PRODUCT_NUMBER_SUFFIX = re.compile(
    rf"^\s*(?:\(\s*{PRODUCT_NUMBER_LABEL}\b|(?:is|as)\s+(?:(?:the|an?)\s+)?(?:product\s+)?{PRODUCT_NUMBER_LABEL}\b"
    r"|(?:mm|cm|ml|kg|mah|volts?|watts?)\b)",
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
    text = re.sub(r"\bpercent\b", "%", normalize("NFKC", text).casefold())
    return " ".join(re.findall(r"\w+|[%$€£¥฿]", text))


def _private_note_values(text: str) -> Iterable[str]:
    for clause in CLAUSE_BOUNDARY.split(normalize("NFKC", text)):
        detail = PRIVATE_BUSINESS_DETAILS.search(clause)
        note = PRIVATE_NOTE.search(clause)
        if detail is None and note is None:
            continue
        yield from (_normalized_prose(figure.group()) for figure in BUSINESS_FIGURE.finditer(clause))
        marker = note or detail
        _, delimiter, value = clause[marker.end():].partition(":")
        if delimiter and (normalized := _normalized_prose(value)):
            yield normalized
            if note is not None:
                yield from (_normalized_prose(identifier.group()) for identifier in NOTE_IDENTIFIER.finditer(value))


def _phone_values(text: str, *, require_contact_format: bool = False) -> Iterable[str]:
    for match in PHONE_NUMBER.finditer(text):
        number = match.group()
        digits = "".join(str(decimal(character)) for character in number if character.isdecimal())
        if not 7 <= len(digits) <= 15:
            continue
        if require_contact_format:
            # A matching string is sensitive only when the buyer supplied it as a contact or in a recognizable phone format.
            prefix = text[max(0, match.start() - 60):match.start()]
            suffix = text[match.end():match.end() + 40]
            if PRODUCT_NUMBER_INTRO.search(prefix) or PRODUCT_NUMBER_SUFFIX.match(suffix):
                continue
            group_sizes = [len(group) for group in re.findall(r"\d+", number)]
            short_groups = len(group_sizes) >= 3 and all(2 <= size <= 4 for size in group_sizes)
            area_and_local = (len(group_sizes) == 2 and digits.startswith("0")
                              and 2 <= group_sizes[0] <= 5 and 5 <= group_sizes[1] <= 8)
            grouped_phone = len(digits) >= 9 and (short_groups or area_and_local)
            if not (number.startswith(("+", "(")) or grouped_phone or PHONE_INTRO.search(prefix)):
                continue
        yield digits


def validate_supplier_privacy(request: ReviewInput, questions: Iterable[str]) -> None:
    buyer_texts = [request.buyer_context, *(answer.answer_text for answer in request.answers if answer.answer_text)]
    buyer_phones = {
        phone for text in buyer_texts for phone in _phone_values(normalize("NFKC", text), require_contact_format=True)
    }
    private_values = {value for text in buyer_texts for value in _private_note_values(text)}
    buyer_context = _normalized_prose(request.buyer_context)
    if len(buyer_context) >= 30:
        private_values.add(buyer_context)

    for question in questions:
        text = normalize("NFKC", question).casefold()
        normalized = f" {_normalized_prose(text)} "
        if (
            PRIVATE_BUSINESS_DETAILS.search(text) or PRIVATE_NOTE.search(text)
            or EMAIL_ADDRESS.search(text)
            or buyer_phones.intersection(_phone_values(text))
            or any(f" {value} " in normalized for value in private_values)
        ):
            raise ValueError("Supplier questions must omit private business details, buyer contact details, and verbatim buyer context.")


def build_supplier_draft(request: ReviewInput, content: ReviewContent) -> str:
    validate_supplier_privacy(request, (
        finding.supplier_question for finding in content.findings if finding.supplier_question is not None
    ))
    questions = []
    for finding in content.findings:
        if finding.supplier_question is None:
            if finding.action_type in {"ask_supplier", "request_evidence"}:
                raise ValueError("An ask_supplier or request_evidence finding must include a supplier_question.")
            continue
        question = " ".join(finding.supplier_question.split())
        letters = [letter for option_id, letter in zip(request.selected_option_ids, OPTION_REFERENCES)
                   if option_id in finding.option_ids]
        scope = "Option" if len(letters) == 1 else "Options"
        questions.append(f"[{scope} {', '.join(letters)}] {question}")

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


def _finding_prose(finding: Finding) -> Iterable[str]:
    yield from (
        finding.title, finding.interpretation, finding.impact, finding.priority_reason,
        finding.requested_evidence, finding.evidence_limits,
    )
    if finding.supplier_question is not None:
        yield finding.supplier_question
    yield from (unknown.text for unknown in finding.unknowns)


def _original_term(match: re.Match[str] | None) -> str:
    term = match["term"] if match is not None else ""
    return term if HAN_CHARACTER.search(term) else ""


def _name_predicate_terms(tail: str, predicate: re.Match[str]) -> Iterable[tuple[str, bool]]:
    """Read the coordinated objects of a name predicate until the prose changes subject."""
    predicate_negative = bool(NAME_NEGATION.search(tail[:predicate.start()]))
    parts = NAME_COORDINATION.split(NAME_ANNOTATION.sub(" ", tail[predicate.end():]))
    for separator, phrase in zip(["", *parts[1::2]], parts[::2]):
        continuation = NAME_COORDINATED_PREDICATE.match(phrase) if separator else None
        if continuation is not None:
            predicate_negative = bool(NAME_NEGATION.search(continuation[0]))
            phrase = phrase[continuation.end():]
        operand = NAME_TERM_AFTER.match(phrase)
        if operand is None:
            break
        remainder = phrase[operand.end():]
        if separator and continuation is None and (
            NAME_COORDINATED_PREDICATE.match(remainder) or NAME_SUBJECT_PREDICATE.match(remainder)
        ):
            # This term is the subject of a new predicate. Its own option is checked separately.
            break
        modifier = (operand["modifier"] or "").casefold()
        if modifier in {"no", "neither"}:
            predicate_negative = True
        if modifier == "rather":
            predicate_negative = False
        absent = predicate_negative or modifier == "not" or separator.casefold().endswith("nor")
        term = _original_term(operand)
        if term:
            yield term, absent


def _name_comparison_options(before: str, selected: Set[str]) -> Set[str]:
    intro, comma, _ = before.rpartition(",")
    if not comma or not NAME_COMPARISON_SCOPE.match(intro):
        return selected
    included, *excluded = NAME_SCOPE_EXCEPTION.split(intro, maxsplit=1)
    named = set().union(*(aliases for aliases, *_ in _named_option_references(included)))
    exceptions = set().union(*(aliases for clause in excluded for aliases, *_ in _named_option_references(clause)))
    return (named or selected) - exceptions


def _validate_name_claims(
    text: str, wording_by_alias: Mapping[str, list[str]], full_name_by_alias: Mapping[str, str],
) -> None:
    for references, before, after, _ in _named_option_references(normalize("NFKC", text), boundary=SENTENCE_BOUNDARY):
        # Each alias in a sentence has its own words and evidence.
        next_reference = NAMED_OPTIONS.search(after)
        tail = after[:next_reference.start()] if next_reference is not None else after
        only_before = NAME_ONLY_BEFORE.search(before)
        prefix = before[:only_before.start()] if only_before else before
        owner_only = only_before is not None and only_before["negated"] is None
        if NAME_INQUIRY.search(before) or not (OPTION_NAME_BEFORE.search(prefix) or OPTION_NAME_AFTER.match(tail)):
            continue
        only_after = NAME_ONLY_AFTER.match(tail)
        predicate = NAME_PREDICATE.search(tail, only_after.end() if only_after else 0)
        term = _original_term(NAME_TERM_BEFORE.search(prefix))
        terms = [(term, False)] if term else []
        exclusive_terms: list[tuple[str, bool]] = []
        term_in_name = NAME_TERM_IN_NAME.search(prefix)
        if term_in_name is not None and (subject := _original_term(term_in_name)):
            qualifier = " ".join((term_in_name["qualifier"] or "").casefold().split())
            exclusive = bool(owner_only or term_in_name["before_predicate"] or qualifier in {"only", "exclusively"})
            negated = bool(NAME_NEGATION.search(term_in_name["predicate"]))
            absent = negated or qualifier in {"not", "never"}
            # Negating "appears only in" negates exclusivity, not membership.
            if not (exclusive and negated):
                terms.append((subject, absent))
                if exclusive:
                    exclusive_terms.append((subject, absent))
        if predicate is None and not terms:
            continue
        if predicate is not None:
            objects = list(_name_predicate_terms(tail, predicate))
            terms.extend(objects)
            if owner_only or only_after:
                exclusive_terms.extend(objects)

        for alias in references:
            wording = wording_by_alias.get(alias)
            if not wording:
                raise ValueError("Each named option claim about wording needs its own cited SKU name.")
            for original_term, absent in terms:
                if absent:
                    if original_term in full_name_by_alias[alias]:
                        raise ValueError("Do not claim an original term is absent when it occurs in that option's full SKU name.")
                elif not any(original_term in excerpt for excerpt in wording):
                    raise ValueError("Match every asserted original term to the cited SKU name for that option.")

        if exclusive_terms:
            comparison = _name_comparison_options(before, full_name_by_alias.keys())
            peers = (comparison & full_name_by_alias.keys()) - references
            if any(
                (original_term in full_name_by_alias[other]) != absent
                for original_term, absent in exclusive_terms
                for other in peers
            ):
                raise ValueError("The exclusive SKU name claim contradicts another option's full name in the comparison.")


def validate_report_attribution(request: ReviewInput, content: ReviewContent) -> None:
    """Bind identifiable SKU-name claims to each option's own report evidence."""
    names = request.snapshot.option_name_sources()
    aliases = dict(zip(request.selected_option_ids, OPTION_REFERENCES))
    full_names = {alias: normalize("NFKC", names[option_id].original_text or "") for option_id, alias in aliases.items()}
    alias_by_name_id = {names[option_id].source_id: alias for option_id, alias in aliases.items()}
    for comparison in content.comparisons:
        for value in comparison.values:
            source = names.get(value.option_id)
            alias = aliases.get(value.option_id)
            wording = {}
            if alias and source and source.original_text and source.source_id in value.source_ids:
                wording[alias] = [full_names[alias]]
            _validate_name_claims(value.summary, wording, full_names)

    for finding in content.findings:
        quotes: dict[str, list[str]] = {}
        for citation in finding.citations:
            alias = alias_by_name_id.get(citation.source_id)
            if alias is not None and citation.quote is not None:
                quotes.setdefault(alias, []).append(normalize("NFKC", citation.quote))
        for text in _finding_prose(finding):
            _validate_name_claims(text, quotes, full_names)


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
        for explanation in _finding_prose(finding):
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
    validate_report_attribution(request, content)

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
        supplier_draft=build_supplier_draft(request, content),
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
