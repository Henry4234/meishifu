"""商品圖片儲存層：本機使用 assets/uploads，Cloud Run 使用 Cloud Storage。"""

import mimetypes
import re
import time
from io import BytesIO
from pathlib import Path

from flask import abort, send_file, send_from_directory
from werkzeug.utils import secure_filename

import config


LOCAL_UPLOAD_DIR = Path(__file__).resolve().parent.parent / "assets" / "uploads"
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


def _storage_bucket():
    if not config.UPLOAD_BUCKET:
        return None
    from google.cloud import storage

    return storage.Client().bucket(config.UPLOAD_BUCKET)


def save_upload(file):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError("僅接受 jpg / png / webp / gif 圖片")

    base = secure_filename(Path(file.filename).stem) or "package"
    base = re.sub(r"[^A-Za-z0-9_-]", "", base)[:40] or "package"
    filename = f"{base}_{int(time.time())}{ext}"
    bucket = _storage_bucket()

    if bucket:
        blob = bucket.blob(f"uploads/{filename}")
        file.stream.seek(0)
        blob.upload_from_file(file.stream, content_type=file.mimetype or None)
    else:
        LOCAL_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        file.save(LOCAL_UPLOAD_DIR / filename)

    return f"/assets/uploads/{filename}"


def serve_upload(filename):
    # 上傳檔名不允許子目錄，避免路徑穿越或讀取非預期物件。
    if not filename or filename != Path(filename).name:
        abort(404)

    bucket = _storage_bucket()
    if not bucket:
        return send_from_directory(LOCAL_UPLOAD_DIR, filename, max_age=3600)

    blob = bucket.blob(f"uploads/{filename}")
    if not blob.exists():
        abort(404)
    payload = BytesIO(blob.download_as_bytes())
    content_type = blob.content_type or mimetypes.guess_type(filename)[0]
    return send_file(payload, mimetype=content_type, max_age=3600)
