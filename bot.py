"""Telegram bot — UX redesign.

- Только inline-клавиатуры (никаких ReplyKeyboard).
- HTML parse_mode по умолчанию (не ломается на ``_`` в названиях упражнений).
- Кнопки с ``style``: primary (синий), success (зелёный), danger (красный)
  поддерживаются с Bot API 9.4 / aiogram 3.27. На старых клиентах TG
  отображаются обычным цветом — поэтому на функционал это не влияет.
- Сообщения **редактируются** на месте (``edit_text``), а не плодят новые.
- На всех экранах есть ``⬅️ Назад`` и ``🏠 Меню``. В FSM — ``❌ Отмена``.
- Команды: /start, /menu, /dashboard, /cancel.
"""

import html
import io
import os
import asyncio
import datetime as dt
from dataclasses import dataclass
from typing import Optional

import aiosqlite
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BotCommand,
    BotCommandScopeDefault,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from dashboard import fetch_dashboard_data, format_dashboard
from ocr_utils import run_inbody_ocr


load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
DB_PATH = os.getenv("DB_PATH", "gym_bot.db")
PADDLE_USE_GPU = os.getenv("PADDLE_USE_GPU", "0") == "1"
TESSERACT_CMD = os.getenv("TESSERACT_CMD")
WEBAPP_URL = os.getenv("WEBAPP_URL", "").strip()  # https-URL мини-приложения

if TESSERACT_CMD:
    try:
        import pytesseract
        pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD
    except Exception:
        pass


# -------------------------
# Default exercises (RU)
# -------------------------
DEFAULT_EXERCISES = {
    "Грудь": [
        "Жим лёжа (штанга)",
        "Жим лёжа (гантели)",
        "Жим на наклонной скамье",
        "Разводка гантелей лёжа",
        "Отжимания на брусьях",
    ],
    "Спина": [
        "Подтягивания",
        "Тяга верхнего блока",
        "Тяга штанги в наклоне",
        "Тяга гантели одной рукой",
        "Становая тяга",
    ],
    "Ноги": [
        "Приседания со штангой",
        "Жим ногами",
        "Румынская тяга",
        "Выпады",
        "Сгибания ног лёжа",
        "Разгибания ног",
    ],
    "Плечи": [
        "Жим штанги стоя",
        "Жим гантелей сидя",
        "Махи в стороны",
        "Махи в наклоне",
    ],
    "Руки": [
        "Сгибания на бицепс (штанга)",
        "Сгибания на бицепс (гантели)",
        "Французский жим",
        "Разгибания на блоке",
    ],
    "Полное тело": [
        "Бёрпи",
        "Планка",
        "Тяга гири",
        "Приседания с гирей (goblet)",
    ]
}


# -------------------------
# FSM states
# -------------------------
class Onboarding(StatesGroup):
    sex = State()
    birth_year = State()
    birth_month = State()
    birth_day = State()
    height = State()
    level = State()
    goal = State()


class InBodyFSM(StatesGroup):
    add_mode = State()            # choose manual/photo
    manual_weight = State()
    manual_pbf = State()
    manual_smm = State()

    photo_wait = State()
    ocr_confirm = State()
    ocr_edit_weight = State()
    ocr_edit_pbf = State()
    ocr_edit_smm = State()
    ocr_edit_date = State()


class StrengthFSM(StatesGroup):
    choose_category = State()
    choose_exercise = State()
    enter_weight = State()
    enter_reps = State()


class ExerciseManageFSM(StatesGroup):
    add_custom_name = State()
    rename_choose = State()
    rename_new_name = State()
    delete_choose = State()


class WorkoutFSM(StatesGroup):
    choose_group = State()
    choose_exercise = State()
    set_weight = State()
    set_reps = State()
    in_workout = State()


# -------------------------
# UI helpers
# -------------------------
def H(s) -> str:
    """Escape user-provided string for safe HTML output."""
    if s is None:
        return ""
    return html.escape(str(s), quote=False)


def btn(
    text: str,
    callback_data: Optional[str] = None,
    *,
    style: Optional[str] = None,
    url: Optional[str] = None,
    web_app: Optional[WebAppInfo] = None,
) -> InlineKeyboardButton:
    """Helper that builds an InlineKeyboardButton with optional Bot API 9.4 ``style``.

    Falls back to a plain button if the installed aiogram doesn't support ``style``.
    """
    kw = {"text": text}
    if callback_data is not None:
        kw["callback_data"] = callback_data
    if url is not None:
        kw["url"] = url
    if web_app is not None:
        kw["web_app"] = web_app
    if style is not None:
        try:
            return InlineKeyboardButton(**kw, style=style)
        except Exception:
            pass
    return InlineKeyboardButton(**kw)


def webapp_btn(text: str = "🚀 Открыть приложение"):
    """Primary CTA button that opens the mini-app (or None if WEBAPP_URL is empty)."""
    if not WEBAPP_URL:
        return None
    return btn(text, None, web_app=WebAppInfo(url=WEBAPP_URL), style="primary")


def main_menu_inline() -> InlineKeyboardMarkup:
    """Single inline main menu (replaces the old reply-keyboard)."""
    rows = []
    wb = webapp_btn()
    if wb:
        rows.append([wb])
    rows += [
        [btn("📊 Дашборд", "open_dashboard"), btn("📷 Замер", "open_inbody")],
        [btn("🏋️ Тренировка", "open_workout"), btn("💪 Силовые", "open_strength")],
        [btn("📈 Аналитика", "open_analytics"), btn("🏆 Рекорды", "strength_pr")],
        [btn("👤 Профиль", "open_profile"), btn("⚙️ Настройки", "open_settings")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def with_back_home(rows: list, *, back_cb: Optional[str] = None) -> InlineKeyboardMarkup:
    """Append a unified Back/Home navigation row to a list of button rows."""
    nav = []
    if back_cb:
        nav.append(btn("⬅️ Назад", back_cb))
    nav.append(btn("🏠 Меню", "go_main_menu"))
    return InlineKeyboardMarkup(inline_keyboard=rows + [nav])


def back_to_menu_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[btn("🏠 Меню", "go_main_menu")]])


def cancel_fsm_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[btn("❌ Отмена", "go_main_menu", style="danger")]])


def dashboard_inline_kb() -> InlineKeyboardMarkup:
    rows = []
    wb = webapp_btn()
    if wb:
        rows.append([wb])
    rows.append([
        btn("📷 Новый замер", "inbody_add", style="success"),
        btn("🏋️ Тренировка", "workout_start", style="success"),
    ])
    rows.append([
        btn("🔄 Обновить", "dashboard_refresh"),
        btn("🏠 Меню", "go_main_menu"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def webapp_inline_kb() -> Optional[InlineKeyboardMarkup]:
    """Backward-compatible: standalone webapp button row."""
    wb = webapp_btn()
    if not wb:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[wb]])


def profile_menu_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn("👁️ Посмотреть", "profile_view"), btn("✏️ Изменить", "profile_edit")],
        [btn("🎯 Цели", "profile_goals")],
        [btn("🗑 Удалить все данные", "wipe_all", style="danger")],
        [btn("🏠 Меню", "go_main_menu")],
    ])


def inbody_menu_inline() -> InlineKeyboardMarkup:
    return with_back_home([
        [btn("➕ Новый замер", "inbody_add", style="success")],
        [btn("📋 История", "inbody_history"), btn("📊 Графики", "inbody_charts")],
        [btn("🧾 Сравнить два", "inbody_compare")],
    ])


def inbody_add_mode_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn("📷 С фото InBody", "inbody_add_photo", style="primary")],
        [btn("✍️ Ввести вручную", "inbody_add_manual")],
        [btn("⬅️ Назад", "open_inbody"), btn("🏠 Меню", "go_main_menu")],
    ])


def strength_menu_inline() -> InlineKeyboardMarkup:
    return with_back_home([
        [btn("➕ Записать результат", "strength_add", style="success")],
        [btn("🏆 Рекорды (PR)", "strength_pr"), btn("📊 Графики", "an_strength")],
        [btn("📚 Упражнения", "strength_exercises")],
    ])


def workout_menu_inline() -> InlineKeyboardMarkup:
    return with_back_home([
        [btn("▶️ Начать тренировку", "workout_start", style="primary")],
        [btn("🧾 История", "workout_history"), btn("🧠 Совет (AI)", "workout_ai_tip")],
    ])


def analytics_menu_inline() -> InlineKeyboardMarkup:
    return with_back_home([
        [btn("📄 Замеры тела", "an_body")],
        [btn("💪 Силовые", "an_strength")],
        [btn("🧩 Общий прогресс", "an_total")],
        [btn("🔮 Прогноз 30 дней", "an_forecast")],
    ])


def settings_menu_inline() -> InlineKeyboardMarkup:
    return with_back_home([
        [btn("🔁 Единицы измерения", "set_units")],
        [btn("🔔 Напоминания", "set_reminders")],
        [btn("🧹 Очистить кэш фото", "set_clear_cache", style="danger")],
    ])


def wipe_confirm_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn("🗑 Да, удалить всё", "wipe_all_confirm", style="danger")],
        [btn("Отмена", "open_profile")],
    ])


def ocr_confirm_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn("✅ Сохранить", "ocr_save", style="success"), btn("✏️ Цифры", "ocr_edit")],
        [btn("📅 Изменить дату", "ocr_edit_date")],
        [btn("❌ Отмена", "ocr_cancel", style="danger")],
    ])


def workout_after_set_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn("➕ Ещё подход", "wo_more", style="primary")],
        [btn("➡️ Сменить упражнение", "wo_end_ex"), btn("🏁 Завершить", "wo_finish", style="success")],
    ])


def inline_year_picker(page_start: int) -> InlineKeyboardMarkup:
    years = list(range(page_start, page_start + 4))
    row = [
        btn("◀️", f"birth_y_prev:{page_start}"),
        btn(str(years[0]), f"birth_y:{years[0]}"),
        btn(str(years[1]), f"birth_y:{years[1]}"),
        btn(str(years[2]), f"birth_y:{years[2]}"),
        btn("▶️", f"birth_y_next:{page_start}"),
    ]
    row2 = [btn(str(years[3]), f"birth_y:{years[3]}")]
    nav = [btn("❌ Отмена", "onb_cancel", style="danger")]
    return InlineKeyboardMarkup(inline_keyboard=[row, row2, nav])


def inline_month_picker() -> InlineKeyboardMarkup:
    months_short = ["янв", "фев", "мар", "апр", "май", "июн",
                    "июл", "авг", "сен", "окт", "ноя", "дек"]
    kb, row = [], []
    for i, name in enumerate(months_short, start=1):
        row.append(btn(name, f"birth_m:{i}"))
        if len(row) == 4:
            kb.append(row)
            row = []
    if row:
        kb.append(row)
    kb.append([btn("⬅️ Год", "birth_back_year"), btn("❌ Отмена", "onb_cancel", style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


def inline_day_picker(year: int, month: int) -> InlineKeyboardMarkup:
    if month == 2:
        leap = (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0))
        days = 29 if leap else 28
    elif month in (4, 6, 9, 11):
        days = 30
    else:
        days = 31

    kb, row = [], []
    for d in range(1, days + 1):
        row.append(btn(str(d), f"birth_d:{d}"))
        if len(row) == 7:
            kb.append(row)
            row = []
    if row:
        kb.append(row)
    kb.append([btn("⬅️ Месяц", "birth_back_month"), btn("❌ Отмена", "onb_cancel", style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


def onb_sex_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn("Мужской", "onb_sex:М"), btn("Женский", "onb_sex:Ж")],
        [btn("❌ Отмена", "onb_cancel", style="danger")],
    ])


def onb_level_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn("Новичок", "onb_level:Новичок")],
        [btn("Средний", "onb_level:Средний")],
        [btn("Продвинутый", "onb_level:Продвинутый")],
        [btn("❌ Отмена", "onb_cancel", style="danger")],
    ])


def onb_goal_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn("Набор мышц", "onb_goal:Набор мышц")],
        [btn("Похудение", "onb_goal:Похудение")],
        [btn("Поддержание", "onb_goal:Поддержание")],
        [btn("Сила", "onb_goal:Сила"), btn("Выносливость", "onb_goal:Выносливость")],
        [btn("❌ Отмена", "onb_cancel", style="danger")],
    ])


# Backward-compat alias (handlers may import this)
def main_menu_kb():
    return main_menu_inline()


async def safe_edit(message: Message, text: str, reply_markup=None) -> Message:
    """Edit message in place; on photo/edit-failure, send a new one and try to delete the old."""
    try:
        if message.photo or message.video or message.document:
            try:
                await message.delete()
            except Exception:
                pass
            return await message.answer(text, reply_markup=reply_markup)
        return await message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest:
        return await message.answer(text, reply_markup=reply_markup)
    except Exception:
        return await message.answer(text, reply_markup=reply_markup)


def pretty_profile_card(p):
    return (
        "👤 *Профиль*\n"
        f"Пол: *{p['sex']}*\n"
        f"Дата рождения: *{p['birth_date']}*\n"
        f"Рост: *{p['height_cm']} см*\n"
        f"Уровень: *{p['level']}*\n"
        f"Цель: *{p['goal']}*\n"
        f"Единицы: *{'кг' if p.get('units','kg')=='kg' else 'фунты'}*"
    )


def today_ymd():
    return dt.date.today().strftime("%Y-%m-%d")


# -------------------------
# DB layer
# -------------------------
SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    tg_id INTEGER NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS profiles (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL UNIQUE,
    sex TEXT CHECK(sex IN ('М','Ж')) NOT NULL,
    birth_date TEXT NOT NULL,
    height_cm INTEGER CHECK(height_cm BETWEEN 120 AND 230) NOT NULL,
    level TEXT NOT NULL,
    goal TEXT NOT NULL,
    units TEXT NOT NULL DEFAULT 'kg',
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS inbody_records (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    record_date TEXT NOT NULL,
    weight_kg REAL,
    pbf_percent REAL,
    smm_kg REAL,
    source TEXT NOT NULL DEFAULT 'manual',
    confidence REAL NOT NULL DEFAULT 0.0,
    raw_ocr_text TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_inbody_unique_date
ON inbody_records(user_id, record_date);

CREATE INDEX IF NOT EXISTS idx_inbody_user_date
ON inbody_records(user_id, record_date);

CREATE TABLE IF NOT EXISTS exercises (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    category TEXT NOT NULL,
    name TEXT NOT NULL,
    is_custom INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_ex_unique_name_per_user
ON exercises(user_id, name);

CREATE INDEX IF NOT EXISTS idx_ex_user_cat
ON exercises(user_id, category);

CREATE TABLE IF NOT EXISTS strength_records (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    exercise_id INTEGER NOT NULL,
    record_date TEXT NOT NULL,
    weight REAL NOT NULL,
    reps INTEGER NOT NULL CHECK(reps BETWEEN 1 AND 100),
    rpe REAL,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY(exercise_id) REFERENCES exercises(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_strength_user_ex_date
ON strength_records(user_id, exercise_id, record_date);

CREATE TABLE IF NOT EXISTS workouts (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    workout_date TEXT NOT NULL,
    group_name TEXT NOT NULL,
    total_sets INTEGER NOT NULL DEFAULT 0,
    total_reps INTEGER NOT NULL DEFAULT 0,
    total_tonnage REAL NOT NULL DEFAULT 0.0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_workouts_user_date
ON workouts(user_id, workout_date);

CREATE TABLE IF NOT EXISTS workout_items (
    id INTEGER PRIMARY KEY,
    workout_id INTEGER NOT NULL,
    exercise_id INTEGER NOT NULL,
    set_no INTEGER NOT NULL,
    weight REAL NOT NULL,
    reps INTEGER NOT NULL CHECK(reps BETWEEN 1 AND 200),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY(workout_id) REFERENCES workouts(id) ON DELETE CASCADE,
    FOREIGN KEY(exercise_id) REFERENCES exercises(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_workout_items_workout
ON workout_items(workout_id, exercise_id);
"""

async def fetchone(db: aiosqlite.Connection, query: str, params=()):
    cur = await db.execute(query, params)
    row = await cur.fetchone()
    await cur.close()
    return row


async def fetchall(db: aiosqlite.Connection, query: str, params=()):
    cur = await db.execute(query, params)
    rows = await cur.fetchall()
    await cur.close()
    return rows



async def db_init():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA_SQL)
        await db.commit()


async def db_get_user_id(db, tg_id: int):
    row = await fetchone(db, "SELECT id FROM users WHERE tg_id=?", (tg_id,))
    if row:
        return row[0]
    cur = await db.execute("INSERT INTO users(tg_id) VALUES(?)", (tg_id,))
    await db.commit()
    return cur.lastrowid


async def db_profile_exists(db, user_id: int):
    row = await fetchone(db, "SELECT 1 FROM profiles WHERE user_id=?", (user_id,))
    return bool(row)


async def db_get_profile(db, user_id: int):
    row = await fetchone(db, """
        SELECT sex,birth_date,height_cm,level,goal,units
        FROM profiles WHERE user_id=?
    """, (user_id,))
    if not row:
        return None
    return {
        "sex": row[0], "birth_date": row[1], "height_cm": row[2],
        "level": row[3], "goal": row[4], "units": row[5]
    }



async def db_upsert_profile(db, user_id: int, sex: str, birth_date: str, height_cm: int, level: str, goal: str):
    exists = await db_profile_exists(db, user_id)
    if exists:
        await db.execute("""
            UPDATE profiles
            SET sex=?, birth_date=?, height_cm=?, level=?, goal=?, updated_at=datetime('now')
            WHERE user_id=?
        """, (sex, birth_date, height_cm, level, goal, user_id))
    else:
        await db.execute("""
            INSERT INTO profiles(user_id, sex, birth_date, height_cm, level, goal)
            VALUES(?,?,?,?,?,?)
        """, (user_id, sex, birth_date, height_cm, level, goal))
    await db.commit()


async def db_seed_default_exercises(db):
    # user_id=0 reserved for system
    for cat, items in DEFAULT_EXERCISES.items():
        for name in items:
            await db.execute("""
                INSERT OR IGNORE INTO exercises(user_id, category, name, is_custom, is_active)
                VALUES(0,?,?,0,1)
            """, (cat, name))
    await db.commit()


async def db_list_exercises(db, user_id: int, category: str = None):
    if category:
        rows = await fetchall(db, """
            SELECT id,category,name,is_custom,user_id FROM exercises
            WHERE is_active=1 AND category=? AND (user_id=0 OR user_id=?)
            ORDER BY user_id DESC, name ASC
        """, (category, user_id))
    else:
        rows = await fetchall(db,"""
            SELECT id,category,name,is_custom,user_id FROM exercises
            WHERE is_active=1 AND (user_id=0 OR user_id=?)
            ORDER BY category ASC, user_id DESC, name ASC
        """, (user_id,))
    return rows


async def db_add_custom_exercise(db, user_id: int, category: str, name: str):
    await db.execute("""
        INSERT OR IGNORE INTO exercises(user_id, category, name, is_custom, is_active)
        VALUES(?,?,?,1,1)
    """, (user_id, category, name.strip()))
    await db.commit()


async def db_rename_custom_exercise(db, user_id: int, ex_id: int, new_name: str):
    await db.execute("""
        UPDATE exercises SET name=? WHERE id=? AND user_id=? AND is_custom=1
    """, (new_name.strip(), ex_id, user_id))
    await db.commit()


async def db_delete_custom_exercise(db, user_id: int, ex_id: int):
    await db.execute("""
        UPDATE exercises SET is_active=0 WHERE id=? AND user_id=? AND is_custom=1
    """, (ex_id, user_id))
    await db.commit()


async def db_add_strength_record(db, user_id: int, exercise_id: int, weight: float, reps: int, record_date: str):
    await db.execute("""
        INSERT INTO strength_records(user_id, exercise_id, weight, reps, record_date)
        VALUES(?,?,?,?,?)
    """, (user_id, exercise_id, weight, reps, record_date))
    await db.commit()


async def db_strength_pr(db, user_id: int):
    # simple PR: best estimated 1RM by exercise
    rows = await fetchall(db,"""
        SELECT e.name,
               MAX(sr.weight) AS best_weight,
               MAX(sr.weight*(1+sr.reps/30.0)) AS best_e1rm,
               MAX(sr.weight*sr.reps) AS best_volume
        FROM strength_records sr
        JOIN exercises e ON e.id=sr.exercise_id
        WHERE sr.user_id=?
        GROUP BY sr.exercise_id
        ORDER BY best_e1rm DESC
        LIMIT 20
    """, (user_id,))
    return rows


async def db_inbody_last(db, user_id: int):
    row = await fetchone(db,"""
        SELECT record_date, weight_kg, pbf_percent, smm_kg
        FROM inbody_records
        WHERE user_id=?
        ORDER BY record_date DESC
        LIMIT 1
    """, (user_id,))
    if not row:
        return None
    return {"record_date": row[0], "weight_kg": row[1], "pbf_percent": row[2], "smm_kg": row[3]}


async def db_add_inbody(db, user_id: int, record_date: str, weight_kg, pbf_percent, smm_kg, source: str, confidence: float, raw_text: str):
    await db.execute("""
        INSERT OR REPLACE INTO inbody_records(user_id, record_date, weight_kg, pbf_percent, smm_kg, source, confidence, raw_ocr_text)
        VALUES(?,?,?,?,?,?,?,?)
    """, (user_id, record_date, weight_kg, pbf_percent, smm_kg, source, confidence, raw_text))
    await db.commit()


async def db_inbody_history(db, user_id: int, limit: int = 12):
    rows = await fetchall(db,"""
        SELECT record_date, weight_kg, pbf_percent, smm_kg, source, confidence
        FROM inbody_records
        WHERE user_id=?
        ORDER BY record_date DESC
        LIMIT ?
    """, (user_id, limit))
    return rows


async def db_inbody_all(db, user_id: int):
    rows = await fetchall(db,"""
        SELECT record_date, weight_kg, pbf_percent, smm_kg
        FROM inbody_records
        WHERE user_id=?
        ORDER BY record_date ASC
    """, (user_id,))
    return rows


async def db_workout_create(db, user_id: int, workout_date: str, group_name: str):
    cur = await db.execute("""
        INSERT INTO workouts(user_id, workout_date, group_name)
        VALUES(?,?,?)
    """, (user_id, workout_date, group_name))
    await db.commit()
    return cur.lastrowid


async def db_workout_add_item(db, workout_id: int, exercise_id: int, set_no: int, weight: float, reps: int):
    await db.execute("""
        INSERT INTO workout_items(workout_id, exercise_id, set_no, weight, reps)
        VALUES(?,?,?,?,?)
    """, (workout_id, exercise_id, set_no, weight, reps))
    await db.commit()


async def db_workout_finish(db, workout_id: int):
    row = await fetchone(db,"""
        SELECT COUNT(*), COALESCE(SUM(reps),0), COALESCE(SUM(weight*reps),0)
        FROM workout_items
        WHERE workout_id=?
    """, (workout_id,))
    total_sets, total_reps, total_tonnage = row
    await db.execute("""
        UPDATE workouts
        SET total_sets=?, total_reps=?, total_tonnage=?
        WHERE id=?
    """, (total_sets, total_reps, float(total_tonnage), workout_id))
    await db.commit()
    return total_sets, total_reps, float(total_tonnage)


async def db_workout_history(db, user_id: int, limit: int = 10):
    rows = await fetchall(db,"""
        SELECT workout_date, group_name, total_sets, total_reps, total_tonnage
        FROM workouts
        WHERE user_id=?
        ORDER BY workout_date DESC, id DESC
        LIMIT ?
    """, (user_id, limit))
    return rows


async def db_wipe_all(db, user_id: int):
    # profiles cascade deletes user? no. we wipe tables explicitly.
    await db.execute("DELETE FROM inbody_records WHERE user_id=?", (user_id,))
    await db.execute("DELETE FROM strength_records WHERE user_id=?", (user_id,))
    await db.execute("DELETE FROM workout_items WHERE workout_id IN (SELECT id FROM workouts WHERE user_id=?)", (user_id,))
    await db.execute("DELETE FROM workouts WHERE user_id=?", (user_id,))
    await db.execute("DELETE FROM exercises WHERE user_id=?", (user_id,))
    await db.execute("DELETE FROM profiles WHERE user_id=?", (user_id,))
    await db.commit()


# -------------------------
# Plot helpers
# -------------------------
def plot_series(dates, values, title, ylabel):
    plt.figure()
    plt.plot(dates, values, marker="o")
    plt.title(title)
    plt.ylabel(ylabel)
    plt.xticks(rotation=45)
    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=140)
    plt.close()
    buf.seek(0)
    return buf


# -------------------------
# Bot
# -------------------------
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher()


# -------------------------
# Start / Main menu
# -------------------------
def _fmt_main_menu_text(name: str, last_inbody: dict | None) -> str:
    """Карточка-приветствие для главного меню (HTML)."""
    head = f"Привет, <b>{H(name)}</b> 👋\n\n"
    if not last_inbody:
        body = (
            "Я помогу вести прогресс в зале — замеры, силовые, тренировки и аналитика.\n\n"
            "Начни с замера — фото InBody или вручную. И открой приложение,\n"
            "в нём всё видно нагляднее.\n"
        )
    else:
        d = last_inbody
        weight = "—" if d.get("weight_kg") is None else f"{float(d['weight_kg']):.1f}"
        pbf = "—" if d.get("pbf_percent") is None else f"{float(d['pbf_percent']):.1f}"
        smm = "—" if d.get("smm_kg") is None else f"{float(d['smm_kg']):.1f}"
        body = (
            "<b>Последний замер</b>\n"
            f"📅 {H(d.get('record_date'))}\n"
            f"⚖️ Вес <b>{weight}</b> кг   "
            f"🔥 Жир <b>{pbf}</b> %   "
            f"💪 Мышцы <b>{smm}</b> кг\n"
        )
    return head + body + "\n<i>Выбери раздел ниже:</i>"


async def _build_main_menu(tg_id: int, name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        user_id = await db_get_user_id(db, tg_id)
        last = await db_inbody_last(db, user_id)
    return _fmt_main_menu_text(name, last), main_menu_inline()


async def _send_main_menu(target: Message, tg_id: int, name: str):
    text, kb = await _build_main_menu(tg_id, name)
    await target.answer(text, reply_markup=kb)


async def _edit_main_menu(message: Message, tg_id: int, name: str):
    text, kb = await _build_main_menu(tg_id, name)
    await safe_edit(message, text, reply_markup=kb)


async def _send_dashboard(target: Message, tg_id: int):
    """Отрисовать дашборд (новое сообщение)."""
    async with aiosqlite.connect(DB_PATH) as db:
        user_id = await db_get_user_id(db, tg_id)
        profile = await db_get_profile(db, user_id)
        data = await fetch_dashboard_data(db, user_id)
    text = format_dashboard(data, profile)
    await target.answer(text, reply_markup=dashboard_inline_kb())


async def _edit_dashboard(message: Message, tg_id: int):
    """Отрисовать дашборд (редактируем текущее сообщение)."""
    async with aiosqlite.connect(DB_PATH) as db:
        user_id = await db_get_user_id(db, tg_id)
        profile = await db_get_profile(db, user_id)
        data = await fetch_dashboard_data(db, user_id)
    text = format_dashboard(data, profile)
    await safe_edit(message, text, reply_markup=dashboard_inline_kb())


@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    async with aiosqlite.connect(DB_PATH) as db:
        user_id = await db_get_user_id(db, message.from_user.id)
        await db_seed_default_exercises(db)
        exists = await db_profile_exists(db, user_id)
    name = message.from_user.first_name or "друг"

    if not exists:
        await message.answer(
            f"Привет, <b>{H(name)}</b> 👋\n\n"
            "Я помогу вести <b>прогресс в зале</b> — замеры, силовые, тренировки и графики.\n\n"
            "Сначала короткая настройка профиля."
        )
        await state.set_state(Onboarding.sex)
        await message.answer("<b>Шаг 1/5</b> · Пол:", reply_markup=onb_sex_inline())
    else:
        await _send_main_menu(message, message.from_user.id, name)


@dp.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext):
    await state.clear()
    name = message.from_user.first_name or "друг"
    await _send_main_menu(message, message.from_user.id, name)


@dp.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    cur = await state.get_state()
    await state.clear()
    name = message.from_user.first_name or "друг"
    if cur:
        await message.answer("Отменил ввод.")
    await _send_main_menu(message, message.from_user.id, name)


@dp.message(Command("dashboard"))
async def cmd_dashboard(message: Message, state: FSMContext):
    await state.clear()
    await _send_dashboard(message, message.from_user.id)


@dp.callback_query(F.data == "open_dashboard")
async def cb_open_dashboard(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await _edit_dashboard(cb.message, cb.from_user.id)
    await cb.answer()


@dp.callback_query(F.data == "dashboard_refresh")
async def dashboard_refresh(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await _edit_dashboard(cb.message, cb.from_user.id)
    await cb.answer("Обновлено")


@dp.callback_query(F.data == "go_main_menu")
async def go_main_menu(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    name = cb.from_user.first_name or "друг"
    await _edit_main_menu(cb.message, cb.from_user.id, name)
    await cb.answer()


# -------------------------
# Sections openers (callback only — reply-keyboard removed)
# -------------------------
@dp.callback_query(F.data == "open_profile")
async def open_profile(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await safe_edit(cb.message, "👤 <b>Профиль</b>", reply_markup=profile_menu_inline())
    await cb.answer()


@dp.callback_query(F.data == "open_inbody")
async def open_inbody(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await safe_edit(cb.message, "📷 <b>Замеры InBody</b>", reply_markup=inbody_menu_inline())
    await cb.answer()


@dp.callback_query(F.data == "open_strength")
async def open_strength(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await safe_edit(cb.message, "💪 <b>Силовые</b>", reply_markup=strength_menu_inline())
    await cb.answer()


@dp.callback_query(F.data == "open_workout")
async def open_workout(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await safe_edit(cb.message, "🏋️ <b>Тренировка</b>", reply_markup=workout_menu_inline())
    await cb.answer()


@dp.callback_query(F.data == "open_analytics")
async def open_analytics(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await safe_edit(cb.message, "📈 <b>Аналитика</b>", reply_markup=analytics_menu_inline())
    await cb.answer()


@dp.callback_query(F.data == "open_settings")
async def open_settings(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await safe_edit(cb.message, "⚙️ <b>Настройки</b>", reply_markup=settings_menu_inline())
    await cb.answer()


# -------------------------
# Onboarding: sex -> birth date picker -> height -> level -> goal
# -------------------------
@dp.callback_query(Onboarding.sex, F.data.startswith("onb_sex:"))
async def onb_sex(cb: CallbackQuery, state: FSMContext):
    sex = cb.data.split(":")[1]
    await state.update_data(sex=sex)
    await state.set_state(Onboarding.birth_year)

    await state.update_data(year_page=2000)
    await safe_edit(
        cb.message,
        "<b>Шаг 2/5</b> · Дата рождения — выбери <b>год</b>:",
        reply_markup=inline_year_picker(2000),
    )
    await cb.answer()


@dp.callback_query(F.data == "onb_cancel")
async def onb_cancel(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    name = cb.from_user.first_name or "друг"
    await _edit_main_menu(cb.message, cb.from_user.id, name)
    await cb.answer("Ок, отменил")


@dp.callback_query(Onboarding.birth_year, F.data.startswith("birth_y_prev:"))
async def birth_prev(cb: CallbackQuery, state: FSMContext):
    start = int(cb.data.split(":")[1])
    new_start = max(1980, start - 4)
    await cb.message.edit_reply_markup(reply_markup=inline_year_picker(new_start))
    await cb.answer()


@dp.callback_query(Onboarding.birth_year, F.data.startswith("birth_y_next:"))
async def birth_next(cb: CallbackQuery, state: FSMContext):
    start = int(cb.data.split(":")[1])
    new_start = min(2012, start + 4)
    await cb.message.edit_reply_markup(reply_markup=inline_year_picker(new_start))
    await cb.answer()


@dp.callback_query(Onboarding.birth_year, F.data.startswith("birth_y:"))
async def birth_pick_year(cb: CallbackQuery, state: FSMContext):
    year = int(cb.data.split(":")[1])
    await state.update_data(birth_year=year)
    await state.set_state(Onboarding.birth_month)
    await safe_edit(
        cb.message,
        f"<b>Шаг 2/5</b> · Год <b>{year}</b> ✅ — теперь выбери <b>месяц</b>:",
        reply_markup=inline_month_picker(),
    )
    await cb.answer()


@dp.callback_query(Onboarding.birth_month, F.data == "birth_back_year")
async def birth_back_year(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Onboarding.birth_year)
    await safe_edit(
        cb.message,
        "<b>Шаг 2/5</b> · Дата рождения — выбери <b>год</b>:",
        reply_markup=inline_year_picker(2000),
    )
    await cb.answer()


@dp.callback_query(Onboarding.birth_month, F.data.startswith("birth_m:"))
async def birth_pick_month(cb: CallbackQuery, state: FSMContext):
    month = int(cb.data.split(":")[1])
    data = await state.get_data()
    year = data["birth_year"]
    await state.update_data(birth_month=month)
    await state.set_state(Onboarding.birth_day)
    await safe_edit(
        cb.message,
        f"<b>Шаг 2/5</b> · <b>{year}-{month:02d}</b> ✅ — выбери <b>день</b>:",
        reply_markup=inline_day_picker(year, month),
    )
    await cb.answer()


@dp.callback_query(Onboarding.birth_day, F.data == "birth_back_month")
async def birth_back_month(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    year = data["birth_year"]
    await state.set_state(Onboarding.birth_month)
    await safe_edit(
        cb.message,
        f"<b>Шаг 2/5</b> · Год <b>{year}</b> ✅ — выбери <b>месяц</b>:",
        reply_markup=inline_month_picker(),
    )
    await cb.answer()


@dp.callback_query(Onboarding.birth_day, F.data.startswith("birth_d:"))
async def birth_pick_day(cb: CallbackQuery, state: FSMContext):
    day = int(cb.data.split(":")[1])
    data = await state.get_data()
    year = data["birth_year"]
    month = data["birth_month"]
    birth_date = f"{year}-{month:02d}-{day:02d}"
    await state.update_data(birth_date=birth_date)

    await state.set_state(Onboarding.height)
    await safe_edit(
        cb.message,
        f"<b>Шаг 3/5</b> · Дата рождения <b>{birth_date}</b> ✅\n\n"
        "Введи <b>рост в см</b> (например: 178):",
        reply_markup=cancel_fsm_inline(),
    )
    await cb.answer()


@dp.message(Onboarding.height)
async def onb_height(message: Message, state: FSMContext):
    txt = (message.text or "").strip()
    if not txt.isdigit():
        await message.answer("Рост должен быть <b>числом</b> (пример: 178).")
        return
    h = int(txt)
    if not (120 <= h <= 230):
        await message.answer("Рост должен быть в диапазоне 120–230 см.")
        return

    await state.update_data(height_cm=h)
    await state.set_state(Onboarding.level)
    await message.answer("<b>Шаг 4/5</b> · Выбери уровень:", reply_markup=onb_level_inline())


@dp.callback_query(Onboarding.level, F.data.startswith("onb_level:"))
async def onb_level(cb: CallbackQuery, state: FSMContext):
    level = cb.data.split(":")[1]
    await state.update_data(level=level)
    await state.set_state(Onboarding.goal)
    await safe_edit(
        cb.message,
        f"<b>Шаг 5/5</b> · Уровень <b>{H(level)}</b> ✅ — выбери <b>цель</b>:",
        reply_markup=onb_goal_inline(),
    )
    await cb.answer()


@dp.callback_query(Onboarding.goal, F.data.startswith("onb_goal:"))
async def onb_goal(cb: CallbackQuery, state: FSMContext):
    goal = cb.data.split(":")[1]
    data = await state.get_data()
    sex = data["sex"]
    birth_date = data["birth_date"]
    height_cm = data["height_cm"]
    level = data["level"]

    async with aiosqlite.connect(DB_PATH) as db:
        user_id = await db_get_user_id(db, cb.from_user.id)
        await db_upsert_profile(db, user_id, sex, birth_date, height_cm, level, goal)

    await state.clear()
    await safe_edit(
        cb.message,
        "✅ <b>Профиль готов!</b>\n\nТеперь можешь вести замеры, силовые и тренировки.",
        reply_markup=back_to_menu_inline(),
    )
    name = cb.from_user.first_name or "друг"
    await _send_main_menu(cb.message, cb.from_user.id, name)
    await cb.answer("Профиль сохранён")


# -------------------------
# Profile callbacks
# -------------------------
@dp.callback_query(F.data == "profile_view")
async def profile_view(cb: CallbackQuery):
    async with aiosqlite.connect(DB_PATH) as db:
        user_id = await db_get_user_id(db, cb.from_user.id)
        p = await db_get_profile(db, user_id)
    if not p:
        await safe_edit(cb.message, "Профиль не заполнен. Нажми /start.", reply_markup=back_to_menu_inline())
        await cb.answer()
        return
    await safe_edit(cb.message, pretty_profile_card(p), reply_markup=profile_menu_inline())
    await cb.answer()


@dp.callback_query(F.data == "profile_edit")
async def profile_edit(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(Onboarding.sex)
    await safe_edit(
        cb.message,
        "Ок, обновим профиль.\n\n<b>Шаг 1/5</b> · Пол:",
        reply_markup=onb_sex_inline(),
    )
    await cb.answer()


@dp.callback_query(F.data == "profile_goals")
async def profile_goals(cb: CallbackQuery):
    await safe_edit(
        cb.message,
        "🎯 <b>Цели</b>\n\n"
        "Цель влияет на инсайты после замеров и тренировок.\n"
        "Поменять цель можно в <b>Изменить</b> — пройдёшь быстрый опрос заново.",
        reply_markup=profile_menu_inline(),
    )
    await cb.answer()


@dp.callback_query(F.data == "wipe_all")
async def wipe_all(cb: CallbackQuery):
    await safe_edit(
        cb.message,
        "⚠️ Ты точно хочешь удалить <b>все данные</b>?\n"
        "Это удалит профиль, замеры, силовые и тренировки.",
        reply_markup=wipe_confirm_inline(),
    )
    await cb.answer()


@dp.callback_query(F.data == "wipe_all_confirm")
async def wipe_all_confirm(cb: CallbackQuery):
    async with aiosqlite.connect(DB_PATH) as db:
        user_id = await db_get_user_id(db, cb.from_user.id)
        await db_wipe_all(db, user_id)
    await safe_edit(
        cb.message,
        "Готово. Данные удалены.\n\nНажми /start чтобы начать заново.",
        reply_markup=back_to_menu_inline(),
    )
    await cb.answer("Удалено")


# -------------------------
# InBody
# -------------------------
@dp.callback_query(F.data == "inbody_add")
async def inbody_add(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(InBodyFSM.add_mode)
    await safe_edit(
        cb.message,
        "➕ <b>Новый замер</b>\n\nВыбери способ ввода:",
        reply_markup=inbody_add_mode_inline(),
    )
    await cb.answer()


@dp.callback_query(InBodyFSM.add_mode, F.data == "inbody_add_manual")
async def inbody_add_manual(cb: CallbackQuery, state: FSMContext):
    await state.set_state(InBodyFSM.manual_weight)
    await safe_edit(
        cb.message,
        "✍️ <b>Ручной ввод</b>\n\nШаг 1/3 · вес (кг), например <code>72.4</code>:",
        reply_markup=cancel_fsm_inline(),
    )
    await cb.answer()


def _parse_float_user(txt: str):
    try:
        return float(txt.strip().replace(",", "."))
    except Exception:
        return None


@dp.message(InBodyFSM.manual_weight)
async def inbody_manual_weight(message: Message, state: FSMContext):
    v = _parse_float_user(message.text or "")
    if v is None or not (30 <= v <= 250):
        await message.answer("Вес введён неверно. Пример: <code>72.4</code>")
        return
    await state.update_data(weight_kg=round(v, 1))
    await state.set_state(InBodyFSM.manual_pbf)
    await message.answer(
        "Шаг 2/3 · жир (%), например <code>14.8</code>:",
        reply_markup=cancel_fsm_inline(),
    )


@dp.message(InBodyFSM.manual_pbf)
async def inbody_manual_pbf(message: Message, state: FSMContext):
    v = _parse_float_user(message.text or "")
    if v is None or not (1 <= v <= 70):
        await message.answer("Процент жира неверный. Пример: <code>14.8</code>")
        return
    await state.update_data(pbf_percent=round(v, 1))
    await state.set_state(InBodyFSM.manual_smm)
    await message.answer(
        "Шаг 3/3 · мышцы (кг), например <code>34.1</code>:",
        reply_markup=cancel_fsm_inline(),
    )


@dp.message(InBodyFSM.manual_smm)
async def inbody_manual_smm(message: Message, state: FSMContext):
    v = _parse_float_user(message.text or "")
    if v is None or not (10 <= v <= 120):
        await message.answer("Мышцы введены неверно. Пример: 34.1")
        return
    smm = round(v, 1)

    data = await state.get_data()
    weight = data["weight_kg"]
    pbf = data["pbf_percent"]

    record_date = today_ymd()

    async with aiosqlite.connect(DB_PATH) as db:
        user_id = await db_get_user_id(db, message.from_user.id)
        prev = await db_inbody_last(db, user_id)
        await db_add_inbody(db, user_id, record_date, weight, pbf, smm, "manual", 1.0, None)

    await state.clear()

    msg = "✅ <b>Замер сохранён</b>\n"
    msg += _inbody_delta_text(prev, {"weight_kg": weight, "pbf_percent": pbf, "smm_kg": smm})
    msg += "\n" + _inbody_reco(weight, pbf, smm)
    await message.answer(msg, reply_markup=inbody_menu_inline())


@dp.callback_query(InBodyFSM.add_mode, F.data == "inbody_add_photo")
async def inbody_add_photo(cb: CallbackQuery, state: FSMContext):
    await state.set_state(InBodyFSM.photo_wait)
    await safe_edit(
        cb.message,
        "📷 <b>OCR InBody</b>\n\n"
        "Пришли фото отчёта InBody.\n"
        "<i>Фото должно быть ровным, без бликов, текст читаемый.</i>",
        reply_markup=cancel_fsm_inline(),
    )
    await cb.answer()


@dp.message(InBodyFSM.photo_wait, F.photo)
async def inbody_photo_received(message: Message, state: FSMContext):
    photo = message.photo[-1]
    os.makedirs("cache_photos", exist_ok=True)
    path = f"cache_photos/inbody_{message.from_user.id}_{int(dt.datetime.now().timestamp())}.jpg"
    await message.bot.download(photo.file_id, destination=path)

    await message.answer("🔍 Обрабатываю фото... (preprocessing + OCR)")

    ocr = await asyncio.to_thread(run_inbody_ocr, path, PADDLE_USE_GPU)

    if not ocr.get("ok"):
        await message.answer("❌ Не удалось распознать фото.\nПопробуй другое фото или введи вручную.")
        return

    metrics = ocr.get("metrics", {})
    confidence = float(ocr.get("confidence", 0.0))
    raw_text = ocr.get("raw_text", "")
    ocr_date = ocr.get("test_date")

    # дата по умолчанию — то, что распознано на фото; если не нашли — сегодня
    record_date = ocr_date or today_ymd()
    date_from_ocr = bool(ocr_date)

    await state.update_data(
        ocr_metrics=metrics,
        ocr_confidence=confidence,
        ocr_raw=raw_text,
        ocr_date=record_date,
        ocr_date_from_ocr=date_from_ocr,
    )

    date_hint = "<i>с фото</i>" if date_from_ocr else "<i>сегодня (не нашли на фото)</i>"
    card = (
        "📷 <b>Распознано</b>\n"
        f"Дата: <b>{H(record_date)}</b> {date_hint}\n"
        f"Вес: <b>{H(metrics.get('weight_kg','—'))}</b> кг\n"
        f"Жир: <b>{H(metrics.get('pbf_percent','—'))}</b> %\n"
        f"Мышцы: <b>{H(metrics.get('smm_kg','—'))}</b> кг\n\n"
        f"<i>Confidence: <b>{int(confidence*100)}%</b></i>"
    )

    await state.set_state(InBodyFSM.ocr_confirm)

    if confidence < 0.67:
        await message.answer(
            "⚠️ Качество распознавания не идеальное — рекомендую нажать «✏️ Исправить».",
        )

    await message.answer(card, reply_markup=ocr_confirm_inline())


@dp.message(InBodyFSM.photo_wait)
async def inbody_photo_wait_else(message: Message):
    await message.answer(
        "Пожалуйста, отправь <b>фото</b> (как изображение).",
        reply_markup=cancel_fsm_inline(),
    )


@dp.callback_query(InBodyFSM.ocr_confirm, F.data == "ocr_cancel")
async def ocr_cancel(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await safe_edit(cb.message, "Ок, замер отменён.", reply_markup=inbody_menu_inline())
    await cb.answer()


@dp.callback_query(InBodyFSM.ocr_confirm, F.data == "ocr_edit")
async def ocr_edit(cb: CallbackQuery, state: FSMContext):
    await state.set_state(InBodyFSM.ocr_edit_weight)
    await cb.message.answer(
        "✏️ <b>Исправление</b>\n\nШаг 1/3 · вес (кг):",
        reply_markup=cancel_fsm_inline(),
    )
    await cb.answer()


def _parse_user_date(s: str) -> Optional[str]:
    """Принимаем YYYY-MM-DD, YYYY.MM.DD, DD.MM.YYYY, DD-MM-YYYY, DD/MM/YYYY.
    Возвращаем YYYY-MM-DD или None."""
    s = (s or "").strip()
    if not s:
        return None
    fmts = ("%Y-%m-%d", "%Y.%m.%d", "%Y/%m/%d",
            "%d.%m.%Y", "%d-%m-%Y", "%d/%m/%Y")
    for f in fmts:
        try:
            d = dt.datetime.strptime(s, f).date()
            if d.year < 2010 or d.year > 2100:
                return None
            return d.isoformat()
        except Exception:
            continue
    return None


@dp.callback_query(InBodyFSM.ocr_confirm, F.data == "ocr_edit_date")
async def ocr_edit_date_start(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    cur = data.get("ocr_date", today_ymd())
    await state.set_state(InBodyFSM.ocr_edit_date)
    await cb.message.answer(
        f"📅 <b>Изменить дату</b>\n\n"
        f"Текущая: <b>{H(cur)}</b>\n"
        f"Введи новую дату — форматы: <code>2026-03-15</code>, "
        f"<code>2026.03.15</code> или <code>15.03.2026</code>.",
        reply_markup=cancel_fsm_inline(),
    )
    await cb.answer()


@dp.message(InBodyFSM.ocr_edit_date)
async def ocr_edit_date_input(message: Message, state: FSMContext):
    iso = _parse_user_date(message.text or "")
    if iso is None:
        await message.answer(
            "Не получилось разобрать дату. Пример: <code>2026-03-15</code> или <code>15.03.2026</code>.",
            reply_markup=cancel_fsm_inline(),
        )
        return
    await state.update_data(ocr_date=iso, ocr_date_from_ocr=False)
    data = await state.get_data()
    metrics = data.get("ocr_metrics", {})
    confidence = float(data.get("ocr_confidence", 0.0))
    await state.set_state(InBodyFSM.ocr_confirm)
    card = (
        "📷 <b>Обновлено</b>\n"
        f"Дата: <b>{H(iso)}</b> <i>(вручную)</i>\n"
        f"Вес: <b>{H(metrics.get('weight_kg','—'))}</b> кг\n"
        f"Жир: <b>{H(metrics.get('pbf_percent','—'))}</b> %\n"
        f"Мышцы: <b>{H(metrics.get('smm_kg','—'))}</b> кг\n\n"
        f"<i>Confidence: <b>{int(confidence*100)}%</b></i>"
    )
    await message.answer(card, reply_markup=ocr_confirm_inline())


@dp.message(InBodyFSM.ocr_edit_weight)
async def ocr_edit_weight(message: Message, state: FSMContext):
    v = _parse_float_user(message.text or "")
    if v is None or not (30 <= v <= 250):
        await message.answer("Вес неверный. Пример: <code>72.4</code>")
        return
    data = await state.get_data()
    metrics = dict(data.get("ocr_metrics", {}))
    metrics["weight_kg"] = round(v, 1)
    await state.update_data(ocr_metrics=metrics)

    await state.set_state(InBodyFSM.ocr_edit_pbf)
    await message.answer("Шаг 2/3 · жир (%):", reply_markup=cancel_fsm_inline())


@dp.message(InBodyFSM.ocr_edit_pbf)
async def ocr_edit_pbf(message: Message, state: FSMContext):
    v = _parse_float_user(message.text or "")
    if v is None or not (1 <= v <= 70):
        await message.answer("Жир неверный. Пример: <code>14.8</code>")
        return
    data = await state.get_data()
    metrics = dict(data.get("ocr_metrics", {}))
    metrics["pbf_percent"] = round(v, 1)
    await state.update_data(ocr_metrics=metrics)

    await state.set_state(InBodyFSM.ocr_edit_smm)
    await message.answer("Шаг 3/3 · мышцы (кг):", reply_markup=cancel_fsm_inline())


@dp.message(InBodyFSM.ocr_edit_smm)
async def ocr_edit_smm(message: Message, state: FSMContext):
    v = _parse_float_user(message.text or "")
    if v is None or not (10 <= v <= 120):
        await message.answer("Мышцы неверные. Пример: <code>34.1</code>")
        return

    data = await state.get_data()
    metrics = dict(data.get("ocr_metrics", {}))
    metrics["smm_kg"] = round(v, 1)
    await state.update_data(ocr_metrics=metrics)

    await state.set_state(InBodyFSM.ocr_confirm)

    record_date = data.get("ocr_date", today_ymd())
    card = (
        "📷 <b>Обновлено</b>\n"
        f"Дата: <b>{H(record_date)}</b>\n"
        f"Вес: <b>{H(metrics.get('weight_kg','—'))}</b> кг\n"
        f"Жир: <b>{H(metrics.get('pbf_percent','—'))}</b> %\n"
        f"Мышцы: <b>{H(metrics.get('smm_kg','—'))}</b> кг"
    )
    await message.answer(card, reply_markup=ocr_confirm_inline())


@dp.callback_query(InBodyFSM.ocr_confirm, F.data == "ocr_save")
async def ocr_save(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    metrics = data.get("ocr_metrics", {})
    record_date = data.get("ocr_date", today_ymd())
    conf = float(data.get("ocr_confidence", 0.0))
    raw = data.get("ocr_raw", "")

    weight = metrics.get("weight_kg")
    pbf = metrics.get("pbf_percent")
    smm = metrics.get("smm_kg")

    async with aiosqlite.connect(DB_PATH) as db:
        user_id = await db_get_user_id(db, cb.from_user.id)
        prev = await db_inbody_last(db, user_id)
        await db_add_inbody(db, user_id, record_date, weight, pbf, smm, "ocr", conf, raw)

    await state.clear()

    msg = "✅ <b>Замер сохранён</b> (OCR)\n"
    msg += _inbody_delta_text(prev, {"weight_kg": weight, "pbf_percent": pbf, "smm_kg": smm})
    msg += "\n" + _inbody_reco(weight, pbf, smm)

    await cb.message.answer(msg, reply_markup=inbody_menu_inline())
    await cb.answer("Сохранено")


def _delta_line(name, cur, prev):
    if cur is None or prev is None:
        return f"{name}: —"
    d = cur - prev
    sign = "+" if d > 0 else ""
    return f"{name}: {cur} ({sign}{d:.1f})"


def _inbody_delta_text(prev, cur):
    if not prev:
        return "\nЭто твой первый замер — отличное начало 💪"
    lines = ["\n📌 Изменения относительно прошлого замера:"]
    lines.append(_delta_line("Вес", cur.get("weight_kg"), prev.get("weight_kg")))
    lines.append(_delta_line("Жир %", cur.get("pbf_percent"), prev.get("pbf_percent")))
    lines.append(_delta_line("Мышцы", cur.get("smm_kg"), prev.get("smm_kg")))
    return "\n".join(lines)


def _inbody_reco(weight, pbf, smm):
    # короткая и понятная рекомендация
    reco = "\n🧠 Короткая рекомендация:\n"
    if pbf is not None:
        if pbf >= 25:
            reco += "• Жир высоковат — сделай акцент на дефицит калорий + шаги.\n"
        elif pbf <= 10:
            reco += "• Жир низкий — следи за восстановлением и силой.\n"
        else:
            reco += "• Жир в нормальном диапазоне — держи стабильный режим.\n"
    if smm is not None:
        reco += "• Для роста мышц: прогрессия нагрузок + белок 1.6–2.2 г/кг.\n"
    reco += "• Следующий замер желательно через 7–14 дней."
    return reco


@dp.callback_query(F.data == "inbody_history")
async def inbody_history(cb: CallbackQuery):
    async with aiosqlite.connect(DB_PATH) as db:
        user_id = await db_get_user_id(db, cb.from_user.id)
        rows = await db_inbody_history(db, user_id)

    if not rows:
        await safe_edit(
            cb.message,
            "История пустая. Добавь первый замер 🙂",
            reply_markup=inbody_menu_inline(),
        )
        await cb.answer()
        return

    text = "📋 <b>История замеров</b>\n\n"
    for r in rows:
        text += (
            f"📅 <b>{H(r[0])}</b>\n"
            f"• Вес: {H(r[1]) if r[1] is not None else '—'} кг\n"
            f"• Жир: {H(r[2]) if r[2] is not None else '—'} %\n"
            f"• Мышцы: {H(r[3]) if r[3] is not None else '—'} кг\n"
            f"<i>Источник: {H(r[4])} (conf {int((r[5] or 0)*100)}%)</i>\n\n"
        )
    await safe_edit(cb.message, text, reply_markup=inbody_menu_inline())
    await cb.answer()


@dp.callback_query(F.data == "inbody_charts")
async def inbody_charts(cb: CallbackQuery):
    async with aiosqlite.connect(DB_PATH) as db:
        user_id = await db_get_user_id(db, cb.from_user.id)
        rows = await db_inbody_all(db, user_id)

    if len(rows) < 2:
        await cb.message.answer("Для графиков нужно минимум 2 замера.", reply_markup=inbody_menu_inline())
        await cb.answer()
        return

    dates = [r[0][5:] for r in rows]
    weight = [r[1] for r in rows]
    pbf = [r[2] for r in rows]
    smm = [r[3] for r in rows]

    if any(v is not None for v in weight):
        buf = plot_series(dates, [v if v is not None else float("nan") for v in weight], "Вес", "кг")
        await cb.message.answer_photo(photo=buf, caption="📊 График: Вес")

    if any(v is not None for v in pbf):
        buf = plot_series(dates, [v if v is not None else float("nan") for v in pbf], "Жир", "%")
        await cb.message.answer_photo(photo=buf, caption="📊 График: Жир %")

    if any(v is not None for v in smm):
        buf = plot_series(dates, [v if v is not None else float("nan") for v in smm], "Мышцы (SMM)", "кг")
        await cb.message.answer_photo(photo=buf, caption="📊 График: Мышцы")

    await cb.message.answer("Готово ✅", reply_markup=inbody_menu_inline())
    await cb.answer()


@dp.callback_query(F.data == "inbody_compare")
async def inbody_compare(cb: CallbackQuery):
    await safe_edit(
        cb.message,
        "🧾 <b>Сравнение замеров</b>\n\n"
        "Сейчас сравнение идёт автоматически при добавлении нового замера.\n"
        "<i>Скоро — выбор двух дат.</i>",
        reply_markup=inbody_menu_inline(),
    )
    await cb.answer()


# -------------------------
# Strength
# -------------------------
@dp.callback_query(F.data == "strength_add")
async def strength_add(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(StrengthFSM.choose_category)

    cats = list(DEFAULT_EXERCISES.keys()) + ["Своя"]
    kb = []
    row = []
    for c in cats:
        row.append(btn(c, f"st_cat:{c}"))
        if len(row) == 2:
            kb.append(row)
            row = []
    if row:
        kb.append(row)
    kb.append([btn("⬅️ Назад", "open_strength"), btn("❌ Отмена", "go_main_menu", style="danger")])
    await safe_edit(
        cb.message,
        "➕ <b>Новый рекорд</b>\n\nШаг 1/4 · выбери категорию:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
    )
    await cb.answer()


@dp.callback_query(F.data == "open_strength")
async def open_strength_cb(cb: CallbackQuery):
    await safe_edit(
        cb.message,
        "💪 <b>Силовые</b>\n\nДобавь рекорд, посмотри PR и управляй упражнениями.",
        reply_markup=strength_menu_inline(),
    )
    await cb.answer()


@dp.callback_query(StrengthFSM.choose_category, F.data.startswith("st_cat:"))
async def strength_choose_cat(cb: CallbackQuery, state: FSMContext):
    cat = cb.data.split(":", 1)[1]
    await state.update_data(category=cat)
    await state.set_state(StrengthFSM.choose_exercise)

    async with aiosqlite.connect(DB_PATH) as db:
        user_id = await db_get_user_id(db, cb.from_user.id)
        rows = await db_list_exercises(db, user_id, None if cat == "Своя" else cat)

    rows = rows[:16]
    kb = []
    for r in rows:
        ex_id, ex_cat, name, is_custom, owner_id = r
        kb.append([btn(name, f"st_ex:{ex_id}")])
    kb.append([btn("⬅️ Назад", "strength_add"), btn("❌ Отмена", "go_main_menu", style="danger")])
    await safe_edit(
        cb.message,
        f"➕ <b>Новый рекорд</b> · {H(cat)}\n\nШаг 2/4 · выбери упражнение:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
    )
    await cb.answer()


@dp.callback_query(StrengthFSM.choose_exercise, F.data.startswith("st_ex:"))
async def strength_choose_ex(cb: CallbackQuery, state: FSMContext):
    ex_id = int(cb.data.split(":")[1])
    await state.update_data(exercise_id=ex_id)
    await state.set_state(StrengthFSM.enter_weight)
    await safe_edit(
        cb.message,
        "➕ <b>Новый рекорд</b>\n\nШаг 3/4 · вес (кг), например <code>80</code>:",
        reply_markup=cancel_fsm_inline(),
    )
    await cb.answer()


@dp.message(StrengthFSM.enter_weight)
async def strength_enter_weight(message: Message, state: FSMContext):
    v = _parse_float_user(message.text or "")
    if v is None or not (0 <= v <= 500):
        await message.answer("Вес неверный. Пример: <code>80</code>")
        return
    await state.update_data(weight=float(v))
    await state.set_state(StrengthFSM.enter_reps)
    await message.answer(
        "Шаг 4/4 · повторы, например <code>8</code>:",
        reply_markup=cancel_fsm_inline(),
    )


@dp.message(StrengthFSM.enter_reps)
async def strength_enter_reps(message: Message, state: FSMContext):
    txt = (message.text or "").strip()
    if not txt.isdigit():
        await message.answer("Повторы должны быть числом. Пример: <code>8</code>")
        return
    reps = int(txt)
    if not (1 <= reps <= 100):
        await message.answer("Повторы должны быть 1..100")
        return

    data = await state.get_data()
    ex_id = data["exercise_id"]
    weight = data["weight"]
    record_date = today_ymd()

    async with aiosqlite.connect(DB_PATH) as db:
        user_id = await db_get_user_id(db, message.from_user.id)
        await db_add_strength_record(db, user_id, ex_id, weight, reps, record_date)
        ex_row = await fetchone(db, "SELECT name FROM exercises WHERE id=?", (ex_id,))
        ex_name = ex_row[0] if ex_row else "Упражнение"

    e1rm = weight * (1 + reps / 30.0)
    await state.clear()
    await message.answer(
        f"✅ <b>Записано:</b> {H(ex_name)}\n"
        f"{weight} кг × {reps}\n"
        f"Оценка 1RM: <b>{e1rm:.1f} кг</b>",
        reply_markup=strength_menu_inline(),
    )


@dp.callback_query(F.data == "strength_pr")
async def strength_pr(cb: CallbackQuery):
    async with aiosqlite.connect(DB_PATH) as db:
        user_id = await db_get_user_id(db, cb.from_user.id)
        rows = await db_strength_pr(db, user_id)

    if not rows:
        await safe_edit(
            cb.message,
            "🏆 Пока нет записей. Добавь первый результат 🙂",
            reply_markup=strength_menu_inline(),
        )
        await cb.answer()
        return

    text = "🏆 <b>Мои рекорды</b>\n\n"
    for r in rows:
        name, best_weight, best_e1rm, best_vol = r
        text += (
            f"• <b>{H(name)}</b>\n"
            f"  · лучший вес: <b>{best_weight:.1f}</b> кг\n"
            f"  · лучший 1RM: <b>{best_e1rm:.1f}</b> кг\n"
            f"  · лучший объём: <b>{best_vol:.0f}</b>\n\n"
        )
    await safe_edit(cb.message, text, reply_markup=strength_menu_inline())
    await cb.answer()


@dp.callback_query(F.data == "strength_chart")
async def strength_chart(cb: CallbackQuery):
    await safe_edit(
        cb.message,
        "📊 <b>График упражнения</b>\n\n"
        "Графики делаются в разделе «📈 Аналитика → 💪 Силовые».",
        reply_markup=strength_menu_inline(),
    )
    await cb.answer()


# -------------------------
# Exercises management
# -------------------------
@dp.callback_query(F.data == "strength_exercises")
async def strength_exercises(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [btn("📚 Список", "ex_list")],
        [btn("➕ Добавить своё", "ex_add", style="primary")],
        [btn("✏️ Переименовать", "ex_rename")],
        [btn("🗑 Удалить", "ex_delete", style="danger")],
        [btn("⬅️ Назад", "open_strength"), btn("🏠 Меню", "go_main_menu")],
    ])
    await safe_edit(cb.message, "🏋️ <b>Упражнения</b>", reply_markup=kb)
    await cb.answer()


@dp.callback_query(F.data == "ex_list")
async def ex_list(cb: CallbackQuery):
    async with aiosqlite.connect(DB_PATH) as db:
        user_id = await db_get_user_id(db, cb.from_user.id)
        rows = await db_list_exercises(db, user_id)

    text = "📚 <b>Упражнения</b>\n"
    current = None
    for r in rows:
        ex_id, cat, name, is_custom, owner = r
        if cat != current:
            current = cat
            text += f"\n<b>{H(cat)}</b>\n"
        tag = " <i>(моё)</i>" if is_custom else ""
        text += f"• {H(name)}{tag}\n"
    await safe_edit(cb.message, text, reply_markup=strength_menu_inline())
    await cb.answer()


@dp.callback_query(F.data == "ex_add")
async def ex_add(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(ExerciseManageFSM.add_custom_name)
    await state.update_data(custom_category="Своя")
    await safe_edit(
        cb.message,
        "➕ <b>Своё упражнение</b>\n\nВведи название:",
        reply_markup=cancel_fsm_inline(),
    )
    await cb.answer()


@dp.message(ExerciseManageFSM.add_custom_name)
async def ex_add_name(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if len(name) < 3:
        await message.answer("Название слишком короткое. Пример: «Жим Арнольда»")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        user_id = await db_get_user_id(db, message.from_user.id)
        await db_add_custom_exercise(db, user_id, "Своя", name)
    await state.clear()
    await message.answer(f"✅ Добавлено: <b>{H(name)}</b>", reply_markup=strength_menu_inline())


@dp.callback_query(F.data == "ex_rename")
async def ex_rename(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(ExerciseManageFSM.rename_choose)

    async with aiosqlite.connect(DB_PATH) as db:
        user_id = await db_get_user_id(db, cb.from_user.id)
        rows = await fetchall(db, """
            SELECT id,name FROM exercises
            WHERE user_id=? AND is_custom=1 AND is_active=1
            ORDER BY name ASC
        """, (user_id,))

    if not rows:
        await safe_edit(cb.message, "У тебя нет своих упражнений.", reply_markup=strength_menu_inline())
        await cb.answer()
        return

    kb = [[btn(r[1], f"ex_ren_pick:{r[0]}")] for r in rows[:20]]
    kb.append([btn("⬅️ Назад", "strength_exercises"), btn("❌ Отмена", "go_main_menu", style="danger")])
    await safe_edit(cb.message, "✏️ Выбери упражнение:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await cb.answer()


@dp.callback_query(ExerciseManageFSM.rename_choose, F.data.startswith("ex_ren_pick:"))
async def ex_ren_pick(cb: CallbackQuery, state: FSMContext):
    ex_id = int(cb.data.split(":")[1])
    await state.update_data(rename_ex_id=ex_id)
    await state.set_state(ExerciseManageFSM.rename_new_name)
    await safe_edit(cb.message, "Введи новое название:", reply_markup=cancel_fsm_inline())
    await cb.answer()


@dp.message(ExerciseManageFSM.rename_new_name)
async def ex_ren_new_name(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if len(name) < 3:
        await message.answer("Название слишком короткое.")
        return
    data = await state.get_data()
    ex_id = data["rename_ex_id"]
    async with aiosqlite.connect(DB_PATH) as db:
        user_id = await db_get_user_id(db, message.from_user.id)
        await db_rename_custom_exercise(db, user_id, ex_id, name)
    await state.clear()
    await message.answer(f"✅ Переименовано в <b>{H(name)}</b>", reply_markup=strength_menu_inline())


@dp.callback_query(F.data == "ex_delete")
async def ex_delete(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(ExerciseManageFSM.delete_choose)

    async with aiosqlite.connect(DB_PATH) as db:
        user_id = await db_get_user_id(db, cb.from_user.id)
        rows = await fetchall(db, """
            SELECT id,name FROM exercises
            WHERE user_id=? AND is_custom=1 AND is_active=1
            ORDER BY name ASC
        """, (user_id,))

    if not rows:
        await safe_edit(cb.message, "У тебя нет своих упражнений.", reply_markup=strength_menu_inline())
        await cb.answer()
        return

    kb = [[btn("🗑 " + r[1], f"ex_del_pick:{r[0]}", style="danger")] for r in rows[:20]]
    kb.append([btn("⬅️ Назад", "strength_exercises"), btn("❌ Отмена", "go_main_menu", style="danger")])
    await safe_edit(cb.message, "🗑 Выбери упражнение для удаления:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await cb.answer()


@dp.callback_query(ExerciseManageFSM.delete_choose, F.data.startswith("ex_del_pick:"))
async def ex_del_pick(cb: CallbackQuery, state: FSMContext):
    ex_id = int(cb.data.split(":")[1])
    async with aiosqlite.connect(DB_PATH) as db:
        user_id = await db_get_user_id(db, cb.from_user.id)
        await db_delete_custom_exercise(db, user_id, ex_id)
    await state.clear()
    await safe_edit(cb.message, "✅ Удалено.", reply_markup=strength_menu_inline())
    await cb.answer("Удалено")


# -------------------------
# Workout diary (live mode)
# -------------------------
WORKOUT_GROUPS = ["Грудь", "Спина", "Ноги", "Плечи", "Руки", "Полное тело", "Своя"]

@dataclass
class WorkoutSession:
    workout_id: int
    group: str
    current_exercise_id: int = None
    current_exercise_name: str = ""
    set_no: int = 0

# in-memory sessions
SESSIONS = {}


@dp.callback_query(F.data == "workout_start")
async def workout_start(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(WorkoutFSM.choose_group)

    kb = []
    row = []
    for g in WORKOUT_GROUPS:
        row.append(btn(g, f"wo_g:{g}"))
        if len(row) == 2:
            kb.append(row)
            row = []
    if row:
        kb.append(row)
    kb.append([btn("⬅️ Назад", "open_workout"), btn("❌ Отмена", "go_main_menu", style="danger")])
    await safe_edit(
        cb.message,
        "🏋️ <b>Новая тренировка</b>\n\nКакую группу тренируем?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
    )
    await cb.answer()


@dp.callback_query(F.data == "open_workout")
async def open_workout_cb(cb: CallbackQuery):
    await safe_edit(
        cb.message,
        "🏋️ <b>Тренировка</b>\n\nДневник, история и итоги.",
        reply_markup=workout_menu_inline(),
    )
    await cb.answer()


@dp.callback_query(WorkoutFSM.choose_group, F.data.startswith("wo_g:"))
async def wo_choose_group(cb: CallbackQuery, state: FSMContext):
    group = cb.data.split(":", 1)[1]
    await state.update_data(workout_group=group)

    async with aiosqlite.connect(DB_PATH) as db:
        user_id = await db_get_user_id(db, cb.from_user.id)
        workout_id = await db_workout_create(db, user_id, today_ymd(), group)
        rows = await db_list_exercises(db, user_id, None if group == "Своя" else group)

    SESSIONS[cb.from_user.id] = WorkoutSession(workout_id=workout_id, group=group)
    await state.set_state(WorkoutFSM.choose_exercise)

    kb = []
    for r in rows[:16]:
        ex_id, cat, name, is_custom, owner = r
        kb.append([btn(name, f"wo_ex:{ex_id}")])
    kb.append([btn("✅ Завершить тренировку", "wo_finish", style="success")])
    await safe_edit(
        cb.message,
        f"▶️ Тренировка <b>{H(group)}</b> стартовала!\n\nВыбери упражнение:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
    )
    await cb.answer()


@dp.callback_query(WorkoutFSM.choose_exercise, F.data.startswith("wo_ex:"))
async def wo_choose_ex(cb: CallbackQuery, state: FSMContext):
    ex_id = int(cb.data.split(":")[1])
    sess = SESSIONS.get(cb.from_user.id)
    if not sess:
        await cb.message.answer("Сессия тренировки не найдена. Начни заново.")
        await cb.answer()
        return

    async with aiosqlite.connect(DB_PATH) as db:
        row = await fetchone(db, "SELECT name FROM exercises WHERE id=?", (ex_id,))
        name = row[0] if row else "Упражнение"

    sess.current_exercise_id = ex_id
    sess.current_exercise_name = name
    sess.set_no = 0

    await state.set_state(WorkoutFSM.set_weight)

    await cb.message.answer(
        f"🏋️ <b>{H(name)}</b>\nПодход <b>1</b> · введи вес (кг):",
        reply_markup=cancel_fsm_inline(),
    )
    await cb.answer()


@dp.message(WorkoutFSM.set_weight)
async def wo_set_weight(message: Message, state: FSMContext):
    sess = SESSIONS.get(message.from_user.id)
    if not sess or not sess.current_exercise_id:
        await message.answer("Выбери упражнение заново из меню тренировки.")
        return

    v = _parse_float_user(message.text or "")
    if v is None or not (0 <= v <= 500):
        await message.answer("Вес неверный. Пример: <code>60</code>")
        return

    await state.update_data(set_weight=float(v))
    await state.set_state(WorkoutFSM.set_reps)
    await message.answer("Теперь введи <b>повторы</b>:", reply_markup=cancel_fsm_inline())


@dp.message(WorkoutFSM.set_reps)
async def wo_set_reps(message: Message, state: FSMContext):
    sess = SESSIONS.get(message.from_user.id)
    if not sess or not sess.current_exercise_id:
        await message.answer("Выбери упражнение заново.")
        return

    txt = (message.text or "").strip()
    if not txt.isdigit():
        await message.answer("Повторы должны быть числом. Пример: <code>10</code>")
        return
    reps = int(txt)
    if not (1 <= reps <= 200):
        await message.answer("Повторы 1..200")
        return

    data = await state.get_data()
    weight = float(data["set_weight"])

    sess.set_no += 1
    set_no = sess.set_no

    async with aiosqlite.connect(DB_PATH) as db:
        await db_workout_add_item(db, sess.workout_id, sess.current_exercise_id, set_no, weight, reps)

    await state.set_state(WorkoutFSM.in_workout)
    await message.answer(
        f"Записано: <b>{weight}</b> кг × <b>{reps}</b> (подход {set_no}) ✅",
        reply_markup=workout_after_set_inline(),
    )


@dp.callback_query(WorkoutFSM.in_workout, F.data == "wo_more_set")
async def wo_more_set(cb: CallbackQuery, state: FSMContext):
    sess = SESSIONS.get(cb.from_user.id)
    if not sess or not sess.current_exercise_id:
        await cb.message.answer("Сессия не найдена.")
        await cb.answer()
        return
    next_set = sess.set_no + 1
    await state.set_state(WorkoutFSM.set_weight)
    await cb.message.answer(
        f"Подход <b>{next_set}</b> · введи вес (кг):",
        reply_markup=cancel_fsm_inline(),
    )
    await cb.answer()


@dp.callback_query(WorkoutFSM.in_workout, F.data == "wo_end_ex")
async def wo_end_ex(cb: CallbackQuery, state: FSMContext):
    await state.set_state(WorkoutFSM.choose_exercise)
    await cb.message.answer("Упражнение завершено ✅\nВыбери следующее в меню тренировки.")
    await cb.answer()


@dp.callback_query(F.data == "wo_finish")
async def wo_finish(cb: CallbackQuery, state: FSMContext):
    sess = SESSIONS.get(cb.from_user.id)
    if not sess:
        await cb.message.answer("Нет активной тренировки.")
        await cb.answer()
        return

    async with aiosqlite.connect(DB_PATH) as db:
        sets, reps, tonnage = await db_workout_finish(db, sess.workout_id)

    SESSIONS.pop(cb.from_user.id, None)
    await state.clear()

    await cb.message.answer(
        "✅ <b>Тренировка сохранена</b>\n\n"
        f"Подходы: <b>{sets}</b>\n"
        f"Повторы: <b>{reps}</b>\n"
        f"Тоннаж: <b>{tonnage:.0f}</b> кг",
        reply_markup=workout_menu_inline(),
    )
    await cb.answer("Сохранено")


@dp.callback_query(F.data == "workout_history")
async def workout_history(cb: CallbackQuery):
    async with aiosqlite.connect(DB_PATH) as db:
        user_id = await db_get_user_id(db, cb.from_user.id)
        rows = await db_workout_history(db, user_id)

    if not rows:
        await safe_edit(
            cb.message,
            "🧾 История тренировок пустая.",
            reply_markup=workout_menu_inline(),
        )
        await cb.answer()
        return

    text = "🧾 <b>История тренировок</b>\n\n"
    for r in rows:
        text += (
            f"📅 <b>{H(r[0])}</b> · {H(r[1])}\n"
            f"· подходы: {r[2]} | повторы: {r[3]} | тоннаж: {r[4]:.0f} кг\n\n"
        )
    await safe_edit(cb.message, text, reply_markup=workout_menu_inline())
    await cb.answer()


@dp.callback_query(F.data == "workout_ai_tip")
async def workout_ai_tip(cb: CallbackQuery):
    await safe_edit(
        cb.message,
        "🧠 <b>Совет по тренировке</b>\n\n"
        "• Разминка 5–10 минут\n"
        "• 1–2 разминочных подхода перед рабочими\n"
        "• 8–12 повторов для гипертрофии, 3–6 для силы\n"
        "• Прогрессия: +1 повтор или <b>+2.5 кг</b> раз в 1–2 недели\n"
        "• Сон 7–9 часов — это часть прогресса",
        reply_markup=workout_menu_inline(),
    )
    await cb.answer()


# -------------------------
# Analytics
# -------------------------
@dp.callback_query(F.data == "an_body")
async def an_body(cb: CallbackQuery):
    await safe_edit(
        cb.message,
        "📄 <b>Анализ замеров</b>\n\n"
        "Графики доступны в «📄 Замеры → 📊 Графики».",
        reply_markup=analytics_menu_inline(),
    )
    await cb.answer()


@dp.callback_query(F.data == "an_strength")
async def an_strength(cb: CallbackQuery):
    async with aiosqlite.connect(DB_PATH) as db:
        user_id = await db_get_user_id(db, cb.from_user.id)
        row = await fetchone(db, """
            SELECT exercise_id, COUNT(*) cnt
            FROM strength_records
            WHERE user_id=?
            GROUP BY exercise_id
            ORDER BY cnt DESC
            LIMIT 1
        """, (user_id,))
        if not row:
            await safe_edit(
                cb.message,
                "💪 Нет данных по силовым. Запиши хотя бы 2 результата.",
                reply_markup=analytics_menu_inline(),
            )
            await cb.answer()
            return
        ex_id = row[0]
        ex_name_row = await fetchone(db, "SELECT name FROM exercises WHERE id=?", (ex_id,))
        ex_name = ex_name_row[0] if ex_name_row else "Упражнение"

        rows = await fetchall(db, """
            SELECT record_date, weight, reps
            FROM strength_records
            WHERE user_id=? AND exercise_id=?
            ORDER BY record_date ASC
        """, (user_id, ex_id))

    dates = [r[0][5:] for r in rows]
    e1rm = [r[1] * (1 + r[2]/30.0) for r in rows]

    buf = plot_series(dates, e1rm, f"Силовые: {ex_name} (1RM)", "кг")
    await cb.message.answer_photo(photo=buf, caption=f"💪 Прогресс (1RM): <b>{H(ex_name)}</b>")
    await cb.message.answer(
        "<i>Больше графиков появится, когда добавишь больше упражнений.</i>",
        reply_markup=analytics_menu_inline(),
    )
    await cb.answer()


@dp.callback_query(F.data == "an_total")
async def an_total(cb: CallbackQuery):
    await safe_edit(
        cb.message,
        "🧩 <b>Общий прогресс</b>\n\n"
        "• Тренировки ≥ 3/нед + сон\n"
        "• Белок и прогрессия нагрузок\n"
        "• Замеры каждые 7–14 дней\n\n"
        "<i>Скоро добавим балльную оценку прогресса.</i>",
        reply_markup=analytics_menu_inline(),
    )
    await cb.answer()


@dp.callback_query(F.data == "an_forecast")
async def an_forecast(cb: CallbackQuery):
    await safe_edit(
        cb.message,
        "🔮 <b>Прогноз на 30 дней</b>\n\n"
        "• При стабильной программе прирост силы виден через <b>2–4 недели</b>.\n"
        "• Замеры тела меняются медленнее — оценивай тренд минимум по 3 замерам.",
        reply_markup=analytics_menu_inline(),
    )
    await cb.answer()


# -------------------------
# Settings
# -------------------------
@dp.callback_query(F.data == "set_units")
async def set_units(cb: CallbackQuery):
    await safe_edit(
        cb.message,
        "🔁 <b>Единицы измерения</b>\n\n"
        "Базово — кг. Переключить kg/lb можно в mini-app (Профиль).",
        reply_markup=settings_menu_inline(),
    )
    await cb.answer()


@dp.callback_query(F.data == "set_reminders")
async def set_reminders(cb: CallbackQuery):
    await safe_edit(
        cb.message,
        "🔔 <b>Напоминания</b>\n\nСкоро будет расписание и нотификации.",
        reply_markup=settings_menu_inline(),
    )
    await cb.answer()


@dp.callback_query(F.data == "clear_cache")
async def clear_cache(cb: CallbackQuery):
    try:
        if os.path.isdir("cache_photos"):
            for fn in os.listdir("cache_photos"):
                if fn.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                    os.remove(os.path.join("cache_photos", fn))
        await safe_edit(cb.message, "🧹 Кэш фото очищен ✅", reply_markup=settings_menu_inline())
    except Exception:
        await safe_edit(cb.message, "Не удалось очистить кэш.", reply_markup=settings_menu_inline())
    await cb.answer("Очищено")


# -------------------------
# Run
# -------------------------
async def _set_bot_commands():
    commands = [
        BotCommand(command="start", description="Главное меню"),
        BotCommand(command="menu", description="Главное меню"),
        BotCommand(command="dashboard", description="Главный экран «Состояние тела»"),
        BotCommand(command="cancel", description="Отменить текущее действие"),
    ]
    await bot.set_my_commands(commands)


async def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN пуст. Заполни .env")
    await db_init()
    async with aiosqlite.connect(DB_PATH) as db:
        await db_seed_default_exercises(db)

    await _set_bot_commands()
    print("Gym Bot started.")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
