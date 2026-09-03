"""將訂單狀態通知送進 Cloud Tasks，未設定佇列時同步寄送。"""
import hashlib
import hmac
import json
import logging
import uuid

from google.cloud import tasks_v2

import config
import mailer

log = logging.getLogger(__name__)

_client = None


def sign_payload(body: bytes) -> str:
    return hmac.new(
        config.SECRET_KEY.encode("utf-8"), body, hashlib.sha256).hexdigest()


def verify_signature(body: bytes, signature: str) -> bool:
    return bool(signature) and hmac.compare_digest(sign_payload(body), signature)


def _get_client():
    global _client
    if _client is None:
        _client = tasks_v2.CloudTasksClient()
    return _client


def dispatch_order_status(order: dict) -> str:
    """排入狀態信任務；回傳 queued / sent / skipped / failed。"""
    status = order.get("status", "")
    if status not in mailer.NOTIFIABLE_STATUSES or not (order.get("email") or "").strip():
        return "skipped"

    event_id = uuid.uuid4().hex
    cfg = config.MAIL_TASKS
    if not all((cfg["project"], cfg["location"], cfg["queue"], cfg["url"])):
        try:
            return "sent" if mailer.send_order_status(order, status, event_id) else "skipped"
        except Exception:  # pragma: no cover - 取決於外部 SMTP
            log.exception("同步寄送訂單狀態信失敗: order_id=%s", order.get("id"))
            return "failed"

    body = json.dumps({
        "event_id": event_id,
        "order_id": order["id"],
        "status": status,
    }, separators=(",", ":")).encode("utf-8")

    try:
        client = _get_client()
        parent = client.queue_path(cfg["project"], cfg["location"], cfg["queue"])
        task = {
            "name": client.task_path(
                cfg["project"], cfg["location"], cfg["queue"], f"order-status-{event_id}"),
            "http_request": {
                "http_method": tasks_v2.HttpMethod.POST,
                "url": cfg["url"],
                "headers": {
                    "Content-Type": "application/json",
                    "X-Meishifu-Task-Signature": sign_payload(body),
                },
                "body": body,
            },
        }
        client.create_task(request={"parent": parent, "task": task})
        return "queued"
    except Exception:  # pragma: no cover - 取決於 GCP 服務狀態
        log.exception("建立訂單狀態寄信任務失敗，改為同步寄送: order_id=%s", order["id"])
        try:
            return "sent" if mailer.send_order_status(order, status, event_id) else "skipped"
        except Exception:
            log.exception("同步備援寄送訂單狀態信失敗: order_id=%s", order["id"])
            return "failed"
