"""OCR для InBody-распечаток.

После тестов на реальных фото InBody570:
- PaddleOCR русский/мультиязычный — выдаёт мусор на печатных мелких отчётах ("сэдржнэ" вместо "содержание").
- Tesseract с пакетом `tesseract-ocr-rus` справляется НАМНОГО лучше — печатный текст это его конёк.

Поэтому стратегия:
- Основной движок — **Tesseract** (`image_to_data`, чтобы получить bbox для каждого слова).
- Несколько preprocessing-вариантов; для каждого собираем `OcrItem`-ы (text + bbox).
- Парсинг через якоря (Идеальный Вес, Процентное содержание жира, Масса скелетной мускулатуры,
  плюс типичные "(kg)"/"(%)" подписи рядом со значениями).
- В качестве fallback можно гонять PaddleOCR, но в большинстве случаев он только мешает.
"""

import os
import re
from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np

try:
    import pytesseract
except Exception:
    pytesseract = None

try:
    from PIL import Image
except Exception:
    Image = None


# ----------------------------------------------------------------------------
# OCR item
# ----------------------------------------------------------------------------
@dataclass
class OcrItem:
    text: str
    cx: float
    cy: float
    x1: float
    y1: float
    x2: float
    y2: float
    conf: float

    @property
    def h(self) -> float:
        return max(1.0, self.y2 - self.y1)


# Допускаем пробел между разрядами и точкой/запятой («79. 3», «79 ,3»).
_NUM_RE = re.compile(r"[-+]?\d{1,4}(?:\s*[.,]\s*\d{1,3})?")


def _to_float(s: str) -> Optional[float]:
    s = s.strip().replace(" ", "").replace(",", ".")
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
    """Готовим 2 варианта: (a) оригинал в RGB и (b) upscale×2 + denoise + CLAHE."""
    base = _deskew(img_bgr)

    # 1) original RGB (PIL ждёт RGB)
    yield "orig", cv2.cvtColor(base, cv2.COLOR_BGR2RGB)

    # 2) upscaled + denoised + CLAHE
    h, w = base.shape[:2]
    up = cv2.resize(base, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC) if max(h, w) < 1800 else base.copy()
    g = cv2.cvtColor(up, cv2.COLOR_BGR2GRAY)
    g = cv2.fastNlMeansDenoising(g, None, 10, 7, 21)
    g = cv2.createCLAHE(clipLimit=2.2, tileGridSize=(8, 8)).apply(g)
    yield "upscaled", g  # одноканальный grayscale, PIL это переварит


# ----------------------------------------------------------------------------
# Tesseract → OcrItem[]
# ----------------------------------------------------------------------------
def _tesseract_items(img_for_pil) -> list:
    """Возвращает список OcrItem на основе `pytesseract.image_to_data`."""
    if pytesseract is None or Image is None:
        return []
    pil = Image.fromarray(img_for_pil)
    config = "--oem 3 --psm 6"
    try:
        data = pytesseract.image_to_data(
            pil,
            lang="rus+eng",
            config=config,
            output_type=pytesseract.Output.DICT,
        )
    except Exception:
        try:
            data = pytesseract.image_to_data(
                pil, lang="eng", config=config, output_type=pytesseract.Output.DICT
            )
        except Exception:
            return []

    items: list = []
    n = len(data.get("text", []))
    for i in range(n):
        txt = (data["text"][i] or "").strip()
        if not txt:
            continue
        try:
            x = float(data["left"][i])
            y = float(data["top"][i])
            w = float(data["width"][i])
            h = float(data["height"][i])
            conf = float(data["conf"][i]) / 100.0
        except Exception:
            continue
        items.append(OcrItem(
            text=txt,
            cx=x + w / 2,
            cy=y + h / 2,
            x1=x,
            y1=y,
            x2=x + w,
            y2=y + h,
            conf=max(0.0, conf),
        ))

    # Tesseract бьёт текст по словам — склеим соседей, чтобы '(kg)' или '79.3' не разваливались.
    # Соседями считаем те, что в одной строке и расстояние < ширина одного символа.
    return _merge_neighbors(items)


def _merge_neighbors(items: list) -> list:
    if not items:
        return []
    items_sorted = sorted(items, key=lambda it: (it.cy, it.cx))
    merged: list = []
    for it in items_sorted:
        if merged:
            prev = merged[-1]
            same_row = (
                min(prev.y2, it.y2) - max(prev.y1, it.y1)
            ) > 0.5 * min(prev.h, it.h)
            close = (it.x1 - prev.x2) < 0.6 * max(prev.h, it.h)
            if same_row and close:
                # склеиваем
                prev.text = (prev.text + " " + it.text).strip()
                prev.x2 = max(prev.x2, it.x2)
                prev.y1 = min(prev.y1, it.y1)
                prev.y2 = max(prev.y2, it.y2)
                prev.cx = (prev.x1 + prev.x2) / 2
                prev.cy = (prev.y1 + prev.y2) / 2
                prev.conf = min(prev.conf, it.conf)
                continue
        # новая «фраза»
        merged.append(OcrItem(
            text=it.text, cx=it.cx, cy=it.cy,
            x1=it.x1, y1=it.y1, x2=it.x2, y2=it.y2, conf=it.conf,
        ))
    return merged


# ----------------------------------------------------------------------------
# Spatial helpers
# ----------------------------------------------------------------------------
def _same_row(a: OcrItem, b: OcrItem, tol_ratio: float = 0.5) -> bool:
    overlap = min(a.y2, b.y2) - max(a.y1, b.y1)
    return overlap > 0 and overlap / min(a.h, b.h) >= tol_ratio


def _first_num_in(item: OcrItem) -> Optional[float]:
    nums = _extract_numbers(item.text)
    return nums[0] if nums else None


def _value_for_label(label: OcrItem, items, max_dx_chars: float = 25) -> Optional[float]:
    n = _first_num_in(label)
    if n is not None:
        return n

    # на той же строке справа — берём первый item с цифрой
    same_row = sorted(
        (it for it in items if it is not label and _same_row(label, it) and it.cx > label.cx),
        key=lambda it: it.cx,
    )
    for it in same_row:
        n = _first_num_in(it)
        if n is not None:
            return n

    # ниже под подписью (для двухстрочных карточек) — но не очень далеко
    label_w = max(1.0, label.x2 - label.x1)
    below = sorted(
        (
            it for it in items
            if it is not label
            and it.cy > label.y2
            and abs(it.cx - label.cx) < label_w
            and (it.cy - label.y2) < 4 * label.h
        ),
        key=lambda it: it.cy,
    )
    for it in below:
        n = _first_num_in(it)
        if n is not None:
            return n

    return None


# ----------------------------------------------------------------------------
# Parser
# ----------------------------------------------------------------------------
# фази-паттерны: Tesseract иногда даёт мусор в одной-двух буквах,
# поэтому проверяем по подстрокам/корням.
_LABEL_PATTERNS = {
    # явные InBody-якоря (надёжнее всего)
    "ideal_weight":   re.compile(r"идеальн\w*\s*[вbBв]ес", re.IGNORECASE),
    "pbf_full":       re.compile(r"процент\w*\s*содерж\w*\s*жир", re.IGNORECASE),
    "smm_full":       re.compile(r"(масса|массы)\s+скелетн", re.IGNORECASE),
    "weight_control": re.compile(r"контрол\w*\s*вес", re.IGNORECASE),
    # generic
    "weight_short":   re.compile(r"^\s*(вес|bес)\s*\(?\s*kg\s*\)?\s*$", re.IGNORECASE),
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
    metrics: dict = {}
    debug: dict = {}

    def try_set(metric: str, value, source: str, label_text: str):
        if value is None:
            return
        if not _in_range(metric, float(value)):
            return
        if metric not in metrics:
            metrics[metric] = round(float(value), 1)
            debug[metric] = {"source": source, "label": label_text}

    # 1) Самый чёткий якорь — "Идеальный Вес 79.3 kg" (правый верхний блок отчёта)
    for it in items:
        if "weight_kg" not in metrics and _LABEL_PATTERNS["ideal_weight"].search(it.text):
            try_set("weight_kg", _value_for_label(it, items), "anchor:ideal", it.text)
        if "pbf_percent" not in metrics and _LABEL_PATTERNS["pbf_full"].search(it.text):
            try_set("pbf_percent", _value_for_label(it, items), "anchor:pbf_full", it.text)
        if "smm_kg" not in metrics and _LABEL_PATTERNS["smm_full"].search(it.text):
            try_set("smm_kg", _value_for_label(it, items), "anchor:smm_full", it.text)

    # 2) Запасной — "Контроль Веса" (но это часто 0.0; отбрасываем малые числа)
    if "weight_kg" not in metrics:
        for it in items:
            if _LABEL_PATTERNS["weight_control"].search(it.text):
                v = _value_for_label(it, items)
                if v is not None and v >= 30.0:
                    try_set("weight_kg", v, "anchor:control", it.text)
                    break

    # 3) Generic «Вес (kg)» — берём правое значение
    if "weight_kg" not in metrics:
        for it in items:
            if _LABEL_PATTERNS["weight_short"].search(it.text):
                try_set("weight_kg", _value_for_label(it, items), "generic:weight_short", it.text)
                if "weight_kg" in metrics:
                    break

    return metrics, debug


# ----------------------------------------------------------------------------
# Main entry
# ----------------------------------------------------------------------------
def run_inbody_ocr(image_path: str, use_gpu: bool = False) -> dict:
    if not os.path.exists(image_path):
        return {"ok": False, "error": "Файл не найден"}
    img_bgr = cv2.imread(image_path)
    if img_bgr is None:
        return {"ok": False, "error": "Не удалось прочитать изображение"}
    if pytesseract is None:
        return {"ok": False, "error": "Tesseract не установлен"}

    best = {
        "metrics": {},
        "items": [],
        "variant": "",
        "score": -1.0,
        "debug": {},
    }

    for name, imgv in _preprocess_variants(img_bgr):
        items = _tesseract_items(imgv)
        if not items:
            continue
        metrics, dbg = parse_inbody_items(items)
        # 1 за каждую метрику, +0.5 за anchor-источник
        score = float(sum(1 for k in ("weight_kg", "pbf_percent", "smm_kg") if k in metrics))
        score += sum(
            0.5 for v in dbg.values()
            if isinstance(v, dict) and str(v.get("source", "")).startswith("anchor")
        )
        if score > best["score"]:
            best = {
                "metrics": metrics,
                "items": items,
                "variant": name,
                "score": score,
                "debug": dbg,
            }

    metrics = best["metrics"]
    conf = 0.0
    for k in ("weight_kg", "pbf_percent", "smm_kg"):
        if k in metrics:
            conf += 0.33
            src = best["debug"].get(k, {}).get("source", "")
            if str(src).startswith("anchor"):
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
            "engine": "tesseract-rus+eng",
            "matches": best["debug"],
        },
    }
