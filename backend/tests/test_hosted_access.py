from pathlib import Path
import sqlite3
import subprocess
import sys

from fastapi.testclient import TestClient
import httpx2
import pytest

from backend import ai
from backend.hosted import COOKIE, DemoRegistry, HostedSettings, create_hosted_app
from backend.tests.test_translation import mock_openai, response, translations_for


ORIGIN = "https://dilidoo.test"
SAMPLE = Path(__file__).resolve().parents[2] / "samples/listing.zip"


def settings(tmp_path, **kwargs):
    static = tmp_path / "static"
    static.mkdir(exist_ok=True)
    (static / "index.html").write_text("DiliDoo")
    return HostedSettings(tmp_path / "data", ORIGIN, static_dir=static, **kwargs)


def open_workspace(client):
    client.get("/api/case").raise_for_status()
    return client.cookies.get(COOKIE)


def upload(client):
    return client.post("/api/imports", files={"file": ("cups.zip", SAMPLE.read_bytes(), "application/zip")}).json()


def test_public_link_opens_a_private_workspace(tmp_path):
    with TestClient(create_hosted_app(settings(tmp_path)), base_url=ORIGIN) as client:
        assert client.get("/").text == "DiliDoo"
        assert client.get("/healthz").status_code == 200
        assert client.post("/api/imports").status_code == 401
        assert COOKIE not in client.cookies
        saved = client.get("/api/case")
        assert saved.json() == {"snapshot": None, "case": None}
        assert all(flag in saved.headers["set-cookie"] for flag in ("HttpOnly", "Secure", "SameSite=strict"))
        assert saved.headers["cache-control"] == "private, no-store"
        token = client.cookies.get(COOKIE)
        assert open_workspace(client) == token
        assert client.post("/api/imports", headers={"origin": "https://other.test"}).status_code == 403
        client.cookies.clear()
        assert open_workspace(client) != token


def test_existing_browser_and_exhausted_legacy_counters_survive_upgrade(tmp_path):
    config = settings(tmp_path)
    config.data_dir.mkdir()
    old_token = "A" * 43
    with sqlite3.connect(config.data_dir / "demo.sqlite3") as db:
        db.executescript("""
            CREATE TABLE sessions (id TEXT PRIMARY KEY, last_seen INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE usage (id INTEGER PRIMARY KEY, attempts INTEGER NOT NULL);
            INSERT INTO usage VALUES (1,100);
        """)
        db.execute("INSERT INTO sessions VALUES (?,unixepoch(),10)", (DemoRegistry.digest(old_token),))
    replies = []
    def provider(request):
        replies.append(request)
        if len(replies) == 1:
            return httpx2.Response(429, json={"error": {"code": "rate_limit_exceeded"}}, request=request)
        return response(request, translations_for(request))

    async def translate(snapshot):
        async with mock_openai(provider) as sdk:
            return await ai.translate_listing(snapshot, client=sdk)

    with TestClient(create_hosted_app(config, translator=translate), base_url=ORIGIN) as client:
        client.cookies.set(COOKIE, old_token, domain="dilidoo.test", path="/")
        assert open_workspace(client) == old_token
        imported = upload(client)
        assert imported["status"] == "ready"
        assert len(replies) == 2
        client.cookies.clear()
        assert open_workspace(client) != old_token
        assert client.get("/api/case").json()["snapshot"] is None
        assert upload(client)["status"] == "ready"
    with TestClient(create_hosted_app(config, translator=translate), base_url=ORIGIN) as restarted:
        restored = restarted.get("/api/case", headers={"cookie": f"{COOKIE}={old_token}"}).json()
        assert restored["snapshot"] == imported["snapshot"]


def test_expired_browser_starts_with_empty_case_after_restart(tmp_path, monkeypatch):
    config = settings(tmp_path, session_ttl_seconds=30, max_sessions=1)
    clock = [1000]
    monkeypatch.setattr("backend.hosted.time", type("Clock", (), {"time": lambda: clock[0]}))
    with TestClient(create_hosted_app(config), base_url=ORIGIN) as client:
        old_token = open_workspace(client)
        clock[0] += 31
        assert client.post("/api/imports").status_code == 401
    with TestClient(create_hosted_app(config), base_url=ORIGIN) as client:
        saved = client.get("/api/case", headers={"cookie": f"{COOKIE}={old_token}"})
        assert saved.json() == {"snapshot": None, "case": None}
        assert client.cookies.get(COOKIE) != old_token


def test_railway_requires_case_data_on_attached_volume(monkeypatch, tmp_path):
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_ID", "demo")
    monkeypatch.setenv("DEMO_ORIGIN", ORIGIN)
    monkeypatch.delenv("RAILWAY_VOLUME_MOUNT_PATH", raising=False)
    monkeypatch.delenv("DEMO_DATA_DIR", raising=False)
    with pytest.raises(ValueError, match="volume"):
        HostedSettings.from_env()
    monkeypatch.setenv("RAILWAY_VOLUME_MOUNT_PATH", str(tmp_path))
    assert HostedSettings.from_env().data_dir == tmp_path
    monkeypatch.setenv("DEMO_DATA_DIR", str(tmp_path.parent / "ephemeral"))
    with pytest.raises(ValueError, match="volume"):
        HostedSettings.from_env()


def test_hosted_boot_recovers_interrupted_image_write_and_isolates_corrupt_case(tmp_path):
    config = settings(tmp_path)
    with TestClient(create_hosted_app(config), base_url=ORIGIN) as client:
        healthy = open_workspace(client)
        client.get("/api/case").raise_for_status()
        client.cookies.clear()
        broken = open_workspace(client)
        client.get("/api/case").raise_for_status()
    files = config.data_dir / "cases"
    path = files / f"{DemoRegistry.digest(healthy)}.sqlite3"
    broken_path = files / f"{DemoRegistry.digest(broken)}.sqlite3"
    # Force dirty pages onto disk and terminate without committing. Reopening the
    # file must roll back the pending update before looking for waiting reviews.
    with sqlite3.connect(path) as db:
        db.executemany("INSERT INTO image_blobs VALUES (?, 'image/png', zeroblob(8000))",
                       ((str(i),) for i in range(200)))
    db.close()
    subprocess.run([sys.executable, "-c", """
import os, sqlite3, sys
db = sqlite3.connect(sys.argv[1])
db.execute("PRAGMA cache_size=3")
db.execute("UPDATE image_blobs SET payload=randomblob(8000)")
os._exit(0)
""", str(path)], check=True)
    assert Path(str(path) + "-journal").stat().st_size > 512
    broken_path.write_bytes(b"invalid sqlite header")
    with TestClient(create_hosted_app(config), base_url=ORIGIN) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/api/case", headers={"Cookie": f"{COOKIE}={healthy}"}).status_code == 200
        assert client.get("/api/case", headers={"Cookie": f"{COOKIE}={broken}"}).status_code == 503
        assert not Path(str(path) + "-journal").exists()
        with sqlite3.connect(path) as db:
            assert db.execute("SELECT count(*) FROM image_blobs WHERE payload=zeroblob(8000)").fetchone() == (200,)
        db.close()
