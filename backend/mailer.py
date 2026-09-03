"""訂單交易通知信 (SMTP；正式環境使用 Resend relay)。

未在 .env 設定 SMTP_HOST 時不會真的寄信,只把信件內容印在後端 log,
方便本機開發時確認內容。訂單成立信沿用背景執行緒；後台狀態信則交由
Cloud Tasks 執行與重試。
"""
import logging
import smtplib
import threading
from html import escape
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr

import config

log = logging.getLogger(__name__)


def _money(n) -> str:
    return f"NT$ {int(n):,}"


def store_destination(order: dict) -> str:
    """店到店顯示門市 (名稱/店號/地址),宅配顯示收件地址。"""
    name = (order.get("store_name") or "").strip()
    if not name:
        return (order.get("address") or "").strip() or "-"
    store_id = (order.get("store_id") or "").strip()
    parts = [f"{name} ({store_id})" if store_id else name]
    if order.get("store_address"):
        parts.append(order["store_address"])
    return " ".join(parts)


NOTIFIABLE_STATUSES = frozenset({"shipped", "completed", "cancelled"})


def _send(to_email: str, subject: str, html: str, *, idempotency_key: str = "") -> bool:
    """透過 SMTP 寄信；回傳是否有實際送到 SMTP relay。

    Resend 支援 Resend-Idempotency-Key，可避免 Cloud Tasks 重試時重複寄信。
    SMTP 錯誤會向上拋出，讓任務處理端回傳非 2xx 並觸發重試。
    """
    cfg = config.MAIL
    if not cfg["host"]:
        log.info("[MAIL 未設定 SMTP,略過寄送] to=%s subject=%s\n%s", to_email, subject, html)
        return False
    msg = MIMEText(html, "html", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = formataddr((str(Header(cfg["sender_name"], "utf-8")), cfg["sender"]))
    msg["To"] = to_email
    if cfg["reply_to"]:
        msg["Reply-To"] = cfg["reply_to"]
    if idempotency_key:
        msg["Resend-Idempotency-Key"] = idempotency_key

    smtp_cls = smtplib.SMTP_SSL if cfg["use_ssl"] else smtplib.SMTP
    with smtp_cls(cfg["host"], cfg["port"], timeout=cfg["timeout"]) as smtp:
        if cfg["use_tls"] and not cfg["use_ssl"]:
            smtp.starttls()
        if cfg["user"]:
            smtp.login(cfg["user"], cfg["password"])
        smtp.send_message(msg)
    log.info("訂單通知信已寄出: %s (%s)", to_email, subject)
    return True


def send_async(to_email: str, subject: str, html: str) -> None:
    """在背景執行緒寄信;失敗只記錄 log,不影響呼叫端。"""
    if not to_email:
        return

    def run():
        try:
            _send(to_email, subject, html)
        except Exception:  # pragma: no cover - 視 SMTP 伺服器狀況而定
            log.exception("寄送訂單通知信失敗: %s", to_email)

    threading.Thread(target=run, daemon=True).start()


def render_order_created(order: dict, items) -> str:
    """訂單成立通知信內容。items 為 [(package_id, name, price, qty, line_total), ...]"""
    rows = "".join(
        f"<tr>"
        f"<td style='padding:8px 0;border-bottom:1px solid #ffd9df'>{escape(str(name))}</td>"
        f"<td style='padding:8px 0;border-bottom:1px solid #ffd9df;text-align:center'>{qty}</td>"
        f"<td style='padding:8px 0;border-bottom:1px solid #ffd9df;text-align:right'>{_money(line_total)}</td>"
        f"</tr>"
        for _pkg_id, name, _price, qty, line_total in items
    )
    shipping = config.SHIPPING_LABELS.get(order["shipping_method"], order["shipping_method"])
    payment = config.PAYMENT_LABELS.get(order["payment_method"], order["payment_method"])
    destination = escape(store_destination(order))
    customer_name = escape(str(order["customer_name"]))
    order_no = escape(str(order["order_no"]))
    phone = escape(str(order["phone"]))
    return f"""
<div style="font-family:'Helvetica Neue',Arial,'Microsoft JhengHei',sans-serif;
            background:#fff8f7;padding:24px;color:#30121a">
  <div style="max-width:560px;margin:0 auto;background:#ffffff;border-radius:16px;padding:32px">
    <h1 style="font-size:22px;margin:0 0 8px">訂單已成立</h1>
    <p style="margin:0 0 24px;color:#4e4447">
      {customer_name} 您好,感謝您在美師傅 meishifu 訂購,以下是您的訂單明細。
    </p>
    <p style="margin:0 0 4px;color:#4e4447">訂單編號</p>
    <p style="margin:0 0 24px;font-size:20px;font-weight:700;color:#6e555d">{order_no}</p>
    <table style="width:100%;border-collapse:collapse;font-size:14px">
      <thead>
        <tr style="color:#4e4447;text-align:left">
          <th style="padding:8px 0">商品</th>
          <th style="padding:8px 0;text-align:center">數量</th>
          <th style="padding:8px 0;text-align:right">小計</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
    <table style="width:100%;margin-top:16px;font-size:14px">
      <tr><td style="color:#4e4447">小計</td>
          <td style="text-align:right">{_money(order['subtotal'])}</td></tr>
      <tr><td style="color:#4e4447">運費 ({shipping})</td>
          <td style="text-align:right">{_money(order['shipping_fee'])}</td></tr>
      <tr><td style="font-size:18px;font-weight:700;padding-top:8px">總計</td>
          <td style="text-align:right;font-size:18px;font-weight:700;color:#6e555d;padding-top:8px">
            {_money(order['total'])}</td></tr>
    </table>
    <div style="margin-top:24px;padding-top:16px;border-top:1px solid #ffd9df;font-size:14px;color:#4e4447">
      <p style="margin:4px 0">配送方式:{shipping}</p>
      <p style="margin:4px 0">收件資訊:{destination}</p>
      <p style="margin:4px 0">聯絡電話:{phone}</p>
      <p style="margin:4px 0">付款方式:{payment}</p>
    </div>
    <p style="margin-top:24px;font-size:13px;color:#807477">
      本信件由系統自動發送,請勿直接回覆。付款完成後我們會盡快為您安排出貨。
    </p>
  </div>
</div>"""


def send_order_created(order: dict, items) -> None:
    email = (order.get("email") or "").strip()
    if not email:
        return
    send_async(email, f"【美師傅 meishifu】訂單成立通知 {order['order_no']}",
               render_order_created(order, items))


def render_order_status(order: dict, status: str) -> str:
    """產生已出貨、已完成或已取消的訂單狀態信。"""
    if status not in NOTIFIABLE_STATUSES:
        raise ValueError(f"不寄送此訂單狀態: {status}")

    content = {
        "shipped": (
            "您的訂單已出貨",
            "商品已交由物流配送，請留意物流通知與收件電話。",
        ),
        "completed": (
            "您的訂單已完成",
            "感謝您的訂購，希望您喜歡美師傅的商品。",
        ),
        "cancelled": (
            "您的訂單已取消",
            "此訂單已取消；如已付款或有任何疑問，請與美師傅聯絡確認退款安排。",
        ),
    }
    title, message = content[status]
    customer_name = escape(str(order.get("customer_name") or "顧客"))
    order_no = escape(str(order["order_no"]))
    destination = escape(store_destination(order))
    shipping = escape(config.SHIPPING_LABELS.get(
        order.get("shipping_method", ""), order.get("shipping_method", "")))
    return f"""
<div style="font-family:'Helvetica Neue',Arial,'Microsoft JhengHei',sans-serif;
            background:#fff8f7;padding:24px;color:#30121a">
  <div style="max-width:560px;margin:0 auto;background:#ffffff;border-radius:16px;padding:32px">
    <h1 style="font-size:22px;margin:0 0 8px">{title}</h1>
    <p style="margin:0 0 24px;color:#4e4447">
      {customer_name} 您好，訂單 <strong>{order_no}</strong> 狀態已更新。
    </p>
    <div style="padding:16px;background:#fff8f7;border-radius:12px;color:#4e4447">
      <p style="margin:0 0 8px">{message}</p>
      <p style="margin:4px 0">配送方式：{shipping or '-'}</p>
      <p style="margin:4px 0">收件資訊：{destination}</p>
    </div>
    <p style="margin:24px 0 0;font-size:13px;color:#807477">
      本信件由系統自動發送。如有任何訂單問題，請透過美師傅官方網站與我們聯絡。
    </p>
  </div>
</div>"""


def send_order_status(order: dict, status: str, event_id: str) -> bool:
    """同步送出狀態信；由 Cloud Tasks 呼叫時讓例外觸發任務重試。"""
    email = (order.get("email") or "").strip()
    if not email or status not in NOTIFIABLE_STATUSES:
        return False
    status_label = {
        "shipped": "已出貨",
        "completed": "已完成",
        "cancelled": "已取消",
    }[status]
    return _send(
        email,
        f"【美師傅 meishifu】訂單{status_label}通知 {order['order_no']}",
        render_order_status(order, status),
        idempotency_key=f"order-status/{event_id}",
    )
