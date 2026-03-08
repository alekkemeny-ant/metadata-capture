"""SQL table definitions for the metadata capture system.

Provides both SQLite and PostgreSQL DDL variants.
"""

# ---------------------------------------------------------------------------
# SQLite DDL
# ---------------------------------------------------------------------------

SQLITE_TABLES = [
    """
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
""",
    """
CREATE TABLE IF NOT EXISTS record_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL REFERENCES metadata_records(id) ON DELETE CASCADE,
    target_id TEXT NOT NULL REFERENCES metadata_records(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(source_id, target_id)
);
""",
    """
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    attachments_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
""",
    """
CREATE TABLE IF NOT EXISTS uploads (
    id TEXT PRIMARY KEY,
    original_filename TEXT NOT NULL,
    content_type TEXT NOT NULL,
    file_path TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    session_id TEXT,
    extracted_text TEXT,
    extracted_images_json TEXT,
    extracted_meta_json TEXT,
    extraction_status TEXT DEFAULT 'pending',
    extraction_error TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
""",
]

# Artifacts — agent-generated spreadsheets/tables/code shown in ArtifactModal.
# Appended as the 5th entry in both SQLITE_TABLES and PG_TABLES below.
SQLITE_TABLES.append("""
CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    artifact_type TEXT NOT NULL
        CHECK (artifact_type IN ('table', 'json', 'markdown', 'code')),
    title TEXT NOT NULL,
    content_json TEXT NOT NULL,
    language TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
""")

# Extraction columns — already inline in the SQLITE_TABLES uploads DDL above,
# but kept here for migrating pre-existing DBs that were created before the
# multimodal extraction feature. SQLiteDatabase.init_tables() runs a PRAGMA
# check + ALTER TABLE for each.
UPLOADS_EXTRACTION_COLUMNS: list[tuple[str, str]] = [
    ("extracted_text", "TEXT"),
    ("extracted_images_json", "TEXT"),
    ("extracted_meta_json", "TEXT"),
    ("extraction_status", "TEXT DEFAULT 'pending'"),
    ("extraction_error", "TEXT"),
]

# ---------------------------------------------------------------------------
# PostgreSQL DDL
# ---------------------------------------------------------------------------

PG_TABLES = [
    """
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
    created_at TEXT NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC')::TEXT,
    updated_at TEXT NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC')::TEXT
);
""",
    """
CREATE TABLE IF NOT EXISTS record_links (
    id SERIAL PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES metadata_records(id) ON DELETE CASCADE,
    target_id TEXT NOT NULL REFERENCES metadata_records(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC')::TEXT,
    UNIQUE(source_id, target_id)
);
""",
    """
CREATE TABLE IF NOT EXISTS conversations (
    id SERIAL PRIMARY KEY,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    attachments_json TEXT,
    created_at TEXT NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC')::TEXT
);
""",
    # uploads — extraction columns mirror SQLITE_TABLES so multimodal features
    # (PR #6) work identically on the Replit production PostgreSQL backend.
    """
CREATE TABLE IF NOT EXISTS uploads (
    id TEXT PRIMARY KEY,
    original_filename TEXT NOT NULL,
    content_type TEXT NOT NULL,
    file_path TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    session_id TEXT,
    extracted_text TEXT,
    extracted_images_json TEXT,
    extracted_meta_json TEXT,
    extraction_status TEXT DEFAULT 'pending',
    extraction_error TEXT,
    created_at TEXT NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC')::TEXT
);
""",
    """
CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    artifact_type TEXT NOT NULL
        CHECK (artifact_type IN ('table', 'json', 'markdown', 'code')),
    title TEXT NOT NULL,
    content_json TEXT NOT NULL,
    language TEXT,
    created_at TEXT NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC')::TEXT
);
""",
]

# ---------------------------------------------------------------------------
# Indexes (shared syntax, compatible with both backends)
# ---------------------------------------------------------------------------

CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_records_session ON metadata_records(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_records_type ON metadata_records(record_type)",
    "CREATE INDEX IF NOT EXISTS idx_records_category ON metadata_records(category)",
    "CREATE INDEX IF NOT EXISTS idx_records_status ON metadata_records(status)",
    "CREATE INDEX IF NOT EXISTS idx_links_source ON record_links(source_id)",
    "CREATE INDEX IF NOT EXISTS idx_links_target ON record_links(target_id)",
    "CREATE INDEX IF NOT EXISTS idx_conv_session ON conversations(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_uploads_session ON uploads(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_artifacts_session ON artifacts(session_id)",
]

# ---------------------------------------------------------------------------
# Category mapping
# ---------------------------------------------------------------------------

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
