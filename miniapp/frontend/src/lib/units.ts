/** Conversion + formatting helpers. БД хранит метрику, тут только отображение. */

export type MassUnit = "kg" | "lb";
export type LengthUnit = "cm" | "ft";

const KG_TO_LB = 2.2046226218;
const CM_TO_IN = 0.3937007874;

export const massFromKg = (kg: number, unit: MassUnit): number =>
  unit === "kg" ? kg : kg * KG_TO_LB;

export const massToKg = (value: number, unit: MassUnit): number =>
  unit === "kg" ? value : value / KG_TO_LB;

export const lengthFromCm = (cm: number, unit: LengthUnit) => {
  if (unit === "cm") return { value: cm, unit: "см" };
  const totalInches = cm * CM_TO_IN;
  const ft = Math.floor(totalInches / 12);
  const inches = Math.round(totalInches - ft * 12);
  return { value: ft, unit: "ft", inches };
};

export const formatMass = (kg: number | null | undefined, unit: MassUnit, digits = 1) => {
  if (kg == null) return "—";
  const v = massFromKg(kg, unit);
  return `${v.toFixed(digits)} ${unit === "kg" ? "кг" : "lb"}`;
};

export const formatMassDelta = (kgDelta: number | null | undefined, unit: MassUnit, digits = 1) => {
  if (kgDelta == null) return null;
  const v = massFromKg(kgDelta, unit);
  const sign = v > 0 ? "+" : v < 0 ? "−" : "";
  return `${sign}${Math.abs(v).toFixed(digits)} ${unit === "kg" ? "кг" : "lb"}`;
};

export const formatHeight = (cm: number | null | undefined, unit: LengthUnit) => {
  if (cm == null) return "—";
  const r = lengthFromCm(cm, unit);
  if (r.unit === "см") return `${r.value} см`;
  return `${r.value}′${r.inches ?? 0}″`;
};

export const formatPercent = (p: number | null | undefined, digits = 1) =>
  p == null ? "—" : `${p.toFixed(digits)} %`;

export const formatPercentDelta = (p: number | null | undefined, digits = 1) => {
  if (p == null) return null;
  const sign = p > 0 ? "+" : p < 0 ? "−" : "";
  return `${sign}${Math.abs(p).toFixed(digits)} %`;
};
