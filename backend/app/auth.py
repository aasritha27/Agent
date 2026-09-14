"""Authentication: Supabase JWTs in the Authorization header, roles from the
profiles table. Two roles: 'coordinator' (generate/approve) and 'viewer'
(faculty: view own timetable, submit room requests)."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from . import db

bearer = HTTPBearer(auto_error=False)


def current_user(
    creds: HTTPAuthorizationCredentials = Depends(bearer),
) -> dict:
    if creds is None:
        raise HTTPException(401, "Sign in required")
    token = creds.credentials
    try:
        user = db.client().auth.get_user(token).user
    except Exception:
        raise HTTPException(401, "Invalid or expired session")
    if user is None:
        raise HTTPException(401, "Invalid or expired session")
    return {"id": user.id, "email": user.email}


def require_coordinator(user: dict = Depends(current_user)) -> dict:
    role = db.get_role(user["id"])
    if role != "coordinator":
        raise HTTPException(403, "Coordinator role required")
    return {**user, "role": role}


def require_user(user: dict = Depends(current_user)) -> dict:
    user["role"] = db.get_role(user["id"])
    return user
