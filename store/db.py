"""Схема SQLite и подключение (`./data/bot.db`). Единственное место с DDL."""
from __future__ import annotations

from pathlib import Path

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL UNIQUE,
    title TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('group', 'supergroup', 'channel', 'private')),
    paused INTEGER NOT NULL DEFAULT 0,
    last_processed_msg_id INTEGER,
    added_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS receivers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL UNIQUE,
    title TEXT NOT NULL,
    added_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS keywords (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK (kind IN ('word', 'phrase', 'regex')),
    pattern TEXT NOT NULL,
    is_stop INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS hits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    author_id INTEGER,
    author_username TEXT,
    author_name TEXT,
    matched_keywords TEXT NOT NULL,
    text_hash TEXT NOT NULL,
    text_preview TEXT,
    also_in TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_hits_source_message
    ON hits (source_chat_id, message_id);

CREATE INDEX IF NOT EXISTS ix_hits_author_text_time
    ON hits (author_id, text_hash, created_at);

CREATE TABLE IF NOT EXISTS delivery_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hit_id INTEGER NOT NULL REFERENCES hits (id),
    receiver_chat_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'sent', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT,
    error TEXT
);
"""

# settings.* по умолчанию (interfaces.md); tz переопределяется TZ из .env мастером setup (R14).
DEFAULT_SETTINGS: dict[str, str] = {
    "tz": "Europe/Moscow",
    "dedup_window_days": "7",
    "dedup_enabled": "1",
    "rate_limit_per_min": "20",
    "scan_days": "3",
    "scan_msgs": "500",
    "retention_days": "90",
    "card_template": "🔑 {keywords}\n👤 {author}\n💬 {chat}\n🕐 {time}",
    "monitoring_paused": "0",
    "monitoring_paused_reason": "",
    "monitoring_paused_at": "",
    "monitor_all_dm": "0",
}


async def create_schema(conn: aiosqlite.Connection) -> None:
    """Создаёт схему (идемпотентно) и досевает отсутствующие настройки по умолчанию."""
    await conn.executescript(SCHEMA)
    for key, value in DEFAULT_SETTINGS.items():
        await conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value)
        )
    await conn.commit()


async def connect(path: str) -> aiosqlite.Connection:
    """Открывает (создавая при необходимости) БД и приводит схему к актуальной."""
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(path)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys = ON")
    await create_schema(conn)
    return conn
