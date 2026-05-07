/** Тонкая обёртка над window.Telegram.WebApp с DEV fallback. */

type ThemeParams = {
  bg_color?: string;
  text_color?: string;
  hint_color?: string;
  button_color?: string;
};

type WebApp = {
  initData?: string;
  initDataUnsafe?: { user?: { id?: number; first_name?: string; username?: string } };
  colorScheme?: "light" | "dark";
  themeParams?: ThemeParams;
  ready?: () => void;
  expand?: () => void;
  setHeaderColor?: (c: string) => void;
  setBackgroundColor?: (c: string) => void;
  HapticFeedback?: { impactOccurred: (s: "light" | "medium" | "heavy" | "rigid" | "soft") => void };
};

declare global {
  interface Window {
    Telegram?: { WebApp?: WebApp };
  }
}

export const tg = (): WebApp | null => window.Telegram?.WebApp ?? null;

export const isInTelegram = (): boolean => Boolean(tg()?.initData);

export const getInitData = (): string | null => tg()?.initData || null;

export const getDevUserId = (): number | null => {
  const v = localStorage.getItem("dev_user_id");
  return v ? Number(v) : null;
};

export const setDevUserId = (id: number | null) => {
  if (id == null) localStorage.removeItem("dev_user_id");
  else localStorage.setItem("dev_user_id", String(id));
};

export const tgUserName = (): string | null =>
  tg()?.initDataUnsafe?.user?.first_name || tg()?.initDataUnsafe?.user?.username || null;

export const haptic = (kind: "light" | "medium" | "heavy" = "light") => {
  try {
    tg()?.HapticFeedback?.impactOccurred(kind);
  } catch {
    /* noop */
  }
};

export const initTelegram = () => {
  const w = tg();
  if (!w) return;
  try {
    w.ready?.();
    w.expand?.();
  } catch {
    /* noop */
  }
};
