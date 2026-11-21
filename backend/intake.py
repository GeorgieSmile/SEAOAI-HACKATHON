from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal
from urllib.parse import unquote, urlsplit
from uuid import uuid4
from zipfile import BadZipFile, ZipFile
from zlib import error as ZlibError

from PIL import Image
from pydantic import Field

from .archive import IntakeError, ListingArchive, MAX_HTML_BYTES, MAX_IMAGE_BYTES, MAX_ZIP_BYTES, preflight_directory, safe_path
from .contracts import ContractModel, ListingSnapshot, NonEmptyText, Option, Source
from .listing_html import Product, read_product


MAX_IMAGE_PIXELS = 25_000_000
IMAGE_MIME = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}
OFFICIAL_SOURCES_PATH = Path(__file__).resolve().parents[1] / "samples" / "official-sources.json"
THUMBNAIL_LIMITATION = (
    "Some saved SKU images are thumbnails. They cannot establish how the decoration was applied or how long it lasts."
)


@dataclass(frozen=True)
class ImageData:
    mime: str
    payload: bytes
    digest: str


@dataclass(frozen=True)
class ParsedListing:
    snapshot: ListingSnapshot
    images: dict[str, ImageData]


@dataclass(frozen=True)
class ImageResult:
    availability: Literal["available", "missing", "unreadable"]
    data: ImageData | None = None
    limitation: str | None = None


def _local_image_path(url: str, main_html: PurePosixPath) -> PurePosixPath | None:
    try:
        uri = urlsplit(url)
        if uri.scheme or uri.netloc or uri.path.startswith("/"):
            return None
        decoded = unquote(uri.path, errors="strict")
        return main_html.parent / safe_path(decoded.removeprefix("./"))
    except (ValueError, UnicodeError):
        return None


def _read_image(archive: ListingArchive, path: PurePosixPath) -> ImageResult:
    if archive.files[path].file_size > MAX_IMAGE_BYTES:
        return ImageResult("unreadable", limitation="The saved image exceeds the 10 MiB limit.")
    return decode_image(archive.read(path, MAX_IMAGE_BYTES))


def decode_image(payload: bytes) -> ImageResult:
    try:
        with Image.open(BytesIO(payload), formats=list(IMAGE_MIME)) as image:
            if (
                image.width * image.height > MAX_IMAGE_PIXELS
                or getattr(image, "n_frames", 1) != 1
                or image.format not in IMAGE_MIME
            ):
                return ImageResult("unreadable", limitation="The image is animated or exceeds the pixel limit.")
            mime = IMAGE_MIME[image.format]
            image.verify()
        with Image.open(BytesIO(payload), formats=list(IMAGE_MIME)) as image:
            image.load()
            thumbnail = image.width <= 150 and image.height <= 150
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        return ImageResult("unreadable", limitation="The saved image is damaged or uses an unsupported format.")

    limitation = THUMBNAIL_LIMITATION if thumbnail else None
    return ImageResult("available", ImageData(mime, payload, sha256(payload).hexdigest()), limitation)


def _text_source(source_id: str, label: str, text: str, option_ids: list[str]) -> Source:
    return Source(
        source_id=source_id, label=label, kind="listing_text", option_ids=option_ids,
        availability="available", original_text=text, text_en=None, image_file=None, url=None,
    )


class OfficialGuidance(Source):
    # The v1 pack is the single DMSC excerpt specified in docs/reference-pack.md.
    source_id: Literal["dmsc-food-contact"]
    kind: Literal["official_guidance"]
    availability: Literal["available"]
    option_ids: Annotated[list[NonEmptyText], Field(max_length=0)]
    original_text: NonEmptyText
    text_en: NonEmptyText
    url: Literal["https://www.dmsc.moph.go.th/th/detailAll/3898/nw/25"]


class ReferencePack(ContractModel):
    kind: NonEmptyText
    sources: Annotated[list[OfficialGuidance], Field(min_length=1, max_length=1)]
    limitations: Annotated[list[NonEmptyText], Field(min_length=1)]


def add_official_sources(parsed: ParsedListing) -> ParsedListing:
    pack = ReferencePack.model_validate_json(OFFICIAL_SOURCES_PATH.read_bytes())
    snapshot = parsed.snapshot.model_dump()
    snapshot["sources"].extend(pack.sources)
    snapshot["limitations"].extend(pack.limitations)
    return ParsedListing(ListingSnapshot.model_validate(snapshot), parsed.images)


def build_listing(product: Product, resolve_image: Callable[[str], ImageResult]) -> ParsedListing:
    options = []
    sources = [_text_source("product-title", "Product title", product.title, [])]
    images: dict[str, ImageData] = {}
    limitations = []

    for selector in product.selectors:
        option_id = f"opt-{sha256(selector.key.encode()).hexdigest()[:24]}"
        sources.append(_text_source(f"{option_id}-name", "SKU name", selector.name, [option_id]))
        image_id = None
        if selector.image_url is None:
            limitations.append(f"{selector.name} has no SKU-specific image reference in the saved page.")
        else:
            image_id = f"{option_id}-image"
            result = resolve_image(selector.image_url)
            if result.limitation:
                if result.availability != "available":
                    limitations.append(f"{selector.name} — {result.limitation}")
                elif result.limitation not in limitations:
                    limitations.append(result.limitation)
            if result.data is not None:
                images[image_id] = result.data
            sources.append(Source(
                source_id=image_id, label="SKU image", kind="listing_image", option_ids=[option_id],
                availability=result.availability, original_text=None, text_en=None,
                image_file=image_id if result.data is not None else None, url=None,
            ))
        options.append(Option(option_id=option_id, name_original=selector.name, name_en=None, image_source_id=image_id))

    if product.shared_value is not None:
        sources.append(_text_source(
            "shared-selector-value", "Shared selector value", product.shared_value,
            [option.option_id for option in options],
        ))
    for index, (label, value) in enumerate(product.attributes, start=1):
        sources.append(_text_source(f"attribute-{index}", "Product attribute", f"{label}: {value}", []))

    return ParsedListing(
        ListingSnapshot(
            snapshot_id=str(uuid4()), title=product.title, options=options,
            sources=sources, limitations=limitations,
        ),
        images,
    )


def _parse_archive(archive: ListingArchive) -> ParsedListing:
    main_html = archive.main_html()
    product = read_product(archive.read(main_html, MAX_HTML_BYTES))
    image_cache: dict[PurePosixPath, ImageResult] = {}

    def resolve_image(url: str) -> ImageResult:
        path = _local_image_path(url, main_html)
        if path is None or path not in archive.files:
            return ImageResult("missing", limitation="The SKU image is not included in the ZIP.")
        if path not in image_cache:
            image_cache[path] = _read_image(archive, path)
        return image_cache[path]

    parsed = build_listing(product, resolve_image)
    archive.validate_remaining()
    return parsed


def parse_listing(payload: bytes) -> ParsedListing:
    if len(payload) > MAX_ZIP_BYTES:
        raise IntakeError("archive_limit", "The ZIP exceeds the 20 MiB upload limit.")
    preflight_directory(payload)
    try:
        with ZipFile(BytesIO(payload), metadata_encoding="utf-8") as zip_file:
            parsed = _parse_archive(ListingArchive(zip_file))
    except IntakeError:
        raise
    except UnicodeDecodeError as error:
        raise IntakeError("unsupported_layout", "The ZIP entry names are not readable UTF-8.") from error
    except (BadZipFile, EOFError, OSError, RuntimeError, ValueError, ZlibError) as error:
        raise IntakeError("invalid_archive", "The ZIP is damaged or uses unsupported archive settings.") from error

    return add_official_sources(parsed)
