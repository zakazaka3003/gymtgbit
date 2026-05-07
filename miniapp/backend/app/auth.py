"""Telegram WebApp initData verification.

См. https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from typing import Optional
from urllib.parse import parse_qsl

from fastapi import Header, HTTPException

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
DEV_MODE = os.getenv("DEV_MODE", "0") == "1"


def _check_init_data(init_data: str) -> dict:
    """Проверить подпись initData и вернуть распарсенные поля. Кидает 401 при ошибке."""
    if not BOT_TOKEN:
        raise HTTPException(500, "BOT_TOKEN not configured on server")

    parsed = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = parsed.pop("hash", None)
    if not received_hash:
        raise HTTPException(401, "missing hash in initData")

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    calc_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(calc_hash, received_hash):
        raise HTTPException(401, "invalid initData signature")

    return parsed


def get_tg_user_id(
    x_telegram_init_data: Optional[str] = Header(default=None),
    x_dev_user_id: Optional[str] = Header(default=None),
) -> int:
    """FastAPI dependency: возвращает Telegram user_id текущего пользователя.

    В DEV_MODE можно прислать заголовок X-Dev-User-Id и обойтись без подписи.
    """
    if DEV_MODE and x_dev_user_id:
        try:
            return int(x_dev_user_id)
        except ValueError:
            raise HTTPException(400, "X-Dev-User-Id must be int")

    if not x_telegram_init_data:
        raise HTTPException(401, "X-Telegram-Init-Data header required")

    parsed = _check_init_data(x_telegram_init_data)
    user_raw = parsed.get("user")
    if not user_raw:
        raise HTTPException(401, "no user in initData")
    try:
        user = json.loads(user_raw)
        return int(user["id"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        raise HTTPException(401, "could not parse user from initData")
