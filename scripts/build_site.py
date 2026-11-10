"""Build the documentation site (GitHub Pages) into _site/.

Run `npm run bundle` first; `npm run site` does both. Produces:

    _site/index.html                 landing page (docs/site/index.html)
    _site/api/index.html             Redoc reference for dist/openapi.yaml
    _site/relay-mailbox/index.html   Redoc reference for dist/relay-mailbox.yaml
    _site/events/index.html          AsyncAPI reference for spec/asyncapi.yaml
    _site/openapi.yaml, relay-mailbox.yaml, asyncapi.yaml   downloadable bundles
    _site/assets/                    TagBrush.css, TagAction (from node_modules), the themes,
                                     favicon.svg and og-image.png (social preview)
    _site/sitemap.xml                the four pages, for search engines

Pages carry a canonical URL, Open Graph tags and (landing page) schema.org JSON-LD, all
built from SITE_URL. og-image.png is rendered from docs/site/og-image.html.

The landing page is plain semantic HTML styled by TagBrush.css with docs/site/theme.css;
the Redoc pages use docs/site/redocly.yaml (Redoc theme) and docs/site/redoc.hbs (page
template), and the AsyncAPI page docs/site/asyncapi-theme.css. All three repeat the same
colors. The top bar on the reference pages uses TagBrush.css's scoped build.

The AsyncAPI document is bundled with Redocly and gets the same `$id`/`$schema`
stripping as finalize_bundle.py, so its in-document refs resolve; schema-level
`examples` are kept because they help readers and no mock reads this copy.

Usage: uv run scripts/build_site.py [out_dir]
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

SITE_URL = "https://kovirlabs.github.io/tds-openapi/"
ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "docs" / "site"
NODE = ROOT / "node_modules"
ASSETS = [
    NODE / "tagbrush" / "tagbrush.min.css",
    NODE / "tagbrush" / "tagbrush.scoped.min.css",
    NODE / "tagaction" / "tagaction.min.js",
    SRC / "theme.css",
    SRC / "asyncapi-theme.css",
    SRC / "favicon.svg",
    SRC / "og-image.png",
]
# Reference pages built by Redoc: (bundle, path under the site, title, meta description).
REDOC_PAGES = [
    (
        "openapi.yaml",
        "api/",
        "TDS Trip Booking API Reference (OpenAPI 3.1)",
        "OpenAPI 3.1 reference for booking demand-response transit trips across providers: "
        "request, confirm, modify and cancel trips, webhook subscriptions, tenders and holds.",
    ),
    (
        "relay-mailbox.yaml",
        "relay-mailbox/",
        "TDS Relay Mailbox API Reference (OpenAPI 3.1)",
        "OpenAPI 3.1 reference for the TDS relay mailbox: an HTTPS mailbox that moves CSV trip "
        "files for transit providers whose scheduler can only call out.",
    ),
]
ENV = {**os.environ, "REDOCLY_SUPPRESS_UPDATE_NOTICE": "true", "REDOCLY_TELEMETRY": "off"}


def redocly(*args: str) -> None:
    subprocess.run(["npx", "--no-install", "redocly", *args], check=True, cwd=ROOT, env=ENV)


def build_docs(spec: Path, out: Path, title: str, url: str, description: str) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    redocly(
        "build-docs",
        str(spec),
        "-o",
        str(out),
        "--title",
        title,
        "--config",
        str(SRC / "redocly.yaml"),
        "--template",
        str(SRC / "redoc.hbs"),
        "--disableGoogleFont",
        "--templateOptions.url",
        url,
        "--templateOptions.description",
        description,
        "--templateOptions.image",
        SITE_URL + "assets/og-image.png",
    )


def sitemap(paths: list[str]) -> str:
    urls = "".join(f"  <url><loc>{SITE_URL}{p}</loc></url>\n" for p in paths)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{urls}</urlset>\n"
    )


def fill(text: str, version: str) -> str:
    return text.replace("__VERSION__", version).replace("__SITE_URL__", SITE_URL)


def asyncapi_bundle(out: Path) -> dict:
    tmp = out / "asyncapi.yaml"
    redocly("bundle", "spec/asyncapi.yaml", "-o", str(tmp))
    doc = yaml.safe_load(tmp.read_text())
    for schema in doc.get("components", {}).get("schemas", {}).values():
        schema.pop("$id", None)
        schema.pop("$schema", None)
    tmp.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=100))
    return doc


def main(out: Path) -> None:
    bundles = [ROOT / "dist" / "openapi.yaml", ROOT / "dist" / "relay-mailbox.yaml"]
    if not all(b.exists() for b in bundles):
        sys.exit("dist/ bundles missing: run `npm run bundle` first (or `npm run site`).")

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    (out / "assets").mkdir()
    for a in ASSETS:
        shutil.copy(a, out / "assets" / a.name)

    version = yaml.safe_load(bundles[0].read_text())["info"]["version"]

    for b in bundles:
        shutil.copy(b, out / b.name)
    for bundle, path, title, description in REDOC_PAGES:
        page = out / path / "index.html"
        build_docs(ROOT / "dist" / bundle, page, title, SITE_URL + path, description)

    doc = asyncapi_bundle(out)
    # Inline the document so the page works without a fetch; `</` must not end the script.
    payload = json.dumps(doc, ensure_ascii=False).replace("</", "<\\/")
    events = fill((SRC / "events.html").read_text(), version)
    (out / "events").mkdir()
    (out / "events" / "index.html").write_text(events.replace("__ASYNCAPI_JSON__", payload))

    (out / "index.html").write_text(fill((SRC / "index.html").read_text(), version))
    (out / "sitemap.xml").write_text(sitemap(["", "api/", "events/", "relay-mailbox/"]))
    (out / ".nojekyll").write_text("")
    print(f"site v{version} built in {out.relative_to(ROOT) if out.is_relative_to(ROOT) else out}")


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT / "_site")
