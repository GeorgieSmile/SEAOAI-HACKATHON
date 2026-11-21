import asyncio
from contextlib import closing
from hashlib import sha256
import json
import sqlite3
import subprocess
import sys
from uuid import uuid4

from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
import httpx2
from openai import AsyncOpenAI
import pytest

from backend.case_models import CaseOperation, CaseRecord
from backend.contracts import QuestionDraft, QuestionResult, ReviewInput
from backend.main import create_app
from backend.report import generate_review
from backend.report_models import ReviewFailed
from backend.state import Store
from backend.tests.offline_server import offline_review
from backend.tests.test_cases import drafts, initial_request, ready, reference, round_request
from backend.tests.test_imports import INVENTORY, RAW, _copy_zip, _image_url, _upload, stub
from backend.tests.test_reports import finding_selection, minimal_content, response as provider_response


def retry_request(case):
    return {**reference(case), "operation_id": case["last_operation"]["operation_id"]}


@pytest.mark.parametrize("action", ["upload", "selection", "context", "answers"])
def test_late_review_cannot_publish_after_inputs_change(tmp_path, action):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def slow_first_review(inputs, images):
            calls.append((inputs, dict(images)))
            if len(calls) == 1:
                entered.set()
                await release.wait()
                assert images == calls[0][1]
            return await offline_review(inputs, images)

        app = create_app(tmp_path / "case.db", translator=stub, question_generator=drafts,
                         review_generator=slow_first_review)
        async with app.router.lifespan_context(app):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                snapshot = (await _upload(client, RAW)).json()["snapshot"]
                first, second, third = snapshot["options"][:3]
                start = initial_request(snapshot, buyer_context="Original buyer context")
                start["selected_option_ids"] = [third["option_id"], first["option_id"]]
                case = ready(await client.post("/api/questions", json=start))
                command = round_request(case, texts=["Original preference", None])
                blocked = asyncio.create_task(client.post("/api/reviews", json=command))
                await asyncio.wait_for(entered.wait(), timeout=5)
                try:
                    pending = (await client.get("/api/case")).json()["case"]
                    assert pending["phase"] == "generating_review"
                    assert pending["last_operation"]["operation_id"] == command["operation_id"]
                    assert pending["revision"] == 1
                    assert pending["report"] is None
                    assert pending["rounds"][0]["answers"][0]["answer_text"] == "Original preference"
                    assert (await client.post("/api/reviews", json=command)).status_code == 409
                    alias = {**reference(pending), "operation_id": str(uuid4()), "submission": None}
                    assert (await client.post("/api/reviews", json=alias)).status_code == 409
                    assert (await client.post("/api/case/retry", json=retry_request(pending))).status_code == 409
                    invalid_key = {**command, "submission": None}
                    assert (await client.post("/api/reviews", json=invalid_key)).status_code == 422
                    assert len(calls) == 1

                    inputs, encoded = calls[0]
                    assert inputs.snapshot.snapshot_id == snapshot["snapshot_id"]
                    assert inputs.buyer_context == "Original buyer context"
                    assert inputs.selected_option_ids == start["selected_option_ids"]
                    assert [answer.question_text for answer in inputs.answers] == [
                        issued["prompt"] for issued in case["rounds"][0]["questions"]
                    ]
                    assert [answer.answer_text for answer in inputs.answers] == ["Original preference", None]
                    assert list(encoded) == [third["image_source_id"], first["image_source_id"]]
                    for index, option in [(2, third), (0, first)]:
                        mime, payload = encoded[option["image_source_id"]]
                        assert mime == "image/jpeg"
                        assert sha256(payload).hexdigest() == INVENTORY["options"][index]["image_sha256"]
                        assert (await client.get(_image_url(snapshot, option))).content == payload

                    if action == "upload":
                        def change(entries):
                            entries[INVENTORY["options"][0]["image"]] = entries[INVENTORY["options"][1]["image"]]

                        newest = (await _upload(client, _copy_zip(change))).json()["snapshot"]
                        assert newest["snapshot_id"] != snapshot["snapshot_id"]
                        assert (await client.get(_image_url(snapshot, first))).status_code == 404
                        image = await client.get(_image_url(newest, first))
                        assert sha256(image.content).hexdigest() == INVENTORY["options"][1]["image_sha256"]
                    elif action in {"context", "selection"}:
                        replacement = {**start, "operation_id": str(uuid4()), "previous_case": reference(pending)}
                        if action == "context":
                            replacement["buyer_context"] = "Updated buyer context"
                        else:
                            replacement["selected_option_ids"] = [second["option_id"]]
                        current = ready(await client.post("/api/questions", json=replacement))
                        assert current["case_id"] != case["case_id"]
                        assert current["report"] is None
                        assert (await client.get(_image_url(snapshot, first))).status_code == 200
                    else:
                        edit = round_request(pending, texts=["Updated preference", None])
                        current = ready(await client.post("/api/reviews", json=edit))
                        assert current["case_id"] == case["case_id"]
                        assert current["revision"] == 2
                        assert current["report"]["revision"] == 2
                        assert [answer.answer_text for answer in calls[1][0].answers] == ["Updated preference", None]
                        assert (await client.post("/api/reviews", json=alias)).status_code == 409
                    expected = (await client.get("/api/case")).json()
                finally:
                    release.set()
                assert (await asyncio.wait_for(blocked, timeout=5)).status_code == 409
                assert (await client.get("/api/case")).json() == expected
                assert (await client.post("/api/reviews", json=command)).status_code == 409
                assert len(calls) == (2 if action == "answers" else 1)

    asyncio.run(scenario())


@pytest.mark.parametrize("defect, code", [
    ("provider_failure", "analysis_failed"), ("exception", "analysis_failed"),
    ("result_case", "invalid_review"), ("report_snapshot", "invalid_review"),
    ("report_selection", "invalid_review"), ("report_revision", "invalid_review"),
    ("sent_scope", "invalid_review"),
])
def test_review_failure_preserves_answers_and_retries_original_input(tmp_path, defect, code):
    database = tmp_path / "case.db"
    failed_calls = []
    successful_calls = []

    async def defect_adapter(inputs, images):
        failed_calls.append((inputs.model_copy(deep=True), dict(images)))
        if defect == "provider_failure":
            return ReviewFailed(case_id=inputs.case_id, revision=inputs.revision, code="analysis_failed",
                                message="Review analysis is unavailable. Try again.")
        if defect == "result_case":
            return ReviewFailed(case_id="wrong-case", revision=inputs.revision, code="analysis_failed",
                                message="An unrelated operation failed.")
        if defect == "exception":
            inputs.answers[0].answer_text = "adapter replaced an answer"
            inputs.snapshot.sources.clear()
            raise RuntimeError("private-provider-details")
        ready_result = await offline_review(inputs, images)
        report = ready_result.report
        if defect == "report_snapshot":
            report.snapshot_id = "wrong-snapshot"
        elif defect == "report_selection":
            report.selected_option_ids.reverse()
        elif defect == "sent_scope":
            ready_result.sent_evidence.attached_image_ids = ()
        else:
            report.revision += 1
        return ready_result

    with TestClient(create_app(database, translator=stub, question_generator=drafts,
                               review_generator=defect_adapter)) as client:
        snapshot = _upload(client, RAW).json()["snapshot"]
        case = ready(client.post("/api/questions", json=initial_request(snapshot, buyer_context="Private note")))
        command = round_request(case, texts=["Keep this saved answer", None])
        response = client.post("/api/reviews", json=command)
        assert response.status_code == 200
        assert response.json()["status"] == "failed"
        assert response.json()["case_id"] == case["case_id"]
        assert response.json()["revision"] == 1
        assert response.json()["code"] == code
        assert "private-provider-details" not in response.text
        assert "An unrelated operation failed." not in response.text
        failed = client.get("/api/case").json()["case"]
        assert failed["phase"] == "failed"
        assert failed["last_operation"]["status"] == "failed"
        assert failed["last_operation"]["operation_id"] == command["operation_id"]
        assert failed["report"] is None
        assert failed["rounds"][0]["answers"] == command["submission"]["answers"]
        assert client.post("/api/reviews", json=command).json() == response.json()
        assert len(failed_calls) == 1

    async def recovered_adapter(inputs, images):
        successful_calls.append((inputs, dict(images)))
        return await offline_review(inputs, images)

    with TestClient(create_app(database, translator=stub, question_generator=drafts,
                               review_generator=recovered_adapter)) as client:
        assert client.get("/api/case").json()["case"] == failed
        assert successful_calls == []
        retry = retry_request(failed)
        reviewed = ready(client.post("/api/case/retry", json=retry))
        assert reviewed["phase"] == "reviewed"
        assert reviewed["report"]["revision"] == 1
        assert reviewed["last_operation"]["operation_id"] == command["operation_id"]
        assert reviewed["rounds"] == failed["rounds"]
        assert ready(client.post("/api/case/retry", json=retry)) == reviewed
        assert ready(client.post("/api/reviews", json=command)) == reviewed
        assert successful_calls == failed_calls


def test_report_does_not_attach_missing_or_unselected_images(tmp_path):
    def change(entries):
        del entries[INVENTORY["options"][0]["image"]]
        entries[INVENTORY["options"][1]["image"]] = b"not an image"

    calls = []

    async def review(inputs, images):
        calls.append((inputs, dict(images)))
        return await offline_review(inputs, images)

    with TestClient(create_app(tmp_path / "case.db", translator=stub, question_generator=drafts,
                               review_generator=review)) as client:
        snapshot = _upload(client, _copy_zip(change)).json()["snapshot"]
        first, second, third, fourth = snapshot["options"][:4]
        case = ready(client.post("/api/questions", json=initial_request(snapshot)))
        reviewed = ready(client.post("/api/reviews", json=round_request(case)))
        assert reviewed["phase"] == "reviewed"
        assert client.get(_image_url(snapshot, first)).status_code == 404
        assert client.get(_image_url(snapshot, second)).status_code == 404
        assert client.get(_image_url(snapshot, fourth)).status_code == 200

    (inputs, images), = calls
    sources = {source.source_id: source for source in inputs.snapshot.sources}
    assert sources[first["image_source_id"]].availability == "missing"
    assert sources[second["image_source_id"]].availability == "unreadable"
    assert sources[fourth["image_source_id"]].availability == "available"
    assert list(images) == [third["image_source_id"]]
    assert sha256(images[third["image_source_id"]][1]).hexdigest() == INVENTORY["options"][2]["image_sha256"]


def test_cancelled_review_releases_reservation_without_restart(tmp_path):
    async def scenario():
        entered = asyncio.Event()
        calls = []

        async def review(inputs, images):
            calls.append((inputs, dict(images)))
            if len(calls) == 1:
                entered.set()
                await asyncio.Future()
            return await offline_review(inputs, images)

        app = create_app(tmp_path / "case.db", translator=stub, question_generator=drafts, review_generator=review)
        async with app.router.lifespan_context(app):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                snapshot = (await _upload(client)).json()["snapshot"]
                case = ready(await client.post("/api/questions", json=initial_request(snapshot)))
                command = round_request(case, texts=["Already saved", None])
                blocked = asyncio.create_task(client.post("/api/reviews", json=command))
                await asyncio.wait_for(entered.wait(), timeout=5)
                blocked.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await blocked
                failed = (await client.get("/api/case")).json()["case"]
                assert failed["phase"] == "failed"
                assert failed["last_operation"]["code"] == "analysis_failed"
                assert failed["rounds"][0]["answers"][0]["answer_text"] == "Already saved"
                resumed = ready(await client.post("/api/case/retry", json=retry_request(failed)))
                assert resumed["phase"] == "reviewed"
                assert calls[0] == calls[1]

    asyncio.run(scenario())


def test_review_restarts_after_a_process_is_killed_with_a_reserved_report(tmp_path):
    database = tmp_path / "case.db"
    process = subprocess.run([sys.executable, "-c", """
import os
import sys
from fastapi.testclient import TestClient
from backend.main import create_app
from backend.tests.test_cases import _upload, drafts, initial_request, ready, round_request, stub

async def stop_mid_review(inputs, images):
    os._exit(23)

with TestClient(create_app(sys.argv[1], translator=stub, question_generator=drafts,
                           review_generator=stop_mid_review)) as client:
    listing = _upload(client).json()['snapshot']
    case = ready(client.post('/api/questions', json=initial_request(listing)))
    client.post('/api/reviews', json=round_request(case, texts=['Already saved', None]))
""", str(database)], check=False, timeout=15)
    assert process.returncode == 23

    # The old persisted input did not carry scope. Retry must read it from the actual questions.
    _replace_saved_case(database, lambda record: record["review_input"].pop("answer_option_ids"))
    calls = []

    async def review(inputs, images):
        calls.append((inputs, dict(images)))
        return await offline_review(inputs, images)

    with TestClient(create_app(database, translator=stub, question_generator=drafts,
                               review_generator=review)) as client:
        workspace = client.get("/api/case").json()
        snapshot, saved = workspace["snapshot"], workspace["case"]
        assert saved["phase"] == "failed"
        assert saved["revision"] == 1
        assert saved["last_operation"]["stage"] == "review"
        assert saved["last_operation"]["code"] == "analysis_failed"
        assert "Review generation was interrupted" in saved["last_operation"]["message"]
        assert saved["rounds"][0]["answers"][0]["answer_text"] == "Already saved"
        assert calls == []
        resumed = ready(client.post("/api/case/retry", json=retry_request(saved)))
        assert resumed["phase"] == "reviewed"
        assert resumed["rounds"] == saved["rounds"]
        (inputs, images), = calls
        assert inputs.snapshot.snapshot_id == snapshot["snapshot_id"]
        assert [answer.answer_text for answer in inputs.answers] == ["Already saved", None]
        assert inputs.answer_option_ids == {
            question["question_id"]: question["option_ids"] for question in saved["rounds"][0]["questions"]
        }
        assert len(images) == 3


def test_phase_two_handoff_can_generate_a_report_after_restart(tmp_path):
    database = tmp_path / "case.db"
    calls = []

    async def no_questions(inputs):
        return QuestionResult(reason="Enough context for a review.", questions=[])

    async def review(inputs, images):
        calls.append(inputs)
        return await offline_review(inputs, images)

    app_options = dict(translator=stub, question_generator=no_questions, review_generator=review)
    with TestClient(create_app(database, **app_options)) as client:
        snapshot = _upload(client).json()["snapshot"]
        case = ready(client.post("/api/questions", json=initial_request(snapshot)))

    storage = Store(database)
    try:
        generation, listing, current = storage.workspace()
        inputs = ReviewInput(**reference(case), snapshot=listing, selected_option_ids=case["selected_option_ids"],
                             buyer_context=case["buyer_context"], answers=[])
        handoff = CaseRecord(view=current.view.model_copy(update={
            "last_operation": CaseOperation(operation_id=str(uuid4()), stage="review", status="ready"),
        }), review_input=inputs)
        assert storage.save_case(generation, current, handoff)
    finally:
        storage.close()

    with TestClient(create_app(database, **app_options)) as client:
        restored = client.get("/api/case").json()["case"]
        assert restored["phase"] == "ready_for_review"
        assert restored["report"] is None
        assert restored["last_operation"]["stage"] == "review"
        assert restored["requirements"] == []
        assert calls == []
        command = {**reference(restored), "operation_id": str(uuid4()), "submission": None}
        reviewed = ready(client.post("/api/reviews", json=command))
        assert reviewed["phase"] == "reviewed"
        assert reviewed["revision"] == restored["revision"]
        assert calls == [inputs.model_copy(update={"requirements": []})]


def _replace_saved_case(database, change):
    with sqlite3.connect(database) as connection:
        (serialized,) = connection.execute("SELECT data FROM active_case").fetchone()
        record = json.loads(serialized)
        change(record)
        connection.execute("UPDATE active_case SET data = ?", (json.dumps(record),))


def test_review_preserves_sku_scope_of_issued_answer_at_provider_and_on_retry(tmp_path):
    database = tmp_path / "case.db"
    answer = "This option has to hold 720 ml."
    requests = []
    wrong_sku = True

    async def ask_about_first_sku(inputs):
        assert "answer_option_ids" not in inputs.model_dump()
        return QuestionResult(reason="Identify the first option's requirement.", questions=[QuestionDraft(
            prompt="What capacity is required for Option A?", why_ask="This applies to the first selected option.",
            option_ids=inputs.selected_option_ids[:1], source_ids=[], choices=[],
        )])

    async def review(inputs, images):
        option_a, option_b = inputs.selected_option_ids

        def serve(request):
            wire_input = json.loads(request.content)["input"][0]["content"][0]["text"]
            (wire_answer,) = json.loads(wire_input)["answers"]
            requests.append(wire_answer)
            payload = minimal_content(inputs)
            payload["buyer_requirements"] = [{
                "requirement_id": "only-option-a", "topic": "capacity",
                "input_ref": f"answer:{wire_answer['question_id']}", "quote": answer,
            }]
            payload["findings"] = [finding_selection(
                [option_b if wrong_sku else option_a], kind="requirement_unconfirmed",
                requirement_ids=["only-option-a"],
            )]
            return provider_response(request, payload)

        async with AsyncOpenAI(
            api_key="offline-test-placeholder", max_retries=0, base_url="https://api.openai.com/v1",
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(serve)),
        ) as sdk:
            return await generate_review(inputs, images, client=sdk)

    options = dict(translator=stub, question_generator=ask_about_first_sku, review_generator=review)
    with TestClient(create_app(database, **options)) as client:
        snapshot = _upload(client, RAW).json()["snapshot"]
        start = initial_request(snapshot)
        start["selected_option_ids"] = start["selected_option_ids"][:2]
        case = ready(client.post("/api/questions", json=start))
        issued, = case["rounds"][0]["questions"]
        response = client.post("/api/reviews", json=round_request(case, texts=[answer]))
        assert response.status_code == 200
        assert response.json()["code"] == "invalid_review"
        saved = client.get("/api/case").json()["case"]
        assert saved["phase"] == "failed"
        assert saved["rounds"][0]["answers"] == [{"question_id": issued["question_id"], "answer_text": answer}]

    expected_wire = {"question_id": issued["question_id"], "question_text": issued["prompt"],
                     "answer_text": answer, "option_ids": issued["option_ids"]}
    assert requests == [expected_wire, expected_wire]

    wrong_sku = False
    with TestClient(create_app(database, **options)) as client:
        assert client.get("/api/case").json()["case"] == saved
        assert len(requests) == 2
        reviewed = ready(client.post("/api/case/retry", json=retry_request(saved)))
        assert reviewed["rounds"] == saved["rounds"]
        finding, = reviewed["report"]["findings"]
        assert finding["option_ids"] == issued["option_ids"]
        requirement, = reviewed["report"]["buyer_requirements"]
        assert requirement["option_ids"] == issued["option_ids"]
        assert requests == [expected_wire] * 3

    with TestClient(create_app(database, **options)) as client:
        assert client.get("/api/case").json()["case"] == reviewed
        assert len(requests) == 3

    # Same case, same quote, and same snapshot are insufficient if the issued scope itself changed.
    _replace_saved_case(database, lambda record: record["view"]["rounds"][0]["questions"][0].update({
        "option_ids": start["selected_option_ids"],
    }))
    with TestClient(create_app(database, **options)) as client:
        recovered = client.get("/api/case").json()["case"]
        assert recovered["last_operation"]["code"] == "invalid_review"
        assert recovered["report"] is None
        assert reference(recovered) == reference(reviewed)
        assert recovered["rounds"][0]["answers"] == saved["rounds"][0]["answers"]
        assert len(requests) == 3


def test_saved_review_is_bound_to_the_issued_question_facet(tmp_path):
    database = tmp_path / "case.db"

    async def ask_about_decoration(inputs):
        return QuestionResult(reason="Check the requested decoration.", questions=[QuestionDraft(
            prompt="How should the artwork be applied?", why_ask="The method affects this order.",
            option_ids=inputs.selected_option_ids[:1], source_ids=[], choices=[], buyer_facet="decoration_method",
        )])

    options = dict(translator=stub, question_generator=ask_about_decoration, review_generator=offline_review)
    with TestClient(create_app(database, **options)) as client:
        snapshot = _upload(client, RAW).json()["snapshot"]
        asking = ready(client.post("/api/questions", json=initial_request(snapshot)))
        reviewed = ready(client.post("/api/reviews", json=round_request(asking, texts=["Screen printing is required."])))
        assert reviewed["phase"] == "reviewed"
        assert reviewed["rounds"][0]["questions"][0]["buyer_facet"] == "decoration_method"

    async def unexpected_generation(*_):
        pytest.fail("Reopening a report must not request generation.")

    options["review_generator"] = unexpected_generation
    with TestClient(create_app(database, **options)) as client:
        assert client.get("/api/case").json()["case"] == reviewed

    _replace_saved_case(database, lambda record: record["view"]["rounds"][0]["questions"][0].update(
        buyer_facet="surface_finish",
    ))
    with TestClient(create_app(database, **options)) as client:
        saved = client.get("/api/case").json()["case"]
        assert saved["last_operation"]["code"] == "invalid_review"
        assert saved["report"] is None
        assert saved["rounds"][0]["answers"] == reviewed["rounds"][0]["answers"]


@pytest.mark.parametrize("damage, expected_code", [
    ("legacy", "review_regeneration_required"),
    ("missing_envelope", "invalid_review"),
    ("missing_version_markers", "invalid_review"),
    ("unknown_version", "invalid_review"),
    ("malformed_claims", "invalid_review"),
    ("invalid_claim_reference", "invalid_review"),
    ("missing_text", "invalid_review"),
    ("unselected_attachment", "invalid_review"),
    ("changed_attachment_bytes", "invalid_review"),
    ("changed_input_binding", "invalid_review"),
    ("changed_public_report", "invalid_review"),
])
def test_saved_review_recovery_requires_bound_claims_and_never_calls_provider(tmp_path, damage, expected_code):
    database = tmp_path / "case.db"
    options = dict(translator=stub, question_generator=drafts, review_generator=offline_review)
    with TestClient(create_app(database, **options)) as client:
        snapshot = _upload(client, RAW).json()["snapshot"]
        start = initial_request(snapshot)
        start["selected_option_ids"] = [snapshot["options"][i]["option_id"] for i in (6, 0)]
        case = ready(client.post("/api/questions", json=start))
        command = round_request(case, texts=["  Keep the deadline exactly.  ", None])
        original = ready(client.post("/api/reviews", json=command))

    def change_report(record):
        saved_review = record["saved_review"]
        report = record["view"]["report"]
        if damage in {"legacy", "missing_envelope", "missing_version_markers"}:
            del record["saved_review"]
        if damage in {"legacy", "missing_version_markers"}:
            del report["contract_version"], report["claims"]
        if damage == "legacy":
            for comparison in report["comparisons"]:
                del comparison["shared_claim_ids"]
                for value in comparison["values"]:
                    del value["claim_ids"]
            for finding in report["findings"]:
                del finding["claim_ids"]
        elif damage == "unknown_version":
            saved_review["version"] = 999
        elif damage == "malformed_claims":
            saved_review["content"]["claims"] = {"not": "a claim list"}
        elif damage == "invalid_claim_reference":
            saved_review["content"]["comparisons"][0]["values"][0]["claim_ids"].append("nonexistent-claim")
        elif damage == "missing_text":
            saved_review["sent_evidence"]["text_ids"].pop()
        elif damage == "unselected_attachment":
            saved_review["sent_evidence"]["attached_image_ids"].append(snapshot["options"][1]["image_source_id"])
        elif damage == "changed_input_binding":
            saved_review["input_digest"] = "f" * 64
        elif damage == "changed_public_report":
            report["supplier_draft"] = "This draft was never validated."

    if damage == "changed_attachment_bytes":
        with sqlite3.connect(database) as connection:
            connection.execute("""
                UPDATE image_blobs SET payload = (
                    SELECT image_blobs.payload FROM image_sources JOIN image_blobs USING (digest)
                    WHERE image_sources.source_id = ?
                ) WHERE digest = (SELECT digest FROM image_sources WHERE source_id = ?)
            """, (snapshot["options"][0]["image_source_id"], snapshot["options"][6]["image_source_id"]))
    else:
        _replace_saved_case(database, change_report)

    async def unexpected_generation(inputs, images):
        pytest.fail("Restoring or replaying a saved report must not call the provider.")

    recovery_options = {**options, "review_generator": unexpected_generation}
    for _ in range(2):
        with TestClient(create_app(database, **recovery_options)) as client:
            workspace = client.get("/api/case").json()
            assert workspace["snapshot"] == snapshot
            recovered = workspace["case"]
            for field in ("case_id", "revision", "snapshot_id", "selected_option_ids", "buyer_context", "rounds"):
                assert recovered[field] == original[field]
            assert recovered["phase"] == "failed"
            assert recovered["report"] is None
            assert recovered["last_operation"]["code"] == expected_code
            assert client.post("/api/reviews", json=command).json()["code"] == expected_code
            alias = {**reference(recovered), "operation_id": str(uuid4()), "submission": None}
            assert client.post("/api/reviews", json=alias).json()["code"] == expected_code

    retries = []

    async def review(inputs, images):
        retries.append(inputs)
        return await offline_review(inputs, images)

    with TestClient(create_app(database, **{**options, "review_generator": review})) as client:
        reviewed = ready(client.post("/api/case/retry", json=retry_request(recovered)))
        assert reviewed["rounds"] == original["rounds"]
        assert reference(reviewed) == reference(original)
        assert reviewed["phase"] == "reviewed"
        assert reviewed["last_operation"]["operation_id"] == original["last_operation"]["operation_id"]
        if damage == "legacy":
            assert reviewed["report"]["buyer_requirements"] == []
            assert [item["status"] for item in reviewed["requirements"]] == ["unresolved", "unresolved"]
            assert [item["provenance"][0]["quote"] for item in reviewed["requirements"]] == ["  Keep the deadline exactly.  ", None]
            assert reviewed["report"]["open_preferences"]
        else:
            assert reviewed["report"] == original["report"]
        assert ready(client.post("/api/case/retry", json=retry_request(recovered))) == reviewed
        (inputs,) = retries
        assert [answer.answer_text for answer in inputs.answers] == ["  Keep the deadline exactly.  ", None]

    with TestClient(create_app(database, **recovery_options)) as client:
        assert client.get("/api/case").json() == {"snapshot": snapshot, "case": reviewed}


def test_unreadable_base_case_neither_becomes_a_report_failure_nor_resets_workspace(tmp_path):
    database = tmp_path / "case.db"
    options = dict(translator=stub, question_generator=drafts, review_generator=offline_review)
    with TestClient(create_app(database, **options)) as client:
        snapshot = _upload(client, RAW).json()["snapshot"]
        case = ready(client.post("/api/questions", json=initial_request(snapshot)))

    _replace_saved_case(database, lambda record: record["view"].update({"rounds": [{"unexpected": "data"}]}))
    with closing(sqlite3.connect(database)) as connection:
        before = connection.execute("SELECT data FROM active_case").fetchone()
    with pytest.raises(sqlite3.DatabaseError, match="saved workspace"):
        with TestClient(create_app(database, **options)):
            pytest.fail("Startup should surface a storage error for an unreadable case.")

    storage = Store(database)
    try:
        assert storage.connection.execute("SELECT data FROM active_case").fetchone() == before
        assert storage.snapshot().snapshot_id == case["snapshot_id"]
        assert storage.image(snapshot["snapshot_id"], snapshot["options"][0]["image_source_id"]) is not None
    finally:
        storage.close()
