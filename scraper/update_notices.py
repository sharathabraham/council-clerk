#!/usr/bin/env python3
"""
Check the Borough's Public Notices feed for new ordinance and budget/audit
notices. These are plain HTML articles (not scanned PDFs), and critically,
they're published much closer to real-time than the meeting minutes - this
fills the gap for ordinances/budget news from meetings that haven't had
their official minutes posted yet.

Only two kinds of notices are captured, matched by the notice's own title:
  - "Introduction to Ordinance ..." / "Adoption of Ordinance ..."
  - anything else containing "Budget" or "Audit"
Everything else (bid notices, meeting notices, surplus auctions, contract
awards) is intentionally out of scope for now.

Safe to re-run any time - only processes notice IDs not already in
docs/data/notices.json.
"""

import json
import re
import sys
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE = "https://www.elmwoodparknj.us"
NOTICES_URL = f"{BASE}/public-notices"

ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / "docs" / "data" / "notices.json"

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; ElmwoodParkCivicTracker/1.0)"}

DEFAULT_DATA = {"ordinanceNotices": [], "budgetAuditNotices": [], "processedNoticeIds": []}


def load_notices():
    if DATA_PATH.exists():
        return json.loads(DATA_PATH.read_text())
    return dict(DEFAULT_DATA)


def save_notices(data):
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATA_PATH.write_text(json.dumps(data, indent=2))


def fetch_notice_listing():
    """Return [{id, slug, title, url}] across every page of the notices feed.
    Each listing entry is an <h2 class="article-title"> whose text is the
    headline and whose nested <meta itemprop="url"> gives the canonical URL -
    plain <a> tags on this page are icons (print/email), not the title."""
    entries = []
    seen_ids = set()
    start = 0
    while True:
        url = NOTICES_URL if start == 0 else f"{NOTICES_URL}?start={start}"
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        page_ids = set()
        for h2 in soup.select("h2.article-title"):
            meta = h2.find("meta", itemprop="url")
            if not meta or not meta.get("content"):
                continue
            href = meta["content"]
            m = re.search(r"/public-notices/(\d+)-([\w-]+)", href)
            if not m:
                continue
            notice_id, slug = int(m.group(1)), m.group(2)
            title = h2.get_text(strip=True)
            if notice_id in seen_ids or not title:
                continue
            seen_ids.add(notice_id)
            page_ids.add(notice_id)
            entries.append({"id": notice_id, "slug": slug, "title": title, "url": urljoin(BASE, href.split("?")[0])})

        if not page_ids:
            break
        start += 25
        if start > 1000:  # sanity guard against an infinite loop
            break
    return entries


def classify(title):
    t = title.lower()
    if t.startswith("introduction to ordinance") or t.startswith("adoption of ordinance"):
        return "ordinance"
    if "budget" in t or "audit" in t:
        return "budget_audit"
    return None


def fetch_article(url):
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    body_el = soup.select_one("section.article-content")
    body = body_el.get_text(" ", strip=True) if body_el else ""
    # the source HTML uses &nbsp; liberally for spacing, which decodes to
    # U+00A0 (non-breaking space) - normalize to plain spaces so regex
    # patterns with literal spaces actually match
    body = re.sub(r"\s+", " ", body)

    date_el = soup.select_one("time[itemprop='datePublished']")
    published = date_el["datetime"][:10] if date_el and date_el.has_attr("datetime") else None

    return body, published


MONTH_DATE_RE = r"([A-Z][a-z]+\s*\d{1,2},?\s*\d{4})"  # source has typos like "March19" with no space


def parse_ordinance_notice(title, body, published):
    ord_m = re.search(r"Ordinance #\s*(\d+-\d+)", body)
    ordinance_number = ord_m.group(1) if ord_m else None

    # phrasing varies ("passed upon first reading" vs "read and passed on
    # first reading", etc.) - match on the distinctive fragment only, since
    # "second and final reading" / "finally adopted" never appear in an
    # introduction notice and "first reading" never appears in an adoption one
    if re.search(r"second and final reading|finally adopted", body, re.I):
        stage = "Second Reading / Final Passage"
    elif re.search(r"first reading", body, re.I):
        stage = "First Reading"
    else:
        stage = None

    # the FIRST "on <date>" in the body is always the actual meeting date in
    # every template seen (bond/non-bond, introduction/adoption) - a second,
    # later "on <date>" only ever shows up in introduction notices, naming a
    # future public-hearing date we don't want
    date_m = re.search(r"\bon\s+(?:\w+day,?\s*)?" + MONTH_DATE_RE, body, re.I)
    action_date = (_to_iso_date(date_m.group(1)) if date_m else None) or published

    is_bond = "BOND ORDINANCE" in body.upper()
    if is_bond:
        # ends at a period followed by whichever summary field label comes
        # next - single-purpose bonds use "Purpose(s):"/"Appropriation:",
        # multi-purpose ones go straight into "The purposes, ..."
        title_m = re.search(
            r"Title\s*:\s*(.+?\.)\s*(?:Purpose\(s\)\s*:|Appropriation\s*:|The purposes,)", body, re.S
        )
        ordinance_title = re.sub(r"\s+", " ", title_m.group(1)).strip() if title_m else None
        # pull the TOTAL from the title's own "TO APPROPRIATE THE SUM OF $X"
        # clause - a generic "Appropriation:" search would grab just the
        # first line item's amount on a multi-purpose bond ordinance
        amount_m = re.search(r"TO APPROPRIATE THE SUM OF \$\s*([\d,]+)", ordinance_title or "", re.I)
        amount = float(amount_m.group(1).replace(",", "")) if amount_m else None
    else:
        # non-bond: title is the ALL-CAPS block after "Ordinance #xx-xx" -
        # ends at whichever boundary shows up first (signature block, page
        # nav bleeding into the extracted text, or a plain sentence period)
        after_m = re.search(r"Ordinance #\s*\d+-\d+\s*(.+)", body, re.S)
        ordinance_title = None
        amount = None
        if after_m:
            remainder = after_m.group(1)
            end = len(remainder)
            for marker in ("Borough of Elmwood Park", "News Menu"):
                idx = remainder.find(marker)
                if idx != -1:
                    end = min(end, idx)
            candidate = remainder[:end].strip()
            if len(candidate) > 400 or not candidate:
                sentence_m = re.match(r"(.+?\.)\s", remainder)
                candidate = sentence_m.group(1).strip() if sentence_m else remainder[:300].strip()
            ordinance_title = re.sub(r"\s+", " ", candidate).strip()

    return {
        "ordinanceNumber": ordinance_number,
        "readingStage": stage,
        "title": ordinance_title,
        "date": action_date,
        "amount": amount,
        "isBondOrdinance": is_bond,
        "noticeTitle": title,
    }


def _to_iso_date(text):
    import datetime

    text = text.replace(",", "")
    text = re.sub(r"([A-Za-z])(\d)", r"\1 \2", text)  # "July16" -> "July 16" (source typo)
    for fmt in ("%B %d %Y",):
        try:
            return datetime.datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def main():
    data = load_notices()
    processed = set(data.get("processedNoticeIds", []))

    print("Fetching public notices listing...")
    listing = fetch_notice_listing()
    new_entries = [e for e in listing if e["id"] not in processed]

    if not new_entries:
        print("No new notices found. Data is up to date.")
        return

    print(f"Found {len(new_entries)} new notice(s) to check.")
    for entry in new_entries:
        category = classify(entry["title"])
        if not category:
            processed.add(entry["id"])
            continue

        print(f"  Processing [{category}] {entry['title']} (id {entry['id']})")
        try:
            body, published = fetch_article(entry["url"])
        except Exception as exc:  # noqa: BLE001
            print(f"  ! failed to fetch {entry['url']}: {exc}", file=sys.stderr)
            continue  # retry next run

        if category == "ordinance":
            parsed = parse_ordinance_notice(entry["title"], body, published)
            if not parsed["ordinanceNumber"]:
                print(f"  ! could not find an ordinance number in {entry['url']} - skipping", file=sys.stderr)
                processed.add(entry["id"])
                continue
            parsed["noticeId"] = entry["id"]
            parsed["publishedDate"] = published
            parsed["url"] = entry["url"]
            data["ordinanceNotices"].append(parsed)
        else:
            data["budgetAuditNotices"].append(
                {
                    "noticeId": entry["id"],
                    "title": entry["title"],
                    "publishedDate": published,
                    "excerpt": body[:400],
                    "url": entry["url"],
                }
            )

        processed.add(entry["id"])

    data["processedNoticeIds"] = sorted(processed)
    data["ordinanceNotices"].sort(key=lambda n: n.get("date") or n.get("publishedDate") or "", reverse=True)
    data["budgetAuditNotices"].sort(key=lambda n: n.get("publishedDate") or "", reverse=True)

    # sanity check: First Reading should never postdate Second Reading for
    # the same ordinance - if it does, that's either a source-data error or
    # a parsing bug, and either way a human should look at it, not have it
    # silently displayed as if it were normal
    by_ord = {}
    for n in data["ordinanceNotices"]:
        by_ord.setdefault(n["ordinanceNumber"], {})[n["readingStage"]] = n["date"]
    for ordnum, stages in by_ord.items():
        first, second = stages.get("First Reading"), stages.get("Second Reading / Final Passage")
        if first and second and first > second:
            print(
                f"  ! ordinance {ordnum}: First Reading ({first}) is AFTER Second Reading ({second}) - "
                f"check the source notices by hand, this may be a Borough clerical error",
                file=sys.stderr,
            )

    save_notices(data)
    print("Done. docs/data/notices.json updated.")


if __name__ == "__main__":
    main()
