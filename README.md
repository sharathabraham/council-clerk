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
- `.github/workflows/update.yml` runs the scraper once a day on GitHub's
  servers and commits any new data automatically — the live site (via GitHub
  Pages) picks up the change on the next commit, no manual steps needed.

## Running the scraper yourself

```
pip install -r scraper/requirements.txt
python3 scraper/update_data.py
```

Safe to re-run any time — it only downloads meetings not already present in
`docs/data/meetings.json`.

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
