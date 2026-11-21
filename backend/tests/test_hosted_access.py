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
    return HostedSettings(tmp_path / "data", ORIGIN, "shared demo code", static_dir=static, **kwargs)


def unlock(client):
    result = client.post("/api/demo/unlock", json={"code": "shared demo code"}, headers={"origin": ORIGIN})
    assert result.status_code == 204
    return client.cookies.get(COOKIE)


def upload(client):
    return client.post("/api/imports", files={"file": ("cups.zip", SAMPLE.read_bytes(), "application/zip")}).json()


def test_only_unlocked_browser_can_use_case_endpoints(tmp_path):
    config = settings(tmp_path)
    with TestClient(create_hosted_app(config), base_url=ORIGIN) as client:
        assert client.get("/").text == "DiliDoo"
        assert client.get("/healthz").status_code == 200
        assert client.get("/api/case").status_code == 401
        assert client.post("/api/imports").status_code == 401
        assert client.post("/api/demo/unlock", json={"code": "wrong"}).status_code == 401
        assert client.post("/api/demo/unlock", json={"code": config.demo_code},
                           headers={"origin": "https://other.test"}).status_code == 403
        token = unlock(client)
        saved = client.get("/api/case")
        assert saved.json() == {"snapshot": None, "case": None}
        assert all(flag in saved.headers["set-cookie"] for flag in ("HttpOnly", "Secure", "SameSite=strict"))
        assert saved.headers["cache-control"] == "private, no-store"
        assert client.post("/api/imports", headers={"origin": "https://other.test"}).status_code == 403
        assert unlock(client) == token


@pytest.mark.parametrize("restart", [False, True])
def test_global_and_browser_attempt_limits_cover_sdk_retries_and_new_cookies(tmp_path, restart):
    sent = []
    def provider(request):
        sent.append(request)
        if len(sent) == 1:
            return httpx2.Response(429, json={"error": {"code": "rate_limit_exceeded"}}, request=request)
        return response(request, translations_for(request))

    config = settings(tmp_path, attempt_budget=2, session_attempt_budget=1)
    async def translate(snapshot):
        async with mock_openai(provider) as sdk:
            return await ai.translate_listing(snapshot, client=sdk)

    app = create_hosted_app(config, translator=translate)
    def check_second_session(client):
        client.cookies.clear()
        unlock(client)
        assert upload(client)["status"] == "ready"
        client.cookies.clear()
        unlock(client)
        assert "allowance" in upload(client)["message"]
        assert len(sent) == 2

    with TestClient(app, base_url=ORIGIN) as client:
        unlock(client)
        assert "allowance" in upload(client)["message"]
        assert len(sent) == 1
        if not restart:
            check_second_session(client)
    if restart:
        with TestClient(create_hosted_app(config, translator=translate), base_url=ORIGIN) as restarted:
            check_second_session(restarted)


def test_expired_browser_cannot_recover_case_and_startup_reclaims_capacity(tmp_path, monkeypatch):
    config = settings(tmp_path, session_ttl_seconds=30, max_sessions=1)
    clock = [1000]
    monkeypatch.setattr("backend.hosted.time", type("Clock", (), {"time": lambda: clock[0]}))
    with TestClient(create_hosted_app(config), base_url=ORIGIN) as client:
        old_token = unlock(client)
        assert client.get("/api/case").status_code == 200
        clock[0] += 31
        assert client.get("/api/case").status_code == 401
    with TestClient(create_hosted_app(config), base_url=ORIGIN) as client:
        assert client.get("/api/case", headers={"cookie": f"{COOKIE}={old_token}"}).status_code == 401
        assert unlock(client) != old_token
        assert client.get("/api/case").json() == {"snapshot": None, "case": None}


def test_railway_requires_case_data_on_attached_volume(monkeypatch, tmp_path):
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_ID", "demo")
    monkeypatch.setenv("DEMO_ORIGIN", ORIGIN)
    monkeypatch.setenv("DEMO_ACCESS_CODE", "demo")
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
        healthy = unlock(client)
        client.get("/api/case").raise_for_status()
        client.cookies.clear()
        broken = unlock(client)
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
