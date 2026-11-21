"""Verify the buying brief at the HTTP/provider boundary."""

import json
import os
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.ai import generate_brief_proposal
from backend.main import create_app
from backend.tests.offline_server import offline_brief, offline_questions, offline_review, offline_translation
from backend.tests.test_cases import confirm_command, initial_request, ready, reference, round_request
from backend.tests.test_imports import RAW, _upload
from backend.tests.test_purchase_decisions import scripted_client
from backend.tests.test_reports import response


CONTEXT = "Ready-made only. All three must arrive by 1 December."
EXCEPTION = "Actually, custom is fine for the blue cup if it still arrives by then."


def preview(client, case, text, option_id=None, resolves_change_id=None):
    correction = {
        **reference(case), "text": text, "option_id": option_id, "resolves_change_id": resolves_change_id,
    }
    response = client.post("/api/brief/preview", json=correction)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert "proposal" in payload, payload
    return correction, payload


def apply(client, correction, payload):
    return ready(client.post("/api/brief/edit", json={
        **reference(correction), "operation_id": str(uuid4()), "correction": correction, "proposal": payload["proposal"],
    }))


def test_conditional_order_previews_edits_confirms_and_reopens_on_original_zip(tmp_path):
    database = tmp_path / "case.sqlite3"
    requests = []

    async def review(inputs, images):
        requests.append((inputs, images))
        return await offline_review(inputs, images)

    options = dict(translator=offline_translation, question_generator=offline_questions,
                   review_generator=review, brief_generator=offline_brief)
    with TestClient(create_app(database, **options)) as client:
        archive = Path(os.environ.get("PURCHASE_TEST_ZIP", RAW))
        snapshot = _upload(client, archive).json()["snapshot"]
        names = {row["name_original"]: row["option_id"] for row in snapshot["options"]}
        selected = [names[name] for name in ["暮色316-贴画黑白", "暮色316-贴画蓝白", "暮色316-贴画黑粉"]]
        case = ready(client.post("/api/questions", json={
            **initial_request(snapshot, buyer_context=CONTEXT), "selected_option_ids": selected,
        }))
        assert case["phase"] == "ready_for_review" and case["rounds"] == []
        assert len(case["requirements"]) == 2
        stale = confirm_command(case)
        assert client.post("/api/reviews", json={k: v for k, v in stale.items() if k != "confirm"}).status_code == 422
        correction, impact = preview(client, case, EXCEPTION)
        assert impact["question"] is None
        assert impact["proposal"]["option_ids"] == selected[1:2]
        case = apply(client, correction, impact)
        conditional = next(row for row in case["requirements"] if row.get("delivery_condition"))
        assert conditional["option_ids"] == selected[1:2]
        assert [source["quote"] for source in conditional["provenance"]] == ["Ready-made only.", EXCEPTION]
        assert conditional["delivery_condition"]["latest_arrival"]["day"] == 1
        stock = next(row for row in case["requirements"] if (row["supplier_target"] or {}).get("value") == "ready_made_only")
        assert stock["option_ids"] == selected[::2]
        assert client.post("/api/reviews", json=stale).status_code == 409
        correction, impact = preview(client, case, "Move the shared deadline to 15 December.")
        updated = [row for row in impact["changed_requirements"] if row.get("delivery_condition")]
        assert updated[0]["delivery_condition"]["latest_arrival"]["day"] == 15
        moved = apply(client, correction, impact)
        undo = {"undo_change_id": moved["brief_changes"][-1]["change_id"]}
        undo_preview = client.post("/api/brief/preview-undo", json={**reference(moved), **undo}).json()
        restored_condition = next(row for row in undo_preview["changed_requirements"] if row.get("delivery_condition"))
        assert restored_condition["delivery_condition"]["latest_arrival"]["day"] == 1
        current = ready(client.post("/api/brief/edit", json={
            **reference(moved), **undo, "operation_id": str(uuid4()),
        }))
        assert requests == []
        assert client.post("/api/reviews", json=confirm_command(moved)).status_code == 409
        reviewed = ready(client.post("/api/reviews", json=confirm_command(current)))
        draft = reviewed["report"]["supplier_draft"]
        assert "[Option B] Can this option be custom-made and still arrive by 1 December?" in draft
        assert "[Options A, C] Can these options ship from ready-made stock and also arrive by 1 December?" in draft
        assert ready(client.post("/api/reviews", json=confirm_command(current))) == reviewed
        assert reviewed["confirmed_revision"] == current["revision"]
        assert len(requests) == 1
        assert set(requests[0][1]) == {row["image_source_id"] for row in snapshot["options"] if row["option_id"] in selected}

    async def forbidden(*args):
        raise AssertionError("Restore contacted a provider")

    with TestClient(create_app(database, translator=forbidden, question_generator=forbidden,
                               review_generator=forbidden, brief_generator=forbidden)) as client:
        assert client.get("/api/case").json()["case"] == reviewed


def test_early_exit_saves_before_confirmation_and_ambiguous_sku_can_be_left_open(tmp_path):
    requests = []

    async def review(inputs, images):
        requests.append(inputs)
        return await offline_review(inputs, images)

    with TestClient(create_app(tmp_path / "case.db", translator=offline_translation, question_generator=offline_questions,
                               review_generator=review, brief_generator=offline_brief)) as client:
        snapshot = _upload(client, Path(os.environ.get("PURCHASE_TEST_ZIP", RAW))).json()["snapshot"]
        names = {row["name_original"]: row["option_id"] for row in snapshot["options"]}
        selected = [names[name] for name in ["暮色316-贴画蓝白", "暮色316-字母蓝白", "暮色316-贴画黑粉"]]
        case = ready(client.post("/api/questions", json={**initial_request(snapshot), "selected_option_ids": selected}))
        assert client.post("/api/reviews", json=confirm_command(case)).status_code == 422
        case = ready(client.post("/api/brief", json=round_request(case, texts=["Ready-made only", None])))
        assert requests == []
        correction, impact = preview(client, case, EXCEPTION)
        assert "Which selected option" in impact["question"]
        assert impact["proposal"]["kind"] == "unclear"
        opened = apply(client, correction, impact)
        assert all(row["status"] != "active" for row in opened["requirements"] if row["statement"] == EXCEPTION)
        assert requests == []
        reviewed = ready(client.post("/api/reviews", json=confirm_command(opened)))
        assert reviewed["report"]["open_preferences"]
        assert "be custom-made" not in reviewed["report"]["supplier_draft"]
        assert len(requests) == 1
        ambiguity, = opened["brief_changes"]
        assert "Which selected option" in ambiguity["question"]
        _, missing_date = preview(client, reviewed, EXCEPTION, option_id=selected[0])
        assert "latest arrival date" in missing_date["question"]
        correction, impact = preview(
            client, reviewed, "Actually, custom is fine for Option A if it arrives by 1 December.",
            resolves_change_id=ambiguity["change_id"],
        )
        clarified = apply(client, correction, impact)
        assert not any(row["statement"] == EXCEPTION for row in clarified["requirements"])
        fixed = next(row for row in clarified["requirements"] if row.get("delivery_condition"))
        assert fixed["option_ids"] == selected[:1]
        assert not fixed["delivery_condition"]["follows_deadline"]
        assert len(requests) == 1


def test_preview_undo_lists_saved_follow_up_answers_before_discarding_them(tmp_path):
    with TestClient(create_app(tmp_path / "case.db", translator=offline_translation, question_generator=offline_questions,
                               review_generator=offline_review, brief_generator=offline_brief)) as client:
        archive = Path(os.environ.get("PURCHASE_TEST_ZIP", RAW))
        snapshot = _upload(client, archive).json()["snapshot"]
        case = ready(client.post("/api/questions", json=initial_request(snapshot)))
        correction, impact = preview(client, case, "Move the delivery deadline to 15 December.")
        assert [row["round_number"] for row in impact["discarded_rounds"]] == [1]
        edited = apply(client, correction, impact)
        assert edited["rounds"] == [] and edited["can_resume_questions"]
        resumed = ready(client.post("/api/questions/resume", json={
            **reference(edited), "operation_id": str(uuid4()),
        }))
        request = round_request(resumed, texts=["Custom orders are acceptable", None])
        next_round = ready(client.post("/api/questions/continue", json=request))
        answered = ready(client.post("/api/brief", json=round_request(next_round, texts=["A sample is required"])))
        undo = {"undo_change_id": answered["brief_changes"][-1]["change_id"]}
        impact = client.post("/api/brief/preview-undo", json={**reference(answered), **undo}).json()
        assert [row["round_number"] for row in impact["discarded_rounds"]] == [1, 2]
        assert impact["discarded_rounds"][1]["answers"][0]["answer_text"] == "A sample is required"
        assert client.get("/api/case").json()["case"] == answered
        undone = ready(client.post("/api/brief/edit", json={**reference(answered), **undo, "operation_id": str(uuid4())}))
        assert undone["rounds"] == [] and undone["revision"] == answered["revision"] + 1
        assert "confirmed_revision" not in undone
        assert client.post("/api/reviews", json=confirm_command(answered)).status_code == 409


def test_structured_correction_rejects_a_provider_proposal_for_the_wrong_option(tmp_path):
    calls = []

    async def propose(text, snapshot, selected, option_id):
        def serve(request):
            wire = json.loads(request.content)
            payload = json.loads(wire["input"])
            assert payload["correction"] == EXCEPTION
            assert [item["option_id"] for item in payload["selected_options"]] == selected
            option = selected[0 if not calls else 1]
            calls.append(option)
            return response(request, {"kind": "custom_by_deadline", "option_ids": [option], "date": None})

        async with scripted_client(serve) as provider:
            return await generate_brief_proposal(text, snapshot, selected, option_id, client=provider)

    with TestClient(create_app(tmp_path / "case.db", translator=offline_translation, question_generator=offline_questions,
                               review_generator=offline_review, brief_generator=propose)) as client:
        snapshot = _upload(client, Path(os.environ.get("PURCHASE_TEST_ZIP", RAW))).json()["snapshot"]
        names = {row["name_original"]: row["option_id"] for row in snapshot["options"]}
        selected = [names[name] for name in ["暮色316-贴画黑白", "暮色316-贴画蓝白", "暮色316-贴画黑粉"]]
        case = ready(client.post("/api/questions", json={
            **initial_request(snapshot, buyer_context=CONTEXT), "selected_option_ids": selected,
        }))
        correction, previewed = preview(client, case, EXCEPTION)
        assert calls == selected[:2]
        assert previewed["proposal"]["option_ids"] == selected[1:2]
        assert client.get("/api/case").json()["case"] == case
        forged = {**previewed["proposal"], "option_ids": selected[:1]}
        assert client.post("/api/brief/edit", json={
            **reference(case), "operation_id": str(uuid4()), "correction": correction, "proposal": forged,
        }).status_code == 422
        revised = apply(client, correction, previewed)
        ready(client.post("/api/reviews", json=confirm_command(revised)))
        assert calls == selected[:2]
