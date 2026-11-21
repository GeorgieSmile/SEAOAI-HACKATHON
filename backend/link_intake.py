"""Read the public product fragment used by TTP Cargo's listing pages."""

import asyncio
from dataclasses import dataclass
import re

import httpx

from .archive import IntakeError, MAX_EXPANDED_BYTES, MAX_HTML_BYTES, MAX_IMAGE_BYTES
from .intake import ImageResult, ParsedListing, add_official_sources, build_listing, decode_image
from .listing_html import cdn_image_url, read_product


SITE = "https://taobao.ttpcargo.com"
DETAIL_ENDPOINT = "https://8gztswndy7.execute-api.ap-southeast-2.amazonaws.com/itemDetail1688/ttpcargoAPI"
RETAIL_ENDPOINT = "https://9b6mppzpzk.execute-api.ap-southeast-2.amazonaws.com/default/itemDetailTaobao_99API"
ENDPOINTS = {"1688": DETAIL_ENDPOINT, "taobao": RETAIL_ENDPOINT, "tmall": RETAIL_ENDPOINT}
LINK_HINT = "Use a TTP Cargo product link with provider 1688, taobao, or tmall, an itemId, and optionally a skuId."
LINK_TIMEOUT_SECONDS = 75
FETCH_FAILED = "TTP Cargo could not load this listing. Try again or choose a saved ZIP."


@dataclass(frozen=True)
class ProductLink:
    provider: str
    item_id: str
    sku_id: str | None


def parse_product_link(url: str) -> ProductLink:
    base, separator, query = url.strip().partition("?")
    fields = {}
    for pair in query.split("&"):
        key, equals, value = pair.partition("=")
        if not equals or key in fields or key not in {"provider", "itemId", "skuId"}:
            raise ValueError(LINK_HINT)
        fields[key] = value
    if (base != SITE + "/product" or not separator or fields.get("provider") not in ENDPOINTS
            or not re.fullmatch(r"[1-9][0-9]{0,17}", fields.get("itemId", ""))
            or ("skuId" in fields and not re.fullmatch(r"[1-9][0-9]{0,17}", fields["skuId"]))):
        raise ValueError(LINK_HINT)
    return ProductLink(fields["provider"], fields["itemId"], fields.get("skuId"))


async def fetch_listing(link: ProductLink, *, transport: httpx.AsyncBaseTransport | None = None) -> ParsedListing:
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
            fragment = await download("POST", ENDPOINTS[link.provider], MAX_HTML_BYTES, json={
                "action": "ttp_get_single", "ttp_provider": link.provider, "ttp_itemid": int(link.item_id),
            })
        except IntakeError:
            raise
        except OverflowError as error:
            raise IntakeError("archive_limit", "The live product HTML exceeds 5 MiB.") from error
        except (httpx.HTTPError, ValueError) as error:
            raise IntakeError("intake_failed", FETCH_FAILED) from error

        product = await asyncio.to_thread(
            read_product, b'<div id="ttp-product">' + fragment + b'</div>',
            expected_item_id=link.item_id, requested_sku_id=link.sku_id, expected_provider=link.provider,
        )
        images: dict[str, ImageResult] = {}
        for selector in product.selectors:
            url = selector.image_url
            if url is None or url in images:
                continue
            result = ImageResult("missing", limitation="The SKU image could not be retrieved from the listing.")
            if image_url := cdn_image_url(url):
                try:
                    payload = await download("GET", image_url, MAX_IMAGE_BYTES)
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
