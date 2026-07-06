"""
db.py — SQLite persistence. Each channel's game is stored as a JSON blob in a
row keyed by channel_id. SQLite gives us atomic writes and no file corruption,
which the flat JSON file couldn't guarantee.
"""

import json
import sqlite3
import threading

_LOCK = threading.Lock()
_DB_PATH = "dnd.db"


def init(db_path="dnd.db"):
    global _DB_PATH
    _DB_PATH = db_path
    con = sqlite3.connect(_DB_PATH)
    con.execute("CREATE TABLE IF NOT EXISTS games (channel_id TEXT PRIMARY KEY, data TEXT NOT NULL)")
    con.commit()
    con.close()


def load_game(channel_id, default_factory):
    with _LOCK:
        con = sqlite3.connect(_DB_PATH)
        row = con.execute("SELECT data FROM games WHERE channel_id=?", (str(channel_id),)).fetchone()
        con.close()
    if row:
        return json.loads(row[0])
    return default_factory()


def save_game(channel_id, game):
    blob = json.dumps(game)
    with _LOCK:
        con = sqlite3.connect(_DB_PATH)
        con.execute(
            "INSERT INTO games (channel_id, data) VALUES (?, ?) "
            "ON CONFLICT(channel_id) DO UPDATE SET data=excluded.data",
            (str(channel_id), blob),
        )
        con.commit()
        con.close()


def delete_game(channel_id):
    with _LOCK:
        con = sqlite3.connect(_DB_PATH)
        con.execute("DELETE FROM games WHERE channel_id=?", (str(channel_id),))
        con.commit()
        con.close()


def all_games():
    with _LOCK:
        con = sqlite3.connect(_DB_PATH)
        rows = con.execute("SELECT channel_id, data FROM games").fetchall()
        con.close()
    return [(cid, json.loads(data)) for cid, data in rows]
