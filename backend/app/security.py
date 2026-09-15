import hashlib
import os
import secrets
import time
from pathlib import Path
from jose import JWTError, jwt
from passlib.context import CryptContext
from .database import DATA_DIR, get_db

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def secret_key():
    configured = os.getenv("BUDMON_SECRET_KEY", "")
    if configured and configured not in {"change-me-in-production", "change-this-secret"}:
        return configured
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / ".jwt-secret"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_hex(32))
    except FileExistsError:
        pass
    return path.read_text()


def hash_password(password):
    if len(password.encode()) > 72:
        raise ValueError("密码最多 72 字节")
    return pwd_context.hash(password)


def verify_password(password, password_hash):
    return len(password.encode()) <= 72 and pwd_context.verify(password, password_hash)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def create_session(db, user_id):
    sid, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
    db.execute("DELETE FROM sessions WHERE expires_at < ?", (int(time.time()),))
    db.execute("INSERT INTO sessions VALUES(?,?,?,?)", (sid, user_id, digest(refresh), int(time.time()) + 30*86400))
    return session_tokens(sid, user_id, refresh)


def session_tokens(sid, user_id, refresh):
    token = jwt.encode({"sub": str(user_id), "sid": sid, "exp": int(time.time()) + 86400}, secret_key(), algorithm="HS256")
    return {"token": token, "refresh_token": refresh, "expires_in": 86400}


def read_token(token):
    try:
        return jwt.decode(token, secret_key(), algorithms=["HS256"])
    except JWTError:
        return None
