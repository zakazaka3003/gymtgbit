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
    birth_date TEXT NOT NULL, -- YYYY-MM-DD
    height_cm INTEGER CHECK(height_cm BETWEEN 120 AND 230) NOT NULL,
    level TEXT NOT NULL, -- Новичок/Средний/Продвинутый
    goal TEXT NOT NULL,  -- Набор мышц/Похудение/Поддержание/Сила/Выносливость
    units TEXT NOT NULL DEFAULT 'kg', -- kg/lb
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS inbody_records (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    record_date TEXT NOT NULL, -- YYYY-MM-DD
    weight_kg REAL,
    pbf_percent REAL, -- body fat %
    smm_kg REAL,      -- skeletal muscle mass
    source TEXT NOT NULL DEFAULT 'manual', -- manual/ocr
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
    user_id INTEGER NOT NULL, -- 0 = default system exercises, else custom
    category TEXT NOT NULL,   -- Грудь/Спина/Ноги/Плечи/Руки/Полное тело/Своя
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
