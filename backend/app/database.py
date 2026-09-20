import json
import logging
import os
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, quote, unquote, urlsplit

logger = logging.getLogger("budmon.database")


def _load_env() -> None:
    candidates = [
        Path("/data/budmon/.env"),
        Path("/data/budmon/budmon.env"),
        Path("/etc/budmon/budmon.env"),
        Path(os.getenv("BUDMON_DATA_DIR", "/data")) / ".env",
        Path(os.getenv("BUDMON_DATA_DIR", "/data")) / "budmon.env",
        Path(__file__).resolve().parent.parent.parent / ".env",
        Path(__file__).resolve().parent.parent.parent / "budmon.env",
        Path.cwd() / ".env",
        Path.cwd() / "budmon.env",
    ]
    for p in candidates:
        if p.is_file():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip("'\"")
                        if k and k not in os.environ:
                            os.environ[k] = v
            except Exception:
                pass


_load_env()

DATA_DIR = Path(os.getenv("BUDMON_DATA_DIR", "/data"))
DB_PATH = Path(os.getenv("BUDMON_DB", DATA_DIR / "budmon.sqlite3"))

try:
    import psycopg
    from psycopg import errors as pg_errors
except ImportError:
    psycopg = None
    pg_errors = None

try:
    from psycopg_pool import ConnectionPool
except ImportError:
    ConnectionPool = None

_postgres_pool = None
_postgres_pool_key = None
_postgres_pool_lock = threading.Lock()


def _positive_env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        return default


class IntegrityError(Exception):
    """Unified database integrity error."""
    pass


class DatabaseError(Exception):
    """Unified database generic error."""
    pass


class CompatRow(dict):
    """Row object supporting both dict key access and numeric index access."""
    def __init__(self, mapping, tuple_values):
        super().__init__(mapping)
        self._tuple = tuple_values

    def __getitem__(self, item):
        if isinstance(item, int):
            return self._tuple[item]
        return super().__getitem__(item)


TABLES_WITH_SERIAL_ID = {
    "users", "targets", "monitor_logs", "billing_events",
    "purchases", "login_events", "devices", "notifications",
    "push_deliveries", "audit_logs"
}
POSTGRES_IMMEDIATE_LOCK_ID = 0x4255444D4F4E


def _convert_sql_placeholders(sql: str) -> str:
    """Convert ? placeholders to %s outside of single-quoted string literals."""
    parts = []
    in_quote = False
    for chunk in sql.split("'"):
        if not in_quote:
            parts.append(chunk.replace("?", "%s"))
        else:
            parts.append(chunk)
        in_quote = not in_quote
    return "'".join(parts)


def get_postgres_connection_params() -> dict[str, Any]:
    raw_url = os.getenv("BUDMON_DATABASE_URL") or os.getenv("DATABASE_URL") or ""
    url = raw_url.strip().strip("'\"")

    params: dict[str, Any] = {
        "host": os.getenv("BUDMON_DB_HOST") or os.getenv("POSTGRES_HOST") or "127.0.0.1",
        "port": int(os.getenv("BUDMON_DB_PORT") or os.getenv("POSTGRES_PORT") or 5432),
        "user": os.getenv("BUDMON_DB_USER") or os.getenv("POSTGRES_USER") or "budmon",
        "password": os.getenv("BUDMON_DB_PASSWORD") or os.getenv("POSTGRES_PASSWORD") or "budmon",
        "dbname": os.getenv("BUDMON_DB_NAME") or os.getenv("POSTGRES_DB") or "budmon",
    }

    if url and (url.startswith("postgresql://") or url.startswith("postgres://")):
        m = re.match(r"^(?:postgresql|postgres)://([^:]+):(.*)@([^:/]+)(?::(\d+))?/(.+)$", url)
        if m:
            user, pwd, host, port, dbname = m.groups()
            params["user"] = unquote(user)
            params["password"] = unquote(pwd)
            params["host"] = host
            if port:
                try:
                    params["port"] = int(port)
                except ValueError:
                    pass
            params["dbname"] = dbname.split("?")[0]
        else:
            try:
                parsed = urlsplit(url)
                if parsed.username:
                    params["user"] = unquote(parsed.username)
                if parsed.password:
                    params["password"] = unquote(parsed.password)
                if parsed.hostname:
                    params["host"] = parsed.hostname
                if parsed.port:
                    params["port"] = parsed.port
                if parsed.path and len(parsed.path) > 1:
                    params["dbname"] = parsed.path.lstrip("/").split("?")[0]
            except Exception:
                pass
        if "?" in url:
            for key, value in parse_qsl(url.rsplit("?", 1)[1], keep_blank_values=True):
                params[key] = value

    # Explicit environment variable overrides
    if os.getenv("BUDMON_DB_PASSWORD"):
        params["password"] = os.getenv("BUDMON_DB_PASSWORD")
    elif os.getenv("POSTGRES_PASSWORD"):
        params["password"] = os.getenv("POSTGRES_PASSWORD")

    if os.getenv("BUDMON_DB_USER"):
        params["user"] = os.getenv("BUDMON_DB_USER")
    elif os.getenv("POSTGRES_USER"):
        params["user"] = os.getenv("POSTGRES_USER")

    if os.getenv("BUDMON_DB_HOST"):
        params["host"] = os.getenv("BUDMON_DB_HOST")
    elif os.getenv("POSTGRES_HOST"):
        params["host"] = os.getenv("POSTGRES_HOST")

    if os.getenv("BUDMON_DB_PORT"):
        try:
            params["port"] = int(os.getenv("BUDMON_DB_PORT"))
        except ValueError:
            pass
    elif os.getenv("POSTGRES_PORT"):
        try:
            params["port"] = int(os.getenv("POSTGRES_PORT"))
        except ValueError:
            pass

    if os.getenv("BUDMON_DB_NAME"):
        params["dbname"] = os.getenv("BUDMON_DB_NAME")
    elif os.getenv("POSTGRES_DB"):
        params["dbname"] = os.getenv("POSTGRES_DB")

    return params


def get_database_url() -> str:
    raw_url = os.getenv("BUDMON_DATABASE_URL") or os.getenv("DATABASE_URL")
    if raw_url:
        cleaned = raw_url.strip().strip("'\"")
        if cleaned:
            return cleaned
    if os.getenv("BUDMON_DB_ENGINE", "").lower() == "sqlite" or "BUDMON_DB" in os.environ:
        return f"sqlite:///{DB_PATH}"
    params = get_postgres_connection_params()
    safe_pwd = quote(str(params.get("password", "")))
    return f"postgresql://{params['user']}:{safe_pwd}@{params['host']}:{params['port']}/{params['dbname']}"


def is_postgres() -> bool:
    url = get_database_url()
    return url.startswith("postgresql://") or url.startswith("postgres://")


class PostgresCursorWrapper:
    def __init__(self, cursor, lastrowid=None):
        self._cursor = cursor
        self.lastrowid = lastrowid

    def fetchone(self):
        row = self._cursor.fetchone()
        if row is None:
            return None
        if isinstance(row, CompatRow):
            return row
        if isinstance(row, dict):
            return CompatRow(row, tuple(row.values()))
        if self._cursor.description:
            names = [col.name for col in self._cursor.description]
            return CompatRow(dict(zip(names, row)), tuple(row))
        return row

    def fetchall(self):
        rows = self._cursor.fetchall()
        result = []
        for row in rows:
            if isinstance(row, CompatRow):
                result.append(row)
            elif isinstance(row, dict):
                result.append(CompatRow(row, tuple(row.values())))
            elif self._cursor.description:
                names = [col.name for col in self._cursor.description]
                result.append(CompatRow(dict(zip(names, row)), tuple(row)))
            else:
                result.append(row)
        return result

    @property
    def rowcount(self):
        return self._cursor.rowcount

    @property
    def description(self):
        return self._cursor.description

    def __iter__(self):
        while True:
            row = self.fetchone()
            if row is None:
                break
            yield row


class PostgresConnectionWrapper:
    def __init__(self, raw_conn, release=None):
        self._raw_conn = raw_conn
        self._release = release

    def execute(self, sql: str, params: Any = None):
        trimmed = sql.strip()
        if trimmed.upper().startswith("BEGIN IMMEDIATE"):
            cursor = self._raw_conn.cursor()
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", (POSTGRES_IMMEDIATE_LOCK_ID,))
            return PostgresCursorWrapper(cursor)
        if trimmed.upper().startswith("BEGIN"):
            cursor = self._raw_conn.cursor()
            cursor.execute("BEGIN")
            return PostgresCursorWrapper(cursor)

        sql_converted = _convert_sql_placeholders(sql)
        lastrowid = None

        is_insert = trimmed.upper().startswith("INSERT INTO ")
        has_returning = " RETURNING " in (" " + trimmed.upper() + " ")
        auto_returning_id = False

        if is_insert and not has_returning and re.search(r"\bVALUES\s*\(", trimmed, re.IGNORECASE):
            match = re.match(r"INSERT\s+INTO\s+([A-Za-z_][A-Za-z0-9_]*)", trimmed, re.IGNORECASE)
            if match:
                table_name = match.group(1).lower()
                if table_name in TABLES_WITH_SERIAL_ID:
                    sql_converted = sql_converted.rstrip(" ;") + " RETURNING id"
                    auto_returning_id = True

        try:
            cursor = self._raw_conn.cursor()
            cursor.execute(sql_converted, params or ())
            if auto_returning_id:
                row = cursor.fetchone()
                if row:
                    lastrowid = row[0]
            return PostgresCursorWrapper(cursor, lastrowid=lastrowid)
        except Exception as e:
            if pg_errors and isinstance(e, pg_errors.IntegrityError):
                raise IntegrityError(str(e)) from e
            raise

    def executescript(self, script: str):
        try:
            cursor = self._raw_conn.cursor()
            cursor.execute(script)
            return PostgresCursorWrapper(cursor)
        except Exception as e:
            if pg_errors and isinstance(e, pg_errors.IntegrityError):
                raise IntegrityError(str(e)) from e
            raise

    def commit(self):
        self._raw_conn.commit()

    def rollback(self):
        self._raw_conn.rollback()

    def close(self):
        if self._release:
            release, self._release = self._release, None
            release(self._raw_conn)
        else:
            self._raw_conn.close()


def _connect_postgres():
    if psycopg is None:
        raise RuntimeError("psycopg is not installed. Please install psycopg[binary] to use PostgreSQL.")
    params = get_postgres_connection_params()
    if ConnectionPool is not None:
        global _postgres_pool, _postgres_pool_key
        min_size = _positive_env_int("BUDMON_DB_POOL_MIN", 2)
        max_size = max(min_size, _positive_env_int("BUDMON_DB_POOL_MAX", 24))
        pool_key = (*sorted(params.items()), min_size, max_size)
        with _postgres_pool_lock:
            if _postgres_pool is None or _postgres_pool_key != pool_key:
                if _postgres_pool is not None:
                    _postgres_pool.close()
                _postgres_pool = ConnectionPool(
                    kwargs={**params, "autocommit": False},
                    min_size=min_size,
                    max_size=max_size,
                    timeout=30,
                    open=True,
                    name="budmon",
                )
                _postgres_pool_key = pool_key
            pool = _postgres_pool
        raw_conn = pool.getconn()
        return PostgresConnectionWrapper(raw_conn, release=pool.putconn)

    try:
        raw_conn = psycopg.connect(**params, autocommit=False)
    except Exception as e:
        logger.error(
            "Failed to connect to PostgreSQL at %s:%s (user=%s, db=%s): %s",
            params.get("host"), params.get("port"), params.get("user"), params.get("dbname"), e
        )
        try:
            url = get_database_url()
            raw_conn = psycopg.connect(url, autocommit=False)
        except Exception:
            raise e
    return PostgresConnectionWrapper(raw_conn)


class SqliteCursorWrapper:
    def __init__(self, cursor):
        self._cursor = cursor

    def fetchone(self):
        row = self._cursor.fetchone()
        if row is None:
            return None
        return CompatRow(dict(row), tuple(row))

    def fetchall(self):
        return [CompatRow(dict(r), tuple(r)) for r in self._cursor.fetchall()]

    @property
    def lastrowid(self):
        return self._cursor.lastrowid

    @property
    def rowcount(self):
        return self._cursor.rowcount

    @property
    def description(self):
        return self._cursor.description

    def __iter__(self):
        while True:
            row = self.fetchone()
            if row is None:
                break
            yield row


class SqliteConnectionWrapper:
    def __init__(self, raw_conn):
        self._raw_conn = raw_conn

    def execute(self, sql: str, params: Any = None):
        try:
            if params is None:
                cur = self._raw_conn.execute(sql)
            else:
                cur = self._raw_conn.execute(sql, params)
            return SqliteCursorWrapper(cur)
        except sqlite3.IntegrityError as e:
            raise IntegrityError(str(e)) from e

    def executescript(self, script: str):
        try:
            cur = self._raw_conn.executescript(script)
            return SqliteCursorWrapper(cur)
        except sqlite3.IntegrityError as e:
            raise IntegrityError(str(e)) from e

    def commit(self):
        self._raw_conn.commit()

    def rollback(self):
        self._raw_conn.rollback()

    def close(self):
        self._raw_conn.close()


def _connect_sqlite():
    url = get_database_url()
    if url.startswith("sqlite:///"):
        path_str = url[len("sqlite:///"):]
        if path_str == ":memory:":
            db_file = ":memory:"
        else:
            db_file = Path(path_str)
            db_file.parent.mkdir(parents=True, exist_ok=True)
    else:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        db_file = DB_PATH

    conn = sqlite3.connect(db_file, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return SqliteConnectionWrapper(conn)


def _connect():
    if is_postgres():
        return _connect_postgres()
    return _connect_sqlite()


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
    if is_postgres():
        _init_postgres()
    else:
        _init_sqlite()


def _init_postgres() -> None:
    with get_db() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                role TEXT NOT NULL DEFAULT 'user',
                disabled INTEGER NOT NULL DEFAULT 0,
                plan_id TEXT NOT NULL DEFAULT 'free',
                plan_expires_at TEXT,
                quota_override INTEGER,
                push_enabled INTEGER NOT NULL DEFAULT 1,
                last_login_at TIMESTAMP WITH TIME ZONE,
                app_account_token TEXT UNIQUE
            );

            CREATE TABLE IF NOT EXISTS targets (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                url TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                failure_count INTEGER NOT NULL DEFAULT 0,
                last_status TEXT NOT NULL DEFAULT 'unknown',
                last_code INTEGER,
                last_error TEXT,
                last_checked_at TIMESTAMP WITH TIME ZONE,
                updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                owner_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                last_cert_days INTEGER,
                last_cert_expires_at TEXT,
                last_cert_error TEXT,
                last_cert_checked_at TIMESTAMP WITH TIME ZONE,
                cert_alert_date TEXT,
                service_alert_failure_count INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS monitor_logs (
                id SERIAL PRIMARY KEY,
                target_id INTEGER NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
                event_type TEXT NOT NULL DEFAULT 'service',
                ok INTEGER NOT NULL,
                status_code INTEGER,
                cert_days INTEGER,
                error TEXT,
                checked_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS plans (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                target_limit INTEGER NOT NULL,
                price_minor INTEGER NOT NULL DEFAULT 0,
                currency TEXT NOT NULL DEFAULT 'CNY',
                apple_product_id TEXT,
                enabled INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS billing_events (
                id SERIAL PRIMARY KEY,
                provider TEXT NOT NULL,
                external_id TEXT NOT NULL,
                user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                payload TEXT NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(provider, external_id)
            );

            CREATE TABLE IF NOT EXISTS purchases (
                id SERIAL PRIMARY KEY,
                environment TEXT NOT NULL,
                transaction_id TEXT NOT NULL,
                user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                username TEXT NOT NULL,
                app_account_token TEXT NOT NULL,
                product_id TEXT NOT NULL,
                quantity INTEGER NOT NULL CHECK(quantity > 0),
                price_milli INTEGER,
                currency TEXT,
                purchased_at TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('credited','revoked')),
                signed_date BIGINT NOT NULL,
                notification_date BIGINT NOT NULL DEFAULT 0,
                created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(environment, transaction_id)
            );

            CREATE TABLE IF NOT EXISTS login_events (
                id SERIAL PRIMARY KEY,
                user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                username TEXT NOT NULL,
                kind TEXT NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                refresh_hash TEXT NOT NULL UNIQUE,
                expires_at BIGINT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS devices (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                token TEXT NOT NULL,
                environment TEXT NOT NULL,
                updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(token, environment)
            );

            CREATE TABLE IF NOT EXISTS notifications (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                target_id INTEGER REFERENCES targets(id) ON DELETE SET NULL,
                kind TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                read_at TIMESTAMP WITH TIME ZONE,
                created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS push_deliveries (
                id SERIAL PRIMARY KEY,
                notification_id INTEGER NOT NULL REFERENCES notifications(id) ON DELETE CASCADE,
                device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
                attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt BIGINT NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending',
                last_error TEXT,
                UNIQUE(notification_id, device_id)
            );

            CREATE TABLE IF NOT EXISTS audit_logs (
                id SERIAL PRIMARY KEY,
                actor_id INTEGER NOT NULL,
                subject_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS rate_limits (
                key TEXT PRIMARY KEY,
                "window" BIGINT NOT NULL,
                count INTEGER NOT NULL
            );

            CREATE INDEX IF NOT EXISTS targets_owner ON targets(owner_id);
            CREATE INDEX IF NOT EXISTS logs_target ON monitor_logs(target_id, id DESC);
            CREATE INDEX IF NOT EXISTS purchases_user ON purchases(user_id, id DESC);
            CREATE INDEX IF NOT EXISTS login_events_user ON login_events(user_id, id DESC);
            CREATE INDEX IF NOT EXISTS notifications_owner ON notifications(user_id, id DESC);
            CREATE INDEX IF NOT EXISTS targets_enabled_id ON targets(enabled, id);
            CREATE INDEX IF NOT EXISTS logs_target_checked ON monitor_logs(target_id, checked_at);
            CREATE INDEX IF NOT EXISTS devices_user ON devices(user_id);
            CREATE INDEX IF NOT EXISTS push_pending ON push_deliveries(status, next_attempt, id);
            CREATE INDEX IF NOT EXISTS notifications_created ON notifications(created_at);

            INSERT INTO plans(id, name, target_limit) VALUES('free', '免费版', 5) ON CONFLICT (id) DO NOTHING;
            INSERT INTO plans(id, name, target_limit) VALUES('pro', '专业版', 30) ON CONFLICT (id) DO NOTHING;
        """)


def _columns(db, table: str) -> set[str]:
    return {row["name"] for row in db.execute(f"PRAGMA table_info({table})").fetchall()}


def _init_sqlite() -> None:
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
        _migrate_sqlite(db)


def _migrate_sqlite(db) -> None:
    user_columns = _columns(db, "users")
    for name, ddl in {
        "role": "TEXT NOT NULL DEFAULT 'user'",
        "disabled": "INTEGER NOT NULL DEFAULT 0",
        "plan_id": "TEXT NOT NULL DEFAULT 'free'",
        "plan_expires_at": "TEXT",
        "quota_override": "INTEGER",
        "push_enabled": "INTEGER NOT NULL DEFAULT 1",
        "last_login_at": "TEXT",
        "app_account_token": "TEXT",
    }.items():
        if name not in user_columns:
            db.execute(f"ALTER TABLE users ADD COLUMN {name} {ddl}")
    if "role" not in user_columns:
        db.execute("UPDATE users SET role='admin' WHERE id=(SELECT MIN(id) FROM users)")
    for row in db.execute("SELECT id FROM users WHERE app_account_token IS NULL").fetchall():
        db.execute("UPDATE users SET app_account_token=? WHERE id=?", (str(uuid.uuid4()), row["id"]))
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS user_apple_token ON users(app_account_token)")
    target_columns = _columns(db, "targets")
    target_additions = {
        "owner_id": "INTEGER REFERENCES users(id)",
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

    db.execute("UPDATE targets SET owner_id=(SELECT MIN(id) FROM users) WHERE owner_id IS NULL")
    db.executescript("""
        CREATE INDEX IF NOT EXISTS targets_owner ON targets(owner_id);
        CREATE INDEX IF NOT EXISTS logs_target ON monitor_logs(target_id, id DESC);
        CREATE TABLE IF NOT EXISTS plans (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, target_limit INTEGER NOT NULL,
            price_minor INTEGER NOT NULL DEFAULT 0, currency TEXT NOT NULL DEFAULT 'CNY',
            apple_product_id TEXT, enabled INTEGER NOT NULL DEFAULT 1
        );
        INSERT OR IGNORE INTO plans(id,name,target_limit) VALUES('free','免费版',5);
        INSERT OR IGNORE INTO plans(id,name,target_limit) VALUES('pro','专业版',30);
        CREATE TABLE IF NOT EXISTS billing_events (
            id INTEGER PRIMARY KEY, provider TEXT NOT NULL, external_id TEXT NOT NULL,
            user_id INTEGER REFERENCES users(id), payload TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(provider,external_id)
        );
        CREATE TABLE IF NOT EXISTS purchases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            environment TEXT NOT NULL, transaction_id TEXT NOT NULL,
            user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            username TEXT NOT NULL, app_account_token TEXT NOT NULL,
            product_id TEXT NOT NULL, quantity INTEGER NOT NULL CHECK(quantity > 0),
            price_milli INTEGER, currency TEXT, purchased_at TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('credited','revoked')),
            signed_date INTEGER NOT NULL, notification_date INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(environment, transaction_id)
        );
        CREATE INDEX IF NOT EXISTS purchases_user ON purchases(user_id, id DESC);
        CREATE TABLE IF NOT EXISTS login_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            username TEXT NOT NULL, kind TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS login_events_user ON login_events(user_id, id DESC);
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            refresh_hash TEXT NOT NULL UNIQUE, expires_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS devices (
            id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            token TEXT NOT NULL, environment TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(token,environment)
        );
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            target_id INTEGER REFERENCES targets(id) ON DELETE SET NULL,
            kind TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL,
            read_at TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS notifications_owner ON notifications(user_id,id DESC);
        CREATE TABLE IF NOT EXISTS push_deliveries (
            id INTEGER PRIMARY KEY,
            notification_id INTEGER NOT NULL REFERENCES notifications(id) ON DELETE CASCADE,
            device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
            attempts INTEGER NOT NULL DEFAULT 0, next_attempt INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending', last_error TEXT,
            UNIQUE(notification_id,device_id)
        );
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY, actor_id INTEGER NOT NULL,
            subject_id INTEGER NOT NULL, action TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS rate_limits (
            key TEXT PRIMARY KEY, "window" INTEGER NOT NULL, count INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS targets_enabled_id ON targets(enabled,id);
        CREATE INDEX IF NOT EXISTS logs_target_checked ON monitor_logs(target_id,checked_at);
        CREATE INDEX IF NOT EXISTS devices_user ON devices(user_id);
        CREATE INDEX IF NOT EXISTS push_pending ON push_deliveries(status,next_attempt,id);
        CREATE INDEX IF NOT EXISTS notifications_created ON notifications(created_at);
    """)
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
            "sms_targets": "",
            "email_targets": "",
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
