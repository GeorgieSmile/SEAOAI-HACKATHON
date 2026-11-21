import asyncio
from contextlib import AsyncExitStack

from httpx import ASGITransport, AsyncClient

from backend.main import create_app
from backend.report_models import ReviewFailed
from backend.review_queue import ReviewQueue
from backend.state import Store
from backend.tests.offline_server import offline_review
from backend.tests.test_cases import confirm_command, drafts, initial_request, ready, reference, round_request
from backend.tests.test_imports import _upload, stub


async def open_workspace(stack, database, queue, review_generator):
    app = create_app(
        database, translator=stub, question_generator=drafts, review_generator=review_generator, review_queue=queue,
    )
    await stack.enter_async_context(app.router.lifespan_context(app))
    return await stack.enter_async_context(AsyncClient(transport=ASGITransport(app=app), base_url="http://test"))


async def prepared_review(client, context):
    snapshot = (await _upload(client)).json()["snapshot"]
    case = ready(await client.post("/api/questions", json=initial_request(snapshot, buyer_context=context)))
    case = ready(await client.post("/api/brief", json=round_request(case, texts=["Keep this answer", None])))
    return confirm_command(case)


async def queue_review(client, context):
    command = await prepared_review(client, context)
    accepted = await client.post("/api/reviews", json=command)
    assert accepted.status_code == 202
    assert accepted.json()["case"]["last_operation"]["status"] == "queued"
    return command


def test_saved_queue_restores_fifo_and_skips_replaced_workspace(tmp_path):
    async def scenario():
        databases = {name: tmp_path / f"{name}.sqlite3" for name in ("active", "first", "second", "replaced")}
        began = asyncio.Event()
        original_input = []

        async def interrupted(inputs, images):
            original_input.append((inputs, dict(images)))
            began.set()
            await asyncio.Event().wait()

        first_queue = ReviewQueue(max_workers=1)
        async with AsyncExitStack() as apps:
            try:
                original = await open_workspace(apps, databases["active"], first_queue, interrupted)
                active_command = await queue_review(original, "active")
                first_queue.start()
                await asyncio.wait_for(began.wait(), 5)
                commands = {}
                for name in ("first", "second", "replaced"):
                    workspace = await open_workspace(apps, databases[name], first_queue, interrupted)
                    commands[name] = await queue_review(workspace, name)
                assert len(original_input) == 1
            finally:
                await first_queue.close()

        assert not Store.has_queued_review(databases["active"])
        assert all(Store.has_queued_review(databases[name]) for name in ("first", "second", "replaced"))

        calls = []
        finished = asyncio.Event()

        async def review(inputs, images):
            calls.append(inputs.buyer_context)
            if inputs.case_id == active_command["case_id"]:
                assert (inputs, dict(images)) == original_input[0]
            result = await offline_review(inputs, images)
            if len(calls) == 3:
                finished.set()
            return result

        restored_queue = ReviewQueue(max_workers=1)
        async with AsyncExitStack() as apps:
            try:
                # Workspace discovery order is independent of enqueue order.
                clients = {
                    name: await open_workspace(apps, databases[name], restored_queue, review)
                    for name in ("replaced", "second", "first", "active")
                }
                active = (await clients["active"].get("/api/case")).json()["case"]
                assert active["phase"] == "failed"
                assert active["last_operation"]["code"] == "analysis_failed"
                retry = {
                    **reference(active), "operation_id": active_command["operation_id"],
                    "retry_token": active["last_operation"]["retry_token"],
                }
                for _ in range(2):
                    accepted = await clients["active"].post("/api/case/retry", json=retry)
                    assert accepted.status_code == 202
                    assert accepted.json()["case"]["last_operation"]["operation_id"] == retry["operation_id"]

                first = clients["first"]
                repeated = await first.post("/api/reviews", json=commands["first"])
                alias = await first.post("/api/reviews", json=confirm_command(commands["first"]))
                assert repeated.status_code == alias.status_code == 202
                assert repeated.json() == alias.json()
                assert alias.json()["case"]["last_operation"]["operation_id"] == commands["first"]["operation_id"]

                replaced = (await clients["replaced"].get("/api/case")).json()["case"]
                reset = await clients["replaced"].post("/api/case/reset", json={
                    "snapshot_id": replaced["snapshot_id"], "previous_case": reference(replaced),
                })
                assert reset.status_code == 200

                restored_queue.start()
                await asyncio.wait_for(finished.wait(), 5)
                assert calls == ["first", "second", "active"]
                for name in calls:
                    case = (await clients[name].get("/api/case")).json()["case"]
                    assert case["phase"] == "reviewed"
                    assert case["rounds"][0]["answers"][0]["answer_text"] == "Keep this answer"
                    assert case["report"]["case_id"] == case["case_id"]
                assert (await clients["replaced"].get("/api/case")).json() == {"snapshot": None, "case": None}
            finally:
                await restored_queue.close()

    asyncio.run(scenario())


def test_delayed_retry_cannot_retry_a_later_failure(tmp_path):
    async def scenario():
        database = tmp_path / "case.sqlite3"
        attempted = asyncio.Queue()
        calls = 0

        async def review(inputs, images):
            nonlocal calls
            calls += 1
            attempted.put_nowait(calls)
            if calls <= 2:
                return ReviewFailed(
                    case_id=inputs.case_id, revision=inputs.revision, code="analysis_failed", message="Try again.",
                )
            return await offline_review(inputs, images)

        # A saved failure from before the token existed can still be retried.
        async with AsyncExitStack() as apps:
            original = await open_workspace(apps, database, None, review)
            confirmed = await prepared_review(original, "Keep saved context")
            assert (await original.post("/api/reviews", json=confirmed)).json()["status"] == "failed"
            assert await attempted.get() == 1
            previous = (await original.get("/api/case")).json()["case"]
            assert "retry_token" not in previous["last_operation"]

        queue = ReviewQueue(max_workers=1)
        async with AsyncExitStack() as apps:
            try:
                hosted = await open_workspace(apps, database, queue, review)
                queue.start()
                first = (await hosted.get("/api/case")).json()["case"]
                retry_command = {**reference(first), "operation_id": confirmed["operation_id"]}
                assert (await hosted.post("/api/case/retry", json=retry_command)).status_code == 422
                retry_command["retry_token"] = first["last_operation"]["retry_token"]
                accepted = await hosted.post("/api/case/retry", json=retry_command)
                assert accepted.status_code == 202
                assert "retry_token" not in accepted.json()["case"]["last_operation"]
                assert await asyncio.wait_for(attempted.get(), 5) == 2

                latest = (await hosted.get("/api/case")).json()["case"]
                operation = latest["last_operation"]
                assert operation["status"] == "failed"
                assert operation["operation_id"] == retry_command["operation_id"]
                assert operation["retry_token"] != retry_command["retry_token"]

                replay = await hosted.post("/api/case/retry", json=retry_command)
                assert replay.json()["status"] == "failed"
                assert (await hosted.get("/api/case")).json()["case"] == latest
                assert calls == 2

                next_retry = {**retry_command, "retry_token": operation["retry_token"]}
                assert (await hosted.post("/api/case/retry", json=next_retry)).status_code == 202
                assert await asyncio.wait_for(attempted.get(), 5) == 3
                assert (await hosted.get("/api/case")).json()["case"]["phase"] == "reviewed"
            finally:
                await queue.close()

    asyncio.run(scenario())
