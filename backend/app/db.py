import os
import sqlite3
from pathlib import Path
from contextlib import contextmanager

DATA = Path(os.getenv('DATA_DIR', './data')).resolve()

@contextmanager
def connect():
    DATA.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DATA / 'travel.sqlite3', timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

def init():
    with connect() as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
            password TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'user', active INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS documents (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, path TEXT NOT NULL,
            status TEXT NOT NULL, error TEXT, active INTEGER NOT NULL DEFAULT 0,
            replaces TEXT, created REAL NOT NULL, chunks INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id), status TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), title TEXT NOT NULL, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            role TEXT NOT NULL, content TEXT NOT NULL, citations TEXT NOT NULL DEFAULT '[]', created REAL NOT NULL);
        ''')
