"""前台公開 API:禮盒查詢、建立訂單。

販售單位是「禮盒」(package),不是單一產品 (products 僅供配方與成本計算)。
價格一律以資料庫為準重新計算,不信任前端傳來的金額。
"""
import random
import re
import string
from datetime import datetime

from flask import Blueprint, jsonify, request

import config
import db
import ecpay
import mailer

shop_bp = Blueprint("shop", __name__)

PACKAGE_FIELDS = "id, name, description, spec, category, price, image, tag, sort_order"


def _secondary_categories(package_ids=None):
    """{package_id: [次要分類, ...]}"""
    rows = db.query("SELECT package_id, category FROM package_categories ORDER BY id")
    out = {}
    for r in rows:
        if package_ids is None or r["package_id"] in package_ids:
            out.setdefault(r["package_id"], []).append(r["category"])
    return out


@shop_bp.get("/categories")
def list_categories():
    """前台側邊選單用:只回傳目前有上架商品的分類,依標準清單順序排列。"""
    used = set()
    for r in db.query("SELECT DISTINCT category FROM package WHERE is_active = 1"):
        used.add(r["category"])
    for r in db.query(
            "SELECT DISTINCT c.category FROM package_categories c"
            " JOIN package k ON k.id = c.package_id AND k.is_active = 1"):
        used.add(r["category"])
    ordered = [c for c in config.PACKAGE_CATEGORIES if c in used]
    ordered += sorted(c for c in used if c not in config.PACKAGE_CATEGORIES)
    return jsonify({"categories": ordered})


@shop_bp.get("/packages")
def list_packages():
    category = request.args.get("category")
    packages = db.query(
        f"SELECT {PACKAGE_FIELDS} FROM package WHERE is_active = 1 ORDER BY sort_order, id")

    secondary = _secondary_categories()
    contents = db.query(
        "SELECT m.package_id, p.name, p.unit, m.quantity FROM package_products_map m"
        " JOIN products p ON p.id = m.product_id ORDER BY m.id")
    by_pkg = {}
    for c in contents:
        by_pkg.setdefault(c["package_id"], []).append(
            {"name": c["name"], "unit": c["unit"], "quantity": float(c["quantity"])})

    for p in packages:
        p["items"] = by_pkg.get(p["id"], [])
        p["secondary_categories"] = secondary.get(p["id"], [])
        # categories = 主要 + 次要,前台用來判斷是否屬於某個系列
        p["categories"] = [p["category"]] + p["secondary_categories"]

    if category and category != "全部商品":
        packages = [p for p in packages if category in p["categories"]]
    return jsonify({"packages": packages})


def _fetch_package(pkg_id):
    """從 package 表撈單一禮盒基本資料 + 內容物,查無或已下架回傳 None。"""
    row = db.query_one(
        f"SELECT {PACKAGE_FIELDS} FROM package WHERE id = %s AND is_active = 1", (pkg_id,))
    if not row:
        return None
    row["items"] = [
        {"name": c["name"], "unit": c["unit"], "quantity": float(c["quantity"])}
        for c in db.query(
            "SELECT p.name, p.unit, m.quantity FROM package_products_map m"
            " JOIN products p ON p.id = m.product_id WHERE m.package_id = %s ORDER BY m.id", (pkg_id,))
    ]
    row["secondary_categories"] = _secondary_categories({pkg_id}).get(pkg_id, [])
    row["categories"] = [row["category"]] + row["secondary_categories"]
    return row


@shop_bp.get("/package")
def get_package_by_param():
    """商品介紹頁用:GET /api/package?product_id=<id>"""
    try:
        pkg_id = int(request.args.get("product_id", ""))
    except ValueError:
        return jsonify({"error": "請提供有效的 product_id"}), 400
    row = _fetch_package(pkg_id)
    if not row:
        return jsonify({"error": "查無此商品或已下架"}), 404
    return jsonify(row)


@shop_bp.get("/packages/<int:pkg_id>")
def get_package(pkg_id):
    row = _fetch_package(pkg_id)
    if not row:
        return jsonify({"error": "package not found"}), 404
    return jsonify(row)


def _gen_order_no():
    """MS + yyyymmddHHMMSS + 4 碼亂數 = 20 碼,剛好符合綠界 MerchantTradeNo 上限。"""
    suffix = "".join(random.choices(string.digits, k=4))
    return "MS" + datetime.now().strftime("%Y%m%d%H%M%S") + suffix


# 前台可選的配送方式 (pickup 僅保留給舊訂單,不再開放下單)
SHIPPING_METHODS = ("delivery", "fami", "unimart")
CVS_METHODS = ("fami", "unimart")
PAYMENT_METHODS = ("credit", "transfer")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _shipping_fee(shipping_method: str, subtotal: int) -> int:
    if subtotal >= config.FREE_SHIPPING_THRESHOLD:
        return 0
    if shipping_method == "delivery":
        return config.SHIPPING_FEE
    if shipping_method in CVS_METHODS:
        return config.CVS_SHIPPING_FEE
    return 0


@shop_bp.post("/orders")
def create_order():
    data = request.get_json(silent=True) or {}
    customer = data.get("customer") or {}
    items = data.get("items") or []

    # --- 基本驗證 ---
    name = (customer.get("name") or "").strip()
    phone = (customer.get("phone") or "").strip()
    email = (customer.get("email") or "").strip()
    address = (customer.get("address") or "").strip()
    store_id = (customer.get("store_id") or "").strip()[:20]
    store_name = (customer.get("store_name") or "").strip()[:60]
    shipping_method = data.get("shipping_method", "delivery")
    payment_method = data.get("payment_method", "credit")

    if not name or not phone:
        return jsonify({"error": "請填寫姓名與電話"}), 400
    if not EMAIL_RE.match(email):
        return jsonify({"error": "請填寫正確的 Email,訂單成立與付款通知會寄到此信箱"}), 400
    if shipping_method not in SHIPPING_METHODS or payment_method not in PAYMENT_METHODS:
        return jsonify({"error": "配送或付款方式不正確"}), 400
    if shipping_method == "delivery" and not address:
        return jsonify({"error": "宅配請填寫收件地址"}), 400
    if shipping_method in CVS_METHODS and not store_name:
        return jsonify({"error": "店到店請填寫取件門市"}), 400
    if not items:
        return jsonify({"error": "購物車是空的"}), 400

    # --- 以資料庫價格重新計算 ---
    order_items = []
    subtotal = 0
    for item in items:
        try:
            pkg_id = int(item.get("package_id"))
            qty = int(item.get("quantity"))
        except (TypeError, ValueError):
            return jsonify({"error": "商品格式不正確"}), 400
        if qty <= 0 or qty > 99:
            return jsonify({"error": "商品數量不正確"}), 400
        pkg = db.query_one(
            "SELECT id, name, price FROM package WHERE id = %s AND is_active = 1", (pkg_id,))
        if not pkg:
            return jsonify({"error": f"禮盒 {pkg_id} 不存在或已下架"}), 400
        line_total = pkg["price"] * qty
        subtotal += line_total
        order_items.append((pkg["id"], pkg["name"], pkg["price"], qty, line_total))

    shipping_fee = _shipping_fee(shipping_method, subtotal)
    total = subtotal + shipping_fee

    # --- 寫入訂單 (單一交易) ---
    order_no = _gen_order_no()
    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO orders
                   (order_no, customer_name, phone, email, address, store_id, store_name,
                    shipping_method, payment_method, subtotal, shipping_fee, total, note)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (order_no, name, phone, email, address, store_id, store_name,
                 shipping_method, payment_method, subtotal, shipping_fee, total,
                 (data.get("note") or "")[:255]),
            )
            order_id = cur.lastrowid
            cur.executemany(
                """INSERT INTO order_items
                   (order_id, package_id, package_name, unit_price, quantity, subtotal)
                   VALUES (%s,%s,%s,%s,%s,%s)""",
                [(order_id, *row) for row in order_items],
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    order = {
        "order_no": order_no, "customer_name": name, "phone": phone, "email": email,
        "address": address, "store_name": store_name, "shipping_method": shipping_method,
        "payment_method": payment_method, "subtotal": subtotal,
        "shipping_fee": shipping_fee, "total": total,
    }
    # --- 訂單成立通知信 (背景寄送,SMTP 失敗不影響下單) ---
    mailer.send_order_created(order, order_items)

    # --- 綠界付款表單:前台收到後自動 POST 到綠界付款頁 ---
    checkout = ecpay.build_checkout(order_no, total, payment_method, order_items, shipping_fee)

    return jsonify({
        "order_no": order_no,
        "subtotal": subtotal,
        "shipping_fee": shipping_fee,
        "total": total,
        "payment": checkout,
    }), 201


@shop_bp.get("/orders/<order_no>")
def get_order(order_no):
    """訂單查詢 (顧客憑訂單編號+電話查詢)。"""
    phone = request.args.get("phone", "")
    order = db.query_one(
        "SELECT order_no, customer_name, shipping_method, payment_method, payment_status,"
        " status, subtotal, shipping_fee, total, created_at FROM orders"
        " WHERE order_no = %s AND phone = %s",
        (order_no, phone),
    )
    if not order:
        return jsonify({"error": "查無訂單"}), 404
    order["created_at"] = order["created_at"].strftime("%Y-%m-%d %H:%M")
    order["items"] = db.query(
        "SELECT oi.package_name, oi.unit_price, oi.quantity, oi.subtotal FROM order_items oi"
        " JOIN orders o ON o.id = oi.order_id WHERE o.order_no = %s",
        (order_no,),
    )
    return jsonify(order)
