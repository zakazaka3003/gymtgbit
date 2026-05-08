"""OCR для InBody-распечаток.

Рерайт под русские распечатки (InBody570 и т.п.):
- PaddleOCR с русской моделью (`lang="ru"`) — читает и кириллицу, и латиницу с цифрами.
- Используем bounding boxes: каждая надпись OCR — это (text, [x1,y1,x2,y2], conf).
  Числа сопоставляются с подписями **по координатам** (на той же строке, либо ближайшее справа/снизу).
- InBody-специфичные «якоря» с приоритетом:
    «Идеальный Вес N kg» → weight
    «Процентное содержание жира … N» → pbf
    «Масса скелетной мускулатуры … N» → smm
- Generic-fallback (по синонимам подписей) и numeric-fallback (диапазоны) — только если якоря не сработали.
- Только 2 preprocessing-варианта (orig + upscale×2 + sharpen) чтобы не тратить минуты.
"""

import os
import re
from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np

try:
    from paddleocr import PaddleOCR
except Exception:
    PaddleOCR = None


# ----------------------------------------------------------------------------
# OCR item
# ----------------------------------------------------------------------------
@dataclass
class OcrItem:
    text: str
    cx: float          # центр bbox по X
    cy: float          # центр bbox по Y
    x1: float
    y1: float
    x2: float
    y2: float
    conf: float

    @property
    def h(self) -> float:
        return max(1.0, self.y2 - self.y1)


_NUM_RE = re.compile(r"[-+]?\d{1,4}(?:[.,]\d{1,3})?")


def _to_float(s: str) -> Optional[float]:
    s = s.strip().replace(",", ".").replace(" ", "")
    try:
        return float(s)
    except Exception:
        return None


def _extract_numbers(text: str):
    out = []
    for m in _NUM_RE.finditer(text):
        v = _to_float(m.group(0))
        if v is not None:
            out.append(v)
    return out


# ----------------------------------------------------------------------------
# Preprocessing
# ----------------------------------------------------------------------------
def _deskew(img_bgr: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    thr = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    thr = 255 - thr

    coords = np.column_stack(np.where(thr > 0))
    if coords.shape[0] < 300:
        return img_bgr

    rect = cv2.minAreaRect(coords)
    angle = rect[-1]
    if angle < -45:
        angle = -(90 + angle)
    else:
        angle = -angle
    if abs(angle) < 0.3:
        return img_bgr

    h, w = img_bgr.shape[:2]
    M = cv2.getRotationMatrix2D((w // 2, h // 2), angle, 1.0)
    return cv2.warpAffine(img_bgr, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def _preprocess_variants(img_bgr: np.ndarray):
    """Только 2 варианта: оригинал + upscale×2 sharpen. Хватает для InBody, экономит ~50% времени."""
    base = _deskew(img_bgr)
    yield "orig", base

    h, w = base.shape[:2]
    if max(h, w) < 1800:
        up = cv2.resize(base, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
    else:
        up = base.copy()
    g = cv2.cvtColor(up, cv2.COLOR_BGR2GRAY)
    g = cv2.fastNlMeansDenoising(g, None, 10, 7, 21)
    g = cv2.createCLAHE(clipLimit=2.2, tileGridSize=(8, 8)).apply(g)
    sharpen_k = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])
    g = cv2.filter2D(g, -1, sharpen_k)
    yield "upscaled", cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)


# ----------------------------------------------------------------------------
# OCR runner
# ----------------------------------------------------------------------------
def _run_paddle(ocr, img_bgr: np.ndarray):
    """Возвращает list[OcrItem]."""
    try:
        res = ocr.ocr(img_bgr, cls=True)
    except Exception:
        return []
    items: list[OcrItem] = []
    if not res:
        return items
    for block in res:
        if not block:
            continue
        for entry in block:
            try:
                box = entry[0]   # [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]
                txt, conf = entry[1]
                xs = [p[0] for p in box]
                ys = [p[1] for p in box]
                items.append(OcrItem(
                    text=txt,
                    cx=float(sum(xs) / 4.0),
                    cy=float(sum(ys) / 4.0),
                    x1=float(min(xs)),
                    y1=float(min(ys)),
                    x2=float(max(xs)),
                    y2=float(max(ys)),
                    conf=float(conf),
                ))
            except Exception:
                continue
    return items


# ----------------------------------------------------------------------------
# Spatial matchers
# ----------------------------------------------------------------------------
def _same_row(a: OcrItem, b: OcrItem, tol_ratio: float = 0.5) -> bool:
    """Соседние items на одной строке: вертикальное перекрытие ≥ tol_ratio."""
    overlap = min(a.y2, b.y2) - max(a.y1, b.y1)
    return overlap > 0 and overlap / min(a.h, b.h) >= tol_ratio


def _find_first_number_in(item: OcrItem) -> Optional[float]:
    nums = _extract_numbers(item.text)
    return nums[0] if nums else None


def _find_value_for_label(label: OcrItem, items) -> Optional[float]:
    """
    Для подписи `label` ищем числовое значение:
      1) в самой подписи (вдруг распознали в одну строку)
      2) на той же строке справа от подписи (ближайший item с цифрой)
      3) ниже подписи в той же колонке (для 2-строчных карточек)
    """
    n = _find_first_number_in(label)
    if n is not None:
        return n

    same_row = [
        it for it in items
        if it is not label and _same_row(label, it) and it.cx > label.cx
    ]
    same_row.sort(key=lambda it: it.cx)
    for it in same_row:
        n = _find_first_number_in(it)
        if n is not None:
            return n

    label_w = max(1.0, label.x2 - label.x1)
    below = [
        it for it in items
        if it is not label
        and it.cy > label.y2
        and abs(it.cx - label.cx) < label_w
        and (it.cy - label.y2) < 4 * label.h
    ]
    below.sort(key=lambda it: it.cy)
    for it in below:
        n = _find_first_number_in(it)
        if n is not None:
            return n

    return None


# ----------------------------------------------------------------------------
# Anchored parsing (InBody-specific)
# ----------------------------------------------------------------------------
_LABEL_PATTERNS = {
    # высокий приоритет — точные InBody-подписи
    "weight_ideal":   re.compile(r"идеальн\w*\s*вес", re.IGNORECASE),
    "weight_control": re.compile(r"контрол\w*\s*вес", re.IGNORECASE),
    "pbf_percent":    re.compile(r"процентн\w*\s*содержани\w*\s*жира", re.IGNORECASE),
    "smm_full":       re.compile(r"масс\w*\s*скелетн\w*\s*мускулатур\w*", re.IGNORECASE),
    # generic fallback
    "weight_any":     re.compile(r"^\s*вес\s*\(?(kg|кг)?\)?\s*$", re.IGNORECASE),
}

_RANGE = {
    "weight_kg":   (30.0, 250.0),
    "smm_kg":      (10.0, 80.0),
    "pbf_percent": (3.0, 60.0),
}


def _in_range(metric: str, value: float) -> bool:
    lo, hi = _RANGE[metric]
    return lo <= value <= hi


def parse_inbody_items(items):
    """
    Возвращает (metrics, debug):
      metrics: dict с возможными ключами weight_kg, pbf_percent, smm_kg
      debug:   {metric: {"source": "anchor"|"generic", "label": str}}
    """
    metrics: dict[str, float] = {}
    debug: dict[str, dict] = {}

    def try_set(metric: str, value, source: str, label_text: str):
        if value is None:
            return
        if not _in_range(metric, float(value)):
            return
        if metric not in metrics:
            metrics[metric] = round(float(value), 1)
            debug[metric] = {"source": source, "label": label_text}

    # 1) Высокоприоритетные InBody-якоря
    for it in items:
        t = it.text
        if "weight_kg" not in metrics and _LABEL_PATTERNS["weight_ideal"].search(t):
            try_set("weight_kg", _find_value_for_label(it, items), "anchor", t)
        if "pbf_percent" not in metrics and _LABEL_PATTERNS["pbf_percent"].search(t):
            try_set("pbf_percent", _find_value_for_label(it, items), "anchor", t)
        if "smm_kg" not in metrics and _LABEL_PATTERNS["smm_full"].search(t):
            try_set("smm_kg", _find_value_for_label(it, items), "anchor", t)

    # 1b) «Контроль Веса» как запасной якорь — но только если идеального не было
    if "weight_kg" not in metrics:
        for it in items:
            if _LABEL_PATTERNS["weight_control"].search(it.text):
                v = _find_value_for_label(it, items)
                if v is not None and v >= 30.0:
                    try_set("weight_kg", v, "anchor", it.text)
                    break

    # 2) Generic — по коротким подписям «Вес»
    if "weight_kg" not in metrics:
        for it in items:
            if _LABEL_PATTERNS["weight_any"].search(it.text):
                try_set("weight_kg", _find_value_for_label(it, items), "generic", it.text)
                if "weight_kg" in metrics:
                    break

    return metrics, debug


# ----------------------------------------------------------------------------
# Main entry
# ----------------------------------------------------------------------------
_PADDLE_INSTANCE = None


def _get_paddle(use_gpu: bool = False):
    """Singleton — иначе каждый запуск инициализирует модели по 30+ сек."""
    global _PADDLE_INSTANCE
    if _PADDLE_INSTANCE is not None:
        return _PADDLE_INSTANCE
    if PaddleOCR is None:
        return None
    try:
        _PADDLE_INSTANCE = PaddleOCR(
            use_angle_cls=True,
            lang="ru",       # ← было "en", теперь читает кириллицу
            use_gpu=use_gpu,
            show_log=False,
        )
    except Exception:
        _PADDLE_INSTANCE = None
    return _PADDLE_INSTANCE


def run_inbody_ocr(image_path: str, use_gpu: bool = False) -> dict:
    if not os.path.exists(image_path):
        return {"ok": False, "error": "Файл не найден"}
    img_bgr = cv2.imread(image_path)
    if img_bgr is None:
        return {"ok": False, "error": "Не удалось прочитать изображение"}

    ocr = _get_paddle(use_gpu)
    if ocr is None:
        return {"ok": False, "error": "OCR-движок недоступен"}

    best = {
        "metrics": {},
        "items": [],
        "variant": "",
        "score": -1.0,
        "debug": {},
    }

    for name, imgv in _preprocess_variants(img_bgr):
        items = _run_paddle(ocr, imgv)
        if not items:
            continue
        metrics, dbg = parse_inbody_items(items)
        # score: 1 за метрику + 0.5 за anchor-источник
        score = float(sum(1 for k in ("weight_kg", "pbf_percent", "smm_kg") if k in metrics))
        score += sum(0.5 for v in dbg.values() if v.get("source") == "anchor")
        if score > best["score"]:
            best = {
                "metrics": metrics,
                "items": items,
                "variant": name,
                "score": score,
                "debug": dbg,
            }

    metrics = best["metrics"]
    # confidence: 33% за каждую найденную метрику + 4% бонус если она через anchor
    conf = 0.0
    for k in ("weight_kg", "pbf_percent", "smm_kg"):
        if k in metrics:
            conf += 0.33
            if best["debug"].get(k, {}).get("source") == "anchor":
                conf += 0.04
    conf = round(min(1.0, conf), 2)

    raw_text = "\n".join(it.text for it in best["items"])[:4000]
    return {
        "ok": True,
        "metrics": metrics,
        "confidence": conf,
        "raw_text": raw_text,
        "debug": {
            "variant": best["variant"],
            "score": best["score"],
            "engine": "paddle-ru",
            "matches": best["debug"],
        },
    }
