import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { Activity, Flame, Scale, Sparkles, Trophy, TrendingUp } from "lucide-react";
import { PageHeader } from "../components/PageHeader";
import { Tile } from "../components/Tile";
import { Sparkline } from "../components/Sparkline";
import { api } from "../lib/api";
import type { Dashboard as DashboardData, InBodyPoint } from "../lib/types";
import { useSettings } from "../store/settings";
import {
  formatMass,
  formatMassDelta,
  formatPercent,
  formatPercentDelta,
} from "../lib/units";
import { tgUserName } from "../lib/twa";

const tone = (d: number | null | undefined, lowerIsBetter = false) => {
  if (d == null || d === 0) return "neutral" as const;
  const good = lowerIsBetter ? d < 0 : d > 0;
  return good ? "good" : ("bad" as const);
};

export function DashboardPage() {
  const mass = useSettings((s) => s.mass);
  const [data, setData] = useState<DashboardData | null>(null);
  const [history, setHistory] = useState<InBodyPoint[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([api.getDashboard(), api.getInBodyHistory(60)])
      .then(([d, h]) => {
        if (cancelled) return;
        setData(d);
        setHistory(h);
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
    return () => {
      cancelled = true;
    };
  }, []);

  const name = tgUserName();
  const subtitle =
    data?.last_record_date == null
      ? "Нет замеров — добавь первый, чтобы увидеть динамику"
      : `Последний замер: ${data.last_record_date}${
          data.days_since_last != null ? ` • ${data.days_since_last} дн назад` : ""
        }`;

  const advice = buildAdvice(data);
  const weightSeries = history.map((p) => p.weight_kg ?? 0).filter((v) => v > 0);
  const fatSeries = history.map((p) => p.pbf_percent ?? 0).filter((v) => v > 0);
  const muscleSeries = history.map((p) => p.smm_kg ?? 0).filter((v) => v > 0);

  return (
    <div>
      <PageHeader
        title={name ? `Привет, ${name}` : "Состояние"}
        subtitle={subtitle}
      />

      {error ? (
        <div className="px-5 pb-3">
          <div className="surface rounded-2xl p-4 text-sm text-danger">
            Ошибка: {error}. Зайди через бота — ему нужны initData. Или включи DEV_MODE.
          </div>
        </div>
      ) : null}

      <div className="px-5">
        <div className="grid grid-cols-2 gap-3">
          <Tile
            label="Вес"
            value={formatMass(data?.weight.value, mass)}
            delta={formatMassDelta(data?.weight.delta_7d, mass)}
            deltaTone={tone(data?.weight.delta_7d, true)}
            hint="за 7 дней"
            icon={<Scale className="w-4 h-4" />}
          />
          <Tile
            label="Жир"
            value={formatPercent(data?.fat_percent.value)}
            delta={formatPercentDelta(data?.fat_percent.delta_7d)}
            deltaTone={tone(data?.fat_percent.delta_7d, true)}
            hint="за 7 дней"
            icon={<Flame className="w-4 h-4" />}
          />
          <Tile
            label="Мышцы (SMM)"
            value={formatMass(data?.muscle.value, mass)}
            delta={formatMassDelta(data?.muscle.delta_30d, mass)}
            deltaTone={tone(data?.muscle.delta_30d)}
            hint="за 30 дней"
            icon={<Activity className="w-4 h-4" />}
          />
          <Tile
            label="Серия недель"
            value={String(data?.streak_weeks ?? 0)}
            hint="подряд ≥ 3 трен/нед"
            icon={<Trophy className="w-4 h-4" />}
          />
        </div>

        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.05, type: "spring", stiffness: 240, damping: 24 }}
          className="surface rounded-3xl p-5 mt-4 flex items-start gap-3"
        >
          <Sparkles className="w-5 h-5 text-accent shrink-0 mt-0.5" />
          <div className="flex-1 space-y-1.5">
            <h3 className="font-medium text-sm">Что говорят цифры</h3>
            <p className="text-sm text-muted leading-relaxed">{advice}</p>
          </div>
        </motion.div>

        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.1, type: "spring", stiffness: 240, damping: 24 }}
          className="surface rounded-3xl p-5 mt-4"
        >
          <div className="flex items-center justify-between mb-3">
            <div>
              <h3 className="font-medium">Динамика</h3>
              <p className="text-muted text-xs">последние 60 дней</p>
            </div>
            <TrendingUp className="w-4 h-4 text-muted" />
          </div>
          <div className="grid grid-cols-3 gap-3">
            <MiniChart label="Вес" values={weightSeries} stroke="rgb(var(--accent))" />
            <MiniChart label="Жир" values={fatSeries} stroke="rgb(var(--danger))" />
            <MiniChart label="Мышцы" values={muscleSeries} stroke="rgb(var(--warn))" />
          </div>
        </motion.div>

        <div className="grid grid-cols-2 gap-3 mt-4">
          <Tile
            label="Тренировки 7д"
            value={String(data?.workouts_7d ?? 0)}
            hint={`за 30д: ${data?.workouts_30d ?? 0}`}
          />
          <Tile
            label="Тоннаж 7д"
            value={`${Math.round(data?.tonnage_7d ?? 0).toLocaleString("ru-RU")} кг`}
          />
        </div>
      </div>
    </div>
  );
}

function MiniChart({ label, values, stroke }: { label: string; values: number[]; stroke: string }) {
  return (
    <div className="surface-2 rounded-2xl p-3">
      <div className="text-muted text-xs mb-2">{label}</div>
      <div style={{ color: stroke }}>
        <Sparkline values={values} width={88} height={32} stroke={stroke} fill={stroke} />
      </div>
    </div>
  );
}

function buildAdvice(d: DashboardData | null): string {
  if (!d || d.weight.value == null) {
    return "Добавь первый замер InBody через бота, и здесь появится интерпретация — динамика веса, жира, мышц и темп прогресса.";
  }
  const parts: string[] = [];
  const w7 = d.weight.delta_7d;
  const fat7 = d.fat_percent.delta_7d;
  const mus30 = d.muscle.delta_30d;

  if (w7 != null) {
    if (w7 < -1) parts.push("Темп снижения веса агрессивный — следи, чтобы не уходили мышцы.");
    else if (w7 < 0) parts.push("Вес идёт вниз спокойно, дефицит контролируемый.");
    else if (w7 > 1) parts.push("Прирост веса быстрый — проверь, не растёт ли заодно жир.");
    else if (w7 > 0) parts.push("Лёгкий профицит — ОК для медленного набора.");
    else parts.push("Вес стоит — проверь калории и активность.");
  }
  if (fat7 != null && fat7 < 0) parts.push("Жир снижается, фигура подсыхает.");
  if (mus30 != null && mus30 > 0.3) parts.push("Мышечная масса растёт — отличный знак.");
  if (d.streak_weeks >= 3) parts.push(`Дисциплина: ${d.streak_weeks} нед подряд ≥3 трен/нед.`);
  if (parts.length === 0) parts.push("Стабильно. Продолжай в том же режиме и добавь свежий замер на этой неделе.");
  return parts.join(" ");
}
