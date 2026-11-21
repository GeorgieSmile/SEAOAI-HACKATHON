import asyncio
import json
import logging
from pathlib import Path
from unittest.mock import Mock

import httpx2
import pytest
from openai import AsyncOpenAI
from pydantic import ValidationError

from backend import ai
from backend.contracts import AnswerSubmission, IssuedRound, ListingSnapshot, QuestionInput, QuestionResult


FIXTURE = Path(__file__).resolve().parents[2] / "frontend/src/fixtures/integration-flow.json"


@pytest.fixture
def request_input():
    snapshot = json.loads(FIXTURE.read_text(encoding="utf-8"))["snapshot"]
    snapshot["sources"].append({
        "source_id": "unavailable-text",
        "kind": "listing_text",
        "label": "Unreadable seller details",
        "option_ids": ["v0"],
        "availability": "unreadable",
        "original_text": None,
        "text_en": None,
        "image_file": None,
        "url": None,
    })
    return QuestionInput(
        case_id="case-id", revision=0, snapshot=ListingSnapshot.model_validate(snapshot),
        selected_option_ids=["v1", "v0"], buyer_context="Tea resale. Private note: Cedar-914.",
        answers=[], round_number=1,
    )


def plan(**overrides):
    question = {
        "prompt": "Do you require a minimum capacity?",
        "why_ask": "The seller lists a capacity. A minimum changes which specification to confirm.",
        "option_ids": ["v1", "v0"],
        "source_ids": ["capacity-selector"],
        "choices": [],
        "topic": "capacity",
        "requirement_ids": [],
        "choice_effects": [],
    }
    return {
        "reason": "Check requirements that affect the order.", "context_requirements": [],
        "questions": [{**question, **overrides}],
    }


def api_response(request, output, *, status="completed"):
    return httpx2.Response(
        200,
        json={
            "status": status,
            "error": None,
            "output": [
                {"type": "message", "content": [{"type": "output_text", "text": json.dumps(output)}]}
            ],
        },
        request=request,
    )


def run_questions(request_input, respond):
    async def run():
        async with AsyncOpenAI(
            api_key="offline-test-placeholder",
            base_url="https://api.openai.com/v1",
            max_retries=0,
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(respond)),
        ) as client:
            return await ai.generate_questions(request_input, client=client)

    return asyncio.run(run())


def test_question_live_requires_its_own_opt_in_and_key(request_input, monkeypatch):
    factory = Mock()
    monkeypatch.setattr(ai, "AsyncOpenAI", factory)
    monkeypatch.setenv("OPENAI_API_KEY", "placeholder")
    monkeypatch.setenv("OPENAI_LIVE_TRANSLATION", "1")
    monkeypatch.delenv("OPENAI_LIVE_QUESTIONS", raising=False)
    assert ai.build_question_client() is None
    factory.assert_not_called()
    with pytest.raises(ai.QuestionGenerationError) as unavailable:
        asyncio.run(ai.generate_questions(request_input))
    assert unavailable.value.code == "analysis_failed"
    assert unavailable.value.retryable is False

    monkeypatch.setenv("OPENAI_LIVE_QUESTIONS", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "   ")
    assert ai.build_question_client() is None
    monkeypatch.setenv("OPENAI_API_KEY", "placeholder")
    assert ai.build_question_client() is factory.return_value
    factory.assert_called_once_with(
        api_key="placeholder", base_url="https://api.openai.com/v1", max_retries=0
    )


def test_questions_send_only_relevant_text_and_keep_answer_history(request_input, caplog):
    request_input = QuestionInput.model_validate({
        **request_input.model_dump(),
        "round_number": 2,
        "revision": 1,
        "answers": [
            {"question_id": "issued-1", "question_text": "How will it be used?", "answer_text": "For tea.",
             "buyer_facet": "other"},
            {"question_id": "issued-2", "question_text": "Which color works?", "answer_text": None},
        ],
    })
    requests = []

    def serve(request):
        requests.append(request)
        return api_response(request, {"reason": "There are no questions left for you.", "questions": []})

    with caplog.at_level(logging.INFO, logger="backend.ai"):
        result = run_questions(request_input, serve)

    assert result.questions == []
    request, = requests
    assert request.url.path == "/v1/responses"
    body = json.loads(request.content)
    assert body["instructions"] == ai.QUESTION_PROMPT
    assert body["model"] == "gpt-6.1-terra"
    assert body["store"] is False
    assert "tools" not in body
    assert body["text"]["format"]["strict"] is True
    draft_schema = body["text"]["format"]["schema"]["$defs"]["QuestionDraft"]
    assert "buyer_facet" in draft_schema["required"]
    facet_schema = draft_schema["properties"]["buyer_facet"]
    assert "default" not in facet_schema
    assert {"type": "null"} in facet_schema["anyOf"]
    assert {"type": "string", "enum": [
        "order_type", "decoration_method", "surface_finish", "capacity", "delivery_deadline", "other",
    ]} in facet_schema["anyOf"]
    assert body["max_output_tokens"] == 4096
    payload = json.loads(body["input"])
    assert payload["round_number"] == 2
    assert payload["answers"] == [answer.model_dump() for answer in request_input.answers]
    assert payload["buyer_context"] == request_input.buyer_context
    assert [(option["reference"], option["option_id"]) for option in payload["selected_options"]] == [
        ("A", "v1"), ("B", "v0")
    ]
    sources = {source["source_id"]: source for source in payload["sources"]}
    assert set(sources) == {
        "product-title", "name-v0", "name-v1", "capacity-selector", "customization-menu",
        "interior", "exterior", "lid", "report-availability", "dmsc-food-contact",
    }
    assert sources["capacity-selector"]["option_ids"] == ["v0", "v1"]
    assert sources["dmsc-food-contact"]["kind"] == "official_guidance"
    assert all("image_file" not in source and "url" not in source for source in sources.values())
    assert "Cedar-914" not in caplog.text
    assert ai.QUESTION_PROMPT_VERSION in caplog.text and "capacity-selector" in caplog.text


def test_round_four_is_rejected_before_provider_request(request_input):
    impossible = request_input.model_copy(update={"round_number": 4})

    def unexpected_request(_):
        pytest.fail("Sent an invalid question input to the provider")

    with pytest.raises(ai.QuestionGenerationError) as invalid:
        run_questions(impossible, unexpected_request)
    assert invalid.value.code == "invalid_input"
    assert invalid.value.retryable is False


def test_first_provider_response_can_save_context_without_issuing_a_question(request_input):
    stock = "Ready-made stock only."
    decoration = "The logo must be laser engraved."
    request_input = request_input.model_copy(update={
        "buyer_context": f"{stock} {decoration} Internal margin target: 43.5%.",
    })
    payload = {
        "reason": "The relevant purchase constraints are clear.",
        "questions": [],
        "context_requirements": [
            {"statement": stock, "quote": stock, "topic": "customization", "option_ids": request_input.selected_option_ids},
            {"statement": decoration, "quote": decoration, "topic": "finish", "option_ids": request_input.selected_option_ids},
        ],
    }

    def serve(request):
        wire = json.loads(request.content)
        schema = wire["text"]["format"]["schema"]
        assert "context_requirements" in schema["required"]
        assert {"topic", "status", "buyer_facet", "supplier_target", "quote"} <= set(
            schema["$defs"]["ContextRequirement"]["required"],
        )
        return api_response(request, payload)

    result = run_questions(request_input, serve)
    assert result.questions == []
    assert [row.quote for row in result.context_requirements] == [stock, decoration]


@pytest.mark.parametrize(("context", "excerpt"), [
    ("We do not need a stock-only order.", "need a stock-only order."),
    ("The logo for Option A must be laser engraved.", "The logo for Option A must be laser engraved."),
])
def test_context_proposals_cannot_clip_buyer_polarity_or_expand_explicit_scope(request_input, context, excerpt):
    request_input = request_input.model_copy(update={"buyer_context": context})
    payload = {
        "reason": "The relevant purchase constraints are clear.",
        "questions": [],
        "context_requirements": [{
            "statement": excerpt, "quote": excerpt, "topic": "finish", "option_ids": request_input.selected_option_ids,
        }],
    }
    with pytest.raises(ai.QuestionGenerationError) as invalid:
        run_questions(request_input, lambda request: api_response(request, payload))
    assert invalid.value.code == "invalid_questions"


def test_followup_provider_sees_ordered_answer_scope_and_current_ids(request_input):
    original = "Ready-made stock only."
    question_id = "submitted-sample-question"
    request_input = QuestionInput.model_validate({
        **request_input.model_dump(),
        "round_number": 2,
        "revision": 1,
        "buyer_context": original,
        "answers": [{"question_id": question_id, "question_text": "Do you need a sample?", "answer_text": None}],
        "answer_option_ids": {question_id: ["v1"]},
        "requirements": [{
            "requirement_id": "saved-stock-decision", "statement": original, "status": "active",
            "topic": "customization", "buyer_facet": "order_type", "option_ids": ["v1"],
            "provenance": [{
                "input_ref": "context", "quote": original, "question_text": None,
                "action": "add", "option_ids": ["v1"], "context_start": 0,
            }],
        }],
    })
    output = plan(
        prompt="Is ready-made stock still the only option you will accept?",
        why_ask="Your decision changes which order terms we should confirm.",
        option_ids=["v1"], source_ids=[], topic="customization", buyer_facet="order_type",
        requirement_ids=["saved-stock-decision"], choices=["Keep ready-made stock only."],
        choice_effects=[{
            "choice": "Keep ready-made stock only.", "action": "keep",
            "requirement_id": "saved-stock-decision", "statement": None,
        }],
    )
    output["context_requirements"] = None
    attempts = []

    def serve(request):
        wire = json.loads(json.loads(request.content)["input"])
        assert wire["requirements"][0]["requirement_id"] == "saved-stock-decision"
        assert wire["answers"][0]["question_id"] == question_id
        assert wire["answers"][0]["option_ids"] == ["v1"]
        attempts.append(request)
        if len(attempts) == 1:
            return api_response(request, {**output, "questions": [{**output["questions"][0], "option_ids": ["v0"]}]})
        return api_response(request, output)

    result = run_questions(request_input, serve)
    assert len(attempts) == 2
    assert result.questions[0].option_ids == ["v1"]
    assert result.questions[0].requirement_ids == ["saved-stock-decision"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"option_ids": ["v6"]},
        {"option_ids": ["v0"], "source_ids": ["name-v1"]},
        {"option_ids": ["v1", "v1"]},
        {"source_ids": ["name-v6"]},
        {"source_ids": ["image-v0"]},
        {"source_ids": ["unavailable-text"]},
        {"source_ids": ["fabricated"]},
        {"source_ids": ["capacity-selector", "capacity-selector"]},
        {"source_ids": []},
        {"why_ask": "Based on the saved photo, the size appears correct."},
        {"choices": ["The image shows a large cup."]},
        {"choices": ["Blue", "BLUE "]},
        {"choices": ["Not sure / Skip"]},
        {"buyer_facet": "arbitrary-topic"},
        {"question_id": "invented"},
    ],
)
def test_invalid_questions_fail_after_at_most_one_correction(request_input, overrides):
    requests = []

    def serve(request):
        requests.append(json.loads(request.content))
        return api_response(request, plan(**overrides))

    with pytest.raises(ai.QuestionGenerationError) as invalid:
        run_questions(request_input, serve)
    assert invalid.value.code == "invalid_questions"
    assert len(requests) == 2
    assert requests[0]["input"] == requests[1]["input"]
    assert requests[0]["instructions"] != requests[1]["instructions"]


@pytest.mark.parametrize("defect", ["too_many", "same_round", "previous_round"])
def test_correction_rejects_excess_or_repeated_questions(request_input, defect):
    output = plan()
    question, = output["questions"]
    if defect == "too_many":
        output["questions"] = [{**question, "prompt": f"Question number {n}?"} for n in range(6)]
    elif defect == "same_round":
        output["questions"].append({**question, "prompt": "do YOU require a minimum capacity "})
    else:
        output["context_requirements"] = None
        request_input = QuestionInput.model_validate({
            **request_input.model_dump(),
            "requirements": [],
            "round_number": 2,
            "revision": 1,
            "answers": [{
                "question_id": "skipped-1",
                "question_text": question["prompt"],
                "answer_text": None,
            }],
        })

    with pytest.raises(ai.QuestionGenerationError) as invalid:
        run_questions(request_input, lambda request: api_response(request, output))
    assert invalid.value.code == "invalid_questions"


@pytest.mark.parametrize("why_ask", [
    "Options A and B use applied-picture wording. Your preference changes what to confirm.",
    "Both options use applied-picture wording. Your preference changes what to confirm.",
    "Option B uses applied-picture wording, so both options use decals.",
])
def test_partial_sku_evidence_cannot_support_the_group(request_input, why_ask):
    output = plan(
        why_ask=why_ask,
        source_ids=["name-v0", "customization-menu", "capacity-selector"],
    )
    with pytest.raises(ai.QuestionGenerationError) as invalid:
        run_questions(request_input, lambda request: api_response(request, output))
    assert invalid.value.code == "invalid_questions"


def test_correctly_attributed_partial_evidence_can_motivate_group_question(request_input):
    output = plan(
        prompt="Is screen printing required for Options A and B?",
        why_ask="Option B's name uses applied-picture wording. The process needs confirmation.",
        source_ids=["name-v0", "customization-menu", "capacity-selector"],
    )
    result = run_questions(request_input, lambda request: api_response(request, output))
    assert result == QuestionResult.model_validate(output)


def test_rejects_question_naming_a_different_sku_than_its_citation(request_input):
    request_input = request_input.model_copy(update={"selected_option_ids": ["v0", "v6"]})
    assert [option.name_original for option in request_input.snapshot.options if option.option_id in {"v0", "v6"}] == [
        "暮色316-贴画粉蓝", "暮色316-字母粉蓝",
    ]
    output = plan(
        option_ids=["v0"],
        source_ids=["name-v0"],
        prompt="Would you accept applied-picture artwork on Option B?",
        why_ask="Option B's name describes applied-picture artwork.",
    )

    with pytest.raises(ai.QuestionGenerationError) as invalid:
        run_questions(request_input, lambda request: api_response(request, output))
    assert invalid.value.code == "invalid_questions"


@pytest.mark.parametrize("field, text", [
    ("prompt", "Since Option B's name describes applied-picture artwork, would you accept it?"),
    ("choices", "I would accept it because Option B's name describes applied-picture artwork."),
])
def test_buyer_preferences_do_not_establish_their_seller_premise(request_input, field, text):
    request_input = request_input.model_copy(update={"selected_option_ids": ["v0", "v6"]})
    output = plan(
        option_ids=["v0", "v6"], source_ids=["name-v0"],
        why_ask="Option A's name uses applied-picture wording. Your preference affects what to verify.",
    )
    output["questions"][0][field] = [text] if field == "choices" else text
    with pytest.raises(ai.QuestionGenerationError) as invalid:
        run_questions(request_input, lambda request: api_response(request, output))
    assert invalid.value.code == "invalid_questions"


@pytest.mark.parametrize(("field", "text"), [
    ("prompt", "Would you accept this finish on SKU B?"),
    ("prompt", "Would finish affect your decision for Options A/b?"),
    ("prompt", "Would finish affect your decision for options A and b?"),
    ("why_ask", "option B's name describes applied-picture artwork."),
    ("why_ask", "B’s name describes applied-picture artwork."),
    ("choices", "I would accept this artwork on Variant B."),
    ("reason", "Option B's name describes applied-picture artwork."),
    ("reason", "Option D has a known finish."),
])
def test_every_public_field_checks_its_named_options(request_input, field, text):
    request_input = request_input.model_copy(update={"selected_option_ids": ["v0", "v6"]})
    output = plan(
        option_ids=["v0"],
        source_ids=["name-v0"],
        prompt="Would you accept applied-picture artwork on Option A?",
        why_ask="Option A's name describes applied-picture artwork.",
    )
    if field == "reason":
        output["reason"] = text
    else:
        output["questions"][0][field] = [text] if field == "choices" else text

    with pytest.raises(ai.QuestionGenerationError) as invalid:
        run_questions(request_input, lambda request: api_response(request, output))
    assert invalid.value.code == "invalid_questions"


@pytest.mark.parametrize("field", ["prompt", "choices", "reason"])
def test_grouped_question_cannot_attribute_partial_sku_evidence_to_the_other_option(request_input, field):
    output = plan(
        prompt="Would you accept this finish for Options A/B?",
        why_ask="Option B's name uses applied-picture wording. The process needs confirmation.",
        source_ids=["name-v0", "customization-menu", "capacity-selector"],
    )
    claim = "Option A's name describes applied-picture artwork."
    if field == "reason":
        output["reason"] = claim
    else:
        output["questions"][0][field] = [claim] if field == "choices" else (
            f"{claim} Would you accept it?"
        )

    with pytest.raises(ai.QuestionGenerationError) as invalid:
        run_questions(request_input, lambda request: api_response(request, output))
    assert invalid.value.code == "invalid_questions"


def test_shared_selector_evidence_does_not_establish_an_option_name(request_input):
    output = plan(why_ask="Option A's name describes applied-picture artwork.")

    with pytest.raises(ai.QuestionGenerationError) as invalid:
        run_questions(request_input, lambda request: api_response(request, output))
    assert invalid.value.code == "invalid_questions"


def test_lowercase_a_is_still_a_reference_when_it_names_the_option(request_input):
    output = plan(
        option_ids=["v0"],
        source_ids=["name-v0"],
        why_ask="The option a is listed with applied-picture artwork.",
    )
    with pytest.raises(ai.QuestionGenerationError) as invalid:
        run_questions(request_input, lambda request: api_response(request, output))
    assert invalid.value.code == "invalid_questions"


def test_corrected_named_reference_follows_selection_order_without_misreading_an_article(request_input):
    request_input = request_input.model_copy(update={"selected_option_ids": ["v6", "v0"]})
    draft = plan(
        option_ids=["v0"],
        source_ids=["name-v0"],
        why_ask="SKU B's name describes applied-picture artwork.",
        prompt="Would you accept this artwork on an option a buyer can choose in advance?",
        choices=["I would accept this artwork on Option B."],
    )
    wrong = plan(**{**draft["questions"][0], "why_ask": "SKU A's name describes applied-picture artwork."})
    attempts = iter([wrong, draft])
    result = run_questions(request_input, lambda request: api_response(request, next(attempts)))
    assert result == QuestionResult.model_validate(draft)


def test_valid_grouped_option_choice_and_separately_scoped_shared_claim(request_input):
    output = plan(
        prompt="Is screen printing required for Options A/B?",
        why_ask="Option B's name uses applied-picture wording. The shared selector lists 720 ml for Options A and B.",
        source_ids=["name-v0", "capacity-selector"],
        choices=["Only for Option A.", "For Options A and B."],
    )
    result = run_questions(request_input, lambda request: api_response(request, output))
    assert result == QuestionResult.model_validate(output)


def test_valid_unscoped_seller_claim_can_accompany_a_neutral_option_comparison(request_input):
    output = plan(
        prompt="The seller lists general customization services, would you consider them for Options A/B?",
        why_ask="The seller lists a general customization menu relevant to your decision about Option B.",
        source_ids=["customization-menu"],
    )
    result = run_questions(request_input, lambda request: api_response(request, output))
    assert result == QuestionResult.model_validate(output)


def test_prefatory_option_reference_can_attribute_a_sku_name(request_input):
    request_input = request_input.model_copy(update={"selected_option_ids": ["v0", "v1"]})
    output = plan(
        why_ask="For Option A, the SKU name includes 贴画 (applied-picture artwork). "
                "Your preference determines which design to verify.",
        source_ids=["name-v0"],
    )
    result = run_questions(request_input, lambda request: api_response(request, output))
    assert result == QuestionResult.model_validate(output)

    output["questions"][0]["why_ask"] = (
        "For Option B, the SKU name includes 贴画 (applied-picture artwork). "
        "For Option A, your preference determines what to verify."
    )
    with pytest.raises(ai.QuestionGenerationError) as invalid:
        run_questions(request_input, lambda request: api_response(request, output))
    assert invalid.value.code == "invalid_questions"


def test_partial_evidence_allows_neutral_reference_on_the_supported_option(request_input):
    output = plan(
        why_ask="For Option B, this might affect the details you need confirmed.",
        source_ids=["name-v0"],
    )
    result = run_questions(request_input, lambda request: api_response(request, output))
    assert result == QuestionResult.model_validate(output)


@pytest.mark.parametrize("choice", [
    "For Option A, I need the artwork confirmed.",
    "I would buy Option A, and I need the artwork confirmed.",
    "For Option A, I’d need the artwork confirmed.",
])
def test_first_person_clause_is_not_part_of_an_option_list(request_input, choice):
    request_input = request_input.model_copy(update={"selected_option_ids": ["v0", "v1"]})
    output = plan(
        option_ids=["v0"], source_ids=["name-v0"],
        why_ask="Option A's name describes applied-picture artwork.",
        choices=[choice],
    )
    result = run_questions(request_input, lambda request: api_response(request, output))
    assert result == QuestionResult.model_validate(output)

    output["questions"][0]["choices"] = [choice.replace("Option A", "Option B")]
    with pytest.raises(ai.QuestionGenerationError) as invalid:
        run_questions(request_input, lambda request: api_response(request, output))
    assert invalid.value.code == "invalid_questions"


def test_first_person_clause_does_not_hide_real_lists(request_input):
    request_input = request_input.model_copy(update={"selected_option_ids": ["v0", "v1", "v6"]})
    output = plan(
        option_ids=["v0", "v1", "v6"],
        choices=["For Options A, B, and C, I need exact dimensions."],
    )
    result = run_questions(request_input, lambda request: api_response(request, output))
    assert result == QuestionResult.model_validate(output)

    for choice in (
        "I would accept Options A, B, and I.",
        "Options A, B, and I would meet my requirements.",
        "For Options A, I, and C, I need exact dimensions.",
    ):
        output["questions"][0]["choices"] = [choice]
        with pytest.raises(ai.QuestionGenerationError) as invalid:
            run_questions(request_input, lambda request: api_response(request, output))
        assert invalid.value.code == "invalid_questions"


@pytest.mark.parametrize("choice", [
    "For Option B, a printed proof would be enough.",
    "For Option B, a clearly dated, independently printed proof would be enough.",
    "I would buy Option B, and a dated printed proof would be enough.",
    "I would choose Option B and a signed copy of the proof.",
])
def test_article_clause_is_not_part_of_an_option_list(request_input, choice):
    output = plan(
        option_ids=["v0"], source_ids=[],
        prompt="Which proof do you need for Option B before ordering?",
        why_ask="Your answer determines which paperwork to request.",
        choices=[choice],
    )
    result = run_questions(request_input, lambda request: api_response(request, output))
    assert result == QuestionResult.model_validate(output)

    output["questions"][0]["choices"] = [choice.replace("Option B", "Option A")]
    with pytest.raises(ai.QuestionGenerationError) as invalid:
        run_questions(request_input, lambda request: api_response(request, output))
    assert invalid.value.code == "invalid_questions"


@pytest.mark.parametrize("choice", [
    "Options B, C, and a would require proof.",
    "Options B and a would require proof.",
    "Options B/a would require proof.",
    "Option B and SKU a would require proof.",
    "For Options B, a, and C, a printed proof would be enough.",
])
def test_real_lists_still_validate_every_option_with_a_lowercase_a(request_input, choice):
    request_input = request_input.model_copy(update={"selected_option_ids": ["v1", "v0", "v6"]})
    output = plan(
        option_ids=["v0", "v6"], source_ids=[],
        why_ask="Your answer determines which paperwork to request.",
        choices=[choice],
    )
    with pytest.raises(ai.QuestionGenerationError) as invalid:
        run_questions(request_input, lambda request: api_response(request, output))
    assert invalid.value.code == "invalid_questions"


def test_question_about_required_proof_does_not_assert_a_product_fact(request_input):
    request_input = request_input.model_copy(update={
        "buyer_context": "We supply schools and may need paperwork for the drinks containers.",
    })
    output = plan(
        option_ids=["v0"], source_ids=[],
        prompt="Do you need proof that Option B has passed independent testing before you buy it?",
        why_ask="Your procurement requirements determine which paperwork to ask the seller for.",
        choices=["I need proof that Option B has passed independent testing."],
    )
    result = run_questions(request_input, lambda request: api_response(request, output))
    assert result == QuestionResult.model_validate(output)

    output["questions"][0]["prompt"] = output["questions"][0]["prompt"].replace("Option B", "Option A")
    with pytest.raises(ai.QuestionGenerationError) as invalid:
        run_questions(request_input, lambda request: api_response(request, output))
    assert invalid.value.code == "invalid_questions"


@pytest.mark.parametrize(
    ("mode", "retryable", "attempts"),
    [("timeout", True, 1), ("503", True, 2), ("retry-after", True, 1),
     ("quota", False, 1), ("auth", False, 1), ("incomplete", True, 1)],
)
def test_provider_failures_are_not_empty_plans(request_input, monkeypatch, mode, retryable, attempts):
    seen = []
    monkeypatch.setattr(ai, "QUESTION_DEADLINE_SECONDS", 0.05 if mode == "timeout" else 60)

    async def serve(request):
        seen.append(request)
        if mode == "timeout":
            await asyncio.sleep(2)
        if mode == "503":
            return httpx2.Response(503, request=request)
        if mode == "retry-after":
            return httpx2.Response(429, headers={"Retry-After": "90"}, request=request)
        if mode == "quota":
            return httpx2.Response(
                429, json={"error": {"code": "insufficient_quota", "message": "provider details"}},
                request=request,
            )
        if mode == "auth":
            return httpx2.Response(401, request=request)
        return api_response(request, plan(), status="incomplete")

    with pytest.raises(ai.QuestionGenerationError) as failure:
        run_questions(request_input, serve)
    assert failure.value.code == "analysis_failed"
    assert failure.value.retryable is retryable
    assert len(seen) == attempts
    assert "provider details" not in failure.value.message


def test_linked_fixture_issued_plans_use_current_answers_and_source_ids():
    linked = json.loads(FIXTURE.read_text(encoding="utf-8"))
    snapshot = ListingSnapshot.model_validate(linked["snapshot"])
    for case in linked["cases"]:
        rounds = [
            case["first_round"],
            *(step["expected_next_round"] for step in case["steps"] if "expected_next_round" in step),
        ]
        for round_data in rounds:
            issued = IssuedRound.model_validate(round_data)
            answered_count = sum(len(previous["questions"]) for previous in rounds[:issued.round_number - 1])
            request = QuestionInput(
                case_id=case["case_id"], revision=issued.round_number - 1, snapshot=snapshot,
                selected_option_ids=case["start"]["selected_option_ids"],
                buyer_context=case["start"]["buyer_context"],
                answers=case["expected_review_input"]["answers"][:answered_count],
                round_number=issued.round_number,
            )
            draft = QuestionResult.model_validate({
                "reason": issued.reason,
                "questions": [question.model_dump(exclude={"question_id"}) for question in issued.questions],
            })
            ai.validate_question_result(request, draft)


def test_submission_normalizes_blank_preserves_text_and_rejects_duplicate_or_client_prompt():
    request = {
        "round_id": "round",
        "answers": [
            {"question_id": "q1", "answer_text": " \n "},
            {"question_id": "q2", "answer_text": " Keep surrounding spaces "},
        ],
    }
    submission = AnswerSubmission.model_validate(request)
    assert [answer.answer_text for answer in submission.answers] == [None, " Keep surrounding spaces "]
    with pytest.raises(ValidationError):
        AnswerSubmission.model_validate({**request, "answers": [request["answers"][0]] * 2})
    with pytest.raises(ValidationError):
        AnswerSubmission.model_validate({
            **request,
            "answers": [{**request["answers"][0], "question_text": "Client-supplied prompt"}],
        })
