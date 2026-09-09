import hashlib
import logging
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from auth import (check_lockout, clear_failures, create_access_token, get_current_user, hash_password, public_user,
                  record_failure, require_role, verify_password, ROLES, ACCESS_HOURS)
from db import db, clean, audit
from emailer import send_email, reset_email_html, test_email_html, record_test, configured as email_configured, get_config as get_email_config
from models import LoginRequest, UserCreate, UserUpdate, ForgotPasswordRequest, ResetPasswordRequest, new_id

logger = logging.getLogger("auth")
router = APIRouter()


@router.post("/auth/login")
async def login(body: LoginRequest, request: Request, response: Response):
    email = body.email.lower().strip()
    ip = (request.headers.get("x-forwarded-for") or (request.client.host if request.client else "unknown")).split(",")[0].strip()
    ident = f"{ip}:{email}"
    await check_lockout(ident)
    user = await db.users.find_one({"email": email})
    if not user or not verify_password(body.password, user["password_hash"]):
        await record_failure(ident)
        raise HTTPException(401, "Invalid email or password")
    if not user.get("active", True):
        raise HTTPException(403, "Account deactivated")
    await clear_failures(ident)
    token = create_access_token(user)
    response.set_cookie("access_token", token, httponly=True, secure=True, samesite="lax", max_age=ACCESS_HOURS * 3600, path="/")
    await db.users.update_one({"id": user["id"]}, {"$set": {"last_login": datetime.now(timezone.utc)}})
    await audit("user", user["id"], "auth.login", {"email": email}, email)
    return {"access_token": token, "token_type": "bearer", "user": clean(public_user(user))}


@router.post("/auth/logout")
async def logout(response: Response, user=Depends(get_current_user)):
    response.delete_cookie("access_token", path="/", httponly=True, secure=True, samesite="lax")
    await audit("user", user["id"], "auth.logout", {}, user["email"])
    return {"ok": True}


@router.get("/auth/me")
async def me(user=Depends(get_current_user)):
    return clean(user)


@router.get("/users")
async def list_users(user=Depends(require_role("admin"))):
    return clean([public_user(u) async for u in db.users.find({}).sort("created_at", 1)])


@router.post("/users", status_code=201)
async def create_user(body: UserCreate, user=Depends(require_role("admin"))):
    if body.role not in ROLES:
        raise HTTPException(400, f"role must be one of {ROLES}")
    email = body.email.lower().strip()
    if await db.users.find_one({"email": email}):
        raise HTTPException(400, "email already registered")
    doc = {"id": new_id(), "email": email, "name": body.name, "role": body.role, "password_hash": hash_password(body.password),
           "active": True, "created_at": datetime.now(timezone.utc), "created_by": user["email"]}
    await db.users.insert_one(dict(doc))
    await audit("user", doc["id"], "user.created", {"email": email, "role": body.role}, user["email"])
    return clean(public_user(doc))


@router.patch("/users/{user_id}")
async def update_user(user_id: str, body: UserUpdate, user=Depends(require_role("admin"))):
    target = await db.users.find_one({"id": user_id})
    if not target:
        raise HTTPException(404, "user not found")
    update = {}
    if body.role is not None:
        if body.role not in ROLES:
            raise HTTPException(400, f"role must be one of {ROLES}")
        if user_id == user["id"] and body.role != "admin":
            raise HTTPException(400, "cannot demote yourself")
        update["role"] = body.role
    if body.active is not None:
        if user_id == user["id"] and not body.active:
            raise HTTPException(400, "cannot deactivate yourself")
        update["active"] = body.active
    if body.name is not None:
        update["name"] = body.name
    if body.notify_alerts is not None:
        update["notify_alerts"] = body.notify_alerts
    if body.password:
        update["password_hash"] = hash_password(body.password)
    if update:
        await db.users.update_one({"id": user_id}, {"$set": update})
        await audit("user", user_id, "user.updated", {k: v for k, v in update.items() if k != "password_hash"}, user["email"])
    return clean(public_user(await db.users.find_one({"id": user_id})))


@router.delete("/users/{user_id}")
async def delete_user(user_id: str, user=Depends(require_role("admin"))):
    if user_id == user["id"]:
        raise HTTPException(400, "cannot delete yourself")
    res = await db.users.delete_one({"id": user_id})
    if not res.deleted_count:
        raise HTTPException(404, "user not found")
    await audit("user", user_id, "user.deleted", {}, user["email"])
    return {"ok": True}


GENERIC_MSG = "If that account exists, a reset link has been issued. Check your inbox (or ask an administrator if email delivery is not configured)."


@router.post("/auth/forgot-password")
async def forgot_password(body: ForgotPasswordRequest, request: Request):
    email = body.email.lower().strip()
    user = await db.users.find_one({"email": email})
    if not user:
        await audit("user", "unknown", "auth.reset_requested_unknown_email", {"email": email}, email)
        return {"message": GENERIC_MSG, "delivery": "none"}
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    link = f"{os.environ.get('FRONTEND_URL', 'http://localhost:3000').rstrip('/')}/reset-password?token={token}"
    sent_res = await send_email(email, "VarunaNetra password reset", reset_email_html(user.get("name") or email, link))
    sent = sent_res["sent"]
    delivery = "email" if sent else "logged"
    await db.password_reset_tokens.insert_one({
        "id": new_id(), "token_hash": hashlib.sha256(token.encode()).hexdigest(), "user_id": user["id"], "email": email,
        "expires_at": now + timedelta(hours=1), "used": False, "delivery": delivery, "link": None if sent else link,
        "requested_ip": (request.headers.get("x-forwarded-for") or (request.client.host if request.client else "")).split(",")[0].strip(), "created_at": now})
    if not sent:
        logger.warning("PASSWORD RESET LINK for %s (email not configured): %s", email, link)
    await audit("user", user["id"], "auth.reset_requested", {"delivery": delivery}, email)
    return {"message": GENERIC_MSG, "delivery": delivery}


@router.post("/auth/reset-password")
async def reset_password(body: ResetPasswordRequest):
    rec = await db.password_reset_tokens.find_one({"token_hash": hashlib.sha256(body.token.encode()).hexdigest()})
    if not rec or rec.get("used"):
        raise HTTPException(400, "Reset link is invalid or has already been used")
    if rec["expires_at"].replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        raise HTTPException(400, "Reset link has expired — request a new one")
    await db.users.update_one({"id": rec["user_id"]}, {"$set": {"password_hash": hash_password(body.new_password), "password_changed_at": datetime.now(timezone.utc)}})
    await db.password_reset_tokens.update_one({"id": rec["id"]}, {"$set": {"used": True, "used_at": datetime.now(timezone.utc), "link": None}})
    await db.login_attempts.delete_many({"identifier": {"$regex": f":{re.escape(rec['email'])}$"}})
    await audit("user", rec["user_id"], "auth.password_reset", {"via": "reset_link"}, rec["email"])
    return {"ok": True, "email": rec["email"]}


@router.get("/auth/reset-requests")
async def list_reset_requests(user=Depends(require_role("admin"))):
    rows = await db.password_reset_tokens.find({}, {"_id": 0, "token_hash": 0}).sort("created_at", -1).to_list(50)
    return clean({"email_configured": await email_configured(), "requests": rows})


class EmailSettings(BaseModel):
    mode: Optional[str] = None
    resend_api_key: Optional[str] = None
    sender_email: Optional[str] = None
    enabled: Optional[bool] = None
    alerts_enabled: Optional[bool] = None
    alert_recipients: Optional[List[str]] = None


def _mask(k: str) -> str:
    return f"{k[:5]}…{k[-4:]}" if k and len(k) > 10 else ("set" if k else "")


@router.get("/settings/email")
async def get_email_settings(user=Depends(require_role("admin"))):
    c = await get_email_config()
    return clean({**c, "api_key": _mask(c["api_key"]), "configured": bool(c["enabled"]) and (c.get("mode")=="demo" or bool(c["api_key"]))})


@router.put("/settings/email")
async def put_email_settings(body: EmailSettings, user=Depends(require_role("admin"))):
    update = {k: v for k, v in body.model_dump(exclude_none=True).items()}
    if "mode" in update and update["mode"] not in ("demo", "live"): raise HTTPException(400, "mode must be demo or live")
    if update.get("resend_api_key"): update["mode"] = "live"
    if "resend_api_key" in update and update["resend_api_key"] == "":
        update["resend_api_key"] = None
    if "alert_recipients" in update:
        update["alert_recipients"] = sorted({e.lower().strip() for e in update["alert_recipients"] if "@" in e})
    update.update({"updated_at": datetime.now(timezone.utc), "updated_by": user["email"]})
    await db.settings.update_one({"key": "email"}, {"$set": update}, upsert=True)
    await audit("settings", "email", "settings.email_updated", {k: ("***" if k == "resend_api_key" else v) for k, v in update.items()}, user["email"])
    return await get_email_settings(user)


@router.post("/settings/email/test")
async def test_email_settings(user=Depends(require_role("admin"))):
    res = await send_email(user["email"], "VarunaNetra delivery test", test_email_html(user.get("name") or user["email"]))
    await record_test(res, user["email"])
    await audit("settings", "email", "settings.email_tested", res, user["email"])
    if not res["sent"]:
        raise HTTPException(400, res.get("error", "send failed"))
    return {"ok": True, "to": user["email"], "id": res.get("id")}
