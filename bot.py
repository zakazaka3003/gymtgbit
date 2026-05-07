import os
import io
import math
import asyncio
import datetime as dt
from dataclasses import dataclass

import aiosqlite
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile, WebAppInfo
)
from aiogram.filters import CommandStart, Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext

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
def main_menu_kb():
    rows = [
        [KeyboardButton(text="📊 Главный экран"), KeyboardButton(text="👤 Профиль")],
        [KeyboardButton(text="📄 Замеры (InBody)"), KeyboardButton(text="💪 Силовые")],
        [KeyboardButton(text="🏋️ Тренировка (дневник)"), KeyboardButton(text="📈 Аналитика")],
        [KeyboardButton(text="⚙️ Настройки")],
    ]
    if WEBAPP_URL:
        rows.insert(0, [KeyboardButton(
            text="🚀 Открыть приложение",
            web_app=WebAppInfo(url=WEBAPP_URL),
        )])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def webapp_inline_kb():
    """Инлайн-клавиатура с кнопкой запуска mini app под дашбордом."""
    if not WEBAPP_URL:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🚀 Открыть приложение", web_app=WebAppInfo(url=WEBAPP_URL)),
    ]])


def dashboard_inline_kb():
    rows = []
    if WEBAPP_URL:
        rows.append([InlineKeyboardButton(
            text="🚀 Открыть приложение",
            web_app=WebAppInfo(url=WEBAPP_URL),
        )])
    rows.append([InlineKeyboardButton(text="🔄 Обновить", callback_data="dashboard_refresh")])
    rows.append([
        InlineKeyboardButton(text="➕ Замер", callback_data="inbody_add"),
        InlineKeyboardButton(text="🏋️ Тренировка", callback_data="workout_start"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_to_menu_inline():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ В меню", callback_data="go_main_menu")]
    ])


def profile_menu_inline():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👁️ Посмотреть профиль", callback_data="profile_view")],
        [InlineKeyboardButton(text="✏️ Изменить профиль", callback_data="profile_edit")],
        [InlineKeyboardButton(text="🎯 Цели", callback_data="profile_goals")],
        [InlineKeyboardButton(text="🗑 Удалить все данные", callback_data="wipe_all")],
        [InlineKeyboardButton(text="⬅️ В меню", callback_data="go_main_menu")],
    ])


def inbody_menu_inline():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить замер", callback_data="inbody_add")],
        [InlineKeyboardButton(text="📋 История замеров", callback_data="inbody_history")],
        [InlineKeyboardButton(text="📊 Графики замеров", callback_data="inbody_charts")],
        [InlineKeyboardButton(text="🧾 Сравнить два замера", callback_data="inbody_compare")],
        [InlineKeyboardButton(text="⬅️ В меню", callback_data="go_main_menu")],
    ])


def inbody_add_mode_inline():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✍️ Ввести вручную", callback_data="inbody_add_manual")],
        [InlineKeyboardButton(text="📷 Считать с фото InBody", callback_data="inbody_add_photo")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="open_inbody")],
    ])


def strength_menu_inline():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Записать результат", callback_data="strength_add")],
        [InlineKeyboardButton(text="📊 График упражнения", callback_data="strength_chart")],
        [InlineKeyboardButton(text="🏆 Мои рекорды (PR)", callback_data="strength_pr")],
        [InlineKeyboardButton(text="🏋️ Упражнения", callback_data="strength_exercises")],
        [InlineKeyboardButton(text="⬅️ В меню", callback_data="go_main_menu")],
    ])


def workout_menu_inline():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="▶️ Начать тренировку", callback_data="workout_start")],
        [InlineKeyboardButton(text="🧾 История тренировок", callback_data="workout_history")],
        [InlineKeyboardButton(text="🧠 Совет по тренировке (AI)", callback_data="workout_ai_tip")],
        [InlineKeyboardButton(text="⬅️ В меню", callback_data="go_main_menu")],
    ])


def analytics_menu_inline():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📄 Анализ замеров тела", callback_data="an_body")],
        [InlineKeyboardButton(text="💪 Анализ силовых", callback_data="an_strength")],
        [InlineKeyboardButton(text="🧩 Общий прогресс", callback_data="an_total")],
        [InlineKeyboardButton(text="🔮 Прогноз на 30 дней", callback_data="an_forecast")],
        [InlineKeyboardButton(text="⬅️ В меню", callback_data="go_main_menu")],
    ])


def settings_menu_inline():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔁 Единицы измерения (кг/фунты)", callback_data="set_units")],
        [InlineKeyboardButton(text="🔔 Напоминания", callback_data="set_reminders")],
        [InlineKeyboardButton(text="🧹 Очистить кэш фото", callback_data="clear_cache")],
        [InlineKeyboardButton(text="⬅️ В меню", callback_data="go_main_menu")],
    ])


def inline_year_picker(page_start: int):
    # 4 years per page
    years = list(range(page_start, page_start + 4))
    row = [
        InlineKeyboardButton(text="◀️ назад", callback_data=f"birth_y_prev:{page_start}"),
        InlineKeyboardButton(text=str(years[0]), callback_data=f"birth_y:{years[0]}"),
        InlineKeyboardButton(text=str(years[1]), callback_data=f"birth_y:{years[1]}"),
        InlineKeyboardButton(text=str(years[2]), callback_data=f"birth_y:{years[2]}"),
        InlineKeyboardButton(text="▶️ вперёд", callback_data=f"birth_y_next:{page_start}"),
    ]
    row2 = [
        InlineKeyboardButton(text=str(years[3]), callback_data=f"birth_y:{years[3]}"),
        InlineKeyboardButton(text="⬅️ Отмена", callback_data="onb_cancel"),
    ]
    return InlineKeyboardMarkup(inline_keyboard=[row, row2])


def inline_month_picker():
    kb = []
    row = []
    for m in range(1, 13):
        row.append(InlineKeyboardButton(text=str(m), callback_data=f"birth_m:{m}"))
        if len(row) == 6:
            kb.append(row)
            row = []
    if row:
        kb.append(row)
    kb.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="birth_back_year")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


def inline_day_picker(year: int, month: int):
    # correct days in month
    if month == 2:
        leap = (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0))
        days = 29 if leap else 28
    elif month in (4, 6, 9, 11):
        days = 30
    else:
        days = 31

    kb = []
    row = []
    for d in range(1, days + 1):
        row.append(InlineKeyboardButton(text=str(d), callback_data=f"birth_d:{d}"))
        if len(row) == 7:
            kb.append(row)
            row = []
    if row:
        kb.append(row)

    kb.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="birth_back_month")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


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
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# -------------------------
# Start / Main menu
# -------------------------
async def _send_dashboard(target, tg_id: int):
    """Отрисовать дашборд. ``target`` — это Message (для answer)."""
    async with aiosqlite.connect(DB_PATH) as db:
        user_id = await db_get_user_id(db, tg_id)
        profile = await db_get_profile(db, user_id)
        data = await fetch_dashboard_data(db, user_id)
    text = format_dashboard(data, profile)
    await target.answer(text, parse_mode="Markdown", reply_markup=dashboard_inline_kb())


@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    async with aiosqlite.connect(DB_PATH) as db:
        user_id = await db_get_user_id(db, message.from_user.id)
        await db_seed_default_exercises(db)
        exists = await db_profile_exists(db, user_id)

    if not exists:
        await message.answer(
            "Привет! 👋\n\n"
            "Я помогу вести *прогресс в зале* — замеры, силовые, тренировки и графики.\n\n"
            "Начнём с профиля: выбери пол.",
            parse_mode="Markdown",
            reply_markup=main_menu_kb()
        )
        await state.set_state(Onboarding.sex)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="М", callback_data="onb_sex:М"),
             InlineKeyboardButton(text="Ж", callback_data="onb_sex:Ж")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="onb_cancel")]
        ])
        await message.answer("Пол:", reply_markup=kb)
    else:
        await message.answer("🏠 Главное меню", reply_markup=main_menu_kb())
        await _send_dashboard(message, message.from_user.id)


@dp.message(Command("dashboard"))
async def cmd_dashboard(message: Message, state: FSMContext):
    await state.clear()
    await _send_dashboard(message, message.from_user.id)


@dp.message(F.text == "📊 Главный экран")
async def open_dashboard(message: Message, state: FSMContext):
    await state.clear()
    await _send_dashboard(message, message.from_user.id)


@dp.callback_query(F.data == "dashboard_refresh")
async def dashboard_refresh(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    async with aiosqlite.connect(DB_PATH) as db:
        user_id = await db_get_user_id(db, cb.from_user.id)
        profile = await db_get_profile(db, user_id)
        data = await fetch_dashboard_data(db, user_id)
    text = format_dashboard(data, profile)
    try:
        await cb.message.edit_text(text, parse_mode="Markdown", reply_markup=dashboard_inline_kb())
    except Exception:
        # старое сообщение нельзя отредактировать — пришлём новое
        await cb.message.answer(text, parse_mode="Markdown", reply_markup=dashboard_inline_kb())
    await cb.answer("Обновлено")


@dp.callback_query(F.data == "go_main_menu")
async def go_main_menu(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await _send_dashboard(cb.message, cb.from_user.id)
    await cb.answer()


# -------------------------
# Sections openers
# -------------------------
@dp.message(F.text == "👤 Профиль")
async def open_profile(message: Message):
    await message.answer("👤 Профиль", reply_markup=profile_menu_inline())


@dp.message(F.text == "📄 Замеры (InBody)")
async def open_inbody(message: Message):
    await message.answer("📄 Замеры (InBody)", reply_markup=inbody_menu_inline())


@dp.message(F.text == "💪 Силовые")
async def open_strength(message: Message):
    await message.answer("💪 Силовые", reply_markup=strength_menu_inline())


@dp.message(F.text == "🏋️ Тренировка (дневник)")
async def open_workout(message: Message):
    await message.answer("🏋️ Тренировка (дневник)", reply_markup=workout_menu_inline())


@dp.message(F.text == "📈 Аналитика")
async def open_analytics(message: Message):
    await message.answer("📈 Аналитика", reply_markup=analytics_menu_inline())


@dp.message(F.text == "⚙️ Настройки")
async def open_settings(message: Message):
    await message.answer("⚙️ Настройки", reply_markup=settings_menu_inline())


# mirror callbacks
@dp.callback_query(F.data == "open_inbody")
async def cb_open_inbody(cb: CallbackQuery):
    await cb.message.edit_text("📄 Замеры (InBody)", reply_markup=inbody_menu_inline())
    await cb.answer()


# -------------------------
# Onboarding: sex -> birth date picker -> height -> level -> goal
# -------------------------
@dp.callback_query(Onboarding.sex, F.data.startswith("onb_sex:"))
async def onb_sex(cb: CallbackQuery, state: FSMContext):
    sex = cb.data.split(":")[1]
    await state.update_data(sex=sex)
    await state.set_state(Onboarding.birth_year)

    # range 1980..2012 paging
    await state.update_data(year_page=2000)
    await cb.message.edit_text(
        "Дата рождения: выбери *год* (страницы листаются):",
        parse_mode="Markdown",
        reply_markup=inline_year_picker(2000)
    )
    await cb.answer()


@dp.callback_query(F.data == "onb_cancel")
async def onb_cancel(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.answer("Ок. Если захочешь — вернись в профиль и заполни данные 🙂", reply_markup=main_menu_kb())
    await cb.answer()


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
    await cb.message.edit_text(
        f"Дата рождения: год *{year}* ✅\n\nТеперь выбери *месяц*:",
        parse_mode="Markdown",
        reply_markup=inline_month_picker()
    )
    await cb.answer()


@dp.callback_query(Onboarding.birth_month, F.data == "birth_back_year")
async def birth_back_year(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Onboarding.birth_year)
    await cb.message.edit_text(
        "Дата рождения: выбери *год*:",
        parse_mode="Markdown",
        reply_markup=inline_year_picker(2000)
    )
    await cb.answer()


@dp.callback_query(Onboarding.birth_month, F.data.startswith("birth_m:"))
async def birth_pick_month(cb: CallbackQuery, state: FSMContext):
    month = int(cb.data.split(":")[1])
    data = await state.get_data()
    year = data["birth_year"]
    await state.update_data(birth_month=month)
    await state.set_state(Onboarding.birth_day)
    await cb.message.edit_text(
        f"Дата рождения: *{year}-{month:02d}* ✅\n\nТеперь выбери *день*:",
        parse_mode="Markdown",
        reply_markup=inline_day_picker(year, month)
    )
    await cb.answer()


@dp.callback_query(Onboarding.birth_day, F.data == "birth_back_month")
async def birth_back_month(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    year = data["birth_year"]
    await state.set_state(Onboarding.birth_month)
    await cb.message.edit_text(
        f"Дата рождения: год *{year}* ✅\n\nВыбери *месяц*:",
        parse_mode="Markdown",
        reply_markup=inline_month_picker()
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
    await cb.message.edit_text(
        f"Дата рождения сохранена: *{birth_date}* ✅\n\n"
        "Теперь введи *рост в см* (например 178):",
        parse_mode="Markdown",
        reply_markup=back_to_menu_inline()
    )
    await cb.answer()


@dp.message(Onboarding.height)
async def onb_height(message: Message, state: FSMContext):
    txt = (message.text or "").strip()
    if not txt.isdigit():
        await message.answer("Пожалуйста, введи рост *числом* (пример: 178).", parse_mode="Markdown")
        return
    h = int(txt)
    if not (120 <= h <= 230):
        await message.answer("Рост должен быть в диапазоне 120–230 см.")
        return

    await state.update_data(height_cm=h)
    await state.set_state(Onboarding.level)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Новичок", callback_data="onb_level:Новичок")],
        [InlineKeyboardButton(text="Средний", callback_data="onb_level:Средний")],
        [InlineKeyboardButton(text="Продвинутый", callback_data="onb_level:Продвинутый")],
        [InlineKeyboardButton(text="⬅️ В меню", callback_data="go_main_menu")],
    ])
    await message.answer("Выбери уровень:", reply_markup=kb)


@dp.callback_query(Onboarding.level, F.data.startswith("onb_level:"))
async def onb_level(cb: CallbackQuery, state: FSMContext):
    level = cb.data.split(":")[1]
    await state.update_data(level=level)
    await state.set_state(Onboarding.goal)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Набор мышц", callback_data="onb_goal:Набор мышц")],
        [InlineKeyboardButton(text="Похудение", callback_data="onb_goal:Похудение")],
        [InlineKeyboardButton(text="Поддержание", callback_data="onb_goal:Поддержание")],
        [InlineKeyboardButton(text="Сила", callback_data="onb_goal:Сила")],
        [InlineKeyboardButton(text="Выносливость", callback_data="onb_goal:Выносливость")],
    ])
    await cb.message.edit_text("Выбери цель:", reply_markup=kb)
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
    await cb.message.answer(
        "✅ Профиль создан!\n\nТеперь можешь вести замеры, силовые и тренировки.",
        reply_markup=main_menu_kb()
    )
    await cb.answer()


# -------------------------
# Profile callbacks
# -------------------------
@dp.callback_query(F.data == "profile_view")
async def profile_view(cb: CallbackQuery):
    async with aiosqlite.connect(DB_PATH) as db:
        user_id = await db_get_user_id(db, cb.from_user.id)
        p = await db_get_profile(db, user_id)
    if not p:
        await cb.message.answer("Профиль не заполнен. Нажми /start.")
        await cb.answer()
        return
    await cb.message.answer(pretty_profile_card(p), parse_mode="Markdown", reply_markup=profile_menu_inline())
    await cb.answer()


@dp.callback_query(F.data == "profile_edit")
async def profile_edit(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(Onboarding.sex)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="М", callback_data="onb_sex:М"),
         InlineKeyboardButton(text="Ж", callback_data="onb_sex:Ж")],
        [InlineKeyboardButton(text="⬅️ В меню", callback_data="go_main_menu")]
    ])
    await cb.message.answer("Ок, обновим профиль.\n\nВыбери пол:", reply_markup=kb)
    await cb.answer()


@dp.callback_query(F.data == "profile_goals")
async def profile_goals(cb: CallbackQuery):
    await cb.message.answer(
        "🎯 Цели помогают мне давать подсказки после замеров и тренировок.\n\n"
        "Если хочешь изменить цель — нажми «✏️ Изменить профиль».",
        reply_markup=profile_menu_inline()
    )
    await cb.answer()


@dp.callback_query(F.data == "wipe_all")
async def wipe_all(cb: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❗️ Да, удалить всё", callback_data="wipe_all_confirm")],
        [InlineKeyboardButton(text="Отмена", callback_data="profile_view")],
    ])
    await cb.message.answer(
        "⚠️ Ты точно хочешь удалить *все данные*?\n"
        "Это удалит профиль, замеры, силовые и тренировки.",
        parse_mode="Markdown",
        reply_markup=kb
    )
    await cb.answer()


@dp.callback_query(F.data == "wipe_all_confirm")
async def wipe_all_confirm(cb: CallbackQuery):
    async with aiosqlite.connect(DB_PATH) as db:
        user_id = await db_get_user_id(db, cb.from_user.id)
        await db_wipe_all(db, user_id)
    await cb.message.answer("Готово. Данные удалены.\n\nНажми /start чтобы начать заново.", reply_markup=main_menu_kb())
    await cb.answer()


# -------------------------
# InBody
# -------------------------
@dp.callback_query(F.data == "inbody_add")
async def inbody_add(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(InBodyFSM.add_mode)
    await cb.message.edit_text("➕ Добавить замер\n\nВыбери способ:", reply_markup=inbody_add_mode_inline())
    await cb.answer()


@dp.callback_query(InBodyFSM.add_mode, F.data == "inbody_add_manual")
async def inbody_add_manual(cb: CallbackQuery, state: FSMContext):
    await state.set_state(InBodyFSM.manual_weight)
    await cb.message.edit_text(
        "✍️ Ручной ввод замера\n\n"
        "Шаг 1/3: введи *вес (кг)* например: 72.4",
        parse_mode="Markdown",
        reply_markup=back_to_menu_inline()
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
        await message.answer("Вес введён неверно. Пример: 72.4")
        return
    await state.update_data(weight_kg=round(v, 1))
    await state.set_state(InBodyFSM.manual_pbf)
    await message.answer("Шаг 2/3: введи *жир (%)* например: 14.8", parse_mode="Markdown")


@dp.message(InBodyFSM.manual_pbf)
async def inbody_manual_pbf(message: Message, state: FSMContext):
    v = _parse_float_user(message.text or "")
    if v is None or not (1 <= v <= 70):
        await message.answer("Процент жира введён неверно. Пример: 14.8")
        return
    await state.update_data(pbf_percent=round(v, 1))
    await state.set_state(InBodyFSM.manual_smm)
    await message.answer("Шаг 3/3: введи *мышцы (кг)* например: 34.1", parse_mode="Markdown")


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

    msg = "✅ Замер сохранён.\n"
    msg += _inbody_delta_text(prev, {"weight_kg": weight, "pbf_percent": pbf, "smm_kg": smm})
    msg += "\n" + _inbody_reco(weight, pbf, smm)
    await message.answer(msg, reply_markup=inbody_menu_inline())


@dp.callback_query(InBodyFSM.add_mode, F.data == "inbody_add_photo")
async def inbody_add_photo(cb: CallbackQuery, state: FSMContext):
    await state.set_state(InBodyFSM.photo_wait)
    await cb.message.edit_text(
        "📷 OCR InBody\n\n"
        "Пришли *фото отчёта InBody*.\n"
        "Совет: фото должно быть ровным, без бликов, текст читаемый.",
        parse_mode="Markdown",
        reply_markup=back_to_menu_inline()
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

    # default date today
    record_date = today_ymd()

    await state.update_data(
        ocr_metrics=metrics,
        ocr_confidence=confidence,
        ocr_raw=raw_text,
        ocr_date=record_date,
    )

    card = (
        "📷 *Распознано с фото*\n"
        f"Дата: *{record_date}*\n"
        f"Вес: *{metrics.get('weight_kg','—')} кг*\n"
        f"Жир: *{metrics.get('pbf_percent','—')} %*\n"
        f"Мышцы: *{metrics.get('smm_kg','—')} кг*\n\n"
        f"Confidence: *{int(confidence*100)}%*"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Сохранить", callback_data="ocr_save"),
         InlineKeyboardButton(text="✏️ Исправить", callback_data="ocr_edit")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="ocr_cancel")],
    ])

    await state.set_state(InBodyFSM.ocr_confirm)

    # если confidence низкий — подсказка
    if confidence < 0.67:
        await message.answer(
            "⚠️ Я вижу замер, но качество распознавания не идеальное.\n"
            "Рекомендую нажать «✏️ Исправить» и пройти по значениям.",
        )

    await message.answer(card, parse_mode="Markdown", reply_markup=kb)


@dp.message(InBodyFSM.photo_wait)
async def inbody_photo_wait_else(message: Message):
    await message.answer("Пожалуйста, отправь *фото* (как изображение).", parse_mode="Markdown")


@dp.callback_query(InBodyFSM.ocr_confirm, F.data == "ocr_cancel")
async def ocr_cancel(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.answer("Ок, замер отменён.", reply_markup=inbody_menu_inline())
    await cb.answer()


@dp.callback_query(InBodyFSM.ocr_confirm, F.data == "ocr_edit")
async def ocr_edit(cb: CallbackQuery, state: FSMContext):
    await state.set_state(InBodyFSM.ocr_edit_weight)
    await cb.message.answer("Исправление.\n\nШаг 1/3: введи *вес (кг)*:", parse_mode="Markdown")
    await cb.answer()


@dp.message(InBodyFSM.ocr_edit_weight)
async def ocr_edit_weight(message: Message, state: FSMContext):
    v = _parse_float_user(message.text or "")
    if v is None or not (30 <= v <= 250):
        await message.answer("Вес неверный. Пример: 72.4")
        return
    data = await state.get_data()
    metrics = dict(data.get("ocr_metrics", {}))
    metrics["weight_kg"] = round(v, 1)
    await state.update_data(ocr_metrics=metrics)

    await state.set_state(InBodyFSM.ocr_edit_pbf)
    await message.answer("Шаг 2/3: введи *жир (%)*:", parse_mode="Markdown")


@dp.message(InBodyFSM.ocr_edit_pbf)
async def ocr_edit_pbf(message: Message, state: FSMContext):
    v = _parse_float_user(message.text or "")
    if v is None or not (1 <= v <= 70):
        await message.answer("Жир неверный. Пример: 14.8")
        return
    data = await state.get_data()
    metrics = dict(data.get("ocr_metrics", {}))
    metrics["pbf_percent"] = round(v, 1)
    await state.update_data(ocr_metrics=metrics)

    await state.set_state(InBodyFSM.ocr_edit_smm)
    await message.answer("Шаг 3/3: введи *мышцы (кг)*:", parse_mode="Markdown")


@dp.message(InBodyFSM.ocr_edit_smm)
async def ocr_edit_smm(message: Message, state: FSMContext):
    v = _parse_float_user(message.text or "")
    if v is None or not (10 <= v <= 120):
        await message.answer("Мышцы неверные. Пример: 34.1")
        return

    data = await state.get_data()
    metrics = dict(data.get("ocr_metrics", {}))
    metrics["smm_kg"] = round(v, 1)
    await state.update_data(ocr_metrics=metrics)

    await state.set_state(InBodyFSM.ocr_confirm)

    record_date = data.get("ocr_date", today_ymd())
    conf = float(data.get("ocr_confidence", 0.5))
    card = (
        "📷 *Обновлено*\n"
        f"Дата: *{record_date}*\n"
        f"Вес: *{metrics.get('weight_kg','—')} кг*\n"
        f"Жир: *{metrics.get('pbf_percent','—')} %*\n"
        f"Мышцы: *{metrics.get('smm_kg','—')} кг*"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Сохранить", callback_data="ocr_save"),
         InlineKeyboardButton(text="✏️ Исправить", callback_data="ocr_edit")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="ocr_cancel")],
    ])
    await message.answer(card, parse_mode="Markdown", reply_markup=kb)


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

    msg = "✅ Замер сохранён (OCR).\n"
    msg += _inbody_delta_text(prev, {"weight_kg": weight, "pbf_percent": pbf, "smm_kg": smm})
    msg += "\n" + _inbody_reco(weight, pbf, smm)

    await cb.message.answer(msg, reply_markup=inbody_menu_inline())
    await cb.answer()


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
        await cb.message.answer("История пустая. Добавь первый замер 🙂", reply_markup=inbody_menu_inline())
        await cb.answer()
        return

    text = "📋 *История замеров*\n\n"
    for r in rows:
        text += (
            f"📅 {r[0]}\n"
            f"• Вес: {r[1] if r[1] is not None else '—'} кг\n"
            f"• Жир: {r[2] if r[2] is not None else '—'} %\n"
            f"• Мышцы: {r[3] if r[3] is not None else '—'} кг\n"
            f"Источник: {r[4]} (conf {int((r[5] or 0)*100)}%)\n\n"
        )
    await cb.message.answer(text, parse_mode="Markdown", reply_markup=inbody_menu_inline())
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
    await cb.message.answer(
        "🧾 Сравнение двух замеров (MVP)\n\n"
        "Пока упрощено: сравнение идёт автоматически при добавлении нового замера.\n"
        "Следующий шаг: выбор двух дат кнопками.",
        reply_markup=inbody_menu_inline()
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
        row.append(InlineKeyboardButton(text=c, callback_data=f"st_cat:{c}"))
        if len(row) == 2:
            kb.append(row)
            row = []
    if row:
        kb.append(row)

    kb.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="open_strength")])
    await cb.message.edit_text("Выбери категорию:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await cb.answer()


@dp.callback_query(F.data == "open_strength")
async def open_strength_cb(cb: CallbackQuery):
    await cb.message.edit_text("💪 Силовые", reply_markup=strength_menu_inline())
    await cb.answer()


@dp.callback_query(StrengthFSM.choose_category, F.data.startswith("st_cat:"))
async def strength_choose_cat(cb: CallbackQuery, state: FSMContext):
    cat = cb.data.split(":", 1)[1]
    await state.update_data(category=cat)
    await state.set_state(StrengthFSM.choose_exercise)

    async with aiosqlite.connect(DB_PATH) as db:
        user_id = await db_get_user_id(db, cb.from_user.id)
        rows = await db_list_exercises(db, user_id, None if cat == "Своя" else cat)

    # show up to 16
    rows = rows[:16]
    kb = []
    for r in rows:
        ex_id, ex_cat, name, is_custom, owner_id = r
        kb.append([InlineKeyboardButton(text=name, callback_data=f"st_ex:{ex_id}")])
    kb.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="strength_add")])
    await cb.message.edit_text("Выбери упражнение:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await cb.answer()


@dp.callback_query(StrengthFSM.choose_exercise, F.data.startswith("st_ex:"))
async def strength_choose_ex(cb: CallbackQuery, state: FSMContext):
    ex_id = int(cb.data.split(":")[1])
    await state.update_data(exercise_id=ex_id)
    await state.set_state(StrengthFSM.enter_weight)
    await cb.message.edit_text("Введи *вес* (кг), например 80:", parse_mode="Markdown", reply_markup=back_to_menu_inline())
    await cb.answer()


@dp.message(StrengthFSM.enter_weight)
async def strength_enter_weight(message: Message, state: FSMContext):
    v = _parse_float_user(message.text or "")
    if v is None or not (0 <= v <= 500):
        await message.answer("Вес неверный. Пример: 80")
        return
    await state.update_data(weight=float(v))
    await state.set_state(StrengthFSM.enter_reps)
    await message.answer("Введи *повторы*, например 8:", parse_mode="Markdown")


@dp.message(StrengthFSM.enter_reps)
async def strength_enter_reps(message: Message, state: FSMContext):
    txt = (message.text or "").strip()
    if not txt.isdigit():
        await message.answer("Повторы должны быть числом. Пример: 8")
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

        ex_name = await fetchone(db,"SELECT name FROM exercises WHERE id=?", (ex_id,))
        ex_name = ex_name[0] if ex_name else "Упражнение"

    e1rm = weight * (1 + reps / 30.0)
    await state.clear()
    await message.answer(
        f"✅ Записано: *{ex_name}*\n"
        f"{weight} кг × {reps}\n"
        f"Оценка 1RM: *{e1rm:.1f} кг*",
        parse_mode="Markdown",
        reply_markup=strength_menu_inline()
    )


@dp.callback_query(F.data == "strength_pr")
async def strength_pr(cb: CallbackQuery):
    async with aiosqlite.connect(DB_PATH) as db:
        user_id = await db_get_user_id(db, cb.from_user.id)
        rows = await db_strength_pr(db, user_id)

    if not rows:
        await cb.message.answer("Пока нет записей. Добавь первый результат 🙂", reply_markup=strength_menu_inline())
        await cb.answer()
        return

    text = "🏆 *Мои рекорды (PR)*\n\n"
    for r in rows:
        name, best_weight, best_e1rm, best_vol = r
        text += (
            f"• *{name}*\n"
            f"  - лучший вес: {best_weight:.1f} кг\n"
            f"  - лучший 1RM: {best_e1rm:.1f} кг\n"
            f"  - лучший объём: {best_vol:.0f}\n\n"
        )
    await cb.message.answer(text, parse_mode="Markdown", reply_markup=strength_menu_inline())
    await cb.answer()


@dp.callback_query(F.data == "strength_chart")
async def strength_chart(cb: CallbackQuery):
    await cb.message.answer(
        "📊 График упражнения (MVP)\n\n"
        "Сейчас это упрощено: графики делаются в разделе «📈 Аналитика → 💪 Анализ силовых».",
        reply_markup=strength_menu_inline()
    )
    await cb.answer()


# -------------------------
# Exercises management
# -------------------------
@dp.callback_query(F.data == "strength_exercises")
async def strength_exercises(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📚 Список по категориям", callback_data="ex_list")],
        [InlineKeyboardButton(text="➕ Добавить своё упражнение", callback_data="ex_add")],
        [InlineKeyboardButton(text="✏️ Переименовать своё", callback_data="ex_rename")],
        [InlineKeyboardButton(text="🗑 Удалить своё", callback_data="ex_delete")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="open_strength")],
    ])
    await cb.message.edit_text("🏋️ Упражнения", reply_markup=kb)
    await cb.answer()


@dp.callback_query(F.data == "ex_list")
async def ex_list(cb: CallbackQuery):
    async with aiosqlite.connect(DB_PATH) as db:
        user_id = await db_get_user_id(db, cb.from_user.id)
        rows = await db_list_exercises(db, user_id)

    text = "📚 *Упражнения*\n\n"
    current = None
    for r in rows:
        ex_id, cat, name, is_custom, owner = r
        if cat != current:
            current = cat
            text += f"\n*{cat}*\n"
        tag = " (моё)" if is_custom else ""
        text += f"• {name}{tag}\n"
    await cb.message.answer(text, parse_mode="Markdown", reply_markup=strength_menu_inline())
    await cb.answer()


@dp.callback_query(F.data == "ex_add")
async def ex_add(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(ExerciseManageFSM.add_custom_name)
    await state.update_data(custom_category="Своя")
    await cb.message.answer("➕ Добавить своё упражнение\n\nВведи название упражнения:", reply_markup=back_to_menu_inline())
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
    await message.answer("✅ Добавлено.", reply_markup=strength_menu_inline())


@dp.callback_query(F.data == "ex_rename")
async def ex_rename(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(ExerciseManageFSM.rename_choose)

    async with aiosqlite.connect(DB_PATH) as db:
        user_id = await db_get_user_id(db, cb.from_user.id)
        rows = await fetchall(db,"""
            SELECT id,name FROM exercises
            WHERE user_id=? AND is_custom=1 AND is_active=1
            ORDER BY name ASC
        """, (user_id,))

    if not rows:
        await cb.message.answer("У тебя нет своих упражнений.", reply_markup=strength_menu_inline())
        await cb.answer()
        return

    kb = [[InlineKeyboardButton(text=r[1], callback_data=f"ex_ren_pick:{r[0]}")] for r in rows[:20]]
    kb.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="strength_exercises")])
    await cb.message.answer("Выбери упражнение для переименования:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await cb.answer()


@dp.callback_query(ExerciseManageFSM.rename_choose, F.data.startswith("ex_ren_pick:"))
async def ex_ren_pick(cb: CallbackQuery, state: FSMContext):
    ex_id = int(cb.data.split(":")[1])
    await state.update_data(rename_ex_id=ex_id)
    await state.set_state(ExerciseManageFSM.rename_new_name)
    await cb.message.answer("Введи новое название:", reply_markup=back_to_menu_inline())
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
    await message.answer("✅ Переименовано.", reply_markup=strength_menu_inline())


@dp.callback_query(F.data == "ex_delete")
async def ex_delete(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(ExerciseManageFSM.delete_choose)

    async with aiosqlite.connect(DB_PATH) as db:
        user_id = await db_get_user_id(db, cb.from_user.id)
        rows = await fetchall(db,"""
            SELECT id,name FROM exercises
            WHERE user_id=? AND is_custom=1 AND is_active=1
            ORDER BY name ASC
        """, (user_id,))

    if not rows:
        await cb.message.answer("У тебя нет своих упражнений.", reply_markup=strength_menu_inline())
        await cb.answer()
        return

    kb = [[InlineKeyboardButton(text="🗑 " + r[1], callback_data=f"ex_del_pick:{r[0]}")] for r in rows[:20]]
    kb.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="strength_exercises")])
    await cb.message.answer("Выбери упражнение для удаления:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await cb.answer()


@dp.callback_query(ExerciseManageFSM.delete_choose, F.data.startswith("ex_del_pick:"))
async def ex_del_pick(cb: CallbackQuery, state: FSMContext):
    ex_id = int(cb.data.split(":")[1])
    async with aiosqlite.connect(DB_PATH) as db:
        user_id = await db_get_user_id(db, cb.from_user.id)
        await db_delete_custom_exercise(db, user_id, ex_id)
    await state.clear()
    await cb.message.answer("✅ Удалено (скрыто из списка).", reply_markup=strength_menu_inline())
    await cb.answer()


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
        row.append(InlineKeyboardButton(text=g, callback_data=f"wo_g:{g}"))
        if len(row) == 2:
            kb.append(row)
            row = []
    if row:
        kb.append(row)

    kb.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="open_workout")])
    await cb.message.edit_text("Какую группу тренируем?", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await cb.answer()


@dp.callback_query(F.data == "open_workout")
async def open_workout_cb(cb: CallbackQuery):
    await cb.message.edit_text("🏋️ Тренировка (дневник)", reply_markup=workout_menu_inline())
    await cb.answer()


@dp.callback_query(WorkoutFSM.choose_group, F.data.startswith("wo_g:"))
async def wo_choose_group(cb: CallbackQuery, state: FSMContext):
    group = cb.data.split(":", 1)[1]
    await state.update_data(workout_group=group)

    async with aiosqlite.connect(DB_PATH) as db:
        user_id = await db_get_user_id(db, cb.from_user.id)
        workout_id = await db_workout_create(db, user_id, today_ymd(), group)

        # choose exercises from this group or all if "Своя"
        rows = await db_list_exercises(db, user_id, None if group == "Своя" else group)

    SESSIONS[cb.from_user.id] = WorkoutSession(workout_id=workout_id, group=group)

    await state.set_state(WorkoutFSM.choose_exercise)

    kb = []
    for r in rows[:16]:
        ex_id, cat, name, is_custom, owner = r
        kb.append([InlineKeyboardButton(text=name, callback_data=f"wo_ex:{ex_id}")])

    kb.append([InlineKeyboardButton(text="✅ Завершить тренировку", callback_data="wo_finish")])
    await cb.message.edit_text(
        f"▶️ Тренировка начата: *{group}*\n\nВыбери упражнение:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
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
        row = await fetchone(db,"SELECT name FROM exercises WHERE id=?", (ex_id,))
        name = row[0] if row else "Упражнение"

    sess.current_exercise_id = ex_id
    sess.current_exercise_name = name
    sess.set_no = 0

    await state.set_state(WorkoutFSM.set_weight)

    await cb.message.answer(
        f"🏋️ *{name}*\n"
        f"Подход 1: введи *вес* (кг):",
        parse_mode="Markdown"
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
        await message.answer("Вес неверный. Пример: 60")
        return

    await state.update_data(set_weight=float(v))
    await state.set_state(WorkoutFSM.set_reps)
    await message.answer("Теперь введи *повторы*:", parse_mode="Markdown")


@dp.message(WorkoutFSM.set_reps)
async def wo_set_reps(message: Message, state: FSMContext):
    sess = SESSIONS.get(message.from_user.id)
    if not sess or not sess.current_exercise_id:
        await message.answer("Выбери упражнение заново.")
        return

    txt = (message.text or "").strip()
    if not txt.isdigit():
        await message.answer("Повторы должны быть числом. Пример: 10")
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

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Ещё подход", callback_data="wo_more_set")],
        [InlineKeyboardButton(text="✅ Завершить упражнение", callback_data="wo_end_ex")],
        [InlineKeyboardButton(text="✅ Завершить тренировку", callback_data="wo_finish")],
    ])

    await state.set_state(WorkoutFSM.in_workout)
    await message.answer(
        f"Записано: {weight} кг × {reps} (подход {set_no}) ✅",
        reply_markup=kb
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
        f"Подход {next_set}: введи *вес* (кг):",
        parse_mode="Markdown"
    )
    await cb.answer()


@dp.callback_query(WorkoutFSM.in_workout, F.data == "wo_end_ex")
async def wo_end_ex(cb: CallbackQuery, state: FSMContext):
    await state.set_state(WorkoutFSM.choose_exercise)
    await cb.message.answer("Упражнение завершено ✅\nВыбери следующее упражнение в меню тренировки.")
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
        "✅ Тренировка сохранена.\n\n"
        f"Итоги:\n"
        f"• Подходы: {sets}\n"
        f"• Повторы: {reps}\n"
        f"• Тоннаж: {tonnage:.0f} кг\n",
        reply_markup=workout_menu_inline()
    )
    await cb.answer()


@dp.callback_query(F.data == "workout_history")
async def workout_history(cb: CallbackQuery):
    async with aiosqlite.connect(DB_PATH) as db:
        user_id = await db_get_user_id(db, cb.from_user.id)
        rows = await db_workout_history(db, user_id)

    if not rows:
        await cb.message.answer("История тренировок пустая.", reply_markup=workout_menu_inline())
        await cb.answer()
        return

    text = "🧾 *История тренировок*\n\n"
    for r in rows:
        text += (
            f"📅 {r[0]} — *{r[1]}*\n"
            f"• Подходы: {r[2]} | Повторы: {r[3]} | Тоннаж: {r[4]:.0f} кг\n\n"
        )
    await cb.message.answer(text, parse_mode="Markdown", reply_markup=workout_menu_inline())
    await cb.answer()


@dp.callback_query(F.data == "workout_ai_tip")
async def workout_ai_tip(cb: CallbackQuery):
    await cb.message.answer(
        "🧠 Совет по тренировке (AI)\n\n"
        "MVP-версия:\n"
        "• Разминка 5–10 минут\n"
        "• 1–2 разминочных подхода перед рабочими\n"
        "• 8–12 повторов для гипертрофии, 3–6 для силы\n"
        "• Прогрессия: +1 повтор или +2.5 кг раз в 1–2 недели\n"
        "• Сон 7–9 часов — это часть прогресса",
        reply_markup=workout_menu_inline()
    )
    await cb.answer()


# -------------------------
# Analytics
# -------------------------
@dp.callback_query(F.data == "an_body")
async def an_body(cb: CallbackQuery):
    await cb.message.answer(
        "📄 Анализ замеров тела\n\n"
        "Используй раздел «📄 Замеры → 📊 Графики замеров».",
        reply_markup=analytics_menu_inline()
    )
    await cb.answer()


@dp.callback_query(F.data == "an_strength")
async def an_strength(cb: CallbackQuery):
    # build one aggregated chart: top exercise by record count
    async with aiosqlite.connect(DB_PATH) as db:
        user_id = await db_get_user_id(db, cb.from_user.id)
        row = await fetchone(db,"""
            SELECT exercise_id, COUNT(*) cnt
            FROM strength_records
            WHERE user_id=?
            GROUP BY exercise_id
            ORDER BY cnt DESC
            LIMIT 1
        """, (user_id,))
        if not row:
            await cb.message.answer("Нет данных по силовым. Запиши хотя бы 2 результата.", reply_markup=analytics_menu_inline())
            await cb.answer()
            return
        ex_id = row[0]
        ex_name = await fetchone(db,"SELECT name FROM exercises WHERE id=?", (ex_id,))
        ex_name = ex_name[0] if ex_name else "Упражнение"

        rows = await fetchall(db,"""
            SELECT record_date, weight, reps
            FROM strength_records
            WHERE user_id=? AND exercise_id=?
            ORDER BY record_date ASC
        """, (user_id, ex_id))

    dates = [r[0][5:] for r in rows]
    e1rm = [r[1] * (1 + r[2]/30.0) for r in rows]

    buf = plot_series(dates, e1rm, f"Силовые: {ex_name} (1RM)", "кг")
    await cb.message.answer_photo(photo=buf, caption=f"💪 Прогресс (1RM): {ex_name}")
    await cb.message.answer("Подсказка: больше графиков появится, когда добавишь больше упражнений.", reply_markup=analytics_menu_inline())
    await cb.answer()


@dp.callback_query(F.data == "an_total")
async def an_total(cb: CallbackQuery):
    await cb.message.answer(
        "🧩 Общий прогресс\n\n"
        "MVP подсказка:\n"
        "• Тренировки ≥ 3/нед + сон\n"
        "• Белок и прогрессия нагрузок\n"
        "• Замеры каждые 7–14 дней\n\n"
        "Если хочешь, в следующей версии сделаю «оценку прогресса» в баллах.",
        reply_markup=analytics_menu_inline()
    )
    await cb.answer()


@dp.callback_query(F.data == "an_forecast")
async def an_forecast(cb: CallbackQuery):
    await cb.message.answer(
        "🔮 Прогноз на 30 дней (MVP)\n\n"
        "Простой ориентир:\n"
        "• При стабильной программе + питании реальный прирост силы обычно заметен через 2–4 недели.\n"
        "• Замеры тела меняются медленнее — оценивай тренд минимум по 3 замерам.",
        reply_markup=analytics_menu_inline()
    )
    await cb.answer()


# -------------------------
# Settings
# -------------------------
@dp.callback_query(F.data == "set_units")
async def set_units(cb: CallbackQuery):
    await cb.message.answer(
        "🔁 Единицы измерения\n\n"
        "MVP: базово используется кг.\n"
        "Если нужно — добавлю полную поддержку фунтов во всех разделах.",
        reply_markup=settings_menu_inline()
    )
    await cb.answer()


@dp.callback_query(F.data == "set_reminders")
async def set_reminders(cb: CallbackQuery):
    await cb.message.answer(
        "🔔 Напоминания\n\n"
        "MVP: раздел подготовлен.\n"
        "В следующей версии можно добавить расписание и нотификации.",
        reply_markup=settings_menu_inline()
    )
    await cb.answer()


@dp.callback_query(F.data == "clear_cache")
async def clear_cache(cb: CallbackQuery):
    try:
        if os.path.isdir("cache_photos"):
            for fn in os.listdir("cache_photos"):
                if fn.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                    os.remove(os.path.join("cache_photos", fn))
        await cb.message.answer("🧹 Кэш фото очищен ✅", reply_markup=settings_menu_inline())
    except Exception:
        await cb.message.answer("Не удалось очистить кэш.", reply_markup=settings_menu_inline())
    await cb.answer()


# -------------------------
# Run
# -------------------------
async def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN пуст. Заполни .env")
    await db_init()
    async with aiosqlite.connect(DB_PATH) as db:
        await db_seed_default_exercises(db)

    print("Gym Bot started.")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
