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
    }
    return {"reason": "Check requirements that affect the order.", "questions": [{**question, **overrides}]}


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
            {"question_id": "issued-1", "question_text": "How will it be used?", "answer_text": "For tea."},
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
        request_input = QuestionInput.model_validate({
            **request_input.model_dump(),
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
