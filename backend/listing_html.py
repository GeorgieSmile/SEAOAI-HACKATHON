from dataclasses import dataclass
import html
from html.parser import HTMLParser
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


@dataclass(frozen=True)
class Product:
    title: str
    selectors: list[Selector]
    shared_value: str | None
    attributes: list[tuple[str, str]]


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


def _decode_object(script: str, *, offset: int = 0) -> tuple[dict[str, object], int]:
    if script[offset:offset + 1] != "{":
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


def _sku_map_candidates(root: Tag) -> list[dict[str, object]]:
    embedded = root.find_all(id="source-sku-map", limit=2)
    if len(embedded) > 1:
        raise _unsupported("The saved page contains conflicting SKU mapping blocks.")
    if embedded:
        block = embedded[0]
        if block.name != "script" or block.get("type") != "application/json" or block.get("src"):
            raise _unsupported("The saved SKU mapping block is unreadable.")
        script = (block.string or "").strip()
        candidate, end = _decode_object(script)
        value = candidate.get("skuMap")
        if script[end:].strip() or not isinstance(value, dict):
            raise _unsupported("The saved SKU mapping block is unreadable.")
        return [value]

    candidates = []
    for block in root.find_all("script"):
        if block.get("src") or not block.string:
            continue
        script = str(block.string)
        for match in SKU_MAP_FIELD.finditer(script):
            if len(candidates) >= MAX_JSON_CANDIDATES:
                raise _work_limit()
            candidate, _ = _decode_object(script, offset=match.end())
            candidates.append(candidate)
    return candidates


def _validate_mapping(root: Tag, selectors: list[Selector], shared_key: str | None) -> None:
    candidates = _sku_map_candidates(root)
    if not candidates or any(candidate != candidates[0] for candidate in candidates[1:]):
        raise _unsupported("The saved page does not establish a single SKU mapping.")
    mappings = candidates[0]
    if len(mappings) > MAX_OPTIONS:
        raise _work_limit()
    expected = {f"{selector.key}>{shared_key}" if shared_key else selector.key for selector in selectors}
    actual = set()
    for key, record in mappings.items():
        decoded = html.unescape(key)
        if (
            decoded in actual
            or not isinstance(record, dict)
            or ("specAttrs" in record and (
                not isinstance(record["specAttrs"], str) or html.unescape(record["specAttrs"]) != decoded
            ))
        ):
            raise _unsupported("The saved SKU mapping disagrees with the product selector.")
        actual.add(decoded)
    if actual != expected:
        raise _unsupported("The saved SKU mapping disagrees with the product selector.")


def _selectors(group: Tag, kind: str) -> list[Selector]:
    inputs = group.find_all("input", attrs={"data-sku-type": kind}, limit=MAX_OPTIONS + 1)
    if len(inputs) > MAX_OPTIONS:
        raise _work_limit()
    images: dict[str, set[str]] = {}
    for image in group.find_all("img", attrs={"data-sku": True}):
        key = image["data-sku"]
        images.setdefault(key, set()).add(image.get("src", "").strip())

    result = []
    keys = set()
    for item in inputs:
        key = _source_text(item.get("data-sku", ""))
        name = _source_text(item.get("data-label", ""))
        if not key or not name or ">" in key or key in keys or item.get("type") != "radio":
            raise _unsupported("The saved product contains ambiguous SKU options.")
        keys.add(key)
        matches = images.get(key, set())
        if len(matches) > 1:
            raise _unsupported("The saved product contains conflicting images for one SKU.")
        image_url = next(iter(matches)) if matches else item.get("data-image", "").strip() or None
        result.append(Selector(key, name, image_url))
    if images.keys() - keys:
        raise _unsupported("The saved product contains an image without a matching SKU selector.")
    return result


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


def read_product(payload: bytes) -> Product:
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
    titles = root.find_all("h1", limit=2)
    if len(titles) != 1 or not (title := _tag_text(titles[0])):
        raise IntakeError("product_unreadable", "No unambiguous product title could be read.")

    groups = root.find_all(attrs={"data-sku-group-type": True}, limit=3)
    main = [group for group in groups if group["data-sku-group-type"] == "parent"]
    child = [group for group in groups if group["data-sku-group-type"] == "child"]
    if len(main) != 1 or len(child) > 1 or len(groups) != len(main) + len(child):
        raise _unsupported("The saved product has unsupported SKU option groups.")
    selectors = _selectors(main[0], "parent")
    if not selectors:
        raise IntakeError("product_unreadable", "No readable SKU options were found on the saved product page.")
    shared_key = shared_value = None
    if child:
        secondary = _selectors(child[0], "child")
        if len(secondary) != 1:
            raise _unsupported("The saved product does not have one shared secondary selector value.")
        shared_key, shared_value = secondary[0].key, secondary[0].name
    _validate_mapping(root, selectors, shared_key)
    return Product(title, selectors, shared_value, _attributes(root))
