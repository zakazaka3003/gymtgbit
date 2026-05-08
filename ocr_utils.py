"""OCR для InBody-распечаток.

Подход после практических замеров:
- PaddleOCR (ru/multi) на печатных мелких отчётах InBody570 даёт мусор.
- Tesseract `image_to_data` тоже плохо: резко режет уверенность и фрагментирует.
- Tesseract `image_to_string` с `lang="rus+eng"` и `--psm 6/4` — единственный, что даёт
  читаемый текст с числами 79.3, 41.7, 7.4 на той же строке, что и подписи.

Стратегия:
1) 2 preprocessing-варианта (orig RGB + upscale×2 + denoise + CLAHE + sharpen).
2) Для каждого варианта берём 1-2 PSM-режима, прогоняем `image_to_string`.
3) Парсим **построчно**: ищем строки со знакомыми подписями («Идеальный Вес», «Процентное …
   жира», «Масса скелетной …», плюс короткие "Вес (kg)" в History) — на той же строке
   достаём число.
4) Защиты:
   • очень нестрогая нормализация подписи (Tesseract путает кириллицу/латиницу: «Bec»/«Вес»);
   • число «79. 3» с пробелом нормализуется в 79.3;
   • валидные диапазоны (вес 30..250, SMM 10..80, PBF 3..60) — отсеиваем шум;
   • уверенность считается от количества якорно-найденных метрик, а не «100% если хоть что-то нашли».
"""

import os
import re
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
# Numbers
# ----------------------------------------------------------------------------
# Допускаем «79. 3», «79 ,3», «79.3», «79,3».
_NUM_RE = re.compile(r"\d{1,4}(?:\s*[.,]\s*\d{1,3})?")
# Только дробные числа — игнорируем целые tick-метки на bar-chart.
_DEC_NUM_RE = re.compile(r"\d{1,3}\s*[.,]\s*\d{1,2}")


def _to_float(s: str) -> Optional[float]:
    s = s.strip().replace(" ", "").replace(",", ".")
    try:
        return float(s)
    except Exception:
        return None


def _numbers_in(s: str):
    return [v for v in (_to_float(m.group(0)) for m in _NUM_RE.finditer(s)) if v is not None]


def _decimals_in(s: str):
    return [v for v in (_to_float(m.group(0)) for m in _DEC_NUM_RE.finditer(s)) if v is not None]


# Tesseract на печатной кириллице регулярно подсовывает латиницу-двойник
# (M, a, c, e, o, p, x, y, B, H, K, T, ...). Делаем безопасный мапинг
# для матчинга подписей: исходный текст в логи мы храним, для regex —
# нормализованный.
_LAT2CYR = str.maketrans({
    "M": "М", "a": "а", "c": "с", "e": "е", "o": "о", "p": "р",
    "x": "х", "y": "у", "B": "В", "H": "Н", "K": "К", "T": "Т",
    "P": "Р", "C": "С", "A": "А", "E": "Е", "O": "О", "X": "Х", "Y": "У",
})


def _normalize_cyr(s: str) -> str:
    return s.translate(_LAT2CYR)


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
    """Готовим 2 варианта изображения для Tesseract.

    ВАЖНО: НЕ ДЕЛАЕМ deskew. На реальных InBody-фото он чаще всего ломает OCR
    (после нашего теста — превращает чистый текст в мусор), потому что ищет
    угол по плотным колонкам/строкам таблицы и крутит на ~ доли градуса.
    Печатные отчёты приходят выровненными, и Tesseract сам толерантен к небольшим
    отклонениям.
    """
    # 1) raw RGB (PIL ждёт RGB-каналы)
    yield "orig", cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)

    # 2) upscale ×2 без агрессивных фильтров — иногда помогает на низком DPI.
    h, w = img_bgr.shape[:2]
    if max(h, w) < 1800:
        up = cv2.resize(img_bgr, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
        yield "upscaled", cv2.cvtColor(up, cv2.COLOR_BGR2RGB)


# ----------------------------------------------------------------------------
# OCR text via Tesseract
# ----------------------------------------------------------------------------
def _tesseract_text(img_for_pil, psm: int = 6) -> str:
    if pytesseract is None or Image is None:
        return ""
    pil = Image.fromarray(img_for_pil)
    cfg = f"--oem 3 --psm {psm}"
    for lang in ("rus+eng", "eng"):
        try:
            return pytesseract.image_to_string(pil, lang=lang, config=cfg)
        except Exception:
            continue
    return ""


# ----------------------------------------------------------------------------
# Label patterns (нестрогие, учитываем мусор Tesseract)
# ----------------------------------------------------------------------------
# Разные варианты «Вес», т.к. Tesseract часто путает В/B, ес/ec/яс/еe.
_W_VAR = r"(?:вес|bес|bec|вeс|вес|wec|wес|bеc|ьес|нес)"
_VES_VAR = r"(?:[вbBв][ея][ес][ея]?|[bB][ea][cз])"

_LABEL_PATTERNS = {
    # «Идеальный Вес» (правый верхний блок).
    "ideal_weight": re.compile(
        r"идеальн\w*\s*в\w{1,3}",
        re.IGNORECASE | re.UNICODE,
    ),
    # Иногда Tesseract схлопывает в одно слово: «Ипеальыйвее»
    "ideal_weight_glued": re.compile(
        r"и[пнт]еа[лр]ь?ны\w*\s*в\w{0,3}",
        re.IGNORECASE | re.UNICODE,
    ),
    # «Процентное содержание жира»
    "pbf_full": re.compile(
        r"процент\w*\s+содерж\w*\s+жир",
        re.IGNORECASE | re.UNICODE,
    ),
    # компактная «Содержание жира» (тоже встречается)
    "pbf_short": re.compile(
        r"содерж\w*\s+жир",
        re.IGNORECASE | re.UNICODE,
    ),
    # «Масса/Массы скелетной мускулатуры» — после нормализации латиницы
    # «Maces» превращается в «Мaceс/Масеs»; ловим по «мас… скел…».
    "smm_full": re.compile(
        r"мас\w*\s*скел[еeё]тн",
        re.IGNORECASE | re.UNICODE,
    ),
    # «Мускулатуры» отдельно — иногда подпись разорвана, и значение лежит
    # на строке с «мускулатуры …».
    "smm_continuation": re.compile(
        r"мускулатур",
        re.IGNORECASE | re.UNICODE,
    ),
    # «Вес (kg)» в истории — короткая подпись
    "weight_short": re.compile(
        r"^\s*\W*(вес)\W*(kg|кг)\W*",
        re.IGNORECASE | re.UNICODE,
    ),
}

_RANGE = {
    "weight_kg":   (30.0, 250.0),
    "smm_kg":      (10.0, 80.0),
    "pbf_percent": (3.0, 60.0),
}


def _in_range(metric: str, value: float) -> bool:
    lo, hi = _RANGE[metric]
    return lo <= value <= hi


# ----------------------------------------------------------------------------
# Парсер
# ----------------------------------------------------------------------------
def parse_inbody_text(text: str):
    """Прогоняем построчно. Для каждой строки — match на подписи, потом числа.

    Возвращает (metrics, debug).
    """
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

    raw_lines = [ln for ln in text.splitlines() if ln.strip()]
    # Нормализованная и оригинальная версия — пара (norm, orig).
    pairs = [(_normalize_cyr(ln), ln) for ln in raw_lines]

    def _scan_value(idx: int, after_text: str, prefer_decimals: bool = True,
                     min_value: float = 0.0, lookahead: int = 2):
        """Ищем подходящее число: сначала на этой строке после якоря,
        затем — в следующих lookahead строках полностью.
        prefer_decimals=True — игнорируем целые числа (бар-чарт tick'и)."""
        # на текущей строке
        nums = _decimals_in(after_text) if prefer_decimals else _numbers_in(after_text)
        cand = [n for n in nums if n >= min_value]
        if cand:
            return cand[0], "same_line"
        # в lookahead-строках
        for j in range(idx + 1, min(len(pairs), idx + 1 + lookahead)):
            nums = _decimals_in(pairs[j][1])
            cand = [n for n in nums if n >= min_value]
            if cand:
                return cand[0], f"next+{j-idx}"
        return None, None

    # Проход 1 — приоритетные якоря (по нормализованному тексту)
    for i, (norm, orig) in enumerate(pairs):
        # weight: «Идеальный Вес 79.3 kg»
        if "weight_kg" not in metrics:
            m = _LABEL_PATTERNS["ideal_weight"].search(norm) or _LABEL_PATTERNS["ideal_weight_glued"].search(norm)
            if m:
                # после якоря — обычное (не только дробное) число ≥30
                nums = _numbers_in(orig[m.end():])
                cand = [n for n in nums if n >= 30.0]
                if cand:
                    try_set("weight_kg", cand[0], "anchor:ideal", orig.strip())

        if "pbf_percent" not in metrics:
            m = _LABEL_PATTERNS["pbf_full"].search(norm)
            if m:
                v, src = _scan_value(i, orig[m.end():], prefer_decimals=True, min_value=2.0)
                if v is not None:
                    try_set("pbf_percent", v, f"anchor:pbf_full({src})", orig.strip())

        if "smm_kg" not in metrics:
            m = _LABEL_PATTERNS["smm_full"].search(norm)
            if m:
                # значение почти всегда на следующей строке — пропускаем tick'и
                v, src = _scan_value(i, orig[m.end():], prefer_decimals=True, min_value=10.0)
                if v is not None:
                    try_set("smm_kg", v, f"anchor:smm_full({src})", orig.strip())

    # Проход 1.5 — если SMM не нашли, ищем по «мускулатуры»: на этой же строке
    # должно быть дробное число.
    if "smm_kg" not in metrics:
        for i, (norm, orig) in enumerate(pairs):
            m = _LABEL_PATTERNS["smm_continuation"].search(norm)
            if m:
                nums = _decimals_in(orig)
                cand = [n for n in nums if 10.0 <= n <= 80.0]
                if cand:
                    try_set("smm_kg", cand[0], "anchor:smm_cont", orig.strip())
                    if "smm_kg" in metrics:
                        break

    # Проход 2 — генерики по таблице «История состава тела»:
    # короткие строки «Вес (kg) 79.3» / «(kg) 41.7».
    if "weight_kg" not in metrics:
        for i, (norm, orig) in enumerate(pairs):
            if _LABEL_PATTERNS["weight_short"].search(norm):
                v, src = _scan_value(i, orig, prefer_decimals=False, min_value=30.0)
                if v is not None:
                    try_set("weight_kg", v, f"generic:weight_short({src})", orig.strip())
                    break

    if "pbf_percent" not in metrics:
        for i, (norm, orig) in enumerate(pairs):
            m = _LABEL_PATTERNS["pbf_short"].search(norm)
            if m:
                v, src = _scan_value(i, orig[m.end():], prefer_decimals=True, min_value=2.0)
                if v is not None:
                    try_set("pbf_percent", v, f"generic:pbf_short({src})", orig.strip())
                    if "pbf_percent" in metrics:
                        break

    return metrics, debug


# ----------------------------------------------------------------------------
# Main
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
        "raw": "",
        "variant": "",
        "psm": 6,
        "score": -1.0,
        "debug": {},
    }

    # Сливаем всё, что нашли разные варианты OCR — берём лучший по числу метрик.
    for vname, imgv in _preprocess_variants(img_bgr):
        for psm in (6, 4):
            txt = _tesseract_text(imgv, psm=psm)
            if not txt.strip():
                continue
            m, d = parse_inbody_text(txt)
            score = float(sum(1 for k in ("weight_kg", "pbf_percent", "smm_kg") if k in m))
            score += sum(
                0.5 for v in d.values()
                if isinstance(v, dict) and str(v.get("source", "")).startswith("anchor")
            )
            if score > best["score"]:
                best = {"metrics": m, "raw": txt, "variant": vname, "psm": psm,
                        "score": score, "debug": d}

    metrics = best["metrics"]
    conf = 0.0
    for k in ("weight_kg", "pbf_percent", "smm_kg"):
        if k in metrics:
            conf += 0.33
            src = best["debug"].get(k, {}).get("source", "")
            if str(src).startswith("anchor"):
                conf += 0.04
    conf = round(min(1.0, conf), 2)

    return {
        "ok": True,
        "metrics": metrics,
        "confidence": conf,
        "raw_text": (best["raw"] or "")[:4000],
        "debug": {
            "variant": best["variant"],
            "psm": best["psm"],
            "score": best["score"],
            "engine": "tesseract-rus+eng",
            "matches": best["debug"],
        },
    }
