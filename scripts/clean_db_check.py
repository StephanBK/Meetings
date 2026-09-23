#!/usr/bin/env python3
"""Real clean-database check: verify pipeline chain runs with existing files (no network/API)."""

import os
import sys
import subprocess
from datetime import datetime
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(__file__).rsplit('/', 2)[0])


def run_cmd(cmd: list[str], description: str) -> tuple[bool, str]:
    """Run a command and return (success, output)."""
    print(f"\n{description}...")
    print(f"  Command: {' '.join(cmd)}")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            cwd=Path(__file__).parent.parent
        )

        output = result.stdout + result.stderr
        if result.returncode == 0:
            print(f"  OK (exit code 0)")
            # Show key output lines
            for line in output.splitlines()[-5:]:
                if line.strip():
                    print(f"    {line}")
            return True, output
        else:
            print(f"  FAIL (exit code {result.returncode})")
            print(f"    {output[:500]}")
            return False, output

    except subprocess.TimeoutExpired:
        print(f"  FAIL (timeout)")
        return False, "Timeout"
    except Exception as e:
        print(f"  FAIL ({e})")
        return False, str(e)


def main():
    print("=== Real Clean-Database Check ===")
    print(f"Started: {datetime.now().isoformat()}")
    print("This test verifies pipeline chain using existing downloaded files.")
    print("No network requests or API calls will be made.\n")

    from meetings import config, db
    from meetings.extract import extract_native_text, extract_ocr_text
    from meetings.scan import scan_extracted_documents

    steps = []

    # Step 1: Check database connection
    print("Step 1: Database connection...")
    try:
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        steps.append(("db_connect", True, "Connected"))
        print("  OK: Database connection established")
    except Exception as e:
        steps.append(("db_connect", False, str(e)))
        print(f"  FAIL: {e}")
        return report_results(steps)

    # Step 2: Verify schema exists
    print("\nStep 2: Schema verification...")
    try:
        tables = list(db.fetch_all(
            """SELECT table_name FROM information_schema.tables
               WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"""
        ))
        table_names = [t['table_name'] for t in tables]
        required = ['bodies', 'documents', 'keyword_hits', 'signals']
        missing = [t for t in required if t not in table_names]
        if missing:
            steps.append(("schema", False, f"Missing tables: {missing}"))
            print(f"  FAIL: Missing tables: {missing}")
        else:
            steps.append(("schema", True, f"{len(table_names)} tables"))
            print(f"  OK: Found {len(table_names)} tables including {required}")
    except Exception as e:
        steps.append(("schema", False, str(e)))
        print(f"  FAIL: {e}")

    # Step 3: Verify bodies loaded
    print("\nStep 3: Bodies check...")
    try:
        body_count = db.fetch_one("SELECT COUNT(*) as cnt FROM bodies")['cnt']
        if body_count > 0:
            steps.append(("bodies", True, f"{body_count} bodies"))
            print(f"  OK: {body_count} bodies in database")
        else:
            steps.append(("bodies", False, "No bodies"))
            print("  FAIL: No bodies in database")
    except Exception as e:
        steps.append(("bodies", False, str(e)))
        print(f"  FAIL: {e}")

    # Step 4: Check downloaded files exist
    print("\nStep 4: Downloaded files check...")
    try:
        downloads_dir = config.DOWNLOADS_DIR
        if downloads_dir.exists():
            pdf_files = list(downloads_dir.rglob("*.pdf"))
            steps.append(("downloads", True, f"{len(pdf_files)} PDFs"))
            print(f"  OK: {len(pdf_files)} PDF files in downloads/")
        else:
            steps.append(("downloads", False, "No downloads dir"))
            print("  FAIL: downloads/ directory not found")
    except Exception as e:
        steps.append(("downloads", False, str(e)))
        print(f"  FAIL: {e}")

    # Step 5: Test extraction on one file
    print("\nStep 5: Extraction test...")
    try:
        # Get one fetched document with a file path
        doc = db.fetch_one(
            """SELECT document_id, file_path FROM documents
               WHERE status IN ('fetched', 'extracted', 'scanned', 'classified')
               AND file_path IS NOT NULL LIMIT 1"""
        )
        if doc and doc['file_path'] and Path(doc['file_path']).exists():
            # Try native extraction first
            text, pages = extract_native_text(doc['file_path'])
            method = "native"
            if not text or len(text) < 100:
                text, pages = extract_ocr_text(doc['file_path'])
                method = "ocr"
            if text and len(text) > 100:
                steps.append(("extract", True, f"{method}, {pages} pages, {len(text)} chars"))
                print(f"  OK: Extracted {len(text)} chars via {method}")
            else:
                steps.append(("extract", False, "Empty text"))
                print("  FAIL: Extraction returned empty text")
        else:
            steps.append(("extract", False, "No file to test"))
            print("  SKIP: No fetched document with file available")
    except Exception as e:
        steps.append(("extract", False, str(e)))
        print(f"  FAIL: {e}")

    # Step 6: Test scanning (just verify function works)
    print("\nStep 6: Scan test...")
    try:
        from meetings.scan import scan_text, get_taxonomy
        taxonomy = get_taxonomy()
        test_text = "The school board approved a contract for roof replacement at the high school."
        hits = scan_text(test_text)
        if isinstance(hits, list):
            steps.append(("scan", True, f"{len(hits)} hits on test text"))
            print(f"  OK: Scan returned {len(hits)} hits on test text")
        else:
            steps.append(("scan", False, "Invalid return"))
            print("  FAIL: scan_text returned invalid result")
    except Exception as e:
        steps.append(("scan", False, str(e)))
        print(f"  FAIL: {e}")

    # Step 7: Verify keyword_hits exist
    print("\nStep 7: Keyword hits check...")
    try:
        hit_count = db.fetch_one("SELECT COUNT(*) as cnt FROM keyword_hits")['cnt']
        steps.append(("hits", True, f"{hit_count} hits"))
        print(f"  OK: {hit_count} keyword hits in database")
    except Exception as e:
        steps.append(("hits", False, str(e)))
        print(f"  FAIL: {e}")

    # Step 8: Verify signals exist
    print("\nStep 8: Signals check...")
    try:
        signal_count = db.fetch_one("SELECT COUNT(*) as cnt FROM signals")['cnt']
        steps.append(("signals", True, f"{signal_count} signals"))
        print(f"  OK: {signal_count} signals in database")
    except Exception as e:
        steps.append(("signals", False, str(e)))
        print(f"  FAIL: {e}")

    # Step 9: Test report generation
    print("\nStep 9: Report generation test...")
    try:
        from meetings.reports import generate_all_reports
        generate_all_reports()
        # Check if report files exist
        coverage_path = config.REPORTS_DIR / "coverage.md"
        if coverage_path.exists():
            steps.append(("report", True, "Reports generated"))
            print("  OK: Reports generated successfully")
        else:
            steps.append(("report", False, "Report file missing"))
            print("  FAIL: Report file not created")
    except Exception as e:
        steps.append(("report", False, str(e)))
        print(f"  FAIL: {e}")

    return report_results(steps)


def report_results(steps: list) -> int:
    """Print summary and return exit code."""
    print("\n" + "=" * 50)
    print("=== Summary ===")

    passed = sum(1 for _, ok, _ in steps if ok)
    failed = sum(1 for _, ok, _ in steps if not ok)

    for name, ok, detail in steps:
        status = "[OK]" if ok else "[FAIL]"
        print(f"  {status} {name}: {detail}")

    print(f"\nPassed: {passed}/{len(steps)}")

    if failed == 0:
        print("\nResult: PASS - Pipeline chain verified")
        return 0
    else:
        print(f"\nResult: FAIL - {failed} step(s) failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
