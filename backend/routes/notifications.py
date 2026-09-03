"""Cloud Tasks 呼叫的內部交易郵件端點。"""
import logging

from flask import Blueprint, jsonify, request

import db
import mail_tasks
import mailer

log = logging.getLogger(__name__)

notifications_bp = Blueprint("notifications", __name__)


@notifications_bp.post("/mail/order-status")
def order_status_mail():
    body = request.get_data(cache=True)
    signature = request.headers.get("X-Meishifu-Task-Signature", "")
    if not mail_tasks.verify_signature(body, signature):
        return jsonify({"error": "invalid task signature"}), 403

    data = request.get_json(silent=True) or {}
    event_id = str(data.get("event_id") or "")
    status = str(data.get("status") or "")
    try:
        order_id = int(data.get("order_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "invalid order id"}), 400
    if len(event_id) != 32 or status not in mailer.NOTIFIABLE_STATUSES:
        return jsonify({"error": "invalid mail event"}), 400

    order = db.query_one(
        "SELECT id, order_no, customer_name, email, phone, address, store_id,"
        " store_name, store_address, shipping_method, status FROM orders WHERE id = %s",
        (order_id,),
    )
    if not order:
        log.warning("寄信任務查無訂單，略過: order_id=%s", order_id)
        return "", 204
    if order["status"] != status:
        log.info(
            "訂單狀態已再次變更，略過過期通知: order_id=%s task=%s current=%s",
            order_id, status, order["status"])
        return "", 204

    try:
        sent = mailer.send_order_status(order, status, event_id)
    except Exception:  # pragma: no cover - Cloud Tasks 會依非 2xx 回應重試
        log.exception("Cloud Task 寄送訂單狀態信失敗: order_id=%s", order_id)
        return jsonify({"error": "mail delivery failed"}), 503
    if not sent:
        log.error("Cloud Task 未實際寄出訂單狀態信: order_id=%s", order_id)
        return jsonify({"error": "mail delivery disabled"}), 503
    return "", 204
