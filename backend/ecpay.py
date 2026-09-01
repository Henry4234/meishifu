"""綠界科技 ECPay 全方位金流 (AioCheckOut V5) 串接。

流程 (對應綠界文件的付款流程圖):
1. 本站建立訂單 → build_checkout() 產生付款參數與 CheckMacValue
2. 前台以隱藏表單 POST 到 ECPAY_AIO_URL,消費者在綠界頁面付款
3. 綠界 server-to-server POST 付款結果到 ReturnURL (/api/payment/notify)
4. 綠界同時讓消費者瀏覽器 POST 到 OrderResultURL (/api/payment/result),
   本後端驗章後再把消費者導回前台結帳結果頁

CheckMacValue 演算法 (EncryptType=1,SHA256):
    HashKey=<key>&<參數依名稱 A-Z 排序>&HashIV=<iv>
    → .NET 風格 UrlEncode → 轉小寫 → SHA256 → 轉大寫
"""
import hashlib
from datetime import datetime
from urllib.parse import quote_plus

import config

# 本站付款方式 → 綠界 ChoosePayment
CHOOSE_PAYMENT = {
    "credit": "Credit",
    "transfer": "ATM",
}

# 綠界回傳碼:1 = 付款成功;2 = ATM 取號成功 (尚未付款)
RTN_PAID = "1"
RTN_ATM_CODE_ISSUED = "2"


def _dotnet_urlencode(raw: str) -> str:
    """模擬 .NET HttpUtility.UrlEncode:空白轉 +,且不編碼 -_.!*()。

    Python 的 quote_plus 預設會編碼 !*() 但不編碼 ~,與 .NET 相反,故兩邊都要修正。
    """
    return quote_plus(raw, safe="-_.!*()").replace("~", "%7e").lower()


def check_mac_value(params: dict, hash_key: str = None, hash_iv: str = None) -> str:
    """依綠界規格計算 CheckMacValue。"""
    hash_key = config.ECPAY_HASH_KEY if hash_key is None else hash_key
    hash_iv = config.ECPAY_HASH_IV if hash_iv is None else hash_iv
    ordered = sorted(
        ((k, v) for k, v in params.items() if k != "CheckMacValue"),
        key=lambda kv: kv[0].lower(),
    )
    raw = "&".join(f"{k}={v}" for k, v in ordered)
    raw = f"HashKey={hash_key}&{raw}&HashIV={hash_iv}"
    return hashlib.sha256(_dotnet_urlencode(raw).encode("utf-8")).hexdigest().upper()


def verify(params: dict) -> bool:
    """驗證綠界回傳的 CheckMacValue 是否正確 (防止偽造付款結果)。"""
    received = (params.get("CheckMacValue") or "").upper()
    return bool(received) and received == check_mac_value(params)


def _item_name(items, shipping_fee: int) -> str:
    """綠界商品名稱欄位:多筆以 # 分隔,上限 400 字。"""
    parts = [
        f"{name.replace('#', ' ')} NT${price} x {qty}"
        for _pkg_id, name, price, qty, _line_total in items
    ]
    if shipping_fee:
        parts.append(f"運費 NT${shipping_fee} x 1")
    return "#".join(parts)[:400]


def build_checkout(order_no: str, total: int, payment_method: str, items, shipping_fee: int = 0) -> dict:
    """產生前台自動送出的付款表單資料。

    回傳 {"gateway", "action", "params"};前台把 params 展開成 hidden input
    後 POST 到 action,即進入綠界付款頁。
    """
    params = {
        "MerchantID": config.ECPAY_MERCHANT_ID,
        "MerchantTradeNo": order_no,                       # 綠界限制 20 碼英數
        "MerchantTradeDate": datetime.now().strftime("%Y/%m/%d %H:%M:%S"),
        "PaymentType": "aio",
        "TotalAmount": str(int(total)),
        "TradeDesc": "meishifu online order",              # 避免中文編碼差異,固定使用英文
        "ItemName": _item_name(items, shipping_fee),
        "ReturnURL": config.PAY_NOTIFY_URL,
        "OrderResultURL": config.PAY_RESULT_URL,
        "ClientBackURL": f"{config.PAY_RETURN_URL}?order_no={order_no}",
        "ChoosePayment": CHOOSE_PAYMENT.get(payment_method, "Credit"),
        "EncryptType": "1",
        "NeedExtraPaidInfo": "Y",
    }
    if params["ChoosePayment"] == "ATM":
        params["ExpireDate"] = "3"                         # ATM 虛擬帳號 3 天內有效
    params["CheckMacValue"] = check_mac_value(params)
    return {
        "gateway": "ecpay",
        "env": config.ECPAY_ENV,
        "action": config.ECPAY_AIO_URL,
        "params": params,
    }


def atm_info(data: dict) -> str:
    """把 ATM 取號結果整理成一行可存進 orders.payment_info 的文字。"""
    bank = data.get("BankCode", "")
    account = data.get("vAccount", "")
    expire = data.get("ExpireDate", "")
    if not (bank or account):
        return ""
    return f"ATM 銀行代碼 {bank} / 虛擬帳號 {account} / 繳費期限 {expire}"[:255]
