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
    "stale-finish-wording-from-pr-10": Interview(
        (replace(WAIVE_PRINT, prompt="Is a different printing process fine for these options?",
                 answer="Any finish is acceptable"),),
        context=PRINT.answer, stale=PRINT.answer,
    ),
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


@pytest.mark.parametrize("followup", [None, "I need to check the minimum quantity before deciding."])
def test_conflicting_order_decisions_are_open_only_for_the_affected_sku(tmp_path, followup):
    database = tmp_path / "conflicting-order.db"
    stock = STOCK.answer
    custom = "Custom orders are acceptable."
    clarify = "Which order type should apply to this option?"

    async def questions(inputs):
        if inputs.round_number == 1:
            return QuestionResult.model_validate({
                "reason": "Check whether to include custom production.",
                "context_requirements": [{
                    "quote": stock, "statement": stock, "option_ids": inputs.selected_option_ids,
                    "topic": "customization", "buyer_facet": "order_type",
                }],
                "questions": [{
                    "prompt": "Would you consider a custom order for this option?",
                    "why_ask": "Confirm which order types can meet your needs.",
                    "topic": "customization", "buyer_facet": "order_type", "source_ids": [],
                    "option_ids": inputs.selected_option_ids[:1], "choices": [custom], "requirement_ids": [],
                    "choice_effects": [{
                        "choice": custom, "action": "add", "statement": custom, "requirement_id": None,
                    }],
                }],
            })
        conflict, = [row for row in inputs.requirements if row.status == "unresolved"]
        assert conflict.option_ids == inputs.selected_option_ids[:1]
        return QuestionResult.model_validate({
            "reason": "Clarify the overlapping order instructions.",
            "questions": [{
                "prompt": clarify, "why_ask": "Resolve the two order instructions for this option.",
                "topic": "customization", "buyer_facet": "order_type", "source_ids": [],
                "option_ids": conflict.option_ids, "choices": [custom], "requirement_ids": [conflict.requirement_id],
                "choice_effects": [{
                    "choice": custom, "action": "replace", "statement": custom,
                    "requirement_id": conflict.requirement_id,
                }],
            }],
        })

    async def review(inputs, images):
        def serve(request):
            payload = canonical_content(inputs)
            unresolved = [row for row in inputs.requirements if row.status == "unresolved"]
            if unresolved:
                conflict, = unresolved
                payload["findings"] = [finding_selection(
                    conflict.option_ids, finding_id="resolve-order", topic="customization",
                    action_type="decide_preference", supplier_action=None, decision_id=conflict.requirement_id,
                )]
            return response(request, payload)

        async with scripted_client(serve) as sdk:
            return await generate_review(inputs, images, client=sdk)

    options = dict(translator=stub, question_generator=questions, review_generator=review)
    with TestClient(create_app(database, **options)) as client:
        snapshot = _upload(client).json()["snapshot"]
        start = initial_request(snapshot, buyer_context=stock)
        first = ready(client.post("/api/questions", json=start))
        second = ready(client.post("/api/questions/continue", json=round_request(first, texts=[custom])))
        opened = ready(client.post("/api/reviews", json=round_request(second, texts=[followup])))
        a, *other_skus = start["selected_option_ids"]
        conflict, = [row for row in opened["requirements"] if row["status"] == "unresolved"]
        assert conflict["option_ids"] == [a]
        quotes = {stock, custom} if followup is None else {stock, custom, followup}
        assert {entry["quote"] for entry in conflict["provenance"]} == quotes
        report = opened["report"]
        requirement, = report["buyer_requirements"]
        assert (requirement["text"], requirement["option_ids"]) == (stock, other_skus)
        preference, = report["open_preferences"]
        assert preference["option_ids"] == [a]
        assert preference["finding_ids"] == ["resolve-order"]
        if followup is not None:
            question_id = second["rounds"][-1]["questions"][0]["question_id"]
            assert f"answer:{question_id}" in preference["input_refs"]
            assert any(question["question_text"] == clarify for question in preference["questions"])
            assert followup not in report["supplier_draft"]
        assert "[Options B, C]" in report["supplier_draft"]
        assert "[Option A]" not in report["supplier_draft"]

    with TestClient(create_app(database, **options)) as client:
        assert client.get("/api/case").json()["case"] == opened
        clarified = ready(client.post("/api/reviews", json=round_request(opened, texts=[custom])))
        assert clarified["revision"] == opened["revision"] + 1
        report = clarified["report"]
        assert not report["open_preferences"]
        assert {row["text"]: row["option_ids"] for row in report["buyer_requirements"]} == {
            stock: other_skus, custom: [a],
        }
        assert "[Option A]" in report["supplier_draft"]
        assert "[Options B, C]" in report["supplier_draft"]

    with TestClient(create_app(database, **options)) as client:
        assert client.get("/api/case").json()["case"] == clarified


def test_pre_guard_report_with_opposite_order_instructions_requires_regeneration(tmp_path, monkeypatch):
    from backend import requirements

    database = tmp_path / "old-order.db"
    stock = STOCK.answer
    custom = "Custom orders are acceptable."

    async def questions(inputs):
        return QuestionResult.model_validate({
            "reason": "Read the order instructions.",
            "context_requirements": [{
                "quote": quote, "statement": quote, "option_ids": scope,
                "topic": "customization", "buyer_facet": "order_type",
            } for quote, scope in [
                (stock, inputs.selected_option_ids), (custom, inputs.selected_option_ids[:1]),
            ]],
            "questions": [],
        })

    async def review(inputs, images):
        async with scripted_client(lambda request: response(request, canonical_content(inputs))) as sdk:
            return await generate_review(inputs, images, client=sdk)

    options = dict(translator=stub, question_generator=questions, review_generator=review)
    with monkeypatch.context() as before_guard:
        before_guard.setattr(requirements, "_order_conflicts", lambda case_id, rows: {})
        with TestClient(create_app(database, **options)) as client:
            snapshot = _upload(client).json()["snapshot"]
            start = initial_request(snapshot, buyer_context=f"{stock} {custom}")
            case = ready(client.post("/api/questions", json=start))
            old = ready(client.post("/api/reviews", json={
                **reference(case), "operation_id": str(uuid4()), "submission": None,
            }))
            assert len(old["report"]["buyer_requirements"]) == 2
            assert "[Options A, B, C]" in old["report"]["supplier_draft"]
            assert "[Option A]" in old["report"]["supplier_draft"]

    async def forbidden(*args):
        pytest.fail("Reopening a case should not call the provider.")

    with TestClient(create_app(database, translator=forbidden, question_generator=forbidden,
                               review_generator=forbidden)) as client:
        restored = client.get("/api/case").json()["case"]
        assert reference(restored) == reference(old)
        assert restored["selected_option_ids"] == old["selected_option_ids"]
        assert restored["buyer_context"] == old["buyer_context"]
        assert restored["report"] is None
        assert restored["last_operation"]["code"] == "review_regeneration_required"

    with TestClient(create_app(database, **options)) as client:
        regenerated = ready(client.post("/api/case/retry", json=retry_request(restored)))
    report = regenerated["report"]
    preference, = report["open_preferences"]
    assert preference["option_ids"] == old["selected_option_ids"][:1]
    assert "[Option A]" not in report["supplier_draft"]
    assert "[Options B, C]" in report["supplier_draft"]


@pytest.mark.parametrize("origin", ["context", "untyped-answer", "untyped-waived-answer", "old-report"])
def test_skipped_repeat_opens_only_unsettled_skus_of_that_facet(tmp_path, monkeypatch, origin):
    from backend import case_workflow

    database = tmp_path / "repeat-decision.db"
    repeat = "Is screen printing required?"
    surface = "Which surface finish do you need?"
    waiver = "Screen printing is no longer required."
    untyped = origin.startswith("untyped-")

    async def questions(inputs):
        a, b, *_ = inputs.selected_option_ids
        if untyped and inputs.round_number == 1:
            return QuestionResult.model_validate({
                "reason": "Confirm the desired method for the first option.",
                "context_requirements": [],
                "questions": [{
                    "prompt": "Which printing process do you need for this option?",
                    "why_ask": "Identify the required decoration for the first option.",
                    "topic": "finish", "buyer_facet": None, "option_ids": [a], "source_ids": [],
                    "requirement_ids": [], "choices": [PRINT.answer], "choice_effects": [{
                        "choice": PRINT.answer, "action": "add", "statement": PRINT.answer,
                        "requirement_id": None,
                    }],
                }],
            })
        if origin == "untyped-waived-answer" and inputs.round_number == 2:
            requirement, = inputs.requirements
            return QuestionResult.model_validate({
                "reason": "Confirm whether the printing requirement still applies.",
                "questions": [{
                    "prompt": "Do you still require screen printing?",
                    "why_ask": "Check whether to keep the prior decoration decision.",
                    "topic": "finish", "buyer_facet": "decoration_method", "option_ids": [a],
                    "source_ids": [], "requirement_ids": [requirement.requirement_id],
                    "choices": [waiver], "choice_effects": [{
                        "choice": waiver, "action": "waive", "statement": None,
                        "requirement_id": requirement.requirement_id,
                    }],
                }],
            })
        return QuestionResult.model_validate({
            "reason": "Check the decoration and texture decisions.",
            "context_requirements": [{
                "quote": PRINT.answer, "statement": PRINT.answer, "option_ids": [a],
                "topic": "finish", "buyer_facet": "decoration_method",
            }] if inputs.round_number == 1 else None,
            "questions": [{
                "prompt": prompt, "why_ask": "Confirm the finish for the applicable options.",
                "topic": "finish", "buyer_facet": facet, "option_ids": scope, "source_ids": [],
                "requirement_ids": [], "choices": [], "choice_effects": [],
            } for prompt, facet, scope in [
                (repeat, "decoration_method", [a, b]), (surface, "surface_finish", [a]),
            ]],
        })

    async def review(inputs, images):
        async with scripted_client(lambda request: response(request, canonical_content(inputs))) as sdk:
            return await generate_review(inputs, images, client=sdk)

    def completed_report(app, start):
        with TestClient(app) as client:
            snapshot = _upload(client, RAW).json()["snapshot"]
            context = "" if untyped else PRINT.answer
            start.update(initial_request(snapshot, buyer_context=context))
            case = ready(client.post("/api/questions", json=start))
            if untyped:
                case = ready(client.post("/api/questions/continue", json=round_request(case, texts=[PRINT.answer])))
            if origin == "untyped-waived-answer":
                case = ready(client.post("/api/questions/continue", json=round_request(case, texts=[waiver])))
            return ready(client.post("/api/reviews", json=round_request(case)))

    options = dict(translator=stub, question_generator=questions, review_generator=review)
    start = {}
    if origin == "old-report":
        original = case_workflow.resolve_requirements

        def previous(*args, **kwargs):
            return original(*args, **{**kwargs, "projection": "before_review_fixes"})

        with monkeypatch.context() as old_projection:
            old_projection.setattr(case_workflow, "resolve_requirements", previous)
            old = completed_report(create_app(database, **options), start)
        assert any(
            row["option_ids"] == start["selected_option_ids"][:2]
            for row in old["report"]["open_preferences"]
        )

        async def forbidden(*args):
            pytest.fail("Restart must preserve answers and wait for explicit regeneration.")

        with TestClient(create_app(database, translator=forbidden, question_generator=forbidden,
                                   review_generator=forbidden)) as client:
            restored = client.get("/api/case").json()["case"]
            assert reference(restored) == reference(old)
            assert restored["rounds"] == old["rounds"]
            assert restored["report"] is None
            assert restored["last_operation"]["code"] == "review_regeneration_required"
        with TestClient(create_app(database, **options)) as client:
            reviewed = ready(client.post("/api/case/retry", json=retry_request(restored)))
    else:
        reviewed = completed_report(create_app(database, **options), start)

    a, b, *_ = start["selected_option_ids"]
    report = reviewed["report"]
    if origin == "untyped-waived-answer":
        assert not report["buyer_requirements"]
        waived, = [row for row in reviewed["requirements"] if row["status"] == "waived"]
        assert (waived["option_ids"], waived["supplier_target"]) == ([a], None)
    else:
        active, = report["buyer_requirements"]
        assert (active["text"], active["option_ids"]) == (PRINT.answer, [a])
    assert {row["questions"][0]["question_text"]: row["option_ids"] for row in report["open_preferences"]} == {
        repeat: [b], surface: [a],
    }
    assert report["open_preferences"][0]["questions"][0]["option_ids"] == [a, b]
    with TestClient(create_app(database, **options)) as client:
        assert client.get("/api/case").json()["case"] == reviewed


def test_recovery_preserves_a_later_question_issued_for_an_unsplit_order_conflict(tmp_path, monkeypatch):
    from backend import case_workflow

    database = tmp_path / "unsplit-conflict.db"
    custom = "Custom orders are acceptable."
    uncertain = "I need to check the minimum quantity before deciding."

    async def questions(inputs):
        scope = inputs.selected_option_ids
        result = {"reason": "Check order constraints for the chosen options."}
        if inputs.round_number == 1:
            result["context_requirements"] = [{
                "quote": STOCK.answer, "statement": STOCK.answer, "option_ids": scope,
                "topic": "customization", "buyer_facet": "order_type",
            }]
            prompt, target, choice = "Would you consider a custom order?", None, custom
        else:
            conflict, = inputs.requirements
            target = conflict.requirement_id
            if inputs.round_number == 2:
                prompt, scope, choice = "What order type works for this option?", scope[:1], None
            else:
                prompt, choice = "Which order type have you chosen for these options?", STOCK.answer
        result["questions"] = [{
            "prompt": prompt, "why_ask": "Confirm the applicable order terms.",
            "option_ids": scope, "source_ids": [], "topic": "customization", "buyer_facet": "order_type",
            "requirement_ids": [target] if target else [], "choices": [choice] if choice else [],
            "choice_effects": [{
                "choice": choice, "statement": choice, "requirement_id": target,
                "action": "replace" if target else "add",
            }] if choice else [],
        }]
        return QuestionResult.model_validate(result)

    async def review(inputs, images):
        async with scripted_client(lambda request: response(request, canonical_content(inputs))) as sdk:
            return await generate_review(inputs, images, client=sdk)

    options = dict(translator=stub, question_generator=questions, review_generator=review)
    original = case_workflow.resolve_requirements

    def previous(*args, **kwargs):
        return original(*args, **{**kwargs, "projection": "before_review_fixes"})

    with monkeypatch.context() as old_projection:
        old_projection.setattr(case_workflow, "resolve_requirements", previous)
        with TestClient(create_app(database, **options)) as client:
            snapshot = _upload(client, RAW).json()["snapshot"]
            start = initial_request(snapshot, buyer_context=STOCK.answer)
            first = ready(client.post("/api/questions", json=start))
            second = ready(client.post("/api/questions/continue", json=round_request(first, texts=[custom])))
            third = ready(client.post("/api/questions/continue", json=round_request(second, texts=[uncertain])))
            assert third["rounds"][-1]["questions"][0]["option_ids"] == start["selected_option_ids"]
            saved = ready(client.post("/api/reviews", json=round_request(third, texts=[STOCK.answer])))

    with TestClient(create_app(database, **options)) as client:
        restored = client.get("/api/case").json()["case"]
        assert reference(restored) == reference(saved)
        assert restored["rounds"] == saved["rounds"]
        assert restored["last_operation"]["code"] == "review_regeneration_required"
        regenerated = ready(client.post("/api/case/retry", json=retry_request(restored)))

    a, *other_options = start["selected_option_ids"]
    assert {tuple(row["option_ids"]): row["text"] for row in regenerated["report"]["buyer_requirements"]} == {
        (a,): STOCK.answer, tuple(other_options): STOCK.answer,
    }
    assert not regenerated["report"]["open_preferences"]
    row_a = next(row for row in regenerated["requirements"] if row["option_ids"] == [a])
    assert any(entry["quote"] == uncertain for entry in row_a["provenance"])
    assert uncertain not in regenerated["report"]["supplier_draft"]
