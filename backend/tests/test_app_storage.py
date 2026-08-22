from io import BytesIO

import pytest
from werkzeug.datastructures import FileStorage

import app as app_module
import config
import image_storage


def test_health_not_found_and_upload_route(client, monkeypatch):
    assert client.get("/api/health").get_json() == {"status": "ok"}

    missing = client.get("/does-not-exist")
    assert missing.status_code == 404
    assert missing.get_json() == {"error": "not found"}

    monkeypatch.setattr(app_module, "serve_upload", lambda name: (f"file:{name}", 200))
    uploaded = client.get("/assets/uploads/example.jpg")
    assert uploaded.status_code == 200
    assert uploaded.get_data(as_text=True) == "file:example.jpg"


def test_cors_can_be_enabled(monkeypatch):
    monkeypatch.setattr(config, "CORS_ORIGINS", ["https://example.com"])
    cors_app = app_module.create_app()
    response = cors_app.test_client().get(
        "/api/health", headers={"Origin": "https://example.com"}
    )
    assert response.headers["Access-Control-Allow-Origin"] == "https://example.com"


def test_save_upload_locally(tmp_path, monkeypatch):
    monkeypatch.setattr(image_storage, "LOCAL_UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(config, "UPLOAD_BUCKET", "")
    monkeypatch.setattr(image_storage.time, "time", lambda: 1234)

    upload = FileStorage(
        stream=BytesIO(b"jpeg-data"),
        filename="My cake.JPG",
        content_type="image/jpeg",
    )
    path = image_storage.save_upload(upload)

    assert path == "/assets/uploads/My_cake_1234.jpg"
    assert (tmp_path / "My_cake_1234.jpg").read_bytes() == b"jpeg-data"


def test_save_upload_rejects_non_image():
    upload = FileStorage(stream=BytesIO(b"text"), filename="bad.txt")
    with pytest.raises(ValueError, match="僅接受"):
        image_storage.save_upload(upload)


class FakeBlob:
    def __init__(self, exists=True, content=b"cloud-image", content_type="image/webp"):
        self._exists = exists
        self._content = content
        self.content_type = content_type
        self.uploaded = None

    def upload_from_file(self, stream, content_type=None):
        self.uploaded = (stream.read(), content_type)

    def exists(self):
        return self._exists

    def download_as_bytes(self):
        return self._content


class FakeBucket:
    def __init__(self, blob):
        self._blob = blob
        self.requested = []

    def blob(self, name):
        self.requested.append(name)
        return self._blob


def test_cloud_storage_upload_and_serve(app, monkeypatch):
    blob = FakeBlob()
    bucket = FakeBucket(blob)
    monkeypatch.setattr(image_storage, "_storage_bucket", lambda: bucket)
    monkeypatch.setattr(image_storage.time, "time", lambda: 99)

    upload = FileStorage(
        stream=BytesIO(b"png-data"), filename="蛋糕.png", content_type="image/png"
    )
    path = image_storage.save_upload(upload)
    assert path == "/assets/uploads/package_99.png"
    assert blob.uploaded == (b"png-data", "image/png")

    with app.test_request_context("/"):
        response = image_storage.serve_upload("package_99.png")
        response.direct_passthrough = False
        assert response.get_data() == b"cloud-image"
        assert response.mimetype == "image/webp"


def test_serve_upload_guards_paths_and_missing_blob(app, monkeypatch):
    with app.test_request_context("/"):
        with pytest.raises(Exception) as invalid:
            image_storage.serve_upload("../secret.jpg")
        assert invalid.value.code == 404

    monkeypatch.setattr(
        image_storage, "_storage_bucket", lambda: FakeBucket(FakeBlob(exists=False))
    )
    with app.test_request_context("/"):
        with pytest.raises(Exception) as missing:
            image_storage.serve_upload("missing.jpg")
        assert missing.value.code == 404
