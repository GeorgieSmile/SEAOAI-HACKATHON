import pytest
from pydantic import ValidationError

from backend.contracts import ReviewInput
from backend.tests.test_reports import (
    assemble, example, finding_selection, image_claim, minimal_content, populated_content, text_claim,
)


@pytest.fixture
def joint_finding(example):
    request, images = example
    request = request.model_copy(update={"selected_option_ids": ["v6", "v0"]})
    content = minimal_content(request, "finish")
    content["claims"] = [
        text_claim("letter-name", "name-v6", "字母", "v6"),
        text_claim("graphic-name", "name-v0", "贴画", "v0"),
    ]
    content["comparisons"][0]["values"][0]["claim_ids"] = ["letter-name"]
    content["comparisons"][0]["values"][1]["claim_ids"] = ["graphic-name"]
    content["findings"] = [finding_selection(
        ["v6", "v0"], finding_id="artwork", topic="finish", claim_ids=["letter-name", "graphic-name"],
    )]
    return request, images, content


def test_comparison_and_joint_finding_keep_each_excerpt_bound_to_its_exact_sku(joint_finding):
    request, images, content = joint_finding
    rendered = assemble(request, images, content)

    first, second = rendered.comparisons[0].values
    assert (first.option_id, first.claim_ids, first.source_ids) == ("v6", ["letter-name"], ["name-v6"])
    assert first.summary == "SKU wording excerpt: “字母”"
    assert (second.option_id, second.claim_ids, second.source_ids) == ("v0", ["graphic-name"], ["name-v0"])
    assert second.summary == "SKU wording excerpt: “贴画”"
    assert rendered.findings[0].claim_ids == ["letter-name", "graphic-name"]
    assert [(cite.source_id, cite.quote) for cite in rendered.findings[0].citations] == [
        ("name-v6", "字母"), ("name-v0", "贴画"),
    ]
    assert "[Options A, B] Please confirm the decoration process and finish" in rendered.supplier_draft


@pytest.mark.parametrize("field", ["quote", "source_id", "option_ids"])
def test_sku_wording_cannot_be_swapped_between_options(joint_finding, field):
    request, images, content = joint_finding
    content["claims"][0][field] = {"quote": "贴画", "source_id": "name-v0", "option_ids": ["v0"]}[field]
    with pytest.raises(ValueError, match="exact quote|owning SKU"):
        assemble(request, images, content)


def test_comparison_cell_rejects_other_claim_even_when_both_are_valid(joint_finding):
    request, images, content = joint_finding
    content["comparisons"][0]["values"][0]["claim_ids"] = ["graphic-name"]
    with pytest.raises(ValueError, match="exact SKU"):
        assemble(request, images, content)


def test_finding_scope_cannot_borrow_other_sku_writing(joint_finding):
    request, images, content = joint_finding
    content["findings"][0]["option_ids"] = ["v6"]
    with pytest.raises(ValueError, match="must cover the SKU"):
        assemble(request, images, content)


@pytest.mark.parametrize(("path", "field"), [
    (("findings", 0), "title"),
    (("findings", 0), "interpretation"),
    (("findings", 0), "impact"),
    (("findings", 0), "priority_reason"),
    (("findings", 0), "supplier_question"),
    (("findings", 0), "requested_evidence"),
    (("findings", 0), "evidence_limits"),
    (("comparisons", 0), "label"),
    (("comparisons", 0, "values", 0), "summary"),
    (("coverage", 0), "note"),
    ((), "limitations"),
    (("claims", 0), "display_text"),
])
def test_provider_has_no_free_prose_field_for_sku_attribution(joint_finding, path, field):
    request, images, content = joint_finding
    target = content
    for step in path:
        target = target[step]
    target[field] = "Only Option A's name includes 贴画."
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        assemble(request, images, content)


def test_global_customization_can_be_cited_as_shared_but_cannot_fill_a_sku_cell(joint_finding):
    request, images, content = joint_finding
    content["claims"].append(text_claim("menu", "customization-menu", "丝印"))
    content["findings"][0]["claim_ids"].append("menu")
    rendered = assemble(request, images, content)
    assert rendered.claims[-1].option_ids == []
    assert rendered.claims[-1].display_text == "Shared listing text excerpt: “丝印”"
    assert "screen printing" not in rendered.supplier_draft

    content["comparisons"][0]["values"][0]["claim_ids"].append("menu")
    with pytest.raises(ValueError, match="exact SKU"):
        assemble(request, images, content)


def test_scoped_shared_selector_cannot_apply_to_other_selected_skus(example):
    request, images = example
    data = request.model_dump()
    selector = next(source for source in data["snapshot"]["sources"] if source["source_id"] == "capacity-selector")
    selector["option_ids"] = ["v0", "v1"]
    request = ReviewInput.model_validate(data)
    content = minimal_content(request)
    content["claims"] = [text_claim("selector", "capacity-selector", "720ml")]
    content["findings"] = [finding_selection(["v0", "v1"], claim_ids=["selector"])]
    rendered = assemble(request, images, content)
    assert rendered.findings[0].option_ids == ["v0", "v1"]
    assert rendered.claims[0].option_ids == []

    content["comparisons"][0]["shared_claim_ids"] = ["selector"]
    with pytest.raises(ValueError, match="entire comparison"):
        assemble(request, images, content)
    content["comparisons"][0]["shared_claim_ids"] = []
    content["findings"][0]["option_ids"].append("v6")
    with pytest.raises(ValueError, match="only options associated"):
        assemble(request, images, content)


@pytest.mark.parametrize("kind", ["image_measurement", "image_detail"])
@pytest.mark.parametrize("wrong", ["other_mapping", "text", "outside_selection", "not_sent"])
def test_visual_claim_requires_the_actual_attached_thumbnail_for_its_exact_sku(joint_finding, kind, wrong):
    request, images, _ = joint_finding
    content = minimal_content(request)
    claim = image_claim("label", "v6", "image-v6")
    if kind == "image_detail":
        del claim["unit"], claim["value"]
        claim.update(kind=kind, detail="letters_visible")
    content["claims"] = [claim]
    if wrong == "other_mapping":
        claim["source_id"] = "image-v0"
    elif wrong == "text":
        claim["source_id"] = "name-v6"
    elif wrong == "outside_selection":
        claim.update(source_id="image-v1", option_ids=["v1"])
    else:
        del images["image-v6"]
    with pytest.raises(ValueError, match="mapped exclusively|actually sent"):
        assemble(request, images, content)


def test_thumbnail_detail_is_rendered_as_visibility_and_cannot_imply_how_the_finish_was_made(joint_finding):
    request, images, content = joint_finding
    content["claims"].append({
        "claim_id": "detail", "kind": "image_detail", "option_ids": ["v6"],
        "source_id": "image-v6", "detail": "letters_visible",
    })
    content["comparisons"][0]["values"][0]["claim_ids"].append("detail")
    content["findings"][0]["claim_ids"].append("detail")
    rendered = assemble(request, images, content)
    assert rendered.claims[-1].display_text == "Thumbnail detail: Letter forms are visible."
    citation = rendered.findings[0].citations[-1]
    assert citation.source_id == "image-v6"
    assert citation.quote is None
    assert citation.image_observation == "Thumbnail detail: Letter forms are visible."
    assert "screen" not in rendered.supplier_draft

    content["claims"][-1]["detail"] = "screen_printed"
    with pytest.raises(ValidationError):
        assemble(request, images, content)


def test_capacity_discrepancy_preserves_shared_value_and_exact_sku_image_reading(example):
    request, images = example
    content = populated_content(request)
    rendered = assemble(request, images, content)
    comparison = rendered.comparisons[0]
    assert comparison.label == "Capacity"
    assert comparison.shared_claim_ids == ["selector"]
    assert rendered.claims[0].display_text == "Shared selector (source translation): 720 ml"
    assert [cell.summary for cell in comparison.values] == [
        "Thumbnail label: 750 ml", "No SKU-specific evidence cited for this topic.", "No SKU-specific evidence cited for this topic.",
    ]
    assert "Option A: shared selector 720 ml and thumbnail label 750 ml." in rendered.supplier_draft
    assert "usable or brim-full" in rendered.supplier_draft


@pytest.mark.parametrize(("reading", "unit"), [(720, "ml"), (0.72, "L"), (25, "fl oz")])
def test_matching_or_incomparable_capacity_values_are_not_a_conflict(example, reading, unit):
    request, images = example
    content = populated_content(request)
    content["claims"][1].update(value=reading, unit=unit)
    with pytest.raises(ValueError, match="comparable, different readings"):
        assemble(request, images, content)


@pytest.mark.parametrize("source_text", ["容量: 720ml or 750ml", "Not 720ml", "Model: 720ml"])
def test_unqualified_text_substring_cannot_be_used_to_create_a_conflict(example, source_text):
    request, images = example
    data = request.model_dump()
    selector = next(source for source in data["snapshot"]["sources"] if source["source_id"] == "capacity-selector")
    selector.update(original_text=source_text, text_en=None)
    request = ReviewInput.model_validate(data)
    content = populated_content(request)

    with pytest.raises(ValueError, match="comparable, different readings"):
        assemble(request, images, content)


def test_visual_artwork_and_sku_wording_cannot_be_promoted_to_a_conflict(joint_finding):
    request, images, content = joint_finding
    content["claims"].append({
        "claim_id": "illustration", "kind": "image_detail", "option_ids": ["v6"],
        "source_id": "image-v6", "detail": "illustration_visible",
    })
    content["findings"][0].update(
        kind="listing_conflict", supplier_action="reconcile_sources", option_ids=["v6"],
        claim_ids=["letter-name", "illustration"],
    )
    with pytest.raises(ValueError, match="comparable, different readings"):
        assemble(request, images, content)


@pytest.mark.parametrize("placement", ["shared_comparison", "sku_cell", "finding"])
@pytest.mark.parametrize("quote", ["720ml", "720"])
def test_standalone_text_capacity_cannot_support_a_weight_topic(example, placement, quote):
    request, images = example
    content = minimal_content(request, "weight")
    if placement == "sku_cell":
        data = request.model_dump()
        data["snapshot"]["sources"].append({
            "source_id": "sku-capacity", "kind": "listing_text", "label": "Product attribute", "option_ids": ["v0"],
            "availability": "available", "original_text": "容量: 720ml", "text_en": None, "image_file": None, "url": None,
        })
        request = ReviewInput.model_validate(data)
        content["claims"] = [text_claim("wrong-topic", "sku-capacity", quote, "v0")]
        content["comparisons"][0]["values"][0]["claim_ids"] = ["wrong-topic"]
    else:
        content["claims"] = [text_claim("wrong-topic", "capacity-selector", quote)]
        if placement == "shared_comparison":
            content["comparisons"][0]["shared_claim_ids"] = ["wrong-topic"]
        else:
            content["findings"] = [finding_selection(["v0"], topic="weight", claim_ids=["wrong-topic"])]

    with pytest.raises(ValueError, match="standalone text measurement with its matching topic"):
        assemble(request, images, content)
