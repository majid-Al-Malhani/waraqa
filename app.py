"""
* وَرَقَة — عارض ملفات في المتصفح. HTML وCSS فقط، بلا جافاسكربت.
* تغيّر جوهري في هذه النسخة: العارض هو الموقع.
* الجذر "/" لم يعد صفحة مكتبة، بل يفتح آخر ملف مباشرة؛ والتنقّل بين الملفات
* صار عبر شريط تبويبات أفقي داخل العارض نفسه.
"""

from __future__ import annotations

import os
import re
import secrets
import shutil
import unicodedata
from pathlib import Path

from flask import (
    Flask,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)

import pages as pages_module

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
PAGES_DIR = BASE_DIR / "pages_out"
UPLOAD_DIR.mkdir(exist_ok=True)
PAGES_DIR.mkdir(exist_ok=True)

ALLOWED: dict[str, tuple[str, str]] = {
    ".pdf": ("application/pdf", "pdf"),
    ".png": ("image/png", "image"),
    ".jpg": ("image/jpeg", "image"),
    ".jpeg": ("image/jpeg", "image"),
    ".webp": ("image/webp", "image"),
    ".gif": ("image/gif", "image"),
    ".txt": ("text/plain", "text"),
    ".md": ("text/plain", "text"),
    ".csv": ("text/plain", "text"),
}

# ! لا .html ولا .svg: كلاهما ينفّذ جافاسكربت، وتقديمهما من نطاقك يفتح ثغرة XSS مخزّنة.

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
NAME_SAFE = re.compile(r"[^\w\u0600-\u06FF.\- ]", re.UNICODE)
STORED_PATTERN = re.compile(r"[A-Za-z0-9_-]{8,32}__[\w\u0600-\u06FF.\- ]{1,80}", re.UNICODE)

# ? يقبل اسم الصفحة الكاملة 0001.webp واسم المصغّرة t0001.webp.
PAGE_PATTERN = re.compile(r"t?\d{4}\.webp")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
app.secret_key = os.environ.get("WARAQA_SECRET") or secrets.token_hex(16)


# ————————————————————————————————————————————————
# * أدوات مساعدة
# ————————————————————————————————————————————————
def clean_name(raw: str) -> str:
    # ! لا تستخدم secure_filename: فهي تحذف الحروف العربية من الاسم.
    name = unicodedata.normalize("NFC", Path(raw).name)
    name = NAME_SAFE.sub("_", name).strip(". ")
    return name[:80] or "file"


def human_size(num: float) -> str:
    for unit in ("B", "KB", "MB"):
        if num < 1024 or unit == "MB":
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} MB"


def describe(stored: str) -> dict | None:
    path = UPLOAD_DIR / stored
    if not path.is_file():
        return None
    mime, kind = ALLOWED.get(path.suffix.lower(), (None, None))
    if kind is None:
        return None
    return {
        "stored": stored,
        "display": stored.split("__", 1)[-1],
        "kind": kind,
        "mime": mime,
        "ext": path.suffix.lower().lstrip("."),
        "size": human_size(path.stat().st_size),
        "mtime": path.stat().st_mtime,
    }


def library() -> list[dict]:
    """كل الملفات الصالحة، الأحدث أولًا. هذه هي مادة شريط التبويبات."""
    cards = [c for name in os.listdir(UPLOAD_DIR) if (c := describe(name))]
    cards.sort(key=lambda c: c["mtime"], reverse=True)
    return cards


def resolve(stored: str) -> Path:
    # ! خط الدفاع ضد Path Traversal مثل ../../app.py
    if not STORED_PATTERN.fullmatch(stored):
        abort(404)
    path = (UPLOAD_DIR / stored).resolve()
    if path.parent != UPLOAD_DIR.resolve() or not path.is_file():
        abort(404)
    return path


# ————————————————————————————————————————————————
# * المسارات
# ————————————————————————————————————————————————
@app.get("/")
def index():
    """لا صفحة مكتبة بعد اليوم: الجذر يفتح آخر ملف مرفوع."""
    cards = library()
    if cards:
        return redirect(url_for("view_file", stored=cards[0]["stored"]))
    return render_template("index.html", limit_mb=MAX_UPLOAD_BYTES // (1024 * 1024))


@app.get("/new")
def new_file():
    """صفحة الرفع، يُوصل إليها من زر + في شريط التبويبات."""
    return render_template(
        "index.html", limit_mb=MAX_UPLOAD_BYTES // (1024 * 1024), library=library()
    )


@app.post("/upload")
def upload():
    item = request.files.get("file")
    if not item or not item.filename:
        flash("اختر ملفًا قبل الرفع.")
        return redirect(url_for("new_file"))

    ext = Path(item.filename).suffix.lower()
    if ext not in ALLOWED:
        flash(f"الامتداد {ext or '؟'} غير مدعوم. المدعوم: " + "، ".join(sorted(ALLOWED)))
        return redirect(url_for("new_file"))

    stored = f"{secrets.token_urlsafe(9)}__{clean_name(item.filename)}"
    item.save(UPLOAD_DIR / stored)

    if ext == ".pdf":
        try:
            pages_module.render_pdf(UPLOAD_DIR / stored, PAGES_DIR / stored)
        except Exception:
            shutil.rmtree(PAGES_DIR / stored, ignore_errors=True)
            (UPLOAD_DIR / stored).unlink(missing_ok=True)
            flash("تعذّرت قراءة هذا الملف. قد يكون تالفًا أو محميًا بكلمة مرور.")
            return redirect(url_for("new_file"))

    return redirect(url_for("view_file", stored=stored))


@app.get("/view/<stored>")
def view_file(stored: str):
    path = resolve(stored)
    card = describe(stored)
    if card is None:
        abort(404)

    sheets: list[dict] = []
    text = None

    if card["kind"] == "pdf":
        sheets = pages_module.read_manifest(PAGES_DIR / stored)
    elif card["kind"] == "text":
        text = path.read_text(encoding="utf-8", errors="replace")

    # * القالب يحتاج إلى كل الملفات ليبني شريط التبويبات العلوي.
    return render_template(
        "view.html", card=card, sheets=sheets, text=text, library=library()
    )


@app.get("/page/<stored>/<name>")
def serve_page(stored: str, name: str):
    """صورة صفحة كاملة أو مصغّرة."""
    resolve(stored)
    if not PAGE_PATTERN.fullmatch(name):
        abort(404)
    response = send_from_directory(PAGES_DIR / stored, name, conditional=True)
    # * الصور مشتقّة وثابتة، فتُخزَّن في ذاكرة المتصفح سنة كاملة.
    response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return response


@app.get("/file/<stored>")
def serve_file(stored: str):
    """الملف الأصلي معروضًا داخل المتصفح: Content-Disposition: inline."""
    resolve(stored)
    card = describe(stored)
    if card is None:
        abort(404)

    response = send_from_directory(
        UPLOAD_DIR,
        card["stored"],
        mimetype=card["mime"],
        as_attachment=False,
        download_name=card["display"],
        conditional=True,
    )
    if card["kind"] == "text":
        response.headers["Content-Type"] = "text/plain; charset=utf-8"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    response.headers["Cache-Control"] = "public, max-age=86400"
    return response


@app.get("/download/<stored>")
def download_file(stored: str):
    """المقابل: تنزيل صريح — Content-Disposition: attachment."""
    resolve(stored)
    card = describe(stored)
    if card is None:
        abort(404)
    return send_from_directory(
        UPLOAD_DIR, card["stored"], as_attachment=True, download_name=card["display"]
    )

@app.get("/delete/<stored>")
def confirm_delete(stored: str):
    """صفحة تأكيد الحذف.

    * الخطوتان مقصودتان: بلا جافاسكربت لا يوجد صندوق تأكيد،
    * فالصفحة الوسيطة هي ما يمنع الحذف بنقرة طائشة.
    """
    resolve(stored)
    card = describe(stored)
    if card is None:
        abort(404)
    return render_template("confirm.html", card=card, library=library())


@app.post("/delete/<stored>")
def delete_file(stored: str):
    """الحذف الفعلي — عبر POST لا GET."""
    # ! الحذف عملية مغيِّرة للحالة، فلا يجوز أن تُنفَّذ بطلب GET:
    # ! أي زاحف أو معاينة رابط قد تمسح ملفاتك كلها.
    resolve(stored)
    (UPLOAD_DIR / stored).unlink(missing_ok=True)
    shutil.rmtree(PAGES_DIR / stored, ignore_errors=True)
    flash("حُذف الملف.")
    return redirect(url_for("index"))

@app.errorhandler(413)
def too_large(_):
    flash(f"حجم الملف يتجاوز {MAX_UPLOAD_BYTES // (1024 * 1024)} ميغابايت.")
    return redirect(url_for("new_file")), 302


if __name__ == "__main__":
    app.run(debug=True, port=5000)
