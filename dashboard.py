"""
Главный экран «Состояние тела».

Логика умышленно отделена от aiogram и хранит ровно один
async-вход в БД (``fetch_dashboard_data``), чтобы её можно было
переиспользовать на FastAPI/Postgres без переписывания.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Iterable, List, Optional

import aiosqlite


# -------------------------
# Data containers
# -------------------------
@dataclass
class InbodyPoint:
    record_date: dt.date
    weight_kg: Optional[float]
    pbf_percent: Optional[float]
    smm_kg: Optional[float]


@dataclass
class DashboardData:
    today: dt.date

    # latest InBody snapshot
    last_inbody: Optional[InbodyPoint]
    inbody_7d_ago: Optional[InbodyPoint]
    inbody_30d_ago: Optional[InbodyPoint]
    days_since_last_inbody: Optional[int]
    total_inbody_records: int

    # workouts
    workouts_last_7d: int
    workouts_last_30d: int
    days_since_last_workout: Optional[int]
    streak_weeks: int  # последовательные недели с >=1 тренировкой, включая текущую
    tonnage_last_7d: float


# -------------------------
# Helpers
# -------------------------
def _parse_date(s: Optional[str]) -> Optional[dt.date]:
    if not s:
        return None
    try:
        return dt.date.fromisoformat(s[:10])
    except ValueError:
        return None


def _row_to_point(row) -> Optional[InbodyPoint]:
    if not row:
        return None
    d = _parse_date(row[0])
    if not d:
        return None
    return InbodyPoint(
        record_date=d,
        weight_kg=row[1],
        pbf_percent=row[2],
        smm_kg=row[3],
    )


def _pick_baseline(rows: Iterable[InbodyPoint], target: dt.date) -> Optional[InbodyPoint]:
    """
    Берём ближайший замер к ``target`` (предпочтительно ``<= target``).
    Если нет ни одного замера до ``target`` — отдаём ближайший после.
    """
    rows = list(rows)
    if not rows:
        return None
    before = [r for r in rows if r.record_date <= target]
    if before:
        return max(before, key=lambda r: r.record_date)
    return min(rows, key=lambda r: abs((r.record_date - target).days))


def _delta(cur: Optional[float], base: Optional[float]) -> Optional[float]:
    if cur is None or base is None:
        return None
    return cur - base


# -------------------------
# Data fetch
# -------------------------
async def fetch_dashboard_data(db: aiosqlite.Connection, user_id: int) -> DashboardData:
    today = dt.date.today()

    # все замеры — их обычно <100, оптимизация не нужна
    cur = await db.execute(
        """
        SELECT record_date, weight_kg, pbf_percent, smm_kg
        FROM inbody_records
        WHERE user_id=?
        ORDER BY record_date ASC
        """,
        (user_id,),
    )
    inbody_rows = await cur.fetchall()
    await cur.close()

    points: List[InbodyPoint] = []
    for r in inbody_rows:
        p = _row_to_point(r)
        if p is not None:
            points.append(p)

    last = points[-1] if points else None
    base_7d = _pick_baseline(points[:-1], today - dt.timedelta(days=7)) if last else None
    base_30d = _pick_baseline(points[:-1], today - dt.timedelta(days=30)) if last else None

    days_since_last_inbody = (today - last.record_date).days if last else None

    # workouts
    week_ago = (today - dt.timedelta(days=7)).isoformat()
    month_ago = (today - dt.timedelta(days=30)).isoformat()

    cur = await db.execute(
        """
        SELECT COUNT(*), COALESCE(SUM(total_tonnage), 0.0)
        FROM workouts
        WHERE user_id=? AND workout_date >= ?
        """,
        (user_id, week_ago),
    )
    row = await cur.fetchone()
    await cur.close()
    workouts_7d = int(row[0] or 0)
    tonnage_7d = float(row[1] or 0.0)

    cur = await db.execute(
        "SELECT COUNT(*) FROM workouts WHERE user_id=? AND workout_date >= ?",
        (user_id, month_ago),
    )
    row = await cur.fetchone()
    await cur.close()
    workouts_30d = int(row[0] or 0)

    cur = await db.execute(
        "SELECT MAX(workout_date) FROM workouts WHERE user_id=?",
        (user_id,),
    )
    row = await cur.fetchone()
    await cur.close()
    last_workout_date = _parse_date(row[0]) if row else None
    days_since_last_workout = (today - last_workout_date).days if last_workout_date else None

    # streak: уникальные ISO-недели (year, week) с тренировками
    cur = await db.execute(
        "SELECT DISTINCT workout_date FROM workouts WHERE user_id=?",
        (user_id,),
    )
    wo_dates_rows = await cur.fetchall()
    await cur.close()

    weeks_with_workout = set()
    for r in wo_dates_rows:
        d = _parse_date(r[0])
        if d is None:
            continue
        iso = d.isocalendar()
        weeks_with_workout.add((iso[0], iso[1]))

    streak_weeks = _streak_from_weeks(weeks_with_workout, today)

    return DashboardData(
        today=today,
        last_inbody=last,
        inbody_7d_ago=base_7d,
        inbody_30d_ago=base_30d,
        days_since_last_inbody=days_since_last_inbody,
        total_inbody_records=len(points),
        workouts_last_7d=workouts_7d,
        workouts_last_30d=workouts_30d,
        days_since_last_workout=days_since_last_workout,
        streak_weeks=streak_weeks,
        tonnage_last_7d=tonnage_7d,
    )


def _streak_from_weeks(weeks_with_workout: set, today: dt.date) -> int:
    """
    Считаем серию подряд идущих недель, заканчивающуюся текущей или прошлой.
    Если на этой неделе тренировок нет, но на прошлой были — серия продолжается
    с прошлой недели (текущая «в долгу», но ещё не сорвана).
    """
    if not weeks_with_workout:
        return 0

    iso = today.isocalendar()
    cur_week = (iso[0], iso[1])

    if cur_week in weeks_with_workout:
        anchor = today
    else:
        prev = today - dt.timedelta(days=7)
        prev_iso = prev.isocalendar()
        prev_week = (prev_iso[0], prev_iso[1])
        if prev_week in weeks_with_workout:
            anchor = prev
        else:
            return 0

    streak = 0
    cursor = anchor
    while True:
        c_iso = cursor.isocalendar()
        if (c_iso[0], c_iso[1]) in weeks_with_workout:
            streak += 1
            cursor -= dt.timedelta(days=7)
        else:
            break
    return streak


# -------------------------
# Formatting
# -------------------------
def _fmt_value(v: Optional[float], digits: int = 1, suffix: str = "") -> str:
    if v is None:
        return "—"
    return f"{v:.{digits}f}{suffix}"


def _fmt_delta(v: Optional[float], digits: int = 1) -> str:
    if v is None:
        return ""
    if abs(v) < 0.05:
        return " ±0.0"
    sign = "+" if v > 0 else ""
    return f" {sign}{v:.{digits}f}"


def _fmt_metric_line(emoji: str, label: str, cur: Optional[float],
                     d7: Optional[float], d30: Optional[float], unit: str) -> str:
    cur_s = _fmt_value(cur, 1, f" {unit}".rstrip())
    parts = [f"{emoji} {label}: *{cur_s}*"]
    bits = []
    if d7 is not None:
        bits.append(f"7д{_fmt_delta(d7)}")
    if d30 is not None:
        bits.append(f"30д{_fmt_delta(d30)}")
    if bits:
        parts.append("(" + " · ".join(bits) + ")")
    return " ".join(parts)


def format_dashboard(data: DashboardData, profile: Optional[dict]) -> str:
    """Рендерим карточку в Markdown (aiogram parse_mode='Markdown')."""
    if data.last_inbody is None and data.workouts_last_30d == 0:
        return (
            "📊 *Состояние тела*\n\n"
            "Замеров и тренировок пока нет.\n\n"
            "Начни с замера InBody — фото или вручную.\n"
            "Затем добавь первую тренировку — и здесь появится твоя динамика."
        )

    lines = ["📊 *Состояние тела*"]

    if data.last_inbody is not None:
        last = data.last_inbody
        d7w = _delta(last.weight_kg, data.inbody_7d_ago.weight_kg if data.inbody_7d_ago else None)
        d30w = _delta(last.weight_kg, data.inbody_30d_ago.weight_kg if data.inbody_30d_ago else None)
        d7p = _delta(last.pbf_percent, data.inbody_7d_ago.pbf_percent if data.inbody_7d_ago else None)
        d30p = _delta(last.pbf_percent, data.inbody_30d_ago.pbf_percent if data.inbody_30d_ago else None)
        d7s = _delta(last.smm_kg, data.inbody_7d_ago.smm_kg if data.inbody_7d_ago else None)
        d30s = _delta(last.smm_kg, data.inbody_30d_ago.smm_kg if data.inbody_30d_ago else None)

        lines.append("")
        lines.append(_fmt_metric_line("⚖️", "Вес", last.weight_kg, d7w, d30w, "кг"))
        lines.append(_fmt_metric_line("🔥", "Жир", last.pbf_percent, d7p, d30p, "%"))
        lines.append(_fmt_metric_line("💪", "Мышцы", last.smm_kg, d7s, d30s, "кг"))

        if data.days_since_last_inbody is not None:
            if data.days_since_last_inbody == 0:
                lines.append("_замер сегодня_")
            else:
                lines.append(f"_замер {data.days_since_last_inbody} дн. назад_")
    else:
        lines.append("")
        lines.append("Замеров InBody пока нет — добавь первый.")

    lines.append("")
    lines.append(f"🏋️ За 7 дней: *{data.workouts_last_7d}* трен. · "
                 f"тоннаж *{int(round(data.tonnage_last_7d))}* кг")
    lines.append(f"🗓 За 30 дней: *{data.workouts_last_30d}* трен.")
    if data.streak_weeks >= 2:
        lines.append(f"🔥 Серия: *{data.streak_weeks}* недель подряд")

    insights = interpret(data, profile)
    if insights:
        lines.append("")
        lines.append("🧠 *Анализ:*")
        for s in insights:
            lines.append(f"• {s}")

    return "\n".join(lines)


# -------------------------
# Rules-based interpretation
# -------------------------
def interpret(data: DashboardData, profile: Optional[dict]) -> List[str]:
    """Возвращаем 1-3 коротких инсайта по приоритету."""
    out: List[str] = []
    goal = (profile or {}).get("goal", "")

    last = data.last_inbody
    base_30d = data.inbody_30d_ago
    base_7d = data.inbody_7d_ago

    d30_w = _delta(last.weight_kg, base_30d.weight_kg) if last and base_30d else None
    d30_pbf = _delta(last.pbf_percent, base_30d.pbf_percent) if last and base_30d else None
    d30_smm = _delta(last.smm_kg, base_30d.smm_kg) if last and base_30d else None
    d7_w = _delta(last.weight_kg, base_7d.weight_kg) if last and base_7d else None

    # 1) данные устарели — приоритетный инсайт
    if last is not None and data.days_since_last_inbody is not None:
        if data.days_since_last_inbody >= 60:
            out.append("Замер старше 2 месяцев — без свежих данных оценка приблизительная. Сделай новый.")
        elif data.days_since_last_inbody >= 21:
            out.append(f"Замер был {data.days_since_last_inbody} дн. назад — пора обновить.")

    # 2) интерпретация по цели
    if goal == "Похудение" and d30_w is not None:
        if d30_w <= -2.0 and (d30_smm is not None and d30_smm < -0.5):
            out.append("⚠️ Вес падает быстро + теряются мышцы. Уменьши дефицит и подними белок до 2 г/кг.")
        elif d30_w < 0 and (d30_smm is None or d30_smm >= -0.2):
            out.append("Жир уходит, мышцы держатся — дефицит подобран хорошо.")
        elif abs(d30_w) < 0.3:
            out.append("Вес стоит 30 дней. Скорее всего питание ушло «в ноль» — проверь дефицит и шаги.")
        elif d30_w > 0.5:
            out.append("Вес растёт, хотя цель — похудение. Урежь калории на ~10%.")

    elif goal == "Набор мышц" and d30_w is not None:
        if d30_w > 0 and (d30_smm is None or d30_smm > 0):
            out.append("Идёшь в плюс — мышцы и общий вес растут. Держи профицит.")
        elif d30_w > 1.5 and (d30_pbf is not None and d30_pbf > 1.0):
            out.append("Профицит великоват — сильно растёт жир. Снизь калории на ~150–200 ккал.")
        elif d30_w <= 0:
            out.append("Веса нет 30 дней — для набора нужен профицит ~+200 ккал и +1 трен/нед.")

    elif goal in ("Поддержание", "Сила", "Выносливость") and d30_w is not None:
        if abs(d30_w) > 1.5:
            out.append(f"Вес сместился на {d30_w:+.1f} кг за 30 дней — это уже не «поддержание».")

    # 3) рекомпозиция как универсальный позитивный сигнал
    if (
        not out
        and d30_pbf is not None and d30_smm is not None
        and d30_pbf < -0.3 and d30_smm > 0.2
    ):
        out.append("Жир падает, мышцы растут — это рекомпозиция, отличный сценарий.")

    # 4) частые/редкие тренировки
    if data.days_since_last_workout is not None and data.days_since_last_workout >= 10:
        out.append(f"Без тренировок уже {data.days_since_last_workout} дн. — пора возвращаться.")
    elif data.workouts_last_7d == 0 and data.workouts_last_30d > 0:
        out.append("На этой неделе ещё ни одной тренировки.")
    elif data.workouts_last_7d >= 5:
        out.append("5+ тренировок за неделю — следи за восстановлением (сон, белок).")

    # 5) серия — мотивационный сигнал, добавляем только если ещё мало инсайтов
    if data.streak_weeks >= 4 and len(out) < 3:
        out.append(f"Серия {data.streak_weeks} недель подряд — держи темп.")

    # 6) резкое падение веса за 7 дней — алерт
    if d7_w is not None and d7_w < -1.5 and goal != "Похудение":
        out.insert(0, f"Резкое падение веса за неделю ({d7_w:+.1f} кг) — это в основном вода/гликоген, не жир.")

    return out[:3]
