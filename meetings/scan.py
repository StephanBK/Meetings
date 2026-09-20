"""Keyword scan for construction/procurement signals in meeting documents.

A 'passage' is a block of text between blank lines (roughly one agenda item or resolution).
Vocabulary comes only from config/taxonomy.yaml.
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


def split_passages(text: str) -> list[str]:
    """Split text into passages (blocks separated by blank lines)."""
    passages = []
    for p in re.split(r"\n\s*\n", text):
        # Normalize whitespace
        p = re.sub(r"\s+", " ", p).strip()
        # Only include passages with meaningful content
        if len(p) > 25:
            passages.append(p)
    return passages


def scan_text(text: str) -> list[dict]:
    """
    Scan text for keyword hits.

    Returns list of hit dicts with:
        - text: the passage text
        - trades: {trade_key: [matched_keywords]}
        - stages: {stage_key: [matched_keywords]}
        - triggers: [matched_keywords]
    """
    trade_matchers, stage_matchers, trigger_matchers, negative_matchers, skip_matchers = build_matchers()

    hits = []
    for passage in split_passages(text):
        # Skip if passage matches any skip pattern (personnel lines, etc.)
        if any(skip.search(passage) for skip in skip_matchers):
            continue

        # Remove negative phrases before matching
        clean_passage = passage
        for neg in negative_matchers:
            clean_passage = neg.sub(" ", clean_passage)

        # Find trade matches
        trades = {}
        for trade_key, matchers in trade_matchers.items():
            matched = [kw for kw, rx in matchers if rx.search(clean_passage)]
            if matched:
                trades[trade_key] = matched

        # Find stage matches
        stages = {}
        for stage_key, matchers in stage_matchers.items():
            matched = [kw for kw, rx in matchers if rx.search(clean_passage)]
            if matched:
                stages[stage_key] = matched

        # Find trigger matches
        triggers = [kw for kw, rx in trigger_matchers if rx.search(clean_passage)]

        # A hit requires at least one trade OR one trigger
        if trades or triggers:
            hits.append({
                "text": passage,
                "trades": trades,
                "stages": stages,
                "triggers": triggers,
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
