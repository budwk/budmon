from __future__ import annotations

import logging
import secrets
import sqlite3
import time
from urllib.parse import urlsplit
import ipaddress
from datetime import datetime, timezone
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, status, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, HttpUrl, field_validator

from .database import default_settings, get_db, get_setting, init_db, is_installed, merge_defaults, set_setting, IntegrityError
from . import billing
from .push import device_lock
from .monitor import reload_scheduler, run_check_once, start_scheduler, stop_scheduler
from .security import create_session, digest, hash_password, read_token, verify_password, session_tokens

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
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

LOCAL_TZ = ZoneInfo("Asia/Shanghai")
TIME_FIELDS = {
    "checked_at",
    "created_at",
    "updated_at",
    "last_login_at",
    "purchased_at",
    "last_checked_at",
    "last_cert_checked_at",
}


def _to_local_time(value):
    if not value:
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")
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
    username: str = Field(min_length=3, max_length=32, pattern=r"^[a-zA-Z0-9_.-]+$")
    password: str = Field(min_length=8, max_length=72)

    @field_validator("password")
    @classmethod
    def password_bytes(cls, value):
        if len(value.encode()) > 72:
            raise ValueError("密码最多 72 字节")
        return value


class LoginIn(BaseModel):
    username: str = Field(max_length=32)
    password: str = Field(max_length=128)


class ResetPasswordIn(BaseModel):
    old_password: str
    new_password: str = Field(min_length=8, max_length=72)

    @field_validator("new_password")
    @classmethod
    def password_bytes(cls, value):
        return InstallIn.password_bytes(value)


class TargetIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    url: HttpUrl
    enabled: bool = True

    @field_validator("url")
    @classmethod
    def public_url(cls, value):
        parsed = urlsplit(str(value))
        if parsed.username or parsed.password or parsed.hostname == "localhost":
            raise ValueError("请输入公网网址，不能包含账号密码")
        try:
            address = ipaddress.ip_address(parsed.hostname)
        except ValueError:
            return value
        if not address.is_global:
            raise ValueError("不支持内网或保留 IP")
        return value


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


def record_login(db, user_id, kind):
    db.execute("UPDATE users SET last_login_at=CURRENT_TIMESTAMP WHERE id=?", (user_id,))
    db.execute("INSERT INTO login_events(user_id,username,kind) SELECT id,username,? FROM users WHERE id=?", (kind, user_id))
    billing.account_token(db, user_id)


def rate_limit(key, limit=20, period=60):
    now = int(time.time()) // period
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute('DELETE FROM rate_limits WHERE "window" < ?', (now-1,))
        row = db.execute("SELECT * FROM rate_limits WHERE key=?", (key,)).fetchone()
        count = row["count"] if row and row["window"] == now else 0
        if count >= limit:
            raise HTTPException(429, "操作过于频繁，请稍后重试")
        db.execute(
            """
            INSERT INTO rate_limits (key, "window", count)
            VALUES (?, ?, ?)
            ON CONFLICT (key) DO UPDATE
            SET "window" = EXCLUDED."window", count = EXCLUDED.count
            """,
            (key, now, count + 1),
        )


def require_user(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(auth_scheme)]):
    claims = read_token(credentials.credentials) if credentials else None
    if not claims:
        raise HTTPException(401, "登录已过期，请重新登录")
    with get_db() as db:
        row = db.execute("SELECT u.* FROM users u JOIN sessions s ON s.user_id=u.id WHERE u.id=? AND s.id=? AND s.expires_at>? AND u.disabled=0",
            (claims.get("sub"), claims.get("sid"), int(time.time()))).fetchone()
    if not row:
        raise HTTPException(401, "登录已失效")
    return dict(row) | {"session_id": claims["sid"]}


def require_admin(user: Annotated[dict, Depends(require_user)]):
    if user["role"] != "admin":
        raise HTTPException(403, "仅管理员可操作")
    return user


User = Annotated[dict, Depends(require_user)]
Admin = Annotated[dict, Depends(require_admin)]


def quota(db, user):
    plan_id = user["plan_id"]
    if user["plan_expires_at"] and user["plan_expires_at"] <= datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"):
        plan_id = "free"
    plan = db.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()
    limit = user["quota_override"] if user["quota_override"] is not None else plan["target_limit"]
    purchased = db.execute("SELECT COALESCE(SUM(quantity),0) FROM purchases WHERE user_id=? AND status='credited'", (user["id"],)).fetchone()[0]
    limit += purchased
    used = db.execute("SELECT COUNT(*) FROM targets WHERE owner_id=?", (user["id"],)).fetchone()[0]
    return {"plan_id": plan_id, "plan_name": plan["name"], "target_limit": limit, "target_used": used,
            "plan_expires_at": user["plan_expires_at"], "purchased_quota": purchased}


def owned_target(db, target_id, user):
    row = db.execute("SELECT * FROM targets WHERE id=? AND owner_id=?", (target_id,user["id"])).fetchone()
    if not row:
        raise HTTPException(404, "监控目标不存在")
    return row


@app.on_event("startup")
def on_startup():
    init_db()
    start_scheduler()


@app.on_event("shutdown")
def on_shutdown():
    stop_scheduler()


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/install/status")
def install_status():
    return {"installed": is_installed()}


@app.post("/api/install")
def install(payload: InstallIn, request: Request):
    rate_limit("auth:" + request.client.host)
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            raise HTTPException(409, "系统已初始化")
        user_id = db.execute("INSERT INTO users(username,password_hash,role) VALUES(?,?,'admin')",
            (payload.username.lower(),hash_password(payload.password))).lastrowid
        db.execute("UPDATE targets SET owner_id=? WHERE owner_id IS NULL", (user_id,))
        record_login(db, user_id, "register")
        tokens = create_session(db,user_id)
    for key, value in default_settings().items():
        set_setting(key, value)
    reload_scheduler()
    return tokens


@app.post("/api/auth/register", status_code=201)
def register(payload: InstallIn, request: Request):
    rate_limit("auth:" + request.client.host)
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        if not db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            raise HTTPException(409, "请先在网页完成管理员初始化")
        try:
            user_id = db.execute("INSERT INTO users(username,password_hash) VALUES(?,?)",
                (payload.username.lower(),hash_password(payload.password))).lastrowid
        except IntegrityError:
            raise HTTPException(409, "账号已存在")
        record_login(db, user_id, "register")
        return create_session(db,user_id)


@app.post("/api/auth/login")
def login(payload: LoginIn, request: Request):
    rate_limit("auth:" + request.client.host)
    with get_db() as db:
        user = db.execute("SELECT * FROM users WHERE lower(username)=?", (payload.username.lower(),)).fetchone()
        if not user or user["disabled"] or not verify_password(payload.password,user["password_hash"]):
            raise HTTPException(401, "用户名或密码错误")
        record_login(db, user["id"], "login")
        return create_session(db,user["id"])


class RefreshIn(BaseModel):
    refresh_token: str = Field(max_length=200)


@app.post("/api/auth/refresh")
def refresh(payload: RefreshIn, request: Request):
    rate_limit("auth:" + request.client.host)
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        session = db.execute("SELECT s.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE refresh_hash=? AND expires_at>? AND u.disabled=0",
            (digest(payload.refresh_token),int(time.time()))).fetchone()
        if not session:
            raise HTTPException(401, "请重新登录")
        db.execute("DELETE FROM sessions WHERE id=?", (session["id"],))
        return create_session(db,session["user_id"])


@app.post("/api/auth/logout")
def logout(user: User):
    with get_db() as db:
        db.execute("DELETE FROM sessions WHERE id=?", (user["session_id"],))
    return {"ok": True}


@app.post("/api/auth/reset-password")
def reset_password(payload: ResetPasswordIn, user: User):
    rate_limit("password:"+str(user["id"]),5)
    if not verify_password(payload.old_password,user["password_hash"]):
        raise HTTPException(400, "原密码错误")
    with device_lock, get_db() as db:
        db.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(payload.new_password),user["id"]))
        db.execute("DELETE FROM sessions WHERE user_id=?", (user["id"],))
        db.execute("DELETE FROM devices WHERE user_id=?", (user["id"],))
    return {"ok": True}


@app.get("/api/me")
def me(user: User):
    from .push import configured
    with get_db() as db:
        return {"id":user["id"], "username":user["username"], "role":user["role"],
                "push_enabled": bool(user["push_enabled"]), "push_configured": configured(), **quota(db,user)}


class PreferencesIn(BaseModel):
    push_enabled: bool


@app.put("/api/me/preferences")
def preferences(payload: PreferencesIn, user: User):
    with device_lock, get_db() as db:
        db.execute("UPDATE users SET push_enabled=? WHERE id=?", (int(payload.push_enabled),user["id"]))
    return {"ok": True}


@app.delete("/api/me")
def delete_account(user: User):
    if user["role"] == "admin":
        raise HTTPException(409,"管理员账号不可自助删除")
    with device_lock, get_db() as db:
        db.execute("DELETE FROM targets WHERE owner_id=?", (user["id"],))
        db.execute("UPDATE billing_events SET user_id=NULL WHERE user_id=?", (user["id"],))
        db.execute("DELETE FROM users WHERE id=?", (user["id"],))
    return {"ok": True}


@app.get("/api/dashboard")
def dashboard(user: User):
    with get_db() as db:
        counts = db.execute(
            """
            SELECT COUNT(*) total,
                   COALESCE(SUM(CASE WHEN enabled=1 THEN 1 ELSE 0 END), 0) enabled,
                   COALESCE(SUM(CASE WHEN last_status='down' THEN 1 ELSE 0 END), 0) down
            FROM targets WHERE owner_id=?
            """,
            (user["id"],),
        ).fetchone()
        recent = db.execute("SELECT l.*,t.name target_name FROM monitor_logs l JOIN targets t ON t.id=l.target_id WHERE t.owner_id=? ORDER BY l.id DESC LIMIT 20", (user["id"],)).fetchall()
    return dict(counts) | {"recent": [_public_row(row) for row in recent]}


@app.get("/api/targets")
def list_targets(user: User):
    with get_db() as db:
        return [_public_row(row) for row in db.execute("SELECT * FROM targets WHERE owner_id=? ORDER BY id DESC", (user["id"],))]


@app.post("/api/targets", status_code=201)
def create_target(payload: TargetIn, user: User):
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        current = db.execute("SELECT * FROM users WHERE id=?",(user["id"],)).fetchone()
        usage = quota(db,current)
        if usage["target_used"] >= usage["target_limit"]:
            raise HTTPException(403,"监测名额已用完，请在 iOS 我的账户中购买更多名额")
        target_id = db.execute("INSERT INTO targets(owner_id,name,url,enabled) VALUES(?,?,?,?)", (user["id"],payload.name,str(payload.url),int(payload.enabled))).lastrowid
        return _public_row(owned_target(db,target_id,user))


@app.get("/api/targets/{target_id}")
def target_detail(target_id: int, user: User):
    with get_db() as db:
        return _public_row(owned_target(db,target_id,user))


@app.put("/api/targets/{target_id}")
def update_target(target_id: int, payload: TargetIn, user: User):
    with get_db() as db:
        existing = owned_target(db,target_id,user)
        if existing["url"] != str(payload.url):
            db.execute("UPDATE targets SET last_status='unknown',failure_count=0,service_alert_failure_count=0,last_checked_at=NULL,last_code=NULL,last_error=NULL,last_cert_days=NULL,last_cert_expires_at=NULL,last_cert_error=NULL,last_cert_checked_at=NULL,cert_alert_date=NULL WHERE id=?",(target_id,))
        db.execute("UPDATE targets SET name=?,url=?,enabled=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND owner_id=?", (payload.name,str(payload.url),int(payload.enabled),target_id,user["id"]))
        return _public_row(owned_target(db,target_id,user))


@app.delete("/api/targets/{target_id}")
def delete_target(target_id: int, user: User):
    with get_db() as db:
        owned_target(db,target_id,user)
        db.execute("DELETE FROM targets WHERE id=?", (target_id,))
    return {"ok": True}


@app.get("/api/targets/{target_id}/logs")
def target_logs(target_id: int, user: User, before: int | None = Query(None,ge=1), limit: int = Query(20,ge=1,le=100), result: Literal["all", "abnormal"] = "all"):
    threshold = int(merge_defaults(get_setting("monitor", {}), default_settings()["monitor"])["cert_expire_days"])
    with get_db() as db:
        owned_target(db,target_id,user)
        rows = db.execute("""SELECT * FROM monitor_logs WHERE target_id=? AND id<?
            AND (?='all' OR ok=0 OR (event_type='certificate' AND cert_days<=?))
            ORDER BY id DESC LIMIT ?""", (target_id,before or 9223372036854775807,result,threshold,limit)).fetchall()
    return [_public_row(row) for row in rows]


@app.post("/api/monitor/run")
def manual_run(user: User):
    rate_limit("run:"+str(user["id"]),1)
    if not run_check_once(user["id"]):
        raise HTTPException(409,"监测正在进行，请稍后刷新")
    return {"ok": True}


@app.delete("/api/monitor/logs")
def clear_logs(user: User):
    with get_db() as db:
        db.execute("DELETE FROM monitor_logs WHERE target_id IN (SELECT id FROM targets WHERE owner_id=?)", (user["id"],))
    return {"ok": True}


@app.get("/api/notifications")
def notifications(user: User, before: int | None = None, limit: int = Query(50,ge=1,le=100)):
    with get_db() as db:
        return [_public_row(row) for row in db.execute("SELECT * FROM notifications WHERE user_id=? AND id<? ORDER BY id DESC LIMIT ?", (user["id"],before or 9223372036854775807,limit))]


@app.post("/api/notifications/{notification_id}/read")
def read_notification(notification_id: int, user: User):
    with get_db() as db:
        if not db.execute("UPDATE notifications SET read_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=?", (notification_id,user["id"])).rowcount:
            raise HTTPException(404,"通知不存在")
    return {"ok": True}


class DeviceIn(BaseModel):
    token: str = Field(min_length=32,max_length=512,pattern=r"^[0-9a-fA-F]+$")
    environment: str = Field(pattern=r"^(sandbox|production)$")


@app.post("/api/devices")
def register_device(payload: DeviceIn, user: User):
    with device_lock, get_db() as db:
        # Cancel old queued deliveries before rebinding a shared physical device.
        db.execute("DELETE FROM push_deliveries WHERE device_id IN (SELECT id FROM devices WHERE token=? AND environment=? AND user_id!=?)", (payload.token.lower(),payload.environment,user["id"]))
        db.execute("INSERT INTO devices(user_id,token,environment) VALUES(?,?,?) ON CONFLICT(token,environment) DO UPDATE SET user_id=excluded.user_id,updated_at=CURRENT_TIMESTAMP", (user["id"],payload.token.lower(),payload.environment))
    return {"ok": True}


@app.delete("/api/devices")
def unregister_device(payload: DeviceIn, user: User):
    with device_lock, get_db() as db:
        db.execute("DELETE FROM devices WHERE user_id=? AND token=? AND environment=?", (user["id"],payload.token.lower(),payload.environment))
    return {"ok": True}


@app.get("/api/plans")
def plans(user: User):
    with get_db() as db:
        return {"purchase_enabled": billing.configured(), "product_id": billing.PRODUCT_ID, "plans": [dict(row) for row in db.execute("SELECT * FROM plans WHERE enabled=1")]}


@app.get("/api/admin/users")
def users(admin: Admin, offset: int = Query(0,ge=0), limit: int = Query(100,ge=1,le=200)):
    with get_db() as db:
        return [{"id":r["id"],"username":r["username"],"role":r["role"],"disabled":bool(r["disabled"]),"quota_override":r["quota_override"], "created_at":_to_local_time(r["created_at"]), "last_login_at":_to_local_time(r["last_login_at"]), **quota(db,r)} for r in db.execute("SELECT * FROM users ORDER BY id LIMIT ? OFFSET ?",(limit,offset)).fetchall()]


class EntitlementIn(BaseModel):
    plan_id: str = "free"
    quota_override: int | None = Field(None,ge=0,le=10000)
    plan_expires_at: datetime | None = None
    disabled: bool = False


@app.put("/api/admin/users/{user_id}")
def entitlement(user_id: int,payload: EntitlementIn,admin: Admin):
    with device_lock, get_db() as db:
        if not db.execute("SELECT 1 FROM users WHERE id=?",(user_id,)).fetchone():
            raise HTTPException(404,"用户不存在")
        if not db.execute("SELECT 1 FROM plans WHERE id=?",(payload.plan_id,)).fetchone():
            raise HTTPException(400,"套餐不存在")
        if payload.disabled and user_id == admin["id"]:
            raise HTTPException(400,"不能停用当前管理员")
        expiry = payload.plan_expires_at
        if expiry and expiry.tzinfo is None:
            raise HTTPException(400,"到期时间必须包含时区")
        db.execute("UPDATE users SET plan_id=?,quota_override=?,plan_expires_at=?,disabled=? WHERE id=?", (payload.plan_id,payload.quota_override,expiry.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S") if expiry else None,int(payload.disabled),user_id))
        if payload.disabled:
            db.execute("DELETE FROM sessions WHERE user_id=?",(user_id,))
            db.execute("DELETE FROM devices WHERE user_id=?",(user_id,))
        db.execute("INSERT INTO audit_logs(actor_id,subject_id,action) VALUES(?,?,?)",(admin["id"],user_id,payload.model_dump_json()))
    return {"ok": True}


@app.get("/api/settings")
def get_settings(_: Annotated[str, Depends(require_admin)]):
    defaults = default_settings()
    return {
        "monitor": _normalize_monitor_settings(merge_defaults(get_setting("monitor", {}), defaults["monitor"])),
        "sms": merge_defaults(get_setting("sms", {}), defaults["sms"]),
        "email": merge_defaults(get_setting("email", {}), defaults["email"]),
    }


@app.put("/api/settings/monitor")
def save_monitor_settings(payload: MonitorSettingsIn, _: Annotated[str, Depends(require_admin)]):
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
def save_sms_settings(payload: SmsSettingsIn, _: Annotated[str, Depends(require_admin)]):
    set_setting("sms", payload.model_dump())
    return {"ok": True}


@app.put("/api/settings/email")
def save_email_settings(payload: EmailSettingsIn, _: Annotated[str, Depends(require_admin)]):
    set_setting("email", payload.model_dump())
    return {"ok": True}


class PurchaseIn(BaseModel):
    signed_transaction: str = Field(min_length=20, max_length=40000)


@app.get("/api/iap/context")
def purchase_context(user: User):
    with get_db() as db:
        token = billing.account_token(db, user["id"])
    return {"enabled": billing.configured(), "product_id": billing.PRODUCT_ID, "app_account_token": token}


@app.post("/api/iap/transactions")
def purchase_transaction(payload: PurchaseIn, user: User):
    rate_limit("iap:" + str(user["id"]), 120)
    transaction = billing.verify_signed(payload.signed_transaction)
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        purchase_id = billing.apply_transaction(db, transaction, user)
        result = db.execute("SELECT status FROM purchases WHERE id=?", (purchase_id,)).fetchone()
    return {"ok": True, "status": result["status"]}


class AppleNotificationIn(BaseModel):
    signedPayload: str = Field(min_length=20, max_length=80000)


@app.post("/api/iap/notifications")
def apple_notification(payload: AppleNotificationIn):
    notification = billing.verify_signed(payload.signedPayload, notification=True)
    event = getattr(notification.notificationType, "value", notification.notificationType)
    if event in {"ONE_TIME_CHARGE", "REFUND", "REVOKE", "REFUND_REVERSED"}:
        if not notification.data or not notification.data.signedTransactionInfo or not notification.signedDate:
            raise HTTPException(400, "通知缺少交易数据")
        transaction = billing.verify_signed(notification.data.signedTransactionInfo)
        if transaction.environment != notification.data.environment:
            raise HTTPException(400, "交易环境不一致")
        if transaction.productId == billing.PRODUCT_ID:
            with get_db() as db:
                db.execute("BEGIN IMMEDIATE")
                billing.apply_transaction(db, transaction, event=event, event_date=notification.signedDate)
    return {"ok": True}


@app.get("/api/admin/purchases")
def admin_purchases(admin: Admin, user_id: int | None = Query(None, ge=1), offset: int = Query(0,ge=0), limit: int = Query(50,ge=1,le=200)):
    with get_db() as db:
        where, args = (" WHERE user_id=?", [user_id]) if user_id else ("", [])
        total = db.execute("SELECT COUNT(*) FROM purchases" + where, args).fetchone()[0]
        rows = db.execute("SELECT id,user_id,username,environment,transaction_id,product_id,quantity,price_milli,currency,purchased_at,status,created_at,updated_at FROM purchases" + where + " ORDER BY id DESC LIMIT ? OFFSET ?", [*args, limit, offset]).fetchall()
        return {"total": total, "items": [_public_row(row) for row in rows]}


@app.get("/api/admin/logins")
def admin_logins(admin: Admin, user_id: int | None = Query(None, ge=1), offset: int = Query(0,ge=0), limit: int = Query(50,ge=1,le=200)):
    with get_db() as db:
        where, args = (" WHERE user_id=?", [user_id]) if user_id else ("", [])
        total = db.execute("SELECT COUNT(*) FROM login_events" + where, args).fetchone()[0]
        rows = db.execute("SELECT * FROM login_events" + where + " ORDER BY id DESC LIMIT ? OFFSET ?", [*args, limit, offset]).fetchall()
        return {"total": total, "items": [_public_row(row) for row in rows]}
