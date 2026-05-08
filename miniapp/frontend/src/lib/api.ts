/** API client. Прокидывает Telegram initData (и dev-id в DEV) в заголовках. */

import type {
  Profile,
  Dashboard,
  InBodyPoint,
  WorkoutItem,
  StrengthPR,
} from "./types";
import { getInitData, getDevUserId } from "./twa";

const BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, "") ?? "";

function buildHeaders(extra?: HeadersInit): HeadersInit {
  const init = getInitData();
  const dev = getDevUserId();
  const h = new Headers(extra);
  h.set("Content-Type", "application/json");
  if (init) h.set("X-Telegram-Init-Data", init);
  if (dev) h.set("X-Dev-User-Id", String(dev));
  return h;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: buildHeaders(init?.headers),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText} — ${text}`);
  }
  return (await res.json()) as T;
}

export const api = {
  getProfile: () => request<Profile>("/api/profile"),
  patchProfile: (patch: Partial<Profile>) =>
    request<Profile>("/api/profile", {
      method: "PATCH",
      body: JSON.stringify(patch),
    }),
  getDashboard: () => request<Dashboard>("/api/dashboard"),
  getInBodyHistory: (days = 365) => request<InBodyPoint[]>(`/api/inbody/history?days=${days}`),
  postInBody: (payload: {
    record_date: string;
    weight_kg?: number;
    pbf_percent?: number;
    smm_kg?: number;
    bfm_kg?: number;
  }) =>
    request<InBodyPoint>("/api/inbody", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  getWorkouts: (days = 60) => request<WorkoutItem[]>(`/api/workouts?days=${days}`),
  getStrengthPRs: () => request<StrengthPR[]>("/api/strength/prs"),
  seedDemo: () => request<{ ok: boolean }>("/api/_demo/seed", { method: "POST" }),
};
