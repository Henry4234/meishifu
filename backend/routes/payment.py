"""綠界 ECPay 付款結果處理。

- POST /api/payment/notify  綠界 server-to-server 付款通知 (ReturnURL,必須公開可連)
- POST /api/payment/result  消費者付款後瀏覽器帶回的結果 (OrderResultURL),驗章後導回前台
- GET  /api/payment/status/<order_no>  前台結帳結果頁查詢付款狀態
- POST /api/payment/mock-pay 開發用,直接把訂單標記為已付款

兩個回呼都會驗證 CheckMacValue 與金額,驗證失敗一律不更新訂單。
"""
import logging

from flask import Blueprint, jsonify, redirect, request

import config
import db
import ecpay

payment_bp = Blueprint("payment", __name__)
log = logging.getLogger(__name__)


def _mark_paid(order_no: str, data: dict) -> str:
    """驗證並將訂單標記為已付款。回傳 'paid' / 'pending' / 錯誤原因。"""
    if str(data.get("SimulatePaid", "0")) == "1":
        log.warning("忽略綠界模擬付款通知: %s", order_no)
        return "simulated"

    order = db.query_one(
        "SELECT id, order_no, customer_name, email, total, payment_status FROM orders"
        " WHERE order_no = %s", (order_no,))
    if not order:
        log.warning("金流回呼查無訂單: %s", order_no)
        return "order_not_found"

    rtn_code = str(data.get("RtnCode", ""))
    trade_no = (data.get("TradeNo") or "")[:32]

    # ATM 取號成功 (尚未付款):記下虛擬帳號供客服與消費者查詢
    if rtn_code == ecpay.RTN_ATM_CODE_ISSUED:
        info = ecpay.atm_info(data)
        db.execute(
            "UPDATE orders SET trade_no = %s, payment_info = %s WHERE order_no = %s",
            (trade_no, info, order_no))
        return "pending"

    if rtn_code != ecpay.RTN_PAID:
        log.warning("綠界回報付款失敗 %s: %s %s", order_no, rtn_code, data.get("RtnMsg", ""))
        return "failed"

    try:
        paid_amount = int(data.get("TradeAmt", 0))
    except (TypeError, ValueError):
        paid_amount = 0
    if paid_amount != int(order["total"]):
        log.error("金額不符 %s: 綠界 %s / 訂單 %s", order_no, paid_amount, order["total"])
        return "amount_mismatch"

    if order["payment_status"] != "paid":
        db.execute(
            "UPDATE orders SET payment_status = 'paid', status = 'paid',"
            " trade_no = %s, paid_at = NOW() WHERE order_no = %s AND payment_status <> 'paid'",
            (trade_no, order_no))
    return "paid"


@payment_bp.post("/notify")
def notify():
    """綠界 server-to-server 付款結果通知 (ReturnURL)。必須回應 '1|OK'。"""
    data = request.form.to_dict() or request.get_json(silent=True) or {}
    if not ecpay.verify(data):
        log.error("綠界通知驗章失敗: %s", data.get("MerchantTradeNo"))
        return "0|CheckMacValue Error"

    result = _mark_paid(data.get("MerchantTradeNo", ""), data)
    if result in ("paid", "pending", "simulated"):
        return "1|OK"
    return "0|FAIL"


@payment_bp.post("/result")
def result():
    """消費者付款完成後,瀏覽器 POST 回本站 (OrderResultURL)。

    驗章後同樣更新訂單 (本機開發時 ReturnURL 收不到,靠這裡完成狀態更新),
    再把消費者導回前台結帳結果頁。
    """
    data = request.form.to_dict() or request.get_json(silent=True) or {}
    order_no = data.get("MerchantTradeNo", "")
    status = "invalid" if not ecpay.verify(data) else _mark_paid(order_no, data)
    return redirect(f"{config.PAY_RETURN_URL}?order_no={order_no}&result={status}", code=303)


@payment_bp.get("/status/<order_no>")
def status(order_no):
    """前台結帳結果頁用:以訂單編號查詢付款狀態 (只回傳非敏感欄位)。"""
    order = db.query_one(
        "SELECT order_no, payment_status, status, payment_method, shipping_method,"
        " subtotal, shipping_fee, total, payment_info FROM orders WHERE order_no = %s",
        (order_no,))
    if not order:
        return jsonify({"error": "查無訂單"}), 404
    order["payment_label"] = config.PAYMENT_LABELS.get(order["payment_method"], order["payment_method"])
    order["shipping_label"] = config.SHIPPING_LABELS.get(order["shipping_method"], order["shipping_method"])
    return jsonify(order)


@payment_bp.post("/mock-pay")
def mock_pay():
    """開發用:模擬付款成功,將訂單標記為已付款。上線前移除。"""
    if config.ECPAY_ENV == "production":
        return jsonify({"error": "Not Found"}), 404

    data = request.get_json(silent=True) or {}
    order_no = data.get("order_no", "")
    # 先確認訂單存在,查無訂單就不要對資料庫下無謂的 UPDATE。
    if not db.query_one("SELECT id FROM orders WHERE order_no = %s", (order_no,)):
        return jsonify({"error": "查無訂單"}), 404

    db.execute(
        "UPDATE orders SET payment_status = 'paid', status = 'paid', paid_at = NOW()"
        " WHERE order_no = %s AND payment_status = 'unpaid'",
        (order_no,),
    )
    row = db.query_one("SELECT payment_status, status FROM orders WHERE order_no = %s", (order_no,))
    if not row:
        return jsonify({"error": "查無訂單"}), 404
    return jsonify({"order_no": order_no, **row})
