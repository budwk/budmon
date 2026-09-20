"""Durable per-device APNs outbox, processed independently of website probes."""
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
import httpx
from jose import jwt
from .database import get_db

logger = logging.getLogger("budmon.push")
_lock = threading.Lock()
# Coordinates token ownership changes with in-flight dispatch in the single server process.
device_lock = threading.RLock()
_cached_token = (0, "")
_token_lock = threading.Lock()


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        return min(maximum, max(minimum, int(os.getenv(name, str(default)))))
    except ValueError:
        return default


def configured():
    return all(os.getenv(k) for k in ("APNS_KEY_ID", "APNS_TEAM_ID", "APNS_BUNDLE_ID", "APNS_KEY_PATH")) and Path(os.getenv("APNS_KEY_PATH", "/nonexistent")).is_file()


def provider_token():
    global _cached_token
    now = int(time.time())
    with _token_lock:
        if now - _cached_token[0] > 3000:
            _cached_token = (now, jwt.encode({"iss": os.environ["APNS_TEAM_ID"], "iat": now},
                Path(os.environ["APNS_KEY_PATH"]).read_text(), algorithm="ES256", headers={"kid": os.environ["APNS_KEY_ID"]}))
        return _cached_token[1]


def _invalidate_provider_token(token):
    global _cached_token
    with _token_lock:
        if _cached_token[1] == token:
            _cached_token = (0, "")


def enqueue(target, kind, params):
    title = {"service_down":"网站异常", "service_recovered":"服务已恢复", "cert_expiring":"证书到期提醒", "cert_invalid":"证书异常"}[kind]
    body = f"{target['name']}：" + {
        "service_down":"连续检测失败，请及时检查服务。",
        "service_recovered":"网站已恢复正常访问。",
        "cert_expiring":f"SSL 证书将在 {params.get('day', 0)} 天内到期。",
        "cert_invalid":"SSL 证书验证失败或无法读取，请检查证书。",
    }[kind]
    with get_db() as db:
        current = db.execute("SELECT owner_id FROM targets WHERE id=?",(target["id"],)).fetchone()
        if not current:
            return
        nid = db.execute("INSERT INTO notifications(user_id,target_id,kind,title,body) VALUES(?,?,?,?,?)", (current["owner_id"],target["id"],kind,title,body)).lastrowid
        db.execute("INSERT INTO push_deliveries(notification_id,device_id) SELECT ?,d.id FROM devices d JOIN users u ON u.id=d.user_id WHERE d.user_id=? AND u.push_enabled=1 AND u.disabled=0", (nid,current["owner_id"]))
        if kind == "service_down":
            db.execute("UPDATE targets SET service_alert_failure_count=1 WHERE id=?",(target["id"],))
        elif kind in {"cert_expiring", "cert_invalid"}:
            today = datetime.now(timezone.utc).date().isoformat()
            db.execute("UPDATE targets SET cert_alert_date=? WHERE id=?", (today, target["id"]))


def _send_delivery(client, row, token):
    state, error = "pending", None
    delete_device = False
    try:
        host = "api.sandbox.push.apple.com" if row["environment"] == "sandbox" else "api.push.apple.com"
        for auth_attempt in range(2):
            response = client.post(f"https://{host}/3/device/{row['token']}", headers={
                "authorization":"bearer " + token, "apns-topic":os.environ["APNS_BUNDLE_ID"],
                "apns-push-type":"alert", "apns-priority":"10", "apns-expiration":str(int(time.time())+3600),
                "apns-collapse-id":str(row["notification_id"]),
            }, json={"aps":{"alert":{"title":row["title"],"body":row["body"]},"sound":"default","thread-id":f"target-{row['target_id']}"},
                     "target_id":row["target_id"], "notification_id":row["notification_id"]})
            try:
                reason = response.json().get("reason", "")
            except (TypeError, ValueError):
                reason = ""
            if response.status_code == 403 and reason == "ExpiredProviderToken" and auth_attempt == 0:
                _invalidate_provider_token(token)
                token = provider_token()
                continue
            break
        if response.status_code == 200:
            state = "sent"
        else:
            error = response.text[:300]
            if response.status_code == 410 or reason in {"BadDeviceToken", "DeviceTokenNotForTopic", "Unregistered"}:
                delete_device = True
            elif response.status_code not in {429, 500, 503}:
                state = "failed"
    except Exception as exc:
        error = str(exc)[:300]
    attempts = row["attempts"] + 1
    if attempts >= 10 and state == "pending":
        state = "failed"
    return row, state, error, attempts, delete_device


def drain_push():
    if not configured() or not _lock.acquire(False):
        return
    try:
        batch_size = _env_int("BUDMON_PUSH_BATCH_SIZE", 500, 10, 500)
        workers = _env_int("BUDMON_PUSH_WORKERS", 8, 1, 32)
        with get_db() as db:
            cutoff = datetime.now(timezone.utc) - timedelta(days=1)
            db.execute(
                "UPDATE push_deliveries SET status='expired' WHERE status='pending' AND notification_id IN (SELECT id FROM notifications WHERE created_at < ?)",
                (cutoff,),
            )
            rows = db.execute("""SELECT p.*,n.user_id,n.target_id,n.kind,n.title,n.body,d.token,d.environment
                FROM push_deliveries p JOIN notifications n ON n.id=p.notification_id
                JOIN devices d ON d.id=p.device_id JOIN users u ON u.id=n.user_id
                WHERE p.status='pending' AND p.next_attempt<=? AND d.user_id=n.user_id
                AND u.push_enabled=1 AND u.disabled=0 ORDER BY p.id LIMIT ?""",(int(time.time()), batch_size)).fetchall()
        if not rows:
            return

        # Releasing the mutation lock between small concurrent waves bounds API
        # lock waits to one APNs timeout while preserving account/device ownership.
        with httpx.Client(http2=True, timeout=10) as client:
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="apns") as pool:
                for start in range(0, len(rows), workers):
                    chunk = rows[start:start + workers]
                    with device_lock:
                        placeholders = ",".join("?" for _ in chunk)
                        with get_db() as db:
                            valid_ids = {
                                row["id"] for row in db.execute(
                                    f"""SELECT p.id FROM push_deliveries p
                                        JOIN notifications n ON n.id=p.notification_id
                                        JOIN devices d ON d.id=p.device_id
                                        JOIN users u ON u.id=d.user_id
                                        WHERE p.id IN ({placeholders}) AND p.status='pending'
                                        AND d.user_id=n.user_id AND u.disabled=0 AND u.push_enabled=1""",
                                    [row["id"] for row in chunk],
                                ).fetchall()
                            }
                            for row in chunk:
                                if row["id"] not in valid_ids:
                                    db.execute("UPDATE push_deliveries SET status='expired' WHERE id=? AND status='pending'", (row["id"],))

                        valid_rows = [row for row in chunk if row["id"] in valid_ids]
                        if not valid_rows:
                            continue
                        token = provider_token()
                        results = list(pool.map(lambda row: _send_delivery(client, row, token), valid_rows))

                        now = int(time.time())
                        with get_db() as db:
                            for row, state, error, attempts, delete_device in results:
                                if delete_device:
                                    db.execute(
                                        "DELETE FROM devices WHERE id=? AND user_id=? AND token=?",
                                        (row["device_id"], row["user_id"], row["token"]),
                                    )
                                else:
                                    db.execute(
                                        "UPDATE push_deliveries SET status=?,last_error=?,attempts=?,next_attempt=? WHERE id=? AND status='pending'",
                                        (state, error, attempts, now + min(3600, 2**attempts*5), row["id"]),
                                    )
                                if error:
                                    logger.warning("APNs delivery %s: %s", row["id"], error)
    except Exception:
        logger.exception("推送任务失败")
    finally:
        _lock.release()
