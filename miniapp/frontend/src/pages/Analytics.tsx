import { useEffect, useMemo, useState } from "react";
import { motion } from "framer-motion";
import { Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { PageHeader } from "../components/PageHeader";
import { SegmentedControl } from "../components/SegmentedControl";
import { api } from "../lib/api";
import type { InBodyPoint } from "../lib/types";
import { useSettings } from "../store/settings";
import { massFromKg } from "../lib/units";

type Range = "7" | "30" | "90" | "365";
type Metric = "weight" | "fat" | "muscle";

const RANGES: { value: Range; label: string }[] = [
  { value: "7", label: "Неделя" },
  { value: "30", label: "Месяц" },
  { value: "90", label: "3 мес" },
  { value: "365", label: "Год" },
];

const METRICS: { value: Metric; label: string; color: string; key: keyof InBodyPoint; isMass: boolean }[] = [
  { value: "weight", label: "Вес", color: "rgb(var(--accent))", key: "weight_kg", isMass: true },
  { value: "fat", label: "Жир %", color: "rgb(var(--danger))", key: "pbf_percent", isMass: false },
  { value: "muscle", label: "Мышцы", color: "rgb(var(--warn))", key: "smm_kg", isMass: true },
];

export function AnalyticsPage() {
  const mass = useSettings((s) => s.mass);
  const [range, setRange] = useState<Range>("30");
  const [metric, setMetric] = useState<Metric>("weight");
  const [history, setHistory] = useState<InBodyPoint[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    api
      .getInBodyHistory(365)
      .then(setHistory)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, []);

  const filtered = useMemo(() => {
    const days = Number(range);
    const cutoff = new Date();
    cutoff.setDate(cutoff.getDate() - days);
    return history.filter((p) => new Date(p.record_date) >= cutoff);
  }, [history, range]);

  const meta = METRICS.find((m) => m.value === metric)!;
  const chartData = useMemo(() => {
    return filtered
      .map((p) => {
        const raw = p[meta.key] as number | null;
        if (raw == null) return null;
        const value = meta.isMass ? massFromKg(raw, mass) : raw;
        return { date: p.record_date, value: Number(value.toFixed(2)) };
      })
      .filter((x): x is { date: string; value: number } => x !== null);
  }, [filtered, meta, mass]);

  const stats = useMemo(() => {
    if (chartData.length < 1) return null;
    const first = chartData[0].value;
    const last = chartData[chartData.length - 1].value;
    const min = Math.min(...chartData.map((d) => d.value));
    const max = Math.max(...chartData.map((d) => d.value));
    return { first, last, min, max, delta: last - first };
  }, [chartData]);

  const unitLabel = meta.isMass ? (mass === "kg" ? "кг" : "lb") : "%";

  return (
    <div>
      <PageHeader title="Аналитика" subtitle="История замеров InBody" />

      <div className="px-5 mb-3 overflow-x-auto no-scrollbar">
        <SegmentedControl options={RANGES} value={range} onChange={setRange} />
      </div>

      <div className="px-5 mb-3 flex gap-2 overflow-x-auto no-scrollbar">
        {METRICS.map((m) => {
          const active = m.value === metric;
          return (
            <button
              key={m.value}
              type="button"
              onClick={() => setMetric(m.value)}
              className={`px-4 py-2 rounded-full text-sm font-medium border transition-colors ${
                active
                  ? "bg-accent text-bg border-transparent"
                  : "surface text-fg border-default"
              }`}
            >
              {m.label}
            </button>
          );
        })}
      </div>

      <div className="px-5">
        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ type: "spring", stiffness: 240, damping: 24 }}
          className="surface rounded-3xl p-4"
        >
          <div className="flex items-baseline justify-between mb-3">
            <div>
              <div className="text-muted text-xs">{meta.label}</div>
              <div className="text-2xl font-semibold font-mono">
                {stats ? `${stats.last.toFixed(1)} ${unitLabel}` : "—"}
              </div>
            </div>
            {stats ? (
              <div className="text-right">
                <div className="text-muted text-xs">за период</div>
                <div
                  className={`text-sm font-medium font-mono ${
                    stats.delta < 0 ? "text-accent" : stats.delta > 0 ? "text-danger" : "text-muted"
                  }`}
                >
                  {stats.delta > 0 ? "+" : ""}
                  {stats.delta.toFixed(1)} {unitLabel}
                </div>
              </div>
            ) : null}
          </div>

          <div className="h-48">
            {loading ? (
              <div className="h-full flex items-center justify-center text-muted text-sm">
                Загрузка…
              </div>
            ) : chartData.length < 2 ? (
              <div className="h-full flex items-center justify-center text-muted text-sm text-center px-6">
                Мало данных. Нужны минимум 2 замера в выбранном периоде.
              </div>
            ) : (
              <ResponsiveContainer>
                <AreaChart data={chartData} margin={{ top: 10, right: 8, left: 0, bottom: 0 }}>
                  <defs>
                    <linearGradient id="g1" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor={meta.color} stopOpacity={0.45} />
                      <stop offset="100%" stopColor={meta.color} stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <XAxis
                    dataKey="date"
                    stroke="rgb(var(--fg-muted))"
                    fontSize={10}
                    tickLine={false}
                    axisLine={false}
                    minTickGap={28}
                  />
                  <YAxis
                    stroke="rgb(var(--fg-muted))"
                    fontSize={10}
                    tickLine={false}
                    axisLine={false}
                    width={32}
                    domain={["auto", "auto"]}
                  />
                  <Tooltip
                    contentStyle={{
                      background: "rgb(var(--surface))",
                      border: "1px solid rgb(var(--border))",
                      borderRadius: 12,
                      fontSize: 12,
                    }}
                    labelStyle={{ color: "rgb(var(--fg-muted))" }}
                  />
                  <Area
                    type="monotone"
                    dataKey="value"
                    stroke={meta.color}
                    strokeWidth={2}
                    fill="url(#g1)"
                  />
                </AreaChart>
              </ResponsiveContainer>
            )}
          </div>
        </motion.div>

        {stats ? (
          <div className="grid grid-cols-3 gap-3 mt-3">
            <StatPill label="мин" value={`${stats.min.toFixed(1)} ${unitLabel}`} />
            <StatPill label="макс" value={`${stats.max.toFixed(1)} ${unitLabel}`} />
            <StatPill label="точек" value={String(chartData.length)} />
          </div>
        ) : null}

        {error ? (
          <div className="surface rounded-2xl p-4 mt-4 text-sm text-danger">{error}</div>
        ) : null}
      </div>
    </div>
  );
}

function StatPill({ label, value }: { label: string; value: string }) {
  return (
    <div className="surface rounded-2xl p-3">
      <div className="text-muted text-[10px] uppercase tracking-wider">{label}</div>
      <div className="font-mono text-sm font-semibold mt-1">{value}</div>
    </div>
  );
}
