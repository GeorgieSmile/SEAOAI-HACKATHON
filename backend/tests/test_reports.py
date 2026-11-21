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
    request = ReviewInput.model_validate(data["input"])
    expected = data["expected_result"]["report"]
    content = {name: expected[name] for name in ReviewContent.model_fields}
    sent_sources = {source.source_id for source in request.snapshot.sources if source.availability == "available"}
    return request, content, sent_sources


@pytest.fixture(scope="module")
def parsed_listing():
    return parse_listing(RAW_ZIP.read_bytes())


@pytest.fixture
def parser_case(parsed_listing):
    request = ReviewInput.model_validate({
        "case_id": "parser-case", "revision": 7, "snapshot": parsed_listing.snapshot.model_dump(),
        "selected_option_ids": [parsed_listing.snapshot.options[i].option_id for i in (1, 6, 0)],
        "buyer_context": "Retail trial. Private note: Cedar-914.",
        "answers": [
            {"question_id": "issued-1", "question_text": "How will it be used?", "answer_text": "For hot drinks."},
            {"question_id": "issued-2", "question_text": "Which decoration works?", "answer_text": None},
        ],
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


def minimal_content(review_input):
    return {
        "buyer_requirements": [], "open_preferences": [], "findings": [], "limitations": [],
        "comparisons": [{
            "label": "Saved listing coverage",
            "values": [{"option_id": option_id, "summary": "Saved option name available.", "source_ids": []}
                       for option_id in review_input.selected_option_ids],
        }],
        "coverage": [
            {"group": "product_match", "status": "limited", "finding_ids": [], "note": "Only the listing is available."},
            {"group": "intended_use", "status": "not_assessed", "finding_ids": [], "note": "Use details need verification."},
            {"group": "supporting_evidence", "status": "limited", "finding_ids": [], "note": "No product sample was included."},
        ],
    }


def test_live_client_requires_report_opt_in(example, monkeypatch):
    request, _, _ = example
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
        content["comparisons"][0]["values"][0]["source_ids"] = [image_id]
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
    assert payload["answers"] == [answer.model_dump() for answer in review_input.answers]
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
        content["comparisons"][0]["values"][1]["source_ids"] = [second]
        return response(request, content)

    result = run_review(review_input, images, serve)
    assert isinstance(result, ReviewReady)
    assert "The saved image for Option C was not attached for review." in result.report.limitations


def test_draft_uses_selection_order_and_numbers_questions_after_nulls(example):
    request, content, sent_sources = example
    request = request.model_copy(update={"selected_option_ids": ["v6", "v0", "v1"]})
    questions = ["Can you send a close-up?", None, "Please identify drink-contact materials."]
    for row, question in zip(content["findings"], questions):
        row["supplier_question"] = question
    mark_buyer_decision(content, content["findings"][1])
    report_value = report.assemble_review(request, ReviewContent.model_validate(content), sent_source_ids=sent_sources)
    names = {option.option_id: option.name_original for option in request.snapshot.options}
    assert report_value.supplier_draft.splitlines()[1:4] == [f"A. {names['v6']}", f"B. {names['v0']}", f"C. {names['v1']}"]
    assert report_value.supplier_draft.splitlines()[7:] == [
        f"1. [Options A, B, C] {questions[0]}", "", f"2. [Options A, B, C] {questions[2]}",
    ]
    assert "40%" not in report_value.supplier_draft
    assert [row.option_id for row in report_value.comparisons[0].values] == ["v6", "v0", "v1"]
    assert [row.finding_id for row in report_value.findings] == [row["finding_id"] for row in content["findings"]]


def test_supplier_draft_labels_each_question_with_its_own_skus(example):
    request, content, sent_sources = example
    request = request.model_copy(update={"selected_option_ids": ["v6", "v0", "v1"]})
    findings = content["findings"]
    scopes = [["v1", "v6"], ["v0"], ["v6"]]
    for finding, scope in zip(findings, scopes, strict=True):
        finding.update(
            option_ids=scope, title="Decoration check", citations=[], unknowns=[],
            interpretation="The decoration needs confirmation.",
            impact="The finish affects this purchase.", priority_reason="Confirm before ordering.",
            requested_evidence="A sample.", evidence_limits="The sample covers one unit.",
            supplier_question="Could you confirm the decoration for this option?",
        )
    mark_buyer_decision(content, findings[1])
    result = report.assemble_review(request, ReviewContent.model_validate(content), sent_source_ids=sent_sources)
    assert result.supplier_draft.splitlines()[7:] == [
        "1. [Options A, C] Could you confirm the decoration for this option?",
        "",
        "2. [Option A] Could you confirm the decoration for this option?",
    ]


def mark_buyer_decision(content, finding):
    finding.update(action_type="decide_preference", supplier_question=None)
    content["open_preferences"].append({
        "preference_id": "buyer-decision", "text": "The buyer needs to choose a preference.",
        "input_refs": [], "finding_ids": [finding["finding_id"]],
    })


def test_buyer_decision_without_a_supplier_question_has_no_draft(example):
    request, example_content, sent_sources = example
    content = minimal_content(request)
    finding = example_content["findings"][0]
    finding["requirement_ids"] = []
    content["findings"] = [finding]
    mark_buyer_decision(content, finding)

    result = report.assemble_review(request, ReviewContent.model_validate(content), sent_source_ids=sent_sources)

    assert result.findings[0].supplier_question is None
    assert result.supplier_draft == ""


@pytest.mark.parametrize(("path", "replacement", "message"), [
    (("findings", 0, "citations", 0, "source_id"), "invented", "source"),
    (("findings", 0, "citations", 0, "quote"), "An unsupported quote", "exact quote"),
    (("findings", 0, "citations", 0, "image_observation"), "A text image", "exact quote"),
    (("findings", 0, "option_ids"), ["unknown-option"], "finding options"),
    (("findings", 0, "option_ids"), ["v0", "v0"], "finding options"),
    (("findings", 0, "requirement_ids"), ["invented"], "finding requirements"),
    (("findings", 0, "unknowns", 0, "source_ids"), ["invented"], "source"),
    (("findings", 0, "action_type"), "decide_preference", "open preference"),
    (("buyer_requirements", 0, "quote"), "made up", "verbatim quote"),
    (("buyer_requirements", 0, "input_ref"), "answer:q-open", "non-null buyer input"),
    (("buyer_requirements", 0, "input_ref"), "answer:old-round", "non-null buyer input"),
    (("open_preferences",), [{"preference_id": "p-invented", "text": "Open", "input_refs": ["answer:old-round"], "finding_ids": []}], "preference input"),
    (("comparisons", 0, "values", 0, "source_ids"), ["name-v1"], "source"),
    (("comparisons", 0, "values", 0, "option_id"), "v1", "comparison values"),
    (("comparisons", 0, "values", 0, "summary"), "Option B shows applied-picture wording.", "option letters"),
    (("coverage", 0, "group"), "intended_use", "coverage groups"),
    (("coverage", 0, "finding_ids"), ["invented"], "coverage findings"),
    (("findings", 0, "supplier_question"), "Could you confirm our profit margin?", "private business details"),
    (("findings", 0, "supplier_question"), "Does this work at 40%?", "private business details"),
])
def test_cross_reference_validation_rejects_bad_content(example, path, replacement, message):
    request, content, sent_sources = example
    target = content
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = replacement
    with pytest.raises(ValueError, match=message):
        report.assemble_review(request, ReviewContent.model_validate(content), sent_source_ids=sent_sources)


def test_wrong_named_option_in_supplier_question_is_rejected(example):
    request, _, sent_sources = example
    content = minimal_content(request)
    content["findings"].append({
        "finding_id": "source-gap", "title": "Exact specification missing", "option_ids": ["v1"],
        "kind": "evidence_gap", "action_type": "ask_supplier", "requirement_ids": [], "citations": [],
        "interpretation": "The exact specification is unconfirmed.", "impact": "Specifications could vary.",
        "priority_reason": "Clarify before choosing an option.", "unknowns": [],
        "requested_evidence": "Written specification", "evidence_limits": "Seller statement only.",
        "supplier_question": "Could you share the exact specification for Option A?",
    })
    with pytest.raises(ValueError, match="option letters"):
        report.assemble_review(request, ReviewContent.model_validate(content), sent_source_ids=sent_sources)


def test_null_answer_can_support_a_preference_but_not_a_requirement(example):
    request, content, sent_sources = example
    finding = content["findings"][0]
    finding["action_type"] = "decide_preference"
    finding["supplier_question"] = None
    content["open_preferences"] = [{
        "preference_id": "p-open", "text": "Accepting another option is still an open preference.",
        "input_refs": ["context", "answer:q-open"], "finding_ids": [finding["finding_id"]],
    }]
    result = report.assemble_review(request, ReviewContent.model_validate(content), sent_source_ids=sent_sources)
    assert result.open_preferences[0].input_refs == ["context", "answer:q-open"]
    assert all(requirement.input_ref != "answer:q-open" for requirement in result.buyer_requirements)


@pytest.mark.parametrize("availability", ["available", "missing", "unreadable"])
def test_omitted_image_is_only_usable_as_unknown_metadata(example, availability):
    request, content, sent_sources = example
    image_id = "image-v0"
    request_data = request.model_dump()
    source = next(source for source in request_data["snapshot"]["sources"] if source["source_id"] == image_id)
    source["availability"] = availability
    if availability != "available":
        source["image_file"] = None
    request = ReviewInput.model_validate(request_data)
    sent_sources.remove(image_id)

    with pytest.raises(ValueError, match="comparison sources"):
        report.assemble_review(request, ReviewContent.model_validate(content), sent_source_ids=sent_sources)
    for comparison in content["comparisons"]:
        for value in comparison["values"]:
            value["source_ids"] = [source_id for source_id in value["source_ids"] if source_id != image_id]
    with pytest.raises(ValueError, match="Citations require available sources"):
        report.assemble_review(request, ReviewContent.model_validate(content), sent_source_ids=sent_sources)
    for finding in content["findings"]:
        finding["citations"] = [cite for cite in finding["citations"] if cite["source_id"] != image_id]
    with pytest.raises(ValueError, match="not_found only"):
        report.assemble_review(request, ReviewContent.model_validate(content), sent_source_ids=sent_sources)
    for finding in content["findings"]:
        for unknown in finding["unknowns"]:
            unknown["source_ids"] = [source_id for source_id in unknown["source_ids"] if source_id != image_id]
    reason = "not_reviewed" if availability == "available" else "unreadable"
    content["findings"][0]["unknowns"].append({"text": "No image observation available.", "reason": reason, "source_ids": [image_id]})
    result = report.assemble_review(request, ReviewContent.model_validate(content), sent_source_ids=sent_sources)
    assert result.findings[0].unknowns[-1].reason == reason


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


@pytest.mark.parametrize("action_type", ["ask_supplier", "request_evidence"])
@pytest.mark.parametrize("corrected", [False, True])
def test_supplier_action_without_a_question_gets_one_correction(parser_case, action_type, corrected):
    review_input, images = parser_case
    malformed = minimal_content(review_input)
    finding = {
        "finding_id": "confirm-sample", "title": "Sample details need confirmation",
        "option_ids": review_input.selected_option_ids, "kind": "clarification_needed", "action_type": action_type,
        "requirement_ids": [], "citations": [], "interpretation": "The listing does not identify sample availability.",
        "impact": "A sample can help confirm the finish.", "priority_reason": "Check the finish before buying.",
        "unknowns": [], "requested_evidence": "A verified sample of each selected option.",
        "evidence_limits": "One sample covers one unit.", "supplier_question": None,
    }
    malformed["findings"].append(finding)
    if action_type == "request_evidence":
        malformed["findings"].append({
            **finding, "finding_id": "confirm-photo", "action_type": "ask_supplier",
            "supplier_question": "Can you send a close-up photo?",
        })
    repaired = deepcopy(malformed)
    question = "Can you send a sample of each selected option?"
    repaired["findings"][0]["supplier_question"] = question
    bodies = []

    def serve(request):
        bodies.append(json.loads(request.content))
        return response(request, repaired if corrected and len(bodies) == 2 else malformed)

    result = run_review(review_input, images, serve)

    assert len(bodies) == 2
    first, second = bodies
    assert first["input"] == second["input"]
    assert "must include a supplier_question" in second["instructions"]
    if corrected:
        assert isinstance(result, ReviewReady)
        assert f"1. [Options A, B, C] {question}" in result.report.supplier_draft
        if action_type == "request_evidence":
            assert "2. [Options A, B, C] Can you send a close-up photo?" in result.report.supplier_draft
    else:
        assert isinstance(result, ReviewFailed)
        assert result.code == "invalid_review"


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
