from copy import deepcopy

import pytest
from pydantic import ValidationError

from backend.contracts import ReviewInput
from backend.report_models import ReviewFailed, ReviewReady
from backend.tests.test_reports import (
    assemble, example, finding_selection, minimal_content, parsed_listing, response, run_review,
)


def build_case(listing, buyer_text, *, source="answer"):
    option_id = listing.snapshot.options[0].option_id
    return ReviewInput.model_validate({
        "case_id": "privacy-case", "revision": 1, "snapshot": listing.snapshot.model_dump(),
        "selected_option_ids": [option_id],
        "buyer_context": buyer_text if source == "context" else "",
        "answers": [{
            "question_id": "issued-1", "question_text": "What else should the review consider?",
            "answer_text": buyer_text if source == "answer" else None,
        }],
        "answer_option_ids": {"issued-1": [option_id]},
    })


@pytest.mark.parametrize(("source", "buyer_text", "question"), [
    ("answer", "Reach cedar914@example.test before sampling.", "Can you send sample photos to cedar914@example.test?"),
    ("answer", "Reach 'cedar914@example.test' before sampling.", "Can you email cedar914@example.test?"),
    ("context", "Email: Cedar914＠example.test.", "Could you email CEDAR914@example.test?"),
    ("context", "I need a sample before ordering.", "Can you send details to samples@example.test?"),
    ("answer", "Call +1 (415) 555-0136 before sending the sample.", "Can you call +1-415-555-0136?"),
    ("context", "Please reach me on 081 234 5678.", "Can you send photos to 0812345678?"),
    ("answer", "Private note: 0812345678.", "Can you text 081-234-5678?"),
    ("answer", "Sample callback 020 7946 0958.", "Can you call 02079460958?"),
    ("answer", "My phone is 081-234-5678.", "Can you confirm product code 0812345678?"),
    ("answer", "Our internal margin target is 43.5% before shipping.", "Could we make 43.5% work?"),
    ("answer", "Internal note: Project Cedar-914 applies to this purchase.", "Can you use Cedar-914 on the sample?"),
    ("context", "Private note: use the Cedar Lotus Launch reference.", "Can you put Cedar Lotus Launch on the sample?"),
])
def test_provider_cannot_insert_private_buyer_details_in_supplier_question(parsed_listing, source, buyer_text, question):
    request = build_case(parsed_listing, buyer_text, source=source)
    content = minimal_content(request)
    content["findings"] = [finding_selection(request.selected_option_ids, supplier_question=question)]
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        assemble(request, {}, content)


@pytest.mark.parametrize(("buyer_text", "topic", "question_text"), [
    ("Buyer margin: 43.5%. We require 720 ml and 316L steel.", "capacity", "the delivered capacity"),
    ("Private note: project Cedar-914. Use SKU 316-A.", "sku_wording", "what is supplied under the exact listing name"),
    ("Reach cedar914@example.test before sampling.", "sample", "whether a sample"),
    ("Call 081-234-5678 before sampling. SKU code 1290472158.", "sku_wording", "what is supplied under the exact listing name"),
    ("Please verify the product UPC 0-36000-29145-2 before sampling.", "sku_wording", "what is supplied under the exact listing name"),
    ("Narrow to the barcode 012-345-6789 for the sample.", "sku_wording", "what is supplied under the exact listing name"),
    ("Check GTIN-14 000-3600-029-1452 for the carton.", "packaging", "the delivered packaging"),
    ("Please reference 020-7946-0958 (SKU number) when ordering.", "sku_wording", "what is supplied under the exact listing name"),
    ("Please call the seller about model number 012-345-6789.", "sku_wording", "what is supplied under the exact listing name"),
    ("I need the box to fit 190-135-121 mm.", "dimensions", "the delivered dimensions"),
])
def test_draft_asks_the_selected_product_question_without_repeating_buyer_text(parsed_listing, buyer_text, topic, question_text):
    request = build_case(parsed_listing, buyer_text)
    content = minimal_content(request, topic)
    content["buyer_requirements"] = [{
        "requirement_id": "buyer-detail", "topic": topic, "input_ref": "answer:issued-1", "quote": buyer_text,
    }]
    content["findings"] = [finding_selection(
        request.selected_option_ids, topic=topic, requirement_ids=["buyer-detail"],
    )]
    rendered = assemble(request, {}, content)

    assert question_text in rendered.supplier_draft
    assert rendered.buyer_requirements[0].quote == buyer_text
    assert buyer_text not in rendered.supplier_draft
    for detail in ("43.5%", "Cedar-914", "@example.test", "081-234-5678", "0-36000-29145-2", "190-135-121"):
        assert detail not in rendered.supplier_draft


def test_model_cannot_publish_a_report_that_keeps_leaking_contact_details(parsed_listing):
    request = build_case(parsed_listing, "Reach cedar914@example.test before sampling.")
    content = minimal_content(request)
    content["findings"] = [finding_selection(
        request.selected_option_ids, supplier_question="Send the sample photos to cedar914@example.test.",
    )]
    prompts = []

    def provider(http_request):
        prompts.append(http_request)
        return response(http_request, deepcopy(content))

    result = run_review(request, {}, provider)
    assert isinstance(result, ReviewFailed)
    assert result.code == "invalid_review"
    assert "cedar914@example.test" not in result.message
    assert len(prompts) == 2


def test_draft_preserves_listing_option_identifiers_without_copying_buyer_supplied_values(example):
    request, images = example
    data = request.model_dump()
    listing_name = "暮色316-贴画粉蓝 (UPC 0-36000-29145-2)"
    data["snapshot"]["options"][0]["name_original"] = listing_name
    data["snapshot"]["sources"][1]["original_text"] = listing_name
    data["buyer_context"] = "Private note: Cedar-914. Please use callback number 020-7946-0958."
    request = ReviewInput.model_validate(data)
    content = minimal_content(request, "sku_wording")
    content["findings"] = [finding_selection(["v0"], topic="sku_wording")]

    rendered = assemble(request, images, content)

    assert f"A. {listing_name}" in rendered.supplier_draft
    assert "Cedar-914" not in rendered.supplier_draft
    assert "020-7946-0958" not in rendered.supplier_draft


@pytest.mark.parametrize(("buyer_answer", "action", "expected", "absent"), [
    ("Ready-made only", "confirm_ready_made", "Please answer for ready-made stock.", "minimum order quantity"),
    ("Custom orders are acceptable", "confirm_custom_order", "lead time and minimum order quantity", "Please answer for ready-made stock."),
])
def test_stock_and_custom_supplier_actions_render_distinct_questions(parsed_listing, buyer_answer, action, expected, absent):
    request = build_case(parsed_listing, buyer_answer)
    payload = minimal_content(request, "finish")
    payload["buyer_requirements"] = [{
        "requirement_id": "order-type", "topic": "customization", "input_ref": "answer:issued-1", "quote": buyer_answer,
    }]
    payload["findings"] = [finding_selection(
        request.selected_option_ids, topic="finish", requirement_ids=["order-type"], supplier_action=action,
    )]

    result = run_review(request, {}, lambda http_request: response(http_request, payload))

    assert isinstance(result, ReviewReady)
    assert expected in result.report.supplier_draft
    assert absent not in result.report.supplier_draft
    assert buyer_answer not in result.report.supplier_draft


def test_order_type_action_requires_a_related_buyer_reference(parsed_listing):
    request = build_case(parsed_listing, "I need 720 mL.")
    payload = minimal_content(request, "finish")
    payload["buyer_requirements"] = [{
        "requirement_id": "capacity", "topic": "capacity", "input_ref": "answer:issued-1", "quote": "I need 720 mL.",
    }]
    payload["findings"] = [finding_selection(
        request.selected_option_ids, topic="finish", requirement_ids=["capacity"], supplier_action="confirm_custom_order",
    )]

    with pytest.raises(ValueError, match="buyer requirement about order type"):
        assemble(request, {}, payload)


def stock_finding(option_ids, *requirement_ids, action="confirm_custom_order"):
    return finding_selection(
        option_ids, topic="finish", requirement_ids=list(requirement_ids), supplier_action=action,
    )


def buyer_selection(requirement_id, question_id, quote, *, topic="customization"):
    return {
        "requirement_id": requirement_id, "topic": topic,
        "input_ref": f"answer:{question_id}", "quote": quote,
    }


def test_requirement_rejects_a_buyer_substring_that_reverses_full_intent(parsed_listing):
    request = build_case(parsed_listing, "I do not require screen printing.")
    payload = minimal_content(request, "finish")
    payload["buyer_requirements"] = [
        buyer_selection("finish", "issued-1", "require screen printing", topic="finish"),
    ]
    payload["findings"] = [stock_finding(request.selected_option_ids, "finish", action="confirm_specification")]

    with pytest.raises(ValueError, match="full verbatim quote"):
        assemble(request, {}, payload)

    payload["buyer_requirements"][0]["quote"] = "I do not require screen printing."
    rendered = assemble(request, {}, payload)
    assert rendered.buyer_requirements[0].quote == "I do not require screen printing."
    assert rendered.buyer_requirements[0].question_text == "What else should the review consider?"
    assert "with screen printing?" not in rendered.supplier_draft
    assert "without screen printing?" not in rendered.supplier_draft


@pytest.mark.parametrize(("buyer_text", "target", "wrong"), [
    ("Screen printing is required.", "with screen printing?", "without screen printing?"),
    ("Screen printing is unacceptable.", "without screen printing?", "with screen printing?"),
    ("Screen printing might work, but I am not sure.", None, "without screen printing?"),
])
def test_supplier_checks_required_or_avoided_finish_from_the_complete_answer(parsed_listing, buyer_text, target, wrong):
    request = build_case(parsed_listing, f"  {buyer_text} ")
    payload = minimal_content(request, "finish")
    payload["buyer_requirements"] = [buyer_selection("finish", "issued-1", buyer_text, topic="finish")]
    payload["findings"] = [stock_finding(request.selected_option_ids, "finish", action="confirm_specification")]

    rendered = assemble(request, {}, payload)

    assert rendered.buyer_requirements[0].text == buyer_text
    if target is not None:
        assert target in rendered.supplier_draft
    else:
        assert "with screen printing?" not in rendered.supplier_draft
    assert wrong not in rendered.supplier_draft
    assert buyer_text not in rendered.supplier_draft


@pytest.mark.parametrize(("buyer_text", "custom", "stock"), [
    ("Ready-made only", False, True),
    ("Custom orders are acceptable", True, False),
    ("Ready-made only for A, but I would consider a custom order for B.", False, False),
    ("Custom orders might be acceptable. Private note: 40%.", False, False),
    ("Custom orders are acceptable?", False, False),
    ("I don't want custom orders.", False, False),
])
def test_provider_action_cannot_override_the_direction_of_the_complete_current_answer(parsed_listing, buyer_text, custom, stock):
    request = build_case(parsed_listing, buyer_text)
    payload = minimal_content(request, "finish")
    payload["buyer_requirements"] = [buyer_selection("order", "issued-1", buyer_text)]
    payload["findings"] = [stock_finding(request.selected_option_ids, "order")]

    rendered = assemble(request, {}, payload)

    assert ("minimum order quantity" in rendered.supplier_draft) == custom
    assert ("Please answer for ready-made stock." in rendered.supplier_draft) == stock
    assert "40%" not in rendered.supplier_draft


def test_later_answer_overrides_order_type_only_for_the_issued_option_scope(example):
    request, images = example
    data = request.model_dump()
    data["answers"] = [
        {"question_id": "original-order", "question_text": "Would you consider a custom order?", "answer_text": "Ready-made only"},
        {
            "question_id": "a-order", "question_text": "Would you consider custom production for Option A?",
            "answer_text": "Custom orders are acceptable",
        },
    ]
    data["answer_option_ids"] = {"original-order": ["v0", "v1", "v6"], "a-order": ["v0"]}
    request = ReviewInput.model_validate(data)
    payload = minimal_content(request, "finish")
    payload["buyer_requirements"] = [
        buyer_selection("old-order", "original-order", "Ready-made only"),
        buyer_selection("new-order", "a-order", "Custom orders are acceptable"),
    ]
    payload["findings"] = [
        {**stock_finding(["v0"], "new-order"), "finding_id": "custom-a"},
        {**stock_finding(["v1", "v6"], "old-order"), "finding_id": "stock-bc"},
    ]

    rendered = assemble(request, images, payload)
    assert [row.option_ids for row in rendered.buyer_requirements] == [["v1", "v6"], ["v0"]]
    assert "minimum order quantity" in rendered.findings[0].supplier_question
    assert "Please answer for ready-made stock." in rendered.findings[1].supplier_question

    payload["findings"][0]["requirement_ids"] = ["old-order"]
    with pytest.raises(ValueError, match="current option scope"):
        assemble(request, images, payload)


def test_a_later_ambiguous_answer_blocks_a_stale_direction_even_if_provider_omits_it(parsed_listing):
    request = build_case(parsed_listing, "Custom orders are acceptable")
    data = request.model_dump()
    data["answers"].append({
        "question_id": "follow-up",
        "question_text": "Has your position on custom orders changed?",
        "answer_text": "I'm undecided and will have to check.",
    })
    data["answer_option_ids"]["follow-up"] = request.selected_option_ids
    request = ReviewInput.model_validate(data)
    payload = minimal_content(request, "finish")
    payload["buyer_requirements"] = [buyer_selection("old-order", "issued-1", "Custom orders are acceptable")]
    payload["findings"] = [stock_finding(request.selected_option_ids, "old-order")]

    with pytest.raises(ValueError, match="current buyer input"):
        assemble(request, {}, payload)

    payload["buyer_requirements"] = [buyer_selection("current", "follow-up", "I'm undecided and will have to check.")]
    payload["findings"][0]["requirement_ids"] = ["current"]
    payload["findings"][0]["supplier_action"] = "confirm_specification"
    rendered = assemble(request, {}, payload)
    assert "minimum order quantity" not in rendered.supplier_draft


def test_a_later_sample_answer_does_not_replace_a_custom_order_answer(parsed_listing):
    request = build_case(parsed_listing, "Custom orders are acceptable")
    data = request.model_dump()
    data["answers"].append({
        "question_id": "sample",
        "question_text": "Would you require a sample before placing a custom order?",
        "answer_text": "A sample is required.",
    })
    data["answer_option_ids"]["sample"] = request.selected_option_ids
    request = ReviewInput.model_validate(data)
    payload = minimal_content(request, "finish")
    payload["buyer_requirements"] = [
        buyer_selection("order", "issued-1", "Custom orders are acceptable"),
        buyer_selection("sample", "sample", "A sample is required.", topic="sample"),
    ]
    payload["findings"] = [stock_finding(request.selected_option_ids, "order")]

    rendered = assemble(request, {}, payload)

    assert "minimum order quantity" in rendered.supplier_draft
    assert {requirement.requirement_id for requirement in rendered.buyer_requirements} == {"order", "sample"}


def test_exact_capacity_requirement_yields_controlled_seller_question(parsed_listing):
    request = build_case(parsed_listing, "I require 720.0 mL.")
    payload = minimal_content(request, "capacity")
    payload["buyer_requirements"] = [buyer_selection("exact", "issued-1", "I require 720.0 mL.", topic="capacity")]
    payload["findings"] = [finding_selection(request.selected_option_ids, requirement_ids=["exact"])]

    rendered = assemble(request, {}, payload)
    assert "Can each named option meet 720 ml" in rendered.supplier_draft
    assert "I require 720.0 mL." not in rendered.supplier_draft
