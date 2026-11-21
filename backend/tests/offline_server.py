"""Run the local app with scripted translation, interview, and review for testing."""

import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn

from backend.case_workflow import ReviewImages
from backend.contracts import ListingSnapshot, QuestionInput, QuestionResult, ReviewInput
from backend.main import create_app
from backend.report import assemble_review, review_sent_evidence
from backend.report_models import CanonicalReviewContent, ReviewContent, ReviewReady


ORDER_PROMPT = "Would you consider a custom order?"
CUSTOM_ORDER = "Custom orders are acceptable"
READY_MADE = "Ready-made only"
SKU_TRANSLATIONS = {
    "暮色316-贴画粉蓝": "Twilight 316 · applied-picture design · pink/blue",
    "暮色316-贴画黑白": "Twilight 316 · applied-picture design · black/white",
    "暮色316-字母粉蓝": "Twilight 316 · letter design · pink/blue",
}


async def offline_translation(snapshot: ListingSnapshot) -> ListingSnapshot:
    names = snapshot.option_name_sources()
    translations = {source.source_id: SKU_TRANSLATIONS.get(source.original_text) for source in names.values()}
    return snapshot.model_copy(update={
        "sources": [source.model_copy(update={"text_en": translations[source.source_id]})
                    if source.source_id in translations else source for source in snapshot.sources],
        "options": [option.model_copy(update={"name_en": translations[names[option.option_id].source_id]})
                    for option in snapshot.options],
        "limitations": [*snapshot.limitations, "Only scripted SKU translations are available in this offline test."],
    })


async def offline_questions(inputs: QuestionInput) -> QuestionResult:
    scope = {"option_ids": inputs.selected_option_ids, "source_ids": []}
    if inputs.round_number == 1:
        context = inputs.buyer_context.strip()
        return QuestionResult.model_validate({
            "reason": "Identify buyer requirements that would affect which options to review.",
            "context_requirements": [{
                "statement": context, "quote": context, "option_ids": inputs.selected_option_ids,
                "topic": "other", "status": "unresolved",
            }] if context else [],
            "questions": [
                {**scope, "prompt": ORDER_PROMPT,
                 "why_ask": "This determines whether the review should include custom orders.",
                 "topic": "customization", "buyer_facet": "order_type", "requirement_ids": [],
                 "choices": [READY_MADE, CUSTOM_ORDER], "choice_effects": [
                     {"choice": label, "action": "add", "requirement_id": None, "statement": label}
                     for label in (READY_MADE, CUSTOM_ORDER)
                 ]},
                {**scope, "prompt": "What purchase deadline should the review consider?",
                 "why_ask": "Your deadline affects which timing details to confirm.",
                 "topic": "deadline", "buyer_facet": "delivery_deadline", "requirement_ids": [],
                 "choices": [], "choice_effects": []},
            ],
        })
    if inputs.round_number == 2 and inputs.answers[0].answer_text == CUSTOM_ORDER:
        return QuestionResult.model_validate({
            "reason": "Clarify the buyer's custom-order requirements.",
            "questions": [
                {**scope, "prompt": "Would you need to approve a sample before production?",
                 "why_ask": "This determines whether to request a sample approval step.",
                 "topic": "sample", "buyer_facet": "other", "requirement_ids": [],
                 "choices": ["A sample is required", "No sample is required"], "choice_effects": [
                     {"choice": "A sample is required", "action": "add", "requirement_id": None,
                      "statement": "A sample is required"},
                     {"choice": "No sample is required", "action": "waive", "requirement_id": None,
                      "statement": None},
                 ]},
            ],
        })
    return QuestionResult(reason="The scripted interview has no further questions.", questions=[])


def scripted_report(inputs: ReviewInput, attached_image_ids: set[str]) -> dict:
    """Scripted report decisions for the local interview. Never used by the production generator."""
    selected = inputs.selected_option_ids
    names = inputs.snapshot.option_name_sources()
    options = {option.option_id: option for option in inputs.snapshot.options}
    claims = []
    values = []
    shared_claim_ids = []
    for source in inputs.snapshot.sources:
        if (source.label == "Shared selector value" and source.original_text == "720ml"
                and set(selected).issubset(source.option_ids)):
            claim_id = "selector-capacity"
            claims.append({"claim_id": claim_id, "option_ids": [], "source_id": source.source_id,
                           "kind": "text_excerpt", "quote": source.original_text})
            shared_claim_ids.append(claim_id)
            break
    for index, option_id in enumerate(selected):
        name = names[option_id]
        claim_ids = [f"name-{index}"]
        claims.append({
            "claim_id": claim_ids[0], "option_ids": [option_id], "source_id": name.source_id,
            "kind": "text_excerpt", "quote": name.original_text,
        })
        option = options[option_id]
        # The three original demo thumbnails have been inspected for this test fixture.
        if option.name_original in SKU_TRANSLATIONS and option.image_source_id in attached_image_ids:
            claim_ids.append(f"image-{index}")
            claims.append({
                "claim_id": claim_ids[-1], "option_ids": [option_id], "source_id": option.image_source_id,
                "kind": "image_measurement", "value": 750, "unit": "ml",
            })
        values.append({"option_id": option_id, "claim_ids": claim_ids})

    order = next((answer for answer in inputs.answers if answer.question_text == ORDER_PROMPT), None)
    requirements = []
    action = "confirm_specification"
    if order and order.answer_text in (CUSTOM_ORDER, READY_MADE):
        requirements.append({
            "requirement_id": "order-preference", "topic": "customization",
            "input_ref": f"answer:{order.question_id}", "quote": order.answer_text,
        })
        action = "confirm_custom_order" if order.answer_text == CUSTOM_ORDER else "confirm_ready_made"
    findings = [
        {
            "finding_id": "decoration", "topic": "customization", "option_ids": selected,
            "kind": "clarification_needed", "action_type": "ask_supplier",
            "requirement_ids": [item["requirement_id"] for item in requirements],
            "claim_ids": [value["claim_ids"][0] for value in values],
            "unknowns": [], "supplier_action": action,
        },
        {
            "finding_id": "option-sample", "topic": "capacity", "option_ids": [selected[0]],
            "kind": "evidence_gap", "action_type": "request_evidence", "requirement_ids": [],
            "claim_ids": values[0]["claim_ids"], "unknowns": [], "supplier_action": "request_sample",
        },
    ]
    image_id = options[selected[0]].image_source_id
    if image_id and image_id not in attached_image_ids:
        source = next(s for s in inputs.snapshot.sources if s.source_id == image_id)
        reason = "not_reviewed" if source.availability == "available" else "unreadable"
        findings[1]["unknowns"] = [{"reason": reason, "source_ids": [image_id]}]
    if inputs.requirements is not None:
        requirements = []
        findings[0].update(requirement_ids=[], supplier_action="confirm_specification")
        for row in inputs.requirements:
            if row.status != "active":
                continue
            if row.topic == "customization" and row.option_ids == selected:
                findings[0]["requirement_ids"].append(row.requirement_id)
                continue
            findings.append({
                "finding_id": f"confirm-{row.requirement_id}", "option_ids": row.option_ids,
                "topic": row.topic, "kind": "requirement_unconfirmed", "action_type": "ask_supplier",
                "requirement_ids": [row.requirement_id], "claim_ids": [], "unknowns": [],
                "supplier_action": "confirm_specification",
            })
    content = {
        "claims": claims,
        "comparisons": [{"topic": "capacity", "shared_claim_ids": shared_claim_ids, "values": values}],
        "coverage": [
            {"group": "product_match", "status": "limited", "finding_ids": ["decoration"]},
            {"group": "intended_use", "status": "not_assessed", "finding_ids": []},
            {"group": "supporting_evidence", "status": "limited", "finding_ids": ["option-sample"]},
        ],
    }
    if inputs.requirements is None:
        return {**content, "contract_version": 2, "buyer_requirements": requirements, "open_preferences": [], "findings": findings}
    return {
        **content, "contract_version": 3,
        "findings": [{**finding, "decision_id": None} for finding in findings],
    }


async def offline_review(inputs: ReviewInput, images: ReviewImages) -> ReviewReady:
    evidence = review_sent_evidence(inputs, images)
    schema = ReviewContent if inputs.requirements is None else CanonicalReviewContent
    content = schema.model_validate(scripted_report(inputs, set(evidence.attached_image_ids)))
    return ReviewReady(report=assemble_review(inputs, content, sent_evidence=evidence),
                       content=content, sent_evidence=evidence)


def run(database: Path, port: int) -> None:
    app = create_app(database, translator=offline_translation, question_generator=offline_questions,
                     review_generator=offline_review)
    uvicorn.run(app, host="127.0.0.1", port=port)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--database", type=Path, help="Reuse this test database across restarts.")
    args = parser.parse_args()
    if args.database:
        run(args.database, args.port)
    else:
        with TemporaryDirectory(prefix="purchase-review-") as directory:
            run(Path(directory) / "case.sqlite3", args.port)
