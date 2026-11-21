"""Serve the hosted app on loopback with scripted providers for local tests."""

import argparse
import asyncio
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic

import uvicorn

from backend.hosted import HostedSettings, create_hosted_app
from backend.tests.offline_server import offline_brief, offline_questions, offline_review, offline_translation


def run(args: argparse.Namespace, data_dir: Path) -> None:
    settings = HostedSettings(
        data_dir=data_dir,
        origin=f"http://127.0.0.1:{args.port}",
        demo_code=args.demo_code,
        report_concurrency=args.report_concurrency,
        attempt_budget=500,
        session_attempt_budget=50,
        static_dir=args.static_dir,
    )

    def record(stage, event, **details):
        if args.provider_log is not None:
            with args.provider_log.open("a", encoding="utf-8") as log:
                log.write(json.dumps({"stage": stage, "event": event, "at": monotonic(), **details}) + "\n")

    async def translate(snapshot):
        record("translation", "start", snapshot_id=snapshot.snapshot_id)
        return await offline_translation(snapshot)

    async def questions(inputs):
        record("questions", "start", snapshot_id=inputs.snapshot.snapshot_id)
        return await offline_questions(inputs)

    async def review(inputs, images):
        details = {"case_id": inputs.case_id, "revision": inputs.revision}
        record(
            "review", "start", **details,
            snapshot_id=inputs.snapshot.snapshot_id,
            selected_option_ids=inputs.selected_option_ids,
            context_sha256=sha256(inputs.buyer_context.encode()).hexdigest(),
            answer_sha256=[
                None if answer.answer_text is None else sha256(answer.answer_text.encode()).hexdigest()
                for answer in inputs.answers
            ],
            image_sha256={key: sha256(payload).hexdigest() for key, (_mime, payload) in images.items()},
        )
        try:
            if args.report_gate is not None:
                while not args.report_gate.exists():
                    await asyncio.sleep(0.02)
            if args.report_delay:
                await asyncio.sleep(args.report_delay)
            return await offline_review(inputs, images)
        finally:
            record("review", "finish", **details)

    app = create_hosted_app(
        settings, translator=translate, question_generator=questions,
        review_generator=review, brief_generator=offline_brief,
    )
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18187)
    parser.add_argument("--demo-code", default="test-demo")
    parser.add_argument("--data-dir", type=Path, help="Reuse this directory across test restarts.")
    parser.add_argument("--static-dir", type=Path, default=Path("frontend/dist"))
    parser.add_argument("--report-concurrency", type=int, default=3)
    parser.add_argument("--report-delay", type=float, default=0, help="Scripted provider delay in seconds.")
    parser.add_argument("--report-gate", type=Path, help="Hold provider calls until this file exists.")
    parser.add_argument("--provider-log", type=Path, help="Append scripted provider events as JSONL.")
    args = parser.parse_args()
    if args.data_dir is not None:
        run(args, args.data_dir)
    else:
        with TemporaryDirectory(prefix="purchase-hosted-") as directory:
            run(args, Path(directory))


if __name__ == "__main__":
    main()
