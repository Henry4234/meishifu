import json

import config
import db
import mail_tasks
import mailer


def _order(status="shipped"):
    return {
        "id": 7,
        "order_no": "MS1",
        "customer_name": "王小明",
        "email": "buyer@example.com",
        "phone": "0912",
        "address": "台北",
        "store_id": "",
        "store_name": "",
        "store_address": "",
        "shipping_method": "delivery",
        "status": status,
    }


def test_dispatch_sync_and_skip(monkeypatch):
    monkeypatch.setitem(config.MAIL_TASKS, "project", "")
    sent = []
    monkeypatch.setattr(
        mailer, "send_order_status", lambda *args: sent.append(args) or True)

    assert mail_tasks.dispatch_order_status(_order()) == "sent"
    assert sent[0][1] == "shipped"
    assert mail_tasks.dispatch_order_status(_order("paid")) == "skipped"
    assert mail_tasks.dispatch_order_status({**_order(), "email": ""}) == "skipped"


def test_dispatch_creates_signed_cloud_task(monkeypatch):
    class FakeClient:
        request = None

        @staticmethod
        def queue_path(project, location, queue):
            return f"{project}/{location}/{queue}"

        @staticmethod
        def task_path(project, location, queue, task):
            return f"{project}/{location}/{queue}/{task}"

        def create_task(self, request):
            self.request = request

    fake = FakeClient()
    for key, value in {
        "project": "meishifu",
        "location": "asia-east1",
        "queue": "meishifu-mail",
        "url": "https://backend.example/api/internal/mail/order-status",
    }.items():
        monkeypatch.setitem(config.MAIL_TASKS, key, value)
    monkeypatch.setattr(mail_tasks, "_get_client", lambda: fake)

    assert mail_tasks.dispatch_order_status(_order()) == "queued"
    task = fake.request["task"]
    body = task["http_request"]["body"]
    payload = json.loads(body)
    assert payload["order_id"] == 7 and payload["status"] == "shipped"
    signature = task["http_request"]["headers"]["X-Meishifu-Task-Signature"]
    assert mail_tasks.verify_signature(body, signature)
    assert not mail_tasks.verify_signature(body + b"x", signature)
    assert "order-status-" in task["name"]


def test_dispatch_falls_back_when_cloud_tasks_fails(monkeypatch):
    for key, value in {
        "project": "meishifu", "location": "asia-east1", "queue": "mail", "url": "https://x",
    }.items():
        monkeypatch.setitem(config.MAIL_TASKS, key, value)
    monkeypatch.setattr(
        mail_tasks, "_get_client", lambda: (_ for _ in ()).throw(RuntimeError("tasks down")))
    monkeypatch.setattr(mailer, "send_order_status", lambda *_args: True)
    assert mail_tasks.dispatch_order_status(_order()) == "sent"


def _signed_request(client, payload, signature=None):
    body = json.dumps(payload, separators=(",", ":")).encode()
    signature = signature if signature is not None else mail_tasks.sign_payload(body)
    return client.post(
        "/api/internal/mail/order-status",
        data=body,
        content_type="application/json",
        headers={"X-Meishifu-Task-Signature": signature},
    )


def test_order_status_task_endpoint(client, monkeypatch):
    payload = {"event_id": "a" * 32, "order_id": 7, "status": "shipped"}
    assert _signed_request(client, payload, "bad").status_code == 403
    assert _signed_request(client, {**payload, "order_id": "bad"}).status_code == 400
    assert _signed_request(client, {**payload, "status": "paid"}).status_code == 400

    monkeypatch.setattr(db, "query_one", lambda *_args, **_kwargs: None)
    assert _signed_request(client, payload).status_code == 204

    monkeypatch.setattr(db, "query_one", lambda *_args, **_kwargs: _order("completed"))
    assert _signed_request(client, payload).status_code == 204

    sent = []
    monkeypatch.setattr(db, "query_one", lambda *_args, **_kwargs: _order())
    monkeypatch.setattr(
        mailer, "send_order_status", lambda *args: sent.append(args) or True)
    assert _signed_request(client, payload).status_code == 204
    assert sent[0][1:] == ("shipped", "a" * 32)


def test_order_status_task_retries_smtp_failure(client, monkeypatch):
    payload = {"event_id": "b" * 32, "order_id": 7, "status": "cancelled"}
    monkeypatch.setattr(db, "query_one", lambda *_args, **_kwargs: _order("cancelled"))
    monkeypatch.setattr(
        mailer, "send_order_status", lambda *_args: (_ for _ in ()).throw(OSError("smtp down")))
    assert _signed_request(client, payload).status_code == 503

    monkeypatch.setattr(mailer, "send_order_status", lambda *_args: False)
    assert _signed_request(client, payload).status_code == 503
