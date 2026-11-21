import asyncio
from functools import partial
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from stat import S_IFLNK
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from bs4 import BeautifulSoup
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient, MockTransport
import pytest

from backend.link_intake import fetch_listing
from backend.main import DEMO_ORIGIN, create_app


SAMPLES = Path(__file__).resolve().parents[2] / "samples"
CURATED = SAMPLES / "listing.zip"
RAW = SAMPLES / "raw-save" / "raw-save-redacted.zip"
INVENTORY = json.loads((SAMPLES / "expected-inventory.json").read_text(encoding="utf-8"))
MIB = 1024 * 1024


async def stub(snapshot):
    return snapshot


def _upload(client, path_or_bytes=CURATED, **kwargs):
    payload = path_or_bytes.read_bytes() if isinstance(path_or_bytes, Path) else path_or_bytes
    return client.post("/api/imports", files={"file": ("listing.zip", payload, "application/zip")}, **kwargs)


def _image_url(snapshot, option):
    return f"/api/snapshots/{snapshot['snapshot_id']}/images/{option['image_source_id']}"


def _copy_zip(change, sample=CURATED):
    with ZipFile(sample, metadata_encoding="utf-8") as source:
        entries = {key: source.read(key) for key in source.namelist()}
    change(entries)
    return _zip(entries.items())


def _zip(entries):
    result = BytesIO()
    with ZipFile(result, mode="w", compression=ZIP_DEFLATED) as saved:
        for key, value in entries:
            saved.writestr(key, value)
    return result.getvalue()


@pytest.mark.parametrize("archive", [CURATED, RAW], ids=["curated", "raw-browser-save"])
def test_real_archives_preserve_listing_and_every_sku_image(archive, tmp_path):
    with TestClient(create_app(tmp_path / "store.db", translator=stub)) as client:
        response = _upload(client, archive, headers={"Origin": DEMO_ORIGIN})
        assert response.status_code == 200
        assert response.json()["status"] == "ready"
        snapshot = response.json()["snapshot"]
        assert snapshot["title"] == INVENTORY["title"]
        assert [option["name_original"] for option in snapshot["options"]] == [
            expected["name_original"] for expected in INVENTORY["options"]
        ]
        shared, = (source for source in snapshot["sources"] if source["label"] == "Shared selector value")
        assert shared["original_text"] == "720ml"
        assert shared["option_ids"] == [option["option_id"] for option in snapshot["options"]]
        properties = [source for source in snapshot["sources"] if source["label"] == "Product attribute"]
        assert [source["original_text"] for source in properties] == [
            f"{item['label']}: {item['value']}" for item in INVENTORY["properties"]
        ]
        assert all(not source["option_ids"] for source in properties)
        assert any("thumbnail" in note.lower() for note in snapshot["limitations"])
        assert any(source["kind"] == "official_guidance" for source in snapshot["sources"])
        for option, expected in zip(snapshot["options"], INVENTORY["options"], strict=True):
            image = client.get(_image_url(snapshot, option))
            assert image.status_code == 200
            assert image.headers["content-type"] == "image/jpeg"
            assert image.headers["x-content-type-options"] == "nosniff"
            assert sha256(image.content).hexdigest() == expected["image_sha256"]


def test_images_distinguish_missing_unreadable_unmapped_and_remote(tmp_path):
    def change(entries):
        first, second, third, fourth = INVENTORY["options"][:4]
        del entries[first["image"]]
        entries[second["image"]] = b"<svg>unsupported image</svg>"
        document = BeautifulSoup(entries["listing.html"].decode(), "html.parser")
        document.find("img", attrs={"data-sku": third["data_sku"]}).decompose()
        del document.find("input", attrs={"data-sku": third["data_sku"]})["data-image"]
        document.find("img", attrs={"data-sku": fourth["data_sku"]})["src"] = "https://example.invalid/image.jpg"
        entries["listing.html"] = document.encode()

    with TestClient(create_app(tmp_path / "store.db", translator=stub)) as client:
        result = _upload(client, _copy_zip(change)).json()
        assert result["status"] == "ready"
        snapshot = result["snapshot"]
        sources = {item["source_id"]: item for item in snapshot["sources"]}
        for option, availability in zip(snapshot["options"][:4], ["missing", "unreadable", None, "missing"], strict=True):
            if availability is None:
                assert option["image_source_id"] is None
                continue
            image_source = sources[option["image_source_id"]]
            assert image_source["availability"] == availability
            assert image_source["image_file"] is None
            assert client.get(_image_url(snapshot, option)).status_code == 404
        assert client.get(_image_url(snapshot, snapshot["options"][4])).status_code == 200


@pytest.mark.parametrize("archive", [CURATED, RAW], ids=["curated", "raw-browser-save"])
def test_import_rejects_images_whose_sku_keys_conflict_with_their_cards(archive, tmp_path):
    def change(entries):
        main = next(path for path in entries if "/" not in path and path.endswith(".html"))
        document = BeautifulSoup(entries[main].decode(), "html.parser")
        first, second = INVENTORY["options"][:2]
        first_image = document.find("img", attrs={"data-sku": first["data_sku"]})
        second_image = document.find("img", attrs={"data-sku": second["data_sku"]})
        first_image["data-sku"], second_image["data-sku"] = second["data_sku"], first["data_sku"]
        entries[main] = document.encode()

    with TestClient(create_app(tmp_path / "store.db", translator=stub)) as client:
        previously_saved = _upload(client, archive).json()["snapshot"]
        result = _upload(client, _copy_zip(change, archive)).json()
        assert (result["status"], result.get("code")) == ("failed", "unsupported_layout")
        assert client.get("/api/case").json() == {"case": None, "snapshot": None}
        assert client.get(_image_url(previously_saved, previously_saved["options"][0])).status_code == 404


def test_import_accepts_explicit_sku_images_without_sku_card_wrappers(tmp_path):
    def change(entries):
        document = BeautifulSoup(entries["listing.html"].decode(), "html.parser")
        first_card, second_card = document.find_all("div", class_="item-parent", limit=2)
        first_id = first_card.find("input")["id"]
        first_card.find("label")["for"] = first_id
        first_card.unwrap()
        second_card.find("label").unwrap()
        second_card.unwrap()
        entries["listing.html"] = document.encode()

    with TestClient(create_app(tmp_path / "store.db", translator=stub)) as client:
        snapshot = _upload(client, _copy_zip(change)).json()["snapshot"]
        for option, expected in zip(snapshot["options"][:2], INVENTORY["options"][:2], strict=True):
            photo = client.get(_image_url(snapshot, option))
            assert photo.status_code == 200
            assert sha256(photo.content).hexdigest() == expected["image_sha256"]


def test_import_rejects_photo_label_with_duplicate_target_across_groups(tmp_path):
    def change(entries):
        document = BeautifulSoup(entries["listing.html"].decode(), "html.parser")
        parent = document.find(attrs={"data-sku-group-type": "parent"})
        child = document.find(attrs={"data-sku-group-type": "child"})
        target_id = parent.find("input")["id"]
        parent.find("label")["for"] = target_id
        child.find("input")["id"] = target_id
        parent.insert_before(child.extract())
        entries["listing.html"] = document.encode()

    with TestClient(create_app(tmp_path / "store.db", translator=stub)) as client:
        result = _upload(client, _copy_zip(change)).json()
        assert (result["status"], result.get("code")) == ("failed", "unsupported_layout")
        assert client.get("/api/case").json()["snapshot"] is None


def test_import_accepts_sku_card_labels_for_preceding_radios(tmp_path):
    def change(entries):
        document = BeautifulSoup(entries["listing.html"].decode(), "html.parser")
        for card in document.find_all("div", class_="item-parent"):
            control = card.find("input")
            label = card.find("label")
            label["for"] = control["id"]
            card.insert_before(control.extract())
            label.insert_after(label.find("img").extract())
        entries["listing.html"] = document.encode()

    with TestClient(create_app(tmp_path / "store.db", translator=stub)) as client:
        result = _upload(client, _copy_zip(change)).json()
        assert result["status"] == "ready"
        snapshot = result["snapshot"]
        for option, expected in zip(snapshot["options"], INVENTORY["options"], strict=True):
            photo = client.get(_image_url(snapshot, option))
            assert photo.status_code == 200
            assert sha256(photo.content).hexdigest() == expected["image_sha256"]


@pytest.mark.parametrize("conflict", ["different-label", "multiple-labels", "multiple-radios"])
def test_import_rejects_sku_cards_with_conflicting_ownership(conflict, tmp_path):
    def change(entries):
        document = BeautifulSoup(entries["listing.html"].decode(), "html.parser")
        first, second = document.find_all("div", class_="item-parent", limit=2)
        first_control, second_control = first.find("input"), second.find("input")
        label = first.find("label")
        label.insert_after(label.find("img").extract())
        if conflict == "different-label":
            label["for"] = second_control["id"]
        else:
            label["for"] = first_control["id"]
            if conflict == "multiple-labels":
                first.insert_before(first_control.extract())
                first.append(document.new_tag("label", attrs={"for": second_control["id"]}))
            else:
                first.append(second_control.extract())
                second.find("label")["for"] = second_control["id"]
        entries["listing.html"] = document.encode()

    with TestClient(create_app(tmp_path / "store.db", translator=stub)) as client:
        result = _upload(client, _copy_zip(change)).json()
        assert (result["status"], result.get("code")) == ("failed", "unsupported_layout")


@pytest.mark.parametrize("axis", ["parent", "child"])
def test_import_rejects_image_labels_linked_to_a_different_control(axis, tmp_path):
    def change(entries, *, valid_label):
        document = BeautifulSoup(entries["listing.html"].decode(), "html.parser")
        group = document.find(attrs={"data-sku-group-type": axis})
        label = group.find("label")
        if axis == "child":
            image = document.new_tag("img", attrs={"data-sku": "720ml", "src": INVENTORY["options"][0]["image"]})
            label.append(image)
        label["for"] = group.find("input")["id"] if valid_label else f"sku-{INVENTORY['options'][1]['data_sku']}"
        entries["listing.html"] = document.encode()

    with TestClient(create_app(tmp_path / "store.db", translator=stub)) as client:
        assert _upload(client, _copy_zip(lambda entries: change(entries, valid_label=True))).json()["status"] == "ready"
        result = _upload(client, _copy_zip(lambda entries: change(entries, valid_label=False))).json()
        assert (result["status"], result.get("code")) == ("failed", "unsupported_layout")


def test_renamed_html_inside_wrapper_resolves_local_images(tmp_path):
    def change(entries):
        wrapped = {
            f"Wrapper/{'任意.html' if path == 'listing.html' else path}": payload
            for path, payload in entries.items()
        }
        entries.clear()
        entries.update(wrapped)

    with TestClient(create_app(tmp_path / "store.db", translator=stub)) as client:
        snapshot = _upload(client, _copy_zip(change)).json()["snapshot"]
        assert snapshot["title"] == INVENTORY["title"]
        response = client.get(_image_url(snapshot, snapshot["options"][0]))
        assert sha256(response.content).hexdigest() == INVENTORY["options"][0]["image_sha256"]


@pytest.mark.parametrize("entries", [
    [("../outside.html", b"x")],
    [("/absolute.html", b"x")],
    [("C:/absolute.html", b"x")],
    [("__MACOSX/../bad", b"x")],
    [("e\u0301.html", b"x"), ("é.html", b"y")],
    [("folder", b"x"), ("folder/nested", b"y")],
])
def test_unsafe_zip_entries_fail_before_translation(entries, tmp_path):
    async def forbidden(snapshot):
        pytest.fail("Translation ran for unsafe evidence")

    with TestClient(create_app(tmp_path / "store.db", translator=forbidden)) as client:
        assert _upload(client, _zip(entries)).json()["code"] == "invalid_archive"
        assert client.get("/api/case").json() == {"case": None, "snapshot": None}


def test_symbolic_link_entry_is_never_opened(tmp_path):
    link = ZipInfo("link")
    link.create_system = 3
    link.external_attr = (S_IFLNK | 0o777) << 16
    with TestClient(create_app(tmp_path / "store.db", translator=stub)) as client:
        assert _upload(client, _zip([(link, b"listing.html")])).json()["code"] == "invalid_archive"


def test_multiple_pages_and_unsupported_or_ambiguous_sku_maps_fail(tmp_path):
    def add_page(entries):
        entries["second.html"] = b"<h1>Second Page</h1>"

    def change_map(entries):
        original = entries["listing.html"]
        assert b"&gt;720ml" in original
        entries["listing.html"] = original.replace(b"&gt;720ml", b"&gt;900ml", 1)

    with TestClient(create_app(tmp_path / "store.db", translator=stub)) as client:
        for change in (add_page, change_map):
            assert _upload(client, _copy_zip(change)).json()["code"] == "unsupported_layout"


def test_body_limit_applies_to_headers_streamed_bytes_and_file_size(tmp_path):
    async def stream():
        yield b'--cap\r\nContent-Disposition: form-data; name="file"; filename="large.zip"\r\n\r\n'
        chunk = b"x" * MIB
        for _ in range(22):
            yield chunk
        yield b"\r\n--cap--\r\n"

    async def scenario():
        app = create_app(tmp_path / "store.db", translator=stub)
        async with app.router.lifespan_context(app):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                headers = {"Content-Length": str(21 * MIB + 1)}
                preflight = await client.post("/api/imports", headers=headers)
                assert preflight.status_code == 413

                response = await client.post(
                    "/api/imports", content=stream(), headers={"Content-Type": "multipart/form-data; boundary=cap"},
                )
                assert response.status_code == 413
                assert response.json()["message"] == "The upload request exceeds 21 MiB."

                response = await _upload(client, b"x" * (20 * MIB + 1))
                assert response.status_code == 413
                assert response.json()["message"] == "The ZIP exceeds the 20 MiB upload limit."

    asyncio.run(scenario())


def test_archive_entry_and_expanded_limits_apply_before_translation(tmp_path):
    async def forbidden(snapshot):
        pytest.fail("Translation ran for an oversized archive")

    too_many = _zip((f"entry-{index}", b"") for index in range(1_001))
    expanded = BytesIO()
    with ZipFile(expanded, mode="w", compression=ZIP_DEFLATED) as archive:
        with archive.open("listing.html", mode="w") as file:
            block = b" " * MIB
            for _ in range(51):
                file.write(block)

    with TestClient(create_app(tmp_path / "store.db", translator=forbidden)) as client:
        for payload in (too_many, expanded.getvalue(), _zip([("listing.html", b" " * (5 * MIB + 1))])):
            response = _upload(client, payload)
            assert response.status_code == 413
            assert response.json()["code"] == "archive_limit"


def test_parser_work_limit_skips_translation_clears_old_state_and_recovers(tmp_path):
    calls = []

    async def translator(snapshot):
        calls.append(snapshot.snapshot_id)
        return snapshot

    def add_many_tags(entries):
        entries["listing.html"] += b"<br>" * 20_000

    with TestClient(create_app(tmp_path / "store.db", translator=translator)) as client:
        previous = _upload(client).json()["snapshot"]
        response = _upload(client, _copy_zip(add_many_tags))
        assert response.status_code == 413
        assert response.json()["code"] == "archive_limit"
        assert calls == [previous["snapshot_id"]]
        assert client.get("/api/case").json() == {"case": None, "snapshot": None}
        assert client.get(_image_url(previous, previous["options"][0])).status_code == 404
        current = _upload(client, RAW).json()["snapshot"]
        assert client.get("/api/case").json()["snapshot"] == current


def test_restart_recovers_the_snapshot_and_its_image_without_translation(tmp_path):
    database = tmp_path / "store.db"
    with TestClient(create_app(database, translator=stub)) as client:
        snapshot = _upload(client).json()["snapshot"]

    async def forbidden(snapshot):
        pytest.fail("GET requests ran translation after restart")

    with TestClient(create_app(database, translator=forbidden)) as restarted:
        assert restarted.get("/api/case").json() == {"case": None, "snapshot": snapshot}
        saved = restarted.get(_image_url(snapshot, snapshot["options"][0]))
        assert saved.status_code == 200
        assert sha256(saved.content).hexdigest() == INVENTORY["options"][0]["image_sha256"]
        assert restarted.get(f"/api/snapshots/{snapshot['snapshot_id']}/images/unknown").status_code == 404


def test_selection_uses_a_current_snapshot_and_preserves_valid_order(tmp_path):
    with TestClient(create_app(tmp_path / "store.db", translator=stub)) as client:
        snapshot = _upload(client).json()["snapshot"]
        first, second, third, fourth = [option["option_id"] for option in snapshot["options"][:4]]
        fields = {
            "operation_id": str(uuid4()), "snapshot_id": snapshot["snapshot_id"], "previous_case": None,
            "selected_option_ids": [third, first, second], "buyer_context": "",
        }
        assert client.post("/api/selections", json=fields).json() == {
            "status": "confirmed", "selection": {
                "snapshot_id": snapshot["snapshot_id"], "selected_option_ids": [third, first, second], "buyer_context": "",
            },
        }
        one = {**fields, "selected_option_ids": [first], "buyer_context": "  preserved exactly  "}
        assert client.post("/api/selections", json=one).json()["selection"]["buyer_context"] == "  preserved exactly  "
        invalid = [
            {"selected_option_ids": []}, {"selected_option_ids": [first, second, third, fourth]},
            {"selected_option_ids": ["unknown"]}, {"selected_option_ids": [first, first]},
            {"snapshot": snapshot}, {"buyer_context": "x" * 2_001}, {"previous_case": {"revision": 0}},
        ]
        for changes in invalid:
            assert client.post("/api/selections", json={**fields, **changes}).status_code == 422
        assert client.get("/api/case").json()["case"] is None
        assert _upload(client, RAW, headers={"Origin": "http://localhost:5173"}).status_code == 403
        assert client.get("/api/case").json()["snapshot"] == snapshot
        assert _upload(client, RAW).json()["status"] == "ready"
        assert client.post("/api/selections", json=fields).status_code == 409


def test_late_import_cannot_replace_a_newer_upload(tmp_path):
    async def scenario():
        translator_entered = asyncio.Event()
        release_translator = asyncio.Event()
        ids = []

        async def translator(snapshot):
            ids.append(snapshot.snapshot_id)
            if len(ids) == 1:
                translator_entered.set()
                await release_translator.wait()
            return snapshot

        app = create_app(tmp_path / "store.db", translator=translator)
        async with app.router.lifespan_context(app):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                original = asyncio.create_task(_upload(client))
                await asyncio.wait_for(translator_entered.wait(), timeout=5)
                try:
                    newer = await _upload(client, RAW)
                finally:
                    release_translator.set()
                older = await original
                assert newer.json()["status"] == "ready"
                assert older.status_code == 409
                current = (await client.get("/api/case")).json()["snapshot"]
                assert current["snapshot_id"] == ids[1]
                assert (await client.get(f"/api/snapshots/{ids[0]}/images/{current['options'][0]['image_source_id']}")).status_code == 404

    asyncio.run(scenario())


def test_disabled_live_translation_returns_failure_and_never_publishes(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_LIVE_TRANSLATION", "0")
    with TestClient(create_app(tmp_path / "store.db")) as client:
        assert _upload(client).json()["code"] == "intake_failed"
        assert client.get("/api/case").json() == {"case": None, "snapshot": None}


def test_link_import_allows_only_ttp_product_links_and_preserves_prior_snapshot(tmp_path):
    with TestClient(create_app(tmp_path / "store.db", translator=stub)) as client:
        snapshot = _upload(client).json()["snapshot"]
        for url in [
            "http://taobao.ttpcargo.com/product?provider=1688&itemId=1",
            "https://taobao.ttpcargo.com.example.org/product?provider=1688&itemId=1",
            "https://taobao.ttpcargo.com/product?provider=taobao&itemId=1",
            "https://taobao.ttpcargo.com/product?provider=1688&itemId=1&itemId=2",
        ]:
            assert client.post("/api/imports", json={"url": url}).status_code == 422
        assert client.post("/api/imports", json={"url": "x" * 3000}).status_code == 413
        assert client.get("/api/case").json()["snapshot"] == snapshot


def test_link_import_binds_identity_and_fetches_only_mapped_cdn_images(monkeypatch, tmp_path):
    import httpx
    from backend.link_intake import DETAIL_ENDPOINT

    with ZipFile(CURATED) as archive:
        root = BeautifulSoup(archive.read("listing.html").decode(), "html.parser").find(id="ttp-product")
        image_bytes = archive.read(INVENTORY["options"][0]["image"])
    imgs = root.select("img[data-sku]")
    for img in imgs:
        img["src"] = "https://outside.invalid/photo.jpg"
    imgs[0]["src"] = "https://cbu01.alicdn.com/img/ibank/one.jpg"
    imgs[1]["src"] = "https://cbu01.alicdn.com/img/ibank/redirect.jpg"
    fragment = b'<script>var AliProductInfo = {"id":123,"title":"example"};</script>' + root.encode_contents()

    def serve(request):
        if str(request.url) == DETAIL_ENDPOINT:
            assert json.loads(request.content)["ttp_provider"] == "1688"
            return httpx.Response(200, stream=httpx.ByteStream(fragment))
        if str(request.url) == imgs[0]["src"]:
            return httpx.Response(200, stream=httpx.ByteStream(image_bytes))
        if str(request.url) == imgs[1]["src"]:
            return httpx.Response(302, headers={"location": "https://outside.invalid/photo.jpg"})
        pytest.fail("The importer requested a resource outside the mapped SKU images.")

    monkeypatch.setattr("backend.main.fetch_listing", partial(fetch_listing, transport=httpx.MockTransport(serve)))
    with TestClient(create_app(tmp_path / "store.db", translator=stub)) as client:
        url = "https://taobao.ttpcargo.com/product?itemId=123&provider=1688"
        snapshot = client.post("/api/imports", json={"url": url}).json()["snapshot"]
        assert snapshot["title"] == INVENTORY["title"]
        first, *rest = snapshot["options"]
        assert client.get(_image_url(snapshot, first)).content == image_bytes
        assert all(client.get(_image_url(snapshot, option)).status_code == 404 for option in rest)
        result = client.post("/api/imports", json={"url": url.replace("123", "124")}).json()
        assert result["code"] == "unsupported_layout"
        assert client.get("/api/case").json()["snapshot"] is None


@pytest.mark.parametrize("newer_action", ["zip", "reset"])
def test_slow_link_body_cannot_replace_newer_workspace_change(tmp_path, newer_action, monkeypatch):
    async def unexpected_fetch(item_id):
        pytest.fail("An obsolete request reached the upstream fetch.")

    monkeypatch.setattr("backend.main.fetch_listing", unexpected_fetch)

    async def scenario():
        reading = asyncio.Event()
        finish = asyncio.Event()

        async def body():
            yield b'{"url":"https://taobao.ttpcargo.com/product?'
            reading.set()
            await finish.wait()
            yield b'provider=1688&itemId=123"}'

        app = create_app(tmp_path / "store.db", translator=stub)
        async with app.router.lifespan_context(app):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                previous = (await _upload(client)).json()["snapshot"]
                slow = asyncio.create_task(client.post(
                    "/api/imports", content=body(), headers={"Content-Type": "application/json"},
                ))
                await asyncio.wait_for(reading.wait(), timeout=5)
                try:
                    if newer_action == "reset":
                        reset = await client.post("/api/case/reset", json={
                            "snapshot_id": previous["snapshot_id"], "previous_case": None,
                        })
                        assert reset.status_code == 200
                        expected = {"snapshot": None, "case": None}
                    else:
                        expected = {"snapshot": (await _upload(client)).json()["snapshot"], "case": None}
                finally:
                    finish.set()
                assert (await slow).status_code == 409
                assert (await client.get("/api/case")).json() == expected

    asyncio.run(scenario())


@pytest.mark.parametrize("contents", [None, "{}", '{"kind":"pack","sources":[],"limitations":"broken"}'],
                         ids=["missing", "missing-fields", "invalid-limitations"])
def test_valid_zip_reports_server_failure_for_unavailable_official_pack(tmp_path, monkeypatch, contents):
    pack = tmp_path / "official-sources.json"
    monkeypatch.setattr("backend.intake.OFFICIAL_SOURCES_PATH", pack)
    if contents is not None:
        pack.write_text(contents, encoding="utf-8")
    with TestClient(create_app(tmp_path / "store.db", translator=stub)) as client:
        response = _upload(client)
        assert response.status_code == 500
        assert response.json()["code"] == "intake_failed"
        assert client.get("/api/case").json() == {"snapshot": None, "case": None}


@pytest.mark.parametrize("change", [
    {"sources": []}, {"limitations": []}, {"source_override": {"kind": "listing_text"}},
    {"source_override": {"text_en": None}}, {"source_override": {"url": None}},
], ids=["no-guidance", "no-limitations", "seller-source", "missing-translation", "missing-url"])
def test_reference_pack_requires_identifiable_official_evidence(tmp_path, monkeypatch, change):
    pack = json.loads((SAMPLES / "official-sources.json").read_text(encoding="utf-8"))
    if "source_override" in change:
        pack["sources"][0].update(change["source_override"])
    else:
        pack.update(change)
    path = tmp_path / "official-sources.json"
    path.write_text(json.dumps(pack), encoding="utf-8")
    monkeypatch.setattr("backend.intake.OFFICIAL_SOURCES_PATH", path)
    with TestClient(create_app(tmp_path / "store.db", translator=stub)) as client:
        response = _upload(client)
        assert response.status_code == 500
        assert response.json()["code"] == "intake_failed"
        assert client.get("/api/case").json() == {"snapshot": None, "case": None}


def test_link_deadline_cancels_wait_for_headers_and_allows_queued_zip(tmp_path, monkeypatch):
    async def scenario():
        entered = asyncio.Event()
        cancelled = asyncio.Event()

        async def stalled_headers(request):
            entered.set()
            try:
                await asyncio.sleep(2)
                pytest.fail("The stalled request outlived the link deadline.")
            except asyncio.CancelledError:
                cancelled.set()
                raise

        monkeypatch.setattr("backend.link_intake.LINK_TIMEOUT_SECONDS", 0.3)
        monkeypatch.setattr("backend.main.fetch_listing", partial(fetch_listing, transport=MockTransport(stalled_headers)))
        app = create_app(tmp_path / "store.db", translator=stub)
        async with app.router.lifespan_context(app):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                link = asyncio.create_task(client.post("/api/imports", json={
                    "url": "https://taobao.ttpcargo.com/product?provider=1688&itemId=123",
                }))
                await asyncio.wait_for(entered.wait(), timeout=2)
                zip_result = await asyncio.wait_for(_upload(client), timeout=3)
                assert cancelled.is_set()
                assert (await link).status_code == 409
                snapshot = zip_result.json()["snapshot"]
                assert snapshot["title"] == INVENTORY["title"]
                assert (await client.get("/api/case")).json()["snapshot"] == snapshot

    asyncio.run(scenario())
