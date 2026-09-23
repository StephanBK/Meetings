"""Report generation for the meetings pipeline."""

import csv
from collections import defaultdict
from datetime import date
from pathlib import Path

from . import db


REPORTS_DIR = Path(__file__).parent.parent / "reports"


def generate_coverage_report() -> str:
    """Generate coverage.md report."""
    lines = ["# Coverage Report", ""]

    # Bodies per coverage level
    level_counts = db.fetch_all("""
        SELECT coverage_level, COUNT(*) as cnt, COALESCE(SUM(enrollment), 0) as total_enrollment
        FROM bodies
        GROUP BY coverage_level
        ORDER BY coverage_level
    """)

    total_bodies = sum(r["cnt"] for r in level_counts)
    total_enrollment = sum(r["total_enrollment"] for r in level_counts)

    lines.append("## Bodies by Coverage Level")
    lines.append("")
    lines.append("| Level | Bodies | % | Enrollment | % Enrollment |")
    lines.append("|-------|--------|---|------------|--------------|")

    for row in level_counts:
        level = row["coverage_level"]
        cnt = row["cnt"]
        enroll = row["total_enrollment"]
        pct = 100 * cnt / total_bodies if total_bodies else 0
        enroll_pct = 100 * enroll / total_enrollment if total_enrollment else 0
        lines.append(f"| {level} | {cnt} | {pct:.1f}% | {enroll:,} | {enroll_pct:.1f}% |")

    lines.append(f"| **Total** | **{total_bodies}** | | **{total_enrollment:,}** | |")
    lines.append("")

    # Blocker code Pareto
    blocker_counts = db.fetch_all("""
        SELECT blocker_code, COUNT(*) as cnt
        FROM bodies
        WHERE blocker_code IS NOT NULL AND blocker_code != ''
        GROUP BY blocker_code
        ORDER BY cnt DESC
    """)

    if blocker_counts:
        lines.append("## Blocker Codes")
        lines.append("")
        lines.append("| Blocker Code | Count |")
        lines.append("|--------------|-------|")
        for row in blocker_counts:
            lines.append(f"| {row['blocker_code']} | {row['cnt']} |")
        lines.append("")

    # Per-body statistics (only bodies with documents)
    body_stats = db.fetch_all("""
        SELECT
            b.body_id,
            b.name,
            COUNT(d.document_id) as total_docs,
            COUNT(CASE WHEN d.in_window = true THEN 1 END) as in_window,
            COUNT(CASE WHEN d.in_window = false THEN 1 END) as out_of_window,
            COUNT(CASE WHEN d.in_window IS NULL THEN 1 END) as no_date
        FROM bodies b
        JOIN documents d ON b.body_id = d.body_id
        GROUP BY b.body_id, b.name
        ORDER BY b.name
    """)

    # Get signal counts per body (in_window only)
    signal_counts = db.fetch_all("""
        SELECT
            d.body_id,
            COUNT(s.signal_id) as signal_count
        FROM signals s
        JOIN documents d ON s.document_id = d.document_id
        WHERE s.is_signal = true AND d.in_window = true
        GROUP BY d.body_id
    """)
    signal_map = {r["body_id"]: r["signal_count"] for r in signal_counts}

    if body_stats:
        lines.append("## Per-Body Document Statistics")
        lines.append("")
        lines.append("| Body | In Window | Out of Window | No Date | Signals (in window) |")
        lines.append("|------|-----------|---------------|---------|---------------------|")

        total_in = total_out = total_no = total_sig = 0
        for row in body_stats:
            in_w = row["in_window"]
            out_w = row["out_of_window"]
            no_d = row["no_date"]
            sigs = signal_map.get(row["body_id"], 0)
            lines.append(f"| {row['name']} | {in_w} | {out_w} | {no_d} | {sigs} |")
            total_in += in_w
            total_out += out_w
            total_no += no_d
            total_sig += sigs

        lines.append(f"| **Total** | **{total_in}** | **{total_out}** | **{total_no}** | **{total_sig}** |")
        lines.append("")

    # Out-of-window summary line
    out_of_window_signals = db.fetch_one("""
        SELECT COUNT(*) as cnt FROM signals s
        JOIN documents d ON s.document_id = d.document_id
        WHERE s.is_signal = true AND d.in_window = false
    """)["cnt"]

    lines.append(f"*Out-of-window totals: {total_out} documents, {out_of_window_signals} signals*")
    lines.append("")

    return "\n".join(lines)


def generate_signals_csv() -> list[dict]:
    """Generate signals.csv data sorted by stage (1 first) then dollar_amount desc."""
    # Stage order mapping (1 first, unknown last)
    stage_order = {
        "1_problem": 1,
        "2_study": 2,
        "3_funding": 3,
        "4_design": 4,
        "5_bid": 5,
        "6_award": 6,
        "7_construction": 7,
        "unknown": 99,
    }

    rows = db.fetch_all("""
        SELECT
            s.signal_id,
            b.name as body,
            b.body_id,
            s.building,
            s.trades,
            s.stage,
            s.stage_verified,
            s.scope_summary,
            s.dollar_amount,
            s.funding_source,
            s.evidence_quote,
            d.source_url,
            d.meeting_date,
            d.in_window
        FROM signals s
        JOIN documents d ON s.document_id = d.document_id
        JOIN bodies b ON d.body_id = b.body_id
        WHERE s.is_signal = true
        ORDER BY d.in_window DESC NULLS LAST
    """)

    # Sort by stage order, then dollar_amount descending
    def sort_key(r):
        stage = r["stage"] or "unknown"
        order = stage_order.get(stage, 50)
        amount = r["dollar_amount"] or 0
        in_window = 0 if r["in_window"] else 1  # in_window first
        return (in_window, order, -amount)

    sorted_rows = sorted(rows, key=sort_key)

    # Format for CSV
    csv_rows = []
    for r in sorted_rows:
        trades = ", ".join(r["trades"]) if r["trades"] else ""
        csv_rows.append({
            "body": r["body"],
            "body_id": r["body_id"],
            "building": r["building"] or "",
            "trades": trades,
            "stage": r["stage"] or "",
            "stage_verified": r["stage_verified"],
            "dollar_amount": r["dollar_amount"] or "",
            "funding_source": r["funding_source"] or "",
            "scope_summary": r["scope_summary"] or "",
            "evidence_quote": r["evidence_quote"] or "",
            "source_url": r["source_url"],
            "meeting_date": r["meeting_date"].isoformat() if r["meeting_date"] else "",
            "in_window": r["in_window"],
        })

    return csv_rows


def generate_calibration_report() -> str:
    """Generate calibration.md report (in_window documents only)."""
    lines = ["# Calibration Report", ""]
    lines.append("*Analysis based on in_window documents only*")
    lines.append("")

    # Get all signals from in_window documents
    signals = db.fetch_all("""
        SELECT
            s.signal_id,
            s.document_id,
            s.passage_index,
            s.scope_summary,
            s.evidence_quote,
            d.body_id,
            d.meeting_date
        FROM signals s
        JOIN documents d ON s.document_id = d.document_id
        WHERE s.is_signal = true AND d.in_window = true
    """)

    # Get all keyword hits from in_window documents
    keyword_hits = db.fetch_all("""
        SELECT
            kh.hit_id,
            kh.document_id,
            kh.passage_index,
            kh.passage_text
        FROM keyword_hits kh
        JOIN documents d ON kh.document_id = d.document_id
        WHERE d.in_window = true
    """)

    # Build lookup: (document_id, passage_index) -> has keyword hit
    hit_lookup = set()
    for h in keyword_hits:
        hit_lookup.add((h["document_id"], h["passage_index"]))

    # Also track all keyword hit (doc_id, passage_index) pairs
    all_hit_passages = set((h["document_id"], h["passage_index"]) for h in keyword_hits)

    # Calculate miss rate: signals without keyword hit in same passage
    signals_with_hit = 0
    signals_without_hit = 0
    missed_signals = []

    for s in signals:
        key = (s["document_id"], s["passage_index"])
        if key in hit_lookup:
            signals_with_hit += 1
        else:
            signals_without_hit += 1
            missed_signals.append(s)

    total_signals = len(signals)
    miss_rate = 100 * signals_without_hit / total_signals if total_signals else 0

    # Calculate false-hit rate: keyword hit passages without LLM signal
    signal_passages = set((s["document_id"], s["passage_index"]) for s in signals)

    hits_with_signal = 0
    hits_without_signal = 0

    for h in keyword_hits:
        key = (h["document_id"], h["passage_index"])
        if key in signal_passages:
            hits_with_signal += 1
        else:
            hits_without_signal += 1

    total_hits = len(keyword_hits)
    false_hit_rate = 100 * hits_without_signal / total_hits if total_hits else 0

    lines.append("## Miss Rate and False-Hit Rate")
    lines.append("")
    lines.append(f"- **Miss rate** (LLM signal with no keyword hit): {miss_rate:.1f}% ({signals_without_hit}/{total_signals})")
    lines.append(f"- **False-hit rate** (keyword hit with no LLM signal): {false_hit_rate:.1f}% ({hits_without_signal}/{total_hits})")
    lines.append("")

    # List missed passages (signals without keyword hits)
    if missed_signals:
        lines.append("## Missed Passages")
        lines.append("")
        lines.append("Signals where no keyword hit existed (taxonomy gaps):")
        lines.append("")

        for s in missed_signals[:20]:  # Limit to first 20
            quote = s["evidence_quote"][:100] + "..." if s["evidence_quote"] and len(s["evidence_quote"]) > 100 else (s["evidence_quote"] or "no quote")
            lines.append(f"- [{s['body_id']}] {s['scope_summary'][:60] if s['scope_summary'] else 'no summary'}...")
            lines.append(f"  Quote: \"{quote}\"")
            lines.append("")

        if len(missed_signals) > 20:
            lines.append(f"*... and {len(missed_signals) - 20} more missed signals*")
            lines.append("")

    # Repeated projects section
    lines.append("## Repeated Projects")
    lines.append("")
    lines.append("Scope summaries appearing 3+ times within one body (preview for projects layer):")
    lines.append("")

    # Find repeated scope_summaries per body
    repeated = db.fetch_all("""
        SELECT
            d.body_id,
            b.name as body_name,
            s.scope_summary,
            COUNT(*) as cnt,
            MIN(d.meeting_date) as first_date,
            MAX(d.meeting_date) as last_date,
            ARRAY_AGG(DISTINCT s.stage) as stages
        FROM signals s
        JOIN documents d ON s.document_id = d.document_id
        JOIN bodies b ON d.body_id = b.body_id
        WHERE s.is_signal = true AND d.in_window = true AND s.scope_summary IS NOT NULL
        GROUP BY d.body_id, b.name, s.scope_summary
        HAVING COUNT(*) >= 3
        ORDER BY cnt DESC, body_name
    """)

    if repeated:
        lines.append("| Body | Scope Summary | Count | First Date | Last Date | Stages |")
        lines.append("|------|---------------|-------|------------|-----------|--------|")

        for r in repeated:
            summary = r["scope_summary"][:50] + "..." if len(r["scope_summary"]) > 50 else r["scope_summary"]
            stages = ", ".join(sorted(set(s for s in r["stages"] if s))) if r["stages"] else ""
            first = r["first_date"].isoformat() if r["first_date"] else ""
            last = r["last_date"].isoformat() if r["last_date"] else ""
            lines.append(f"| {r['body_name']} | {summary} | {r['cnt']} | {first} | {last} | {stages} |")

        lines.append("")
    else:
        lines.append("*No scope summaries appear 3+ times within a single body.*")
        lines.append("")

    return "\n".join(lines)


def generate_coverage_trend_csv() -> list[dict]:
    """Generate coverage_trend.csv showing bodies at each level over time.

    Each row represents a point in time when coverage changed,
    with cumulative counts at each level.
    """
    # Get all coverage log entries ordered by time
    logs = db.fetch_all("""
        SELECT changed_at::date as change_date, body_id, old_level, new_level
        FROM coverage_log
        ORDER BY changed_at
    """)

    # Get current state as starting point
    current_state = db.fetch_all("""
        SELECT coverage_level, COUNT(*) as cnt
        FROM bodies
        GROUP BY coverage_level
    """)

    # Initialize level counts from current state
    level_counts = {0: 0, 1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    for row in current_state:
        level = row["coverage_level"]
        if level is not None:
            level_counts[level] = row["cnt"]

    # Reverse-apply the logs to get historical state
    # Work backwards from current state
    body_current_level = {}
    for log in reversed(logs):
        body_id = log["body_id"]
        old_level = log["old_level"] or 0
        new_level = log["new_level"] or 0

        # Track body's current level in our reconstruction
        if body_id not in body_current_level:
            body_current_level[body_id] = new_level

        # Move from new_level back to old_level
        if new_level in level_counts:
            level_counts[new_level] -= 1
        if old_level not in level_counts:
            level_counts[old_level] = 0
        level_counts[old_level] += 1

    # Now level_counts represents the state before all logs
    # Generate forward timeline
    rows = []

    # Add initial state
    rows.append({
        "date": "initial",
        "level_0": level_counts.get(0, 0),
        "level_1": level_counts.get(1, 0),
        "level_2": level_counts.get(2, 0),
        "level_3": level_counts.get(3, 0),
        "level_4": level_counts.get(4, 0),
        "level_5": level_counts.get(5, 0),
        "level_3_plus": level_counts.get(3, 0) + level_counts.get(4, 0) + level_counts.get(5, 0),
    })

    # Apply logs forward
    for log in logs:
        old_level = log["old_level"] or 0
        new_level = log["new_level"] or 0
        change_date = log["change_date"].isoformat() if log["change_date"] else "unknown"

        # Update counts
        if old_level in level_counts:
            level_counts[old_level] -= 1
        if new_level not in level_counts:
            level_counts[new_level] = 0
        level_counts[new_level] += 1

        rows.append({
            "date": change_date,
            "level_0": level_counts.get(0, 0),
            "level_1": level_counts.get(1, 0),
            "level_2": level_counts.get(2, 0),
            "level_3": level_counts.get(3, 0),
            "level_4": level_counts.get(4, 0),
            "level_5": level_counts.get(5, 0),
            "level_3_plus": level_counts.get(3, 0) + level_counts.get(4, 0) + level_counts.get(5, 0),
        })

    return rows


def generate_projects_csv() -> list[dict]:
    """Generate projects.csv data sorted by latest_stage then last_signal_date."""
    stage_order = {
        "1_needs": 1,
        "2_planning": 2,
        "3_funding": 3,
        "4_design": 4,
        "5_bid": 5,
        "6_award": 6,
        "7_construction": 7,
        "8_closeout": 8,
    }

    rows = db.fetch_all("""
        SELECT
            p.project_id,
            b.name as body,
            b.body_id,
            p.building,
            p.scope_summary,
            p.first_signal_date,
            p.last_signal_date,
            p.latest_stage,
            p.signal_count
        FROM projects p
        JOIN bodies b ON p.body_id = b.body_id
        ORDER BY p.latest_stage, p.last_signal_date DESC NULLS LAST
    """)

    csv_rows = []
    for r in rows:
        csv_rows.append({
            "project_id": r["project_id"],
            "body": r["body"],
            "body_id": r["body_id"],
            "building": r["building"] or "",
            "scope_summary": r["scope_summary"] or "",
            "first_signal_date": r["first_signal_date"].isoformat() if r["first_signal_date"] else "",
            "last_signal_date": r["last_signal_date"].isoformat() if r["last_signal_date"] else "",
            "latest_stage": r["latest_stage"] or "",
            "signal_count": r["signal_count"],
        })

    return csv_rows


def generate_all_reports():
    """Generate all reports to the reports/ directory."""
    REPORTS_DIR.mkdir(exist_ok=True)

    print("Generating coverage.md...")
    coverage_content = generate_coverage_report()
    (REPORTS_DIR / "coverage.md").write_text(coverage_content)

    print("Generating signals.csv...")
    signals_data = generate_signals_csv()
    if signals_data:
        fieldnames = list(signals_data[0].keys())
        with open(REPORTS_DIR / "signals.csv", "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(signals_data)

    print("Generating projects.csv...")
    projects_data = generate_projects_csv()
    if projects_data:
        fieldnames = list(projects_data[0].keys())
        with open(REPORTS_DIR / "projects.csv", "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(projects_data)

    print("Generating coverage_trend.csv...")
    trend_data = generate_coverage_trend_csv()
    if trend_data:
        fieldnames = list(trend_data[0].keys())
        with open(REPORTS_DIR / "coverage_trend.csv", "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(trend_data)

    print("Generating calibration.md...")
    calibration_content = generate_calibration_report()
    (REPORTS_DIR / "calibration.md").write_text(calibration_content)

    # Print summary
    in_window_signals = len([r for r in signals_data if r["in_window"]])
    out_of_window_signals = len([r for r in signals_data if not r["in_window"]])

    print()
    print(f"Reports written to {REPORTS_DIR}/")
    print(f"  coverage.md")
    print(f"  signals.csv ({len(signals_data)} signals: {in_window_signals} in_window, {out_of_window_signals} out_of_window)")
    print(f"  projects.csv ({len(projects_data)} projects)")
    print(f"  coverage_trend.csv ({len(trend_data)} data points)")
    print(f"  calibration.md")
