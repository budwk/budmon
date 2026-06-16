from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, HttpUrl

from .database import default_settings, get_db, get_setting, init_db, is_installed, merge_defaults, set_setting
from .monitor import reload_scheduler, run_check_once, start_scheduler, stop_scheduler
from .security import create_token, hash_password, read_token, verify_password

app = FastAPI(title="BudMon")
auth_scheme = HTTPBearer(auto_error=False)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logging.getLogger("budmon.monitor").setLevel(logging.INFO)
logging.getLogger("budmon.sms").setLevel(logging.INFO)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

LOCAL_TZ = ZoneInfo("Asia/Shanghai")
TIME_FIELDS = {
    "checked_at",
    "created_at",
    "updated_at",
    "last_checked_at",
    "last_cert_checked_at",
}


def _to_local_time(value):
    if not value:
        return value
    if not isinstance(value, str):
        return value
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z"):
        try:
            dt = datetime.strptime(value, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    return value


def _public_row(row) -> dict:
    data = dict(row)
    for field in TIME_FIELDS:
        if field in data:
            data[field] = _to_local_time(data[field])
    return data


def _lines(value: str) -> list[str]:
    return [line.strip() for line in value.splitlines() if line.strip()]


def _normalize_monitor_settings(settings: dict) -> dict:
    if settings.get("sms_targets") or settings.get("email_targets"):
        return settings

    legacy_targets = _lines(settings.get("notify_targets", ""))
    if not legacy_targets:
        return settings

    settings["sms_targets"] = "\n".join(line for line in legacy_targets if "@" not in line)
    settings["email_targets"] = "\n".join(line for line in legacy_targets if "@" in line)
    return settings


class InstallIn(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=6, max_length=128)


class LoginIn(BaseModel):
    username: str
    password: str


class ResetPasswordIn(BaseModel):
    old_password: str
    new_password: str = Field(min_length=6, max_length=128)


class TargetIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    url: HttpUrl
    enabled: bool = True


class MonitorSettingsIn(BaseModel):
    interval_seconds: int = Field(ge=10, le=86400)
    retry_delay_seconds: int = Field(ge=0, le=300)
    cert_expire_days: int = Field(default=5, ge=1, le=365)
    notify_methods: list[str] = Field(default_factory=list)
    notify_targets: str = ""
    sms_targets: str = ""
    email_targets: str = ""


class SmsSettingsIn(BaseModel):
    provider: str = "aliyun"
    aliyun: dict = Field(default_factory=dict)
    tencent: dict = Field(default_factory=dict)
    templates: dict = Field(default_factory=dict)


class EmailSettingsIn(BaseModel):
    host: str = ""
    port: int = Field(default=465, ge=1, le=65535)
    username: str = ""
    password: str = ""
    sender: str = ""


def require_user(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(auth_scheme)]) -> str:
    if not credentials:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "未登录")
    username = read_token(credentials.credentials)
    if not username:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "登录已过期")
    with get_db() as db:
        user = db.execute("SELECT username FROM users WHERE username = ?", (username,)).fetchone()
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户不存在")
    return username


@app.on_event("startup")
def on_startup() -> None:
    init_db()
    start_scheduler()


@app.on_event("shutdown")
def on_shutdown() -> None:
    stop_scheduler()


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/install/status")
def install_status():
    return {"installed": is_installed()}


@app.post("/api/install")
def install(payload: InstallIn):
    if is_installed():
        raise HTTPException(409, "系统已初始化")
    with get_db() as db:
        db.execute(
            "INSERT INTO users(username, password_hash) VALUES(?, ?)",
            (payload.username, hash_password(payload.password)),
        )
    for key, value in default_settings().items():
        set_setting(key, value)
    reload_scheduler()
    return {"token": create_token(payload.username)}


@app.post("/api/auth/login")
def login(payload: LoginIn):
    with get_db() as db:
        user = db.execute("SELECT * FROM users WHERE username = ?", (payload.username,)).fetchone()
    if not user or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户名或密码错误")
    return {"token": create_token(payload.username)}


@app.post("/api/auth/reset-password")
def reset_password(payload: ResetPasswordIn, username: Annotated[str, Depends(require_user)]):
    with get_db() as db:
        user = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if not user or not verify_password(payload.old_password, user["password_hash"]):
            raise HTTPException(400, "原密码错误")
        db.execute(
            "UPDATE users SET password_hash = ? WHERE username = ?",
            (hash_password(payload.new_password), username),
        )
    return {"ok": True}


@app.get("/api/dashboard")
def dashboard(_: Annotated[str, Depends(require_user)]):
    with get_db() as db:
        total = db.execute("SELECT COUNT(*) AS n FROM targets").fetchone()["n"]
        enabled = db.execute("SELECT COUNT(*) AS n FROM targets WHERE enabled = 1").fetchone()["n"]
        down = db.execute("SELECT COUNT(*) AS n FROM targets WHERE last_status = 'down'").fetchone()["n"]
        recent = db.execute(
            """
            SELECT l.*, t.name AS target_name
            FROM monitor_logs l
            JOIN targets t ON t.id = l.target_id
            ORDER BY l.id DESC
            LIMIT 20
            """
        ).fetchall()
    return {
        "total": total,
        "enabled": enabled,
        "down": down,
        "recent": [_public_row(row) for row in recent],
    }


@app.get("/api/targets")
def list_targets(_: Annotated[str, Depends(require_user)]):
    with get_db() as db:
        rows = db.execute("SELECT * FROM targets ORDER BY id DESC").fetchall()
    return [_public_row(row) for row in rows]


@app.post("/api/targets")
def create_target(payload: TargetIn, _: Annotated[str, Depends(require_user)]):
    with get_db() as db:
        cursor = db.execute(
            "INSERT INTO targets(name, url, enabled) VALUES(?, ?, ?)",
            (payload.name, str(payload.url), int(payload.enabled)),
        )
        row = db.execute("SELECT * FROM targets WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return _public_row(row)


@app.put("/api/targets/{target_id}")
def update_target(target_id: int, payload: TargetIn, _: Annotated[str, Depends(require_user)]):
    with get_db() as db:
        db.execute(
            """
            UPDATE targets
            SET name = ?, url = ?, enabled = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (payload.name, str(payload.url), int(payload.enabled), target_id),
        )
        row = db.execute("SELECT * FROM targets WHERE id = ?", (target_id,)).fetchone()
    if not row:
        raise HTTPException(404, "监控目标不存在")
    return _public_row(row)


@app.delete("/api/targets/{target_id}")
def delete_target(target_id: int, _: Annotated[str, Depends(require_user)]):
    with get_db() as db:
        db.execute("DELETE FROM targets WHERE id = ?", (target_id,))
    return {"ok": True}


@app.post("/api/monitor/run")
def manual_run(_: Annotated[str, Depends(require_user)]):
    run_check_once()
    return {"ok": True}


@app.delete("/api/monitor/logs")
def clear_logs(_: Annotated[str, Depends(require_user)]):
    with get_db() as db:
        db.execute("DELETE FROM monitor_logs")
    return {"ok": True}


@app.get("/api/settings")
def get_settings(_: Annotated[str, Depends(require_user)]):
    defaults = default_settings()
    return {
        "monitor": _normalize_monitor_settings(merge_defaults(get_setting("monitor", {}), defaults["monitor"])),
        "sms": merge_defaults(get_setting("sms", {}), defaults["sms"]),
        "email": merge_defaults(get_setting("email", {}), defaults["email"]),
    }


@app.put("/api/settings/monitor")
def save_monitor_settings(payload: MonitorSettingsIn, _: Annotated[str, Depends(require_user)]):
    illegal = set(payload.notify_methods) - {"sms", "email"}
    if illegal:
        raise HTTPException(400, "通知方式只能选择短信或邮箱")
    sms_targets = _lines(payload.sms_targets)
    email_targets = _lines(payload.email_targets)
    if any("@" in target for target in sms_targets):
        raise HTTPException(400, "短信通知目标只能填写手机号")
    if any("@" not in target for target in email_targets):
        raise HTTPException(400, "邮箱通知目标只能填写邮箱地址")

    data = payload.model_dump()
    data["sms_targets"] = "\n".join(sms_targets)
    data["email_targets"] = "\n".join(email_targets)
    data["notify_targets"] = ""
    set_setting("monitor", data)
    reload_scheduler()
    return {"ok": True}


@app.put("/api/settings/sms")
def save_sms_settings(payload: SmsSettingsIn, _: Annotated[str, Depends(require_user)]):
    set_setting("sms", payload.model_dump())
    return {"ok": True}


@app.put("/api/settings/email")
def save_email_settings(payload: EmailSettingsIn, _: Annotated[str, Depends(require_user)]):
    set_setting("email", payload.model_dump())
    return {"ok": True}
