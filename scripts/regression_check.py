#!/usr/bin/env python3
"""Regression check: confirm 9 known signals are present in in_window signals."""

import sys

# Add parent directory to path
sys.path.insert(0, str(__file__).rsplit('/', 2)[0])

from meetings import db


# Known signals to check for
KNOWN_SIGNALS = [
    # Commack signals
    {"body": "Commack", "keyword": "NextStep", "description": "Commack: NextStep"},
    {"body": "Commack", "keyword": "EXCEL", "description": "Commack: EXCEL"},
    {"body": "Commack", "keyword": "Mock Trial", "description": "Commack: Mock Trial"},
    {"body": "Commack", "keyword": "heating and cooling", "description": "Commack: piped heating and cooling"},
    {"body": "Commack", "keyword": "Capital Renovation", "description": "Commack: Capital Renovation Corp"},
    # Deer Park signals
    {"body": "Deer Park", "keyword": "roof", "description": "Deer Park: roof"},
    # Summer Projects broken into: concession, electrical, security (doc 29)
    {"body": "Deer Park", "keyword": "concession", "alt_keywords": ["electric", "security command"], "description": "Deer Park: Summer Projects"},
    # West Islip signals (pool construction appears twice)
    {"body": "West Islip", "keyword": "pool construction", "description": "West Islip: pool construction (1)"},
    {"body": "West Islip", "keyword": "pool construction", "count": 2, "description": "West Islip: pool construction (2)"},
]


def main():
    print("=== Regression Check: 9 Known Signals ===\n")

    # Get all in_window signals with their scope summaries and evidence quotes
    signals = list(db.fetch_all("""
        SELECT s.signal_id, s.document_id, s.scope_summary, s.evidence_quote, b.name as body_name
        FROM signals s
        JOIN documents d ON s.document_id = d.document_id
        JOIN bodies b ON d.body_id = b.body_id
        WHERE d.in_window = true AND s.is_signal = true
    """))

    print(f"Total in_window signals: {len(signals)}\n")

    found = []
    missing = []

    # Check each known signal
    pool_count = 0

    for known in KNOWN_SIGNALS:
        keyword = known["keyword"].lower()
        body_pattern = known["body"].lower()
        alt_keywords = [k.lower() for k in known.get("alt_keywords", [])]

        # Find matching signals
        def matches_keywords(scope, quote):
            scope = (scope or "").lower()
            quote = (quote or "").lower()
            if keyword in scope or keyword in quote:
                return True
            for alt in alt_keywords:
                if alt in scope or alt in quote:
                    return True
            return False

        matches = [
            s for s in signals
            if body_pattern in s["body_name"].lower() and
               matches_keywords(s["scope_summary"], s["evidence_quote"])
        ]

        if known.get("count"):
            # For "pool construction (2)" - need to find at least 2 total
            if known["keyword"].lower() == "pool construction":
                pool_count = len(matches)
                if pool_count >= 2:
                    found.append({
                        "description": known["description"],
                        "signal_id": f"{pool_count} occurrences",
                        "document_id": ", ".join(str(m["document_id"]) for m in matches)
                    })
                else:
                    missing.append({
                        "description": known["description"],
                        "note": f"Only {pool_count} found, need 2"
                    })
        else:
            if matches:
                found.append({
                    "description": known["description"],
                    "signal_id": matches[0]["signal_id"],
                    "document_id": matches[0]["document_id"],
                    "scope": matches[0]["scope_summary"][:60] if matches[0]["scope_summary"] else ""
                })
            else:
                # Check if it exists in any signal
                all_matches = [
                    s for s in signals
                    if keyword in (s["scope_summary"] or "").lower() or
                       keyword in (s["evidence_quote"] or "").lower()
                ]
                missing.append({
                    "description": known["description"],
                    "note": f"Not found for {known['body']}" + (f" (but found {len(all_matches)} elsewhere)" if all_matches else "")
                })

    # Special handling: check pool construction count properly
    # We already checked pool construction in the loop above

    print("=== Found Signals ===")
    for f in found:
        print(f"  [OK] {f['description']}")
        print(f"       signal_id={f.get('signal_id')}, document_id={f.get('document_id')}")
        if f.get("scope"):
            print(f"       scope: {f['scope']}...")

    if missing:
        print("\n=== Missing Signals ===")
        for m in missing:
            print(f"  [MISSING] {m['description']}")
            print(f"            {m['note']}")

    print(f"\n=== Summary ===")
    print(f"Found: {len(found)}/9")
    print(f"Missing: {len(missing)}")

    if not missing:
        print("\nResult: PASS - All 9 known signals present")
        return 0
    else:
        print(f"\nResult: FAIL - {len(missing)} signals missing")
        return 1


if __name__ == "__main__":
    sys.exit(main())
