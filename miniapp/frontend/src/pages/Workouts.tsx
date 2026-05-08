import { useEffect, useMemo, useState } from "react";
import { motion } from "framer-motion";
import { Calendar, Dumbbell, Trophy } from "lucide-react";
import { PageHeader } from "../components/PageHeader";
import { api } from "../lib/api";
import type { StrengthPR, WorkoutItem } from "../lib/types";
import { useSettings } from "../store/settings";
import { formatMass, massFromKg } from "../lib/units";

const RU_DOW = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
const RU_MONTH = ["Янв", "Фев", "Мар", "Апр", "Май", "Июн", "Июл", "Авг", "Сен", "Окт", "Ноя", "Дек"];

function buildHeatmap(workouts: WorkoutItem[], weeks = 12): Array<{ date: string; intensity: number }[]> {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  // Найти понедельник этой недели
  const dayShift = (today.getDay() + 6) % 7; // Пн=0
  const monday = new Date(today);
  monday.setDate(today.getDate() - dayShift);

  const start = new Date(monday);
  start.setDate(monday.getDate() - (weeks - 1) * 7);

  const counts = new Map<string, number>();
  for (const w of workouts) {
    counts.set(w.workout_date, (counts.get(w.workout_date) ?? 0) + 1);
  }

  const grid: Array<{ date: string; intensity: number }[]> = [];
  for (let w = 0; w < weeks; w++) {
    const week: { date: string; intensity: number }[] = [];
    for (let d = 0; d < 7; d++) {
      const date = new Date(start);
      date.setDate(start.getDate() + w * 7 + d);
      const iso = date.toISOString().slice(0, 10);
      week.push({ date: iso, intensity: counts.get(iso) ?? 0 });
    }
    grid.push(week);
  }
  return grid;
}

const intensityClass = (n: number, future: boolean) => {
  if (future) return "bg-transparent border border-default";
  if (n === 0) return "surface-2";
  if (n === 1) return "bg-accent/40";
  return "bg-accent";
};

export function WorkoutsPage() {
  const mass = useSettings((s) => s.mass);
  const [workouts, setWorkouts] = useState<WorkoutItem[]>([]);
  const [prs, setPrs] = useState<StrengthPR[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([api.getWorkouts(120), api.getStrengthPRs()])
      .then(([w, p]) => {
        setWorkouts(w);
        setPrs(p);
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  const heatmap = useMemo(() => buildHeatmap(workouts, 12), [workouts]);
  const todayIso = new Date().toISOString().slice(0, 10);

  const totals = useMemo(() => {
    const total = workouts.length;
    const byMonth = new Map<string, number>();
    for (const w of workouts) {
      const k = w.workout_date.slice(0, 7);
      byMonth.set(k, (byMonth.get(k) ?? 0) + 1);
    }
    return { total, months: byMonth };
  }, [workouts]);

  return (
    <div>
      <PageHeader title="Тренировки" subtitle={`Всего: ${totals.total} • PR: ${prs.length}`} />

      <div className="px-5">
        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ type: "spring", stiffness: 240, damping: 24 }}
          className="surface rounded-3xl p-5"
        >
          <div className="flex items-center justify-between mb-4">
            <div>
              <div className="flex items-center gap-2">
                <Calendar className="w-4 h-4 text-muted" />
                <h3 className="font-medium">Календарь посещений</h3>
              </div>
              <p className="text-muted text-xs mt-1">12 недель</p>
            </div>
            <div className="flex items-center gap-1">
              <span className="w-3 h-3 rounded-sm surface-2" />
              <span className="w-3 h-3 rounded-sm bg-accent/40" />
              <span className="w-3 h-3 rounded-sm bg-accent" />
            </div>
          </div>

          <div className="flex gap-3">
            <div className="flex flex-col justify-between text-[10px] text-muted py-1">
              {RU_DOW.map((d) => (
                <span key={d}>{d}</span>
              ))}
            </div>
            <div className="flex-1 grid grid-flow-col auto-cols-fr gap-1">
              {heatmap.map((week, wi) => (
                <div key={wi} className="grid grid-rows-7 gap-1">
                  {week.map((cell) => {
                    const future = cell.date > todayIso;
                    return (
                      <div
                        key={cell.date}
                        title={`${cell.date}: ${cell.intensity} тр.`}
                        className={`aspect-square rounded-md ${intensityClass(cell.intensity, future)}`}
                      />
                    );
                  })}
                </div>
              ))}
            </div>
          </div>
        </motion.div>

        <div className="mt-4">
          <div className="flex items-center justify-between mb-2 px-1">
            <h3 className="font-medium flex items-center gap-2">
              <Trophy className="w-4 h-4 text-muted" /> Personal records
            </h3>
            <span className="text-muted text-xs">{prs.length}</span>
          </div>
          {prs.length === 0 ? (
            <div className="surface rounded-2xl p-4 text-sm text-muted text-center">
              Пока нет PR. Закроются автоматически после первых тренировок.
            </div>
          ) : (
            <div className="space-y-2">
              {prs.slice(0, 8).map((pr) => (
                <div key={`${pr.exercise}-${pr.record_date}`} className="surface rounded-2xl px-4 py-3 flex items-center justify-between">
                  <div className="min-w-0">
                    <div className="font-medium truncate">{pr.exercise}</div>
                    <div className="text-muted text-xs">{pr.record_date}</div>
                  </div>
                  <div className="text-right shrink-0 ml-3">
                    <div className="font-mono font-semibold">
                      {formatMass(pr.weight, mass, 1)}
                    </div>
                    <div className="text-muted text-xs">{pr.reps} повт.</div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="mt-4">
          <div className="flex items-center justify-between mb-2 px-1">
            <h3 className="font-medium flex items-center gap-2">
              <Dumbbell className="w-4 h-4 text-muted" /> История
            </h3>
            <span className="text-muted text-xs">{workouts.length}</span>
          </div>
          {workouts.length === 0 ? (
            <div className="surface rounded-2xl p-4 text-sm text-muted text-center">
              Нет тренировок за 120 дней. Запиши первую через бота.
            </div>
          ) : (
            <div className="space-y-2">
              {workouts.slice(0, 30).map((w) => (
                <motion.div
                  key={w.id}
                  initial={{ opacity: 0, y: 6 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.18 }}
                  className="surface rounded-2xl px-4 py-3 flex items-center justify-between"
                >
                  <div className="min-w-0">
                    <div className="font-medium truncate">{w.group_name}</div>
                    <div className="text-muted text-xs">
                      {fmtDateShort(w.workout_date)} • {w.total_sets} подх • {w.total_reps} повт
                    </div>
                  </div>
                  <div className="text-right shrink-0 ml-3">
                    <div className="font-mono text-sm font-semibold">
                      {Math.round(massFromKg(w.total_tonnage, mass)).toLocaleString("ru-RU")}
                    </div>
                    <div className="text-muted text-xs">{mass === "kg" ? "кг" : "lb"} тоннаж</div>
                  </div>
                </motion.div>
              ))}
            </div>
          )}
        </div>

        {error ? (
          <div className="surface rounded-2xl p-4 mt-4 text-sm text-danger">{error}</div>
        ) : null}
      </div>
    </div>
  );
}

function fmtDateShort(iso: string): string {
  const d = new Date(iso + "T00:00:00");
  return `${d.getDate()} ${RU_MONTH[d.getMonth()]}`;
}
