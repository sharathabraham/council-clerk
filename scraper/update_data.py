#!/usr/bin/env python3
"""
Scrape Elmwood Park, NJ town council minutes/videos, parse per-member votes out
of each resolution, and update the JSON files under data/ that the site reads.

Safe to re-run any time - only downloads meetings not already in data/meetings.json.
"""

import json
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

import pdfplumber
import requests
from bs4 import BeautifulSoup

BASE = "https://www.elmwoodparknj.us"
MINUTES_URL = f"{BASE}/governing-body/minutes/minutes-2026"
VIDEOS_URL = f"{BASE}/governing-body/meeting-videos/meeting-videos-2026"

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "docs" / "data"
PDF_CACHE_DIR = ROOT / "scraper" / "_pdf_cache"

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; ElmwoodParkCouncilTracker/1.0)"}

CATEGORY_RULES = [
    # Order matters - checked top to bottom, first match wins. Ordinances and
    # bonds are checked before Grants since ordinance/bond titles often mention
    # "grant" incidentally (e.g. a bond ordinance appropriating a federal grant).
    ("Ordinances", ["ordinance"]),
    ("Budget & Bonds", ["bond", "appropriat", "payroll", "budget"]),
    ("Grants", ["grant"]),
    ("Contracts", ["agreement", "contract", "award", "shared service"]),
    ("Refunds & Tax", ["refund", "tax exempt", "overpayment", "tax appeal"]),
    ("Appointments", ["appoint", "hiring", "hire employees"]),
]


def categorize(title):
    t = title.lower()
    for category, keywords in CATEGORY_RULES:
        if any(k in t for k in keywords):
            return category
    return "Other"


def load_json(name, default):
    path = DATA_DIR / name
    if path.exists():
        return json.loads(path.read_text())
    return default


def save_json(name, data):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / name
    path.write_text(json.dumps(data, indent=2))


def fetch_minutes_listing():
    """Return [{id, date, type, phocaDownloadId, minutesUrl}] from the minutes page,
    newest-looking entries deduplicated by download id."""
    resp = requests.get(MINUTES_URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    entries = []
    seen_ids = set()
    for a in soup.select('a[href*="download="]'):
        href = a["href"]
        m = re.search(r"download=(\d+):([\w-]+)", href)
        if not m:
            continue
        download_id, slug = m.group(1), m.group(2)
        if download_id in seen_ids:
            continue
        seen_ids.add(download_id)

        date_m = re.search(r"(\d{1,2})-(\d{1,2})-(\d{4})$", slug)
        if not date_m:
            print(f"  ! skipping link with no date in slug: {slug}", file=sys.stderr)
            continue
        month, day, year = date_m.groups()
        date = f"{year}-{int(month):02d}-{int(day):02d}"

        if "work-session" in slug:
            mtype = "Work Session"
        elif "reorganization" in slug:
            mtype = "Reorganization Meeting"
        elif "regular" in slug:
            mtype = "Regular Meeting"
        else:
            mtype = "Meeting"

        entries.append(
            {
                "id": f"{date}-{mtype.lower().replace(' ', '-')}",
                "date": date,
                "type": mtype,
                "phocaDownloadId": download_id,
                "minutesUrl": urljoin(BASE, href),
            }
        )
    return entries


def fetch_videos_by_date():
    """Return {date_str: youtube_embed_url} from the meeting videos page."""
    resp = requests.get(VIDEOS_URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()

    videos = {}
    # Matches: <a href="https://www.youtube.com/embed/ID?..." ... title="Month D, YYYY ... meeting">
    pattern = re.compile(
        r'href="(https://www\.youtube\.com/embed/[^"?]+)[^"]*"[^>]*title="([^"]+)"'
    )
    for embed_url, title in pattern.findall(resp.text):
        date_m = re.search(r"([A-Z][a-z]+ \d{1,2}, \d{4})", title)
        if not date_m:
            continue
        try:
            d = datetime.strptime(date_m.group(1), "%B %d, %Y")
        except ValueError:
            continue
        videos[d.strftime("%Y-%m-%d")] = embed_url
    return videos


def download_pdf(url, download_id):
    PDF_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = PDF_CACHE_DIR / f"{download_id}.pdf"
    if path.exists():
        return path
    resp = requests.get(url, headers=HEADERS, timeout=120)
    resp.raise_for_status()
    path.write_bytes(resp.content)
    return path


VOTE_CATEGORIES = ["Aye", "Nay", "Abstain", "Absent"]


def parse_vote_table(page, member_last_names):
    """Read a 'Record of Council Vote on Passage' table using word x/y positions
    (robust to how the table's spacing gets flattened in plain-text extraction).
    Returns {last_name: "Aye"/"Nay"/"Abstain"/"Absent"} or {} if no table found."""
    text = page.extract_text() or ""
    if "Record of Council Vote" not in text:
        return {}

    words = page.extract_words()

    header_words = [w for w in words if w["text"] in ("AYE", "NAY", "Abstain", "Absent")]
    if len(header_words) < 8:
        return {}
    header_words.sort(key=lambda w: w["x0"])
    header_cols = []
    for i, w in enumerate(header_words[:8]):
        category = VOTE_CATEGORIES[i % 4]
        header_cols.append({"x0": w["x0"], "category": category, "half": "left" if i < 4 else "right"})

    name_words = [w for w in words if w["text"].rstrip(",") in member_last_names]
    votes = {}
    for name_w in name_words:
        name = name_w["text"].rstrip(",")
        row_top = name_w["top"]
        x_words = [
            w
            for w in words
            if w["text"] == "X" and abs(w["top"] - row_top) < 3
        ]
        if not x_words:
            votes[name] = "Absent"  # blank row = not marked, treat as absent
            continue
        # a name's mark is whichever X sits on its side of the table (there can be
        # one X per side per row, for the two members sharing that row)
        same_row_names = [w for w in name_words if abs(w["top"] - row_top) < 3]
        same_row_names.sort(key=lambda w: w["x0"])
        side = "left" if same_row_names.index(name_w) == 0 else "right"
        candidates = [x for x in x_words if _nearest_col(x["x0"], header_cols)["half"] == side]
        if not candidates:
            continue
        nearest = _nearest_col(candidates[0]["x0"], header_cols)
        votes[name] = nearest["category"]
    return votes


def _nearest_col(x0, header_cols):
    return min(header_cols, key=lambda c: abs(c["x0"] - x0))


TITLE_END_MARKERS = [
    "be passed and adopted",
    "was introduced and passed",
    "pass on final reading",
    "NOW, THEREFORE",
    "WHEREAS, all persons",
    "\nWHEREAS",
]


def extract_title(text):
    entitled_m = re.search(r"entitled:\s*\n?", text)
    if entitled_m:
        start = entitled_m.end()
        end = len(text)
        for marker in TITLE_END_MARKERS:
            idx = text.find(marker, start)
            if idx != -1:
                end = min(end, idx)
        title = text[start:end]
    else:
        # consent-agenda / appointment style: title sits between "SECONDED BY: ..."
        # and whichever operative clause starts the resolution body - not every
        # resolution has a "WHEREAS" clause, some go straight to "BE IT RESOLVED".
        seconded_m = re.search(r"SECONDED BY:.*\n", text)
        start = seconded_m.end() if seconded_m else -1
        end_idx = -1
        if start != -1:
            candidates = [
                text.find(marker, start)
                for marker in ("WHEREAS", "BE IT RESOLVED", "NOW, THEREFORE")
            ]
            # CFO funding-confirmation line ("I, <name>, Chief Financial Officer...")
            # also marks the end of a title when nothing else does
            cfo_m = re.search(r"\nI,\s", text[start:])
            if cfo_m:
                candidates.append(start + cfo_m.start())
            candidates = [c for c in candidates if c != -1]
            end_idx = min(candidates) if candidates else -1
        if start != -1 and end_idx != -1:
            title = text[start:end_idx]
        else:
            title = ""
    title = re.sub(r"\s+", " ", title).strip(" .")

    if not title:
        # some resolutions have no standalone title line at all - fall back to a
        # short snippet of the operative clause so the item is still identifiable
        body_m = re.search(r"BE IT (FURTHER )?RESOLVED.{0,220}", text, re.S)
        if body_m:
            snippet = re.sub(
                r"^BE IT (FURTHER )?RESOLVED,?\s*(by the Mayor and Council of the Borough of Elmwood Park)?\s*(that)?\s*",
                "",
                body_m.group(0),
                flags=re.I,
            )
            snippet = re.sub(r"\s+", " ", snippet).strip()
            period_idx = snippet.find(".")
            if 20 < period_idx < 200:
                snippet = snippet[:period_idx]
            title = snippet[:150].rstrip(", ")
    return title


def parse_resolution_span(text, span_pages, member_last_names):
    """Parse one resolution's fields from the combined text of the page(s) it
    spans - long resolutions (bid awards, bills lists, etc.) can run several
    pages before their vote table shows up, so a resolution isn't always
    confined to a single PDF page."""
    num_m = re.search(r"RESOLUTION\s+(R-\d+-\d+)", text)
    if not num_m:
        return None
    number = num_m.group(1)

    consent = "CONSENT AGENDA" in text

    moved_m = re.search(r"RESOLUTION BY:\s*([^\n]+)", text)
    seconded_m = re.search(r"SECONDED BY:\s*([^\n]+)", text)
    moved_by = moved_m.group(1).strip() if moved_m else None
    seconded_by = seconded_m.group(1).strip() if seconded_m else None

    ordinance_m = re.search(r"ORDINANCE #\s*(\d+-\d+)", text)
    ordinance_number = ordinance_m.group(1) if ordinance_m else None
    if "FIRST READING" in text:
        reading_stage = "First Reading"
    elif "SECOND READING" in text:
        reading_stage = "Second Reading / Final Passage"
    else:
        reading_stage = None

    title = extract_title(text)
    if not title:
        print(f"  ! could not extract a title for {number} - check it manually", file=sys.stderr)
        title = f"(untitled - {number})"

    votes = {}
    for page in span_pages:
        votes = parse_vote_table(page, member_last_names)
        if votes:
            break
    if not votes:
        print(f"  ! no vote table found for {number} - check it manually", file=sys.stderr)

    return {
        "number": number,
        "title": title,
        "movedBy": moved_by,
        "secondedBy": seconded_by,
        "consentAgenda": consent,
        "category": categorize(title),
        "ordinanceNumber": ordinance_number,
        "readingStage": reading_stage,
        "votes": votes,
    }


def parse_meeting_pdf(pdf_path, member_last_names):
    resolutions = []
    with pdfplumber.open(pdf_path) as pdf:
        pages = pdf.pages
        page_texts = [(p.extract_text() or "") for p in pages]
        start_indices = [
            i for i, t in enumerate(page_texts) if re.search(r"RESOLUTION\s+R-\d+-\d+", t)
        ]
        for pos, start in enumerate(start_indices):
            end = start_indices[pos + 1] if pos + 1 < len(start_indices) else len(pages)
            span_text = "\n".join(page_texts[start:end])
            span_pages = pages[start:end]
            record = parse_resolution_span(span_text, span_pages, member_last_names)
            if record:
                resolutions.append(record)
    return resolutions


def main():
    members = load_json("members.json", [])
    if not members:
        print("data/members.json is empty - add council members before running the scraper.", file=sys.stderr)
        sys.exit(1)
    member_last_names = {m["name"].split()[-1] for m in members if m.get("votes", True)}

    meetings = load_json("meetings.json", [])
    resolutions = load_json("resolutions.json", [])
    known_ids = {m["phocaDownloadId"] for m in meetings}

    print("Fetching minutes listing...")
    listing = fetch_minutes_listing()
    print("Fetching video listing...")
    videos_by_date = fetch_videos_by_date()

    new_meetings = [e for e in listing if e["phocaDownloadId"] not in known_ids]
    if not new_meetings:
        print("No new meetings found. Data is up to date.")
        return

    print(f"Found {len(new_meetings)} new meeting(s).")
    for entry in new_meetings:
        print(f"  Processing {entry['date']} - {entry['type']} (id {entry['phocaDownloadId']})")
        entry["agendaUrl"] = None  # not scraped yet - agendas page not wired in for v1
        entry["videoUrl"] = videos_by_date.get(entry["date"])

        try:
            pdf_path = download_pdf(entry["minutesUrl"], entry["phocaDownloadId"])
            meeting_resolutions = parse_meeting_pdf(pdf_path, member_last_names)
        except Exception as exc:  # noqa: BLE001 - surface any parsing failure loudly
            print(f"  ! failed to parse {entry['minutesUrl']}: {exc}", file=sys.stderr)
            print(f"    -> skipping this meeting for now, will retry on next run", file=sys.stderr)
            continue  # don't mark as known - so the next run retries this meeting

        for r in meeting_resolutions:
            r["meetingId"] = entry["id"]
        resolutions.extend(meeting_resolutions)
        meetings.append(entry)
        print(f"    -> parsed {len(meeting_resolutions)} resolution(s)")

    meetings.sort(key=lambda m: m["date"], reverse=True)
    save_json("meetings.json", meetings)
    save_json("resolutions.json", resolutions)
    print("Done. data/meetings.json and data/resolutions.json updated.")


if __name__ == "__main__":
    main()
