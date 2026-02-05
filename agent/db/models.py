"""SQL table definitions for the metadata capture system."""

METADATA_RECORDS_TABLE = """
CREATE TABLE IF NOT EXISTS metadata_records (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    record_type TEXT NOT NULL
        CHECK (record_type IN (
            'subject', 'procedures', 'instrument', 'rig',
            'data_description', 'acquisition', 'session',
            'processing', 'quality_control'
        )),
    category TEXT NOT NULL
        CHECK (category IN ('shared', 'asset')),
    name TEXT,
    data_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'validated', 'confirmed', 'error')),
    validation_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

RECORD_LINKS_TABLE = """
CREATE TABLE IF NOT EXISTS record_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL REFERENCES metadata_records(id) ON DELETE CASCADE,
    target_id TEXT NOT NULL REFERENCES metadata_records(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(source_id, target_id)
);
"""

CONVERSATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

CREATE_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_records_session ON metadata_records(session_id);
CREATE INDEX IF NOT EXISTS idx_records_type ON metadata_records(record_type);
CREATE INDEX IF NOT EXISTS idx_records_category ON metadata_records(category);
CREATE INDEX IF NOT EXISTS idx_records_status ON metadata_records(status);
CREATE INDEX IF NOT EXISTS idx_links_source ON record_links(source_id);
CREATE INDEX IF NOT EXISTS idx_links_target ON record_links(target_id);
CREATE INDEX IF NOT EXISTS idx_conv_session ON conversations(session_id);
"""

ALL_TABLES = [METADATA_RECORDS_TABLE, RECORD_LINKS_TABLE, CONVERSATIONS_TABLE, CREATE_INDEXES]

# Category mapping: record_type -> category
CATEGORY_MAP = {
    "subject": "shared",
    "procedures": "shared",
    "instrument": "shared",
    "rig": "shared",
    "data_description": "asset",
    "acquisition": "asset",
    "session": "asset",
    "processing": "asset",
    "quality_control": "asset",
}

VALID_RECORD_TYPES = frozenset(CATEGORY_MAP.keys())
