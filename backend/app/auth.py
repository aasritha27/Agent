"""Authentication — fully self-hosted, no external auth service.

Passwords are hashed with PBKDF2-SHA256. Sessions are HMAC-signed tokens
(payload base64url + signature), sent as `Authorization: Bearer <token>`.
Two roles: 'coordinator' (generate/approve) and 'viewer' (faculty: view
timetables, submit room requests).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from . import db
from .config import SECRET_KEY

bearer = HTTPBearer(auto_error=False)

TOKEN_TTL_SECONDS = 30 * 24 * 3600  # 30 days


# ---- passwords -------------------------------------------------------------

def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000)
    return f"pbkdf2${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt, expected = stored.split("$")
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000)
    return hmac.compare_digest(digest.hex(), expected)


# ---- tokens ----------------------------------------------------------------

def _sign(payload: bytes) -> str:
    return hmac.new(SECRET_KEY.encode(), payload, hashlib.sha256).hexdigest()


def create_token(user_id: str, email: str) -> str:
    payload = {
        "uid": user_id,
        "email": email,
        "exp": int(time.time()) + TOKEN_TTL_SECONDS,
    }
    raw = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=")
    return raw.decode() + "." + _sign(raw)


def parse_token(token: str) -> dict | None:
    try:
        raw_b64, sig = token.rsplit(".", 1)
        raw = raw_b64.encode()
        if not hmac.compare_digest(_sign(raw), sig):
            return None
        payload = json.loads(base64.urlsafe_b64decode(raw + b"=" * (-len(raw) % 4)))
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:
        return None


# ---- FastAPI dependencies ----------------------------------------------------

def current_user(
    creds: HTTPAuthorizationCredentials = Depends(bearer),
) -> dict:
    if creds is None:
        raise HTTPException(401, "Sign in required")
    payload = parse_token(creds.credentials)
    if payload is None:
        raise HTTPException(401, "Invalid or expired session")
    user = db.get_user(payload["uid"])
    if user is None:
        raise HTTPException(401, "Invalid or expired session")
    return {"id": user["id"], "email": user["email"], "full_name": user["full_name"]}


def require_coordinator(user: dict = Depends(current_user)) -> dict:
    role = db.get_role(user["id"])
    if role != "coordinator":
        raise HTTPException(403, "Coordinator role required")
    return {**user, "role": role}


def require_user(user: dict = Depends(current_user)) -> dict:
    user["role"] = db.get_role(user["id"])
    return user
