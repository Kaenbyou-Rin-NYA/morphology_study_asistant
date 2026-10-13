"""SQLite-backed JSON records; each write is atomic and each read is a snapshot."""
import json
import os
import sqlite3
from pathlib import Path

DATA = Path(os.environ.get("MORPH_DATA_DIR", Path(__file__).resolve().parents[1] / "data"))
DATA.mkdir(parents=True, exist_ok=True)
DB = DATA / "study.sqlite3"


def connect():
    connection = sqlite3.connect(DB, timeout=30)
    connection.execute("CREATE TABLE IF NOT EXISTS records (kind TEXT, id TEXT, value TEXT, PRIMARY KEY(kind,id))")
    return connection


def save(kind, record):
    with connect() as connection:
        connection.execute("INSERT OR REPLACE INTO records VALUES (?,?,?)", (kind, record["id"], json.dumps(record, ensure_ascii=False)))


def get(kind, identity):
    with connect() as connection:
        row = connection.execute("SELECT value FROM records WHERE kind=? AND id=?", (kind, identity)).fetchone()
    return json.loads(row[0]) if row else None


def all_records(kind):
    with connect() as connection:
        rows = connection.execute("SELECT value FROM records WHERE kind=? ORDER BY rowid", (kind,)).fetchall()
    return [json.loads(row[0]) for row in rows]
