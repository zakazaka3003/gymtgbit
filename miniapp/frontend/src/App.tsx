import { useEffect, useState } from "react";
import { AppShell, type Tab } from "./components/AppShell";
import { DashboardPage } from "./pages/Dashboard";
import { AnalyticsPage } from "./pages/Analytics";
import { WorkoutsPage } from "./pages/Workouts";
import { ProfilePage } from "./pages/Profile";
import { useSettings } from "./store/settings";
import { initTelegram, tg } from "./lib/twa";

function applyThemeClass(theme: "dark" | "light") {
  const root = document.documentElement;
  root.classList.toggle("dark", theme === "dark");
  const w = tg();
  const bg = theme === "dark" ? "#09090b" : "#fafafb";
  try {
    w?.setHeaderColor?.(bg);
    w?.setBackgroundColor?.(bg);
  } catch {
    /* noop */
  }
}

export default function App() {
  const theme = useSettings((s) => s.theme);
  const [tab, setTab] = useState<Tab>("dashboard");

  useEffect(() => {
    initTelegram();
  }, []);

  useEffect(() => {
    applyThemeClass(theme);
  }, [theme]);

  return (
    <div className="min-h-full bg-bg text-fg">
      <AppShell active={tab} onChange={setTab}>
        {tab === "dashboard" ? <DashboardPage /> : null}
        {tab === "analytics" ? <AnalyticsPage /> : null}
        {tab === "workouts" ? <WorkoutsPage /> : null}
        {tab === "profile" ? <ProfilePage /> : null}
      </AppShell>
    </div>
  );
}
