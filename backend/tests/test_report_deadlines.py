import pytest

from backend.contracts import ReviewInput
from backend.tests.test_reports import assemble, example, finding_selection, minimal_content


def _question(question_id, prompt, answer, facet=None):
    return {"question_id": question_id, "question_text": prompt, "answer_text": answer, "buyer_facet": facet}


def _requirement(answer):
    return {
        "requirement_id": answer["question_id"], "input_ref": f"answer:{answer['question_id']}",
        "topic": "deadline", "quote": answer["answer_text"],
    }


@pytest.mark.parametrize("facet", ["delivery_deadline", "other", None])
def test_report_rejects_superseded_delivery_deadline_for_the_same_options(example, facet):
    request, images = example
    first_prompt, later_prompt = "What is your required delivery deadline?", "What is your revised delivery deadline?"
    if facet == "delivery_deadline":
        first_prompt, later_prompt = "How late may the order arrive?", "How late can the order arrive now?"
    first = _question("first", first_prompt, "Delivery is required by 1 December.", facet)
    later = _question("later", later_prompt, "The deadline has moved to 15 December. Private note: Cedar-914.", facet)
    selected = request.selected_option_ids
    request = ReviewInput.model_validate({
        **request.model_dump(), "answers": [first, later], "answer_option_ids": {"first": selected, "later": selected},
    })
    payload = minimal_content(request, "deadline")
    payload["buyer_requirements"] = [_requirement(first), _requirement(later)]
    payload["findings"] = [finding_selection(selected, topic="deadline", requirement_ids=["later"])]

    with pytest.raises(ValueError, match="current buyer input"):
        assemble(request, images, payload)

    payload["buyer_requirements"] = [_requirement(later)]
    report = assemble(request, images, payload)

    requirement, = report.buyer_requirements
    assert requirement.quote == later["answer_text"]
    assert requirement.question_text == later_prompt
    assert requirement.option_ids == selected
    assert "the expected ship date and delivery window" in report.supplier_draft
    assert "Cedar-914" not in report.supplier_draft


def test_report_preserves_independent_timing_answers_and_other_options_deadlines(example):
    request, images = example
    first = _question("first", "When do you need delivery?", "1 December for the order.", "other")
    revised = _question("revised", "What delivery deadline should we use for Option A?", "15 December for Option A.")
    sample = _question(
        "sample", "What is your required delivery deadline for the sample?", "The sample must arrive by 10 November.", "other",
    )
    shipment = _question("shipment", "What is your latest acceptable ship date?", "The order must ship by 20 November.", "other")
    selected = request.selected_option_ids
    request = ReviewInput.model_validate({
        **request.model_dump(), "answers": [first, sample, shipment, revised],
        "answer_option_ids": {"first": selected, "revised": selected[:1], "sample": selected, "shipment": selected},
    })
    payload = minimal_content(request, "deadline")
    payload["buyer_requirements"] = [_requirement(answer) for answer in (first, revised, sample, shipment)]
    payload["findings"] = [finding_selection(
        selected[1:], topic="deadline", finding_id="older", requirement_ids=["first"],
    ), finding_selection(
        selected[:1], topic="deadline", finding_id="newer", requirement_ids=["revised"],
    )]

    report = assemble(request, images, payload)

    assert {row.requirement_id: row.option_ids for row in report.buyer_requirements} == {
        "first": selected[1:], "revised": selected[:1], "sample": selected, "shipment": selected,
    }
    assert "[Option A]" in report.supplier_draft
    assert "[Options B, C]" in report.supplier_draft

    payload["findings"][0]["option_ids"] = selected
    with pytest.raises(ValueError, match="current option scope"):
        assemble(request, images, payload)


def test_skipped_delivery_deadline_question_keeps_the_prior_answer(example):
    request, images = example
    first = _question("first", "When do you need delivery?", "1 December.", "delivery_deadline")
    skipped = _question("skipped", "What is your revised delivery deadline?", None, "delivery_deadline")
    selected = request.selected_option_ids
    request = ReviewInput.model_validate({
        **request.model_dump(), "answers": [first, skipped], "answer_option_ids": {"first": selected, "skipped": selected},
    })
    payload = minimal_content(request, "deadline")
    payload["buyer_requirements"] = [_requirement(first)]

    report = assemble(request, images, payload)

    assert report.buyer_requirements[0].quote == "1 December."
