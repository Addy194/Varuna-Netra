import os
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request

from db import db, audit
from models import new_id

ALG = "HS256"
ROLES = ["analyst", "supervisor", "admin"]
ROLE_RANK = {r: i for i, r in enumerate(ROLES)}
ACCESS_HOURS = 12
LOCKOUT_ATTEMPTS, LOCKOUT_MINUTES = 5, 15


def hash_password(p: str) -> str:
    return bcrypt.hashpw(p.encode(), bcrypt.gensalt()).decode()


def verify_password(p: str, h: str) -> bool:
    return bcrypt.checkpw(p.encode(), h.encode())


def create_access_token(user: dict) -> str:
    payload = {"sub": user["id"], "email": user["email"], "role": user["role"], "type": "access",
               "exp": datetime.now(timezone.utc) + timedelta(hours=ACCESS_HOURS)}
    return jwt.encode(payload, os.environ.get("JWT_SECRET", "demo-only-change-me"), algorithm=ALG)


def public_user(u: dict) -> dict:
    return {k: v for k, v in u.items() if k not in ("_id", "password_hash")}


async def get_current_user(request: Request) -> dict:
    token = request.cookies.get("access_token")
    if not token:
        h = request.headers.get("Authorization", "")
        if h.startswith("Bearer "):
            token = h[7:]
    if not token:
        raise HTTPException(401, "Not authenticated")
    try:
        payload = jwt.decode(token, os.environ.get("JWT_SECRET", "demo-only-change-me"), algorithms=[ALG])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Invalid token")
    if payload.get("type") != "access":
        raise HTTPException(401, "Invalid token type")
    user = await db.users.find_one({"id": payload["sub"]})
    if not user or not user.get("active", True):
        raise HTTPException(401, "User not found or deactivated")
    return public_user(user)


def require_role(min_role: str):
    async def dep(user: dict = Depends(get_current_user)) -> dict:
        if ROLE_RANK.get(user["role"], -1) < ROLE_RANK[min_role]:
            raise HTTPException(403, f"Requires role '{min_role}' or higher")
        return user
    return dep


async def check_lockout(identifier: str):
    rec = await db.login_attempts.find_one({"identifier": identifier})
    if rec and rec.get("count", 0) >= LOCKOUT_ATTEMPTS:
        last = rec["last_attempt"].replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - last < timedelta(minutes=LOCKOUT_MINUTES):
            raise HTTPException(429, f"Too many failed attempts. Try again in {LOCKOUT_MINUTES} minutes.")
        await db.login_attempts.delete_one({"identifier": identifier})


async def record_failure(identifier: str):
    await db.login_attempts.update_one({"identifier": identifier}, {"$inc": {"count": 1}, "$set": {"last_attempt": datetime.now(timezone.utc)}}, upsert=True)


async def clear_failures(identifier: str):
    await db.login_attempts.delete_one({"identifier": identifier})


async def upsert_user(email: str, password: str, name: str, role: str):
    email = email.lower().strip()
    existing = await db.users.find_one({"email": email})
    if not existing:
        await db.users.insert_one({"id": new_id(), "email": email, "password_hash": hash_password(password), "name": name, "role": role,
                                   "active": True, "created_at": datetime.now(timezone.utc)})
    elif not verify_password(password, existing["password_hash"]):
        await db.users.update_one({"email": email}, {"$set": {"password_hash": hash_password(password)}})


async def seed_users():
    await db.users.create_index("email", unique=True)
    await db.login_attempts.create_index("identifier")
    await db.password_reset_tokens.create_index("expires_at", expireAfterSeconds=86400)
    await db.password_reset_tokens.create_index("token_hash")
    await upsert_user(os.environ.get("ADMIN_EMAIL", "admin@varunanetra.local"), os.environ.get("ADMIN_PASSWORD", "demo-admin-password"), "System Administrator", "admin")
    await upsert_user(os.environ.get("DEMO_SUPERVISOR_EMAIL", "supervisor@varunanetra.local"), os.environ.get("DEMO_SUPERVISOR_PASSWORD", "demo-supervisor-password"), "Duty Supervisor", "supervisor")
    await upsert_user(os.environ.get("DEMO_ANALYST_EMAIL", "analyst@varunanetra.local"), os.environ.get("DEMO_ANALYST_PASSWORD", "demo-analyst-password"), "Marine Analyst", "analyst")
