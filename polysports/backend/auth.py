from __future__ import annotations

import hashlib
import hmac
import secrets
import time
import uuid
from datetime import datetime, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError, VerificationError
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy import text

SESSION_COOKIE = "__Host-polysports_session"
LOGIN_COOKIE = "__Host-polysports_login"
SESSION_SECONDS = 8 * 60 * 60
LOGIN_SECONDS = 10 * 60
RATE_LIMIT = 5
RATE_SECONDS = 300
hasher = PasswordHasher()
DUMMY_HASH = hasher.hash("mock-placeholder-never-a-real-password")
router = APIRouter(prefix="/api/v1/auth")


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def mac(secret: str, purpose: str, value: str) -> str:
    return hmac.new(secret.encode(), (purpose + ":" + value).encode(), hashlib.sha256).hexdigest()


def safe_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def csrf(secret: str, token: str, purpose: str) -> str:
    return mac(secret, "csrf-" + purpose, token)


def error(request: Request, status: int, code: str, message: str, delay: int | None = None):
    result = JSONResponse({"error": {"code": code, "message": message, "details": {}, "retry_after_seconds": delay}, "request_id": request.state.request_id}, status_code=status)
    result.headers["Cache-Control"] = "no-store"
    if delay is not None:
        result.headers["Retry-After"] = str(delay)
    return result


def new_login_cookie(secret: str) -> str:
    data = f"{secrets.token_urlsafe(32)}.{int(time.time())}"
    return f"{data}.{mac(secret, 'login-cookie', data)}"


def valid_login_cookie(secret: str, value: str) -> bool:
    try:
        nonce, timestamp, signature = value.split(".")
        elapsed = time.time() - int(timestamp)
        return bool(nonce) and 0 <= elapsed <= LOGIN_SECONDS and safe_equal(signature, mac(secret, "login-cookie", f"{nonce}.{timestamp}"))
    except (ValueError, TypeError):
        return False


def get_session(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if not token or len(token) > 256:
        return None
    with request.app.state.engine.connect() as conn:
        row = conn.execute(text("""SELECT s.id, s.admin_id, s.expires_at, s.csrf_secret_hash, u.username
            FROM admin_sessions s JOIN admin_users u ON u.id=s.admin_id
            WHERE s.token_hash=:token_hash AND s.revoked_at IS NULL AND s.expires_at > now() AND u.disabled=false"""), {"token_hash": digest(token)}).mappings().first()
    if row is None:
        return None
    expected = csrf(request.app.state.settings.auth_secret.get_secret_value(), token, "session")
    if not safe_equal(row["csrf_secret_hash"], digest(expected)):
        return None
    return {**dict(row), "csrf_token": expected}


def check_csrf(request: Request, session) -> bool:
    if request.headers.get("origin") != request.app.state.settings.public_origin:
        return False
    supplied = request.headers.get("x-csrf-token", "")
    secret = request.app.state.settings.auth_secret.get_secret_value()
    if request.url.path == "/api/v1/auth/login":
        cookie = request.cookies.get(LOGIN_COOKIE, "")
        return valid_login_cookie(secret, cookie) and safe_equal(supplied, csrf(secret, cookie, "login"))
    return bool(session) and safe_equal(supplied, session["csrf_token"])


def use_rate_limit(conn, secret: str, ip: str, username: str) -> int:
    # Ordered keys avoid deadlocks when simultaneous attempts use different IPs/usernames.
    keys = sorted([mac(secret, "rate-ip", ip), mac(secret, "rate-user", username)])
    max_wait = 0
    for key in keys:
        row = conn.execute(text("""INSERT INTO auth_rate_limits (bucket_key,window_start,attempt_count,blocked_until,updated_at)
            VALUES (:key,now(),1,NULL,now())
            ON CONFLICT (bucket_key) DO UPDATE SET
              window_start=CASE WHEN auth_rate_limits.window_start <= now() - make_interval(secs => :window) THEN now() ELSE auth_rate_limits.window_start END,
              attempt_count=CASE WHEN auth_rate_limits.window_start <= now() - make_interval(secs => :window) THEN 1 ELSE auth_rate_limits.attempt_count + 1 END,
              blocked_until=CASE WHEN auth_rate_limits.window_start <= now() - make_interval(secs => :window) THEN NULL
                WHEN auth_rate_limits.attempt_count + 1 > :limit THEN GREATEST(COALESCE(auth_rate_limits.blocked_until, now()), auth_rate_limits.window_start + make_interval(secs => :window))
                ELSE auth_rate_limits.blocked_until END,
              updated_at=now()
            RETURNING GREATEST(0, CEIL(EXTRACT(EPOCH FROM (blocked_until - now())))) AS wait"""), {"key": key, "window": RATE_SECONDS, "limit": RATE_LIMIT}).mappings().one()
        max_wait = max(max_wait, int(row["wait"] or 0))
    return max_wait


class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: SecretStr


@router.get("/csrf")
def anonymous_csrf(request: Request):
    secret = request.app.state.settings.auth_secret.get_secret_value()
    value = new_login_cookie(secret)
    response = JSONResponse({"data": {"csrf_token": csrf(secret, value, "login")}, "request_id": request.state.request_id})
    response.set_cookie(LOGIN_COOKIE, value, max_age=LOGIN_SECONDS, secure=True, httponly=True, samesite="strict", path="/")
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/login")
def login(body: LoginBody, request: Request):
    username = body.username.strip().casefold()
    password = body.password.get_secret_value()
    secret = request.app.state.settings.auth_secret.get_secret_value()
    with request.app.state.engine.begin() as conn:
        wait = use_rate_limit(conn, secret, request.client.host if request.client else "unknown", username)
        if wait:
            return error(request, 429, "RATE_LIMITED", "登录尝试过于频繁，请稍后重试", wait)
        user = conn.execute(text("SELECT id,username,password_hash,disabled FROM admin_users WHERE username=:username"), {"username": username}).mappings().first()
        try:
            valid = len(password.encode("utf-8")) <= 1024 and hasher.verify(user["password_hash"] if user else DUMMY_HASH, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            valid = False
        if not user or user["disabled"] or not valid:
            conn.execute(text("INSERT INTO audit_events (id, action, details) VALUES (:id,'login_failed','{}'::jsonb)"), {"id": uuid.uuid4()})
            return error(request, 401, "UNAUTHENTICATED", "用户名或密码无效")
        old = request.cookies.get(SESSION_COOKIE)
        if old:
            conn.execute(text("UPDATE admin_sessions SET revoked_at=now() WHERE token_hash=:hash AND revoked_at IS NULL"), {"hash": digest(old)})
        token = secrets.token_urlsafe(32)
        session_csrf = csrf(secret, token, "session")
        conn.execute(text("""INSERT INTO admin_sessions (id,admin_id,token_hash,csrf_secret_hash,expires_at,last_seen_at)
            VALUES (:id,:admin,:token,:csrf,now()+make_interval(secs => :seconds),now())"""), {"id": uuid.uuid4(), "admin": user["id"], "token": digest(token), "csrf": digest(session_csrf), "seconds": SESSION_SECONDS})
        conn.execute(text("INSERT INTO audit_events (id,actor_id,action,details) VALUES (:id,:actor,'login_succeeded','{}'::jsonb)"), {"id":uuid.uuid4(), "actor":user["id"]})
    response = JSONResponse({"data": {"authenticated": True, "csrf_token": session_csrf}, "request_id":request.state.request_id})
    response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_SECONDS, secure=True, httponly=True, samesite="strict", path="/")
    response.delete_cookie(LOGIN_COOKIE, secure=True, httponly=True, samesite="strict", path="/")
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/session")
def session_status(request: Request):
    session = request.state.admin_session
    return {"data": {"authenticated": True, "username": session["username"], "expires_at": session["expires_at"].astimezone(timezone.utc).isoformat().replace("+00:00", "Z"), "csrf_token": session["csrf_token"]}, "request_id": request.state.request_id}


@router.post("/logout")
def logout(request: Request):
    with request.app.state.engine.begin() as conn:
        conn.execute(text("UPDATE admin_sessions SET revoked_at=now() WHERE id=:id AND revoked_at IS NULL"), {"id": request.state.admin_session["id"]})
        conn.execute(text("INSERT INTO audit_events (id,actor_id,action,details) VALUES (:id,:actor,'logout','{}'::jsonb)"), {"id":uuid.uuid4(), "actor":request.state.admin_session["admin_id"]})
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE, secure=True, httponly=True, samesite="strict", path="/")
    response.headers["Cache-Control"] = "no-store"
    return response
