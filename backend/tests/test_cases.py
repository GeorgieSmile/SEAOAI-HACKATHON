import asyncio
from hashlib import sha256
import subprocess
import sys
from uuid import uuid4

from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
import pytest

from backend.ai import QuestionGenerationError
from backend.contracts import QuestionDraft, QuestionResult
from backend.main import create_app
from backend.tests.offline_server import offline_review
from backend.tests.test_imports import INVENTORY, RAW, _image_url, _upload, stub


PROMPTS = {
    1: ("What finish do you require?", "How will the cups be used?"),
    2: ("Would you consider a custom order?",),
    3: ("Do you need a pre-order sample?",),
}


async def drafts(request):
    structured = (
        {"topic": "other", "requirement_ids": [], "choice_effects": []}
        if request.requirements is not None else {}
    )
    return QuestionResult(reason="These answers affect the purchase review.", questions=[
        QuestionDraft(
            prompt=prompt, why_ask="This determines what you need from the supplier.",
            option_ids=request.selected_option_ids, source_ids=[], choices=[],
            **structured,
        )
        for prompt in PROMPTS[request.round_number]
    ])


def reference(case):
    return {field: case[field] for field in ("case_id", "revision")}


def initial_request(snapshot, *, buyer_context="", previous_case=None):
    return {
        "operation_id": str(uuid4()), "snapshot_id": snapshot["snapshot_id"],
        "previous_case": previous_case, "selected_option_ids": [item["option_id"] for item in snapshot["options"][:3]],
        "buyer_context": buyer_context,
    }


def round_request(case, index=-1, texts=None):
    round = case["rounds"][index]
    questions = round["questions"]
    return {
        "operation_id": str(uuid4()), **reference(case), "submission": {
            "round_id": round["round_id"], "answers": [
                {"question_id": item["question_id"], "answer_text": text}
                for item, text in zip(questions, texts if texts is not None else [None] * len(questions), strict=True)
            ],
        },
    }


def ready(response):
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ready", payload
    return payload["case"]


def confirm_command(case, operation_id=None):
    return {**reference(case), "operation_id": operation_id or str(uuid4()), "confirm": True}


def reopen_brief(client, case):
    payload = {**reference(case), "operation_id": str(uuid4()), "submission": None}
    return ready(client.post("/api/brief", json=payload))


def finish_review(client, command):
    """Save a changed brief using its real endpoint, then explicitly confirm the saved revision."""
    if command.get("submission") is not None or command.get("order_clarification") is not None:
        prepared = client.post("/api/brief", json={**command, "operation_id": str(uuid4())})
        if prepared.status_code != 200 or prepared.json()["status"] != "ready":
            return prepared
        case = prepared.json()["case"]
        if case["phase"] != "ready_for_review":
            return prepared
    else:
        case = command
    return client.post("/api/reviews", json=confirm_command(case, command["operation_id"]))


def test_zero_question_case_generates_and_recovers_the_same_report_with_no_answers(tmp_path):
    database = tmp_path / "case.db"
    generation_requests = []
    review_requests = []

    async def no_questions(request):
        generation_requests.append(request)
        return QuestionResult(reason="Your context covers the remaining preferences.", questions=[])

    async def generate_review(request, images):
        review_requests.append(request)
        return await offline_review(request, images)

    app_options = dict(translator=stub, question_generator=no_questions, review_generator=generate_review)
    with TestClient(create_app(database, **app_options)) as client:
        snapshot = _upload(client, RAW).json()["snapshot"]
        start = initial_request(snapshot, buyer_context="  Intended for resale.  ")
        start["selected_option_ids"].reverse()
        case = ready(client.post("/api/questions", json=start))
        assert case["rounds"] == []
        assert case["phase"] == "ready_for_review"
        assert case["reason"] == "Your context covers the remaining preferences."
        assert ready(client.post("/api/questions", json=start)) == case

        review = {"operation_id": str(uuid4()), **reference(case), "submission": None}
        reviewed = ready(finish_review(client, review))
        assert reference(reviewed) == reference(case)
        assert reviewed["last_operation"]["stage"] == "review"
        assert reviewed["last_operation"]["status"] == "ready"
        assert reviewed["phase"] == "reviewed"
        assert reviewed["report"]["selected_option_ids"] == start["selected_option_ids"]
        assert "review_input" not in reviewed
        assert "saved_review" not in reviewed
        assert ready(finish_review(client, review)) == reviewed
        alias = {**review, "operation_id": str(uuid4())}
        assert ready(finish_review(client, alias)) == reviewed
        assert len(generation_requests) == 1

    with TestClient(create_app(database, **app_options)) as client:
        assert client.get("/api/case").json() == {"snapshot": snapshot, "case": reviewed}
        assert ready(finish_review(client, alias)) == reviewed
        assert ready(finish_review(client, review)) == reviewed
    inputs, = review_requests
    assert inputs.snapshot.snapshot_id == snapshot["snapshot_id"]
    assert inputs.selected_option_ids == start["selected_option_ids"]
    assert inputs.buyer_context == start["buyer_context"]
    assert inputs.answers == []


def test_answers_follow_issued_order_limit_rounds_and_discard_an_edited_branch(tmp_path):
    database = tmp_path / "case.db"
    generation_requests = []
    review_requests = []

    async def questionnaire(request):
        generation_requests.append(request)
        return await drafts(request)

    async def generate_review(request, images):
        review_requests.append(request)
        return await offline_review(request, images)

    with TestClient(create_app(database, translator=stub, question_generator=questionnaire,
                               review_generator=generate_review)) as client:
        snapshot = _upload(client).json()["snapshot"]
        one = ready(client.post("/api/questions", json=initial_request(snapshot)))
        first_round = one["rounds"][0]
        request = round_request(one, texts=["  Screen print if available.  ", " \u3000 "])
        request["submission"]["answers"].reverse()
        two = ready(client.post("/api/questions/continue", json=request))
        assert two["revision"] == 1
        flattened = [{
            "question_id": question["question_id"], "question_text": question["prompt"], "answer_text": text,
        } for question, text in zip(first_round["questions"], ["  Screen print if available.  ", None], strict=True)]
        assert [answer.model_dump() for answer in generation_requests[-1].answers] == flattened
        reordered = {**request, "submission": {
            **request["submission"], "answers": [request["submission"]["answers"][1], {
                **request["submission"]["answers"][0], "answer_text": None,
            }],
        }}
        assert ready(client.post("/api/questions/continue", json=reordered)) == two
        changed_replay = {**request, "submission": {**request["submission"], "answers": [
            {**entry, "answer_text": "A different response"} for entry in request["submission"]["answers"]
        ]}}
        assert client.post("/api/questions/continue", json=changed_replay).status_code == 422
        assert client.post("/api/questions/continue", json={**request, "operation_id": str(uuid4())}).status_code == 409

        three = ready(client.post("/api/questions/continue", json=round_request(two)))
        assert three["rounds"][1]["answers"][0]["answer_text"] is None
        same = round_request(three, 0, texts=["  Screen print if available.  ", None])
        assert ready(client.post("/api/questions/continue", json=same)) == three
        assert ready(client.post("/api/questions/continue", json=same)) == three
        assert ready(finish_review(client, {**same, "operation_id": str(uuid4())})) == three

        capped = ready(client.post("/api/questions/continue", json=round_request(three)))
        assert capped["phase"] == "ready_for_review"
        assert capped["revision"] == 3
        assert [item.round_number for item in generation_requests] == [1, 2, 3]

        edit = round_request(capped, 0, texts=["Applied graphics are acceptable.", None])
        edited = ready(client.post("/api/questions/continue", json=edit))
        assert edited["revision"] == 4
        assert len(edited["rounds"]) == 2
        assert edited["rounds"][0]["questions"] == first_round["questions"]
        assert edited["rounds"][1]["round_id"] != capped["rounds"][1]["round_id"]
        assert edited["rounds"][1]["questions"][0]["question_id"] != capped["rounds"][1]["questions"][0]["question_id"]
        assert len(generation_requests[-1].answers) == 2
        discarded = {**round_request(capped, 1), **reference(edited)}
        assert client.post("/api/questions/continue", json=discarded).status_code == 422

        empty_review = {"operation_id": str(uuid4()), **reference(edited), "submission": None}
        assert finish_review(client, empty_review).status_code == 422
        review = round_request(edited, texts=["   "])
        reviewed = ready(finish_review(client, review))
        assert reviewed["revision"] == 5
        assert reviewed["phase"] == "reviewed"
        assert [item.round_number for item in generation_requests] == [1, 2, 3, 2]

        next_edit = round_request(reviewed, 0, texts=["Ready-made options are required.", None])
        updated = ready(client.post("/api/questions/continue", json=next_edit))
        assert updated["revision"] == 6
        assert updated["report"] is None
        assert finish_review(client, review).status_code == 409
        assert client.get("/api/case").json()["case"] == updated

    inputs, = review_requests
    assert inputs.case_id == edited["case_id"]
    assert inputs.revision == 5
    assert [item.question_text for item in inputs.answers] == [*PROMPTS[1], *PROMPTS[2]]
    assert [item.answer_text for item in inputs.answers] == ["Applied graphics are acceptable.", None, None]


def test_client_cannot_supply_unissued_answers_or_evidence(tmp_path):
    generation_requests = []

    async def questionnaire(request):
        generation_requests.append(request)
        return await drafts(request)

    with TestClient(create_app(tmp_path / "case.db", translator=stub, question_generator=questionnaire)) as client:
        snapshot = _upload(client).json()["snapshot"]
        start = initial_request(snapshot)
        for changes in ({"snapshot": snapshot}, {"selected_option_ids": ["foreign"]}, {"buyer_context": "x" * 2_001}):
            assert client.post("/api/questions", json={**start, **changes}).status_code == 422
        assert generation_requests == []
        case = ready(client.post("/api/questions", json=start))
        base = round_request(case)
        submission = base["submission"]
        first, second = submission["answers"]
        invalid = [
            {"round_id": "unissued"}, {"answers": [first]}, {"answers": [first, first]},
            {"answers": [first, {**second, "question_id": "unissued"}]},
            {"answers": [first, {**second, "question_text": "Forged prompt"}]},
            {"answers": [first, {**second, "answer_text": "x" * 1_001}]},
            {"image_file": "fake.png"}, {"sources": []},
        ]
        for changes in invalid:
            response = client.post("/api/questions/continue", json={**base, "submission": {**submission, **changes}})
            assert response.status_code == 422
            assert response.json()["case_id"] == case["case_id"]
        assert client.get("/api/case").json()["case"] == case
        assert len(generation_requests) == 1


def test_round_and_answer_capacity_is_fifteen_without_a_fourth_call(tmp_path):
    database = tmp_path / "case.db"
    requested_rounds = []
    review_requests = []

    async def five_questions(request):
        requested_rounds.append(request.round_number)
        return QuestionResult(reason="These answers inform what you ask the supplier.", questions=[
            QuestionDraft(
                prompt=f"What is your requirement for product preference {request.round_number}-{number}?",
                why_ask="This affects what you ask the supplier.", option_ids=request.selected_option_ids,
                source_ids=[], choices=[],
            )
            for number in range(1, 6)
        ])

    async def generate_review(request, images):
        review_requests.append(request)
        return await offline_review(request, images)

    with TestClient(create_app(database, translator=stub, question_generator=five_questions,
                               review_generator=generate_review)) as client:
        snapshot = _upload(client).json()["snapshot"]
        case = ready(client.post("/api/questions", json=initial_request(snapshot)))
        for number in range(1, 4):
            assert case["rounds"][-1]["round_number"] == number
            assert len(case["rounds"][-1]["questions"]) == 5
            case = ready(client.post("/api/questions/continue", json=round_request(case)))
        assert case["phase"] == "ready_for_review"
        ready(finish_review(client, {"operation_id": str(uuid4()), **reference(case), "submission": None}))
        assert requested_rounds == [1, 2, 3]

    inputs, = review_requests
    assert len(inputs.answers) == len({item.question_id for item in inputs.answers}) == 15
    assert inputs.revision == 3
    assert all(answer.answer_text is None for answer in inputs.answers)


@pytest.mark.parametrize("defect", ["too-many-questions", "image-reference", "wrong-sku-attribution"])
def test_invalid_provider_questions_leave_the_issue_retryable(tmp_path, defect):
    async def invalid(request):
        base = await drafts(request)
        if defect == "too-many-questions":
            return base.model_copy(update={"questions": base.questions * 3})
        option_a = request.selected_option_ids[0]
        if defect == "wrong-sku-attribution":
            draft = base.questions[0].model_copy(update={
                "option_ids": [option_a],
                "source_ids": [request.snapshot.option_name_sources()[option_a].source_id],
                "why_ask": "Option B's name describes applied-picture artwork.",
            })
        else:
            image = next(option.image_source_id for option in request.snapshot.options if option.option_id == option_a)
            draft = base.questions[0].model_copy(update={"source_ids": [image]})
        return base.model_copy(update={"questions": [draft]})

    database = tmp_path / "case.db"
    with TestClient(create_app(database, translator=stub, question_generator=invalid)) as client:
        snapshot = _upload(client).json()["snapshot"]
        start = initial_request(snapshot)
        if defect == "wrong-sku-attribution":
            by_name = {option["name_original"]: option["option_id"] for option in snapshot["options"]}
            start["selected_option_ids"] = [by_name["暮色316-贴画粉蓝"], by_name["暮色316-字母粉蓝"]]
        failure = client.post("/api/questions", json=start).json()
        assert failure["status"] == "failed"
        assert failure["code"] == "invalid_questions"
        case = client.get("/api/case").json()["case"]
        assert case["phase"] == "failed"
        assert case["rounds"] == []
        assert case["revision"] == 0

    with TestClient(create_app(database, translator=stub, question_generator=drafts)) as client:
        assert client.get("/api/case").json()["case"] == case
        retry = {"operation_id": case["last_operation"]["operation_id"], **reference(case)}
        resumed = ready(client.post("/api/case/retry", json=retry))
        assert resumed["rounds"][0]["round_number"] == 1


def test_failed_round_preserves_answers_and_retries_the_same_input_after_restart(tmp_path):
    database = tmp_path / "case.db"
    failed_inputs = []

    async def fails_after_first_round(request):
        if request.round_number == 2:
            failed_inputs.append(request)
            raise QuestionGenerationError("analysis_failed", "Try preparing questions again.")
        return await drafts(request)

    with TestClient(create_app(database, translator=stub, question_generator=fails_after_first_round)) as client:
        snapshot = _upload(client).json()["snapshot"]
        case = ready(client.post("/api/questions", json=initial_request(snapshot)))
        command = round_request(case, texts=["  This answer is already saved.  ", None])
        failure = client.post("/api/questions/continue", json=command)
        assert failure.status_code == 200
        assert failure.json()["code"] == "analysis_failed"
        assert failure.json()["revision"] == 1
        assert client.post("/api/questions/continue", json=command).json() == failure.json()
        failed = client.get("/api/case").json()["case"]
        alias = {**command, "operation_id": str(uuid4()), **reference(failed)}
        assert client.post("/api/questions/continue", json=alias).json() == failure.json()
        assert len(failed_inputs) == 1

    retried_inputs = []

    async def recovered_provider(request):
        retried_inputs.append(request)
        return await drafts(request)

    with TestClient(create_app(database, translator=stub, question_generator=recovered_provider)) as client:
        assert client.get("/api/case").json() == {"snapshot": snapshot, "case": failed}
        image = client.get(_image_url(snapshot, snapshot["options"][0]))
        assert sha256(image.content).hexdigest() == INVENTORY["options"][0]["image_sha256"]
        assert not retried_inputs
        retry = {"operation_id": command["operation_id"], **reference(failed)}
        continued = ready(client.post("/api/case/retry", json=retry))
        assert continued["revision"] == failed["revision"]
        assert continued["rounds"][0] == failed["rounds"][0]
        assert continued["rounds"][1]["answers"] is None
        assert ready(client.post("/api/case/retry", json=retry)) == continued
        assert retried_inputs == failed_inputs


def test_configuration_failure_can_be_retried_after_the_provider_is_repaired(tmp_path):
    database = tmp_path / "case.db"

    async def no_access(request):
        raise QuestionGenerationError("analysis_failed", "Check API model access.", retryable=False)

    with TestClient(create_app(database, translator=stub, question_generator=no_access)) as client:
        snapshot = _upload(client).json()["snapshot"]
        start = initial_request(snapshot)
        error = client.post("/api/questions", json=start).json()
        assert error["status"] == "failed"
        state = client.get("/api/case").json()["case"]
        alias = {**start, "operation_id": str(uuid4()), "previous_case": reference(state)}
        assert client.post("/api/questions", json=alias).json() == error

    with TestClient(create_app(database, translator=stub, question_generator=drafts)) as client:
        restored = client.get("/api/case").json()["case"]
        assert restored == state
        retried = ready(client.post("/api/case/retry", json={"operation_id": start["operation_id"], **reference(state)}))
        assert reference(retried) == reference(state)
        assert retried["rounds"][0]["round_number"] == 1
        assert retried["phase"] == "awaiting_answers"


@pytest.mark.parametrize("action", ["review", "replace", "reset", "import"])
def test_in_flight_question_cannot_publish_into_a_superseding_state(tmp_path, action):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def slow_first_selection(request):
            if request.buyer_context == "first":
                entered.set()
                await release.wait()
            return await drafts(request)

        app = create_app(tmp_path / "case.db", translator=stub, question_generator=slow_first_selection,
                         review_generator=offline_review)
        async with app.router.lifespan_context(app):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                snapshot = (await _upload(client)).json()["snapshot"]
                start = initial_request(snapshot, buyer_context="first")
                blocked = asyncio.create_task(client.post("/api/questions", json=start))
                await asyncio.wait_for(entered.wait(), timeout=5)
                try:
                    case = (await client.get("/api/case")).json()["case"]
                    assert case["phase"] == "preparing_questions"
                    assert (await client.post("/api/questions", json=start)).status_code == 409
                    alias = {**start, "operation_id": str(uuid4()), "previous_case": reference(case)}
                    assert (await client.post("/api/questions", json=alias)).status_code == 409
                    if action == "review":
                        prepared = ready(await client.post("/api/brief", json={
                            **reference(case), "operation_id": str(uuid4()), "submission": None,
                        }))
                        review_ready = ready(await client.post("/api/reviews", json=confirm_command(prepared)))
                        assert review_ready["last_operation"]["stage"] == "review"
                    elif action == "replace":
                        replacement = initial_request(snapshot, buyer_context="second", previous_case=reference(case))
                        replacement["selected_option_ids"].reverse()
                        new_case = ready(await client.post("/api/questions", json=replacement))
                        assert new_case["case_id"] != case["case_id"]
                        assert (await client.post("/api/questions", json=start)).status_code == 409
                    elif action == "reset":
                        reset = {"snapshot_id": snapshot["snapshot_id"], "previous_case": reference(case)}
                        assert (await client.post("/api/case/reset", json={**reset, "previous_case": None})).status_code == 409
                        assert (await client.post("/api/case/reset", json=reset)).json() == {"snapshot": None, "case": None}
                        assert (await client.get(_image_url(snapshot, snapshot["options"][0]))).status_code == 404
                    else:
                        newest = (await _upload(client, RAW)).json()["snapshot"]
                        assert newest["snapshot_id"] != snapshot["snapshot_id"]
                    expected_state = (await client.get("/api/case")).json()
                finally:
                    release.set()
                assert (await asyncio.wait_for(blocked, timeout=5)).status_code == 409
                assert (await client.get("/api/case")).json() == expected_state

    asyncio.run(scenario())


def test_answers_saved_during_one_round_cannot_be_replaced_by_its_late_questions(tmp_path):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def delayed_branch(request):
            if request.revision == 1:
                entered.set()
                await release.wait()
            return await drafts(request)

        app = create_app(tmp_path / "case.db", translator=stub, question_generator=delayed_branch)
        async with app.router.lifespan_context(app):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                snapshot = (await _upload(client)).json()["snapshot"]
                case = ready(await client.post("/api/questions", json=initial_request(snapshot)))
                command = round_request(case, texts=["Old preference", None])
                first_branch = asyncio.create_task(client.post("/api/questions/continue", json=command))
                await asyncio.wait_for(entered.wait(), timeout=5)
                try:
                    pending = (await client.get("/api/case")).json()["case"]
                    assert pending["revision"] == 1
                    assert pending["rounds"][0]["answers"][0]["answer_text"] == "Old preference"
                    edit = round_request(pending, texts=["New preference", None])
                    current = ready(await client.post("/api/questions/continue", json=edit))
                    assert current["revision"] == 2
                    assert current["rounds"][0]["answers"][0]["answer_text"] == "New preference"
                finally:
                    release.set()
                assert (await asyncio.wait_for(first_branch, timeout=5)).status_code == 409
                assert (await client.get("/api/case")).json()["case"] == current

    asyncio.run(scenario())


def test_cancelled_question_reservation_becomes_retryable_without_restart(tmp_path):
    async def scenario():
        entered = asyncio.Event()
        calls = []

        async def block_first(request):
            calls.append(request)
            if len(calls) == 1:
                entered.set()
                await asyncio.Future()
            return await drafts(request)

        app = create_app(tmp_path / "case.db", translator=stub, question_generator=block_first)
        async with app.router.lifespan_context(app):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                snapshot = (await _upload(client)).json()["snapshot"]
                start = initial_request(snapshot)
                blocked = asyncio.create_task(client.post("/api/questions", json=start))
                await asyncio.wait_for(entered.wait(), timeout=5)
                blocked.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await blocked
                interrupted = (await client.get("/api/case")).json()["case"]
                assert interrupted["last_operation"]["status"] == "failed"
                retry = {"operation_id": start["operation_id"], **reference(interrupted)}
                resumed = ready(await client.post("/api/case/retry", json=retry))
                assert resumed["phase"] == "awaiting_answers"
                assert calls[0] == calls[1]

    asyncio.run(scenario())


def test_restart_of_a_process_killed_mid_round_recovers_saved_answers_and_retries(tmp_path):
    database = tmp_path / "case.db"
    process = subprocess.run([sys.executable, "-c", """
import os
import sys
from fastapi.testclient import TestClient
from backend.main import create_app
from backend.tests.test_cases import _upload, drafts, initial_request, ready, round_request, stub

async def stop_mid_round(question_input):
    if question_input.round_number == 2:
        os._exit(23)
    return await drafts(question_input)

with TestClient(create_app(sys.argv[1], translator=stub, question_generator=stop_mid_round)) as client:
    listing = _upload(client).json()['snapshot']
    case = ready(client.post('/api/questions', json=initial_request(listing)))
    client.post('/api/questions/continue', json=round_request(case, texts=['Already saved', None]))
""", str(database)], check=False, timeout=15)
    assert process.returncode == 23

    restored_inputs = []

    async def after_restart(question_input):
        restored_inputs.append(question_input)
        return await drafts(question_input)

    with TestClient(create_app(database, translator=stub, question_generator=after_restart)) as client:
        workspace = client.get("/api/case").json()
        snapshot, saved = workspace["snapshot"], workspace["case"]
        assert saved["phase"] == "failed"
        assert saved["revision"] == 1
        assert saved["last_operation"]["code"] == "analysis_failed"
        assert "interrupted" in saved["last_operation"]["message"]
        assert saved["rounds"][0]["answers"][0]["answer_text"] == "Already saved"
        assert client.get(_image_url(snapshot, snapshot["options"][0])).status_code == 200
        assert restored_inputs == []
        retry = {"operation_id": saved["last_operation"]["operation_id"], **reference(saved)}
        resumed = ready(client.post("/api/case/retry", json=retry))
        assert resumed["revision"] == 1
        assert resumed["rounds"][1]["round_number"] == 2
        assert [answer.answer_text for answer in restored_inputs[0].answers] == ["Already saved", None]
