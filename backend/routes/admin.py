"""後台管理 API:登入 (JWT)、儀表板統計、訂單管理。"""
from datetime import datetime, timedelta, date
from functools import wraps

import jwt
from flask import Blueprint, jsonify, request
from werkzeug.security import check_password_hash

import config
import db

admin_bp = Blueprint("admin", __name__)

STATUS_LABELS = {
    "pending": "待處理",
    "paid": "已付款",
    "shipped": "已出貨",
    "completed": "已完成",
    "cancelled": "已取消",
}


ROLE_LABELS = {
    "super": "超級管理員",
    "order": "訂單管理員",
    "finance": "財務管理員",
    "staff": "一般管理員",
}


def _make_token(admin):
    payload = {
        "sub": str(admin["id"]),
        "username": admin["username"],
        "display_name": admin["display_name"],
        "role": admin.get("role") or "staff",
        "exp": datetime.utcnow() + timedelta(hours=config.JWT_EXPIRE_HOURS),
    }
    return jwt.encode(payload, config.SECRET_KEY, algorithm="HS256")


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return jsonify({"error": "unauthorized"}), 401
        try:
            request.admin = jwt.decode(auth[7:], config.SECRET_KEY, algorithms=["HS256"])
        except jwt.PyJWTError:
            return jsonify({"error": "token invalid or expired"}), 401
        return fn(*args, **kwargs)
    return wrapper


def role_required(*roles):
    """限制僅特定角色可操作 (super 永遠可以)。"""
    def deco(fn):
        @wraps(fn)
        @login_required
        def wrapper(*args, **kwargs):
            role = request.admin.get("role", "staff")
            if role != "super" and role not in roles:
                return jsonify({"error": "權限不足"}), 403
            return fn(*args, **kwargs)
        return wrapper
    return deco


@admin_bp.post("/login")
def login():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    admin = db.query_one("SELECT * FROM admins WHERE username = %s", (username,))
    if not admin or not check_password_hash(admin["password_hash"], password):
        return jsonify({"error": "帳號或密碼錯誤"}), 401
    if not admin.get("is_active", 1):
        return jsonify({"error": "此帳號已被停用"}), 403
    db.execute("UPDATE admins SET last_login = NOW() WHERE id = %s", (admin["id"],))
    return jsonify({
        "token": _make_token(admin),
        "display_name": admin["display_name"],
        "username": admin["username"],
        "role": admin.get("role") or "staff",
        "role_label": ROLE_LABELS.get(admin.get("role") or "staff", "管理員"),
    })


@admin_bp.get("/dashboard")
@login_required
def dashboard():
    today = date.today()
    revenue_today = db.query_one(
        "SELECT COALESCE(SUM(total),0) AS v FROM orders"
        " WHERE DATE(created_at) = %s AND status != 'cancelled'", (today,))["v"]
    orders_today = db.query_one(
        "SELECT COUNT(*) AS v FROM orders WHERE DATE(created_at) = %s", (today,))["v"]
    pending = db.query_one(
        "SELECT COUNT(*) AS v FROM orders WHERE status = 'pending'")["v"]
    total_orders = db.query_one("SELECT COUNT(*) AS v FROM orders")["v"]

    best = db.query_one(
        "SELECT oi.package_name AS product_name, SUM(oi.quantity) AS qty, k.image"
        " FROM order_items oi"
        " JOIN orders o ON o.id = oi.order_id AND o.status != 'cancelled'"
        " LEFT JOIN package k ON k.id = oi.package_id"
        " GROUP BY oi.package_name, k.image ORDER BY qty DESC LIMIT 1")

    # 近 7 日銷售趨勢
    trend = db.query(
        "SELECT DATE(created_at) AS d, COALESCE(SUM(total),0) AS v FROM orders"
        " WHERE created_at >= %s AND status != 'cancelled'"
        " GROUP BY DATE(created_at) ORDER BY d",
        (today - timedelta(days=6),))
    trend_map = {row["d"].strftime("%Y-%m-%d"): int(row["v"]) for row in trend}
    days = [(today - timedelta(days=i)) for i in range(6, -1, -1)]
    trend_series = [
        {"date": d.strftime("%m/%d"), "value": trend_map.get(d.strftime("%Y-%m-%d"), 0)}
        for d in days
    ]

    recent = db.query(
        "SELECT order_no, customer_name, total, status, created_at FROM orders"
        " ORDER BY id DESC LIMIT 5")
    for r in recent:
        r["created_at"] = r["created_at"].strftime("%Y-%m-%d %H:%M")
        r["status_label"] = STATUS_LABELS.get(r["status"], r["status"])

    return jsonify({
        "revenue_today": int(revenue_today),
        "orders_today": orders_today,
        "pending_orders": pending,
        "total_orders": total_orders,
        "best_seller": best,
        "trend": trend_series,
        "recent_orders": recent,
    })


@admin_bp.get("/orders")
@login_required
def list_orders():
    page = max(int(request.args.get("page", 1)), 1)
    per_page = min(int(request.args.get("per_page", 10)), 50)
    status = request.args.get("status", "")
    q = (request.args.get("q") or "").strip()

    where, args = [], []
    if status and status in STATUS_LABELS:
        where.append("status = %s")
        args.append(status)
    if q:
        where.append("(order_no LIKE %s OR customer_name LIKE %s OR phone LIKE %s)")
        args += [f"%{q}%"] * 3
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""

    total = db.query_one(f"SELECT COUNT(*) AS c FROM orders{where_sql}", args)["c"]
    rows = db.query(
        f"SELECT id, order_no, customer_name, phone, total, status, payment_status,"
        f" payment_method, shipping_method, created_at FROM orders{where_sql}"
        f" ORDER BY id DESC LIMIT %s OFFSET %s",
        args + [per_page, (page - 1) * per_page],
    )
    for r in rows:
        r["created_at"] = r["created_at"].strftime("%Y-%m-%d %H:%M")
        r["status_label"] = STATUS_LABELS.get(r["status"], r["status"])
    return jsonify({"orders": rows, "total": total, "page": page, "per_page": per_page})


@admin_bp.get("/orders/<int:order_id>")
@login_required
def order_detail(order_id):
    order = db.query_one("SELECT * FROM orders WHERE id = %s", (order_id,))
    if not order:
        return jsonify({"error": "查無訂單"}), 404
    order["created_at"] = order["created_at"].strftime("%Y-%m-%d %H:%M")
    order["status_label"] = STATUS_LABELS.get(order["status"], order["status"])
    order["items"] = db.query(
        "SELECT package_name AS product_name, unit_price, quantity, subtotal"
        " FROM order_items WHERE order_id = %s",
        (order_id,))
    return jsonify(order)


@admin_bp.patch("/orders/<int:order_id>/status")
@login_required
def update_status(order_id):
    data = request.get_json(silent=True) or {}
    status = data.get("status", "")
    if status not in STATUS_LABELS:
        return jsonify({"error": "狀態不正確"}), 400
    if not db.query_one("SELECT id FROM orders WHERE id = %s", (order_id,)):
        return jsonify({"error": "查無訂單"}), 404
    db.execute("UPDATE orders SET status = %s WHERE id = %s", (status, order_id))
    if status == "paid":
        db.execute("UPDATE orders SET payment_status = 'paid' WHERE id = %s", (order_id,))
    return jsonify({"id": order_id, "status": status, "status_label": STATUS_LABELS[status]})
