"""FastAPI backend для Telegram Mini App gymtgbit."""
from __future__ import annotations

import datetime as dt
import os
from contextlib import asynccontextmanager
from typing import Optional

import aiosqlite
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from .auth import get_tg_user_id
from .db import get_db, get_or_create_user, init_db
from .models import (
    DashboardMetric,
    DashboardOut,
    InBodyCreate,
    InBodyPoint,
    ProfileOut,
    ProfilePatch,
    StrengthPR,
    WorkoutOut,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="gymtgbit miniapp", lifespan=lifespan)

# Disable CORS. Do not remove this for full-stack development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


# ---------- Profile ----------

@app.get("/api/profile", response_model=ProfileOut)
async def get_profile(
    tg_id: int = Depends(get_tg_user_id),
    db: aiosqlite.Connection = Depends(get_db),
):
    user_id = await get_or_create_user(db, tg_id)
    cur = await db.execute(
        "SELECT sex, birth_date, height_cm, level, goal, units "
        "FROM profiles WHERE user_id = ?",
        (user_id,),
    )
    row = await cur.fetchone()
    if not row:
        return ProfileOut()
    return ProfileOut(**dict(row))


@app.patch("/api/profile", response_model=ProfileOut)
async def patch_profile(
    body: ProfilePatch,
    tg_id: int = Depends(get_tg_user_id),
    db: aiosqlite.Connection = Depends(get_db),
):
    user_id = await get_or_create_user(db, tg_id)
    cur = await db.execute("SELECT * FROM profiles WHERE user_id = ?", (user_id,))
    existing = await cur.fetchone()

    fields = body.model_dump(exclude_none=True)
    if not fields and not existing:
        raise HTTPException(400, "empty body and no existing profile")

    if existing:
        if fields:
            assignments = ", ".join(f"{k} = ?" for k in fields)
            await db.execute(
                f"UPDATE profiles SET {assignments}, updated_at = datetime('now') "
                f"WHERE user_id = ?",
                (*fields.values(), user_id),
            )
    else:
        defaults = {
            "sex": "М",
            "birth_date": "1990-01-01",
            "height_cm": 175,
            "level": "Средний",
            "goal": "Поддержание",
            "units": "kg",
        }
        defaults.update(fields)
        await db.execute(
            "INSERT INTO profiles(user_id, sex, birth_date, height_cm, level, goal, units) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                user_id,
                defaults["sex"],
                defaults["birth_date"],
                defaults["height_cm"],
                defaults["level"],
                defaults["goal"],
                defaults["units"],
            ),
        )
    await db.commit()
    return await get_profile(tg_id=tg_id, db=db)


# ---------- Dashboard ----------

def _delta(curr: Optional[float], prev: Optional[float]) -> Optional[float]:
    if curr is None or prev is None:
        return None
    return round(curr - prev, 2)


async def _last_inbody(db: aiosqlite.Connection, user_id: int):
    cur = await db.execute(
        "SELECT record_date, weight_kg, pbf_percent, smm_kg "
        "FROM inbody_records WHERE user_id = ? "
        "ORDER BY record_date DESC, id DESC LIMIT 1",
        (user_id,),
    )
    return await cur.fetchone()


async def _inbody_at_or_before(
    db: aiosqlite.Connection, user_id: int, before_date: dt.date
):
    cur = await db.execute(
        "SELECT record_date, weight_kg, pbf_percent, smm_kg "
        "FROM inbody_records WHERE user_id = ? AND record_date <= ? "
        "ORDER BY record_date DESC, id DESC LIMIT 1",
        (user_id, before_date.isoformat()),
    )
    return await cur.fetchone()


@app.get("/api/dashboard", response_model=DashboardOut)
async def get_dashboard(
    tg_id: int = Depends(get_tg_user_id),
    db: aiosqlite.Connection = Depends(get_db),
):
    user_id = await get_or_create_user(db, tg_id)
    today = dt.date.today()

    last = await _last_inbody(db, user_id)
    last_date = None
    days_since = None
    if last:
        last_date_obj = dt.date.fromisoformat(last["record_date"])
        last_date = last["record_date"]
        days_since = (today - last_date_obj).days

        prev_7 = await _inbody_at_or_before(db, user_id, last_date_obj - dt.timedelta(days=7))
        prev_30 = await _inbody_at_or_before(db, user_id, last_date_obj - dt.timedelta(days=30))

        def metric(key: str) -> DashboardMetric:
            v = last[key]
            v7 = prev_7[key] if prev_7 else None
            v30 = prev_30[key] if prev_30 else None
            return DashboardMetric(value=v, delta_7d=_delta(v, v7), delta_30d=_delta(v, v30))

        weight_m = metric("weight_kg")
        fat_m = metric("pbf_percent")
        muscle_m = metric("smm_kg")
    else:
        weight_m = DashboardMetric()
        fat_m = DashboardMetric()
        muscle_m = DashboardMetric()

    cur = await db.execute(
        "SELECT COUNT(*) AS c, COALESCE(SUM(total_tonnage),0) AS t "
        "FROM workouts WHERE user_id = ? AND workout_date >= ?",
        (user_id, (today - dt.timedelta(days=7)).isoformat()),
    )
    row7 = await cur.fetchone()

    cur = await db.execute(
        "SELECT COUNT(*) AS c FROM workouts WHERE user_id = ? AND workout_date >= ?",
        (user_id, (today - dt.timedelta(days=30)).isoformat()),
    )
    row30 = await cur.fetchone()

    cur = await db.execute(
        "SELECT DISTINCT workout_date FROM workouts WHERE user_id = ? "
        "ORDER BY workout_date DESC LIMIT 200",
        (user_id,),
    )
    rows = await cur.fetchall()
    weeks_with_workouts = {dt.date.fromisoformat(r["workout_date"]).isocalendar()[:2] for r in rows}
    streak = 0
    cursor_date = today
    cursor = cursor_date.isocalendar()[:2]
    while cursor in weeks_with_workouts:
        streak += 1
        cursor_date = cursor_date - dt.timedelta(days=7)
        cursor = cursor_date.isocalendar()[:2]

    return DashboardOut(
        last_record_date=last_date,
        days_since_last=days_since,
        weight=weight_m,
        fat_percent=fat_m,
        muscle=muscle_m,
        workouts_7d=row7["c"] if row7 else 0,
        workouts_30d=row30["c"] if row30 else 0,
        tonnage_7d=float(row7["t"]) if row7 else 0.0,
        streak_weeks=streak,
    )


# ---------- InBody ----------

@app.get("/api/inbody/history", response_model=list[InBodyPoint])
async def inbody_history(
    limit: int = Query(180, ge=1, le=1000),
    tg_id: int = Depends(get_tg_user_id),
    db: aiosqlite.Connection = Depends(get_db),
):
    user_id = await get_or_create_user(db, tg_id)
    cur = await db.execute(
        "SELECT record_date, weight_kg, pbf_percent, smm_kg "
        "FROM inbody_records WHERE user_id = ? "
        "ORDER BY record_date ASC LIMIT ?",
        (user_id, limit),
    )
    rows = await cur.fetchall()
    return [InBodyPoint(**dict(r)) for r in rows]


@app.post("/api/inbody", response_model=InBodyPoint)
async def inbody_create(
    body: InBodyCreate,
    tg_id: int = Depends(get_tg_user_id),
    db: aiosqlite.Connection = Depends(get_db),
):
    user_id = await get_or_create_user(db, tg_id)
    record_date = body.record_date or dt.date.today().isoformat()
    if body.weight_kg is None and body.pbf_percent is None and body.smm_kg is None:
        raise HTTPException(400, "at least one of weight_kg/pbf_percent/smm_kg required")
    await db.execute(
        "INSERT INTO inbody_records(user_id, record_date, weight_kg, pbf_percent, smm_kg, source) "
        "VALUES (?,?,?,?,?,'manual') "
        "ON CONFLICT(user_id, record_date) DO UPDATE SET "
        "  weight_kg = COALESCE(excluded.weight_kg, weight_kg), "
        "  pbf_percent = COALESCE(excluded.pbf_percent, pbf_percent), "
        "  smm_kg = COALESCE(excluded.smm_kg, smm_kg)",
        (user_id, record_date, body.weight_kg, body.pbf_percent, body.smm_kg),
    )
    await db.commit()
    return InBodyPoint(
        record_date=record_date,
        weight_kg=body.weight_kg,
        pbf_percent=body.pbf_percent,
        smm_kg=body.smm_kg,
    )


# ---------- Workouts ----------

@app.get("/api/workouts", response_model=list[WorkoutOut])
async def workouts_list(
    days: int = Query(60, ge=1, le=365),
    tg_id: int = Depends(get_tg_user_id),
    db: aiosqlite.Connection = Depends(get_db),
):
    user_id = await get_or_create_user(db, tg_id)
    since = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    cur = await db.execute(
        "SELECT id, workout_date, group_name, total_sets, total_reps, total_tonnage "
        "FROM workouts WHERE user_id = ? AND workout_date >= ? "
        "ORDER BY workout_date DESC",
        (user_id, since),
    )
    rows = await cur.fetchall()
    return [WorkoutOut(**dict(r)) for r in rows]


@app.get("/api/strength/prs", response_model=list[StrengthPR])
async def strength_prs(
    tg_id: int = Depends(get_tg_user_id),
    db: aiosqlite.Connection = Depends(get_db),
):
    user_id = await get_or_create_user(db, tg_id)
    cur = await db.execute(
        """
        SELECT e.name AS exercise, sr.weight, sr.reps, sr.record_date
        FROM strength_records sr
        JOIN exercises e ON e.id = sr.exercise_id
        JOIN (
          SELECT exercise_id, MAX(weight) AS max_w
          FROM strength_records WHERE user_id = ?
          GROUP BY exercise_id
        ) best ON best.exercise_id = sr.exercise_id AND best.max_w = sr.weight
        WHERE sr.user_id = ?
        GROUP BY sr.exercise_id
        ORDER BY sr.record_date DESC
        LIMIT 20
        """,
        (user_id, user_id),
    )
    rows = await cur.fetchall()
    return [StrengthPR(**dict(r)) for r in rows]


# ---------- Demo seed (only in DEV_MODE) ----------

@app.post("/api/_demo/seed")
async def demo_seed(
    tg_id: int = Depends(get_tg_user_id),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Засеять демо-данные для текущего юзера. Используется в DEV_MODE."""
    if os.getenv("DEV_MODE", "0") != "1":
        raise HTTPException(403, "demo seed allowed only in DEV_MODE")
    user_id = await get_or_create_user(db, tg_id)
    await db.execute(
        "INSERT OR REPLACE INTO profiles(user_id, sex, birth_date, height_cm, level, goal, units) "
        "VALUES (?, 'М', '1995-04-12', 182, 'Средний', 'Похудение', 'kg')",
        (user_id,),
    )
    today = dt.date.today()
    samples = [
        (60, 80.5, 18.2, 34.0),
        (45, 80.1, 17.8, 34.1),
        (30, 79.6, 17.0, 34.3),
        (14, 78.9, 16.0, 34.6),
        (7,  78.5, 15.4, 34.9),
        (1,  77.9, 14.8, 35.2),
    ]
    for offset, w, p, s in samples:
        d = (today - dt.timedelta(days=offset)).isoformat()
        await db.execute(
            "INSERT OR REPLACE INTO inbody_records(user_id, record_date, weight_kg, pbf_percent, smm_kg, source) "
            "VALUES (?, ?, ?, ?, ?, 'manual')",
            (user_id, d, w, p, s),
        )
    for offset, group, tonnage in [
        (1, "Грудь", 8400),
        (3, "Ноги", 12450),
        (5, "Спина", 9100),
        (8, "Грудь", 7900),
        (10, "Плечи", 6200),
        (12, "Ноги", 11800),
        (15, "Спина", 9300),
        (17, "Грудь", 8100),
        (22, "Ноги", 12000),
        (24, "Спина", 8800),
    ]:
        d = (today - dt.timedelta(days=offset)).isoformat()
        await db.execute(
            "INSERT INTO workouts(user_id, workout_date, group_name, total_sets, total_reps, total_tonnage) "
            "VALUES (?, ?, ?, 18, 110, ?)",
            (user_id, d, group, tonnage),
        )
    await db.commit()
    return {"ok": True}
