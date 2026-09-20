"""Bound monitoring history by age and per-target count; deleted pages reuse SQLite space."""
import logging
import os
from datetime import datetime, timedelta, timezone
from .database import get_db

logger = logging.getLogger("budmon.retention")


def retention_limits():
    def positive(name, default):
        try:
            return max(1, int(os.getenv(name, str(default))))
        except ValueError:
            return default
    return positive("BUDMON_LOG_MAX_PER_TARGET", 1000), positive("BUDMON_LOG_RETENTION_DAYS", 7)


def _prune_target_logs(db, target_id, maximum, cutoff_dt):
    db.execute("DELETE FROM monitor_logs WHERE target_id=? AND checked_at < ?", (target_id, cutoff_dt))
    cutoff = db.execute(
        "SELECT id FROM monitor_logs WHERE target_id=? ORDER BY id DESC LIMIT 1 OFFSET ?",
        (target_id, maximum - 1),
    ).fetchone()
    if cutoff:
        db.execute("DELETE FROM monitor_logs WHERE target_id=? AND id<?", (target_id, cutoff["id"]))


def prune_target_logs(target_id):
    maximum, days = retention_limits()
    with get_db() as db:
        cutoff_dt = datetime.now(timezone.utc) - timedelta(days=days)
        _prune_target_logs(db, target_id, maximum, cutoff_dt)


def prune_history():
    try:
        maximum, days = retention_limits()
        cutoff_dt = datetime.now(timezone.utc) - timedelta(days=days)
        try:
            push_days = max(1, int(os.getenv("BUDMON_PUSH_RETENTION_DAYS", "30")))
        except ValueError:
            push_days = 30
        push_cutoff = datetime.now(timezone.utc) - timedelta(days=push_days)
        with get_db() as db:
            db.execute(
                """DELETE FROM push_deliveries
                   WHERE status IN ('sent','failed','expired')
                   AND notification_id IN (SELECT id FROM notifications WHERE created_at < ?)""",
                (push_cutoff,),
            )
        try:
            batch_size = min(2000, max(10, int(os.getenv("BUDMON_RETENTION_BATCH_SIZE", "250"))))
        except ValueError:
            batch_size = 250
        last_id = 0
        while True:
            with get_db() as db:
                ids = [
                    row["id"] for row in db.execute(
                        "SELECT id FROM targets WHERE id>? ORDER BY id LIMIT ?",
                        (last_id, batch_size),
                    ).fetchall()
                ]
                for target_id in ids:
                    _prune_target_logs(db, target_id, maximum, cutoff_dt)
            if not ids:
                break
            last_id = ids[-1]
    except Exception:
        logger.exception("检测历史清理失败")
