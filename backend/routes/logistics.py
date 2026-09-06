"""綠界電子地圖 (ExpressMap) 選店。

前台按下「選擇門市」→ 同分頁前往 GET /api/logistics/map?method=fami
→ 本後端回傳一頁自動送出的表單,把消費者帶到綠界電子地圖
→ 消費者選好門市,綠界 POST 到 /api/logistics/map-reply
→ 本後端把門市資料簽章後,以 303 導回購物車頁 (門市資料放在查詢字串)

為什麼不用彈出視窗 + postMessage:
    LINE 內建瀏覽器 (WebView) 不支援 window.open 彈出視窗,就算開得起來
    window.opener 也是 null,postMessage 永遠回不到購物車頁。同分頁跳轉在
    每一種瀏覽器都可行,也順便消除了彈出視窗被攔截的失敗模式。

門市資料經由網址傳遞,因此一定要簽章:電子地圖本身不需要 CheckMacValue,
我們以 SECRET_KEY 對門市欄位做 HMAC,建立訂單時再驗一次,消費者無法竄改。
"""
from html import escape
from urllib.parse import urlencode

from flask import Blueprint, redirect, request

import config
import ecpay

logistics_bp = Blueprint("logistics", __name__)


def _page(title: str, body: str, script: str = "") -> str:
    return f"""<!DOCTYPE html>
<html lang="zh-TW"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{escape(title)}</title>
<style>body{{margin:0;padding:32px;font-family:"Helvetica Neue",Arial,"Microsoft JhengHei",sans-serif;
background:#fff8f7;color:#30121a;text-align:center}}
.btn{{display:inline-block;margin-top:24px;padding:14px 32px;border:0;border-radius:999px;
background:#6e555d;color:#fff;font-size:16px;cursor:pointer;text-decoration:none}}</style></head>
<body>{body}<script>{script}</script></body></html>"""


def _with_query(url: str, params: dict) -> str:
    return url + ("&" if "?" in url else "?") + urlencode(params)


@logistics_bp.get("/map")
def open_map():
    """回傳一頁自動送出的表單,把消費者帶到綠界電子地圖選擇門市。

    表單保留一顆真的送出按鈕:自動送出若被瀏覽器擋下 (部分 App 內建瀏覽器會
    阻擋非使用者觸發的導向),消費者仍可自己按下去,不會卡在空白頁。
    """
    method = request.args.get("method", "")
    if method not in ecpay.LOGISTICS_SUBTYPE:
        return _page(
            "配送方式不正確",
            '<p>不支援的配送方式,請返回購物車重新選擇。</p>'
            f'<a class="btn" href="{escape(config.PAY_RETURN_URL)}">返回購物車</a>'), 400

    device = 1 if request.args.get("device") == "1" else 0
    params = ecpay.map_params(method, device)
    inputs = "".join(
        f'<input type="hidden" name="{escape(k)}" value="{escape(v)}">' for k, v in params.items())
    body = (
        '<p>正在開啟綠界門市地圖…</p>'
        f'<form id="map-form" method="POST" action="{escape(config.ECPAY_MAP_URL)}"'
        f' target="_self" accept-charset="utf-8">{inputs}'
        '<button class="btn" type="submit">開啟門市地圖</button></form>')
    return _page("選擇取件門市", body, 'document.getElementById("map-form").submit();')


@logistics_bp.post("/map-reply")
def map_reply():
    """綠界回傳選定的門市,簽章後以 303 導回購物車頁。

    303 See Other 會把綠界的 POST 轉成 GET,消費者直接回到購物車,
    不需要彈出視窗,也不依賴 JavaScript。
    """
    data = request.form.to_dict() or request.get_json(silent=True) or {}
    method = data.get("ExtraData", "")
    store = {
        "store_id": (data.get("CVSStoreID") or "")[:20],
        "store_name": (data.get("CVSStoreName") or "")[:60],
        "store_address": (data.get("CVSAddress") or "")[:120],
        "sub_type": data.get("LogisticsSubType", ""),
    }
    query = {
        **store,
        "method": method if method in ecpay.LOGISTICS_SUBTYPE else "",
        "store_sig": ecpay.sign_store(store),
    }
    return redirect(_with_query(config.PAY_RETURN_URL, query), code=303)
