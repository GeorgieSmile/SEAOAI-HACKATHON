# raw-save-redacted.zip

A redacted copy of a real raw browser save ("Save page as, complete", then zipped in Finder) of the
same TTP Cargo product as `samples/listing.zip`. It exists to test the importer on an unprocessed save.
Unlike `samples/listing.zip` it is the whole page: navigation, site scripts, tracking, admin/debug
blocks inside `#ttp-product`, many CSS/JS files and iframe pages. Not a derived product-only copy.

- Input sha256 (user-supplied file, not stored in this repo): `f15b98345810e8c74f218a82ecb5f5f39144f0fc1415948264b9de751256908d`
- Output sha256: `bec7972b007632a4bc8b25ba5f5bf9af4c22a642105824e6d32f0f1e935fb12a`
- The original browser save and the rehearsal tool are kept outside this package.
- Use [the ZIP](raw-save-redacted.zip) only as an upload to the static importer. The saved page includes scripts and other site assets.

## Structure kept

All 314 entries and their order, names, dates and modes. File names are UTF-8 bytes **without**
the ZIP UTF-8 flag (as Finder writes them), which is what the importer must accept. The main HTML sits at the
top level beside its `*_files/` folder; `__MACOSX/` AppleDouble entries are present. Images (and other binary
files, 58 files) are byte-identical to the input. Product data is untouched: title, SKU names/images,
attribute list, `AliProductInfo` script and the admin/debug `<pre>` dumps of product data
(these contain product data only; they are kept so tests can prove the importer ignores that block).

## What was redacted (kinds and counts, never values)

Text files scanned: 97 (HTML, JS, CSS, other text). Values were replaced in place with the
`[REDACTED-*]` placeholders (e-mail: `redacted@example.invalid`).

| Category | What it was | Replacements (file kind: count) |
| --- | --- | --- |
| account-id | seller account/member identifier (sellerMemberId / memberId=) | html: 1 |
| account-id | seller member id inside a shop URL | html: 1 |
| api-key | Google API key (AIza...) in page/JS settings | html: 2, js: 6 |
| credential | credential-like query parameter (key=/secret=/token=/password=...) in a URL | js: 6 |
| debug-token | server debug string in an HTML comment | html: 1 |
| email | e-mail address (site contact lines, JS/CSS author headers) | css: 1, html: 7, js: 15 |
| phone | tel: link | html: 1 |
| phone | telephone number in the structured-data (JSON-LD) block | html: 1 |
| phone | telephone number written as 0X-XXX-XXXX (site contact lines) | html: 12 |
| session-nonce | WordPress nonce/security token values in JS settings | html: 11 |
| viewer-ip | viewer IP address in a JavaScript settings object (u_ip) | html: 1 |
| viewer-ip | viewer IP address shown in the page ('User IP:' line) | html: 1 |

The rehearsal redaction tool re-scanned the text it processed. Before packaging, the handoff maintainer also scanned the HTML, JS, CSS and SVG for common API keys, auth tokens, session/query credentials, viewer IP and email patterns, checked the AppleDouble stubs and checked JPEGs for EXIF/XMP markers. No residual matches were found for those checks.

## Other files checked

- `__MACOSX/` AppleDouble entries (157): each held macOS extended attributes (download quarantine
  record with the browser name, a timestamp/id, and provenance/macl attributes). Contents were replaced with an
  empty AppleDouble header; names and count are unchanged.
- JS/CSS/iframe pages: scanned with the same rules. Hits were only e-mail addresses in library headers/site
  settings, Google API keys in site JS, and nonce/viewer-IP values in the HTML inline settings (counts above).
  No other personal data pattern (IP, token, phone) was found. Third-party library code is otherwise unchanged.
- Not redacted on purpose: the shop's public product/seller data (including the numeric sellerId, which is also
  part of the image file names), tracking container ids (GTM/Ads/Pixel ids), public site and CDN URLs, cart hash
  keys derived from the site, and the business name of the seller. The seller *member-id string* is redacted
  (see account-id above); the numeric id is not.
- The "credential" rows are third-party site-operator API credentials that were embedded in public site JS (query
  strings such as key=/secret=); they are not the user's, but are redacted for consistency with the Google API keys.
