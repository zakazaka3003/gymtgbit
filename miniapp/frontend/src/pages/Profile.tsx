import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { Moon, Ruler, Sun, Target, User as UserIcon, Weight } from "lucide-react";
import { PageHeader } from "../components/PageHeader";
import { SegmentedControl } from "../components/SegmentedControl";
import { api } from "../lib/api";
import type { Profile } from "../lib/types";
import { useSettings } from "../store/settings";
import { formatHeight, formatMass } from "../lib/units";
import { tgUserName } from "../lib/twa";

const GOAL_LABELS: Record<string, string> = {
  cut: "Сушка",
  bulk: "Масса",
  recomp: "Рекомпозиция",
  maintain: "Поддержание",
};

const LEVEL_LABELS: Record<string, string> = {
  beginner: "Начинающий",
  intermediate: "Средний",
  advanced: "Продвинутый",
};

export function ProfilePage() {
  const { theme, mass, length, toggleTheme, setMass, setLength } = useSettings();
  const [profile, setProfile] = useState<Profile | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .getProfile()
      .then(setProfile)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  const name = tgUserName();
  const ageStr = profile?.birth_date ? calcAge(profile.birth_date) + " лет" : null;

  return (
    <div>
      <PageHeader title="Профиль" subtitle={name ? `@${name}` : undefined} />

      <div className="px-5 space-y-4">
        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ type: "spring", stiffness: 240, damping: 24 }}
          className="surface rounded-3xl p-5"
        >
          <div className="flex items-start gap-4">
            <div className="w-14 h-14 rounded-full surface-2 flex items-center justify-center shrink-0">
              <UserIcon className="w-6 h-6 text-muted" />
            </div>
            <div className="flex-1 min-w-0">
              <div className="font-medium truncate">{name ?? "Без имени"}</div>
              <div className="text-muted text-sm mt-0.5">
                {[profile?.sex, ageStr].filter(Boolean).join(" • ") || "Профиль не заполнен"}
              </div>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3 mt-4">
            <InfoRow label="Рост" value={formatHeight(profile?.height_cm, length === "cm" ? "cm" : "ft")} />
            <InfoRow
              label="Цель"
              value={profile?.goal ? GOAL_LABELS[profile.goal] ?? profile.goal : "—"}
            />
            <InfoRow
              label="Уровень"
              value={profile?.level ? LEVEL_LABELS[profile.level] ?? profile.level : "—"}
            />
            <InfoRow label="Единицы" value={mass === "kg" ? "Метрика" : "Имперская"} />
          </div>

          {profile == null && !error ? (
            <div className="text-muted text-xs mt-3">Загружаем…</div>
          ) : null}
          {error ? (
            <div className="text-danger text-xs mt-3">{error}</div>
          ) : null}
        </motion.div>

        <SectionCard
          icon={<Sun className="w-4 h-4" />}
          title="Тема"
          subtitle="Сохраняется на устройстве"
          right={
            <button
              type="button"
              onClick={toggleTheme}
              className="surface-2 rounded-full px-4 py-2 text-sm font-medium flex items-center gap-2"
            >
              {theme === "dark" ? <Moon className="w-4 h-4" /> : <Sun className="w-4 h-4" />}
              {theme === "dark" ? "Тёмная" : "Светлая"}
            </button>
          }
        />

        <SectionCard
          icon={<Weight className="w-4 h-4" />}
          title="Масса"
          subtitle={`Сейчас: ${mass === "kg" ? "килограммы" : "фунты"}`}
          right={
            <SegmentedControl
              size="sm"
              options={[
                { value: "kg", label: "кг" },
                { value: "lb", label: "lb" },
              ]}
              value={mass}
              onChange={setMass}
            />
          }
        />

        <SectionCard
          icon={<Ruler className="w-4 h-4" />}
          title="Рост"
          subtitle={`Сейчас: ${length === "cm" ? "сантиметры" : "футы/дюймы"}`}
          right={
            <SegmentedControl
              size="sm"
              options={[
                { value: "cm", label: "см" },
                { value: "ft", label: "ft" },
              ]}
              value={length}
              onChange={setLength}
            />
          }
        />

        <SectionCard
          icon={<Target className="w-4 h-4" />}
          title="Что приходит из бота"
          subtitle="Меняй цели и параметры через @bot — экран обновится автоматически"
        />

        <div className="text-center text-muted text-xs py-2">
          {formatMass(78.4, mass, 1)} • {formatHeight(180, length)} — пример отображения
        </div>
      </div>
    </div>
  );
}

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="surface-2 rounded-2xl px-3 py-2.5">
      <div className="text-muted text-[10px] uppercase tracking-wider">{label}</div>
      <div className="font-medium text-sm mt-0.5 truncate">{value}</div>
    </div>
  );
}

function SectionCard({
  icon,
  title,
  subtitle,
  right,
}: {
  icon: React.ReactNode;
  title: string;
  subtitle?: string;
  right?: React.ReactNode;
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2 }}
      className="surface rounded-3xl p-4 flex items-center justify-between gap-3"
    >
      <div className="flex items-start gap-3 min-w-0">
        <div className="w-9 h-9 rounded-2xl surface-2 flex items-center justify-center text-muted shrink-0">
          {icon}
        </div>
        <div className="min-w-0">
          <div className="font-medium truncate">{title}</div>
          {subtitle ? <div className="text-muted text-xs mt-0.5 truncate">{subtitle}</div> : null}
        </div>
      </div>
      {right ? <div className="shrink-0">{right}</div> : null}
    </motion.div>
  );
}

function calcAge(birth: string): number {
  const b = new Date(birth);
  const now = new Date();
  let age = now.getFullYear() - b.getFullYear();
  const m = now.getMonth() - b.getMonth();
  if (m < 0 || (m === 0 && now.getDate() < b.getDate())) age--;
  return age;
}
