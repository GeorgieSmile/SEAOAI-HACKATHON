import pytest

from backend.contracts import ReviewInput
from backend.tests.test_reports import assemble, example, finding_selection, image_claim, minimal_content, text_claim


def test_unassessed_groups_explain_their_own_gaps_without_findings(example):
    request, images = example
    content = minimal_content(request)
    for row in content["coverage"]:
        row.update(status="not_assessed", finding_ids=[])
    report = assemble(request, images, content)
    notes = {row.group: row.note for row in report.coverage}

    assert report.findings == []
    assert "no cited listing evidence" in notes["product_match"]
    assert "No buyer requirement describing use or temperature" in notes["intended_use"]
    assert "0 SKU thumbnails" in notes["supporting_evidence"]


@pytest.mark.parametrize(("availability", "reason"), [
    ("missing", "is missing"), ("unreadable", "could not be opened"), ("available", "was not attached for review"),
])
def test_capacity_gap_stays_in_the_correct_cell_even_when_name_evidence_survives(example, availability, reason):
    request, images = example
    affected, healthy = request.selected_option_ids[:2]
    affected_img = f"image-{affected}"
    data = request.model_dump()
    source = next(s for s in data["snapshot"]["sources"] if s["source_id"] == affected_img)
    source["availability"] = availability
    if availability != "available":
        source["image_file"] = None
    request = ReviewInput.model_validate(data)
    images = {key: value for key, value in images.items() if key != affected_img}

    content = minimal_content(request)
    own_name = request.snapshot.option_name_sources()[affected]
    content["claims"] = [text_claim("name", own_name.source_id, own_name.original_text, affected),
                         image_claim("photo", healthy, f"image-{healthy}")]
    values = content["comparisons"][0]["values"]
    values[0]["claim_ids"], values[1]["claim_ids"] = ["name"], ["photo"]
    report = assemble(request, images, content)

    first, second = report.comparisons[0].values[:2]
    assert first.claim_ids == ["name"]
    assert first.limitations == [f"The saved thumbnail for Option A {reason}. Its capacity label could not be checked."]
    assert second.claim_ids == ["photo"] and second.limitations == []
    note = next(row.note for row in report.coverage if row.group == "supporting_evidence")
    assert "1 listing text source, 1 SKU thumbnail" in note and "attached for Option A" in note


@pytest.mark.parametrize("placement", ["comparison", "same_finding", "separate_finding"])
def test_same_thumbnail_and_topic_cannot_be_both_readable_and_unreadable(example, placement):
    request, images = example
    first, second = request.selected_option_ids[:2]
    source_id = f"image-{first}"
    content = minimal_content(request)
    content["claims"] = [image_claim("label", first, source_id)]
    finding = finding_selection(
        [first, second], unknowns=[{"reason": "unreadable", "source_ids": [source_id]}],
    )
    content["findings"] = [finding]
    if placement == "comparison":
        content["comparisons"][0]["values"][0]["claim_ids"] = ["label"]
    elif placement == "same_finding":
        finding["claim_ids"] = ["label"]
    else:
        content["findings"].append(finding_selection([first], finding_id="readable", claim_ids=["label"]))

    with pytest.raises(ValueError, match="same thumbnail and topic"):
        assemble(request, images, content)


def test_unreadable_capacity_keeps_other_thumbnail_topics_and_sources_available(example):
    request, images = example
    first, second = request.selected_option_ids[:2]
    first_source_id, second_source_id = f"image-{first}", f"image-{second}"
    content = minimal_content(request)
    content["claims"] = [
        text_claim("selector", "capacity-selector", "720ml"),
        image_claim("second-label", second, second_source_id),
        {"claim_id": "first-finish", "source_id": first_source_id, "option_ids": [first],
         "kind": "image_detail", "detail": "illustration_visible"},
    ]
    content["comparisons"][0]["shared_claim_ids"] = ["selector"]
    content["comparisons"][0]["values"][1]["claim_ids"] = ["second-label"]
    finish = minimal_content(request, "finish")["comparisons"][0]
    finish["values"][0]["claim_ids"] = ["first-finish"]
    content["comparisons"].append(finish)
    content["findings"] = [finding_selection(
        [first, second], claim_ids=["selector", "second-label"],
        unknowns=[{"reason": "unreadable", "source_ids": [first_source_id]}],
    )]

    report = assemble(request, images, content)

    capacity, finish = report.comparisons
    assert capacity.shared_claim_ids == ["selector"]
    assert capacity.values[0].limitations == ["The capacity label could not be read in the thumbnail for Option A."]
    assert capacity.values[1].summary == "Thumbnail label: 750 ml"
    assert capacity.values[1].limitations == []
    assert finish.values[0].summary == "Thumbnail detail: An illustration is visible."
    assert finish.values[0].limitations == []
    assert report.findings[0].unknowns[0].source_ids == [first_source_id]
