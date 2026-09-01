"""訂單通知信 (SMTP)。

未在 .env 設定 SMTP_HOST 時不會真的寄信,只把信件內容印在後端 log,
方便本機開發時確認內容。寄信固定在背景執行緒進行,SMTP 逾時或失敗
都不會讓建立訂單的 API 失敗。
"""
import logging
import smtplib
import threading
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr

import config

log = logging.getLogger(__name__)


def _money(n) -> str:
    return f"NT$ {int(n):,}"


def _send(to_email: str, subject: str, html: str) -> None:
    cfg = config.MAIL
    if not cfg["host"]:
        log.info("[MAIL 未設定 SMTP,略過寄送] to=%s subject=%s\n%s", to_email, subject, html)
        return
    msg = MIMEText(html, "html", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = formataddr((str(Header(cfg["sender_name"], "utf-8")), cfg["sender"]))
    msg["To"] = to_email

    smtp_cls = smtplib.SMTP_SSL if cfg["use_ssl"] else smtplib.SMTP
    with smtp_cls(cfg["host"], cfg["port"], timeout=cfg["timeout"]) as smtp:
        if cfg["use_tls"] and not cfg["use_ssl"]:
            smtp.starttls()
        if cfg["user"]:
            smtp.login(cfg["user"], cfg["password"])
        smtp.send_message(msg)
    log.info("訂單通知信已寄出: %s (%s)", to_email, subject)


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
        f"<td style='padding:8px 0;border-bottom:1px solid #ffd9df'>{name}</td>"
        f"<td style='padding:8px 0;border-bottom:1px solid #ffd9df;text-align:center'>{qty}</td>"
        f"<td style='padding:8px 0;border-bottom:1px solid #ffd9df;text-align:right'>{_money(line_total)}</td>"
        f"</tr>"
        for _pkg_id, name, _price, qty, line_total in items
    )
    shipping = config.SHIPPING_LABELS.get(order["shipping_method"], order["shipping_method"])
    payment = config.PAYMENT_LABELS.get(order["payment_method"], order["payment_method"])
    destination = order.get("store_name") or order.get("address") or "-"
    return f"""
<div style="font-family:'Helvetica Neue',Arial,'Microsoft JhengHei',sans-serif;
            background:#fff8f7;padding:24px;color:#30121a">
  <div style="max-width:560px;margin:0 auto;background:#ffffff;border-radius:16px;padding:32px">
    <h1 style="font-size:22px;margin:0 0 8px">訂單已成立</h1>
    <p style="margin:0 0 24px;color:#4e4447">
      {order['customer_name']} 您好,感謝您在美師傅 meishifu 訂購,以下是您的訂單明細。
    </p>
    <p style="margin:0 0 4px;color:#4e4447">訂單編號</p>
    <p style="margin:0 0 24px;font-size:20px;font-weight:700;color:#6e555d">{order['order_no']}</p>
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
      <p style="margin:4px 0">聯絡電話:{order['phone']}</p>
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


def render_payment_success(order: dict) -> str:
    return f"""
<div style="font-family:'Helvetica Neue',Arial,'Microsoft JhengHei',sans-serif;
            background:#fff8f7;padding:24px;color:#30121a">
  <div style="max-width:560px;margin:0 auto;background:#ffffff;border-radius:16px;padding:32px">
    <h1 style="font-size:22px;margin:0 0 8px">付款成功</h1>
    <p style="margin:0 0 24px;color:#4e4447">
      {order['customer_name']} 您好,我們已收到訂單 <strong>{order['order_no']}</strong>
      的款項 {_money(order['total'])},將盡快為您安排出貨。
    </p>
    <p style="margin:0;font-size:13px;color:#807477">本信件由系統自動發送,請勿直接回覆。</p>
  </div>
</div>"""


def send_payment_success(order: dict) -> None:
    email = (order.get("email") or "").strip()
    if not email:
        return
    send_async(email, f"【美師傅 meishifu】付款成功通知 {order['order_no']}",
               render_payment_success(order))
