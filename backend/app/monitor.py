from __future__ import annotations

import socket
import smtplib
import ssl
import threading
import time
import logging
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Iterable
from urllib.parse import urlparse

import requests
from apscheduler.schedulers.background import BackgroundScheduler

from .database import default_settings, get_db, get_setting, merge_defaults
from .sms import send_sms

_scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
_lock = threading.Lock()
logger = logging.getLogger("budmon.monitor")


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


def _probe(url: str) -> tuple[bool, int | None, str | None]:
    try:
        response = requests.get(url, timeout=10, allow_redirects=True)
        return response.status_code < 500, response.status_code, None
    except requests.RequestException as exc:
        return False, None, str(exc)


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


def _notify(event_type: str, params: dict[str, str | int]) -> None:
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
        raise RuntimeError("; ".join(errors))


def _certificate_days(url: str) -> tuple[int | None, str | None, str | None]:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        return None, None, None
    try:
        context = ssl.create_default_context()
        with socket.create_connection((parsed.hostname, parsed.port or 443), timeout=10) as sock:
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
    alert_sent_count = int(target["service_alert_failure_count"] or 0)
    if not ok and failure_count >= 2 and alert_sent_count == 0:
        logger.warning("服务连续失败达到阈值，触发告警 target_id=%s name=%s", target["id"], target["name"])
        try:
            _notify("service_down", {"name": target["name"]})
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

    if cert_days is None or cert_days > cert_expire_days or target["cert_alert_date"] == today:
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
        _notify("cert_expiring", {"name": target["name"], "day": cert_days})
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


def run_check_once() -> None:
    if not _lock.acquire(blocking=False):
        logger.warning("上一轮监控尚未结束，本轮跳过")
        return
    started_at = datetime.now().isoformat(timespec="seconds")
    logger.info("监控任务开始 started_at=%s", started_at)
    try:
        monitor_cfg = merge_defaults(get_setting("monitor", {}), default_settings()["monitor"])
        retry_delay = int(monitor_cfg.get("retry_delay_seconds", 5))
        cert_expire_days = int(monitor_cfg.get("cert_expire_days", 5))
        today = datetime.now().date().isoformat()
        with get_db() as db:
            targets = db.execute("SELECT * FROM targets WHERE enabled = 1").fetchall()

        logger.info(
            "监控任务配置 interval=%s retry_delay=%s cert_expire_days=%s enabled_targets=%s notify_methods=%s",
            monitor_cfg.get("interval_seconds"),
            retry_delay,
            cert_expire_days,
            len(targets),
            monitor_cfg.get("notify_methods"),
        )
        for target in targets:
            _check_service(target, retry_delay)
            _check_certificate(target, cert_expire_days, today)
        logger.info("监控任务结束 started_at=%s target_count=%s", started_at, len(targets))
    except Exception:
        logger.exception("监控任务异常")
    finally:
        _lock.release()


def start_scheduler() -> None:
    if not _scheduler.running:
        _scheduler.start()
        logger.info("监控调度器已启动")
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
