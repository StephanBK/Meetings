-- Meetings pipeline schema
-- Run with: python -m meetings initdb

-- bodies: one row per public body (school district, city council, etc.)
CREATE TABLE IF NOT EXISTS bodies (
    body_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    segment TEXT,
    county TEXT,
    website TEXT,
    board_page_url TEXT,
    platform TEXT,
    coverage_level INTEGER DEFAULT 0,
    blocker_code TEXT,
    enrollment INTEGER,
    expected_meetings_per_year INTEGER,
    own_site_doc_links INTEGER,
    last_checked TIMESTAMP,
    last_success TIMESTAMP,
    manual_notes TEXT
);

-- meetings: one row per meeting event
CREATE TABLE IF NOT EXISTS meetings (
    meeting_id SERIAL PRIMARY KEY,
    body_id TEXT NOT NULL REFERENCES bodies(body_id),
    meeting_date DATE NOT NULL,
    meeting_type TEXT,
    title TEXT,
    UNIQUE (body_id, meeting_date, meeting_type)
);

-- documents: agenda, minutes, packet files
CREATE TABLE IF NOT EXISTS documents (
    document_id SERIAL PRIMARY KEY,
    body_id TEXT NOT NULL REFERENCES bodies(body_id),
    meeting_id INTEGER REFERENCES meetings(meeting_id),
    doc_type TEXT CHECK (doc_type IN ('agenda', 'minutes', 'packet', 'committee_agenda', 'other')),
    source_url TEXT UNIQUE NOT NULL,
    link_text TEXT,
    meeting_date DATE,
    in_window BOOLEAN,
    file_path TEXT,
    sha256 TEXT,
    bytes INTEGER,
    pages INTEGER,
    text TEXT,
    text_method TEXT CHECK (text_method IN ('native', 'ocr')),
    fetched_at TIMESTAMP,
    status TEXT DEFAULT 'new' CHECK (status IN ('new', 'fetched', 'extracted', 'scanned', 'classified', 'error')),
    error TEXT
);

-- keyword_hits: passages that match taxonomy keywords
CREATE TABLE IF NOT EXISTS keyword_hits (
    hit_id SERIAL PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES documents(document_id),
    passage_index INTEGER NOT NULL,
    passage_text TEXT NOT NULL,
    trades TEXT[],
    stages TEXT[],
    triggers TEXT[]
);

-- signals: LLM-classified construction/procurement signals
CREATE TABLE IF NOT EXISTS signals (
    signal_id SERIAL PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES documents(document_id),
    passage_index INTEGER,
    is_signal BOOLEAN NOT NULL,
    trades TEXT[],
    stage TEXT,
    stage_evidence TEXT,
    stage_verified BOOLEAN DEFAULT FALSE,
    low_value BOOLEAN DEFAULT FALSE,
    building TEXT,
    scope_summary TEXT,
    dollar_amount NUMERIC,
    amount_from_ocr BOOLEAN,
    funding_source TEXT,
    key_dates JSONB,
    vendors_named JSONB,
    evidence_quote TEXT,
    page_or_item TEXT,
    confidence NUMERIC,
    model TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

-- llm_runs: token usage tracking
CREATE TABLE IF NOT EXISTS llm_runs (
    run_id SERIAL PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES documents(document_id),
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

-- coverage_log: audit trail for coverage level changes
CREATE TABLE IF NOT EXISTS coverage_log (
    id SERIAL PRIMARY KEY,
    body_id TEXT NOT NULL REFERENCES bodies(body_id),
    old_level INTEGER,
    new_level INTEGER,
    blocker_code TEXT,
    note TEXT,
    changed_at TIMESTAMP DEFAULT NOW()
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_documents_body_id ON documents(body_id);
CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status);
CREATE INDEX IF NOT EXISTS idx_meetings_body_id ON meetings(body_id);
CREATE INDEX IF NOT EXISTS idx_meetings_date ON meetings(meeting_date);
CREATE INDEX IF NOT EXISTS idx_keyword_hits_document_id ON keyword_hits(document_id);
CREATE INDEX IF NOT EXISTS idx_signals_document_id ON signals(document_id);
CREATE INDEX IF NOT EXISTS idx_coverage_log_body_id ON coverage_log(body_id);
