"""Bound monitoring history by age and per-target count; deleted pages reuse SQLite space."""
import logging
import os
from .database import get_db

logger = logging.getLogger("budmon.retention")


def retention_limits():
    def positive(name, default):
        try:
            return max(1, int(os.getenv(name, str(default))))
        except ValueError:
            return default
    return positive("BUDMON_LOG_MAX_PER_TARGET", 1000), positive("BUDMON_LOG_RETENTION_DAYS", 7)


def prune_target_logs(target_id):
    maximum, days = retention_limits()
    with get_db() as db:
        db.execute("DELETE FROM monitor_logs WHERE target_id=? AND checked_at < datetime('now', ?)", (target_id, f"-{days} days"))
        cutoff = db.execute("SELECT id FROM monitor_logs WHERE target_id=? ORDER BY id DESC LIMIT 1 OFFSET ?", (target_id, maximum-1)).fetchone()
        if cutoff:
            db.execute("DELETE FROM monitor_logs WHERE target_id=? AND id<?", (target_id, cutoff["id"]))


def prune_history():
    try:
        with get_db() as db:
            ids = [row["id"] for row in db.execute("SELECT id FROM targets")]
        for target_id in ids:
            prune_target_logs(target_id)
    except Exception:
        logger.exception("检测历史清理失败")
