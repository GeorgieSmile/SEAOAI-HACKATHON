import asyncio
import json
import logging
import math
import os
import re
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Literal, TypeVar
from unicodedata import normalize

from openai import APIConnectionError, APIResponseValidationError, APIStatusError, AsyncOpenAI
from openai.types.responses import Response
from pydantic import ValidationError

from .contracts import ContractModel, ListingSnapshot, QuestionInput, QuestionResult, Source


TRANSLATION_MODEL = "gpt-6.1-terra"
TRANSLATION_PROMPT_VERSION = "translate-listing-v1"
TRANSLATION_DEADLINE_SECONDS = 60
TRANSLATION_LIMITATION = "English translation is unavailable. The original listing text is shown."
QUESTION_PROMPT_VERSION = "buyer-questions-v2"
QUESTION_DEADLINE_SECONDS = 60
QUOTA_ERRORS = {"insufficient_quota", "billing_hard_limit_reached", "billing_not_active", "quota_exceeded"}
PROMPT_DIRECTORY = Path(__file__).parent / "prompts"
PROMPT = (PROMPT_DIRECTORY / f"{TRANSLATION_PROMPT_VERSION}.txt").read_text(encoding="utf-8")
QUESTION_PROMPT = (PROMPT_DIRECTORY / f"{QUESTION_PROMPT_VERSION}.txt").read_text(encoding="utf-8")
LOGGER = logging.getLogger(__name__)
OPTION_REFERENCES = "ABC"

IMAGE_CLAIM = re.compile(
    r"\b(?:images?|photos?|pictures?|thumbnails?|screenshots?)\s+"
    r"(?:(?:clearly|apparently|seemingly)\s+)?"
    r"(?:shows?|showed|depicts?|depicted|indicates?|indicated|confirms?|confirmed|proves?|proved|"
    r"reveals?|revealed|appears?|appeared|looks?|looked)\b"
    r"|\b(?:visible in|pictured in|shown in|seen in|based on|judging from)\s+"
    r"(?:(?:the|a|this|these|those|product|seller|listing|saved)\s+)*"
    r"(?:images?|photos?|pictures?|thumbnails?|screenshots?)\b",
    re.IGNORECASE,
)
LISTING_CLAIM = re.compile(
    r"\b(?:listing|seller|supplier|manufacturer|product page|product description)\s+"
    r"(?:lists?|says?|states?|describes?|claims?|shows?|offers?|specifies?|confirms?)\b",
    re.IGNORECASE,
)
NAMED_OPTIONS = re.compile(
    r"\boptions?\s+([ABC](?:\s*(?:,\s*(?:and\s+)?|and\s+|or\s+|/\s*)[ABC])*)\b",
    re.IGNORECASE,
)
GROUP_EVIDENCE_CLAIM = re.compile(
    r"\b(?:both|all|each)\s+(?:(?:of|the|selected|confirmed)\s+)*"
    r"(?:options?|SKUs?|variants?)\s+"
    r"(?:uses?|has|have|lists?|shows?|contains?|offers?|specifies?|confirms?)\b",
    re.IGNORECASE,
)
QuestionErrorCode = Literal["invalid_input", "invalid_questions", "analysis_failed"]
StructuredOutput = TypeVar("StructuredOutput", bound=ContractModel)
ValidatedOutput = TypeVar("ValidatedOutput")


class TranslationConfigurationError(Exception):
    """Translation cannot run until API settings, access, or billing are fixed."""


class QuestionGenerationError(ValueError):
    def __init__(self, code: QuestionErrorCode, message: str, retryable: bool = True):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class _ProviderFailure(Exception):
    def __init__(self, message: str, *, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


class _InvalidStructuredOutput(ValueError):
    pass


class TranslationItem(ContractModel):
    source_id: str
    text_en: str


class TranslationOutput(ContractModel):
    translations: list[TranslationItem]

    def by_source(self, expected_ids: set[str]) -> dict[str, str]:
        translations = {item.source_id: item.text_en for item in self.translations}
        if len(translations) != len(self.translations) or translations.keys() != expected_ids:
            raise ValueError("Return each provided source ID exactly once and no other IDs.")
        if any(not translation.strip() for translation in translations.values()):
            raise ValueError("Provide a nonempty English translation for each source.")
        return translations


def _build_client(opt_in: str) -> AsyncOpenAI | None:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if os.environ.get(opt_in) != "1" or not api_key:
        return None
    return AsyncOpenAI(
        api_key=api_key,
        base_url="https://api.openai.com/v1",
        max_retries=0,
    )


def build_translation_client() -> AsyncOpenAI | None:
    """Create the app-lifetime client only when live translation is enabled."""
    return _build_client("OPENAI_LIVE_TRANSLATION")


def build_question_client() -> AsyncOpenAI | None:
    """Create the app-lifetime client only when live question generation is enabled."""
    return _build_client("OPENAI_LIVE_QUESTIONS")


def _retry_after(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        delay = float(value)
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
        except (ValueError, TypeError, OverflowError):
            return 0.0
        delay = (date.astimezone(UTC) - datetime.now(UTC)).total_seconds()
    return max(delay, 0.0) if math.isfinite(delay) else 0.0


def _apply_translations(
    snapshot: ListingSnapshot, translations: dict[str, str] | None
) -> ListingSnapshot:
    name_sources = snapshot.option_name_sources()
    by_source = translations or {}
    return snapshot.model_copy(
        update={
            "options": [
                option.model_copy(
                    update={"name_en": by_source.get(name_sources[option.option_id].source_id)}
                )
                for option in snapshot.options
            ],
            "sources": [
                source.model_copy(update={"text_en": by_source.get(source.source_id)})
                if source.kind == "listing_text"
                else source
                for source in snapshot.sources
            ],
            "limitations": (
                [*snapshot.limitations, TRANSLATION_LIMITATION]
                if translations is None and TRANSLATION_LIMITATION not in snapshot.limitations
                else snapshot.limitations
            ),
        }
    )


async def _request_structured(
    *,
    client: AsyncOpenAI,
    payload: str,
    prompt: str,
    schema_name: str,
    schema: type[StructuredOutput],
    validate: Callable[[StructuredOutput], ValidatedOutput],
    deadline_seconds: float,
) -> ValidatedOutput:
    output_format = {
        "type": "json_schema",
        "name": schema_name,
        "strict": True,
        "schema": schema.model_json_schema(),
    }
    loop = asyncio.get_running_loop()
    deadline = loop.time() + deadline_seconds
    instructions = prompt
    temporary_failure = _ProviderFailure("OpenAI is temporarily unavailable. Try again.")
    malformed = _InvalidStructuredOutput("The model did not return a valid result.")
    failure: Exception = temporary_failure
    quota_message = "OpenAI API quota or billing prevents this request. Check the server's API account."
    config_message = "OpenAI rejected the request. Check API settings and model access."

    try:
        async with asyncio.timeout_at(deadline):
            for attempt in range(2):
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                try:
                    response = await client.responses.create(
                        model=TRANSLATION_MODEL,
                        reasoning={"effort": "medium"},
                        instructions=instructions,
                        input=payload,
                        text={"format": output_format},
                        max_output_tokens=4096,
                        store=False,
                        truncation="disabled",
                        timeout=remaining,
                    )
                except APIStatusError as error:
                    if error.code in QUOTA_ERRORS:
                        raise _ProviderFailure(quota_message, retryable=False) from None
                    if error.status_code == 408:
                        raise TimeoutError from None
                    if error.status_code != 429 and error.status_code < 500:
                        raise _ProviderFailure(config_message, retryable=False) from None
                    failure = temporary_failure
                    delay = _retry_after(error.response.headers.get("Retry-After"))
                    if attempt or delay >= deadline - loop.time():
                        break
                    await asyncio.sleep(delay)
                    continue
                except (APIResponseValidationError, json.JSONDecodeError):
                    failure = malformed
                    continue

                if not isinstance(response, Response):
                    failure = malformed
                    continue

                if response.error is not None:
                    error_code = getattr(response.error, "code", None)
                    if not isinstance(error_code, str):
                        failure = malformed
                        continue
                    if error_code in QUOTA_ERRORS:
                        raise _ProviderFailure(quota_message, retryable=False)
                    if error_code in {"invalid_prompt", "data_residency_mismatch"}:
                        raise _ProviderFailure(config_message, retryable=False)
                    if error_code in {"server_error", "rate_limit_exceeded"}:
                        failure = temporary_failure
                        continue
                    raise _ProviderFailure("OpenAI could not complete the request. Try again.")
                if response.status != "completed":
                    raise _ProviderFailure("The model response was incomplete. Try again.")

                try:
                    if any(
                        part.type == "refusal"
                        for item in response.output
                        if item.type == "message"
                        for part in item.content
                    ):
                        raise _ProviderFailure("The model refused the request. Try again.")
                    result = schema.model_validate_json(response.output_text)
                except (AttributeError, TypeError, ValidationError):
                    failure = malformed
                    instructions = f"{prompt}\nThe previous reply did not match the required JSON schema."
                    continue
                try:
                    output = validate(result)
                except ValueError as error:
                    failure = _InvalidStructuredOutput(str(error))
                    instructions = f"{prompt}\nCorrect the previous reply. {error}"
                    continue
                if loop.time() < deadline:
                    return output
                raise TimeoutError
    except TimeoutError:
        raise _ProviderFailure("The OpenAI request timed out. Try again.") from None
    except APIConnectionError:
        raise temporary_failure from None
    raise failure


async def translate_listing(
    snapshot: ListingSnapshot, *, client: AsyncOpenAI | None = None
) -> ListingSnapshot:
    if client is None:
        raise TranslationConfigurationError(
            "Live translation is unavailable. Check the server's OpenAI API configuration."
        )

    sources = [
        {
            "source_id": source.source_id,
            "label": source.label,
            "option_ids": source.option_ids,
            "original_text": source.original_text,
        }
        for source in snapshot.sources
        if source.kind == "listing_text" and source.availability == "available"
    ]
    expected_ids = {source["source_id"] for source in sources}
    try:
        return await _request_structured(
            client=client,
            payload=json.dumps({"sources": sources}, ensure_ascii=False),
            prompt=PROMPT,
            schema_name="listing_translation",
            schema=TranslationOutput,
            validate=lambda result: _apply_translations(snapshot, result.by_source(expected_ids)),
            deadline_seconds=TRANSLATION_DEADLINE_SECONDS,
        )
    except _InvalidStructuredOutput:
        pass
    except _ProviderFailure as error:
        if not error.retryable:
            raise TranslationConfigurationError(str(error)) from None

    return _apply_translations(snapshot, None)


def _question_sources(request: QuestionInput) -> dict[str, Source]:
    selected = set(request.selected_option_ids)
    return {
        source.source_id: source
        for source in request.snapshot.sources
        if source.kind in {"listing_text", "official_guidance"}
        and source.availability == "available"
        and (not source.option_ids or selected.intersection(source.option_ids))
    }


def _normalized_question_text(text: str) -> str:
    return " ".join(normalize("NFKC", text).casefold().split()).rstrip(" ?!.")


def _named_option_references(text: str) -> set[str]:
    return {
        label
        for match in NAMED_OPTIONS.finditer(text)
        for label in re.findall(r"\b[ABC]\b", match[1].upper())
    }


def validate_question_result(request: QuestionInput, result: QuestionResult) -> None:
    """Check the draft against the same allowed evidence and history sent to the model."""
    selected = set(request.selected_option_ids)
    seen_prompts = {_normalized_question_text(answer.question_text) for answer in request.answers}
    sources = _question_sources(request)

    if IMAGE_CLAIM.search(result.reason):
        raise QuestionGenerationError("invalid_questions", "Base the reason on text and buyer input only.")

    for question in result.questions:
        prompt = _normalized_question_text(question.prompt)
        if not prompt or prompt in seen_prompts:
            raise QuestionGenerationError(
                "invalid_questions", "Do not repeat a question from this round or an earlier round."
            )
        seen_prompts.add(prompt)

        option_ids = set(question.option_ids)
        if len(option_ids) != len(question.option_ids) or not option_ids.issubset(selected):
            raise QuestionGenerationError(
                "invalid_questions", "Question option IDs must be unique and in the buyer's selection."
            )
        if len(set(question.source_ids)) != len(question.source_ids):
            raise QuestionGenerationError("invalid_questions", "Question source IDs must be unique.")
        partial_evidence_scope = set()
        for source_id in question.source_ids:
            source = sources.get(source_id)
            if source is None or (source.option_ids and not option_ids.intersection(source.option_ids)):
                raise QuestionGenerationError(
                    "invalid_questions", "Cite only supplied text sources in the question's option scope."
                )
            covered_options = option_ids.intersection(source.option_ids)
            if covered_options != option_ids:
                partial_evidence_scope.update(covered_options)

        if partial_evidence_scope and partial_evidence_scope != option_ids:
            covered_references = {
                reference
                for reference, option_id in zip(OPTION_REFERENCES, request.selected_option_ids)
                if option_id in partial_evidence_scope
            }
            named = _named_option_references(question.why_ask)
            public_text = (question.why_ask, question.prompt, *question.choices)
            if (
                not named
                or not named.issubset(covered_references)
                or any(GROUP_EVIDENCE_CLAIM.search(text) for text in public_text)
            ):
                raise QuestionGenerationError(
                    "invalid_questions",
                    "Explicitly name the supported options in why_ask without applying partial SKU evidence to the group.",
                )

        choices = {_normalized_question_text(choice) for choice in question.choices}
        if len(choices) != len(question.choices) or choices.intersection(
            {"", "not sure", "skip", "not sure / skip"}
        ):
            raise QuestionGenerationError(
                "invalid_questions", "Use distinct answer choices and leave skipping to the buyer controls."
            )

        for text in (question.prompt, question.why_ask, *question.choices):
            if IMAGE_CLAIM.search(text):
                raise QuestionGenerationError(
                    "invalid_questions", "Do not describe images in questions, reasons, or choices."
                )
            if not question.source_ids and LISTING_CLAIM.search(text):
                raise QuestionGenerationError(
                    "invalid_questions", "A claim about the listing must cite a supplied text source."
                )


async def generate_questions(
    request: QuestionInput, *, client: AsyncOpenAI | None = None
) -> QuestionResult:
    try:
        request = QuestionInput.model_validate(request.model_dump())
    except (AttributeError, ValidationError):
        raise QuestionGenerationError(
            "invalid_input", "The case input is invalid for this question round.", retryable=False
        ) from None
    if client is None:
        raise QuestionGenerationError(
            "analysis_failed",
            "Live question generation is unavailable. Check the server's OpenAI API configuration.",
            retryable=False,
        )

    options = {option.option_id: option for option in request.snapshot.options}
    selected = set(request.selected_option_ids)
    sources = _question_sources(request)
    payload = {
        "round_number": request.round_number,
        "selected_options": [
            {
                "option_id": option_id,
                "reference": reference,
                "name_original": options[option_id].name_original,
                "name_en": options[option_id].name_en,
            }
            for reference, option_id in zip(OPTION_REFERENCES, request.selected_option_ids)
        ],
        "buyer_context": request.buyer_context,
        "answers": [answer.model_dump() for answer in request.answers],
        "sources": [
            {
                "source_id": source.source_id,
                "kind": source.kind,
                "label": source.label,
                "option_ids": [option_id for option_id in source.option_ids if option_id in selected],
                "original_text": source.original_text,
                "text_en": source.text_en,
            }
            for source in sources.values()
        ],
    }
    LOGGER.info(
        "Question prompt %s round %d text sources: %s",
        QUESTION_PROMPT_VERSION,
        request.round_number,
        ", ".join(sources),
    )

    def validate(result: QuestionResult) -> QuestionResult:
        validate_question_result(request, result)
        return result

    try:
        return await _request_structured(
            client=client,
            payload=json.dumps(payload, ensure_ascii=False),
            prompt=QUESTION_PROMPT,
            schema_name="buyer_questions",
            schema=QuestionResult,
            validate=validate,
            deadline_seconds=QUESTION_DEADLINE_SECONDS,
        )
    except _InvalidStructuredOutput as error:
        LOGGER.info("Question prompt %s produced invalid questions", QUESTION_PROMPT_VERSION)
        raise QuestionGenerationError("invalid_questions", str(error)) from None
    except _ProviderFailure as error:
        raise QuestionGenerationError("analysis_failed", str(error), error.retryable) from None
