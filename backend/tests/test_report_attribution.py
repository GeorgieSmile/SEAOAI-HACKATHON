import json

import pytest

from backend.contracts import ReviewInput
from backend.report import assemble_review
from backend.report_models import ReviewContent
from backend.tests.test_reports import CONTRACT_EXAMPLE, minimal_content


@pytest.fixture
def joint_finding():
    fixture = json.loads(CONTRACT_EXAMPLE.read_text(encoding="utf-8"))
    request = ReviewInput.model_validate({**fixture["input"], "selected_option_ids": ["v6", "v0"]})
    content = minimal_content(request)
    finding = {
        "finding_id": "artwork", "title": "Confirm the decoration process", "option_ids": ["v6", "v0"],
        "kind": "requirement_unconfirmed", "action_type": "ask_supplier", "requirement_ids": [],
        "citations": [{"source_id": "name-v0", "quote": "贴画", "image_observation": None}],
        "interpretation": "The listing does not establish the exact decoration process.",
        "impact": "The process may affect whether the buyer orders.",
        "priority_reason": "Verify the process before buying.", "unknowns": [],
        "requested_evidence": "An exact SKU sample and a production specification.",
        "evidence_limits": "A specification may still require sample verification.",
        "supplier_question": "Could you confirm the decoration process for Options A and B?",
    }
    content["findings"] = [finding]
    content["coverage"][0]["finding_ids"] = ["artwork"]
    return request, content, finding


def validate(request, content):
    sent_sources = {source.source_id for source in request.snapshot.sources if source.kind != "listing_image"}
    return assemble_review(request, ReviewContent.model_validate(content), sent_source_ids=sent_sources)


@pytest.mark.parametrize("field", ["interpretation", "supplier_question"])
def test_shared_finding_cannot_use_b_name_evidence_for_a(joint_finding, field):
    request, content, finding = joint_finding
    finding[field] = "The listing name for Option A includes 贴画. Could you confirm the decoration method?"
    with pytest.raises(ValueError, match="cited SKU name"):
        validate(request, content)


@pytest.mark.parametrize(("field", "statement"), [
    ("interpretation", "The listing name for Option A includes 贴画."),
    ("interpretation", "Option A’s name includes 贴画 and Option B’s name includes 字母."),
    ("supplier_question", "Option A’s name includes 贴画 and Option B’s name includes 字母."),
    ("supplier_question", "Could you confirm the process? Since the listing name for Option A includes 贴画, please send a sample."),
])
def test_citing_both_names_does_not_allow_swapping_their_original_terms(joint_finding, field, statement):
    request, content, finding = joint_finding
    finding["citations"].append({"source_id": "name-v6", "quote": "字母", "image_observation": None})
    finding[field] = statement
    with pytest.raises(ValueError, match="original term"):
        validate(request, content)


def test_shared_finding_can_combine_a_translated_statement_and_real_sku_wording(joint_finding):
    request, content, finding = joint_finding
    finding["citations"].append({"source_id": "name-v6", "quote": "字母", "image_observation": None})
    finding["interpretation"] = "Option A’s name uses letter-design wording, while Option B’s name includes 贴画."
    finding["supplier_question"] = "The listing name for Option B includes 贴画. What is the finish for each option?"
    result = validate(request, content)
    assert result.findings[0].interpretation == finding["interpretation"]
    assert finding["supplier_question"] in result.supplier_draft


def test_seller_question_can_ask_about_an_unconfirmed_sku_name(joint_finding):
    request, content, finding = joint_finding
    finding["supplier_question"] = "Could you confirm whether the current listing name for Option A includes 贴画?"
    result = validate(request, content)
    assert finding["supplier_question"] in result.supplier_draft


def test_a_cited_sku_name_can_rule_out_a_wording(joint_finding):
    request, content, finding = joint_finding
    source = request.snapshot.option_name_sources()["v6"]
    finding["citations"].append({"source_id": source.source_id, "quote": source.original_text, "image_observation": None})
    finding["interpretation"] = "The listing name for Option A does not include 贴画."
    result = validate(request, content)
    assert result.findings[0].interpretation == finding["interpretation"]


def test_comparison_row_cannot_attribute_other_skus_name_wording(joint_finding):
    request, content, _ = joint_finding
    row = content["comparisons"][0]["values"][0]
    row["source_ids"] = ["name-v6"]
    row["summary"] = "The listing name for Option A includes 贴画."
    with pytest.raises(ValueError, match="original term"):
        validate(request, content)


def test_comparison_row_can_describe_its_own_cited_sku_name(joint_finding):
    request, content, _ = joint_finding
    row = content["comparisons"][0]["values"][0]
    row["source_ids"] = ["name-v6"]
    row["summary"] = "Option A's name includes 字母, which describes lettering."
    result = validate(request, content)
    assert result.comparisons[0].values[0].summary == row["summary"]


@pytest.mark.parametrize(("statement", "error"), [
    ("Option A’s name includes 字母 and 贴画.", "original term"),
    ("Option A’s name includes 316 and 贴画.", "original term"),
    ("Option A’s name includes 316, lettering, and 贴画.", "original term"),
    ("Option A’s name includes 316, letter design, and 贴画.", "original term"),
    ("Option A’s name includes 贴画 in the listing.", "original term"),
    ("Option A’s name does not include 318 or 字母.", "absent"),
    ("Option A’s name does not include 字母.", "absent"),
    ("Option A's name includes 字母 but not 粉蓝.", "absent"),
    ("Option A's name includes 字母, but does not include 粉蓝.", "absent"),
    ("Option A's name does not include 贴画 but includes 字母 and 贴画.", "original term"),
    ("Option A's name includes 字母 and 粉蓝, while Option B's name does not include 贴画.", "absent"),
    ("Option A’s name includes 字母, and 字母 appears only in Option B’s name.", "original term"),
    ("Option A’s name includes 字母, and 贴画 does not appear in Option B’s name.", "absent"),
])
def test_all_name_terms_must_match_their_option_and_polarity(joint_finding, statement, error):
    request, content, finding = joint_finding
    names = request.snapshot.option_name_sources()
    # Cite both SKUs, but omit 字母 from A's quote. An absence claim must still check its full name.
    a_quote = "粉蓝" if statement == "Option A’s name does not include 字母." else names["v6"].original_text
    finding["citations"] = [
        {"source_id": names["v6"].source_id, "quote": a_quote, "image_observation": None},
        {"source_id": names["v0"].source_id, "quote": names["v0"].original_text, "image_observation": None},
    ]
    finding["interpretation"] = statement
    with pytest.raises(ValueError, match=error):
        validate(request, content)


@pytest.mark.parametrize("statement", [
    "Option A’s name includes the Chinese words 字母 and 粉蓝.",
    "Option A’s name includes 316 and 字母.",
    "Option A’s name includes 316, lettering, and 粉蓝.",
    "Option A’s name includes letter design and 粉蓝.",
    "Option A’s name does not include 318 or 贴画.",
    "Option A's name does not include 贴画 or 黑白.",
    "Option A's name includes 字母 and 粉蓝, but not 贴画.",
    "Option A's name includes 字母, but does not include 贴画.",
    "Option A's name does not include 贴画 but includes 字母 and 粉蓝.",
    "Option A's name includes 字母 and 粉蓝, while Option B's name includes 贴画 but not 字母.",
    "Option A’s name includes 字母, and 贴画 appears only in Option B’s name.",
    "Option A’s name includes 字母, and 贴画 may be available as a custom finish.",
    "Option A’s name includes 字母, and 字母 does not appear in Option B’s name.",
    "Could you confirm whether 贴画 appears in Option A’s name?",
    "Could you confirm whether Option A's name includes 字母 and 贴画?",
])
def test_grounded_name_clauses_and_questions_still_pass(joint_finding, statement):
    request, content, finding = joint_finding
    names = request.snapshot.option_name_sources()
    finding["citations"] = [
        {"source_id": names[option_id].source_id, "quote": names[option_id].original_text, "image_observation": None}
        for option_id in request.selected_option_ids
    ]
    finding["supplier_question"] = statement
    result = validate(request, content)
    assert statement in result.supplier_draft
