import { motion, AnimatePresence } from "framer-motion";
import { Activity, BarChart3, Dumbbell, User } from "lucide-react";
import type { ReactNode } from "react";
import { haptic } from "../lib/twa";

export type Tab = "dashboard" | "analytics" | "workouts" | "profile";

const TABS: { key: Tab; label: string; icon: typeof Activity }[] = [
  { key: "dashboard", label: "Состояние", icon: Activity },
  { key: "analytics", label: "Аналитика", icon: BarChart3 },
  { key: "workouts", label: "Тренировки", icon: Dumbbell },
  { key: "profile", label: "Профиль", icon: User },
];

type Props = {
  active: Tab;
  onChange: (t: Tab) => void;
  children: ReactNode;
};

export function AppShell({ active, onChange, children }: Props) {
  return (
    <div className="min-h-full flex flex-col">
      <main className="flex-1 pb-28">
        <AnimatePresence mode="wait">
          <motion.div
            key={active}
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -4 }}
            transition={{ duration: 0.18, ease: "easeOut" }}
          >
            {children}
          </motion.div>
        </AnimatePresence>
      </main>

      <nav className="fixed bottom-0 inset-x-0 safe-bottom z-50">
        <div className="mx-auto max-w-md px-3">
          <div className="surface rounded-3xl shadow-xl shadow-black/5 backdrop-blur-md flex items-stretch">
            {TABS.map((t) => {
              const Icon = t.icon;
              const isActive = active === t.key;
              return (
                <button
                  key={t.key}
                  type="button"
                  onClick={() => {
                    if (!isActive) haptic("light");
                    onChange(t.key);
                  }}
                  className="relative flex-1 flex flex-col items-center justify-center py-2.5 gap-1"
                >
                  {isActive ? (
                    <motion.span
                      layoutId="nav-active"
                      transition={{ type: "spring", stiffness: 500, damping: 36 }}
                      className="absolute inset-1 rounded-2xl surface-2"
                    />
                  ) : null}
                  <Icon
                    className={`relative z-10 w-5 h-5 ${isActive ? "text-fg" : "text-fg-muted"}`}
                    strokeWidth={2.2}
                  />
                  <span
                    className={`relative z-10 text-[10px] font-medium ${
                      isActive ? "text-fg" : "text-fg-muted"
                    }`}
                  >
                    {t.label}
                  </span>
                </button>
              );
            })}
          </div>
        </div>
      </nav>
    </div>
  );
}
