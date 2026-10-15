import os
import sqlite3
import time

from .config import S

_conn = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders(
  order_gid TEXT PRIMARY KEY, order_name TEXT, phone TEXT, first_name TEXT,
  total TEXT, status TEXT, created_at REAL, updated_at REAL, wamid TEXT);
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY AUTOINCREMENT, direction TEXT, phone TEXT, wamid TEXT,
  kind TEXT, body TEXT, result TEXT, ts REAL);
CREATE UNIQUE INDEX IF NOT EXISTS ux_in_wamid ON messages(wamid) WHERE direction='in';
CREATE TABLE IF NOT EXISTS windows(phone TEXT PRIMARY KEY, last_inbound REAL);
CREATE TABLE IF NOT EXISTS costs(
  wamid TEXT PRIMARY KEY, category TEXT, billable INTEGER, pricing_type TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, level TEXT, text TEXT, ts REAL);
"""


def conn():
    global _conn
    if _conn is None:
        if S.DB_PATH != ":memory:":
            os.makedirs(os.path.dirname(S.DB_PATH) or ".", exist_ok=True)
        _conn = sqlite3.connect(S.DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.executescript(SCHEMA)
    return _conn


def reset():
    global _conn
    if _conn:
        _conn.close()
    _conn = None


def q(sql, args=()):
    return conn().execute(sql, args).fetchall()


def x(sql, args=()):
    c = conn().execute(sql, args)
    conn().commit()
    return c


def event(level, text):
    x("INSERT INTO events(level,text,ts) VALUES(?,?,?)", (level, text, time.time()))


def seen_inbound(wamid):
    """Record an inbound message id; return True if already processed (Meta retries)."""
    try:
        x("INSERT INTO messages(direction,wamid,ts) VALUES('in',?,?)", (wamid, time.time()))
        return False
    except sqlite3.IntegrityError:
        return True


def touch_window(phone):
    x("INSERT INTO windows VALUES(?,?) ON CONFLICT(phone) DO UPDATE SET last_inbound=excluded.last_inbound",
      (phone, time.time()))


def window_open(phone):
    r = q("SELECT last_inbound FROM windows WHERE phone=?", (phone,))
    return bool(r) and time.time() - r[0]["last_inbound"] < 23.5 * 3600


def templates_sent_today():
    since = time.time() - 86400
    return q("SELECT COUNT(*) n FROM messages WHERE direction='out' AND kind='template' "
             "AND result='sent' AND ts>?", (since,))[0]["n"]


def latest_pending(phone):
    r = q("SELECT * FROM orders WHERE phone=? AND status='pending' ORDER BY created_at DESC LIMIT 1", (phone,))
    return r[0] if r else None


def latest_order(phone):
    r = q("SELECT * FROM orders WHERE phone=? ORDER BY created_at DESC LIMIT 1", (phone,))
    return r[0] if r else None


def set_status(gid, status):
    x("UPDATE orders SET status=?, updated_at=? WHERE order_gid=?", (status, time.time(), gid))
