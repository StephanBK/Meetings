"""Keyword scan for construction/procurement signals in meeting documents.

A 'passage' is a matching line plus 2 lines before and 3 lines after, merged when windows overlap.
Skip patterns apply per line. Vocabulary comes only from config/taxonomy.yaml.
"""

import re
from pathlib import Path
from typing import Optional

import yaml

from . import config, db


# Load taxonomy once at module import
_taxonomy: Optional[dict] = None


def get_taxonomy() -> dict:
    """Load and cache taxonomy from config/taxonomy.yaml."""
    global _taxonomy
    if _taxonomy is None:
        taxonomy_path = config.CONFIG_DIR / "taxonomy.yaml"
        with open(taxonomy_path) as f:
            _taxonomy = yaml.safe_load(f)
    return _taxonomy


def make_keyword_regex(keyword: str) -> re.Pattern:
    """
    Create regex for a keyword that matches:
    - Case insensitive
    - Whole words only
    - Optional plural forms (s, es, ing, ed)
    - Spaces can be replaced by whitespace or hyphens
    """
    # Escape special chars and allow flexible whitespace/hyphen
    pattern = re.escape(keyword.lower()).replace(r"\ ", r"[\s\-]+")
    # Word boundary: not preceded or followed by a letter
    # Allow optional suffixes: s, es, ing, ed
    return re.compile(r"(?<![a-z])" + pattern + r"(?:s|es|ing|ed)?(?![a-z])", re.IGNORECASE)


def build_matchers():
    """Build compiled regex matchers from taxonomy."""
    taxonomy = get_taxonomy()

    # Trade matchers: {trade_key: [(keyword, regex), ...]}
    trade_matchers = {}
    for trade_key, trade_data in taxonomy["trades"].items():
        trade_matchers[trade_key] = [
            (kw, make_keyword_regex(kw)) for kw in trade_data["keywords"]
        ]

    # Stage matchers: {stage_key: [(keyword, regex), ...]}
    stage_matchers = {}
    for stage_key, stage_data in taxonomy["stages"].items():
        stage_matchers[stage_key] = [
            (kw, make_keyword_regex(kw)) for kw in stage_data["keywords"]
        ]

    # Trigger matchers: [(keyword, regex), ...]
    trigger_matchers = [
        (kw, make_keyword_regex(kw)) for kw in taxonomy["triggers"]
    ]

    # Negative phrase matchers (simple substring, case insensitive)
    negative_matchers = [
        re.compile(re.escape(neg), re.IGNORECASE) for neg in taxonomy["negatives"]
    ]

    # Skip patterns (regex patterns for personnel lines etc.)
    skip_matchers = [
        re.compile(pattern, re.IGNORECASE)
        for pattern in taxonomy.get("skip_patterns", [])
    ]

    return trade_matchers, stage_matchers, trigger_matchers, negative_matchers, skip_matchers


def merge_windows(windows: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Merge overlapping or adjacent windows."""
    if not windows:
        return []
    # Sort by start index
    sorted_windows = sorted(windows)
    merged = [sorted_windows[0]]
    for start, end in sorted_windows[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end + 1:  # Overlapping or adjacent
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def scan_text(text: str) -> list[dict]:
    """
    Scan text for keyword hits using line-based matching.

    A hit passage is the matching line plus 2 lines before and 3 after, merged when windows overlap.
    Skip patterns apply per line, not per passage.

    Returns list of hit dicts with:
        - text: the passage text
        - trades: {trade_key: [matched_keywords]}
        - stages: {stage_key: [matched_keywords]}
        - triggers: [matched_keywords]
    """
    trade_matchers, stage_matchers, trigger_matchers, negative_matchers, skip_matchers = build_matchers()

    # Split text into lines
    lines = text.split('\n')

    # Find all matching line indices (excluding skip pattern lines)
    hit_lines = []  # List of (line_index, trades, stages, triggers)

    for i, line in enumerate(lines):
        # Skip if line matches any skip pattern (personnel lines, etc.)
        if any(skip.search(line) for skip in skip_matchers):
            continue

        # Remove negative phrases before matching
        clean_line = line
        for neg in negative_matchers:
            clean_line = neg.sub(" ", clean_line)

        # Find trade matches
        trades = {}
        for trade_key, matchers in trade_matchers.items():
            matched = [kw for kw, rx in matchers if rx.search(clean_line)]
            if matched:
                trades[trade_key] = matched

        # Find stage matches
        stages = {}
        for stage_key, matchers in stage_matchers.items():
            matched = [kw for kw, rx in matchers if rx.search(clean_line)]
            if matched:
                stages[stage_key] = matched

        # Find trigger matches
        triggers = [kw for kw, rx in trigger_matchers if rx.search(clean_line)]

        # A hit requires at least one trade OR one trigger
        if trades or triggers:
            hit_lines.append((i, trades, stages, triggers))

    if not hit_lines:
        return []

    # Build windows: 2 lines before, 3 lines after for each hit line
    windows = []
    for line_idx, _, _, _ in hit_lines:
        start = max(0, line_idx - 2)
        end = min(len(lines) - 1, line_idx + 3)
        windows.append((start, end))

    # Merge overlapping windows
    merged_windows = merge_windows(windows)

    # Create passages from merged windows, aggregating keywords from all hit lines in each window
    hits = []
    hit_line_set = {idx: (trades, stages, triggers) for idx, trades, stages, triggers in hit_lines}

    for start, end in merged_windows:
        # Extract passage text
        passage_lines = lines[start:end + 1]
        passage_text = ' '.join(line.strip() for line in passage_lines if line.strip())

        # Only include passages with meaningful content
        if len(passage_text) < 25:
            continue

        # Aggregate trades, stages, triggers from all hit lines in this window
        all_trades = {}
        all_stages = {}
        all_triggers = []

        for line_idx in range(start, end + 1):
            if line_idx in hit_line_set:
                trades, stages, triggers = hit_line_set[line_idx]
                for trade_key, keywords in trades.items():
                    if trade_key not in all_trades:
                        all_trades[trade_key] = []
                    all_trades[trade_key].extend(kw for kw in keywords if kw not in all_trades[trade_key])
                for stage_key, keywords in stages.items():
                    if stage_key not in all_stages:
                        all_stages[stage_key] = []
                    all_stages[stage_key].extend(kw for kw in keywords if kw not in all_stages[stage_key])
                all_triggers.extend(t for t in triggers if t not in all_triggers)

        hits.append({
            "text": passage_text,
            "trades": all_trades,
            "stages": all_stages,
            "triggers": all_triggers,
        })

    return hits


def scan_document(document_id: int, text: str) -> int:
    """
    Scan a document and write keyword_hits to database.

    Returns number of hits found.
    """
    hits = scan_text(text)

    for i, hit in enumerate(hits):
        # Convert dicts to lists for database storage
        trade_keys = list(hit["trades"].keys())
        stage_keys = list(hit["stages"].keys())
        trigger_list = hit["triggers"]

        db.execute(
            """INSERT INTO keyword_hits
               (document_id, passage_index, passage_text, trades, stages, triggers)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (document_id, i, hit["text"], trade_keys, stage_keys, trigger_list),
            commit=True
        )

    # Update document status
    db.execute(
        "UPDATE documents SET status = 'scanned' WHERE document_id = %s",
        (document_id,),
        commit=True
    )

    return len(hits)


def scan_extracted_documents() -> tuple[int, int]:
    """
    Scan all documents with status 'extracted'.

    Returns (documents_scanned, total_hits).
    """
    docs = list(db.fetch_all(
        """SELECT document_id, body_id, text
           FROM documents
           WHERE status = 'extracted'
           ORDER BY body_id, document_id"""
    ))

    if not docs:
        print("No extracted documents to scan.")
        return 0, 0

    print(f"Scanning {len(docs)} documents...")

    total_docs = 0
    total_hits = 0
    current_body = None

    for doc in docs:
        if doc["body_id"] != current_body:
            current_body = doc["body_id"]
            print(f"\n  {current_body}:")

        hit_count = scan_document(doc["document_id"], doc["text"] or "")
        total_docs += 1
        total_hits += hit_count

        if hit_count > 0:
            print(f"    {doc['document_id']}: {hit_count} hits")

    return total_docs, total_hits
