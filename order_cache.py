"""
Persistent SQLite cache mapping Shipday order_id → AVS activityId.
Stored on the Render persistent disk mounted at /data.
Falls back to /tmp if /data is not available (local dev).
"""
import os
import sqlite3
import logging

logger = logging.getLogger(__name__)

_DB_PATH = "/data/order_cache.db" if os.path.isdir("/data") else "/tmp/order_cache.db"


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS order_map ("
        "  shipday_order_id TEXT PRIMARY KEY,"
        "  activity_id      TEXT NOT NULL,"
        "  created_at       DATETIME DEFAULT CURRENT_TIMESTAMP"
        ")"
    )
    conn.commit()
    return conn


def store(shipday_order_id: str, activity_id: str) -> None:
    """Persist shipday_order_id → activity_id mapping."""
    try:
        with _get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO order_map (shipday_order_id, activity_id) VALUES (?, ?)",
                (str(shipday_order_id), str(activity_id)),
            )
        logger.info(f"[order_cache] Stored {shipday_order_id} → {activity_id}")
    except Exception as e:
        logger.error(f"[order_cache] Failed to store mapping: {e}")


def dump_all() -> list:
    """Return all (shipday_order_id, activity_id) pairs in the cache."""
    try:
        with _get_conn() as conn:
            rows = conn.execute(
                "SELECT shipday_order_id, activity_id FROM order_map ORDER BY created_at"
            ).fetchall()
        return [{"shipday_order_id": r[0], "activity_id": r[1]} for r in rows]
    except Exception as e:
        logger.error(f"[order_cache] Failed to dump all: {e}")
        return []


def lookup_by_activity_id(activity_id: str) -> str | None:
    """Return shipday_order_id for the given activity_id (customer_reference), or None if not found."""
    try:
        with _get_conn() as conn:
            row = conn.execute(
                "SELECT shipday_order_id FROM order_map WHERE activity_id = ? ORDER BY created_at DESC LIMIT 1",
                (str(activity_id),),
            ).fetchone()
        if row:
            logger.info(f"[order_cache] Found activity_id={activity_id} → {row[0]}")
            return row[0]
        logger.warning(f"[order_cache] No mapping for activity_id={activity_id}")
        return None
    except Exception as e:
        logger.error(f"[order_cache] Failed to lookup by activity_id: {e}")
        return None


def lookup(shipday_order_id: str) -> str | None:
    """Return activity_id for the given shipday_order_id, or None if not found."""
    try:
        with _get_conn() as conn:
            row = conn.execute(
                "SELECT activity_id FROM order_map WHERE shipday_order_id = ?",
                (str(shipday_order_id),),
            ).fetchone()
        if row:
            logger.info(f"[order_cache] Found {shipday_order_id} → {row[0]}")
            return row[0]
        logger.warning(f"[order_cache] No mapping for shipday_order_id={shipday_order_id}")
        return None
    except Exception as e:
        logger.error(f"[order_cache] Failed to lookup mapping: {e}")
        return None
