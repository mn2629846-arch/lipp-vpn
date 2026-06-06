import asyncio
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import sys
import uuid
from base64 import b64decode, b64encode
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = ROOT_DIR.parent

if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import main as core

if not Path(core.DB_PATH).is_absolute():
    core.DB_PATH = str((PROJECT_DIR / core.DB_PATH).resolve())

STATIC_DIR = ROOT_DIR / "web_static"

WEB_COOKIE_NAME = os.getenv("WEB_COOKIE_NAME", "lipp_session")
WEB_COOKIE_SECURE = os.getenv("WEB_COOKIE_SECURE", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
WEB_PUBLIC_URL = os.getenv("WEB_PUBLIC_URL", "").strip()
WEB_SESSION_DAYS = int(os.getenv("WEB_SESSION_DAYS", "30"))
WEB_USER_ID_BASE = int(os.getenv("WEB_USER_ID_BASE", "900000000000"))
PASSWORD_ITERATIONS = int(os.getenv("WEB_PASSWORD_ITERATIONS", "260000"))


class AuthPayload(BaseModel):
    email: str
    password: str
    name: str | None = None
    referral: str | None = None


class LoginPayload(BaseModel):
    email: str
    password: str


class PaymentCreatePayload(BaseModel):
    tariff_key: str


class TelegramCodePayload(BaseModel):
    pass


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def normalize_email(value: str) -> str:
    email = (value or "").strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise HTTPException(status_code=422, detail="Введите корректный email")
    return email


def normalize_name(value: str | None, email: str) -> str:
    name = (value or "").strip()
    if not name:
        return email.split("@", 1)[0]
    return name[:80]


def validate_password(value: str) -> str:
    password = value or ""
    if len(password) < 8:
        raise HTTPException(status_code=422, detail="Пароль должен быть не короче 8 символов")
    return password


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PASSWORD_ITERATIONS,
    )
    return "pbkdf2_sha256${}${}${}".format(
        PASSWORD_ITERATIONS,
        b64encode(salt).decode("ascii"),
        b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        scheme, iterations_text, salt_text, digest_text = stored_hash.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        iterations = int(iterations_text)
        salt = b64decode(salt_text.encode("ascii"))
        expected = b64decode(digest_text.encode("ascii"))
    except Exception:
        return False

    actual = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
    )
    return hmac.compare_digest(actual, expected)


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def validate_web_config():
    required_vars = {
        "YOOKASSA_SHOP_ID": core.YOOKASSA_SHOP_ID,
        "YOOKASSA_SECRET_KEY": core.YOOKASSA_SECRET_KEY,
        "PANEL_URL": core.PANEL_URL,
        "VPN_SERVER_HOST": core.VPN_SERVER_HOST,
    }
    missing = [name for name, value in required_vars.items() if not value]

    if core.REALITY_PUBLIC_KEY == "PUT_YOUR_PUBLIC_KEY":
        missing.append("REALITY_PUBLIC_KEY")

    if missing:
        raise RuntimeError(
            "Не заполнены переменные окружения для сайта: " + ", ".join(missing)
        )

    if not core.PANEL_API_TOKEN and not (core.PANEL_ADMIN and core.PANEL_PASSWORD):
        raise RuntimeError("Нужен PANEL_API_TOKEN или PANEL_ADMIN + PANEL_PASSWORD")

    core.Configuration.account_id = core.YOOKASSA_SHOP_ID
    core.Configuration.secret_key = core.YOOKASSA_SECRET_KEY


async def ensure_web_schema():
    await core.db.execute("""
    CREATE TABLE IF NOT EXISTS web_accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        display_name TEXT,
        user_id INTEGER UNIQUE,
        created_at TEXT,
        last_login_at TEXT
    )
    """)

    await ensure_web_column("web_accounts", "telegram_user_id", "INTEGER")
    await ensure_web_column("web_accounts", "telegram_username", "TEXT")
    await ensure_web_column("web_accounts", "telegram_linked_at", "TEXT")

    await core.db.execute("""
    CREATE TABLE IF NOT EXISTS web_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        token_hash TEXT NOT NULL UNIQUE,
        account_id INTEGER NOT NULL,
        expires_at TEXT NOT NULL,
        created_at TEXT,
        last_seen_at TEXT,
        FOREIGN KEY(account_id) REFERENCES web_accounts(id)
    )
    """)

    await core.db.execute("""
    CREATE INDEX IF NOT EXISTS idx_web_sessions_token
    ON web_sessions(token_hash)
    """)

    await core.db.execute("""
    CREATE INDEX IF NOT EXISTS idx_web_accounts_user_id
    ON web_accounts(user_id)
    """)

    await core.db.execute("""
    CREATE UNIQUE INDEX IF NOT EXISTS idx_web_accounts_telegram_user_id
    ON web_accounts(telegram_user_id)
    WHERE telegram_user_id IS NOT NULL
    """)

    await core.db.execute("""
    CREATE TABLE IF NOT EXISTS web_link_codes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT NOT NULL UNIQUE,
        account_id INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        used_at TEXT,
        telegram_user_id INTEGER,
        FOREIGN KEY(account_id) REFERENCES web_accounts(id)
    )
    """)

    await core.db.execute("""
    CREATE INDEX IF NOT EXISTS idx_web_link_codes_code
    ON web_link_codes(code)
    """)

    await core.db.execute("""
    CREATE INDEX IF NOT EXISTS idx_web_link_codes_account
    ON web_link_codes(account_id)
    """)

    await core.db.commit()


async def ensure_web_column(table: str, column: str, definition: str):
    cursor = await core.db.execute(f"PRAGMA table_info({table})")
    columns = [row[1] for row in await cursor.fetchall()]

    if column not in columns:
        await core.db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


async def cleanup_expired_sessions():
    await core.db.execute(
        "DELETE FROM web_sessions WHERE expires_at <= ?",
        (now_iso(),),
    )
    await core.db.commit()


async def resolve_referral_by(referral: str | None) -> int | None:
    if not referral:
        return None

    match = re.search(r"\d+", referral)
    if not match:
        return None

    value = int(match.group(0))

    cursor = await core.db.execute(
        "SELECT user_id FROM web_accounts WHERE id = ?",
        (value,),
    )
    row = await cursor.fetchone()
    if row and row[0]:
        return int(row[0])

    cursor = await core.db.execute(
        "SELECT user_id FROM users WHERE user_id = ?",
        (value,),
    )
    row = await cursor.fetchone()
    return int(row[0]) if row else None


async def create_web_account(payload: AuthPayload) -> dict[str, Any]:
    email = normalize_email(payload.email)
    password = validate_password(payload.password)
    display_name = normalize_name(payload.name, email)
    referral_by = await resolve_referral_by(payload.referral)
    password_hash = hash_password(password)
    created_at = now_iso()

    try:
        await core.db.execute("BEGIN")
        cursor = await core.db.execute(
            """
            INSERT INTO web_accounts (email, password_hash, display_name, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (email, password_hash, display_name, created_at),
        )
        account_id = cursor.lastrowid
        user_id = WEB_USER_ID_BASE + int(account_id)

        while True:
            try:
                await core.db.execute(
                    """
                    INSERT INTO users (user_id, username, referral_by, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (user_id, display_name or email, referral_by, created_at),
                )
                break
            except sqlite3.IntegrityError:
                user_id += 1

        await core.db.execute(
            "UPDATE web_accounts SET user_id = ? WHERE id = ?",
            (user_id, account_id),
        )
        await core.db.commit()
    except sqlite3.IntegrityError as exc:
        await core.db.rollback()
        raise HTTPException(status_code=409, detail="Аккаунт с таким email уже есть") from exc
    except Exception:
        await core.db.rollback()
        raise

    return {
        "id": account_id,
        "email": email,
        "display_name": display_name,
        "user_id": user_id,
    }


async def set_session_cookie(account_id: int, response: Response) -> None:
    token = secrets.token_urlsafe(36)
    token_hash = hash_session_token(token)
    expires_at = datetime.now(UTC) + timedelta(days=WEB_SESSION_DAYS)

    await core.db.execute(
        """
        INSERT INTO web_sessions (token_hash, account_id, expires_at, created_at, last_seen_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (token_hash, account_id, expires_at.isoformat(), now_iso(), now_iso()),
    )
    await core.db.commit()

    response.set_cookie(
        WEB_COOKIE_NAME,
        token,
        max_age=WEB_SESSION_DAYS * 24 * 60 * 60,
        httponly=True,
        secure=WEB_COOKIE_SECURE,
        samesite="lax",
        path="/",
    )


async def get_current_account(request: Request) -> dict[str, Any]:
    token = request.cookies.get(WEB_COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="Нужно войти в кабинет")

    cursor = await core.db.execute(
        """
        SELECT
            a.id,
            a.email,
            a.display_name,
            a.user_id,
            a.telegram_user_id,
            a.telegram_username,
            a.telegram_linked_at
        FROM web_sessions s
        JOIN web_accounts a ON a.id = s.account_id
        WHERE s.token_hash = ?
          AND s.expires_at > ?
        """,
        (hash_session_token(token), now_iso()),
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="Сессия истекла")

    await core.db.execute(
        "UPDATE web_sessions SET last_seen_at = ? WHERE token_hash = ?",
        (now_iso(), hash_session_token(token)),
    )
    await core.db.commit()

    web_user_id = row[3]
    telegram_user_id = row[4]

    return {
        "id": row[0],
        "email": row[1],
        "display_name": row[2],
        "web_user_id": web_user_id,
        "telegram_user_id": telegram_user_id,
        "telegram_username": row[5],
        "telegram_linked_at": row[6],
        "user_id": telegram_user_id or web_user_id,
    }


async def get_account_by_email(email: str) -> dict[str, Any] | None:
    cursor = await core.db.execute(
        """
        SELECT
            id,
            email,
            display_name,
            user_id,
            password_hash,
            telegram_user_id,
            telegram_username,
            telegram_linked_at
        FROM web_accounts
        WHERE email = ?
        """,
        (email,),
    )
    row = await cursor.fetchone()
    if not row:
        return None
    web_user_id = row[3]
    telegram_user_id = row[5]

    return {
        "id": row[0],
        "email": row[1],
        "display_name": row[2],
        "web_user_id": web_user_id,
        "user_id": telegram_user_id or web_user_id,
        "password_hash": row[4],
        "telegram_user_id": telegram_user_id,
        "telegram_username": row[6],
        "telegram_linked_at": row[7],
    }


def request_base_url(request: Request) -> str:
    if WEB_PUBLIC_URL:
        return WEB_PUBLIC_URL.rstrip("/")
    return str(request.base_url).rstrip("/")


def tariff_list() -> list[dict[str, Any]]:
    return [
        {
            "key": key,
            "name": tariff["name"],
            "days": tariff["days"],
            "price": tariff["price"],
        }
        for key, tariff in core.TARIFFS.items()
    ]


async def payment_history(user_id: int) -> list[dict[str, Any]]:
    cursor = await core.db.execute(
        """
        SELECT payment_id, amount, status, processed, created_at, tariff_key
        FROM payments
        WHERE user_id = ?
        ORDER BY id DESC
        LIMIT 12
        """,
        (user_id,),
    )
    rows = await cursor.fetchall()
    return [
        {
            "payment_id": row[0],
            "amount": row[1],
            "status": row[2],
            "processed": bool(row[3]),
            "created_at": row[4],
            "tariff_key": row[5],
            "tariff_name": core.TARIFFS.get(row[5] or "", {}).get("name", row[5] or "Тариф"),
        }
        for row in rows
    ]


async def user_access_status(account: dict[str, Any], request: Request) -> dict[str, Any]:
    cursor = await core.db.execute(
        """
        SELECT
            vpn_key,
            subscription_end,
            referral_count,
            referral_bonus_days,
            referral_pending_days
        FROM users
        WHERE user_id = ?
        """,
        (account["user_id"],),
    )
    row = await cursor.fetchone()

    vpn_key = row[0] if row else None
    subscription_end = row[1] if row else None
    sub_dt = core.parse_dt_or_none(subscription_end)
    is_active = bool(vpn_key and sub_dt and sub_dt > datetime.now(UTC))

    subscription_link = ""
    if vpn_key:
        try:
            subscription_link = core.build_subscription_link(account["user_id"])
        except Exception:
            subscription_link = ""

    return {
        "active": is_active,
        "vpn_key": vpn_key or "",
        "subscription_link": subscription_link,
        "subscription_end": subscription_end,
        "subscription_end_text": core.format_subscription_end(subscription_end),
        "days_left_text": core.days_left_text(subscription_end),
        "referral_count": row[2] or 0 if row else 0,
        "referral_bonus_days": row[3] or 0 if row else 0,
        "referral_pending_days": row[4] or 0 if row else 0,
        "referral_link": f"{request_base_url(request)}/?ref={account['id']}",
    }


async def dashboard_payload(account: dict[str, Any], request: Request) -> dict[str, Any]:
    return {
        "account": {
            "id": account["id"],
            "email": account["email"],
            "display_name": account["display_name"],
            "user_id": account["user_id"],
            "web_user_id": account.get("web_user_id") or account["user_id"],
            "telegram": {
                "linked": bool(account.get("telegram_user_id")),
                "user_id": account.get("telegram_user_id"),
                "username": account.get("telegram_username"),
                "linked_at": account.get("telegram_linked_at"),
            },
        },
        "access": await user_access_status(account, request),
        "payments": await payment_history(account["user_id"]),
        "tariffs": tariff_list(),
        "support": {
            "telegram": core.SUPPORT_USERNAME,
            "news": core.NEWS_CHANNEL_USERNAME,
            "project": core.PROJECT_NAME,
        },
    }


def telegram_start_command(code: str) -> str:
    return f"/start web_{code}"


async def create_telegram_link_code(account: dict[str, Any]) -> dict[str, Any]:
    if account.get("telegram_user_id"):
        return {
            "linked": True,
            "telegram_user_id": account["telegram_user_id"],
            "telegram_username": account.get("telegram_username"),
        }

    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    expires_at = datetime.now(UTC) + timedelta(minutes=15)

    for _ in range(12):
        code = "".join(secrets.choice(alphabet) for _ in range(6))
        try:
            await core.db.execute(
                """
                INSERT INTO web_link_codes (code, account_id, created_at, expires_at)
                VALUES (?, ?, ?, ?)
                """,
                (code, account["id"], now_iso(), expires_at.isoformat()),
            )
            await core.db.commit()
            break
        except sqlite3.IntegrityError:
            continue
    else:
        raise HTTPException(status_code=500, detail="Не удалось создать код привязки")

    command = telegram_start_command(code)
    bot_username = core.clean_username(core.BOT_USERNAME) if core.BOT_USERNAME else ""
    bot_url = (
        f"https://t.me/{bot_username}?start=web_{code}"
        if bot_username
        else ""
    )

    return {
        "linked": False,
        "code": code,
        "command": command,
        "bot_url": bot_url,
        "expires_at": expires_at.isoformat(),
    }


async def process_payment_for_account(
    payment_id: str,
    account: dict[str, Any],
) -> dict[str, Any]:
    cursor = await core.db.execute(
        """
        SELECT user_id, processed, tariff_key
        FROM payments
        WHERE payment_id = ?
        """,
        (payment_id,),
    )
    row = await cursor.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Платеж не найден")

    payment_user_id, processed, stored_tariff_key = row
    if int(payment_user_id) != int(account["user_id"]):
        raise HTTPException(status_code=403, detail="Платеж относится к другому аккаунту")

    if processed == 1:
        return {"status": "succeeded", "processed": True}

    try:
        payment = await asyncio.wait_for(
            asyncio.to_thread(core.Payment.find_one, payment_id),
            timeout=20.0,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail="Не удалось проверить платеж. Повторите позже",
        ) from exc

    payment_status = getattr(payment, "status", "pending")
    if payment_status != "succeeded":
        await core.db.execute(
            "UPDATE payments SET status = ? WHERE payment_id = ?",
            (payment_status, payment_id),
        )
        await core.db.commit()
        return {"status": payment_status, "processed": False}

    metadata = getattr(payment, "metadata", None) or {}
    tariff_key = metadata.get("tariff") or stored_tariff_key
    if tariff_key not in core.TARIFFS:
        raise HTTPException(status_code=422, detail="Тариф из платежа не найден")

    tariff = core.TARIFFS[tariff_key]
    cursor = await core.db.execute(
        """
        SELECT subscription_end, referral_pending_days
        FROM users
        WHERE user_id = ?
        """,
        (account["user_id"],),
    )
    user_row = await cursor.fetchone()

    current_subscription_end = user_row[0] if user_row else None
    pending_bonus_days = user_row[1] if user_row and user_row[1] else 0
    has_referral_bonus = await core.user_can_receive_referral_bonus(account["user_id"])
    invitee_bonus_days = core.REFERRAL_BONUS_DAYS if has_referral_bonus else 0
    total_days = tariff["days"] + invitee_bonus_days + pending_bonus_days
    subscription_end_dt = core.add_days_to_subscription(current_subscription_end, total_days)

    try:
        vpn_key = await core.create_vpn_user(
            user_id=account["user_id"],
            subscription_end=subscription_end_dt,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Оплата прошла, но ключ не создался. Напишите @{core.SUPPORT_USERNAME}",
        ) from exc

    await core.db.execute(
        """
        UPDATE users
        SET vpn_key = ?,
            subscription_end = ?,
            referral_pending_days = 0,
            reminder_3d_sent_at = NULL,
            reminder_1d_sent_at = NULL,
            reminder_6h_sent_at = NULL
        WHERE user_id = ?
        """,
        (vpn_key, subscription_end_dt.isoformat(), account["user_id"]),
    )
    await core.db.execute(
        """
        UPDATE payments
        SET processed = 1,
            status = 'succeeded'
        WHERE payment_id = ?
        """,
        (payment_id,),
    )
    await core.apply_referral_bonus(account["user_id"])
    await core.db.commit()

    return {"status": "succeeded", "processed": True}


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_web_config()
    await core.init_db()
    await ensure_web_schema()
    await cleanup_expired_sessions()
    try:
        yield
    finally:
        if core.db:
            await core.db.close()


app = FastAPI(title="LIPP VPN Web Cabinet", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/bootstrap")
async def bootstrap():
    return {
        "tariffs": tariff_list(),
        "project": core.PROJECT_NAME,
        "support": core.SUPPORT_USERNAME,
    }


@app.post("/api/auth/register")
async def register(payload: AuthPayload, response: Response, request: Request):
    account = await create_web_account(payload)
    await set_session_cookie(int(account["id"]), response)
    return await dashboard_payload(account, request)


@app.post("/api/auth/login")
async def login(payload: LoginPayload, response: Response, request: Request):
    email = normalize_email(payload.email)
    account = await get_account_by_email(email)
    if not account or not verify_password(payload.password, account["password_hash"]):
        raise HTTPException(status_code=401, detail="Неверный email или пароль")

    await core.db.execute(
        "UPDATE web_accounts SET last_login_at = ? WHERE id = ?",
        (now_iso(), account["id"]),
    )
    await core.db.commit()
    await set_session_cookie(int(account["id"]), response)
    return await dashboard_payload(account, request)


@app.post("/api/auth/logout")
async def logout(request: Request, response: Response):
    token = request.cookies.get(WEB_COOKIE_NAME)
    if token:
        await core.db.execute(
            "DELETE FROM web_sessions WHERE token_hash = ?",
            (hash_session_token(token),),
        )
        await core.db.commit()
    response.delete_cookie(WEB_COOKIE_NAME, path="/")
    return {"ok": True}


@app.get("/api/me")
async def me(request: Request, account: dict[str, Any] = Depends(get_current_account)):
    return await dashboard_payload(account, request)


@app.post("/api/telegram/link-code")
async def telegram_link_code(account: dict[str, Any] = Depends(get_current_account)):
    return await create_telegram_link_code(account)


@app.get("/api/telegram/status")
async def telegram_status(
    request: Request,
    account: dict[str, Any] = Depends(get_current_account),
):
    return await dashboard_payload(account, request)


@app.post("/api/payments/create")
async def create_payment(
    payload: PaymentCreatePayload,
    request: Request,
    account: dict[str, Any] = Depends(get_current_account),
):
    if payload.tariff_key not in core.TARIFFS:
        raise HTTPException(status_code=404, detail="Тариф не найден")

    tariff = core.TARIFFS[payload.tariff_key]
    amount = tariff["price"]
    payment_data = {
        "amount": {
            "value": f"{amount}.00",
            "currency": "RUB",
        },
        "confirmation": {
            "type": "redirect",
            "return_url": request_base_url(request),
        },
        "capture": True,
        "description": f"VPN {tariff['name']}",
        "metadata": {
            "source": "web",
            "account_id": str(account["id"]),
            "user_id": str(account["user_id"]),
            "tariff": payload.tariff_key,
        },
    }

    payment = await asyncio.to_thread(
        core.Payment.create,
        payment_data,
        str(uuid.uuid4()),
    )

    await core.db.execute(
        """
        INSERT INTO payments (
            user_id,
            payment_id,
            amount,
            status,
            created_at,
            tariff_key
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            account["user_id"],
            payment.id,
            amount,
            "pending",
            now_iso(),
            payload.tariff_key,
        ),
    )
    await core.db.commit()

    return {
        "payment_id": payment.id,
        "confirmation_url": payment.confirmation.confirmation_url,
    }


@app.post("/api/payments/{payment_id}/check")
async def check_payment(
    payment_id: str,
    request: Request,
    account: dict[str, Any] = Depends(get_current_account),
):
    result = await process_payment_for_account(payment_id, account)
    payload = await dashboard_payload(account, request)
    payload["payment_result"] = result
    return payload


@app.post("/api/access/refresh")
async def refresh_access(
    request: Request,
    account: dict[str, Any] = Depends(get_current_account),
):
    cursor = await core.db.execute(
        "SELECT subscription_end FROM users WHERE user_id = ?",
        (account["user_id"],),
    )
    row = await cursor.fetchone()
    sub_dt = core.parse_dt_or_none(row[0] if row else None)

    if not sub_dt or sub_dt <= datetime.now(UTC):
        raise HTTPException(status_code=403, detail="Нет активной подписки")

    try:
        vpn_key = await core.create_vpn_user(
            user_id=account["user_id"],
            subscription_end=sub_dt,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Не удалось обновить ключ. Напишите @{core.SUPPORT_USERNAME}",
        ) from exc

    await core.db.execute(
        "UPDATE users SET vpn_key = ? WHERE user_id = ?",
        (vpn_key, account["user_id"]),
    )
    await core.db.commit()
    return await dashboard_payload(account, request)


@app.post("/api/access/check")
async def check_access(account: dict[str, Any] = Depends(get_current_account)):
    cursor = await core.db.execute(
        "SELECT vpn_key FROM users WHERE user_id = ?",
        (account["user_id"],),
    )
    row = await cursor.fetchone()
    if not row or not row[0]:
        raise HTTPException(status_code=403, detail="Ключ еще не выдан")

    try:
        status = await core.get_xui_client_status(account["user_id"])
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail="Не удалось получить статус подключения",
        ) from exc

    stat = status.get("client_stat") or {}
    return {
        "online": bool(stat),
        "up": core.format_traffic(stat.get("up")),
        "down": core.format_traffic(stat.get("down")),
        "total": core.format_traffic((stat.get("up") or 0) + (stat.get("down") or 0)),
        "last_online": core.format_last_online(stat.get("lastOnline")),
    }
