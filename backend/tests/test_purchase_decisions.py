"""Exercise the entire interview/report path with scripted providers and a real listing ZIP."""
from dataclasses import dataclass, replace
import json
import os
import re
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
import httpx2
from openai import AsyncOpenAI
import pytest

from backend.ai import generate_questions
from backend.contracts import QuestionResult
from backend.main import create_app
from backend.report import generate_review
from backend.tests.test_cases import initial_request, ready, reference, round_request
from backend.tests.test_imports import RAW, _upload, stub
from backend.tests.test_reports import canonical_content, finding_selection, image_claim, response, text_claim
from backend.tests.test_review_workflow import retry_request


@dataclass(frozen=True)
class Decision:
    prompt: str
    answer: str
    topic: str
    facet: str
    effect: str = "add"
    revisits: str | None = None


PRINT = Decision("How should the artwork be applied?", "Screen printing is required.", "finish", "decoration_method")
MATTE = Decision("Which surface texture do you need?", "A matte finish is required.", "finish", "surface_finish")
WAIVE_PRINT = Decision("Could the decoration be made another way?", "Any finish is acceptable.",
                       "finish", "decoration_method", "waive", PRINT.answer)
ANY_TEXTURE = Decision("What surface textures work?", "Any surface texture is acceptable.", "finish", "surface_finish", "waive")
NO_PRINT = replace(WAIVE_PRINT, prompt="Is screen printing required?", answer="Waive the screen-printing requirement.")
NO_SAMPLE = Decision("Do you need a pre-production sample?", "No sample is required.", "sample", "other", "waive")
LOGO = "The logo must be screen printed."
LASER = "The logo must be laser engraved."
NEEDS_LASER = "The logo needs to be laser engraved."
AVOID_LASER = "The logo must not be laser engraved."
OPTIONAL = replace(WAIVE_PRINT, prompt="Is screen printing still required for your logo?",
                   answer="Screen printing is optional.", revisits=LOGO)
DEC_1 = Decision("What is your required delivery deadline?", "The order must be delivered by 1 December.",
                 "deadline", "delivery_deadline")
DEC_15 = replace(DEC_1, prompt="What is your revised delivery deadline?", answer="The deadline has moved to 15 December.",
                 effect="replace", revisits=DEC_1.answer)
STOCK = Decision("What kind of order do you need?", "Ready-made stock only.", "customization", "order_type")
KEEP = replace(STOCK, prompt="Has your position on custom orders changed?", answer="Keep ready-made stock only.",
               effect="keep", revisits=STOCK.answer)


@dataclass(frozen=True)
class Interview:
    questions: tuple[Decision, ...] = ()
    expected: tuple[str, ...] = ()
    context: str = ""
    stale: str | None = None
    tagged: bool = True
    revise_a_only: bool = False


CASES = {
    "printed": Interview((PRINT,), (PRINT.answer,)),
    "stale-print-untagged": Interview((PRINT, WAIVE_PRINT), stale=PRINT.answer, tagged=False),
    "relaxed-finish": Interview((PRINT, WAIVE_PRINT)),
    "compatible-finish-untagged": Interview((PRINT, MATTE), (PRINT.answer, MATTE.answer), tagged=False),
    "stale-print-tagged": Interview((PRINT, WAIVE_PRINT), stale=PRINT.answer),
    "compatible-finish-tagged": Interview((PRINT, MATTE), (PRINT.answer, MATTE.answer)),
    "separate-texture": Interview((PRINT, ANY_TEXTURE), (PRINT.answer,)),
    "revoked-by-no": Interview((PRINT, NO_PRINT), stale=PRINT.answer),
    "current-after-no": Interview((PRINT, NO_PRINT)),
    "independent-sample": Interview((PRINT, NO_SAMPLE), (PRINT.answer,)),
    "superseded-deadline": Interview((DEC_1, DEC_15), (DEC_15.answer,), stale=DEC_1.answer),
    "current-deadline": Interview((DEC_1, DEC_15), (DEC_15.answer,)),
    "superseded-context": Interview((OPTIONAL,), context=LOGO, stale=LOGO),
    "waived-context": Interview((OPTIONAL,), context=LOGO),
    "independent-context": Interview((ANY_TEXTURE,), (LOGO,), context=LOGO),
    # The three release acceptance cases.
    "reaffirmed-stock": Interview((STOCK, KEEP), (STOCK.answer,)),
    "engraving-independent-of-texture": Interview((ANY_TEXTURE,), (LASER,), context=LASER),
    "engraving-needs-to-be": Interview(expected=(NEEDS_LASER,), context=NEEDS_LASER),
    "engraving-prohibited": Interview(expected=(AVOID_LASER,), context=AVOID_LASER),
    "per-sku-deadline": Interview((DEC_15,), (DEC_1.answer, DEC_15.answer), context=DEC_1.answer, revise_a_only=True),
}
IMAGE_CASES = ("cropped-selector", "complete-selector", "different-readings",
               "readable-and-unreadable", "readable", "unreadable")


def scripted_client(handler):
    return AsyncOpenAI(
        api_key="scripted-test", base_url="https://offline.test/v1", max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )


@pytest.mark.parametrize("name", [*CASES, *IMAGE_CASES])
def test_original_zip_review_matrix(tmp_path, name):
    database = tmp_path / "case.db"
    scenario = CASES.get(name, Interview())
    question_ids = {}

    async def questions(inputs):
        def serve(request):
            wire = json.loads(json.loads(request.content)["input"])
            index = wire["round_number"] - 1
            result = {"reason": "Check the buyer’s requirements.", "questions": [], "context_requirements": None}
            if index == 0:
                result["context_requirements"] = []
                if scenario.context:
                    result["context_requirements"].append({
                        "statement": scenario.context, "quote": scenario.context,
                        "topic": "deadline" if scenario.revise_a_only else "finish", "option_ids": inputs.selected_option_ids,
                    })
            if index < len(scenario.questions):
                decision = scenario.questions[index]
                target = None
                if decision.revisits:
                    if index == 0:
                        assert decision.revisits == scenario.context
                        target = "context:1"
                    else:
                        row, = [row for row in wire["requirements"] if row["statement"] == decision.revisits]
                        target = row["requirement_id"]
                result["questions"].append({
                    "prompt": decision.prompt, "why_ask": "Confirm the preference for these selected options.",
                    "source_ids": [], "option_ids": inputs.selected_option_ids[:1] if scenario.revise_a_only else inputs.selected_option_ids,
                    "choices": [decision.answer], "topic": decision.topic, "buyer_facet": decision.facet if scenario.tagged else None,
                    "requirement_ids": [target] if target else [], "choice_effects": [{
                        "choice": decision.answer, "action": decision.effect, "requirement_id": target,
                        "statement": decision.answer if decision.effect in ("replace", "add") else None,
                    }],
                })
            return response(request, result)

        async with scripted_client(serve) as sdk:
            return await generate_questions(inputs, client=sdk)

    async def review(inputs, images):
        def serve(request):
            parts = json.loads(request.content)["input"][0]["content"]
            wire = json.loads(parts[0]["text"])
            payload = canonical_content(inputs)
            if name in IMAGE_CASES:
                selector, = [src for src in wire["text_sources"] if src["label"] == "Shared selector value"]
                assert selector["original_text"] == "720ml"
                a = wire["selected_options"][0]
                assert a["image_source_id"] in wire["attached_image_ids"]
                comparison = payload["comparisons"][0]
                if name in ("cropped-selector", "complete-selector", "different-readings"):
                    quote = "20ml" if name == "cropped-selector" else "720ml"
                    payload["claims"].append(text_claim("selector", selector["source_id"], quote))
                    comparison["shared_claim_ids"] = ["selector"]
                if name in ("readable", "readable-and-unreadable", "different-readings"):
                    payload["claims"].append(image_claim("photo", a["option_id"], a["image_source_id"]))
                    comparison["values"][0]["claim_ids"] = ["photo"]
                unreadable = name in ("unreadable", "readable-and-unreadable")
                conflict = name == "different-readings"
                payload["findings"].append(finding_selection(
                    [a["option_id"]], decision_id=None, claim_ids=[row["claim_id"] for row in payload["claims"]],
                    kind="listing_conflict" if conflict else "clarification_needed",
                    supplier_action="reconcile_sources" if conflict else "confirm_specification",
                    unknowns=[{"reason": "unreadable", "source_ids": [a["image_source_id"]]}] if unreadable else [],
                ))
            elif name == "superseded-deadline":
                # A full-scope replacement retains the decision ID. The provider cannot supply the old value.
                payload["buyer_requirements"] = [{
                    "requirement_id": question_ids[DEC_1.answer], "topic": "deadline",
                    "input_ref": f"answer:{inputs.answers[0].question_id}", "quote": DEC_1.answer,
                }]
            elif scenario.stale:
                # Try to publish a previous value. Only the server's current set may authorize a claim.
                stale_id = question_ids[scenario.stale]
                payload["findings"].append(finding_selection(
                    inputs.selected_option_ids, topic="deadline" if scenario.stale == DEC_1.answer else "finish",
                    kind="requirement_unconfirmed", decision_id=None, requirement_ids=[stale_id],
                ))
            # Empty findings also exercise automatic, scoped supplier checks for each active typed target.
            return response(request, payload)

        async with scripted_client(serve) as sdk:
            return await generate_review(inputs, images, client=sdk)

    def check_ready(case, selected, day=15):
        report = case["report"]
        assert report["selected_option_ids"] == selected
        draft = report["supplier_draft"].casefold()
        assert "47%" not in draft
        if scenario.revise_a_only:
            assert len(report["buyer_requirements"]) == 2
            for index, date in enumerate([day, 1]):
                row, = [r for r in report["buyer_requirements"] if selected[index] in r["option_ids"]]
                assert row["option_ids"] == [selected[index]] and f"{date} December" in row["text"]
                line, = [line for line in draft.splitlines() if f"[option {'ab'[index]}]" in line]
                assert f"{date} december" in line
        else:
            assert sorted(r["text"] for r in report["buyer_requirements"]) == sorted(scenario.expected)
            for text, term in [(STOCK.answer, "ready-made"), (LASER, "laser engrav"), (PRINT.answer, "screen print"),
                               (MATTE.answer, "matte"), (DEC_15.answer, "15 december")]:
                if text in scenario.expected:
                    assert term in draft
            if name == "current-deadline":
                assert "1 december" not in draft
            if name == "engraving-needs-to-be":
                assert "with laser engraving?" in draft and "without laser engraving?" not in draft
            if name == "engraving-prohibited":
                assert "without laser engraving?" in draft and "with laser engraving?" not in draft
        if name in ("complete-selector", "readable", "different-readings"):
            assert report["claims"]
        if name == "unreadable":
            assert "could not be read" in report["comparisons"][0]["values"][0]["limitations"][0]

    options = dict(translator=stub, question_generator=questions, review_generator=review)
    with TestClient(create_app(database, **options)) as client:
        snapshot = _upload(client, Path(os.environ.get("PURCHASE_TEST_ZIP", RAW))).json()["snapshot"]
        context = scenario.context + (" Our private margin is 47%." if scenario.context else "")
        start = initial_request(snapshot, buyer_context=context)
        selected = start["selected_option_ids"] = [snapshot["options"][i]["option_id"] for i in (6, 0)]
        case = ready(client.post("/api/questions", json=start))
        for index, decision in enumerate(scenario.questions):
            issued = case["rounds"][-1]["questions"][0]
            question_ids.update({row["statement"]: row["requirement_id"] for row in case["requirements"]})
            if decision.revisits:
                question_ids[decision.revisits] = issued["requirement_ids"][0]
            command = round_request(case, texts=[decision.answer])
            if index + 1 < len(scenario.questions):
                case = ready(client.post("/api/questions/continue", json=command))
        if not scenario.questions:
            command = {**reference(case), "operation_id": str(uuid4()), "submission": None}
        result = client.post("/api/reviews", json=command).json()
        completed = client.get("/api/case").json()["case"]
        if scenario.stale or name in ("cropped-selector", "readable-and-unreadable"):
            assert (result["status"], result["code"]) == ("failed", "invalid_review")
            assert completed["report"] is None
        else:
            assert result["status"] == "ready", result
            check_ready(completed, selected)
            if scenario.revise_a_only:
                edit = round_request(completed, texts=["The deadline has moved to 20 December."])
                issued = completed["rounds"][0]["questions"][0]
                edit["submission"]["answers"][0]["requirement_effect"] = {
                    "action": "replace", "requirement_id": issued["requirement_ids"][0],
                }
                edited = ready(client.post("/api/reviews", json=edit))
                assert edited["revision"] == completed["revision"] + 1
                check_ready(edited, selected, day=20)
                completed = edited

    async def unexpected_request(*args):
        pytest.fail("Loading a saved report requested generation.")

    with TestClient(create_app(
        database, translator=unexpected_request, question_generator=unexpected_request, review_generator=unexpected_request,
    )) as client:
        assert client.get("/api/case").json()["case"] == completed


def test_skipped_sample_cannot_change_the_deadline_after_edit_and_restart(tmp_path):
    database = tmp_path / "decisions.db"
    sample_prompt = "Do you need a pre-production sample?"

    async def questions(inputs):
        selected = inputs.selected_option_ids
        return QuestionResult.model_validate({
            "reason": "Check the revised date and sample preference.",
            "context_requirements": [{
                "quote": DEC_1.answer, "statement": DEC_1.answer, "topic": "deadline",
                "buyer_facet": "delivery_deadline", "option_ids": selected,
            }],
            "questions": [
                {
                    "prompt": DEC_15.prompt, "why_ask": "Check the delivery date for this option.",
                    "topic": "deadline", "buyer_facet": "delivery_deadline", "option_ids": selected[:1],
                    "source_ids": [], "requirement_ids": ["context:1"], "choices": [DEC_15.answer],
                    "choice_effects": [{
                        "choice": DEC_15.answer, "action": "replace", "statement": DEC_15.answer,
                        "requirement_id": "context:1",
                    }],
                },
                {
                    "prompt": sample_prompt, "why_ask": "Check whether a sample is needed.",
                    "topic": "sample", "buyer_facet": "other", "option_ids": selected, "source_ids": [],
                    "requirement_ids": [], "choices": [], "choice_effects": [],
                },
            ],
        })

    submitted_topic = "deadline"

    async def review(inputs, images):
        def serve(request):
            wire = json.loads(json.loads(request.content)["input"][0]["content"][0]["text"])
            sample, = [row for row in wire["requirements"] if row["topic"] == "sample"]
            payload = canonical_content(inputs)
            payload["findings"] = [finding_selection(
                inputs.selected_option_ids, finding_id="sample-choice", topic=submitted_topic,
                action_type="decide_preference", supplier_action=None, decision_id=sample["requirement_id"],
            )]
            return response(request, payload)

        async with scripted_client(serve) as sdk:
            return await generate_review(inputs, images, client=sdk)

    def assert_decisions(case, selected, expected):
        report = case["report"]
        assert report["selected_option_ids"] == selected
        rows = report["buyer_requirements"]
        draft = report["supplier_draft"].lower().splitlines()
        for index, (option_id, quote) in enumerate(zip(selected, expected, strict=True)):
            requirement, = [r for r in rows if option_id in r["option_ids"]]
            assert requirement["text"] == quote
            date_text = "15 december" if quote == DEC_15.answer else "1 december"
            date_lines = [
                line for line in draft if "december" in line
                and (scope := re.search(r"\[options? ([abc](?:, [abc])*)\]", line))
                and "abc"[index] in scope.group(1).split(", ")
            ]
            assert len(date_lines) == 1 and date_text in date_lines[0], draft
        preference, = report["open_preferences"]
        assert "sample" in preference["text"].lower()
        assert preference["option_ids"] == selected
        assert preference["finding_ids"] == ["sample-choice"]
        assert [q["question_text"] for q in preference["questions"]] == [sample_prompt]

    with TestClient(create_app(database, translator=stub, question_generator=questions, review_generator=review)) as client:
        snap = _upload(client, Path(os.environ.get("PURCHASE_TEST_ZIP", RAW))).json()["snapshot"]
        start = initial_request(snap, buyer_context=DEC_1.answer)
        selected = start["selected_option_ids"]
        case = ready(client.post("/api/questions", json=start))
        invalid = client.post("/api/reviews", json=round_request(case)).json()
        assert (invalid["status"], invalid["code"]) == ("failed", "invalid_review")
        failed = client.get("/api/case").json()["case"]
        assert failed["report"] is None

        submitted_topic = "sample"
        valid = ready(client.post("/api/case/retry", json=retry_request(failed)))
        assert_decisions(valid, selected, [DEC_1.answer] * 3)
        edited = ready(client.post("/api/reviews", json=round_request(valid, texts=[DEC_15.answer, None])))
        assert edited["revision"] == valid["revision"] + 1
        assert_decisions(edited, selected, [DEC_15.answer, DEC_1.answer, DEC_1.answer])

    async def forbidden(*args):
        pytest.fail("Reopening a settled purchase requested a provider.")

    with TestClient(create_app(database, translator=forbidden, question_generator=forbidden, review_generator=forbidden)) as client:
        restored = client.get("/api/case").json()["case"]
        assert restored == edited
        assert_decisions(restored, selected, [DEC_15.answer, DEC_1.answer, DEC_1.answer])
