"""Apple-verified, account-bound consumable credits. Never trust client quantities."""
import os
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from uuid import UUID

from fastapi import HTTPException
from appstoreserverlibrary.models.Environment import Environment
from appstoreserverlibrary.signed_data_verifier import SignedDataVerifier, VerificationException, VerificationStatus

PRODUCT_ID = "com.budwk.app.budmon.1"


def configured():
    return os.getenv("APPLE_APP_ID", "").isdigit() and int(os.getenv("APPLE_APP_ID", "0")) > 0


@lru_cache(maxsize=4)
def _verifier(environment, bundle_id, app_id):
    roots = [(Path(__file__).parent / "certs" / "AppleRootCA-G3.cer").read_bytes()]
    return SignedDataVerifier(roots, True, environment, bundle_id, app_id)


def verify_signed(value, notification=False):
    if not configured():
        raise HTTPException(503, "内购尚未配置，请设置 APPLE_APP_ID")
    environments = [Environment.PRODUCTION]
    if os.getenv("APPLE_ALLOW_SANDBOX", "0") == "1":
        environments.append(Environment.SANDBOX)
    # An environment hint in a client token is not a trust decision.
    for environment in environments:
        verifier = _verifier(environment, os.getenv("APPLE_BUNDLE_ID", "com.budwk.app.budmon"), int(os.environ["APPLE_APP_ID"]))
        try:
            return (verifier.verify_and_decode_notification(value) if notification
                    else verifier.verify_and_decode_signed_transaction(value))
        except VerificationException as exc:
            if exc.status == VerificationStatus.RETRYABLE_VERIFICATION_FAILURE:
                raise HTTPException(503, "Apple 验证暂不可用，请稍后重试") from exc
            if exc.status != VerificationStatus.INVALID_ENVIRONMENT:
                raise HTTPException(400, "Apple 签名或应用信息验证失败") from exc
    raise HTTPException(400, "不接受此交易环境")


def account_token(db, user_id):
    # Existing users are backfilled by migration; new accounts receive a UUID here.
    from uuid import uuid4
    db.execute("UPDATE users SET app_account_token=? WHERE id=? AND app_account_token IS NULL", (str(uuid4()), user_id))
    return db.execute("SELECT app_account_token FROM users WHERE id=?", (user_id,)).fetchone()[0]


def apply_transaction(db, transaction, user=None, event=None, event_date=0):
    env = getattr(transaction.environment, "value", transaction.environment)
    kind = getattr(transaction.type, "value", transaction.type)
    if transaction.productId != PRODUCT_ID or kind != "Consumable" or env not in {"Production", "Sandbox"}:
        raise HTTPException(400, "不是支持的监测名额商品")
    try:
        token = str(UUID(str(transaction.appAccountToken)))
        quantity = transaction.quantity
        if type(quantity) is not int or quantity < 1 or quantity > 2147483647:
            raise ValueError()
        if not transaction.transactionId or not transaction.signedDate or not transaction.purchaseDate:
            raise ValueError()
        purchased_at = datetime.fromtimestamp(transaction.purchaseDate / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError, OverflowError):
        raise HTTPException(400, "交易缺少有效的账号、数量或时间")
    owner = db.execute("SELECT * FROM users WHERE app_account_token=?", (token,)).fetchone()
    if user is not None and (not owner or owner["id"] != user["id"]):
        raise HTTPException(403, "此购买属于其他 BudMon 账号，请登录购买时的账号")
    existing = db.execute("SELECT * FROM purchases WHERE environment=? AND transaction_id=?", (env, transaction.transactionId)).fetchone()
    if existing and existing["app_account_token"] != token:
        raise HTTPException(409, "交易账号不一致")
    revoked = event != "REFUND_REVERSED" and (transaction.revocationDate is not None or event in {"REFUND", "REVOKE"})
    # A client replay must never undo a refund; only a newer Apple reversal may do so.
    if existing:
        if existing["quantity"] != quantity:
            raise HTTPException(409, "交易数量不一致")
        if not event and (transaction.signedDate < existing["signed_date"] or transaction.signedDate <= existing["notification_date"]):
            return existing["id"]
        if event and event_date <= existing["notification_date"]:
            return existing["id"]
        if event == "REFUND_REVERSED":
            status = "credited"
        elif revoked:
            status = "revoked"
        else:
            status = existing["status"]
        new_signed_date = max(int(existing["signed_date"] or 0), int(transaction.signedDate or 0))
        new_notification_date = max(int(existing["notification_date"] or 0), int(event_date or 0))
        db.execute("UPDATE purchases SET status=?, signed_date=?, notification_date=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                   (status, new_signed_date, new_notification_date, existing["id"]))
        return existing["id"]
    # Retain a tombstone even if the account was deleted, preventing re-credit elsewhere.
    return db.execute("""INSERT INTO purchases(environment,transaction_id,user_id,username,app_account_token,
        product_id,quantity,price_milli,currency,purchased_at,status,signed_date,notification_date)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", (env, transaction.transactionId, owner["id"] if owner else None,
        owner["username"] if owner else "已删除账号", token, PRODUCT_ID, quantity, transaction.price,
        transaction.currency, purchased_at, "revoked" if revoked else "credited", transaction.signedDate, event_date)).lastrowid
