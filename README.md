# gymtgbit

Telegram-бот для трекинга прогресса в зале: замеры InBody (вручную или OCR с фото), силовые рекорды, журнал тренировок, графики и аналитика.

## Стек

- [aiogram 3.6](https://docs.aiogram.dev/) — Telegram Bot framework
- [aiosqlite](https://github.com/omnilib/aiosqlite) — async SQLite
- [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) + [Tesseract](https://github.com/tesseract-ocr/tesseract) — OCR
- matplotlib — графики

## Запуск

1. Создать и активировать виртуальное окружение:
   ```bash
   python -m venv .venv
   # Windows:
   .venv\Scripts\activate
   # Linux/macOS:
   source .venv/bin/activate
   ```

2. Установить зависимости:
   ```bash
   pip install -r requirements.txt
   ```

3. Скопировать `.env.example` в `.env` и заполнить токен:
   ```bash
   cp .env.example .env
   # затем отредактировать .env и вставить BOT_TOKEN от @BotFather
   ```

4. Запустить бота:
   ```bash
   python bot.py
   ```

База данных `gym_bot.db` будет создана автоматически при первом запуске.

## Переменные окружения

| Переменная | По умолчанию | Описание |
|---|---|---|
| `BOT_TOKEN` | — | Токен Telegram-бота от @BotFather |
| `DB_PATH` | `gym_bot.db` | Путь к файлу SQLite |
| `PADDLE_USE_GPU` | `0` | `1` чтобы PaddleOCR использовал GPU |
| `TESSERACT_CMD` | — | Путь к бинарнику Tesseract (для Windows) |

## Структура

- `bot.py` — обработчики aiogram, FSM, UI
- `dashboard.py` — главный экран «Состояние тела» с интерпретацией
- `ocr_utils.py` — OCR pipeline (preprocessing + PaddleOCR + Tesseract fallback)
- `schema.sql` — SQL-схема (применяется встроенная копия из `bot.py:SCHEMA_SQL`)

## Что умеет

- Онбординг профиля (пол, возраст, рост, уровень, цель)
- Замеры InBody — ручной ввод или OCR с фото
- Главный экран с интерпретацией динамики тела (вес, жир, мышцы за 7/30 дней, серии тренировок)
- Силовые рекорды с автоматическим расчётом 1RM
- Журнал тренировок (группы мышц, упражнения, подходы)
- Базовая аналитика и графики
