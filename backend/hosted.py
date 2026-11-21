"""Single-worker Railway entry point. Each unlocked browser owns one durable workspace."""
import asyncio
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from dataclasses import dataclass
from functools import partial
import hashlib
import hmac
import logging
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from pydantic import BaseModel, Field, ValidationError
from starlette.responses import JSONResponse, Response
from starlette.staticfiles import StaticFiles

from . import ai
from .main import create_app
from .report import build_review_client, generate_review
from .review_queue import ReviewQueue
from .state import Store


COOKIE = "dilidoo_session"
SESSION_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}")
LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class HostedSettings:
    data_dir: Path
    origin: str
    demo_code: str
    static_dir: Path = Path("frontend/dist")
    report_concurrency: int = 2
    attempt_budget: int = 0
    session_attempt_budget: int = 0
    session_ttl_seconds: int = 48 * 3600
    max_sessions: int = 100

    def __post_init__(self):
        url = urlsplit(self.origin)
        local = url.scheme == "http" and url.hostname in ("127.0.0.1", "localhost", "::1")
        if not (local or url.scheme == "https") or not url.hostname or url.username or url.password or (
            url.path or url.query or url.fragment
        ):
            raise ValueError("Set DEMO_ORIGIN to the exact HTTPS origin, or HTTP on localhost.")
        if not self.demo_code.strip() or len(self.demo_code.encode()) > 256:
            raise ValueError("Set a nonempty DEMO_ACCESS_CODE of up to 256 bytes.")
        if not 1 <= self.report_concurrency <= 10 or min(self.attempt_budget, self.session_attempt_budget) < 0:
            raise ValueError("Set report concurrency to 1–10 and nonnegative API attempt limits.")
        if self.session_ttl_seconds < 1 or self.max_sessions < 1:
            raise ValueError("Session lifetime and capacity must be positive.")

    @classmethod
    def from_env(cls):
        mount = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH")
        data_dir = Path(os.environ.get("DEMO_DATA_DIR") or mount or "/data")
        if os.environ.get("RAILWAY_ENVIRONMENT_ID") and (
            not mount or not data_dir.resolve().is_relative_to(Path(mount).resolve())
        ):
            raise ValueError("Mount a Railway volume at /data to persist demo cases and API limits.")
        return cls(
            data_dir=data_dir,
            origin=os.environ["DEMO_ORIGIN"], demo_code=os.environ["DEMO_ACCESS_CODE"],
            report_concurrency=int(os.environ.get("DEMO_REPORT_CONCURRENCY", "2")),
            attempt_budget=int(os.environ.get("DEMO_API_ATTEMPTS", "0")),
            session_attempt_budget=int(os.environ.get("DEMO_SESSION_API_ATTEMPTS", "0")),
            session_ttl_seconds=int(os.environ.get("DEMO_SESSION_TTL_HOURS", "48")) * 3600,
        )


class DemoRegistry:
    def __init__(self, settings):
        self.settings = settings
        self.directory = settings.data_dir / "cases"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(settings.data_dir / "demo.sqlite3")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY, last_seen INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS usage (id INTEGER PRIMARY KEY CHECK(id=1), attempts INTEGER NOT NULL);
            INSERT OR IGNORE INTO usage VALUES (1,0);
        """)

    def new_session(self):
        if self.db.execute("SELECT count(*) FROM sessions").fetchone()[0] >= self.settings.max_sessions:
            return None
        token = secrets.token_urlsafe(32)
        with self.db:
            self.db.execute("INSERT INTO sessions (id,last_seen) VALUES (?,?)", (self.digest(token), int(time.time())))
        return token

    @staticmethod
    def digest(token):
        return hashlib.sha256(token.encode("ascii")).hexdigest()

    def authenticate(self, token):
        if not token or SESSION_PATTERN.fullmatch(token) is None:
            return None
        key = self.digest(token)
        now = int(time.time())
        seen = self.db.execute("SELECT last_seen FROM sessions WHERE id=?", (key,)).fetchone()
        if seen is None or seen[0] <= now - self.settings.session_ttl_seconds:
            return None
        if seen[0] != now:
            with self.db:
                self.db.execute("UPDATE sessions SET last_seen=? WHERE id=?", (now, key))
        return key

    def reserve(self, key):
        # One synchronous transaction per SDK attempt, including retries. Global spend
        # remains charged when a browser expires or obtains a new cookie.
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            global_used, = self.db.execute("SELECT attempts FROM usage WHERE id=1").fetchone()
            if global_used >= self.settings.attempt_budget:
                return False
            updated = self.db.execute("UPDATE sessions SET attempts=attempts+1 WHERE id=? AND attempts<?",
                                      (key, self.settings.session_attempt_budget))
            if not updated.rowcount:
                return False
            self.db.execute("UPDATE usage SET attempts=attempts+1 WHERE id=1")
        return True

    def path(self, key):
        return self.directory / f"{key}.sqlite3"


@dataclass
class Workspace:
    app: FastAPI
    resources: AsyncExitStack
    requests: int = 0


class Workspaces:
    def __init__(self, settings, registry, queue, providers):
        self.settings = settings
        self.registry = registry
        self.queue = queue
        self.providers = providers
        self.opened: dict[str, Workspace] = {}
        self.open_lock = asyncio.Lock()

    async def open(self, key):
        if key in self.opened:
            return self.opened[key]

        async with self.open_lock:
            if key not in self.opened:
                self.opened[key] = await self._create(key)
            return self.opened[key]

    async def _create(self, key):
        def metered(provider):
            async def call(*args, **kwargs):
                ctx = ai.reserve_provider_attempt.set(partial(self.registry.reserve, key))
                try:
                    return await provider(*args, **kwargs)
                finally:
                    ai.reserve_provider_attempt.reset(ctx)
            return call

        scoped = {name: metered(fn) for name, fn in self.providers.items() if fn is not None}
        child = create_app(self.registry.path(key), allowed_origin=self.settings.origin,
                           review_queue=self.queue, **scoped)
        resources = AsyncExitStack()
        await resources.enter_async_context(child.router.lifespan_context(child))
        return Workspace(child, resources)

    async def clean(self):
        cutoff = int(time.time()) - self.settings.session_ttl_seconds
        expired = self.registry.db.execute("SELECT id FROM sessions WHERE last_seen<=?", (cutoff,)).fetchall()
        for key, in expired:
            workspace = self.opened.get(key)
            if workspace:
                if workspace.requests or self.queue.busy(workspace.app.state.case_workflow):
                    continue
                del self.opened[key]
                await workspace.resources.aclose()
            path = self.registry.path(key)
            for suffix in ("", "-journal", "-wal", "-shm"):
                Path(str(path) + suffix).unlink(missing_ok=True)
            with self.registry.db:
                self.registry.db.execute("DELETE FROM sessions WHERE id=?", (key,))

    async def sweep(self):
        while True:
            await asyncio.sleep(60)
            try:
                await self.clean()
            except (sqlite3.Error, OSError):
                LOG.exception("Could not expire old demo workspaces")


class Unlock(BaseModel):
    code: str = Field(max_length=256)


def locked():
    return JSONResponse({"code": "demo_locked", "message": "Enter the demo code to open your workspace."},
                        status_code=401, headers={"Cache-Control": "no-store"})


def create_hosted_app(settings: HostedSettings, *, translator=None, question_generator=None, review_generator=None,
                      brief_generator=None, interpret_answers=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        async with AsyncExitStack() as resources:
            registry = DemoRegistry(settings)
            resources.callback(registry.db.close)

            def client_if_needed(needed, build):
                client = build() if needed else None
                if client is not None:
                    resources.push_async_callback(client.close)
                return client

            translation = client_if_needed(translator is None, ai.build_translation_client)
            questions = client_if_needed(question_generator is None or brief_generator is None, ai.build_question_client)
            reviews = client_if_needed(review_generator is None, build_review_client)
            providers = {
                "translator": translator or partial(ai.translate_listing, client=translation),
                "question_generator": question_generator or partial(ai.generate_questions, client=questions),
                "review_generator": review_generator or partial(generate_review, client=reviews),
                "brief_generator": brief_generator or partial(ai.generate_brief_proposal, client=questions),
                "interpret_answers": interpret_answers or (
                    partial(ai.interpret_written_answers, client=questions) if questions is not None else None),
            }
            queue = ReviewQueue(max_workers=settings.report_concurrency)
            manager = Workspaces(settings, registry, queue, providers)
            app.state.workspaces = manager
            try:
                await manager.clean()
                # Attach persisted waiting jobs before releasing workers so FIFO spans a restart.
                for key, in registry.db.execute("SELECT id FROM sessions").fetchall():
                    path = registry.path(key)
                    try:
                        if path.exists() and Store.has_queued_review(path):
                            await manager.open(key)
                    except sqlite3.DatabaseError:
                        # An unreadable case must not take other browsers offline.
                        LOG.exception("Could not recover one hosted demo workspace")
                queue.start()
                sweep = asyncio.create_task(manager.sweep())
                try:
                    yield
                finally:
                    sweep.cancel()
                    with suppress(asyncio.CancelledError):
                        await sweep
            finally:
                await queue.close()
                for workspace in manager.opened.values():
                    await workspace.resources.aclose()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/healthz")
    async def health():
        app.state.workspaces.registry.db.execute("SELECT id FROM usage WHERE id=1").fetchone()
        return {"status": "ready"}

    def set_cookie(response, token):
        response.set_cookie(COOKIE, token, httponly=True, secure=settings.origin.startswith("https://"),
                            max_age=settings.session_ttl_seconds, samesite="strict", path="/")

    @app.post("/api/demo/unlock")
    async def unlock(request: Request):
        origins = request.headers.getlist("origin")
        if origins and origins != [settings.origin]:
            return Response(status_code=403)
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > 2048:
                return Response(status_code=413)
            body.extend(chunk)
        try:
            command = Unlock.model_validate_json(body)
        except ValidationError:
            return locked()
        if not hmac.compare_digest(command.code.encode(), settings.demo_code.encode()):
            return locked()
        registry = app.state.workspaces.registry
        token = request.cookies.get(COOKIE)
        if registry.authenticate(token) is None:
            token = registry.new_session()
            if token is None:
                return JSONResponse({"message": "All demo workspaces are in use. Try again later."}, status_code=503)
        response = Response(status_code=204, headers={"Cache-Control": "no-store"})
        set_cookie(response, token)
        return response

    # Keep the full /api path when forwarding to the existing single-workspace routes.
    async def route_workspace(scope, receive, send):
        request = Request(scope)
        registry = app.state.workspaces.registry
        token = request.cookies.get(COOKIE)
        key = registry.authenticate(token)
        if key is None:
            await locked()(scope, receive, send)
            return
        try:
            workspace = await app.state.workspaces.open(key)
        except sqlite3.DatabaseError:
            await JSONResponse({"code": "storage_failed", "message": "Your saved workspace could not be opened."},
                               status_code=503, headers={"Cache-Control": "no-store"})(scope, receive, send)
            return
        workspace.requests += 1
        cookie_response = Response()
        set_cookie(cookie_response, token)

        async def send_private(message):
            if message["type"] == "http.response.start":
                headers = [(name, value) for name, value in message["headers"] if name.lower() != b"cache-control"]
                headers.append((b"cache-control", b"private, no-store"))
                headers.append((b"set-cookie", cookie_response.headers["set-cookie"].encode("latin-1")))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await workspace.app({**scope, "root_path": ""}, receive, send_private)
        finally:
            workspace.requests -= 1

    app.mount("/api", route_workspace)
    app.mount("/", StaticFiles(directory=settings.static_dir, html=True))
    return app


def from_env():
    return create_hosted_app(HostedSettings.from_env())
