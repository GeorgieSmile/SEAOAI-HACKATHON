import asyncio
import os
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from backend.contracts import ContextRequirement, QuestionDraft, QuestionResult, RequirementEffect
from backend.main import create_app
from backend.natural_answer import NaturalAnswerDecision
from backend.supplier_targets import OrderTarget
from backend.tests.offline_server import offline_brief, offline_review, offline_translation
from backend.tests.test_cases import confirm_command, initial_request, ready, round_request
from backend.tests.test_imports import RAW, _upload


STOCK = "Ready-made only."
DEADLINE = "All three must arrive by 1 December."
CONTEXT = f"{STOCK} {DEADLINE}"
BESPOKE = "A bespoke production run works for us."
CONDITIONAL = "  Actually, custom is fine for the blue cup if it still arrives by then.  "
ORIGINAL_ZIP = Path(os.environ.get("PURCHASE_TEST_ZIP", RAW))


async def no_provider(*_):
    raise AssertionError("This path should not call a provider.")


def ask_order(option_ids, prompt):
    return QuestionDraft(
        prompt=prompt, why_ask="The purchase depends on the production method.",
        option_ids=option_ids, source_ids=[], choices=[],
        topic="customization", buyer_facet="order_type", requirement_ids=["context:1"], choice_effects=[],
    )


async def order_questions(inputs):
    if inputs.round_number > 1:
        return QuestionResult(reason="The buyer answered the order question.", questions=[])
    return QuestionResult(
        reason="Clarify whether the order constraints have changed.",
        context_requirements=[
            ContextRequirement(statement=STOCK, quote=STOCK, option_ids=inputs.selected_option_ids,
                               topic="customization", buyer_facet="order_type"),
            ContextRequirement(statement=DEADLINE, quote=DEADLINE, option_ids=inputs.selected_option_ids,
                               topic="deadline", buyer_facet="delivery_deadline"),
        ],
        questions=[ask_order(inputs.selected_option_ids, "Are custom orders acceptable for these options?")],
    )


def case_request(snapshot):
    names = {row["name_original"]: row["option_id"] for row in snapshot["options"]}
    return {
        **initial_request(snapshot, buyer_context=CONTEXT),
        "selected_option_ids": [names[name] for name in ["暮色316-贴画黑白", "暮色316-贴画蓝白", "暮色316-贴画黑粉"]],
    }


def natural_submission(case, *texts):
    command = round_request(case, texts=texts)
    for answer in command["submission"]["answers"]:
        if answer["answer_text"] is not None:
            answer["answer_mode"] = "natural"
    return command


def options(**overrides):
    return {
        "translator": offline_translation, "question_generator": order_questions,
        "review_generator": offline_review, "brief_generator": offline_brief,
        **overrides,
    }


def test_natural_answer_changes_only_blue_and_survives_early_confirm_edit_and_restart(tmp_path):
    db = tmp_path / "case.db"
    with TestClient(create_app(db, **options(interpret_answers=no_provider))) as client:
        snapshot = _upload(client, ORIGINAL_ZIP).json()["snapshot"]
        opening = ready(client.post("/api/questions", json=case_request(snapshot)))
        selection = opening["selected_option_ids"]
        command = natural_submission(opening, CONDITIONAL)

        preview = client.post("/api/brief/preview-answers", json=command)
        assert preview.status_code == 200, preview.text
        assert client.get("/api/case").json()["case"] == opening
        conditional, = [row for row in preview.json()["changed_requirements"] if row.get("delivery_condition")]
        assert conditional["option_ids"] == selection[1:2]
        assert [entry["quote"] for entry in conditional["provenance"]] == [STOCK, CONDITIONAL]
        assert conditional["provenance"][-1]["option_ids"] == selection[1:2]

        brief = ready(client.post("/api/brief", json=command))
        assert brief["phase"] == "ready_for_review"
        saved, = brief["rounds"][0]["answers"]
        assert saved["answer_text"] == CONDITIONAL
        assert saved["natural_decision"]["option_ids"] == selection[1:2]
        assert saved["natural_decision"]["effect"]["action"] == "replace"
        stock, = [row for row in brief["requirements"] if (row["supplier_target"] or {}).get("value") == "ready_made_only"]
        assert stock["option_ids"] == selection[::2]
        confirmed = confirm_command(brief)
        reviewed = ready(client.post("/api/reviews", json=confirmed))
        assert "[Option B] Can this option be custom-made and still arrive by 1 December?" in reviewed["report"]["supplier_draft"]
        assert "[Options A, C] Can these options ship from ready-made stock and also arrive by 1 December?" in reviewed["report"]["supplier_draft"]

        unchanged = round_request(reviewed, texts=[CONDITIONAL])
        assert ready(client.post("/api/brief", json=unchanged)) == reviewed
        revised_command = natural_submission(reviewed, "For Option B, custom is fine if it arrives by 15 December.")
        revised_preview = client.post("/api/brief/preview-answers", json=revised_command).json()
        assert revised_preview["changed_requirements"][0]["delivery_condition"]["latest_arrival"]["day"] == 15
        revised = ready(client.post("/api/brief", json=revised_command))
        assert revised["revision"] == reviewed["revision"] + 1
        assert revised["report"] is None
        assert client.post("/api/reviews", json=confirmed).status_code == 409
        final = ready(client.post("/api/reviews", json=confirm_command(revised)))
        assert "[Option B] Can this option be custom-made and still arrive by 15 December?" in final["report"]["supplier_draft"]
        assert "[Options A, C] Can these options ship from ready-made stock and also arrive by 1 December?" in final["report"]["supplier_draft"]

    with TestClient(create_app(db, **options(question_generator=no_provider, review_generator=no_provider))) as client:
        assert client.get("/api/case").json()["case"] == final


def test_ambiguous_natural_answer_and_client_forged_metadata_do_not_change_case(tmp_path):
    async def one_sku(inputs):
        result = await order_questions(inputs)
        return result.model_copy(update={"questions": [ask_order(inputs.selected_option_ids[:1], "Is custom production acceptable?")]})

    with TestClient(create_app(tmp_path / "case.db", **options(question_generator=one_sku, interpret_answers=no_provider))) as client:
        snapshot = _upload(client, ORIGINAL_ZIP).json()["snapshot"]
        case = ready(client.post("/api/questions", json=case_request(snapshot)))
        wrong = natural_submission(case, "Custom is fine for Option B.")
        question_id = case["rounds"][0]["questions"][0]["question_id"]
        for route in ("/api/brief/preview-answers", "/api/brief", "/api/questions/continue"):
            response = client.post(route, json=wrong)
            assert response.status_code == 422, response.text
            assert response.json()["code"] == "answer_clarification_required"
            assert response.json()["question_id"] == question_id
            assert client.get("/api/case").json()["case"] == case
        wrong["submission"]["answers"][0]["natural_decision"] = {
            "effect": {"action": "replace", "requirement_id": case["requirements"][0]["requirement_id"]},
            "option_ids": case["selected_option_ids"][:1],
            "supplier_target": {"kind": "order_type", "value": "custom_orders_acceptable"},
            "delivery_condition": None,
        }
        response = client.post("/api/brief", json=wrong)
        assert response.status_code == 422 and response.json()["code"] == "invalid_input"
        brief = ready(client.post("/api/brief", json=natural_submission(case, "I'm not sure.")))
        assert brief["requirements"] == case["requirements"]
        assert brief["rounds"][0]["answers"][0]["answer_text"] == "I'm not sure."
        assert "natural_decision" not in brief["rounds"][0]["answers"][0]


async def split_order_questions(inputs):
    result = await order_questions(inputs)
    if inputs.round_number == 1:
        result = result.model_copy(update={"questions": [
            ask_order(inputs.selected_option_ids[:1], "Would custom production work for Option A?"),
            ask_order(inputs.selected_option_ids[1:2], "Can Option B be made to order?"),
        ]})
    return result


def bespoke_decisions(batch):
    return {
        question_id: NaturalAnswerDecision(
            "decided", RequirementEffect(action="replace", requirement_id=question.requirement_ids[0]),
            question.option_ids, OrderTarget(value="custom_orders_acceptable"),
        )
        for question_id, (question, _) in batch.items()
    }


def test_fallback_batches_questions_and_reuses_preview_after_restart(tmp_path):
    db = tmp_path / "case.db"
    calls = []

    async def fallback(snapshot, selected, batch):
        calls.append((snapshot.snapshot_id, list(selected), list(batch)))
        return bespoke_decisions(batch)

    overrides = options(question_generator=split_order_questions, interpret_answers=fallback)
    with TestClient(create_app(db, **overrides)) as client:
        snapshot = _upload(client, ORIGINAL_ZIP).json()["snapshot"]
        case = ready(client.post("/api/questions", json=case_request(snapshot)))
        selected = case["selected_option_ids"]
        command = natural_submission(case, BESPOKE, BESPOKE)
        first = client.post("/api/brief/preview-answers", json=command)
        assert first.status_code == 200, first.text
        assert [row["option_ids"] for row in first.json()["changed_requirements"] if row["statement"] == BESPOKE] == [
            selected[:1], selected[1:2],
        ]
        assert client.post("/api/brief/preview-answers", json=command).json() == first.json()
        assert calls == [(snapshot["snapshot_id"], selected, [question["question_id"] for question in case["rounds"][0]["questions"]])]

    with TestClient(create_app(db, **overrides)) as client:
        assert client.get("/api/case").json()["case"] == case
        changed = natural_submission(case, BESPOKE, "A made-to-order variant is acceptable to our team.")
        assert client.post("/api/brief", json=changed).status_code == 422
        save = {**command, "operation_id": str(uuid4())}
        reviewed_brief = ready(client.post("/api/questions/continue", json=save))
        assert reviewed_brief["phase"] == "ready_for_review"
        assert len(calls) == 1
        assert [answer["natural_decision"]["option_ids"] for answer in reviewed_brief["rounds"][0]["answers"]] == [
            selected[:1], selected[1:2],
        ]
        assert ready(client.post("/api/questions/continue", json=save)) == reviewed_brief
        reviewed = ready(client.post("/api/reviews", json=confirm_command(reviewed_brief)))
        assert {(row["quote"], tuple(row["option_ids"])) for row in reviewed["report"]["buyer_requirements"]} >= {
            (BESPOKE, tuple(selected[:1])), (BESPOKE, tuple(selected[1:2])), (STOCK, tuple(selected[2:])),
        }

    with TestClient(create_app(db, **options(question_generator=no_provider, review_generator=no_provider))) as client:
        assert client.get("/api/case").json()["case"] == reviewed
    assert len(calls) == 1


def test_failed_provider_attempt_is_spent_across_restart_but_skip_can_continue(tmp_path):
    db = tmp_path / "case.db"
    calls = []

    async def fail(_, __, batch):
        calls.append(batch)
        raise TimeoutError

    def run(db, **overrides):
        return TestClient(create_app(db, **options(interpret_answers=fail, **overrides)))

    with run(db) as client:
        snapshot = _upload(client, ORIGINAL_ZIP).json()["snapshot"]
        case = ready(client.post("/api/questions", json=case_request(snapshot)))
        command = natural_submission(case, BESPOKE)
        response = client.post("/api/brief", json=command)
        assert response.status_code == 422
        assert "unavailable" in response.json()["message"]

    with run(db) as client:
        assert client.post("/api/brief", json=command).status_code == 422
        assert len(calls) == 1
        open_brief = ready(client.post("/api/brief", json=natural_submission(case, None)))
        assert open_brief["requirements"] == case["requirements"]


def test_inflight_provider_is_single_and_late_result_cannot_write_to_replaced_upload(tmp_path):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def fallback(_, __, batch):
            calls.append(batch)
            entered.set()
            await release.wait()
            return bespoke_decisions(batch)

        app = create_app(tmp_path / "case.db", **options(interpret_answers=fallback))
        async with app.router.lifespan_context(app):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                snapshot = (await _upload(client, ORIGINAL_ZIP)).json()["snapshot"]
                case = ready(await client.post("/api/questions", json=case_request(snapshot)))
                command = natural_submission(case, BESPOKE)
                obsolete = asyncio.create_task(client.post("/api/brief", json=command))
                await asyncio.wait_for(entered.wait(), timeout=5)
                try:
                    assert (await client.post("/api/brief/preview-answers", json=command)).status_code == 409
                    fresh = (await _upload(client, ORIGINAL_ZIP)).json()["snapshot"]
                    assert fresh["snapshot_id"] != snapshot["snapshot_id"]
                finally:
                    release.set()
                assert (await asyncio.wait_for(obsolete, timeout=5)).status_code == 409
                assert (await client.get("/api/case")).json() == {"snapshot": fresh, "case": None}
                assert len(calls) == 1

    asyncio.run(scenario())
