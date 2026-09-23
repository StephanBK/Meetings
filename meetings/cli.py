"""Command-line interface for the meetings pipeline."""

import argparse
import csv
import sys
from pathlib import Path
from datetime import datetime, date

import yaml

from . import db, config
from .adapters.pdf_watcher import PdfWatcherAdapter
from .fetch import fetch_new_documents
from .extract import extract_fetched_documents
from .scan import scan_extracted_documents
from .classify import classify_documents, print_cost_summary
from .reports import generate_all_reports


def cmd_initdb(args):
    """Initialize database schema."""
    print("Initializing database schema...")
    db.init_schema()
    print("Schema created successfully.")


def cmd_load_bodies(args):
    """Load bodies from CSV file."""
    csv_path = Path(args.csv_file)
    if not csv_path.exists():
        print(f"Error: File not found: {csv_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Loading bodies from {csv_path}...")

    # Read CSV
    with open(csv_path, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    # Prepare insert SQL with ON CONFLICT for idempotency
    insert_sql = """
        INSERT INTO bodies (
            body_id, name, segment, county, website, board_page_url,
            platform, coverage_level, blocker_code, enrollment,
            expected_meetings_per_year, own_site_doc_links,
            last_checked, last_success, manual_notes
        ) VALUES (
            %(body_id)s, %(name)s, %(segment)s, %(county)s, %(website)s, %(board_page_url)s,
            %(platform)s, %(coverage_level)s, %(blocker_code)s, %(enrollment)s,
            %(expected_meetings_per_year)s, %(own_site_doc_links)s,
            %(last_checked)s, %(last_success)s, %(manual_notes)s
        )
        ON CONFLICT (body_id) DO UPDATE SET
            name = EXCLUDED.name,
            segment = EXCLUDED.segment,
            county = EXCLUDED.county,
            website = EXCLUDED.website,
            board_page_url = EXCLUDED.board_page_url,
            platform = EXCLUDED.platform,
            coverage_level = EXCLUDED.coverage_level,
            blocker_code = EXCLUDED.blocker_code,
            enrollment = EXCLUDED.enrollment,
            expected_meetings_per_year = EXCLUDED.expected_meetings_per_year,
            own_site_doc_links = EXCLUDED.own_site_doc_links,
            last_checked = EXCLUDED.last_checked,
            last_success = EXCLUDED.last_success,
            manual_notes = EXCLUDED.manual_notes
    """

    def parse_int(val):
        """Parse integer, return None for empty strings."""
        if val is None or val.strip() == "":
            return None
        try:
            return int(val)
        except ValueError:
            return None

    def parse_timestamp(val):
        """Parse timestamp, return None for empty strings."""
        if val is None or val.strip() == "":
            return None
        try:
            return datetime.fromisoformat(val)
        except ValueError:
            # Try date-only format
            try:
                return datetime.strptime(val, "%Y-%m-%d")
            except ValueError:
                return None

    def empty_to_none(val):
        """Convert empty string to None."""
        if val is None or val.strip() == "":
            return None
        return val

    # Transform rows to params
    params_list = []
    for row in rows:
        params = {
            "body_id": row["body_id"],
            "name": row["name"],
            "segment": empty_to_none(row.get("segment")),
            "county": empty_to_none(row.get("county")),
            "website": empty_to_none(row.get("website")),
            "board_page_url": empty_to_none(row.get("board_page_url")),
            "platform": empty_to_none(row.get("platform")),
            "coverage_level": parse_int(row.get("coverage_level")) or 0,
            "blocker_code": empty_to_none(row.get("blocker_code")),
            "enrollment": parse_int(row.get("enrollment")),
            "expected_meetings_per_year": parse_int(row.get("expected_meetings_per_year")),
            "own_site_doc_links": parse_int(row.get("own_site_doc_links")),
            "last_checked": parse_timestamp(row.get("last_checked")),
            "last_success": parse_timestamp(row.get("last_success")),
            "manual_notes": empty_to_none(row.get("manual_notes")),
        }
        params_list.append(params)

    # Insert all rows
    with db.get_connection() as conn:
        with conn.cursor() as cur:
            for params in params_list:
                cur.execute(insert_sql, params)
        conn.commit()

    print(f"Loaded {len(params_list)} bodies.")


def update_coverage_level(body_id: str, new_level: int, note: str = None):
    """Update coverage level and log the change."""
    current = db.fetch_one(
        "SELECT coverage_level FROM bodies WHERE body_id = %s",
        (body_id,)
    )
    old_level = current["coverage_level"] if current else 0

    if old_level == new_level:
        return  # No change needed

    # Update body
    db.execute(
        "UPDATE bodies SET coverage_level = %s, last_checked = %s WHERE body_id = %s",
        (new_level, datetime.now(), body_id),
        commit=True
    )

    # Log the change
    db.execute(
        """INSERT INTO coverage_log (body_id, old_level, new_level, note, changed_at)
           VALUES (%s, %s, %s, %s, %s)""",
        (body_id, old_level, new_level, note, datetime.now()),
        commit=True
    )

    print(f"  Coverage: {body_id} {old_level} -> {new_level}")


def cmd_discover(args):
    """Discover documents from board pages."""
    # Load slice bodies config
    slice_config_path = config.CONFIG_DIR / "slice_bodies.yaml"
    if not slice_config_path.exists():
        print(f"Error: {slice_config_path} not found", file=sys.stderr)
        sys.exit(1)

    with open(slice_config_path) as f:
        slice_config = yaml.safe_load(f)

    # Backfill window: 12 months ago from today
    backfill_start = date(date.today().year - 1, date.today().month, date.today().day)
    print(f"Backfill window: {backfill_start} to today")

    adapter = PdfWatcherAdapter(backfill_start)
    total_new = 0
    total_existing = 0

    for body_config in slice_config["bodies"]:
        body_id = body_config["body_id"]
        name = body_config["name"]
        pages = body_config["pages"]
        expected = body_config.get("expected_meetings_per_year", 20)

        print(f"\n{name} ({body_id})")

        # Discover documents
        docs = adapter.list_documents(body_id, pages)

        if not docs:
            print("  No documents found")
            continue

        # Update to level 4 (adapter found documents)
        update_coverage_level(body_id, 4, "Adapter discovered documents")

        # Insert documents into database
        new_count = 0
        existing_count = 0

        for doc in docs:
            # Check if already exists
            existing = db.fetch_one(
                "SELECT document_id FROM documents WHERE source_url = %s",
                (doc.source_url,)
            )

            if existing:
                existing_count += 1
                continue

            # Calculate in_window based on meeting_date
            in_window = None
            if doc.meeting_date:
                window_start = date.today().replace(year=date.today().year - 1)
                in_window = doc.meeting_date >= window_start

            # Insert new document with meeting_date and in_window
            db.execute(
                """INSERT INTO documents (body_id, doc_type, source_url, link_text, status, meeting_date, in_window)
                   VALUES (%s, %s, %s, %s, 'new', %s, %s)""",
                (body_id, doc.doc_type, doc.source_url, doc.link_text, doc.meeting_date, in_window),
                commit=True
            )

            # Create or find meeting record if we have a date
            if doc.meeting_date:
                meeting = db.fetch_one(
                    """SELECT meeting_id FROM meetings
                       WHERE body_id = %s AND meeting_date = %s AND meeting_type = %s""",
                    (body_id, doc.meeting_date, doc.meeting_type or "regular")
                )

                if not meeting:
                    db.execute(
                        """INSERT INTO meetings (body_id, meeting_date, meeting_type)
                           VALUES (%s, %s, %s)
                           ON CONFLICT DO NOTHING""",
                        (body_id, doc.meeting_date, doc.meeting_type or "regular"),
                        commit=True
                    )

            new_count += 1

        total_new += new_count
        total_existing += existing_count

        # Get actual count in backfill window
        doc_count = db.fetch_one(
            "SELECT COUNT(*) as cnt FROM documents WHERE body_id = %s",
            (body_id,)
        )["cnt"]

        print(f"  New: {new_count}, Existing: {existing_count}")
        print(f"  Documents in DB: {doc_count}, Expected meetings/year: {expected}")

    print(f"\nTotal: {total_new} new, {total_existing} existing")


def cmd_fetch(args):
    """Fetch documents with status 'new'."""
    print("Fetching new documents...")
    success, errors = fetch_new_documents()
    print(f"\nFetch complete: {success} success, {errors} errors")

    # Update coverage to level 5 for bodies with fetched documents
    bodies_with_fetched = db.fetch_all(
        """SELECT DISTINCT body_id FROM documents WHERE status = 'fetched'"""
    )

    for row in bodies_with_fetched:
        body_id = row["body_id"]
        current = db.fetch_one(
            "SELECT coverage_level FROM bodies WHERE body_id = %s",
            (body_id,)
        )
        if current and current["coverage_level"] < 5:
            update_coverage_level(body_id, 5, "First successful document fetch")

            # Update last_success
            db.execute(
                "UPDATE bodies SET last_success = %s WHERE body_id = %s",
                (datetime.now(), body_id),
                commit=True
            )

    # Print gap check
    print("\n--- Gap Check (Level 6 readiness) ---")
    slice_config_path = config.CONFIG_DIR / "slice_bodies.yaml"
    with open(slice_config_path) as f:
        slice_config = yaml.safe_load(f)

    for body_config in slice_config["bodies"]:
        body_id = body_config["body_id"]
        name = body_config["name"]
        expected = body_config.get("expected_meetings_per_year", 20)

        doc_count = db.fetch_one(
            "SELECT COUNT(*) as cnt FROM documents WHERE body_id = %s AND status = 'fetched'",
            (body_id,)
        )["cnt"]

        coverage = db.fetch_one(
            "SELECT coverage_level FROM bodies WHERE body_id = %s",
            (body_id,)
        )
        level = coverage["coverage_level"] if coverage else 0

        print(f"{name}: {doc_count} docs fetched, {expected} expected/year, level {level}")


def cmd_extract(args):
    """Extract text from fetched documents."""
    print("Extracting text from fetched documents...")
    native, ocr, errors = extract_fetched_documents()
    print(f"\nExtraction complete: {native} native, {ocr} OCR, {errors} errors")


def cmd_scan(args):
    """Scan extracted documents for keyword hits."""
    print("Scanning extracted documents for keyword hits...")
    docs_scanned, total_hits = scan_extracted_documents()
    print(f"\nScan complete: {docs_scanned} documents scanned, {total_hits} total hits")


def cmd_classify(args):
    """Classify documents with LLM."""
    from . import config
    from .classify import check_daily_cap

    # Parse document IDs if provided
    doc_ids = None
    if args.docs:
        doc_ids = [int(d.strip()) for d in args.docs.split(",")]
        print(f"Classifying {len(doc_ids)} specific documents...")
    elif args.all:
        print("Classifying all scanned documents...")
    elif args.in_window:
        print("Classifying in_window scanned documents only...")
    else:
        print("Error: specify --all, --in-window, or --docs", file=sys.stderr)
        sys.exit(1)

    # Get cost cap
    cost_cap = config.LLM_DAILY_CAP if hasattr(config, 'LLM_DAILY_CAP') else None

    docs_classified, total_signals, input_tokens, output_tokens = classify_documents(
        doc_ids,
        in_window_only=getattr(args, 'in_window', False),
        cost_cap=cost_cap
    )

    print(f"\nClassification complete: {docs_classified} documents, {total_signals} signals")
    print_cost_summary(input_tokens, output_tokens, config.LLM_MODEL)


def cmd_report(args):
    """Generate reports."""
    print("Generating reports...")
    generate_all_reports()


def cmd_run_all(args):
    """Run the complete pipeline: discover, fetch, extract, scan, classify, report.

    This is the schedule-ready command for automated runs.
    Uses slice_bodies.yaml for document discovery.
    """
    from datetime import datetime as dt
    from . import config

    dry_run = getattr(args, 'dry_run', False)
    mode = "DRY RUN" if dry_run else "FULL RUN"

    print("=" * 60)
    print(f"MEETINGS PIPELINE - {mode}")
    print(f"Started: {dt.now().isoformat()}")
    if not dry_run:
        print(f"Daily cost cap: ${config.LLM_DAILY_CAP:.2f}")
    print("=" * 60)

    if dry_run:
        # Dry run: just report what would happen
        _dry_run_report()
        return

    # Create a mock args object for sub-commands
    class Args:
        pass

    # Step 1: Discover
    print("\n" + "=" * 60)
    print("STEP 1: DISCOVER")
    print("=" * 60)
    discover_args = Args()
    discover_args.slice = True
    cmd_discover(discover_args)

    # Step 2: Fetch
    print("\n" + "=" * 60)
    print("STEP 2: FETCH")
    print("=" * 60)
    fetch_args = Args()
    cmd_fetch(fetch_args)

    # Step 3: Extract
    print("\n" + "=" * 60)
    print("STEP 3: EXTRACT")
    print("=" * 60)
    extract_args = Args()
    cmd_extract(extract_args)

    # Step 4: Scan
    print("\n" + "=" * 60)
    print("STEP 4: SCAN")
    print("=" * 60)
    scan_args = Args()
    cmd_scan(scan_args)

    # Step 5: Classify (in_window documents only, with cost cap)
    print("\n" + "=" * 60)
    print("STEP 5: CLASSIFY (in_window only)")
    print("=" * 60)
    classify_args = Args()
    classify_args.all = False
    classify_args.in_window = True
    classify_args.docs = None
    cmd_classify(classify_args)

    # Step 6: Report
    print("\n" + "=" * 60)
    print("STEP 6: REPORT")
    print("=" * 60)
    report_args = Args()
    cmd_report(report_args)

    print("\n" + "=" * 60)
    print(f"PIPELINE COMPLETE")
    print(f"Finished: {dt.now().isoformat()}")
    print("=" * 60)


def _dry_run_report():
    """Report what each pipeline step would do without executing."""
    import yaml
    from . import config
    from .classify import get_today_llm_cost

    print("\n--- DRY RUN: What each step would do ---\n")

    # Discover
    slice_path = config.CONFIG_DIR / "slice_bodies.yaml"
    if slice_path.exists():
        with open(slice_path) as f:
            slice_config = yaml.safe_load(f)
        body_count = len(slice_config.get("bodies", []))
        print(f"DISCOVER: Scan {body_count} bodies from slice_bodies.yaml for new documents")
    else:
        print("DISCOVER: No slice_bodies.yaml found")

    # Fetch
    new_docs = db.fetch_one("SELECT COUNT(*) as cnt FROM documents WHERE status = 'new'")["cnt"]
    print(f"FETCH: Download {new_docs} documents with status='new'")

    # Extract
    fetched_docs = db.fetch_one("SELECT COUNT(*) as cnt FROM documents WHERE status = 'fetched'")["cnt"]
    print(f"EXTRACT: Extract text from {fetched_docs} fetched documents")

    # Scan
    extracted_docs = db.fetch_one("SELECT COUNT(*) as cnt FROM documents WHERE status = 'extracted'")["cnt"]
    print(f"SCAN: Scan {extracted_docs} extracted documents for keywords")

    # Classify
    scanned_in_window = db.fetch_one(
        "SELECT COUNT(*) as cnt FROM documents WHERE status = 'scanned' AND in_window = true"
    )["cnt"]
    today_cost = get_today_llm_cost()
    remaining = max(0, config.LLM_DAILY_CAP - today_cost)
    print(f"CLASSIFY: Classify {scanned_in_window} in_window documents")
    print(f"          Today's LLM cost: ${today_cost:.4f}, daily cap: ${config.LLM_DAILY_CAP:.2f}, remaining: ${remaining:.4f}")

    # Report
    print(f"REPORT: Generate coverage.md, signals.csv, projects.csv, coverage_trend.csv, calibration.md")

    print("\n--- No changes made (dry run) ---")


def main():
    parser = argparse.ArgumentParser(
        prog="meetings",
        description="Meetings pipeline CLI",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # initdb command
    subparsers.add_parser("initdb", help="Initialize database schema")

    # load-bodies command
    load_parser = subparsers.add_parser("load-bodies", help="Load bodies from CSV")
    load_parser.add_argument("csv_file", help="Path to bodies CSV file")

    # discover command
    discover_parser = subparsers.add_parser("discover", help="Discover documents from board pages")
    discover_parser.add_argument("--slice", action="store_true", help="Use slice_bodies.yaml config")

    # fetch command
    subparsers.add_parser("fetch", help="Fetch documents with status 'new'")

    # extract command
    subparsers.add_parser("extract", help="Extract text from fetched documents")

    # scan command
    subparsers.add_parser("scan", help="Scan extracted documents for keyword hits")

    # classify command
    classify_parser = subparsers.add_parser("classify", help="Classify documents with LLM")
    classify_parser.add_argument("--all", action="store_true", help="Classify all scanned documents")
    classify_parser.add_argument("--in-window", action="store_true", dest="in_window", help="Classify only in_window scanned documents")
    classify_parser.add_argument("--docs", help="Comma-separated list of document IDs to classify")

    # report command
    subparsers.add_parser("report", help="Generate reports")

    # run-all command
    run_all_parser = subparsers.add_parser("run-all", help="Run complete pipeline (discover, fetch, extract, scan, classify, report)")
    run_all_parser.add_argument("--dry-run", action="store_true", dest="dry_run", help="Print what each step would do without executing")

    args = parser.parse_args()

    if args.command == "initdb":
        cmd_initdb(args)
    elif args.command == "load-bodies":
        cmd_load_bodies(args)
    elif args.command == "discover":
        cmd_discover(args)
    elif args.command == "fetch":
        cmd_fetch(args)
    elif args.command == "extract":
        cmd_extract(args)
    elif args.command == "scan":
        cmd_scan(args)
    elif args.command == "classify":
        cmd_classify(args)
    elif args.command == "report":
        cmd_report(args)
    elif args.command == "run-all":
        cmd_run_all(args)


if __name__ == "__main__":
    main()
