/** Глобальные настройки клиента: тема + единицы. Сохраняются в localStorage. */
import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { LengthUnit, MassUnit } from "../lib/units";

export type Theme = "dark" | "light";

type SettingsState = {
  theme: Theme;
  mass: MassUnit;
  length: LengthUnit;
  setTheme: (t: Theme) => void;
  toggleTheme: () => void;
  setMass: (u: MassUnit) => void;
  setLength: (u: LengthUnit) => void;
};

export const useSettings = create<SettingsState>()(
  persist(
    (set) => ({
      theme: "dark",
      mass: "kg",
      length: "cm",
      setTheme: (theme) => set({ theme }),
      toggleTheme: () => set((s) => ({ theme: s.theme === "dark" ? "light" : "dark" })),
      setMass: (mass) => set({ mass }),
      setLength: (length) => set({ length }),
    }),
    {
      name: "gymbit:settings",
      version: 1,
    },
  ),
);
