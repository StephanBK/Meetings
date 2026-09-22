# BUILD_PLAN.md: vertical slice, 3 districts end to end

Written 2026-09-20. Owner: Stephan. Executor: Claude Code, one task at a time.
Context and all decisions: README.md and the handover doc linked there.

## Goal of this slice

Push 3 Long Island school districts through the whole pipeline: find documents, download, extract text,
keyword scan, LLM classification, reports. Then widen to the 44 bodies in pilot_44.csv.

Slice districts (all post PDFs on their own sites, files hosted on files.smartsites.parentsquare.com):

| Body | Pages to watch |
|---|---|
| Commack UFSD | https://commack.k12.ny.us/boeminutes and https://commack.k12.ny.us/agenda |
| Deer Park UFSD | https://www.deerparkschools.org/agenda-and-minutes |
| West Islip UFSD | https://www.wi.k12.ny.us/boe (includes Buildings and Grounds and Finance committee agendas) |

Backfill window: documents dated 2025-09-20 or later (12 months).

## How to work (rules for the executor)

1. Do ONE task per session. Stop after its acceptance checks pass. Summarize what was built and wait.
2. Before coding each task: state the number of steps and a time estimate. Explain any new concept in 2 to 3 plain sentences.
3. Small commits, clear messages. Never commit secrets. Secrets live in `.env` (already in .gitignore).
4. Python 3.11+, standard layout below. Keep dependencies few. Add each to requirements.txt.
5. No em dashes in any output, code comments or docs.
6. Crawler rules are not negotiable:
   - Honest User-Agent: `MeetingsBot/0.1 (+https://github.com/StephanBK/Meetings)`
   - Obey robots.txt per host. 4xx on robots.txt means no rules, 5xx means stay out
   - Max 1 request per second per host
   - Never work around a 403, a bot challenge or a CAPTCHA. Never disguise the client as a browser.
     Record a blocker code in `bodies` and move on
   - BoardDocs (go.boarddocs.com, www.boarddocs.com) returns 403 to automated clients. Do not request it at all
7. Every level change of a body writes a row to `coverage_log`.

## Target layout

```
meetings/            Python package
  config.py          reads .env
  db.py              connection + helpers
  schema.sql         all tables
  adapters/
    base.py          Adapter interface: list_documents(body) -> [DocRef]
    pdf_watcher.py   generic: board page -> dated PDF links
  fetch.py           polite downloader (robots, rate limit, sha256)
  extract.py         PDF -> text, OCR fallback
  scan.py            keyword scan (move existing scan.py logic here, keep taxonomy.yaml as the only vocabulary source)
  classify.py        LLM classifier
  reports.py         coverage + signals + calibration
  cli.py             `python -m meetings <command>`
config/taxonomy.yaml
config/slice_bodies.yaml   the 3 bodies above with their page URLs
data/                existing CSVs move here
downloads/           raw files, gitignored
tests/
```

Moving existing files into this layout is part of T1. Keep fingerprint.py, pass2.py, pass3.py under `census/` unchanged.

## Environment (.env, never committed)

```
DATABASE_URL=postgresql://...        # Railway Postgres
ANTHROPIC_API_KEY=...
LLM_MODEL=claude-haiku-4-5-20251001  # verify the current Haiku model name in Anthropic docs before first run
```

---

## T1. Database tables (est. 30 min)

Create `schema.sql` and a `python -m meetings initdb` command. Tables:

- `bodies`: body_id PK (text, reuse ids from data/bodies.csv), name, segment, county, website, board_page_url,
  platform, coverage_level int, blocker_code, enrollment int, expected_meetings_per_year int,
  own_site_doc_links int, last_checked, last_success, manual_notes
- `meetings`: meeting_id PK, body_id FK, meeting_date date, meeting_type, title. Unique (body_id, meeting_date, meeting_type)
- `documents`: document_id PK, body_id FK, meeting_id FK nullable, doc_type (agenda, minutes, packet, committee_agenda, other),
  source_url UNIQUE, link_text, file_path, sha256, bytes int, pages int, text, text_method (native or ocr),
  fetched_at, status (new, fetched, extracted, scanned, classified, error), error
- `keyword_hits`: hit_id PK, document_id FK, passage_index int, passage_text, trades text[], stages text[], triggers text[]
- `signals`: signal_id PK, document_id FK, passage_index int nullable, is_signal bool, trades text[], stage, building,
  scope_summary, dollar_amount numeric, amount_from_ocr bool, funding_source, key_dates jsonb, vendors_named jsonb,
  evidence_quote, page_or_item, confidence numeric, model, created_at
- `llm_runs`: run_id PK, document_id FK, model, input_tokens int, output_tokens int, created_at
- `coverage_log`: id PK, body_id FK, old_level int, new_level int, blocker_code, note, changed_at

Also: `python -m meetings load-bodies data/bodies.csv` loads all 1,026 bodies.

Acceptance:
- [ ] `initdb` runs twice without error (idempotent)
- [ ] `select count(*) from bodies` returns 1026
- [ ] `select coverage_level, count(*) from bodies group by 1` matches the CSV

## T2. PDF watcher adapter (est. 2 to 3 hr)

`python -m meetings discover --slice` visits each page in config/slice_bodies.yaml, extracts links to agenda and
minutes files, parses the meeting date from link text or file name, classifies doc_type from the words
(minutes, agenda, committee names), keeps only dates inside the backfill window, inserts new `documents` rows with status `new`.
`python -m meetings fetch` downloads every `new` document into downloads/<body_id>/, records sha256, bytes, status `fetched`.

Notes:
- All 3 sites run the same CMS and store files at files.smartsites.parentsquare.com. Check that host's robots.txt too.
- Same URL is never downloaded twice (source_url is unique). Same sha256 under a new URL is stored once and linked.
- A date that cannot be parsed goes in with meeting_id null and a warning, never dropped silently.

Acceptance:
- [ ] Commack: minutes for 2026-08-20 and 2026-09-03 are found
- [ ] Deer Park: agenda 2026-08-25 and minutes 2026-07-21 are found
- [ ] West Islip: regular meeting agenda 2026-09-10 and Buildings and Grounds agenda 2026-09-08 are found
- [ ] Second run of `discover` inserts 0 new rows
- [ ] Bodies move to coverage_level 5 with `coverage_log` rows (level 4 when the adapter first lists documents, 5 after first successful fetch)
- [ ] Print per body: documents found vs meetings expected in 12 months (first version of the level 6 gap check)
- [ ] Every document has a meeting_date (dates parsed from link text with URL year inference)
- [ ] No documents outside the backfill window (documents without parseable dates are still inserted with a warning)

## T3. Text extraction (est. 45 min)

`python -m meetings extract`: native text first (pdftotext or pdfplumber). If a document has fewer than 100 characters
per page on average, treat it as a scan: render pages at about 130 dpi and OCR with tesseract.
Set text_method, pages, status `extracted`.

Acceptance:
- [ ] West Islip 2026-09-10 agenda (36 scanned pages) gets text_method = ocr and more than 50,000 characters
- [ ] The other 5 known documents get text_method = native
- [ ] Extraction of the 6 known documents finishes in under 3 minutes

## T4. Keyword scan into the database (est. 30 min)

Move the logic of scan.py into meetings/scan.py, unchanged in behavior. Vocabulary comes only from config/taxonomy.yaml.
`python -m meetings scan` writes `keyword_hits` for every extracted document, status `scanned`.

Acceptance (taxonomy v0.2 on the 6 known documents):
- [ ] 26 hit passages in total
- [ ] Deer Park 2026-08-25 agenda: 12 hit passages, roofing present
- [ ] Deer Park 2026-07-21 minutes: no hit on personnel lines (custodian, asbestos inspector)
- [ ] A unit test covers: negative phrase removal, skip patterns, plural and -ing endings

## T5. LLM classifier, calibration mode (est. 1 hr)

`python -m meetings classify --all` sends EVERY extracted document to the model (not only keyword hits). This is the
calibration design: the LLM result is the ground truth used to measure the keyword filter.
- Split long documents into chunks of about 12,000 characters on passage boundaries
- System prompt: the taxonomy (trades, stages) plus the output_schema from taxonomy.yaml. Ask for a JSON array, one object per
  distinct project or purchase. Personnel actions, tuition, curriculum and policy items are not signals
- evidence_quote must be copied from the text, under 25 words. Reject and retry once if the quote is not found in the chunk
- If text_method = ocr, set amount_from_ocr = true on every signal with a dollar amount
- Log tokens to `llm_runs`

Acceptance:
- [ ] Deer Park 2026-08-25: a roofing signal for Deer Park High School, dollar_amount 573850, stage 3 or 4
- [ ] Deer Park 2026-07-21 minutes: signals for the concession and bathrooms work, the high school electric upgrade, and the Lincoln security command center
- [ ] Commack 2026-09-03: an hvac signal for piped heating and cooling distribution, and an award to Capital Renovation Corp. for masonry
- [ ] West Islip: a signal mentioning the high school pool construction
- [ ] Zero signals from personnel appointments
- [ ] Print total tokens and cost for the run

## T6. Reports (est. 1 hr)

`python -m meetings report` writes three files to reports/:
1. `coverage.md`: bodies per coverage level, by count and weighted by enrollment, plus blocker code Pareto
2. `signals.csv`: all signals, sorted by stage (earliest first), then dollar amount. Columns include body, building, trades,
   stage, amount, evidence_quote, source_url
3. `calibration.md`: for each LLM signal, did a keyword hit exist in the same document within the same passage?
   Report miss rate (LLM signal with no keyword hit) and false-hit rate (keyword hit passage with no LLM signal), plus the
   list of missed passages so taxonomy.yaml can be improved

Acceptance:
- [ ] All three files are produced from a clean database by running: initdb, load-bodies, discover, fetch, extract, scan, classify, report
- [ ] calibration.md shows the two rates as percentages with the counts behind them

---

## After the slice

Review signals.csv and calibration.md with Stephan before widening. Then: add the remaining bodies from data/pilot_44.csv
to config, handle the Google Drive group (public folder listing through the Drive API, no scraping), and move file storage
from downloads/ to object storage.
