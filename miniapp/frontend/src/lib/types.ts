export type Profile = {
  sex: "М" | "Ж" | null;
  birth_date: string | null;
  height_cm: number | null;
  level: string | null;
  goal: string | null;
  units: "kg" | "lb";
};

export type DashboardMetric = {
  value: number | null;
  delta_7d: number | null;
  delta_30d: number | null;
};

export type Dashboard = {
  last_record_date: string | null;
  days_since_last: number | null;
  weight: DashboardMetric;
  fat_percent: DashboardMetric;
  muscle: DashboardMetric;
  workouts_7d: number;
  workouts_30d: number;
  tonnage_7d: number;
  streak_weeks: number;
};

export type InBodyPoint = {
  record_date: string;
  weight_kg: number | null;
  pbf_percent: number | null;
  smm_kg: number | null;
};

export type WorkoutItem = {
  id: number;
  workout_date: string;
  group_name: string;
  total_sets: number;
  total_reps: number;
  total_tonnage: number;
};

export type StrengthPR = {
  exercise: string;
  weight: number;
  reps: number;
  record_date: string;
};
