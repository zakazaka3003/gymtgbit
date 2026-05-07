import os
import re
import math
import cv2
import numpy as np
from PIL import Image, ImageEnhance

try:
    from paddleocr import PaddleOCR
except Exception:
    PaddleOCR = None

try:
    import pytesseract
except Exception:
    pytesseract = None


_METRIC_SYNONYMS = {
    "weight": [
        r"\bweight\b", r"\bвес\b",
    ],
    "pbf": [
        r"\bpbf\b", r"\bbody\s*fat\b", r"\bfat\b",
        r"\bжир\b", r"\bжировая\s*масса\b", r"\bжировая\b",
    ],
    "smm": [
        r"\bsmm\b", r"\bskeletal\s*muscle\b", r"\bmuscle\b",
        r"\bмышц", r"\bмышечная\s*масса\b",
    ],
}

FLOAT_RE = r"(\d{1,3}(?:[.,]\d{1,2})?)"


def _to_float(x: str):
    x = x.strip().replace(" ", "").replace(",", ".")
    try:
        return float(x)
    except Exception:
        return None


def _safe_crop_document(img_bgr: np.ndarray):
    """Попытка вырезать лист/рамку по контурам."""
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    thr = cv2.adaptiveThreshold(
        blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 7
    )
    thr_inv = 255 - thr

    contours, _ = cv2.findContours(thr_inv, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return img_bgr

    contours = sorted(contours, key=cv2.contourArea, reverse=True)
    h, w = gray.shape[:2]

    for c in contours[:5]:
        area = cv2.contourArea(c)
        if area < 0.25 * (w * h):
            continue
        rect = cv2.minAreaRect(c)
        box = cv2.boxPoints(rect)
        box = np.int0(box)

        x, y, bw, bh = cv2.boundingRect(box)
        pad = int(0.02 * max(w, h))
        x0 = max(0, x - pad)
        y0 = max(0, y - pad)
        x1 = min(w, x + bw + pad)
        y1 = min(h, y + bh + pad)
        crop = img_bgr[y0:y1, x0:x1]
        if crop.size > 0:
            return crop
    return img_bgr


def _deskew(img_bgr: np.ndarray):
    """Deskew: оцениваем угол наклона по минимальному прямоугольнику текста."""
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

    (h, w) = img_bgr.shape[:2]
    center = (w // 2, h // 2)
    M = cv2.getRotationMatrix2D(center, angle, 1.0)
    rotated = cv2.warpAffine(
        img_bgr, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
    )
    return rotated


def _clahe(gray: np.ndarray):
    clahe = cv2.createCLAHE(clipLimit=2.2, tileGridSize=(8, 8))
    return clahe.apply(gray)


def _sharpen(img: np.ndarray):
    kernel = np.array([[0, -1, 0],
                       [-1, 5, -1],
                       [0, -1, 0]])
    return cv2.filter2D(img, -1, kernel)


def _preprocess_variants(img_bgr: np.ndarray):
    """Готовим несколько вариантов изображения (pipeline)."""
    variants = []

    # base: auto-rotate + crop
    img = _deskew(img_bgr)
    img = _safe_crop_document(img)

    variants.append(("orig", img))

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # attempt 2: CLAHE + adaptive threshold + morphology
    g2 = _clahe(gray)
    thr = cv2.adaptiveThreshold(
        g2, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 7
    )
    thr = cv2.medianBlur(thr, 3)
    kernel = np.ones((2, 2), np.uint8)
    thr = cv2.dilate(thr, kernel, iterations=1)
    thr = cv2.erode(thr, kernel, iterations=1)
    variants.append(("thr", cv2.cvtColor(thr, cv2.COLOR_GRAY2BGR)))

    # attempt 3: upscale + sharpen + denoise
    scale = 2
    up = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    up_gray = cv2.cvtColor(up, cv2.COLOR_BGR2GRAY)
    up_gray = cv2.fastNlMeansDenoising(up_gray, None, 15, 7, 21)
    up_gray = _clahe(up_gray)
    up_gray = _sharpen(up_gray)
    thr2 = cv2.threshold(up_gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    variants.append(("up_sharp", cv2.cvtColor(thr2, cv2.COLOR_GRAY2BGR)))

    # attempt 4: more aggressive upscale x3
    scale = 3
    up3 = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    up3 = cv2.fastNlMeansDenoisingColored(up3, None, 10, 10, 7, 21)
    up3g = cv2.cvtColor(up3, cv2.COLOR_BGR2GRAY)
    up3g = _clahe(up3g)
    thr3 = cv2.adaptiveThreshold(
        up3g, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 41, 9
    )
    thr3 = _sharpen(thr3)
    variants.append(("up3_aggr", cv2.cvtColor(thr3, cv2.COLOR_GRAY2BGR)))

    return variants


def _paddle_ocr_text(ocr, img_bgr: np.ndarray):
    """Вернуть text из PaddleOCR."""
    try:
        res = ocr.ocr(img_bgr, cls=True)
        lines = []
        for block in res:
            for item in block:
                txt = item[1][0]
                if txt:
                    lines.append(txt)
        return "\n".join(lines)
    except Exception:
        return ""


def _tesseract_text(img_bgr: np.ndarray):
    if pytesseract is None:
        return ""
    # tesseract любит RGB/PIL
    img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
    pil = Image.fromarray(img_rgb)
    try:
        config = r"--oem 3 --psm 6"
        return pytesseract.image_to_string(pil, lang="eng+rus", config=config)
    except Exception:
        try:
            # fallback eng only
            return pytesseract.image_to_string(pil, lang="eng", config=r"--oem 3 --psm 6")
        except Exception:
            return ""


def parse_inbody_metrics(text: str):
    """
    Парсер устойчивый к шуму.
    Возвращает dict: weight_kg, pbf_percent, smm_kg + score.
    """
    raw = text or ""
    t = raw.lower()
    t = t.replace("—", "-")

    found = {}

    def find_metric(metric_key):
        patterns = _METRIC_SYNONYMS[metric_key]
        for p in patterns:
            # допускаем мусор между словом и числом
            rx = re.compile(p + r".{0,25}?" + FLOAT_RE, re.IGNORECASE | re.DOTALL)
            m = rx.search(t)
            if m:
                val = _to_float(m.group(1))
                if val is not None:
                    return val
        return None

    weight = find_metric("weight")
    pbf = find_metric("pbf")
    smm = find_metric("smm")

    # fallback: иногда InBody пишет значения без рядом стоящих слов,
    # поэтому пытаемся по наиболее вероятным диапазонам.
    all_nums = [_to_float(x) for x in re.findall(FLOAT_RE, t)]
    all_nums = [x for x in all_nums if x is not None]

    if weight is None:
        # типичный вес 35..200
        candidates = [x for x in all_nums if 35 <= x <= 200]
        if candidates:
            weight = max(candidates)  # часто вес — самое большое число

    if pbf is None:
        # жир 3..60
        candidates = [x for x in all_nums if 3 <= x <= 60]
        if candidates:
            pbf = min(candidates) if len(candidates) > 1 else candidates[0]

    if smm is None:
        # мышцы 15..80
        candidates = [x for x in all_nums if 15 <= x <= 80]
        # исключим вес
        if weight is not None:
            candidates = [x for x in candidates if abs(x - weight) > 3]
        if candidates:
            smm = max(candidates)

    if weight is not None:
        found["weight_kg"] = round(float(weight), 1)
    if pbf is not None:
        found["pbf_percent"] = round(float(pbf), 1)
    if smm is not None:
        found["smm_kg"] = round(float(smm), 1)

    score = 0
    if "weight_kg" in found:
        score += 1
    if "pbf_percent" in found:
        score += 1
    if "smm_kg" in found:
        score += 1

    return found, score


def run_inbody_ocr(image_path: str, use_gpu: bool = False):
    """
    Основная функция:
    - загружает фото
    - делает preprocessing + несколько OCR попыток
    - выбирает лучший результат по числу найденных метрик
    """
    if not os.path.exists(image_path):
        return {"ok": False, "error": "Файл не найден"}

    img_bgr = cv2.imread(image_path)
    if img_bgr is None:
        return {"ok": False, "error": "Не удалось прочитать изображение"}

    # init Paddle once
    paddle = None
    if PaddleOCR is not None:
        try:
            paddle = PaddleOCR(use_angle_cls=True, lang="en", use_gpu=use_gpu)
        except Exception:
            paddle = None

    variants = _preprocess_variants(img_bgr)

    best = {
        "score": -1,
        "metrics": {},
        "text": "",
        "variant": "",
        "engine": "",
    }

    for name, imgv in variants:
        # PaddleOCR
        if paddle is not None:
            text = _paddle_ocr_text(paddle, imgv)
            metrics, score = parse_inbody_metrics(text)
            if score > best["score"]:
                best.update({
                    "score": score,
                    "metrics": metrics,
                    "text": text,
                    "variant": name,
                    "engine": "paddle",
                })

        # Tesseract fallback
        text2 = _tesseract_text(imgv)
        metrics2, score2 = parse_inbody_metrics(text2)
        if score2 > best["score"]:
            best.update({
                "score": score2,
                "metrics": metrics2,
                "text": text2,
                "variant": name,
                "engine": "tesseract",
            })

    confidence = min(1.0, best["score"] / 3.0)

    return {
        "ok": True,
        "metrics": best["metrics"],
        "confidence": confidence,
        "raw_text": best["text"][:4000],
        "debug": {"variant": best["variant"], "engine": best["engine"], "score": best["score"]},
    }
