"""Exercise real hosted HTTP, independent browser sessions, and process recovery."""

import asyncio
from collections import Counter
from contextlib import AsyncExitStack, contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import socket
from statistics import median
import subprocess
import sys
from time import monotonic, sleep
from urllib.parse import unquote
from uuid import uuid4
from zipfile import ZipFile

from bs4 import BeautifulSoup
import httpx
import pytest

from backend.tests.test_cases import confirm_command, reference, round_request
from backend.tests.test_imports import RAW, _image_url, _upload


ROOT = Path(__file__).resolve().parents[2]


def digest(value):
    return sha256(value.encode() if isinstance(value, str) else value).hexdigest()


@pytest.fixture(scope="module")
def archive():
    path = Path(os.environ["PURCHASE_TEST_ZIP"]) if "PURCHASE_TEST_ZIP" in os.environ else RAW
    with ZipFile(path, metadata_encoding="utf-8") as zip_file:
        members = zip_file.namelist()
        page, = (name for name in members if "/" not in name and name.endswith(".html"))
        document = BeautifulSoup(zip_file.read(page), "html.parser")
        images = {
            image["data-sku"]: digest(zip_file.read(unquote(image["src"])))
            for image in document.select("img[data-sku][src]")
        }
    assert len(images) == 12
    return path.read_bytes(), images


class HostedServer:
    def __init__(self, tmp_path):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]
        self.origin = f"http://127.0.0.1:{self.port}"
        self.data_dir = tmp_path / "hosted-data"
        self.provider_log = tmp_path / "providers.jsonl"
        self.server_log = tmp_path / "server.log"
        self.gate = tmp_path / "release-report"
        self.static_dir = tmp_path / "assets"
        self.static_dir.mkdir()
        (self.static_dir / "index.html").write_text("Hosted HTTP test", encoding="utf-8")

    def client(self, cookies=None):
        return httpx.AsyncClient(
            base_url=self.origin, headers={"Origin": self.origin}, cookies=cookies, trust_env=False, timeout=30,
        )

    def events(self):
        if not self.provider_log.exists():
            return []
        return [json.loads(line) for line in self.provider_log.read_text(encoding="utf-8").splitlines()]

    @contextmanager
    def run(self, concurrency):
        with self.server_log.open("ab") as log:
            process = subprocess.Popen([
                sys.executable, "-m", "backend.tests.hosted_server",
                "--port", str(self.port),
                "--data-dir", str(self.data_dir), "--static-dir", str(self.static_dir),
                "--report-concurrency", str(concurrency), "--report-delay", "0.06",
                "--report-gate", str(self.gate), "--provider-log", str(self.provider_log),
            ], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = monotonic() + 10
            with httpx.Client(base_url=self.origin, trust_env=False, timeout=0.5) as client:
                while process.poll() is None and monotonic() < deadline:
                    try:
                        if client.get("/").text == "Hosted HTTP test":
                            break
                    except httpx.TransportError:
                        pass
                    sleep(0.03)
                else:
                    pytest.fail(f"Hosted server failed to start:\n{self.server_log.read_text()}")
            yield process
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


async def eventually(read, matches, timeout=20):
    deadline = monotonic() + timeout
    while True:
        value = await read()
        if matches(value):
            return value
        if monotonic() >= deadline:
            pytest.fail(f"Expected condition was not reached. Last result: {value!r}")
        await asyncio.sleep(0.03)


async def current(client):
    response = await client.get("/api/case")
    assert response.status_code == 200, response.text
    return response.json()


def case_result(response, status=200):
    assert response.status_code == status, response.text
    body = response.json()
    assert body["status"] == "ready", body
    return body["case"]


async def prepare(client, zip_bytes, source_images, index):
    assert await current(client) == {"case": None, "snapshot": None}
    response = await _upload(client, zip_bytes)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "ready", payload
    snapshot = payload["snapshot"]
    expected_names = list(source_images)
    assert [row["name_original"] for row in snapshot["options"]] == expected_names
    selected_names = [expected_names[index], expected_names[(index + 5) % len(expected_names)]]
    options = {option["name_original"]: option for option in snapshot["options"]}
    selected = [options[name] for name in selected_names]
    fields = {
        "operation_id": str(uuid4()), "snapshot_id": snapshot["snapshot_id"], "previous_case": None,
        "selected_option_ids": [option["option_id"] for option in selected],
        "buyer_context": f"Hosted purchase group {index:02d}",
    }
    selection = await client.post("/api/selections", json=fields)
    assert selection.status_code == 200, selection.text
    assert selection.json()["status"] == "confirmed"
    case = case_result(await client.post("/api/questions", json=fields))
    assert [question["prompt"] for question in case["rounds"][0]["questions"]] == [
        "Would you consider a custom order?", "What purchase deadline should the review consider?",
    ]
    first_answers = ["Custom orders are acceptable", f"{index + 1} December"]
    submission = round_request(case, texts=first_answers)
    submission["submission"]["answers"][1]["answer_mode"] = "natural"
    two = case_result(await client.post("/api/questions/continue", json=submission))
    assert two["rounds"][1]["questions"][0]["prompt"] == "Would you need to approve a sample before production?"
    last_answers = ["A sample is required"]
    brief = case_result(await client.post("/api/brief", json=round_request(two, texts=last_answers)))
    assert brief["phase"] == "ready_for_review", brief
    assert brief["revision"] == 2
    assert brief["report"] is None
    assert brief["buyer_context"] == fields["buyer_context"]
    assert brief["selected_option_ids"] == fields["selected_option_ids"]
    assert [answer["answer_text"] for row in brief["rounds"] for answer in row["answers"]] == first_answers + last_answers
    images = {_image_url(snapshot, option): source_images[name] for name, option in zip(selected_names, selected, strict=True)}
    return snapshot, brief, images, selected_names, first_answers + last_answers


async def assert_images(client, images):
    for url, expected in images.items():
        response = await client.get(url)
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/jpeg"
        assert "no-store" in response.headers["cache-control"]
        assert digest(response.content) == expected


async def wait_for_report(client, prepared):
    snapshot, brief, _, original_names, answers = prepared
    state = await eventually(lambda: current(client), lambda value: value["case"]["phase"] != "generating_review")
    assert state["snapshot"] == snapshot
    reviewed = state["case"]
    assert reviewed["phase"] == "reviewed", reviewed
    assert reference(reviewed) == reference(brief)
    assert reviewed["buyer_context"] == brief["buyer_context"]
    assert reviewed["rounds"] == brief["rounds"]
    assert reviewed["last_operation"]["status"] == "ready"
    report = reviewed["report"]
    assert reference(report) == reference(brief)
    assert report["snapshot_id"] == snapshot["snapshot_id"]
    assert report["selected_option_ids"] == brief["selected_option_ids"]
    assert [claim["quote"] for claim in report["claims"] if claim["kind"] == "text_excerpt" and claim["option_ids"]] == original_names
    assert any(requirement["quote"] == answers[1] for requirement in report["buyer_requirements"])
    return state


def assert_provider_input(event, prepared):
    snapshot, brief, images, _, answers = prepared
    assert reference(event) == reference(brief)
    assert event["snapshot_id"] == snapshot["snapshot_id"]
    assert event["selected_option_ids"] == brief["selected_option_ids"]
    assert event["context_sha256"] == digest(brief["buyer_context"])
    assert event["answer_sha256"] == [digest(answer) for answer in answers]
    assert event["image_sha256"] == {url.rsplit("/", 1)[-1]: expected for url, expected in images.items()}


@pytest.mark.parametrize("concurrency", [3, 10])
def test_ten_hosted_workspaces_keep_cases_and_report_evidence_isolated_after_restart(tmp_path, archive, concurrency):
    server = HostedServer(tmp_path)
    zip_bytes, source_images = archive

    async def scenario():
        async with AsyncExitStack() as stack:
            clients = [await stack.enter_async_context(server.client()) for _ in range(10)]
            guest = await stack.enter_async_context(server.client())
            with server.run(concurrency=concurrency):
                prepared = await asyncio.gather(*(
                    prepare(client, zip_bytes, source_images, index) for index, client in enumerate(clients)
                ))
                by_case = {item[1]["case_id"]: item for item in prepared}
                assert len(by_case) == 10
                assert len({item[0]["snapshot_id"] for item in prepared}) == 10
                for index, (client, (_, brief, images, _, _)) in enumerate(zip(clients, prepared, strict=True)):
                    assert (await current(client))["case"] == brief
                    await assert_images(client, images)
                    # Replay a report command as the wrong session and open the copied image URL.
                    outsider = clients[(index + 1) % len(clients)]
                    rejected = await outsider.post("/api/reviews", json=confirm_command(brief))
                    assert rejected.status_code in {404, 409}, rejected.text
                    assert brief["buyer_context"] not in rejected.text
                    copied = next(iter(images))
                    assert (await outsider.get(copied)).status_code == 404
                    assert (await guest.get(copied)).status_code in {401, 404}
                assert await current(guest) == {"case": None, "snapshot": None}
                assert all(event["stage"] != "review" for event in server.events())
                commands = [confirm_command(item[1]) for item in prepared]

                async def queue(client, payload):
                    started = monotonic()
                    queued = case_result(await client.post("/api/reviews", json=payload), 202)
                    elapsed = monotonic() - started
                    assert queued["phase"] == "generating_review"
                    assert queued["last_operation"]["operation_id"] == payload["operation_id"]
                    return started, elapsed

                confirmations = await asyncio.gather(*(
                    queue(client, payload) for client, payload in zip(clients, commands, strict=True)
                ))
                submitted_at = {item[1]["case_id"]: timing[0] for item, timing in zip(prepared, confirmations, strict=True)}
                for client, command in zip(clients, commands, strict=True):
                    for replay in (command, {**command, "operation_id": str(uuid4())}):
                        duplicate = case_result(await client.post("/api/reviews", json=replay), 202)
                        assert duplicate["last_operation"]["operation_id"] == command["operation_id"]
                async def operation_counts():
                    states = await asyncio.gather(*(current(client) for client in clients))
                    return Counter(state["case"]["last_operation"]["status"] for state in states)

                expected_counts = Counter(running=concurrency, queued=10 - concurrency)
                await eventually(operation_counts, lambda counts: counts == expected_counts)
                released = monotonic()
                server.gate.touch()

                async def timed_report(client, item):
                    report = await wait_for_report(client, item)
                    return report, monotonic() - submitted_at[item[1]["case_id"]]

                completed = await asyncio.gather(*(
                    timed_report(client, item) for client, item in zip(clients, prepared, strict=True)
                ))
                saved, ready_latencies = zip(*completed, strict=True)
                drain = monotonic() - released
                events = server.events()
                report_events = [event for event in events if event["stage"] == "review"]
                assert Counter(event["event"] for event in report_events) == {"start": 10, "finish": 10}
                assert Counter(event["case_id"] for event in report_events if event["event"] == "start") == Counter(by_case.keys())
                active = set()
                peak = 0
                waits = []
                for event in report_events:
                    if event["event"] == "start":
                        assert_provider_input(event, by_case[event["case_id"]])
                        assert event["case_id"] not in active
                        active.add(event["case_id"])
                        peak = max(peak, len(active))
                        waits.append(event["at"] - submitted_at[event["case_id"]])
                    else:
                        active.remove(event["case_id"])
                assert not active
                assert peak == concurrency
                acknowledgements = [elapsed for _, elapsed in confirmations]
                print(
                    f"10 clients, {peak} active / {10 - peak} queued. Median / slowest seconds: "
                    f"confirm {median(acknowledgements):.3f} / {max(acknowledgements):.3f}, "
                    f"submit to provider start {median(waits):.3f} / {max(waits):.3f}, "
                    f"submit to ready {median(ready_latencies):.3f} / {max(ready_latencies):.3f}. "
                    f"All reports ready {drain:.3f}s after provider gate release."
                )
            with server.run(concurrency=concurrency):
                for index, (client, expected, item, command) in enumerate(zip(clients, saved, prepared, commands, strict=True)):
                    async with server.client(cookies=client.cookies) as reopened:
                        assert await current(reopened) == expected
                        await assert_images(reopened, item[2])
                        replay = case_result(await reopened.post("/api/reviews", json=command))
                        assert replay == expected["case"]
                    assert (await clients[(index + 1) % len(clients)].get(next(iter(item[2])))).status_code == 404
                assert server.events() == events

    asyncio.run(scenario())


def test_hosted_restart_recovers_queued_report_and_retries_interrupted_report(tmp_path, archive):
    server = HostedServer(tmp_path)
    zip_bytes, source_images = archive

    async def scenario():
        async with server.client() as first, server.client() as second:
            with server.run(concurrency=1) as process:
                interrupted, waiting = await asyncio.gather(
                    prepare(first, zip_bytes, source_images, 0), prepare(second, zip_bytes, source_images, 1),
                )
                first_command, second_command = [confirm_command(item[1]) for item in (interrupted, waiting)]
                case_result(await first.post("/api/reviews", json=first_command), 202)
                await eventually(lambda: current(first), lambda state: state["case"]["last_operation"]["status"] == "running")
                queued = case_result(await second.post("/api/reviews", json=second_command), 202)
                assert queued["last_operation"]["status"] == "queued"
                started, = [event for event in server.events() if event["stage"] == "review"]
                assert_provider_input(started, interrupted)
                process.kill()
                process.wait(timeout=5)

            server.gate.touch()
            with server.run(concurrency=1):
                restored = await current(first)
                assert restored["snapshot"] == interrupted[0]
                failed = restored["case"]
                assert failed["phase"] == "failed"
                assert failed["report"] is None
                assert failed["rounds"] == interrupted[1]["rounds"]
                assert reference(failed) == reference(interrupted[1])
                assert failed["last_operation"]["operation_id"] == first_command["operation_id"]
                assert failed["last_operation"]["code"] == "analysis_failed"
                await assert_images(first, interrupted[2])
                assert (await wait_for_report(second, waiting))["case"]["last_operation"]["operation_id"] == second_command["operation_id"]
                assert [event["case_id"] for event in server.events() if event["stage"] == "review" and event["event"] == "start"] == [
                    interrupted[1]["case_id"], waiting[1]["case_id"],
                ]
                retry = {**reference(failed), "operation_id": first_command["operation_id"],
                         "retry_token": failed["last_operation"]["retry_token"]}
                case_result(await first.post("/api/case/retry", json=retry), 202)
                assert (await wait_for_report(first, interrupted))["case"]["last_operation"]["operation_id"] == first_command["operation_id"]
                starts = [event for event in server.events() if event["stage"] == "review" and event["event"] == "start"]
                assert [event["case_id"] for event in starts] == [interrupted[1]["case_id"], waiting[1]["case_id"], interrupted[1]["case_id"]]
                assert_provider_input(starts[1], waiting)
                assert_provider_input(starts[2], interrupted)
                print("SIGKILL: queued report resumed once, running report failed until explicit retry, provider inputs matched")

    asyncio.run(scenario())
