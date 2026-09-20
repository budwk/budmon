from __future__ import annotations

import os
import socket
import smtplib
import ssl
import threading
import time
import logging
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Iterable
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

from apscheduler.schedulers.background import BackgroundScheduler

from .database import default_settings, get_db, get_setting, merge_defaults
from .sms import send_sms
from .network import probe, resolve_public
from .push import enqueue, drain_push
from .retention import prune_history

_scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
_lock = threading.Lock()
logger = logging.getLogger("budmon.monitor")


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        return min(maximum, max(minimum, int(os.getenv(name, str(default)))))
    except ValueError:
        return default


def _lines(value: str) -> list[str]:
    return [line.strip() for line in value.splitlines() if line.strip()]


def _sms_targets(lines: Iterable[str]) -> list[str]:
    return [line for line in lines if "@" not in line]


def _mail_targets(lines: Iterable[str]) -> list[str]:
    return [line for line in lines if "@" in line]


def _configured_targets(monitor_cfg: dict) -> tuple[list[str], list[str]]:
    sms_targets = _lines(monitor_cfg.get("sms_targets", ""))
    email_targets = _lines(monitor_cfg.get("email_targets", ""))
    if sms_targets or email_targets:
        return sms_targets, email_targets

    legacy_targets = _lines(monitor_cfg.get("notify_targets", ""))
    return _sms_targets(legacy_targets), _mail_targets(legacy_targets)


_probe = probe

def _render(template: str, params: dict[str, str | int]) -> str:
    rendered = template
    for key, value in params.items():
        rendered = rendered.replace("${" + key + "}", str(value))
        rendered = rendered.replace("{" + key + "}", str(value))
    return rendered


def _template_content(event_type: str) -> str:
    sms_cfg = merge_defaults(get_setting("sms", {}), default_settings()["sms"])
    templates = sms_cfg.get("templates", {})
    template = templates.get(event_type, {})
    if template.get("content"):
        return template["content"]
    if event_type == "cert_expiring":
        return "温馨提醒：您的 ${name} 证书剩余 ${day} 天过期，请及时关注。"
    return "温馨提醒：您的 ${name} 服务已断连，请及时关注。"


def _send_email(addresses: list[str], event_type: str, params: dict[str, str | int]) -> None:
    smtp_cfg = get_setting("email", {})
    host = smtp_cfg.get("host")
    username = smtp_cfg.get("username")
    password = smtp_cfg.get("password")
    sender = smtp_cfg.get("sender") or username
    if not all([host, username, password, sender]):
        raise RuntimeError("邮件配置不完整")

    subject = f"{params['name']} 证书即将过期" if event_type == "cert_expiring" else f"{params['name']} 服务已断连"
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(addresses)
    msg.set_content(_render(_template_content(event_type), params))
    with smtplib.SMTP_SSL(host, int(smtp_cfg.get("port", 465)), timeout=15) as smtp:
        smtp.login(username, password)
        smtp.send_message(msg)


def _notify(event_type: str, params: dict[str, str | int], target) -> None:
    enqueue(target, event_type, params)
    # Legacy global SMS/email recipients only receive the administrator's targets.
    with get_db() as db:
        owner = db.execute("SELECT role FROM users WHERE id=?", (target["owner_id"],)).fetchone()
    if not owner or owner["role"] != "admin" or event_type not in {"service_down", "cert_expiring"}:
        return
    defaults = default_settings()
    monitor_cfg = merge_defaults(get_setting("monitor", {}), defaults["monitor"])
    sms_cfg = merge_defaults(get_setting("sms", {}), defaults["sms"])
    methods = set(monitor_cfg.get("notify_methods", []))
    phones, mails = _configured_targets(monitor_cfg)
    logger.info(
        "准备通知 event=%s methods=%s sms_target_count=%s email_target_count=%s params=%s",
        event_type,
        sorted(methods),
        len(phones),
        len(mails),
        params,
    )
    if not methods:
        logger.warning("未配置通知方式，跳过通知 event=%s", event_type)
        return
    selected_target_count = (len(phones) if "sms" in methods else 0) + (len(mails) if "email" in methods else 0)
    if selected_target_count == 0:
        logger.warning("未配置通知目标，跳过通知 event=%s", event_type)
        return
    errors: list[str] = []
    if "sms" in methods:
        logger.info("短信通知目标数量=%s event=%s", len(phones), event_type)
        for phone in phones:
            try:
                send_sms(sms_cfg, phone, event_type, params)
            except Exception as exc:
                logger.exception("短信通知失败 phone=%s event=%s", phone, event_type)
                errors.append(f"短信 {phone}: {exc}")
    if "email" in methods:
        logger.info("邮件通知目标数量=%s event=%s", len(mails), event_type)
        if mails:
            try:
                _send_email(mails, event_type, params)
                logger.info("邮件通知发送完成 event=%s targets=%s", event_type, mails)
            except Exception as exc:
                logger.exception("邮件通知失败 event=%s", event_type)
                errors.append(f"邮件: {exc}")
    if errors:
        logger.error("Legacy notification failure: %s", "; ".join(errors))


def _certificate_days(url: str) -> tuple[int | None, str | None, str | None]:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        return None, None, None
    try:
        parsed, address, port = resolve_public(url)
        context = ssl.create_default_context()
        with socket.create_connection((address, port), timeout=10) as sock:
            with context.wrap_socket(sock, server_hostname=parsed.hostname) as ssock:
                cert = ssock.getpeercert()
        expires = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
        seconds = (expires - datetime.now(timezone.utc)).total_seconds()
        return max(int(seconds // 86400), 0), expires.isoformat(), None
    except Exception as exc:
        return None, None, str(exc)


def _check_service(target, retry_delay: int) -> None:
    logger.info("开始服务检测 target_id=%s name=%s url=%s", target["id"], target["name"], target["url"])
    ok, status_code, error = _probe(target["url"])
    logger.info(
        "服务检测结果 target_id=%s ok=%s status_code=%s error=%s",
        target["id"],
        ok,
        status_code,
        error,
    )
    if not ok:
        logger.warning("服务检测失败，%s 秒后重试 target_id=%s", max(retry_delay, 0), target["id"])
        time.sleep(max(retry_delay, 0))
        ok, status_code, error = _probe(target["url"])
        logger.info(
            "服务重试结果 target_id=%s ok=%s status_code=%s error=%s",
            target["id"],
            ok,
            status_code,
            error,
        )

    with get_db() as db:
        live = db.execute("SELECT url,enabled FROM targets WHERE id=?",(target["id"],)).fetchone()
        if not live or live["url"] != target["url"] or not live["enabled"]:
            return
        old_count = int(target["failure_count"] or 0)
        failure_count = 0 if ok else old_count + 1
        status = "up" if ok else "down"
        db.execute(
            """
            UPDATE targets
            SET failure_count = ?, last_status = ?, last_code = ?,
                last_error = ?, last_checked_at = CURRENT_TIMESTAMP,
                service_alert_failure_count = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                failure_count,
                status,
                status_code,
                error,
                0 if ok else int(target["service_alert_failure_count"] or 0),
                target["id"],
            ),
        )
        db.execute(
            """
            INSERT INTO monitor_logs(target_id, event_type, ok, status_code, error)
            VALUES(?, 'service', ?, ?, ?)
            """,
            (target["id"], int(ok), status_code, error),
        )

    logger.info(
        "服务检测入库 target_id=%s status=%s old_failure=%s new_failure=%s",
        target["id"],
        status,
        int(target["failure_count"] or 0),
        failure_count,
    )
    if ok and target["service_alert_failure_count"]:
        _notify("service_recovered", {"name": target["name"]}, target)
    alert_sent_count = int(target["service_alert_failure_count"] or 0)
    if not ok and failure_count >= 2 and alert_sent_count == 0:
        logger.warning("服务连续失败达到阈值，触发告警 target_id=%s name=%s", target["id"], target["name"])
        try:
            _notify("service_down", {"name": target["name"]}, target)
            with get_db() as db:
                db.execute(
                    "UPDATE targets SET service_alert_failure_count = ? WHERE id = ?",
                    (failure_count, target["id"]),
                )
            logger.info("服务断连告警发送完成 target_id=%s", target["id"])
        except Exception as exc:
            logger.exception("服务断连告警发送失败 target_id=%s", target["id"])
            with get_db() as db:
                db.execute(
                    "UPDATE targets SET last_error = ? WHERE id = ?",
                    (f"{error or '访问失败'}; 告警失败: {exc}", target["id"]),
                )


def _check_certificate(target, cert_expire_days: int, today: str) -> None:
    if not target["url"].startswith("https://"):
        return
    logger.info("开始证书检测 target_id=%s name=%s url=%s", target["id"], target["name"], target["url"])
    cert_days, cert_expires_at, cert_error = _certificate_days(target["url"])
    logger.info(
        "证书检测结果 target_id=%s days=%s expires_at=%s error=%s",
        target["id"],
        cert_days,
        cert_expires_at,
        cert_error,
    )
    with get_db() as db:
        live = db.execute("SELECT url,enabled FROM targets WHERE id=?",(target["id"],)).fetchone()
        if not live or live["url"] != target["url"] or not live["enabled"]:
            return
        db.execute(
            """
            UPDATE targets
            SET last_cert_days = ?, last_cert_expires_at = ?,
                last_cert_error = ?, last_cert_checked_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (cert_days, cert_expires_at, cert_error, target["id"]),
        )
        db.execute(
            """
            INSERT INTO monitor_logs(target_id, event_type, ok, cert_days, error)
            VALUES(?, 'certificate', ?, ?, ?)
            """,
            (target["id"], int(cert_error is None), cert_days, cert_error),
        )

    if (not cert_error and (cert_days is None or cert_days > cert_expire_days)) or target["cert_alert_date"] == today:
        logger.info(
            "证书告警未触发 target_id=%s days=%s threshold=%s already_alerted_today=%s",
            target["id"],
            cert_days,
            cert_expire_days,
            target["cert_alert_date"] == today,
        )
        return
    logger.warning(
        "证书剩余天数达到阈值，触发告警 target_id=%s name=%s days=%s threshold=%s",
        target["id"],
        target["name"],
        cert_days,
        cert_expire_days,
    )
    try:
        _notify("cert_invalid" if cert_error else "cert_expiring", {"name": target["name"], "day": cert_days}, target)
        with get_db() as db:
            db.execute("UPDATE targets SET cert_alert_date = ? WHERE id = ?", (today, target["id"]))
        logger.info("证书告警发送完成 target_id=%s", target["id"])
    except Exception as exc:
        logger.exception("证书告警发送失败 target_id=%s", target["id"])
        with get_db() as db:
            db.execute(
                "UPDATE targets SET last_cert_error = ? WHERE id = ?",
                (f"{cert_error or '证书即将过期'}; 告警失败: {exc}", target["id"]),
            )


def _certificate_check_due(value, interval_seconds: int) -> bool:
    if not value:
        return True
    try:
        checked_at = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if checked_at.tzinfo is None:
            checked_at = checked_at.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - checked_at.astimezone(timezone.utc)).total_seconds() >= interval_seconds
    except (TypeError, ValueError):
        return True


def run_check_once(owner_id=None) -> bool:
    if not _lock.acquire(blocking=False):
        logger.warning("上一轮监控尚未结束，本轮跳过")
        return False
    started_monotonic = time.monotonic()
    started_at = datetime.now().isoformat(timespec="seconds")
    logger.info("监控任务开始 started_at=%s", started_at)
    try:
        monitor_cfg = merge_defaults(get_setting("monitor", {}), default_settings()["monitor"])
        retry_delay = int(monitor_cfg.get("retry_delay_seconds", 5))
        cert_expire_days = int(monitor_cfg.get("cert_expire_days", 5))
        interval_seconds = max(int(monitor_cfg.get("interval_seconds", 60)), 10)
        batch_size = _env_int("BUDMON_MONITOR_BATCH_SIZE", 250, 10, 2000)
        workers = _env_int("BUDMON_MONITOR_WORKERS", 16, 1, 64)
        cert_interval = _env_int("BUDMON_CERT_CHECK_INTERVAL_SECONDS", 21600, 300, 86400)
        today = datetime.now(timezone.utc).date().isoformat()
        logger.info(
            "监控任务配置 interval=%s retry_delay=%s cert_expire_days=%s cert_interval=%s batch_size=%s workers=%s notify_methods=%s",
            interval_seconds,
            retry_delay,
            cert_expire_days,
            cert_interval,
            batch_size,
            workers,
            monitor_cfg.get("notify_methods"),
        )

        def check(target):
            try:
                _check_service(target, retry_delay)
                if owner_id is not None or _certificate_check_due(target["last_cert_checked_at"], cert_interval):
                    _check_certificate(target, cert_expire_days, today)
            except Exception:
                logger.exception("目标检测失败 target_id=%s", target["id"])

        target_count = 0
        last_id = 0
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="probe") as pool:
            while True:
                sql = (
                    "SELECT t.* FROM targets t "
                    "JOIN users u ON u.id=t.owner_id "
                    "WHERE t.enabled=1 AND u.disabled=0 AND t.id>?"
                )
                params = [last_id]
                if owner_id is not None:
                    sql += " AND t.owner_id=?"
                    params.append(owner_id)
                sql += " ORDER BY t.id LIMIT ?"
                params.append(batch_size)
                with get_db() as db:
                    targets = db.execute(sql, params).fetchall()
                if not targets:
                    break
                list(pool.map(check, targets))
                target_count += len(targets)
                last_id = targets[-1]["id"]

        duration = time.monotonic() - started_monotonic
        log = logger.warning if owner_id is None and duration > interval_seconds else logger.info
        log(
            "监控任务结束 started_at=%s target_count=%s duration_seconds=%.2f interval_seconds=%s",
            started_at,
            target_count,
            duration,
            interval_seconds,
        )
    except Exception:
        logger.exception("监控任务异常")
    finally:
        _lock.release()
    return True


def start_scheduler() -> None:
    if not _scheduler.running:
        _scheduler.start()
        logger.info("监控调度器已启动")
    push_interval = _env_int("BUDMON_PUSH_INTERVAL_SECONDS", 2, 1, 60)
    retention_interval = _env_int("BUDMON_RETENTION_INTERVAL_MINUTES", 15, 1, 1440)
    _scheduler.add_job(drain_push, "interval", seconds=push_interval, id="push-outbox", replace_existing=True, max_instances=1)
    _scheduler.add_job(prune_history, "interval", minutes=retention_interval, id="history-retention", replace_existing=True, max_instances=1, next_run_time=datetime.now())
    reload_scheduler()


def reload_scheduler() -> None:
    monitor_cfg = merge_defaults(get_setting("monitor", {}), default_settings()["monitor"])
    interval = max(int(monitor_cfg.get("interval_seconds", 60)), 10)
    if _scheduler.get_job("monitor-check"):
        _scheduler.remove_job("monitor-check")
    _scheduler.add_job(
        run_check_once,
        "interval",
        seconds=interval,
        id="monitor-check",
        replace_existing=True,
        max_instances=1,
        next_run_time=datetime.now(),
    )
    job = _scheduler.get_job("monitor-check")
    logger.info("监控调度任务已加载 interval=%s next_run_time=%s", interval, job.next_run_time if job else None)


def stop_scheduler() -> None:
    if _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("监控调度器已停止")
