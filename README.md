# Elmwood Park Council Tracker

Makes Elmwood Park, NJ town council meetings easier to follow: pulls minutes,
videos, and per-member roll-call votes straight from the Borough's own
website (elmwoodparknj.us), and updates itself automatically.

## How it works

- `scraper/update_data.py` checks the Borough's minutes page for new meetings,
  downloads each new minutes PDF, and parses every resolution's "Record of
  Council Vote on Passage" table to get each member's Aye/Nay/Abstain/Absent
  vote. It also matches meetings to their YouTube video, when one exists.
- Results are written to `docs/data/meetings.json` and `docs/data/resolutions.json`.
- `docs/` is a plain HTML/CSS/JS site (no build step) that reads those JSON
  files directly. Data lives inside `docs/` so the folder is self-contained —
  it works the same whether it's served from the repo root or as its own
  GitHub Pages root.
- `scraper/update_budgets.py` checks the Borough's municipal-budgets page for
  a new "User Friendly Budget" filing (a standardized NJ state form) and, if
  it's a real digital PDF (not a scanned image), parses the property-tax
  breakdown into `docs/data/budgets.json`. Years 2017-2023 are scanned images
  with no extractable text and were entered by hand once, sourced from the
  Borough's own filings (see `sourceUrl`/`sourceLabel` on each entry); 2024
  onward is parsed automatically as new filings appear.
- `.github/workflows/update.yml` runs both scrapers once a day on GitHub's
  servers and commits any new data automatically — the live site (via GitHub
  Pages) picks up the change on the next commit, no manual steps needed.

## Running the scrapers yourself

```
pip install -r scraper/requirements.txt
python3 scraper/update_data.py
python3 scraper/update_budgets.py
```

Both are safe to re-run any time — they only process meetings/filings not
already present in `docs/data/`.

## Viewing the site locally

Browsers block a static page from loading local JSON files directly
(`file://` URLs), so serve the folder over a local server instead:

```
cd docs
python3 -m http.server 8000
```

Then open http://localhost:8000 in a browser.

## Scope

v1 covers 2026 meetings only. Older years can be backfilled later by pointing
the scraper at the Borough's `minutes-2025`, `minutes-2024`, etc. pages.
Upcoming-agenda tracking and dollar-amount totals from budget/bond items are
deferred to a future version.
