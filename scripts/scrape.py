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
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urljoin
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


class AttachmentsParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.attachments: list[dict[str, str]] = []
        self.current_url: str | None = None
        self.buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        values = {key: value or "" for key, value in attrs}
        href = values.get("href", "")
        if "/upload/" in href:
            self.current_url = urljoin(SOURCE_URL, href)
            self.buffer = []

    def handle_data(self, data: str) -> None:
        if self.current_url:
            self.buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.current_url:
            name = clean("".join(self.buffer))
            if not name:
                name = unquote(self.current_url.rsplit("/", 1)[-1]).replace("+", " ")
            self.attachments.append({"name": name, "url": self.current_url})
            self.current_url = None
            self.buffer = []


def fetch(url: str, attempts: int = 3, insecure: bool = False, timeout: int = 30) -> str:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
    context = ssl._create_unverified_context() if insecure else None
    for attempt in range(attempts):
        try:
            with urlopen(request, timeout=timeout, context=context) as response:
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


def is_offer_attachment(name: str) -> bool:
    normalized = clean(name).casefold()
    mentions_offer = (
        ("индикатив" in normalized and "предлож" in normalized)
        or (re.search(r"\bинд\.?\s*предлож", normalized) is not None)
        or "оферта" in normalized
        or "indicative offer" in normalized
    )
    excluded = (
        "образец",
        "бланка",
        "форма за",
        "покана",
        "удължав",
        "срок за",
        "искане за",
        "информационно съобщение",
    )
    return mentions_offer and not any(term in normalized for term in excluded)


def participant_from_filename(name: str) -> str | None:
    value = re.sub(r"\.(pdf|docx?|xlsx?|zip|rar|7z)$", "", clean(name), flags=re.IGNORECASE)
    value = re.sub(
        r"(?:[_\s.-]*(?:redacted|заличено|заличена версия))+$",
        "",
        value,
        flags=re.IGNORECASE,
    )

    offer_from = re.search(r"оферта\s+от\s+(.+)$", value, flags=re.IGNORECASE)
    if offer_from:
        value = offer_from.group(1)
    else:
        value = re.sub(
            r"^.*?(?:индикативно|индикатино|инд\.?)(?:\s+ценово)?\s*(?:предложение|предл\.?)\s*",
            "",
            value,
            flags=re.IGNORECASE,
        )

    value = value.replace("_", " ")
    value = re.sub(r"^[\s.–—-]+|[\s.–—-]+$", "", value)
    value = re.sub(r"^от\s+", "", value, flags=re.IGNORECASE)
    value = re.sub(r"^\d{4,}\s*[-–—]\s*", "", value)
    value = re.sub(r"\b(?:получено|представено)\s+след.+$", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\b(?:вх|изх)[-.\s]*[а-яa-z]*[-.\s]*\d+.*$", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+(?:по\s+)?(?:пк|оп)[\s№._/-]*\d+.*$", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s*[,;]\s*(?:оторизация|вариант|ревизия).*$", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+", " ", value).strip(" ._-–—")

    generic = re.fullmatch(r"(?:№\s*)?\d+(?:[-_/]\d+)*(?:\s*(?:ревизия|rev\.?)[-\s]*\d+)?", value, re.IGNORECASE)
    if not value or generic or len(value) < 3:
        return None
    if re.fullmatch(r"[\d\s№._/\-–—]+", value):
        return None
    administrative_terms = (
        "след изтичане",
        "ценово предложение",
        "техническо предложение",
        "indicative quote",
        "извън срок",
        "частично",
        "редактирано",
        "актуализирано",
        "корекция",
        "с вх",
        "пазарна консултация",
        "вариант",
    )
    if any(phrase in value.casefold() for phrase in administrative_terms):
        return None
    legal_form = re.search(
        r"\b(?:еоод|оод|еад|ад|доoел|дооел|ltd\.?|gmbh|s\.?a\.?u\.?|inc\.?|llc|a\.?g\.?|sas|b\.?v\.?)\b",
        value,
        flags=re.IGNORECASE,
    )
    if any(character.isdigit() for character in value) and not legal_form:
        return None
    words = [word for word in value.split() if word]
    if len(words) > 7:
        return None
    if legal_form or value == value.upper() or all(not word[0].isalpha() or word[0].isupper() for word in words):
        return value[:160]
    return None


def parse_attachments(html_text: str) -> list[dict[str, str]]:
    parser = AttachmentsParser()
    parser.feed(html_text)
    return parser.attachments


def detail_payload(html_text: str) -> dict[str, object]:
    attachments = parse_attachments(html_text)
    offers: list[dict[str, object]] = []
    for attachment in attachments:
        if is_offer_attachment(attachment["name"]):
            offers.append(
                {
                    "name": attachment["name"],
                    "url": attachment["url"],
                    "participant": participant_from_filename(attachment["name"]),
                }
            )
    return {
        "attachment_count": len(attachments),
        "offer_count": len(offers),
        "offers": offers,
        "details_checked": True,
        "details_checked_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }


def enrich_details(
    items: list[dict[str, object]],
    previous_items: dict[str, dict[str, object]],
    max_workers: int,
    insecure: bool,
    backfill_limit: int = 120,
) -> None:
    # Recheck recent consultations, where offers may still be added, and
    # gradually backfill the older archive without overloading the source.
    refresh_after = (datetime.now(timezone.utc).date() - timedelta(days=180)).isoformat()
    targets: list[dict[str, object]] = []
    backfill_candidates: list[dict[str, object]] = []

    for item in items:
        previous = previous_items.get(str(item["id"]), {})
        if previous.get("details_checked"):
            previous_offers = []
            for previous_offer in previous.get("offers", []):
                offer = dict(previous_offer)
                offer["participant"] = participant_from_filename(str(offer.get("name") or ""))
                previous_offers.append(offer)
            item.update(
                {
                    "attachment_count": previous.get("attachment_count", 0),
                    "offer_count": previous.get("offer_count", 0),
                    "offers": previous_offers,
                    "details_checked": True,
                    "details_checked_at": previous.get("details_checked_at"),
                }
            )
        else:
            item.update({"attachment_count": 0, "offer_count": 0, "offers": [], "details_checked": False})

        if str(item.get("valid_from") or "") >= refresh_after:
            targets.append(item)
        elif not previous.get("details_checked"):
            backfill_candidates.append(item)

    targets.extend(backfill_candidates[:backfill_limit])

    def load_detail(item: dict[str, object]) -> tuple[str, dict[str, object]]:
        html_text = fetch(str(item["url"]), attempts=2, insecure=insecure, timeout=12)
        return str(item["id"]), detail_payload(html_text)

    print(f"Checking attachments for {len(targets)} consultations…")
    completed = 0
    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, 4))) as executor:
        futures = {executor.submit(load_detail, item): item for item in targets}
        for future in as_completed(futures):
            item = futures[future]
            try:
                _, details = future.result()
                item.update(details)
            except Exception as error:
                if not item.get("details_checked"):
                    item.update({"attachment_count": 0, "offer_count": 0, "offers": [], "details_checked": False})
                print(f"Warning: could not read {item['url']}: {error}")
            completed += 1
            if completed % 200 == 0:
                print(f"Checked {completed}/{len(targets)} detail pages")


def scrape(
    max_workers: int = 8,
    insecure: bool = False,
    previous_items: dict[str, dict[str, object]] | None = None,
    include_details: bool = True,
) -> dict[str, object]:
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
    if include_details:
        enrich_details(items, previous_items or {}, max_workers, insecure)

    consultations_with_offers = sum(bool(item.get("offer_count")) for item in items)
    published_offers = sum(int(item.get("offer_count") or 0) for item in items)
    details_checked_count = sum(bool(item.get("details_checked")) for item in items)
    return {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "source": SOURCE_URL,
        "source_pages": max_page,
        "count": len(items),
        "consultations_with_offers": consultations_with_offers,
        "published_offers": published_offers,
        "details_checked_count": details_checked_count,
        "items": items,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="data/consultations.json")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--skip-details", action="store_true", help="Skip consultation attachment pages.")
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Disable TLS verification (local diagnostics only; not used in CI).",
    )
    args = parser.parse_args()

    output = Path(args.output)
    previous_items: dict[str, dict[str, object]] = {}
    if output.exists():
        try:
            previous_payload = json.loads(output.read_text(encoding="utf-8"))
            previous_items = {str(item["id"]): item for item in previous_payload.get("items", [])}
        except (json.JSONDecodeError, OSError, KeyError, TypeError):
            previous_items = {}

    payload = scrape(
        max_workers=max(1, min(args.workers, 16)),
        insecure=args.insecure,
        previous_items=previous_items,
        include_details=not args.skip_details,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Saved {payload['count']} consultations from {payload['source_pages']} pages to {output}")


if __name__ == "__main__":
    main()
