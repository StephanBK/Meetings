#!/usr/bin/env python3
"""Parallel document classification with rate limiting and resume support."""

import argparse
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

# Add parent directory to path for imports
sys.path.insert(0, str(__file__).rsplit('/', 2)[0])

from meetings import db, config
from meetings.classify import (
    classify_document, load_taxonomy, build_system_prompt,
    print_cost_summary
)
import anthropic


# Configuration
NUM_WORKERS = 4
RATE_LIMIT_BACKOFF_SECONDS = 60
MAX_RETRIES = 3


# Thread-safe counters
lock = threading.Lock()
total_docs = 0
total_signals = 0
total_input_tokens = 0
total_output_tokens = 0
errors = 0


def get_unclassified_documents(in_window_only: bool = False):
    """Get documents that need classification."""
    if in_window_only:
        # Get in_window documents regardless of status
        docs = list(db.fetch_all(
            """SELECT document_id, body_id, text, text_method
               FROM documents
               WHERE in_window = true
               ORDER BY body_id, document_id"""
        ))
    else:
        docs = list(db.fetch_all(
            """SELECT document_id, body_id, text, text_method
               FROM documents
               WHERE status = 'scanned'
               ORDER BY body_id, document_id"""
        ))
    return docs


def classify_with_retry(doc, client, system_prompt, model):
    """Classify a document with retry on rate limit errors."""
    global total_docs, total_signals, total_input_tokens, total_output_tokens, errors

    doc_id = doc['document_id']
    body_id = doc['body_id']
    text = doc['text'] or ''
    text_method = doc['text_method']

    if not text:
        print(f"  [{doc_id}] {body_id}: no text, skipping")
        return

    for attempt in range(MAX_RETRIES):
        try:
            signal_count, input_tokens, output_tokens = classify_document(
                doc_id, text, text_method, client, system_prompt, model
            )

            with lock:
                total_docs += 1
                total_signals += signal_count
                total_input_tokens += input_tokens
                total_output_tokens += output_tokens

            print(f"  [{doc_id}] {body_id}: {signal_count} signals ({input_tokens}+{output_tokens} tokens)")
            return

        except anthropic.RateLimitError as e:
            wait_time = RATE_LIMIT_BACKOFF_SECONDS * (attempt + 1)
            print(f"  [{doc_id}] Rate limited, waiting {wait_time}s (attempt {attempt + 1}/{MAX_RETRIES})")
            time.sleep(wait_time)

        except Exception as e:
            print(f"  [{doc_id}] ERROR: {e}")
            with lock:
                errors += 1

            # Mark document as error
            db.execute(
                "UPDATE documents SET status = 'error', error = %s WHERE document_id = %s",
                (str(e)[:500], doc_id),
                commit=True
            )
            return

    # Max retries exceeded
    print(f"  [{doc_id}] Max retries exceeded")
    with lock:
        errors += 1


def main():
    global total_docs, total_signals, total_input_tokens, total_output_tokens, errors

    parser = argparse.ArgumentParser(description="Parallel document classification")
    parser.add_argument("--in-window", action="store_true",
                        help="Classify only in_window documents (regardless of status)")
    args = parser.parse_args()

    print(f"=== Parallel Classification ({NUM_WORKERS} workers) ===")
    print(f"Started at: {datetime.now().isoformat()}")
    if args.in_window:
        print("Mode: in_window documents only")
    print()

    # Get documents to classify
    docs = get_unclassified_documents(in_window_only=args.in_window)
    print(f"Documents to classify: {len(docs)}")

    if not docs:
        print("No documents to classify.")
        return

    # Initialize shared resources
    model = config.LLM_MODEL
    taxonomy = load_taxonomy()
    system_prompt = build_system_prompt(taxonomy)

    # Create client with workspace header if needed
    client_kwargs = {"api_key": config.ANTHROPIC_API_KEY}
    if config.ANTHROPIC_WORKSPACE_ID:
        client_kwargs["default_headers"] = {
            "anthropic-workspace-id": config.ANTHROPIC_WORKSPACE_ID
        }

    # Each worker gets its own client to avoid connection issues
    def create_client():
        return anthropic.Anthropic(**client_kwargs)

    print(f"Model: {model}")
    print()

    # Process with thread pool
    start_time = time.time()

    with ThreadPoolExecutor(max_workers=NUM_WORKERS) as executor:
        futures = []
        for doc in docs:
            client = create_client()
            future = executor.submit(
                classify_with_retry, doc, client, system_prompt, model
            )
            futures.append(future)

        # Wait for all to complete
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                print(f"  Worker error: {e}")

    elapsed = time.time() - start_time

    print()
    print(f"=== Classification Complete ===")
    print(f"Finished at: {datetime.now().isoformat()}")
    print(f"Elapsed: {elapsed:.1f}s")
    print()
    print(f"Documents classified: {total_docs}")
    print(f"Total signals: {total_signals}")
    print(f"Errors: {errors}")
    print()
    print_cost_summary(total_input_tokens, total_output_tokens, model)


if __name__ == "__main__":
    main()
