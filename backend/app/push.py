"""Durable per-device APNs outbox, processed independently of website probes."""
import logging
import os
import threading
import time
from pathlib import Path
import httpx
from jose import jwt
from .database import get_db

logger = logging.getLogger("budmon.push")
_lock = threading.Lock()
# Coordinates token ownership changes with in-flight dispatch in the single server process.
device_lock = threading.RLock()
_cached_token = (0, "")


def configured():
    return all(os.getenv(k) for k in ("APNS_KEY_ID", "APNS_TEAM_ID", "APNS_BUNDLE_ID", "APNS_KEY_PATH")) and Path(os.getenv("APNS_KEY_PATH", "/nonexistent")).is_file()


def provider_token():
    global _cached_token
    now = int(time.time())
    if now - _cached_token[0] > 3000:
        _cached_token = (now, jwt.encode({"iss": os.environ["APNS_TEAM_ID"], "iat": now},
            Path(os.environ["APNS_KEY_PATH"]).read_text(), algorithm="ES256", headers={"kid": os.environ["APNS_KEY_ID"]}))
    return _cached_token[1]


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
            db.execute("UPDATE targets SET cert_alert_date=date('now') WHERE id=?",(target["id"],))


def drain_push():
    if not configured() or not _lock.acquire(False):
        return
    try:
        with get_db() as db:
            db.execute("UPDATE push_deliveries SET status='expired' WHERE status='pending' AND notification_id IN (SELECT id FROM notifications WHERE created_at < datetime('now','-1 day'))")
            rows = db.execute("""SELECT p.*,n.user_id,n.target_id,n.kind,n.title,n.body,d.token,d.environment
                FROM push_deliveries p JOIN notifications n ON n.id=p.notification_id
                JOIN devices d ON d.id=p.device_id JOIN users u ON u.id=n.user_id
                WHERE p.status='pending' AND p.next_attempt<=? AND d.user_id=n.user_id
                AND u.push_enabled=1 AND u.disabled=0 ORDER BY p.id LIMIT 100""",(int(time.time()),)).fetchall()
        with httpx.Client(http2=True, timeout=10) as client:
            for row in rows:
                with device_lock:
                    with get_db() as db:
                        valid = db.execute("SELECT 1 FROM push_deliveries p JOIN devices d ON d.id=p.device_id JOIN users u ON u.id=d.user_id WHERE p.id=? AND d.user_id=? AND d.token=? AND u.disabled=0 AND u.push_enabled=1", (row["id"],row["user_id"],row["token"])).fetchone()
                    if not valid:
                        continue
                    state, error = "pending", None
                    try:
                        host = "api.sandbox.push.apple.com" if row["environment"] == "sandbox" else "api.push.apple.com"
                        response = client.post(f"https://{host}/3/device/{row['token']}", headers={
                            "authorization":"bearer " + provider_token(), "apns-topic":os.environ["APNS_BUNDLE_ID"],
                            "apns-push-type":"alert", "apns-priority":"10", "apns-expiration":str(int(time.time())+3600),
                            "apns-collapse-id":str(row["notification_id"]),
                        }, json={"aps":{"alert":{"title":row["title"],"body":row["body"]},"sound":"default","thread-id":f"target-{row['target_id']}"},
                                 "target_id":row["target_id"], "notification_id":row["notification_id"]})
                        if response.status_code == 200:
                            state = "sent"
                        else:
                            error = response.text[:300]
                            reason = response.json().get("reason", "")
                            if response.status_code == 410 or reason in {"BadDeviceToken", "DeviceTokenNotForTopic", "Unregistered"}:
                                with get_db() as db:
                                    db.execute("DELETE FROM devices WHERE id=? AND user_id=? AND token=?", (row["device_id"],row["user_id"],row["token"]))
                                continue
                            if response.status_code not in {429,500,503,403}:
                                state = "failed"
                    except Exception as exc:
                        error = str(exc)[:300]
                    attempts = row["attempts"]+1
                    if attempts >= 10 and state == "pending":
                        state = "failed"
                    with get_db() as db:
                        db.execute("UPDATE push_deliveries SET status=?,last_error=?,attempts=?,next_attempt=? WHERE id=?", (state,error,attempts,int(time.time())+min(3600,2**attempts*5),row["id"]))
                    if error:
                        logger.warning("APNs delivery %s: %s",row["id"],error)
    except Exception:
        logger.exception("推送任务失败")
    finally:
        _lock.release()
