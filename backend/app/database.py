import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

DATA_DIR = Path(os.getenv("BUDMON_DATA_DIR", "/data"))
DB_PATH = Path(os.getenv("BUDMON_DB", DATA_DIR / "budmon.sqlite3"))


def _connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def get_db():
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with get_db() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS targets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                url TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                failure_count INTEGER NOT NULL DEFAULT 0,
                last_status TEXT NOT NULL DEFAULT 'unknown',
                last_code INTEGER,
                last_error TEXT,
                last_checked_at TEXT,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS monitor_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target_id INTEGER NOT NULL,
                event_type TEXT NOT NULL DEFAULT 'service',
                ok INTEGER NOT NULL,
                status_code INTEGER,
                cert_days INTEGER,
                error TEXT,
                checked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(target_id) REFERENCES targets(id) ON DELETE CASCADE
            );
            """
        )
        _migrate(db)


def _columns(db: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in db.execute(f"PRAGMA table_info({table})").fetchall()}


def _migrate(db: sqlite3.Connection) -> None:
    target_columns = _columns(db, "targets")
    target_additions = {
        "last_cert_days": "INTEGER",
        "last_cert_expires_at": "TEXT",
        "last_cert_error": "TEXT",
        "last_cert_checked_at": "TEXT",
        "cert_alert_date": "TEXT",
        "service_alert_failure_count": "INTEGER NOT NULL DEFAULT 0",
    }
    for name, ddl in target_additions.items():
        if name not in target_columns:
            db.execute(f"ALTER TABLE targets ADD COLUMN {name} {ddl}")

    log_columns = _columns(db, "monitor_logs")
    log_additions = {
        "event_type": "TEXT NOT NULL DEFAULT 'service'",
        "cert_days": "INTEGER",
    }
    for name, ddl in log_additions.items():
        if name not in log_columns:
            db.execute(f"ALTER TABLE monitor_logs ADD COLUMN {name} {ddl}")


def is_installed() -> bool:
    with get_db() as db:
        row = db.execute("SELECT COUNT(*) AS total FROM users").fetchone()
        return bool(row["total"])


def get_setting(key: str, default: Any = None) -> Any:
    with get_db() as db:
        row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if not row:
        return default
    try:
        return json.loads(row["value"])
    except json.JSONDecodeError:
        return row["value"]


def set_setting(key: str, value: Any) -> None:
    with get_db() as db:
        db.execute(
            """
            INSERT INTO settings(key, value)
            VALUES(?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, json.dumps(value, ensure_ascii=False)),
        )


def merge_defaults(value: Any, default: Any) -> Any:
    if isinstance(value, dict) and isinstance(default, dict):
        merged = dict(default)
        for key, item in value.items():
            merged[key] = merge_defaults(item, default.get(key))
        return merged
    return default if value is None else value


def default_settings() -> dict[str, Any]:
    return {
        "monitor": {
            "interval_seconds": 60,
            "retry_delay_seconds": 5,
            "cert_expire_days": 5,
            "notify_methods": ["sms"],
            "notify_targets": "",
        },
        "sms": {
            "provider": "aliyun",
            "aliyun": {
                "accessKeyId": "",
                "accessKeySecret": "",
                "regionId": "cn-hangzhou",
                "signName": "",
            },
            "tencent": {
                "secretId": "",
                "secretKey": "",
                "region": "ap-guangzhou",
                "smsSdkAppId": "",
                "signName": "",
            },
            "templates": {
                "service_down": {
                    "name": "服务通知",
                    "code": "SMS_507940075",
                    "content": "温馨提醒：您的 ${name} 服务已断连，请及时关注。",
                    "params": ["name"],
                },
                "cert_expiring": {
                    "name": "证书过期",
                    "code": "SMS_507940075",
                    "content": "温馨提醒：您的 ${name} 证书剩余 ${day} 天过期，请及时关注。",
                    "params": ["name", "day"],
                },
            },
        },
        "email": {
            "host": "",
            "port": 465,
            "username": "",
            "password": "",
            "sender": "",
        },
    }
