import asyncio
from base64 import b64decode
from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import Mock

import httpx2
import pytest
from openai import AsyncOpenAI

from backend import ai, report
from backend.contracts import ReviewInput
from backend.intake import parse_listing
from backend.report_models import ReviewContent, ReviewFailed, ReviewReady


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_EXAMPLE = ROOT / "docs/contracts/examples/report-contract-example.json"
RAW_ZIP = ROOT / "samples/raw-save/raw-save-redacted.zip"


@pytest.fixture
def example():
    data = json.loads(CONTRACT_EXAMPLE.read_text(encoding="utf-8"))
    data["input"]["answers"].append({
        "question_id": "q-open", "question_text": "Would you accept another option?", "answer_text": None,
    })
    data["input"]["answer_option_ids"] = {
        answer["question_id"]: data["input"]["selected_option_ids"] for answer in data["input"]["answers"]
    }
    request = ReviewInput.model_validate(data["input"])
    images = {
        source.source_id: ("image/png", b"mapped-thumbnail")
        for source in request.snapshot.sources if source.kind == "listing_image"
    }
    return request, images


@pytest.fixture(scope="module")
def parsed_listing():
    return parse_listing(RAW_ZIP.read_bytes())


@pytest.fixture
def parser_case(parsed_listing):
    selected = [parsed_listing.snapshot.options[i].option_id for i in (1, 6, 0)]
    request = ReviewInput.model_validate({
        "case_id": "parser-case", "revision": 7, "snapshot": parsed_listing.snapshot.model_dump(),
        "selected_option_ids": selected,
        "buyer_context": "Retail trial. Private note: Cedar-914.",
        "answers": [
            {"question_id": "issued-1", "question_text": "How will it be used?", "answer_text": "For hot drinks."},
            {"question_id": "issued-2", "question_text": "Which decoration works?", "answer_text": None},
        ],
        "answer_option_ids": {"issued-1": selected, "issued-2": [selected[1]]},
    })
    return request, {source_id: (image.mime, image.payload) for source_id, image in parsed_listing.images.items()}


def response(request, output=None, *, status="completed", refusal=False):
    if refusal:
        content = {"type": "refusal", "refusal": "Test refusal"}
    else:
        content = {"type": "output_text", "text": json.dumps(output)}
    return httpx2.Response(200, json={
        "model": "gpt-6.1-terra", "status": status, "error": None,
        "output": [{"type": "message", "content": [content]}],
    }, request=request)


def run_review(review_input, images, handler):
    async def run():
        async with AsyncOpenAI(
            api_key="offline-test-placeholder", max_retries=0, base_url="https://api.openai.com/v1",
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
        ) as client:
            return await report.generate_review(review_input, images, client=client)

    return asyncio.run(run())


def minimal_content(request, topic="capacity"):
    return {
        "contract_version": 2, "claims": [], "buyer_requirements": [], "open_preferences": [], "findings": [],
        "comparisons": [{
            "topic": topic, "shared_claim_ids": [],
            "values": [{"option_id": option_id, "claim_ids": []} for option_id in request.selected_option_ids],
        }],
        "coverage": [
            {"group": "product_match", "status": "limited", "finding_ids": []},
            {"group": "intended_use", "status": "not_assessed", "finding_ids": []},
            {"group": "supporting_evidence", "status": "limited", "finding_ids": []},
        ],
    }


def text_claim(claim_id, source_id, quote, option_id=None):
    return {
        "claim_id": claim_id, "kind": "text_excerpt", "option_ids": [option_id] if option_id else [],
        "source_id": source_id, "quote": quote,
    }


def image_claim(claim_id, option_id, source_id, value=750, unit="ml"):
    return {
        "claim_id": claim_id, "kind": "image_measurement", "option_ids": [option_id],
        "source_id": source_id, "value": value, "unit": unit,
    }


def finding_selection(option_ids, **overrides):
    return {
        "finding_id": "capacity-check", "topic": "capacity", "option_ids": option_ids,
        "kind": "clarification_needed", "action_type": "ask_supplier", "requirement_ids": [],
        "claim_ids": [], "unknowns": [], "supplier_action": "confirm_specification", **overrides,
    }


def assemble(request, images, payload):
    return report.assemble_review(
        request, ReviewContent.model_validate(payload), sent_evidence=report.review_sent_evidence(request, images),
    )


def test_live_client_requires_report_opt_in(example, monkeypatch):
    request, _ = example
    client = Mock()
    monkeypatch.setattr(ai, "AsyncOpenAI", client)
    monkeypatch.setenv("OPENAI_API_KEY", "placeholder")
    monkeypatch.setenv("OPENAI_LIVE_QUESTIONS", "1")
    monkeypatch.delenv("OPENAI_LIVE_REVIEW", raising=False)
    assert report.build_review_client() is None
    assert asyncio.run(report.generate_review(request, {})).code == "analysis_failed"
    client.assert_not_called()

    monkeypatch.setenv("OPENAI_LIVE_REVIEW", "1")
    assert report.build_review_client() is client.return_value
    client.assert_called_once_with(
        api_key="placeholder", base_url="https://api.openai.com/v1", max_retries=0,
    )


def test_request_is_bound_to_real_selected_evidence(parser_case, caplog):
    review_input, images = parser_case
    requests = []

    def serve(request):
        requests.append(json.loads(request.content))
        content = minimal_content(review_input)
        image_id = f"{review_input.selected_option_ids[0]}-image"
        content["claims"] = [image_claim("first-label", review_input.selected_option_ids[0], image_id)]
        content["comparisons"][0]["values"][0]["claim_ids"] = ["first-label"]
        return response(request, content)

    with caplog.at_level("INFO"):
        result = run_review(review_input, images, serve)
    assert isinstance(result, ReviewReady)
    assert result.report.snapshot_id == review_input.snapshot.snapshot_id
    assert result.report.case_id == "parser-case"
    assert result.report.revision == 7
    assert result.report.findings == []
    assert result.report.supplier_draft == ""
    assert report.DETAIL_IMAGE_LIMITATION in result.report.limitations

    (body,) = requests
    assert body["store"] is False
    assert "tools" not in body
    assert body["max_output_tokens"] == 12_288
    assert body["reasoning"] == {"effort": "medium"}
    assert body["truncation"] == "disabled"
    schema = body["text"]["format"]
    assert schema["strict"] is True
    assert schema["schema"]["additionalProperties"] is False
    assert set(schema["schema"]["required"]) == set(ReviewContent.model_fields)
    assert body["instructions"] == report.REVIEW_PROMPT
    (message,) = body["input"]
    assert message["role"] == "user"
    json_input, *image_parts = message["content"]
    payload = json.loads(json_input["text"])
    assert set(payload) == {
        "selected_options", "buyer_context", "answers", "text_sources", "image_inventory",
        "attached_image_ids", "metadata_only_image_ids", "limitations",
    }
    assert [entry["reference"] for entry in payload["selected_options"]] == ["A", "B", "C"]
    assert [entry["option_id"] for entry in payload["selected_options"]] == review_input.selected_option_ids
    assert payload["buyer_context"] == review_input.buyer_context
    assert payload["answers"] == [
        {**answer.model_dump(), "option_ids": review_input.answer_option_ids[answer.question_id]}
        for answer in review_input.answers
    ]
    assert len(payload["image_inventory"]) == 12
    assert set(payload["attached_image_ids"]).isdisjoint(payload["metadata_only_image_ids"])
    assert set(payload["attached_image_ids"] + payload["metadata_only_image_ids"]) == images.keys()
    assert len(payload["attached_image_ids"]) == 3
    assert len(image_parts) == 6
    for source_id, label, image in zip(payload["attached_image_ids"], image_parts[::2], image_parts[1::2]):
        assert label == {"type": "input_text", "text": f"Attached image source IDs: {source_id}"}
        mime, original_bytes = images[source_id]
        assert image["type"] == "input_image"
        assert image["detail"] == "auto"
        assert image["image_url"].startswith(f"data:{mime};base64,")
        assert b64decode(image["image_url"].partition(",")[2]) == original_bytes
    assert "dmsc-food-contact" in {source["source_id"] for source in payload["text_sources"]}
    selected = set(review_input.selected_option_ids)
    assert all(not source["option_ids"] or selected.issuperset(source["option_ids"]) for source in payload["text_sources"])
    assert all("image_file" not in entry and "url" not in entry for entry in payload["image_inventory"])
    assert "Cedar-914" not in caplog.text


def test_shared_image_is_attached_once_with_both_source_ids(parser_case):
    review_input, images = parser_case
    first, second, absent = [f"{option_id}-image" for option_id in review_input.selected_option_ids]
    images[second] = images[first]
    del images[absent]

    def serve(request):
        parts = json.loads(request.content)["input"][0]["content"]
        payload = json.loads(parts[0]["text"])
        assert payload["attached_image_ids"] == [first, second]
        assert absent in payload["metadata_only_image_ids"]
        assert next(row for row in payload["image_inventory"] if row["source_id"] == absent)["availability"] == "available"
        assert len(parts) == 3
        assert parts[1]["text"] == f"Attached image source IDs: {first}, {second}"
        content = minimal_content(review_input)
        content["claims"] = [image_claim("second-label", review_input.selected_option_ids[1], second)]
        content["comparisons"][0]["values"][1]["claim_ids"] = ["second-label"]
        return response(request, content)

    result = run_review(review_input, images, serve)
    assert isinstance(result, ReviewReady)
    assert "The saved image for Option C was not attached for review." in result.report.limitations


def test_invalid_report_gets_one_repair_using_identical_input(parser_case):
    review_input, images = parser_case
    good = minimal_content(review_input)
    malformed = deepcopy(good)
    malformed["comparisons"][0]["values"].pop()
    bodies = []

    def serve(request):
        bodies.append(json.loads(request.content))
        return response(request, malformed if len(bodies) == 1 else good)

    assert isinstance(run_review(review_input, images, serve), ReviewReady)
    first, second = bodies
    assert first["input"] == second["input"]
    assert "Each comparison must contain every selected option once." in second["instructions"]


@pytest.mark.parametrize(("failure", "attempts", "code"), [
    ("incomplete", 1, "analysis_failed"), ("refusal", 1, "analysis_failed"),
    ("schema", 2, "invalid_review"), ("quota", 1, "analysis_failed"),
    ("transient", 2, "analysis_failed"), ("timeout", 1, "analysis_failed"),
])
def test_provider_failure_is_never_ready(parser_case, monkeypatch, failure, attempts, code):
    review_input, images = parser_case
    calls = 0

    async def serve(request):
        nonlocal calls
        calls += 1
        if failure == "timeout":
            await asyncio.sleep(1)
        if failure == "quota":
            return httpx2.Response(429, json={"error": {"code": "insufficient_quota", "message": "private detail"}})
        if failure == "transient":
            return httpx2.Response(503, json={"error": {"message": "private detail"}}, headers={"Retry-After": "0"})
        return response(request, {"invalid": True}, status="incomplete" if failure == "incomplete" else "completed",
                        refusal=failure == "refusal")

    if failure == "timeout":
        monkeypatch.setattr(report, "REVIEW_DEADLINE_SECONDS", 0.05)
    result = run_review(review_input, images, serve)
    assert calls == attempts
    assert isinstance(result, ReviewFailed)
    assert result.code == code
    assert result.case_id == review_input.case_id
    assert result.revision == review_input.revision
    assert "private detail" not in result.message


def test_invalid_selection_is_rejected_before_provider_call(parser_case):
    review_input, images = parser_case
    invalid = review_input.model_copy(update={"selected_option_ids": ["not-in-snapshot"]})
    result = run_review(invalid, images, lambda _: pytest.fail("unexpected provider call"))
    assert isinstance(result, ReviewFailed)
    assert result.code == "invalid_input"


def populated_content(request):
    payload = minimal_content(request)
    payload["claims"] = [
        text_claim("selector", "capacity-selector", "720ml"),
        image_claim("first-label", "v0", "image-v0"),
    ]
    payload["comparisons"][0]["shared_claim_ids"] = ["selector"]
    next(cell for cell in payload["comparisons"][0]["values"] if cell["option_id"] == "v0")["claim_ids"] = ["first-label"]
    payload["buyer_requirements"] = [{
        "requirement_id": "req-capacity", "topic": "capacity",
        "input_ref": "answer:q-capacity", "quote": "720 ml is required.",
    }]
    payload["findings"] = [finding_selection(
        ["v0"], kind="listing_conflict", claim_ids=["selector", "first-label"],
        requirement_ids=["req-capacity"], supplier_action="reconcile_sources",
        unknowns=[{"reason": "not_found", "source_ids": ["capacity-selector"]}],
    )]
    payload["coverage"][0]["finding_ids"] = ["capacity-check"]
    return payload


@pytest.mark.parametrize(("path", "replacement", "message"), [
    (("claims", 0, "source_id"), "invented", "actually sent"),
    (("claims", 0, "quote"), "1510 ml", "exact quote"),
    (("claims", 0, "option_ids"), ["v0"], "empty scope for shared"),
    (("claims", 1, "unit"), "kg", "matching topic"),
    (("findings", 0, "option_ids"), ["unknown-option"], "finding options"),
    (("findings", 0, "option_ids"), ["v0", "v0"], "finding options"),
    (("findings", 0, "requirement_ids"), ["invented"], "finding requirements"),
    (("findings", 0, "claim_ids"), ["invented"], "finding claims"),
    (("findings", 0, "unknowns", 0, "source_ids"), ["invented"], "unknown sources"),
    (("findings", 0, "supplier_action"), None, "reconcile_sources"),
    (("buyer_requirements", 0, "quote"), "made up", "verbatim quote"),
    (("buyer_requirements", 0, "input_ref"), "answer:q-open", "non-null buyer input"),
    (("buyer_requirements", 0, "input_ref"), "answer:old-round", "non-null buyer input"),
    (("open_preferences",), [
        {"preference_id": "p-old", "topic": "finish", "input_refs": ["answer:old-round"], "finding_ids": []},
    ], "preference input"),
    (("comparisons", 0, "values", 0, "claim_ids"), ["invented"], "comparison claims"),
    (("comparisons", 0, "values", 0, "option_id"), "v1", "comparison values"),
    (("comparisons", 0, "shared_claim_ids"), ["first-label"], "shared comparison claim"),
    (("coverage", 0, "group"), "intended_use", "coverage groups"),
    (("coverage", 0, "finding_ids"), ["invented"], "coverage findings"),
])
def test_cross_reference_validation_rejects_bad_content(example, path, replacement, message):
    request, images = example
    content = populated_content(request)
    target = content
    for step in path[:-1]:
        target = target[step]
    target[path[-1]] = replacement
    with pytest.raises(ValueError, match=message):
        assemble(request, images, content)


def test_draft_uses_selection_order_and_numbers_only_supplier_actions(example):
    request, images = example
    request = request.model_copy(update={"selected_option_ids": ["v6", "v0", "v1"]})
    content = minimal_content(request)
    content["comparisons"][0]["values"].reverse()
    content["findings"] = [
        finding_selection(["v1", "v6"], finding_id="photo", supplier_action="request_close_up"),
        finding_selection(["v0"], finding_id="decide", topic="finish", action_type="decide_preference", supplier_action=None),
        finding_selection(["v6"], finding_id="materials", topic="materials"),
    ]
    content["open_preferences"] = [{
        "preference_id": "p-open", "topic": "finish", "input_refs": ["context", "answer:q-open"], "finding_ids": ["decide"],
    }]
    rendered = assemble(request, images, content)

    names = {option.option_id: option.name_original for option in request.snapshot.options}
    assert rendered.supplier_draft.splitlines()[1:4] == [f"A. {names['v6']}", f"B. {names['v0']}", f"C. {names['v1']}"]
    first, second = rendered.supplier_draft.splitlines()[7::2]
    assert first.startswith("1. [Options A, C] Could you send a close-up photo")
    assert second.startswith("2. [Option A] Please confirm the materials")
    assert "40%" not in rendered.supplier_draft
    assert [value.option_id for value in rendered.comparisons[0].values] == ["v6", "v0", "v1"]
    assert [finding.finding_id for finding in rendered.findings] == ["photo", "decide", "materials"]
    assert rendered.open_preferences[0].input_refs == ["context", "answer:q-open"]

    content["findings"] = content["findings"][1:2]
    rendered = assemble(request, images, content)
    assert rendered.findings[0].supplier_question is None
    assert rendered.supplier_draft == ""

    content["open_preferences"] = []
    with pytest.raises(ValueError, match="open preference"):
        assemble(request, images, content)


@pytest.mark.parametrize("availability", ["available", "missing", "unreadable"])
def test_omitted_image_is_only_usable_as_unknown_metadata(example, availability):
    request, images = example
    image_id = "image-v0"
    del images[image_id]
    data = request.model_dump()
    source = next(source for source in data["snapshot"]["sources"] if source["source_id"] == image_id)
    source["availability"] = availability
    if availability != "available":
        source["image_file"] = None
    request = ReviewInput.model_validate(data)
    content = populated_content(request)
    with pytest.raises(ValueError, match="actually sent"):
        assemble(request, images, content)

    content = minimal_content(request)
    finding = finding_selection(["v0"], unknowns=[{"reason": "not_found", "source_ids": [image_id]}])
    content["findings"] = [finding]
    with pytest.raises(ValueError, match="not_found only"):
        assemble(request, images, content)
    reason = "not_reviewed" if availability == "available" else "unreadable"
    finding["unknowns"][0]["reason"] = reason
    rendered = assemble(request, images, content)
    assert rendered.findings[0].unknowns[0].reason == reason
    assert rendered.findings[0].unknowns[0].source_ids == [image_id]


@pytest.mark.parametrize("action_type", ["ask_supplier", "request_evidence"])
@pytest.mark.parametrize("corrected", [False, True])
def test_supplier_finding_without_a_selected_action_gets_one_correction(parser_case, action_type, corrected):
    request, images = parser_case
    malformed = minimal_content(request)
    malformed["findings"] = [finding_selection(request.selected_option_ids, action_type=action_type, supplier_action=None)]
    repaired = deepcopy(malformed)
    repaired["findings"][0]["supplier_action"] = "request_sample"
    bodies = []

    def serve(http_request):
        bodies.append(json.loads(http_request.content))
        return response(http_request, repaired if corrected and len(bodies) == 2 else malformed)

    result = run_review(request, images, serve)

    first, second = bodies
    assert first["input"] == second["input"]
    assert "must select a supplier action" in second["instructions"]
    if corrected:
        assert isinstance(result, ReviewReady)
        assert "1. [Options A, B, C] Could you send a sample" in result.report.supplier_draft
    else:
        assert isinstance(result, ReviewFailed)
        assert result.code == "invalid_review"


def test_deadline_preference_uses_actual_scoped_issued_question(example):
    request, images = example
    data = request.model_dump()
    data["answers"].append({
        "question_id": "a-deadline", "question_text": "What delivery deadline should we use for Option A?", "answer_text": None,
    })
    data["answer_option_ids"]["a-deadline"] = ["v0"]
    request = ReviewInput.model_validate(data)
    content = minimal_content(request, "deadline")
    content["findings"] = [finding_selection(
        ["v0"], topic="deadline", finding_id="decide-a", action_type="decide_preference", supplier_action=None,
    )]
    content["open_preferences"] = [{
        "preference_id": "deadline", "topic": "deadline", "input_refs": ["answer:a-deadline"], "finding_ids": ["decide-a"],
    }]

    rendered = assemble(request, images, content)
    preference = rendered.open_preferences[0]
    assert preference.option_ids == ["v0"]
    assert preference.questions[0].model_dump() == {
        "option_ids": ["v0"], "question_text": "What delivery deadline should we use for Option A?", "question_id": "a-deadline",
    }
    assert rendered.supplier_draft == ""

    content["findings"][0]["option_ids"] = ["v1"]
    with pytest.raises(ValueError, match="linked buyer input scopes"):
        assemble(request, images, content)


def test_answer_without_issued_option_scope_cannot_anchor_a_requirement(example):
    request, images = example
    data = request.model_dump()
    data["answer_option_ids"] = {}
    request = ReviewInput.model_validate(data)
    content = populated_content(request)
    with pytest.raises(ValueError, match="issued option scope"):
        assemble(request, images, content)
