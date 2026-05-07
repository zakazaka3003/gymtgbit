"""SQLite connection helpers."""
from __future__ import annotations

import os
from pathlib import Path
from typing import AsyncIterator

import aiosqlite

# В проде: /data/app.db (Fly volume).
# В дев: рядом с приложением.
DEFAULT_DB_PATH = "/data/app.db" if Path("/data").is_dir() else str(
    Path(__file__).resolve().parent.parent / "app.db"
)
DB_PATH = os.getenv("DB_PATH", DEFAULT_DB_PATH)
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


async def init_db() -> None:
    """Создать схему, если БД пустая."""
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA_PATH.read_text())
        await db.commit()


async def get_db() -> AsyncIterator[aiosqlite.Connection]:
    """FastAPI dependency: открывает соединение на запрос."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys = ON")
        yield db


async def get_or_create_user(db: aiosqlite.Connection, tg_id: int) -> int:
    """Вернёт users.id, создаст если нет."""
    cur = await db.execute("SELECT id FROM users WHERE tg_id = ?", (tg_id,))
    row = await cur.fetchone()
    if row:
        return row["id"]
    cur = await db.execute("INSERT INTO users(tg_id) VALUES (?)", (tg_id,))
    await db.commit()
    return cur.lastrowid
