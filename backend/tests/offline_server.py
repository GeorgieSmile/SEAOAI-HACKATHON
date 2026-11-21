"""Run the local app with offline translation and a scripted buyer interview for testing."""

import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn

from backend.contracts import ListingSnapshot, QuestionInput, QuestionResult
from backend.main import create_app


async def offline_translation(snapshot: ListingSnapshot) -> ListingSnapshot:
    return snapshot.model_copy(update={
        "limitations": [*snapshot.limitations, "English translation is disabled in this offline test."],
    })


async def offline_questions(inputs: QuestionInput) -> QuestionResult:
    scope = {"option_ids": inputs.selected_option_ids, "source_ids": []}
    if inputs.round_number == 1:
        return QuestionResult.model_validate({
            "reason": "Identify buyer requirements that would affect which options to review.",
            "questions": [
                {**scope, "prompt": "Would you consider a custom order?",
                 "why_ask": "This determines whether the review should include custom orders.",
                 "choices": ["Ready-made only", "Custom orders are acceptable"]},
                {**scope, "prompt": "What purchase deadline should the review consider?",
                 "why_ask": "Your deadline affects which timing details to confirm.", "choices": []},
            ],
        })
    if inputs.round_number == 2 and inputs.answers[0].answer_text == "Custom orders are acceptable":
        return QuestionResult.model_validate({
            "reason": "Clarify the buyer's custom-order requirements.",
            "questions": [
                {**scope, "prompt": "Would you need to approve a sample before production?",
                 "why_ask": "This determines whether to request a sample approval step.",
                 "choices": ["A sample is required", "No sample is required"]},
            ],
        })
    return QuestionResult(reason="The scripted interview has no further questions.", questions=[])


def run(database: Path, port: int) -> None:
    app = create_app(database, translator=offline_translation, question_generator=offline_questions)
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
