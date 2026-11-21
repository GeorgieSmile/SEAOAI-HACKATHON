from collections import Counter
from dataclasses import dataclass, replace
import html
from html.parser import HTMLParser
from itertools import permutations
import json
import re

from bs4 import BeautifulSoup, Tag

from .archive import IntakeError


MAX_HTML_TAGS = 20_000
MAX_HTML_TAG_CHARS = 32_768
MAX_HTML_DEPTH = 128
MAX_HTML_REFERENCES = 20_000
MAX_OPTIONS = 128
MAX_ATTRIBUTES = 160
MAX_SOURCE_TEXT_CHARS = 4_096
MAX_JSON_CANDIDATES = 8
MAX_JSON_CHARS = 65_536
MAX_JSON_DEPTH = 24
VOID_TAGS = frozenset("area base br col embed hr img input link meta param source track wbr".split())
SKU_MAP_FIELD = re.compile(r'"skuMap"\s*:\s*')
PRODUCT_ASSIGNMENT = re.compile(r"^\s*var\s+AliProductInfo\s*=\s*")
OPTION_CARD_CLASSES = frozenset({"item-parent", "item-child", "item-child1"})
CDN_IMAGE_URL = re.compile(
    r"https://(?:cbu01\.alicdn\.com/img/ibank/|img\.alicdn\.com/(?:bao/uploaded|imgextra)/i[1-4]/[0-9]+/)[A-Za-z0-9_!-][A-Za-z0-9_!.-]*"
)


def _unsupported(message: str) -> IntakeError:
    return IntakeError("unsupported_layout", message)


def _work_limit() -> IntakeError:
    return IntakeError("archive_limit", "The saved page exceeds the importer’s HTML or JSON work limit.")


class _HTMLBudget(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.tags = 0
        self.references = 0
        self.open_tags: list[str] = []

    def _count(self, length: int) -> None:
        self.tags += 1
        if self.tags > MAX_HTML_TAGS or length > MAX_HTML_TAG_CHARS:
            raise _work_limit()

    def handle_starttag(self, tag: str, attrs: list) -> None:
        self._count(len(self.get_starttag_text() or ""))
        if tag not in VOID_TAGS:
            self.open_tags.append(tag)
            if len(self.open_tags) > MAX_HTML_DEPTH:
                raise _work_limit()

    def handle_startendtag(self, tag: str, attrs: list) -> None:
        self._count(len(self.get_starttag_text() or ""))

    def handle_endtag(self, tag: str) -> None:
        self._count(len(tag))
        if tag in self.open_tags:
            while self.open_tags.pop() != tag:
                pass

    def handle_comment(self, data: str) -> None:
        self._count(len(data))

    def handle_entityref(self, name: str) -> None:
        self.references += 1
        if self.references > MAX_HTML_REFERENCES:
            raise _work_limit()

    handle_charref = handle_entityref


@dataclass(frozen=True)
class Selector:
    key: str
    name: str
    image_url: str | None
    prop_path: str | None
    control_image: str | None


@dataclass(frozen=True)
class Product:
    title: str
    selectors: list[Selector]
    shared_value: str | None
    attributes: list[tuple[str, str]]
    linked_key: str | None


def _source_text(value: str) -> str:
    if len(value) > MAX_SOURCE_TEXT_CHARS:
        raise _work_limit()
    return value.strip()


def _tag_text(tag: Tag) -> str:
    return _source_text(tag.get_text(" ", strip=True))


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _decode_collection(script: str, *, offset: int = 0) -> tuple[dict | list, int]:
    if script[offset:offset + 1] not in ("{", "["):
        raise _unsupported("The saved SKU mapping is unreadable.")

    # Bound the candidate before handing it to the JSON decoder. Track quoted
    # strings so a brace in a label does not affect the structural budget.
    depth = 0
    quoted = False
    escaped = False
    for end in range(offset, min(len(script), offset + MAX_JSON_CHARS)):
        char = script[end]
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > MAX_JSON_DEPTH:
                raise _work_limit()
        elif char in "]}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(script[offset:end + 1], object_pairs_hook=_unique_object), end + 1
                except (ValueError, RecursionError) as error:
                    raise _unsupported("The saved SKU mapping is unreadable.") from error

    if len(script) - offset >= MAX_JSON_CHARS:
        raise _work_limit()
    raise _unsupported("The saved SKU mapping is incomplete.")


def _sku_map_candidates(root: Tag, expected_item_id: str | None) -> list[dict | list]:
    candidates = []
    matched_product = False
    embedded = root.find_all(id="source-sku-map", limit=2)
    if len(embedded) > 1:
        raise _unsupported("The saved page contains conflicting SKU mapping blocks.")
    if embedded:
        block = embedded[0]
        if block.name != "script" or block.get("type") != "application/json" or block.get("src"):
            raise _unsupported("The saved SKU mapping block is unreadable.")
        script = (block.string or "").strip()
        candidate, end = _decode_collection(script)
        value = candidate.get("skuMap") if isinstance(candidate, dict) else None
        if script[end:].strip() or not isinstance(value, (dict, list)):
            raise _unsupported("The saved SKU mapping block is unreadable.")
        candidates.append(value)

    for block in root.find_all("script"):
        if block.get("src") or not block.string or block.get("id") == "source-sku-map":
            continue
        script = str(block.string)
        offset = 0
        if expected_item_id is not None and (assignment := PRODUCT_ASSIGNMENT.match(script)):
            if len(candidates) >= MAX_JSON_CANDIDATES:
                raise _work_limit()
            product, offset = _decode_collection(script, offset=assignment.end())
            if matched_product or not isinstance(product, dict) or str(product.get("id")) != expected_item_id:
                raise _unsupported("TTP Cargo did not return the requested product. Choose a saved ZIP.")
            if not isinstance(product.get("skuMap"), (dict, list)):
                raise _unsupported("The requested product does not include its SKU mapping.")
            matched_product = True
            candidates.append(product["skuMap"])
        for match in SKU_MAP_FIELD.finditer(script, offset):
            if len(candidates) >= MAX_JSON_CANDIDATES:
                raise _work_limit()
            candidate, _ = _decode_collection(script, offset=match.end())
            candidates.append(candidate)
    if expected_item_id is not None and not matched_product:
        raise _unsupported("TTP Cargo did not return the requested product. Choose a saved ZIP.")
    return candidates


def cdn_image_url(url: str) -> str | None:
    secure = "https://" + url[7:] if url.startswith("http://") else url
    return secure if CDN_IMAGE_URL.fullmatch(secure) else None


def _check_image_sources(*urls: str | None) -> None:
    # The card may request a resized copy of the original Alibaba asset. Keep
    # browser-saved local paths opaque for the ZIP resolver.
    assets = set()
    for url in urls:
        if isinstance(url, str) and (remote := cdn_image_url(url)):
            assets.add(re.sub(r"(\.(?:jpe?g|png|webp|gif))_[0-9]+x[0-9]+\.(?:jpe?g|png|webp|gif)$",
                              r"\1", remote, flags=re.IGNORECASE))
    if len(assets) > 1:
        raise _unsupported("The SKU thumbnail and its source image identify different assets.")


def _matches_sku_name(name: object, properties: list[dict]) -> bool:
    if not isinstance(name, str):
        return False
    # The supported layout has one or two properties. Match whole seller
    # values so punctuation within a label stays part of the option name.
    parts = []
    for prop in properties:
        value = re.escape(html.unescape(prop["value"]).strip())
        label = prop.get("name")
        if isinstance(label, str) and label.strip():
            value = rf"(?:{re.escape(html.unescape(label).strip())}\s*:\s*)?" + value
        parts.append(value)
    name = html.unescape(name).strip()
    return any(re.fullmatch(r"\s*[;>/]\s*".join(order), name) for order in permutations(parts))


def _retail_sku_ids(rows: list, selectors: list[Selector], shared: Selector | None,
                    row_images: dict[str, str]) -> dict[str, str]:
    # Tmall and Taobao identify each variant using property:value IDs. Join those
    # IDs and their seller labels to the radio controls which own the thumbnail.
    by_path = {selector.prop_path: selector for selector in selectors}
    if len(by_path) != len(selectors) or None in by_path or (shared and shared.prop_path in by_path):
        raise _unsupported("The product has ambiguous SKU property paths.")
    sku_ids = {}
    seller_names = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("properties"), list):
            raise _unsupported("The product has an unreadable SKU mapping.")
        values = {}
        for prop in row["properties"]:
            if not isinstance(prop, dict):
                raise _unsupported("The SKU property is unreadable.")
            path = prop.get("propPath")
            if (not isinstance(path, str) or not re.fullmatch(r"[0-9]+:[0-9]+", path)
                    or path != f"{prop.get('propId')}:{prop.get('valueId')}" or path in values
                    or not isinstance(prop.get("value"), str)):
                raise _unsupported("The SKU property path is ambiguous.")
            values[path] = html.unescape(prop["value"]).strip()
        paths = row["propPath"].split(";") if isinstance(row.get("propPath"), str) else []
        if len(paths) != len(values) or set(paths) != values.keys():
            raise _unsupported("The SKU property paths disagree.")
        if shared and values.pop(shared.prop_path, None) != shared.name:
            raise _unsupported("The SKU mapping disagrees with the shared selector.")
        if len(values) != 1:
            raise _unsupported("The SKU has unsupported option groups.")
        path, value = next(iter(values.items()))
        selector = by_path.get(path)
        sku_id = str(row.get("skuId", ""))
        if (selector is None or value != selector.name or selector.key in sku_ids
                or not re.fullmatch(r"[1-9][0-9]{0,17}", sku_id)):
            raise _unsupported("The SKU mapping disagrees with the product selector.")
        if "skuName" in row:
            if not _matches_sku_name(row["skuName"], row["properties"]):
                raise _unsupported("The SKU seller name disagrees with the product selector.")
            seller_name = html.unescape(row["skuName"]).strip()
            if seller_name in seller_names:
                raise _unsupported("The same seller name identifies multiple SKU rows.")
            seller_names.add(seller_name)
        image = row.get("imageUrl")
        if isinstance(image, str) and (image := _source_text(image)):
            prior = row_images.get(selector.key)
            _check_image_sources(selector.image_url, selector.control_image, prior, image)
            if prior and prior != image and not (cdn_image_url(image) and cdn_image_url(prior)):
                raise _unsupported("The SKU mapping copies disagree about the source image.")
            row_images.setdefault(selector.key, image)
        sku_ids[selector.key] = sku_id
    if len(sku_ids) != len(selectors) or len(set(sku_ids.values())) != len(sku_ids):
        raise _unsupported("The product does not establish one ID per SKU.")
    return sku_ids


def _validate_mapping(candidates: list[dict | list], selectors: list[Selector], shared: Selector | None,
                      requested_sku_id: str | None, expected_provider: str | None) -> tuple[str | None, dict[str, str]]:
    if not candidates:
        raise _unsupported("The saved page does not establish a single SKU mapping.")
    mappings = candidates[0]
    if expected_provider is not None:
        expected_shape = dict if expected_provider == "1688" else list
        if not isinstance(mappings, expected_shape):
            raise _unsupported("The SKU mapping does not match the link’s provider.")
    if len(mappings) > MAX_OPTIONS:
        raise _work_limit()
    row_images = {}
    if isinstance(mappings, list):
        sku_ids = _retail_sku_ids(mappings, selectors, shared, row_images)
    else:
        expected = {f"{s.key}>{shared.key}" if shared else s.key: s.key for s in selectors}
        sku_ids = {}
        for key, record in mappings.items():
            decoded = html.unescape(key)
            selector_key = expected.get(decoded)
            if (selector_key is None or selector_key in sku_ids or not isinstance(record, dict)
                    or ("specAttrs" in record and (
                        not isinstance(record["specAttrs"], str) or html.unescape(record["specAttrs"]) != decoded
                    ))):
                raise _unsupported("The saved SKU mapping disagrees with the product selector.")
            sku_ids[selector_key] = str(record.get("skuId", ""))
        if len(sku_ids) != len(selectors):
            raise _unsupported("The saved SKU mapping disagrees with the product selector.")

    for other in candidates[1:]:
        if other == mappings:
            continue
        if len(other) > MAX_OPTIONS:
            raise _work_limit()
        # Retail fragments can repeat the map in several scripts. Each copy
        # must independently corroborate the same SKU-to-control bindings.
        if (not isinstance(other, list) or not isinstance(mappings, list)
                or _retail_sku_ids(other, selectors, shared, row_images) != sku_ids):
            raise _unsupported("The saved page contains conflicting SKU mappings.")

    if requested_sku_id is None:
        return None, row_images
    matches = [key for key, sku_id in sku_ids.items() if sku_id == requested_sku_id]
    if len(matches) != 1:
        raise _unsupported("The skuId in this link could not be matched to one option on the listing.")
    return matches[0], row_images


def _selectors(group: Tag, kind: str, document_id_counts: Counter[str]) -> list[Selector]:
    inputs = group.find_all("input", attrs={"data-sku-type": kind}, limit=MAX_OPTIONS + 1)
    if len(inputs) > MAX_OPTIONS:
        raise _work_limit()

    options: dict[str, tuple[str, str | None, str | None]] = {}
    control_keys: dict[str, str] = {}
    container_keys: dict[int, str | None] = {}
    for item in inputs:
        key = _source_text(item.get("data-sku", ""))
        name = _source_text(item.get("data-label", ""))
        if not key or not name or ">" in key or key in options or item.get("type") != "radio":
            raise _unsupported("The saved product contains ambiguous SKU options.")
        options[key] = (name, item.get("data-image", "").strip() or None, item.get("data-prop-path"))

        control_id = item.get("id")
        if control_id and document_id_counts[control_id] == 1:
            control_keys[control_id] = key
        for container in item.parents:
            if container is group:
                break
            # A container with several controls owns no individual option.
            identity = id(container)
            container_keys[identity] = None if identity in container_keys else key

    for label in group.find_all("label", attrs={"for": True}):
        target = label["for"]
        if not target:
            continue
        key = control_keys.get(target)
        for container in (label, *label.parents):
            if container is group:
                break
            if OPTION_CARD_CLASSES.isdisjoint(container.get("class", [])):
                continue
            # Explicit label ownership extends to sibling images in the card.
            # A label may supply missing ownership, but cannot resolve a conflict.
            identity = id(container)
            if container_keys.setdefault(identity, key) != key:
                container_keys[identity] = None

    images = {}
    for image in group.find_all("img", attrs={"data-sku": True}):
        key = _source_text(image["data-sku"])
        if key not in options:
            raise _unsupported("The saved product contains an image without a matching SKU selector.")

        for container in image.parents:
            if container is group:
                break
            owner = container_keys.get(id(container))
            label_target = container.get("for") if container.name == "label" else None
            classes = container.get("class", [])
            if owner is not None and owner != key:
                raise _unsupported("A saved SKU image disagrees with the selector that contains it.")
            if not OPTION_CARD_CLASSES.isdisjoint(classes) and (
                owner != key or f"item-{kind}" not in classes
            ):
                raise _unsupported("A saved SKU image is in an ambiguous option card.")
            if label_target and control_keys.get(label_target) != key:
                raise _unsupported("A saved SKU image disagrees with its label’s selector.")

        url = image.get("src", "").strip()
        _check_image_sources(url, options[key][1])
        if images.setdefault(key, url) != url:
            raise _unsupported("The saved product contains conflicting images for one SKU.")

    return [Selector(key, name, images.get(key) or fallback, path, fallback)
            for key, (name, fallback, path) in options.items()]


def _attributes(root: Tag) -> list[tuple[str, str]]:
    containers = root.find_all(id="attr-data", limit=2)
    if len(containers) > 1:
        raise _unsupported("The saved product contains ambiguous attribute sections.")
    if not containers:
        return []

    rows = containers[0].find_all("div", recursive=False, limit=MAX_ATTRIBUTES + 1)
    if len(rows) > MAX_ATTRIBUTES:
        raise _work_limit()
    result = []
    for row in rows:
        labels = row.find_all("span", class_="Tdetail", recursive=False, limit=2)
        values = row.find_all("span", class_="Vdetail", recursive=False, limit=2)
        if len(labels) == len(values) == 1:
            label, value = _tag_text(labels[0]), _tag_text(values[0])
            if label and value:
                result.append((label, value))
    return result


def read_product(payload: bytes, *, expected_item_id: str | None = None, requested_sku_id: str | None = None,
                 expected_provider: str | None = None) -> Product:
    try:
        markup = payload.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise _unsupported("The saved product page is not UTF-8.") from error

    budget = _HTMLBudget()
    budget.feed(markup)
    budget.close()
    document = BeautifulSoup(markup, "html.parser")
    roots = document.find_all(id="ttp-product", limit=2)
    if len(roots) != 1:
        raise _unsupported("The ZIP does not contain one supported TTP Cargo product page.")
    root = roots[0]
    candidates = _sku_map_candidates(root, expected_item_id)
    titles = root.find_all("h1", limit=2)
    if len(titles) != 1 or not (title := _tag_text(titles[0])):
        raise IntakeError("product_unreadable", "No unambiguous product title could be read.")

    groups = root.find_all(attrs={"data-sku-group-type": True}, limit=3)
    main = [group for group in groups if group["data-sku-group-type"] == "parent"]
    child = [group for group in groups if group["data-sku-group-type"] == "child"]
    # Some retail listings render the sole selector group as child1.
    if len(groups) == 1 and groups[0]["data-sku-group-type"] == "child1":
        main = groups
    if len(main) != 1 or len(child) > 1 or len(groups) != len(main) + len(child):
        raise _unsupported("The saved product has unsupported SKU option groups.")
    # HTML labels resolve by ID across the whole saved document.
    document_id_counts = Counter(tag["id"] for tag in document.find_all(id=True))
    selectors = _selectors(main[0], main[0]["data-sku-group-type"], document_id_counts)
    if not selectors:
        raise IntakeError("product_unreadable", "No readable SKU options were found on the saved product page.")
    shared = None
    if child:
        secondary = _selectors(child[0], "child", document_id_counts)
        if len(secondary) != 1:
            raise _unsupported("The saved product does not have one shared secondary selector value.")
        shared = secondary[0]
    linked_key, row_images = _validate_mapping(candidates, selectors, shared, requested_sku_id, expected_provider)
    selectors = [s if s.image_url else replace(s, image_url=row_images.get(s.key)) for s in selectors]
    return Product(title, selectors, shared.name if shared else None, _attributes(root), linked_key)
