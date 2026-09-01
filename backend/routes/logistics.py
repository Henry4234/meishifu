"""綠界電子地圖 (ExpressMap) 選店。

前台按下「選擇門市」→ 開新視窗到 GET /api/logistics/map?method=fami
→ 本後端回傳一頁自動送出的表單,把消費者帶到綠界電子地圖
→ 消費者選好門市,綠界 POST 到 /api/logistics/map-reply
→ 本後端把門市資料簽章後以 postMessage 回傳給購物車頁,並關閉視窗

電子地圖本身不需要 CheckMacValue;為了避免前端在送出訂單時竄改門市,
回傳的門市資料會附上以 SECRET_KEY 產生的簽章,建立訂單時再驗證一次。
"""
import json
from html import escape

from flask import Blueprint, request

import config
import ecpay

logistics_bp = Blueprint("logistics", __name__)


def _page(title: str, body: str, script: str = "") -> str:
    return f"""<!DOCTYPE html>
<html lang="zh-TW"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{escape(title)}</title>
<style>body{{margin:0;padding:32px;font-family:"Helvetica Neue",Arial,"Microsoft JhengHei",sans-serif;
background:#fff8f7;color:#30121a;text-align:center}}</style></head>
<body>{body}<script>{script}</script></body></html>"""


@logistics_bp.get("/map")
def open_map():
    """回傳一頁自動送出的表單,把消費者帶到綠界電子地圖選擇門市。"""
    method = request.args.get("method", "")
    if method not in ecpay.LOGISTICS_SUBTYPE:
        return _page("配送方式不正確", "<p>不支援的配送方式,請關閉視窗後重新選擇。</p>"), 400

    device = 1 if request.args.get("device") == "1" else 0
    params = ecpay.map_params(method, device)
    inputs = "".join(
        f'<input type="hidden" name="{escape(k)}" value="{escape(v)}">' for k, v in params.items())
    body = (
        f'<p>正在開啟綠界門市地圖…</p>'
        f'<form id="map-form" method="POST" action="{escape(config.ECPAY_MAP_URL)}"'
        f' accept-charset="utf-8">{inputs}</form>')
    return _page("選擇取件門市", body, 'document.getElementById("map-form").submit();')


@logistics_bp.post("/map-reply")
def map_reply():
    """綠界回傳選定的門市,轉交給開啟此視窗的購物車頁後自動關閉。"""
    data = request.form.to_dict() or request.get_json(silent=True) or {}
    method = data.get("ExtraData", "")
    store = {
        "store_id": (data.get("CVSStoreID") or "")[:20],
        "store_name": (data.get("CVSStoreName") or "")[:60],
        "store_address": (data.get("CVSAddress") or "")[:120],
        "store_phone": (data.get("CVSTelephone") or "")[:30],
        "outside": (data.get("CVSOutSide") or ""),      # 1 = 離島門市
        "sub_type": data.get("LogisticsSubType", ""),
        "method": method if method in ecpay.LOGISTICS_SUBTYPE else "",
    }
    store["signature"] = ecpay.sign_store(store)

    payload = {"source": "ecpay-map", "store": store}
    # </ 會提前結束 script 標籤,需轉義
    payload_js = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    target_origin = config.PUBLIC_ORIGIN

    body = (
        f'<p>已選擇門市:<strong>{escape(store["store_name"] or "-")}</strong></p>'
        f'<p>{escape(store["store_address"])}</p>'
        '<p id="hint">正在返回購物車…</p>')
    script = f"""
var payload = {payload_js};
if (window.opener && !window.opener.closed) {{
  window.opener.postMessage(payload, {json.dumps(target_origin)});
  window.close();
}} else {{
  document.getElementById("hint").textContent = "請關閉此視窗,回到購物車重新選擇門市。";
}}"""
    return _page("已選擇門市", body, script)
