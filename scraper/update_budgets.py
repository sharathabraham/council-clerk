#!/usr/bin/env python3
"""
Check the Borough's municipal-budgets page for a new "User Friendly Budget"
filing and, if the PDF is a real digital document (not a scanned image),
parse the property-tax breakdown into docs/data/budgets.json.

Only recent filings (2024+ as of this writing) are digital - older ones are
scanned images with no extractable text, and were entered by hand once. If a
new filing turns out to be scanned, this script leaves it for manual entry
rather than guessing.

Safe to re-run any time - does nothing if there's no new filing.
"""

import json
import re
import sys
from pathlib import Path
from urllib.parse import urljoin

import pdfplumber
import requests
from bs4 import BeautifulSoup

BASE = "https://www.elmwoodparknj.us"
BUDGETS_URL = f"{BASE}/elmwood-park-municipal-budgets"

ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / "docs" / "data" / "budgets.json"
PDF_CACHE_DIR = ROOT / "scraper" / "_pdf_cache"

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; ElmwoodParkCouncilTracker/1.0)"}

ENTITIES = [
    "Local School District",
    "Municipal Purpose Tax",
    "County Purposes",
    "Municipal Library",
    "Municipal Open Space",
    "Regional School District",
    "County Library",
    "County Board of Health",
    "County Open Space",
    "Municipal Arts and Culture",
]


def load_budgets():
    return json.loads(DATA_PATH.read_text())


def save_budgets(data):
    DATA_PATH.write_text(json.dumps(data, indent=2))


def fetch_filing_years():
    """Return {year: pdf_url} for every 'user friendly budget' link on the page."""
    resp = requests.get(BUDGETS_URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    out = {}
    for a in soup.select('a[href*="download="]'):
        href = a["href"]
        m = re.search(r"download=(\d+):(\d{4})-user-friendly", href)
        if not m:
            continue
        download_id, year = m.group(1), int(m.group(2))
        out[year] = (download_id, urljoin(BASE, href))
    return out


def download_pdf(url, download_id):
    PDF_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = PDF_CACHE_DIR / f"budget_{download_id}.pdf"
    if path.exists():
        return path
    resp = requests.get(url, headers=HEADERS, timeout=120)
    resp.raise_for_status()
    path.write_bytes(resp.content)
    return path


def find_breakdown_page(pdf):
    for page in pdf.pages:
        text = page.extract_text() or ""
        if "PROPERTY TAX BREAKDOWN" in text:
            return text
    return None


def parse_entity_row(text, name):
    # e.g. "Municipal Purpose Tax 0.903 $19,155,373.22 30.21% $3,019.16 Municipal Purpose Tax"
    pattern = re.compile(
        re.escape(name) + r"\s+([\d.]+)\s+\$([\d,]+\.\d{2})\s+([\d.]+)%\s+\$([\d,]+\.\d{2})\s+" + re.escape(name)
    )
    m = pattern.search(text)
    if not m:
        return None
    rate, levy, pct, impact = m.groups()
    return {
        "name": name,
        "rate": float(rate),
        "levy": float(levy.replace(",", "")),
        "pctOfTotal": float(pct),
        "avgResidentialImpact": float(impact.replace(",", "")),
    }


def parse_breakdown(text, filing_year):
    year_m = re.search(r"(\d{4}) Calendar Year Property Tax Levies", text)
    if not year_m:
        return None
    actual_year = int(year_m.group(1))

    entities = [e for e in (parse_entity_row(text, name) for name in ENTITIES) if e]
    if not entities:
        return None

    total_m = re.search(
        rf"Total \(Calendar Year {actual_year} Budget\)\s+([\d.]+)\s+\$([\d,]+\.\d{{2}})", text
    )
    valuation_m = re.search(r"Total Taxable Valuation as of[^$]+\$([\d,]+\.\d{2})", text)
    estimate_m = re.search(r"Total ESTIMATED amount to be raised by taxes\s+\$([\d,]+\.\d{2})", text)

    if not total_m:
        return None

    return {
        "actualYear": actual_year,
        "filingYear": filing_year,
        "entities": entities,
        "totalRate": float(total_m.group(1)),
        "totalLevy": float(total_m.group(2).replace(",", "")),
        "totalAvgResidentialImpact": round(sum(e["avgResidentialImpact"] for e in entities), 2),
        "taxableValuation": float(valuation_m.group(1).replace(",", "")) if valuation_m else None,
        "currentYearEstimatedLevy": float(estimate_m.group(1).replace(",", "")) if estimate_m else None,
    }


def main():
    budgets = load_budgets()
    processed_ids = set(budgets.get("processedFilingIds", []))

    print("Fetching municipal budgets listing...")
    filings = fetch_filing_years()
    new_filings = {
        y: (download_id, url)
        for y, (download_id, url) in filings.items()
        if int(download_id) not in processed_ids
    }

    if not new_filings:
        print("No new budget filings found. Data is up to date.")
        return

    for filing_year, (download_id, url) in sorted(new_filings.items()):
        print(f"Found new filing: {filing_year} (id {download_id})")
        pdf_path = download_pdf(url, download_id)

        with pdfplumber.open(pdf_path) as pdf:
            text = find_breakdown_page(pdf)

        if not text:
            print(
                f"  ! {filing_year} filing has no extractable text (likely a scanned PDF) - "
                f"add it to docs/data/budgets.json by hand: {url}",
                file=sys.stderr,
            )
            continue

        parsed = parse_breakdown(text, filing_year)
        if not parsed:
            print(
                f"  ! could not parse the tax breakdown for {filing_year} - the form layout may "
                f"have changed. Check by hand: {url}",
                file=sys.stderr,
            )
            continue

        # the previous latestBreakdown is already a finalized actual year -
        # move it into history untouched, then the newly-parsed year (also a
        # finalized actual, straight from this new filing's own left-hand
        # "Calendar Year" table) becomes the new latestBreakdown.
        prev = budgets["latestBreakdown"]
        if parsed["actualYear"] <= prev["year"]:
            print(
                f"  ! parsed actual year {parsed['actualYear']} is not newer than the "
                f"current latest ({prev['year']}) - skipping, check by hand: {url}",
                file=sys.stderr,
            )
            continue
        budgets["history"].append(
            {
                "year": prev["year"],
                "totalRate": prev["totalRate"],
                "totalLevy": prev["totalLevy"],
                "taxableValuation": prev.get("taxableValuation"),
                "sourceUrl": prev["sourceUrl"],
                "sourceLabel": f"reported in the {filing_year} filing",
            }
        )
        budgets["latestBreakdown"] = {
            "year": parsed["actualYear"],
            "entities": parsed["entities"],
            "totalRate": parsed["totalRate"],
            "totalLevy": parsed["totalLevy"],
            "totalAvgResidentialImpact": parsed["totalAvgResidentialImpact"],
            "taxableValuation": parsed["taxableValuation"],
            "sourceUrl": url,
        }
        if parsed["currentYearEstimatedLevy"]:
            budgets["currentYearEstimate"] = {
                "year": filing_year,
                "estimatedTotalLevy": parsed["currentYearEstimatedLevy"],
                "note": (
                    "The Borough's own current-year budget estimate - not yet a finalized "
                    "calendar-year actual, shown separately from the history above for that reason."
                ),
                "sourceUrl": url,
            }
        processed_ids.add(int(download_id))
        budgets["processedFilingIds"] = sorted(processed_ids)
        print(f"  -> parsed {parsed['actualYear']} actuals and {filing_year} estimate")

    save_budgets(budgets)
    print("Done. docs/data/budgets.json updated.")


if __name__ == "__main__":
    main()
