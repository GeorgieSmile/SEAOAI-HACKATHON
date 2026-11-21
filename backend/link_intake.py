"""Read the public product fragment used by TTP Cargo's 1688 listing page."""

import asyncio
import re

import httpx

from .archive import IntakeError, MAX_EXPANDED_BYTES, MAX_HTML_BYTES, MAX_IMAGE_BYTES
from .intake import ImageResult, ParsedListing, add_official_sources, build_listing, decode_image
from .listing_html import read_product


SITE = "https://taobao.ttpcargo.com"
DETAIL_ENDPOINT = "https://8gztswndy7.execute-api.ap-southeast-2.amazonaws.com/itemDetail1688/ttpcargoAPI"
PRODUCT_URL = re.compile(
    re.escape(SITE) + r"/product\?(?:provider=1688&itemId=([1-9][0-9]{0,17})|itemId=([1-9][0-9]{0,17})&provider=1688)"
)
# Accept the SKU-thumbnail paths emitted by this provider; never follow redirects.
IMAGE_URL = re.compile(r"https://cbu01\.alicdn\.com/img/ibank/[A-Za-z0-9_!.-]+")
LINK_HINT = "Paste a TTP Cargo product link with provider=1688 and a numeric itemId."
LINK_TIMEOUT_SECONDS = 75
FETCH_FAILED = "TTP Cargo could not load this listing. Try again or choose a saved ZIP."


def item_id_from_link(url: str) -> str:
    match = PRODUCT_URL.fullmatch(url.strip())
    if not match:
        raise ValueError(LINK_HINT)
    return match[1] or match[2]


async def fetch_listing(item_id: str, *, transport: httpx.AsyncBaseTransport | None = None) -> ParsedListing:
    deadline = asyncio.get_running_loop().time() + LINK_TIMEOUT_SECONDS
    remaining_bytes = MAX_EXPANDED_BYTES

    async def download(method: str, url: str, limit: int, **kwargs) -> bytes:
        nonlocal remaining_bytes
        # All requests share one deadline, including time spent waiting for headers.
        try:
            async with asyncio.timeout_at(deadline):
                async with client.stream(method, url, **kwargs) as response:
                    response.raise_for_status()
                    if response.headers.get("content-encoding", "identity").lower() != "identity":
                        raise ValueError("Unsupported content encoding")
                    body = bytearray()
                    async for chunk in response.aiter_raw():
                        remaining_bytes -= len(chunk)
                        if remaining_bytes < 0:
                            raise IntakeError("archive_limit", "The live listing exceeds the 50 MiB import limit.")
                        if len(body) + len(chunk) > limit:
                            raise OverflowError
                        body.extend(chunk)
                    return bytes(body)
        except TimeoutError as error:
            raise IntakeError("intake_failed", FETCH_FAILED) from error

    async with httpx.AsyncClient(
        headers={"Accept-Encoding": "identity", "Origin": SITE, "Referer": SITE + "/"},
        follow_redirects=False, trust_env=False, transport=transport, timeout=32,
    ) as client:
        try:
            fragment = await download("POST", DETAIL_ENDPOINT, MAX_HTML_BYTES, json={
                "action": "ttp_get_single", "ttp_provider": "1688", "ttp_itemid": int(item_id),
            })
        except IntakeError:
            raise
        except OverflowError as error:
            raise IntakeError("archive_limit", "The live product HTML exceeds 5 MiB.") from error
        except (httpx.HTTPError, ValueError) as error:
            raise IntakeError("intake_failed", FETCH_FAILED) from error

        product = await asyncio.to_thread(
            read_product, b'<div id="ttp-product">' + fragment + b'</div>', expected_item_id=item_id,
        )
        images: dict[str, ImageResult] = {}
        for selector in product.selectors:
            url = selector.image_url
            if url is None or url in images:
                continue
            result = ImageResult("missing", limitation="The SKU image could not be retrieved from the listing.")
            if IMAGE_URL.fullmatch(url):
                try:
                    payload = await download("GET", url, MAX_IMAGE_BYTES)
                    result = await asyncio.to_thread(decode_image, payload)
                except OverflowError:
                    result = ImageResult("unreadable", limitation="The SKU image exceeds the 10 MiB limit.")
                except IntakeError:
                    raise
                except (httpx.HTTPError, ValueError):
                    pass
            images[url] = result

    def assemble() -> ParsedListing:
        return add_official_sources(build_listing(product, images.__getitem__))

    return await asyncio.to_thread(assemble)
