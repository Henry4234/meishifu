"""前台公開 API:商品查詢、建立訂單。

價格一律以資料庫為準重新計算,不信任前端傳來的金額。
"""
import random
import string
from datetime import datetime

from flask import Blueprint, jsonify, request

import config
import db
from routes.payment import build_payment_payload

shop_bp = Blueprint("shop", __name__)


@shop_bp.get("/products")
def list_products():
    category = request.args.get("category")
    sql = "SELECT id, name, description, spec, category, price, image, tag FROM products WHERE is_active = 1"
    args = []
    if category and category != "全部商品":
        sql += " AND category = %s"
        args.append(category)
    sql += " ORDER BY id"
    return jsonify({"products": db.query(sql, args)})


@shop_bp.get("/products/<int:pid>")
def get_product(pid):
    row = db.query_one(
        "SELECT id, name, description, spec, category, price, image, tag FROM products WHERE id = %s AND is_active = 1",
        (pid,),
    )
    if not row:
        return jsonify({"error": "product not found"}), 404
    return jsonify(row)


def _gen_order_no():
    suffix = "".join(random.choices(string.digits, k=4))
    return "MS" + datetime.now().strftime("%Y%m%d%H%M%S") + suffix


@shop_bp.post("/orders")
def create_order():
    data = request.get_json(silent=True) or {}
    customer = data.get("customer") or {}
    items = data.get("items") or []

    # --- 基本驗證 ---
    name = (customer.get("name") or "").strip()
    phone = (customer.get("phone") or "").strip()
    address = (customer.get("address") or "").strip()
    shipping_method = data.get("shipping_method", "delivery")
    payment_method = data.get("payment_method", "credit")

    if not name or not phone:
        return jsonify({"error": "請填寫姓名與電話"}), 400
    if shipping_method == "delivery" and not address:
        return jsonify({"error": "宅配請填寫收件地址"}), 400
    if shipping_method not in ("delivery", "pickup") or payment_method not in ("credit", "transfer"):
        return jsonify({"error": "配送或付款方式不正確"}), 400
    if not items:
        return jsonify({"error": "購物車是空的"}), 400

    # --- 以資料庫價格重新計算 ---
    order_items = []
    subtotal = 0
    for item in items:
        try:
            pid = int(item.get("product_id"))
            qty = int(item.get("quantity"))
        except (TypeError, ValueError):
            return jsonify({"error": "商品格式不正確"}), 400
        if qty <= 0 or qty > 99:
            return jsonify({"error": "商品數量不正確"}), 400
        product = db.query_one(
            "SELECT id, name, price FROM products WHERE id = %s AND is_active = 1", (pid,)
        )
        if not product:
            return jsonify({"error": f"商品 {pid} 不存在或已下架"}), 400
        line_total = product["price"] * qty
        subtotal += line_total
        order_items.append((product["id"], product["name"], product["price"], qty, line_total))

    shipping_fee = 0
    if shipping_method == "delivery" and subtotal < config.FREE_SHIPPING_THRESHOLD:
        shipping_fee = config.SHIPPING_FEE
    total = subtotal + shipping_fee

    # --- 寫入訂單 (單一交易) ---
    order_no = _gen_order_no()
    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO orders
                   (order_no, customer_name, phone, address, shipping_method,
                    payment_method, subtotal, shipping_fee, total, note)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (order_no, name, phone, address, shipping_method,
                 payment_method, subtotal, shipping_fee, total,
                 (data.get("note") or "")[:255]),
            )
            order_id = cur.lastrowid
            cur.executemany(
                """INSERT INTO order_items
                   (order_id, product_id, product_name, unit_price, quantity, subtotal)
                   VALUES (%s,%s,%s,%s,%s,%s)""",
                [(order_id, *row) for row in order_items],
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    # --- 金流對接 (預留) ---
    payment = build_payment_payload(order_no, total, payment_method)

    return jsonify({
        "order_no": order_no,
        "subtotal": subtotal,
        "shipping_fee": shipping_fee,
        "total": total,
        "payment": payment,
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
        "SELECT oi.product_name, oi.unit_price, oi.quantity, oi.subtotal FROM order_items oi"
        " JOIN orders o ON o.id = oi.order_id WHERE o.order_no = %s",
        (order_no,),
    )
    return jsonify(order)
