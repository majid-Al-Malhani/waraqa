"""
* pages.py — تحويل صفحات PDF عند الرفع.
* لكل صفحة ثلاثة نواتج:
*   1. صورة كاملة للعرض.
*   2. مصغّرة للشريط الجانبي.
*   3. طبقة نص شفافة بإحداثياتها — هي ما يعيد البحث والنسخ وقارئات الشاشة.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pymupdf
from PIL import Image

RENDER_DPI = 144
MAX_PAGES = 200
WEBP_QUALITY = 82

THUMB_WIDTH = 150
THUMB_QUALITY = 68


def extract_layer(page) -> list[dict]:
    """يستخرج مقاطع النص مع مواضعها كنسب مئوية من أبعاد الصفحة.

    * النسب لا البكسلات: الصورة تتمدّد وتنكمش مع عرض الشاشة،
    * والنسبة وحدها هي ما يبقى صحيحًا عند كل عرض.
    """
    rect = page.rect
    if not rect.width or not rect.height:
        return []

    spans: list[dict] = []
    for block in page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = span.get("text", "")
                if not text.strip():
                    continue
                x0, y0, x1, y1 = span["bbox"]
                spans.append({
                    "t": text,
                    "x": round(x0 / rect.width * 100, 3),
                    "y": round(y0 / rect.height * 100, 3),
                    "w": round((x1 - x0) / rect.width * 100, 3),
                    "h": round((y1 - y0) / rect.height * 100, 3),
                    # ? حجم الخط منسوبًا إلى عرض الصفحة، ليُقرأ في CSS بوحدة cqw.
                    "f": round(span.get("size", 10) / rect.width * 100, 3),
                })
    return spans


def render_pdf(pdf_path: Path, out_dir: Path) -> list[dict]:
    """يرسم كل صفحة صورةً ومصغّرة، ويستخرج طبقة نصها."""
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest: list[dict] = []

    with pymupdf.open(pdf_path) as document:
        for number, page in enumerate(document, start=1):
            if number > MAX_PAGES:
                break

            pixmap = page.get_pixmap(dpi=RENDER_DPI)
            image = Image.open(io.BytesIO(pixmap.tobytes("png")))

            name = f"{number:04d}.webp"
            image.save(out_dir / name, "WEBP", quality=WEBP_QUALITY, method=4)

            # * المصغّرة تُشتقّ من الصورة نفسها، فلا نرسم الصفحة مرتين.
            height = max(1, round(image.height * THUMB_WIDTH / image.width))
            thumb = image.resize((THUMB_WIDTH, height), Image.LANCZOS)
            thumb_name = f"t{number:04d}.webp"
            thumb.save(out_dir / thumb_name, "WEBP", quality=THUMB_QUALITY, method=4)

            manifest.append({
                "file": name,
                "thumb": thumb_name,
                "w": pixmap.width,
                "h": pixmap.height,
                "tw": thumb.width,
                "th": thumb.height,
                "text": extract_layer(page),
            })

    (out_dir / "index.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    return manifest


def read_manifest(out_dir: Path) -> list[dict]:
    index = out_dir / "index.json"
    if not index.is_file():
        return []
    return json.loads(index.read_text(encoding="utf-8"))


# TODO: نقل التحويل إلى مهمة خلفية حتى لا ينتظر المستخدم أمام صفحة متجمّدة.
# TODO: الصفحات المصوّرة ضوئيًا تعطي طبقة نص فارغة — تحتاج OCR لاستخراج نصها.