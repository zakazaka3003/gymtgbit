"""Pydantic-схемы для API."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class ProfileOut(BaseModel):
    sex: Optional[str] = None
    birth_date: Optional[str] = None
    height_cm: Optional[int] = None
    level: Optional[str] = None
    goal: Optional[str] = None
    units: str = "kg"


class ProfilePatch(BaseModel):
    sex: Optional[str] = Field(default=None, pattern="^[МЖ]$")
    birth_date: Optional[str] = None
    height_cm: Optional[int] = Field(default=None, ge=120, le=230)
    level: Optional[str] = None
    goal: Optional[str] = None
    units: Optional[str] = Field(default=None, pattern="^(kg|lb)$")


class InBodyPoint(BaseModel):
    record_date: str
    weight_kg: Optional[float] = None
    pbf_percent: Optional[float] = None
    smm_kg: Optional[float] = None


class InBodyCreate(BaseModel):
    record_date: Optional[str] = None
    weight_kg: Optional[float] = Field(default=None, ge=20, le=400)
    pbf_percent: Optional[float] = Field(default=None, ge=2, le=70)
    smm_kg: Optional[float] = Field(default=None, ge=5, le=120)


class DashboardMetric(BaseModel):
    value: Optional[float] = None
    delta_7d: Optional[float] = None
    delta_30d: Optional[float] = None


class DashboardOut(BaseModel):
    last_record_date: Optional[str] = None
    days_since_last: Optional[int] = None
    weight: DashboardMetric
    fat_percent: DashboardMetric
    muscle: DashboardMetric
    workouts_7d: int
    workouts_30d: int
    tonnage_7d: float
    streak_weeks: int


class WorkoutOut(BaseModel):
    id: int
    workout_date: str
    group_name: str
    total_sets: int
    total_reps: int
    total_tonnage: float


class StrengthPR(BaseModel):
    exercise: str
    weight: float
    reps: int
    record_date: str
