-- Your personal notes/tracking, keyed by the entry id from config/seeds.yaml.
-- The weekly refresh never writes here.
CREATE TABLE IF NOT EXISTS notes (
  entry_id   TEXT PRIMARY KEY,
  status     TEXT NOT NULL DEFAULT 'none',   -- none | emailed | follow_up | replied | meeting | applied | interview | offer | closed
  priority   INTEGER NOT NULL DEFAULT 0,     -- 0..3 stars
  follow_up  TEXT,                           -- YYYY-MM-DD
  contact    TEXT,                           -- who you're talking to
  tags       TEXT NOT NULL DEFAULT '[]',     -- JSON array of strings
  body       TEXT NOT NULL DEFAULT '',       -- free-text note
  log        TEXT NOT NULL DEFAULT '[]',     -- JSON array of {date, text}
  updated_at TEXT NOT NULL
);
