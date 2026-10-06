#!/usr/bin/env python3
"""Collect the public market-consultation index from kznpp.org.

The script intentionally uses only Python's standard library so it can run in
GitHub Actions without an install step.
"""

from __future__ import annotations

import argparse
import json
import re
import ssl
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen


SOURCE_URL = "https://www.kznpp.org/bg/pazarni-konsultatsii"
USER_AGENT = "KZNPP-Market-Consultations-Dashboard/1.0 (+GitHub Pages)"


def clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


class ConsultationsParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[dict[str, object]] = []
        self.current: dict[str, object] | None = None
        self.capture: str | None = None
        self.buffer: list[str] = []
        self.max_page = 1
        self._anchor_href = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        classes = set(values.get("class", "").split())

        if tag == "a":
            self._anchor_href = values.get("href", "")
            match = re.search(r"[?&]p=(\d+)", self._anchor_href)
            if match:
                self.max_page = max(self.max_page, int(match.group(1)))
            if "procedure-link" in classes:
                self.current = {
                    "url": urljoin(SOURCE_URL, self._anchor_href),
                    "title": "",
                    "meta": [],
                }

        if self.current is not None:
            if tag == "h4":
                self.capture = "title"
                self.buffer = []
            elif tag in {"p", "div"} and "procedures-date" in classes:
                self.capture = "meta"
                self.buffer = []

    def handle_data(self, data: str) -> None:
        if self.capture:
            self.buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self.current is not None and self.capture == "title" and tag == "h4":
            self.current["title"] = clean("".join(self.buffer))
            self.capture = None
        elif self.current is not None and self.capture == "meta" and tag in {"p", "div"}:
            value = clean("".join(self.buffer))
            if value:
                self.current["meta"].append(value)  # type: ignore[union-attr]
            self.capture = None

        if tag == "a" and self.current is not None:
            self.items.append(self.current)
            self.current = None
            self.capture = None


def fetch(url: str, attempts: int = 3, insecure: bool = False) -> str:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
    context = ssl._create_unverified_context() if insecure else None
    for attempt in range(attempts):
        try:
            with urlopen(request, timeout=30, context=context) as response:
                return response.read().decode("utf-8", errors="replace")
        except (HTTPError, URLError, TimeoutError):
            if attempt == attempts - 1:
                raise
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError("Unreachable")


def parse_page(html: str) -> tuple[list[dict[str, object]], int]:
    parser = ConsultationsParser()
    parser.feed(html)
    return parser.items, parser.max_page


def iso_date(text: str, prefix: str) -> str | None:
    match = re.search(rf"{re.escape(prefix)}\s*(\d{{2}}\.\d{{2}}\.\d{{4}})", text)
    if not match:
        return None
    return datetime.strptime(match.group(1), "%d.%m.%Y").date().isoformat()


def normalize(raw: dict[str, object]) -> dict[str, object]:
    meta = [str(value) for value in raw.get("meta", [])]
    joined = " ".join(meta)
    reference_text = ""
    for line in meta:
        if not line.startswith("Валидна"):
            reference_text = line
            break
    reference_match = re.search(r"\b(\d{4,})\b", reference_text)
    url = str(raw["url"])
    id_match = re.search(r"/(\d+)(?:/)?$", url)
    return {
        "id": id_match.group(1) if id_match else url,
        "title": str(raw.get("title", "")),
        "valid_from": iso_date(joined, "Валидна от:"),
        "valid_to": iso_date(joined, "Валидна до:"),
        "reference": reference_match.group(1) if reference_match else "",
        "description": reference_text,
        "url": url,
    }


def scrape(max_workers: int = 8, insecure: bool = False) -> dict[str, object]:
    first_html = fetch(SOURCE_URL, insecure=insecure)
    first_items, max_page = parse_page(first_html)
    all_items = list(first_items)

    def page_url(page: int) -> str:
        return f"{SOURCE_URL}?p={page}"

    if max_page > 1:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(fetch, page_url(page), 3, insecure): page
                for page in range(2, max_page + 1)
            }
            for future in as_completed(futures):
                page_items, _ = parse_page(future.result())
                all_items.extend(page_items)

    unique: dict[str, dict[str, object]] = {}
    for raw in all_items:
        item = normalize(raw)
        unique[str(item["id"])] = item

    items = sorted(
        unique.values(),
        key=lambda item: (str(item.get("valid_from") or ""), str(item.get("id") or "")),
        reverse=True,
    )
    return {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "source": SOURCE_URL,
        "source_pages": max_page,
        "count": len(items),
        "items": items,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="data/consultations.json")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Disable TLS verification (local diagnostics only; not used in CI).",
    )
    args = parser.parse_args()

    payload = scrape(max_workers=max(1, min(args.workers, 16)), insecure=args.insecure)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Saved {payload['count']} consultations from {payload['source_pages']} pages to {output}")


if __name__ == "__main__":
    main()
