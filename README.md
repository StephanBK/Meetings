# Meetings

Turns public meeting records (agendas, minutes, packets) into early buying signals for building trades.
Pilot area: New York City and Long Island. Status: design and measurement done, no pipeline built yet.

Full project state, decisions and math: see the handover doc
https://claude.ai/code/artifact/d5639c5e-13c6-4d8d-b408-04fee9a0c9b6

## Files

| File | What it is |
|---|---|
| universe_ledger.csv | One row per segment (e.g. LI school districts). Registry source, exact count, coverage so far. This is the denominator |
| bodies.csv | One row per public body (1,026). Coverage level 0 to 6, blocker code, platform, board page URL |
| manual_queue_li.csv | The 24 Long Island school bodies still below level 3, biggest first. Fill in board_page_url and platform by hand |
| li_census_raw_detail.csv | Raw output of the first crawl pass, for reference |
| taxonomy.yaml | Signal vocabulary v0.2: 12 trades, 7 stages, triggers, negative phrases, skip patterns, LLM output fields |
| fingerprint.py | Pass 1. Visits each body's website, detects the agenda platform from link patterns |
| pass2.py | Pass 2. Deeper crawl: URL variants, sitemap, two link levels |
| pass3.py | Pass 3. Headless browser, for sites whose menus are built by JavaScript |
| scan.py | Free keyword scan. Reads taxonomy.yaml, scans .txt files, writes hits.json |

## Coverage ladder

0 known, 1 website found, 2 board page found, 3 platform identified, 4 adapter exists, 5 documents flowing, 6 verified complete.

## Rules the crawlers follow

- Identify honestly (User-Agent names this repo)
- Obey robots.txt. A 4xx on robots.txt means no rules, a 5xx means stay out
- Max 1 request per second per site
- Never work around a bot challenge or a 403. Log it as a blocker code instead

## Run

    pip install -r requirements.txt
    python3 -m playwright install chromium        # only needed for pass3.py
    python3 fingerprint.py li_bodies.csv out.csv   # input needs columns: name, website
    python3 scan.py path/to/folder_with_txt_files

## Known issues

- BoardDocs (about 62% of Long Island enrollment) answers automated clients with 403. Access route undecided
- scan.py was tuned on the same 6 documents it was tested on. Needs a fresh test set
- OCR text can misread dollar amounts
