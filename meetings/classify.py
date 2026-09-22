"""LLM classifier for construction/procurement signals in meeting documents."""

import json
import re
from datetime import datetime
from typing import Optional

import anthropic
import yaml

from . import config, db


# Target chunk size in characters
CHUNK_SIZE = 12000


def load_taxonomy() -> dict:
    """Load taxonomy from config/taxonomy.yaml."""
    taxonomy_path = config.CONFIG_DIR / "taxonomy.yaml"
    with open(taxonomy_path) as f:
        return yaml.safe_load(f)


def build_system_prompt(taxonomy: dict) -> str:
    """Build system prompt from taxonomy."""
    # Build trades section
    trades_text = "TRADES (what kind of work):\n"
    for key, data in taxonomy["trades"].items():
        trades_text += f"  {key}: {data['label']}\n"

    # Build stages section
    stages_text = "STAGES (how early in the process):\n"
    for key, data in taxonomy["stages"].items():
        stages_text += f"  {key}: {data['label']} - {data['value']}\n"

    # Build output schema
    schema = taxonomy["output_schema"]
    schema_text = "OUTPUT SCHEMA (return a JSON array of objects, one per distinct project or purchase):\n"
    for field, desc in schema.items():
        schema_text += f"  {field}: {desc}\n"

    prompt = f"""You are an expert at identifying construction, facilities, and procurement signals in school board meeting documents.

{trades_text}
{stages_text}
{schema_text}

RULES:
1. Return a JSON array of signal objects. If no signals, return an empty array [].
2. Each object represents ONE distinct project or purchase. Do not combine different projects.
3. Personnel appointments, tuition payments, curriculum adoptions, and policy items are NOT signals. Skip them entirely.
4. Hiring, appointing, or paying district staff is NEVER a signal, even when the role relates to a construction project (e.g., security guards, inspectors, project managers).
5. The evidence_quote MUST be copied exactly from the text (under 25 words). It must appear verbatim in the document.
6. For dollar_amount, extract only the numeric value (no $ or commas). Leave null if not stated.
7. For trades, use the exact keys from the list above.
8. For stage, choose the most advanced stage for which THIS project has explicit evidence in the text. Words about other agenda items do not count. If the text contains no stage keywords (from the STAGES list above) for this specific project, you MUST use stage "unknown". Valid values: 1_problem, 2_study, 3_funding, 4_design, 5_bid, 6_award, 7_construction, or "unknown".
9. REQUIRED: For stage_evidence, copy the exact words from the text (under 15 words) that justify the stage you chose. Must appear verbatim in the document. If you cannot find explicit stage keywords for this project, set stage to "unknown" and leave stage_evidence empty. A stage other than "unknown" REQUIRES non-empty stage_evidence.
10. confidence should be 0.0 to 1.0 based on how clearly this is a real construction/facilities signal.

Return ONLY valid JSON, no other text."""

    return prompt


def split_into_chunks(text: str, chunk_size: int = CHUNK_SIZE) -> list[str]:
    """Split text into chunks of approximately chunk_size, breaking at passage boundaries."""
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    # Split on double newlines (passage boundaries)
    passages = re.split(r'\n\s*\n', text)

    current_chunk = ""
    for passage in passages:
        passage = passage.strip()
        if not passage:
            continue

        # If adding this passage would exceed chunk size, start a new chunk
        if len(current_chunk) + len(passage) + 2 > chunk_size and current_chunk:
            chunks.append(current_chunk)
            current_chunk = passage
        else:
            if current_chunk:
                current_chunk += "\n\n" + passage
            else:
                current_chunk = passage

    if current_chunk:
        chunks.append(current_chunk)

    return chunks


def extract_json_array(text: str) -> Optional[list]:
    """Extract JSON array from response text."""
    # Try to find JSON array in the response
    text = text.strip()

    # If it starts with [, try to parse directly
    if text.startswith('['):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

    # Try to find array in the text
    match = re.search(r'\[[\s\S]*\]', text)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    return None


def validate_evidence_quote(quote: str, text: str, min_word_match: float = 0.8) -> bool:
    """
    Check if quote appears in text using flexible matching.

    Compares after lowercasing and collapsing whitespace.
    Accepts a match of at least min_word_match (80%) of the words in order.

    Args:
        quote: The quote to validate
        text: The source text to check against
        min_word_match: Minimum fraction of words that must match in order (default 0.8)

    Returns:
        True if quote matches sufficiently, False otherwise
    """
    if not quote:
        return True  # No quote to validate

    # Normalize whitespace and lowercase
    quote_normalized = ' '.join(quote.lower().split())
    text_normalized = ' '.join(text.lower().split())

    # First try exact substring match
    if quote_normalized in text_normalized:
        return True

    # Fall back to word-level matching (80% of words in order)
    quote_words = quote_normalized.split()
    text_words = text_normalized.split()

    if not quote_words:
        return True

    # Find longest common subsequence of words
    matched_count = 0
    text_idx = 0

    for quote_word in quote_words:
        # Look for this word in remaining text
        while text_idx < len(text_words):
            if text_words[text_idx] == quote_word:
                matched_count += 1
                text_idx += 1
                break
            text_idx += 1
        else:
            # Word not found in remaining text
            break

    match_ratio = matched_count / len(quote_words)
    return match_ratio >= min_word_match


def classify_chunk(
    client: anthropic.Anthropic,
    system_prompt: str,
    chunk: str,
    model: str
) -> tuple[list[dict], int, int]:
    """
    Classify a single chunk of text.

    Returns (signals, input_tokens, output_tokens).
    """
    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=system_prompt,
        messages=[
            {"role": "user", "content": f"Extract construction and facilities signals from this document:\n\n{chunk}"}
        ]
    )

    input_tokens = response.usage.input_tokens
    output_tokens = response.usage.output_tokens

    # Parse response
    response_text = response.content[0].text
    signals = extract_json_array(response_text)

    if signals is None:
        print(f"    WARNING: Could not parse JSON from response")
        return [], input_tokens, output_tokens

    return signals, input_tokens, output_tokens


def classify_document(
    document_id: int,
    text: str,
    text_method: str,
    client: anthropic.Anthropic,
    system_prompt: str,
    model: str
) -> tuple[int, int, int]:
    """
    Classify a document and write signals to database.

    Returns (signal_count, total_input_tokens, total_output_tokens).
    """
    chunks = split_into_chunks(text)

    total_input = 0
    total_output = 0
    signal_count = 0

    for i, chunk in enumerate(chunks):
        signals, input_tokens, output_tokens = classify_chunk(
            client, system_prompt, chunk, model
        )
        total_input += input_tokens
        total_output += output_tokens

        for signal in signals:
            # Skip if is_signal is explicitly false
            if signal.get("is_signal") is False:
                continue

            # Validate evidence_quote and stage_evidence
            quote = signal.get("evidence_quote", "")
            stage_evidence = signal.get("stage_evidence", "")
            quote_valid = not quote or validate_evidence_quote(quote, chunk)
            stage_evidence_valid = not stage_evidence or validate_evidence_quote(stage_evidence, chunk)

            if not quote_valid or not stage_evidence_valid:
                # Retry once with explicit instruction
                print(f"    Quote not found, retrying...")
                retry_prompt = f"""The evidence_quote or stage_evidence you provided was not found in the text. Please re-extract signals and ensure both evidence_quote and stage_evidence are copied EXACTLY from the text below:

{chunk}"""
                retry_response = client.messages.create(
                    model=model,
                    max_tokens=4096,
                    system=system_prompt,
                    messages=[
                        {"role": "user", "content": retry_prompt}
                    ]
                )
                total_input += retry_response.usage.input_tokens
                total_output += retry_response.usage.output_tokens

                retry_signals = extract_json_array(retry_response.content[0].text)
                if retry_signals:
                    # Find the corresponding signal in retry
                    for retry_signal in retry_signals:
                        if retry_signal.get("scope_summary") == signal.get("scope_summary"):
                            signal = retry_signal
                            stage_evidence = signal.get("stage_evidence", "")
                            break

            # Clear stage_evidence if stage is unknown
            stage = signal.get("stage")
            if stage == "unknown":
                stage_evidence = ""

            # Determine stage_verified: true if stage is not unknown, stage_evidence is non-empty and validated
            stage_verified = (
                stage is not None and
                stage != "unknown" and
                bool(stage_evidence) and
                validate_evidence_quote(stage_evidence, chunk)
            )

            # Determine amount_from_ocr
            amount_from_ocr = False
            if text_method == "ocr" and signal.get("dollar_amount"):
                amount_from_ocr = True

            # Parse dollar amount
            dollar_amount = signal.get("dollar_amount")
            if dollar_amount:
                if isinstance(dollar_amount, str):
                    # Remove $ and commas
                    dollar_amount = dollar_amount.replace("$", "").replace(",", "")
                    try:
                        dollar_amount = float(dollar_amount)
                    except ValueError:
                        dollar_amount = None

            # Insert signal
            db.execute(
                """INSERT INTO signals
                   (document_id, passage_index, is_signal, trades, stage, stage_evidence,
                    stage_verified, building, scope_summary, dollar_amount, amount_from_ocr,
                    funding_source, key_dates, vendors_named, evidence_quote, page_or_item,
                    confidence, model, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    document_id,
                    i,  # passage_index is chunk index
                    signal.get("is_signal", True),
                    signal.get("trades", []),
                    stage,
                    stage_evidence,
                    stage_verified,
                    signal.get("building"),
                    signal.get("scope_summary"),
                    dollar_amount,
                    amount_from_ocr,
                    signal.get("funding_source"),
                    json.dumps(signal.get("key_dates")) if signal.get("key_dates") else None,
                    json.dumps(signal.get("vendors_named")) if signal.get("vendors_named") else None,
                    signal.get("evidence_quote"),
                    signal.get("page_or_item"),
                    signal.get("confidence"),
                    model,
                    datetime.now()
                ),
                commit=True
            )
            signal_count += 1

    # Log LLM run
    db.execute(
        """INSERT INTO llm_runs (document_id, model, input_tokens, output_tokens, created_at)
           VALUES (%s, %s, %s, %s, %s)""",
        (document_id, model, total_input, total_output, datetime.now()),
        commit=True
    )

    # Update document status
    db.execute(
        "UPDATE documents SET status = 'classified' WHERE document_id = %s",
        (document_id,),
        commit=True
    )

    return signal_count, total_input, total_output


def classify_documents(document_ids: Optional[list[int]] = None) -> tuple[int, int, int, int]:
    """
    Classify documents.

    If document_ids is None, classify all extracted documents.
    If document_ids is provided, classify only those documents.

    Returns (documents_classified, total_signals, total_input_tokens, total_output_tokens).
    """
    # Initialize client
    client_kwargs = {"api_key": config.ANTHROPIC_API_KEY}
    if config.ANTHROPIC_WORKSPACE_ID:
        client_kwargs["default_headers"] = {
            "anthropic-workspace-id": config.ANTHROPIC_WORKSPACE_ID
        }
    client = anthropic.Anthropic(**client_kwargs)
    model = config.LLM_MODEL

    # Load taxonomy and build prompt
    taxonomy = load_taxonomy()
    system_prompt = build_system_prompt(taxonomy)

    # Get documents to classify
    if document_ids:
        placeholders = ','.join(['%s'] * len(document_ids))
        docs = list(db.fetch_all(
            f"""SELECT document_id, body_id, text, text_method
               FROM documents
               WHERE document_id IN ({placeholders})
               ORDER BY document_id""",
            tuple(document_ids)
        ))
    else:
        docs = list(db.fetch_all(
            """SELECT document_id, body_id, text, text_method
               FROM documents
               WHERE status = 'scanned'
               ORDER BY body_id, document_id"""
        ))

    if not docs:
        print("No documents to classify.")
        return 0, 0, 0, 0

    print(f"Classifying {len(docs)} documents with {model}...")

    total_docs = 0
    total_signals = 0
    total_input = 0
    total_output = 0
    current_body = None

    for doc in docs:
        if doc["body_id"] != current_body:
            current_body = doc["body_id"]
            print(f"\n  {current_body}:")

        text = doc["text"] or ""
        if not text:
            print(f"    {doc['document_id']}: no text, skipping")
            continue

        signal_count, input_tokens, output_tokens = classify_document(
            doc["document_id"],
            text,
            doc["text_method"],
            client,
            system_prompt,
            model
        )

        total_docs += 1
        total_signals += signal_count
        total_input += input_tokens
        total_output += output_tokens

        print(f"    {doc['document_id']}: {signal_count} signals ({input_tokens} in, {output_tokens} out)")

    return total_docs, total_signals, total_input, total_output


def print_cost_summary(input_tokens: int, output_tokens: int, model: str):
    """Print token counts and estimated cost using prices from config."""
    pricing = config.LLM_PRICING.get(model, {"input": 1.00, "output": 5.00})
    input_cost = input_tokens * pricing["input"] / 1_000_000
    output_cost = output_tokens * pricing["output"] / 1_000_000
    total_cost = input_cost + output_cost

    print(f"\n--- Token Summary ---")
    print(f"Input tokens:  {input_tokens:,}")
    print(f"Output tokens: {output_tokens:,}")
    print(f"Total tokens:  {input_tokens + output_tokens:,}")
    print(f"Estimated cost: ${total_cost:.4f} (${pricing['input']}/M in, ${pricing['output']}/M out)")
