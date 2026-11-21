"""Run the local app with scripted translation, interview, and review for testing."""

import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn

from backend.case_workflow import ReviewImages
from backend.contracts import ListingSnapshot, QuestionInput, QuestionResult, ReviewInput
from backend.main import create_app
from backend.report import assemble_review
from backend.report_models import (
    BuyerRequirement, Citation, Comparison, ComparisonValue, Coverage, Finding, ReviewContent, ReviewReady, Unknown,
)


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
        return QuestionResult.model_validate({
            "reason": "Identify buyer requirements that would affect which options to review.",
            "questions": [
                {**scope, "prompt": ORDER_PROMPT,
                 "why_ask": "This determines whether the review should include custom orders.",
                 "choices": [READY_MADE, CUSTOM_ORDER]},
                {**scope, "prompt": "What purchase deadline should the review consider?",
                 "why_ask": "Your deadline affects which timing details to confirm.", "choices": []},
            ],
        })
    if inputs.round_number == 2 and inputs.answers[0].answer_text == CUSTOM_ORDER:
        return QuestionResult.model_validate({
            "reason": "Clarify the buyer's custom-order requirements.",
            "questions": [
                {**scope, "prompt": "Would you need to approve a sample before production?",
                 "why_ask": "This determines whether to request a sample approval step.",
                 "choices": ["A sample is required", "No sample is required"]},
            ],
        })
    return QuestionResult(reason="The scripted interview has no further questions.", questions=[])


async def offline_review(inputs: ReviewInput, images: ReviewImages) -> ReviewReady:
    snapshot = inputs.snapshot
    selected = inputs.selected_option_ids
    names = snapshot.option_name_sources()
    options = {option.option_id: option for option in snapshot.options}
    title_source = next(source for source in snapshot.sources if source.label == "Product title")
    first = options[selected[0]]
    first_name = names[first.option_id]
    first_citations = [Citation(source_id=first_name.source_id, quote=first.name_original, image_observation=None)]
    unknowns = []
    if first.image_source_id in images:
        first_citations.append(Citation(
            source_id=first.image_source_id, quote=None,
            image_observation="A thumbnail is linked to this exact option.",
        ))
    elif first.image_source_id is not None:
        first_image = next(source for source in snapshot.sources if source.source_id == first.image_source_id)
        unknowns.append(Unknown(
            text="The exact option image was not included in the review.",
            reason="not_reviewed" if first_image.availability == "available" else "unreadable",
            source_ids=[first.image_source_id],
        ))

    order = next((answer for answer in inputs.answers if answer.question_text == ORDER_PROMPT), None)
    requirements = []
    supplier_question = "Please confirm the decoration process for each selected option."
    impact = "The seller's answer will help compare the selected options before ordering."
    if order is not None and order.answer_text in (CUSTOM_ORDER, READY_MADE):
        requirements.append(BuyerRequirement(
            requirement_id="order-preference", text=order.answer_text, input_ref=f"answer:{order.question_id}",
            quote=order.answer_text,
        ))
        if order.answer_text == CUSTOM_ORDER:
            impact = "You can consider a custom order if the ready-made options do not meet your needs."
            supplier_question += " If a different finish requires a custom order, please give the lead time and minimum order quantity."
        else:
            impact = "The ready-made finish matters because you are considering existing stock only."
            supplier_question += " Please answer for ready-made stock."

    content = ReviewContent(
        buyer_requirements=requirements, open_preferences=[],
        comparisons=[Comparison(label="Listing option name", values=[
            ComparisonValue(option_id=option_id, summary=options[option_id].name_en or options[option_id].name_original,
                            source_ids=[names[option_id].source_id]) for option_id in selected
        ])],
        findings=[
            Finding(
                finding_id="decoration", title="Confirm the decoration process", option_ids=selected,
                kind="clarification_needed", action_type="ask_supplier",
                requirement_ids=[requirement.requirement_id for requirement in requirements],
                citations=[Citation(source_id=title_source.source_id, quote=snapshot.title, image_observation=None)],
                interpretation="The listing title describes the product. The process for each selected option still needs confirmation.",
                impact=impact, priority_reason="The decoration process affects which options meet the purchase needs.",
                unknowns=[Unknown(text="The exact decoration process is still open.", reason="not_found",
                                  source_ids=[title_source.source_id])],
                requested_evidence="Ask the seller to name the decoration process for each selected option.",
                evidence_limits="A written answer is the seller's description of the product.", supplier_question=supplier_question,
            ),
            Finding(
                finding_id="option-sample", title="Check the exact option sample", option_ids=[first.option_id],
                kind="evidence_gap", action_type="request_evidence", requirement_ids=[], citations=first_citations,
                interpretation="The listing names this specific option. A sample would give more detail about its finish.",
                impact="A sample gives you a chance to inspect the finish before placing the order.",
                priority_reason="First confirm which stock or custom option would be supplied.", unknowns=unknowns,
                requested_evidence="Ask for a close-up or sample of this exact option.",
                evidence_limits="A supplier sample covers the piece you receive for inspection.",
                supplier_question="Please send a close-up photo or a sample of option A.",
            ),
        ],
        coverage=[
            Coverage(group="product_match", status="limited", finding_ids=["decoration"],
                     note="The finish still needs confirmation for each selected option."),
            Coverage(group="intended_use", status="not_assessed", finding_ids=[],
                     note="This scripted review does not assess the intended use."),
            Coverage(group="supporting_evidence", status="limited", finding_ids=["option-sample"],
                     note="A close-up or sample would give more detail about the exact option."),
        ],
        limitations=["This report uses scripted analysis for offline testing."],
    )
    sent_source_ids = {title_source.source_id, *(names[option_id].source_id for option_id in selected), *images}
    return ReviewReady(report=assemble_review(inputs, content, sent_source_ids=sent_source_ids))


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
