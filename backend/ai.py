import asyncio
import json
import math
import os
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

from openai import APIConnectionError, APIResponseValidationError, APIStatusError, AsyncOpenAI
from openai.types.responses import Response
from pydantic import ValidationError

from .contracts import ContractModel, ListingSnapshot


TRANSLATION_MODEL = "gpt-6.1-terra"
TRANSLATION_PROMPT_VERSION = "translate-listing-v1"
TRANSLATION_DEADLINE_SECONDS = 60
TRANSLATION_LIMITATION = "English translation is unavailable. The original listing text is shown."
QUOTA_ERRORS = {"insufficient_quota", "billing_hard_limit_reached", "billing_not_active", "quota_exceeded"}
PROMPT = (
    Path(__file__).parent / "prompts" / f"{TRANSLATION_PROMPT_VERSION}.txt"
).read_text(encoding="utf-8")


class TranslationConfigurationError(Exception):
    """Translation cannot run until API settings, access, or billing are fixed."""


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


def build_translation_client() -> AsyncOpenAI | None:
    """Create the app-lifetime client only when live usage is explicitly enabled."""
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if os.environ.get("OPENAI_LIVE_TRANSLATION") != "1" or not api_key:
        return None
    return AsyncOpenAI(
        api_key=api_key,
        base_url="https://api.openai.com/v1",
        max_retries=0,
    )


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
    payload = json.dumps({"sources": sources}, ensure_ascii=False)
    output_format = {
        "type": "json_schema",
        "name": "listing_translation",
        "strict": True,
        "schema": TranslationOutput.model_json_schema(),
    }
    loop = asyncio.get_running_loop()
    deadline = loop.time() + TRANSLATION_DEADLINE_SECONDS
    instructions = PROMPT

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
                        raise TranslationConfigurationError(
                            "OpenAI API quota or billing prevents translation. Check the server's API account."
                        ) from None
                    if error.status_code == 408:
                        break
                    if error.status_code != 429 and error.status_code < 500:
                        raise TranslationConfigurationError(
                            "OpenAI rejected the translation request. Check API settings and model access."
                        ) from None
                    delay = _retry_after(error.response.headers.get("Retry-After"))
                    if attempt or delay >= deadline - loop.time():
                        break
                    await asyncio.sleep(delay)
                    continue
                except (APIResponseValidationError, json.JSONDecodeError):
                    continue

                if not isinstance(response, Response):
                    continue

                if response.error is not None:
                    if response.error.code in QUOTA_ERRORS:
                        raise TranslationConfigurationError(
                            "OpenAI API quota or billing prevents translation. Check the server's API account."
                        )
                    if response.error.code in {"invalid_prompt", "data_residency_mismatch"}:
                        raise TranslationConfigurationError(
                            "OpenAI rejected the translation request. Check API settings and model access."
                        )
                    if response.error.code in {"server_error", "rate_limit_exceeded"}:
                        continue
                    break
                if response.status != "completed":
                    break

                try:
                    if any(
                        part.type == "refusal"
                        for item in response.output
                        if item.type == "message"
                        for part in item.content
                    ):
                        break
                    result = TranslationOutput.model_validate_json(response.output_text)
                except (AttributeError, TypeError, ValidationError):
                    instructions = (
                        f"{PROMPT}\nThe previous reply did not match the required JSON schema. "
                        "Return a translation for each supplied source ID in the requested schema."
                    )
                    continue
                try:
                    output = _apply_translations(snapshot, result.by_source(expected_ids))
                except ValueError as error:
                    instructions = f"{PROMPT}\nCorrect the previous reply. {error}"
                    continue
                if loop.time() < deadline:
                    return output
    except (TimeoutError, APIConnectionError):
        pass

    return _apply_translations(snapshot, None)
