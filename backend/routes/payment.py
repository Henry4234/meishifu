"""金流公司對接 (預留介面)。

正式串接時 (例: 綠界 ECPay / 藍新 NewebPay):
1. 在 .env 補上 PAY_PROVIDER / PAY_MERCHANT_ID / PAY_HASH_KEY / PAY_HASH_IV / PAY_API_URL
2. 於 build_payment_payload() 依金流商規格產生加密參數與付款頁網址
3. 金流商付款完成後會 POST 回 /api/payment/notify,於 notify() 驗證簽章並更新訂單狀態
"""
from flask import Blueprint, jsonify, request

import config
import db

payment_bp = Blueprint("payment", __name__)


def build_payment_payload(order_no: str, amount: int, payment_method: str) -> dict:
    """產生前端發起付款所需的資訊。

    目前為 stub:回傳模擬資料。正式串接時改為依金流商規格
    產生 CheckMacValue / TradeInfo 等加密欄位與付款頁 URL。
    """
    gw = config.PAYMENT_GATEWAY
    return {
        "provider": gw["provider"],
        "merchant_id": gw["merchant_id"] or "MOCK_MERCHANT",
        "order_no": order_no,
        "amount": amount,
        "method": payment_method,
        # 正式環境: 金流商付款頁 URL (或前端表單 POST 目標)
        "payment_url": gw["api_url"] or None,
        "notify_url": gw["notify_url"],
        "return_url": gw["return_url"],
        "mock": not gw["api_url"],  # True 表示尚未接上真實金流
    }


@payment_bp.post("/notify")
def notify():
    """金流商付款結果回呼 (Webhook)。

    正式串接時必須:
    1. 依金流商文件驗證簽章 (CheckMacValue / TradeSha)
    2. 核對金額與訂單編號
    3. 回應金流商要求的字串 (例: ECPay 需回 '1|OK')
    """
    data = request.form.to_dict() or request.get_json(silent=True) or {}
    order_no = data.get("order_no") or data.get("MerchantTradeNo", "")
    success = str(data.get("RtnCode", data.get("status", ""))) in ("1", "SUCCESS", "paid")

    if order_no and success:
        db.execute(
            "UPDATE orders SET payment_status = 'paid', status = 'paid'"
            " WHERE order_no = %s AND payment_status = 'unpaid'",
            (order_no,),
        )
        return "1|OK"
    return "0|FAIL"


@payment_bp.post("/mock-pay")
def mock_pay():
    """開發用:模擬付款成功,將訂單標記為已付款。上線前移除。"""
    data = request.get_json(silent=True) or {}
    order_no = data.get("order_no", "")
    affected = db.execute(
        "UPDATE orders SET payment_status = 'paid', status = 'paid'"
        " WHERE order_no = %s AND payment_status = 'unpaid'",
        (order_no,),
    )
    row = db.query_one("SELECT payment_status, status FROM orders WHERE order_no = %s", (order_no,))
    if not row:
        return jsonify({"error": "查無訂單"}), 404
    return jsonify({"order_no": order_no, **row})
