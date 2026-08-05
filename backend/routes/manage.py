"""後台管理 API:材料管理、產品管理 (含圖片上傳)、用戶權限、財務統計。"""
import re
import time
from datetime import date, timedelta
from pathlib import Path

from flask import Blueprint, jsonify, request
from werkzeug.security import generate_password_hash
from werkzeug.utils import secure_filename

import db
from routes.admin import ROLE_LABELS, login_required, role_required

manage_bp = Blueprint("manage", __name__)

# 上傳圖片存到 website/assets/uploads,由前端靜態伺服器直接提供
UPLOAD_DIR = Path(__file__).resolve().parent.parent.parent / "assets" / "uploads"
ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

ACTIVE_ORDER_STATUSES = ("pending", "paid")  # 尚未出貨 → 仍會消耗材料


# ---------------------------------------------------------------- 材料管理
def _material_demand():
    """依未出貨訂單 × 商品配方,計算每項材料的預估需求量。"""
    rows = db.query(
        "SELECT pm.material_id, SUM(oi.quantity * pm.quantity) AS demand"
        " FROM order_items oi"
        " JOIN orders o ON o.id = oi.order_id AND o.status IN %s"
        " JOIN product_materials pm ON pm.product_id = oi.product_id"
        " GROUP BY pm.material_id",
        (ACTIVE_ORDER_STATUSES,),
    )
    return {r["material_id"]: float(r["demand"]) for r in rows}


def _material_status(stock, safety, demand):
    if stock < demand or (safety > 0 and stock < safety * 0.5):
        return "shortage"
    if stock < safety:
        return "low"
    return "ok"


@manage_bp.get("/materials")
@login_required
def list_materials():
    demand = _material_demand()
    rows = db.query(
        "SELECT id, name, category, batch_no, unit, stock, safety_stock, unit_cost,"
        " expiry_date, created_at FROM materials WHERE is_active = 1 ORDER BY id")
    today = date.today()
    expiring_soon = 0
    for r in rows:
        r["stock"] = float(r["stock"])
        r["safety_stock"] = float(r["safety_stock"])
        r["unit_cost"] = float(r["unit_cost"])
        r["demand"] = round(demand.get(r["id"], 0), 2)
        r["status"] = _material_status(r["stock"], r["safety_stock"], r["demand"])
        if r["expiry_date"]:
            if r["expiry_date"] <= today + timedelta(days=30):
                expiring_soon += 1
            r["expiry_date"] = r["expiry_date"].strftime("%Y-%m-%d")
        r["created_at"] = r["created_at"].strftime("%Y-%m-%d")
    return jsonify({
        "materials": rows,
        "stats": {
            "total": len(rows),
            "shortage": sum(1 for r in rows if r["status"] == "shortage"),
            "expiring": expiring_soon,
        },
    })


@manage_bp.post("/materials")
@login_required
def create_material():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "請填寫材料名稱"}), 400
    mid = db.execute(
        "INSERT INTO materials (name, category, batch_no, unit, stock, safety_stock, unit_cost, expiry_date)"
        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        (name, data.get("category") or "其他", data.get("batch_no") or "",
         data.get("unit") or "kg", data.get("stock") or 0,
         data.get("safety_stock") or 0, data.get("unit_cost") or 0,
         data.get("expiry_date") or None),
    )
    return jsonify({"id": mid}), 201


@manage_bp.patch("/materials/<int:mid>")
@login_required
def update_material(mid):
    data = request.get_json(silent=True) or {}
    fields, args = [], []
    for col in ("name", "category", "batch_no", "unit", "stock", "safety_stock", "unit_cost", "expiry_date"):
        if col in data:
            fields.append(f"{col} = %s")
            args.append(data[col] or (None if col == "expiry_date" else data[col]))
    if not fields:
        return jsonify({"error": "沒有要更新的欄位"}), 400
    if not db.query_one("SELECT id FROM materials WHERE id = %s", (mid,)):
        return jsonify({"error": "查無材料"}), 404
    db.execute(f"UPDATE materials SET {', '.join(fields)} WHERE id = %s", args + [mid])
    if "stock" in data:
        db.execute(
            "INSERT INTO material_logs (material_id, type, quantity, note) VALUES (%s,'adjust',%s,%s)",
            (mid, data["stock"], "盤點調整"))
    return jsonify({"id": mid, "updated": True})


@manage_bp.post("/materials/<int:mid>/purchase")
@login_required
def purchase_material(mid):
    """採購入庫:增加庫存並記錄採購成本 (單位成本以最新進價更新)。"""
    data = request.get_json(silent=True) or {}
    try:
        qty = float(data.get("quantity"))
    except (TypeError, ValueError):
        return jsonify({"error": "數量不正確"}), 400
    if qty <= 0:
        return jsonify({"error": "數量必須大於 0"}), 400
    m = db.query_one("SELECT id, unit_cost FROM materials WHERE id = %s", (mid,))
    if not m:
        return jsonify({"error": "查無材料"}), 404
    unit_cost = data.get("unit_cost")
    if unit_cost in (None, ""):
        unit_cost = float(m["unit_cost"])
    db.execute("UPDATE materials SET stock = stock + %s, unit_cost = %s WHERE id = %s",
               (qty, unit_cost, mid))
    db.execute(
        "INSERT INTO material_logs (material_id, type, quantity, unit_cost, note) VALUES (%s,'purchase',%s,%s,%s)",
        (mid, qty, unit_cost, (data.get("note") or "採購入庫")[:255]))
    row = db.query_one("SELECT stock, unit_cost FROM materials WHERE id = %s", (mid,))
    return jsonify({"id": mid, "stock": float(row["stock"]), "unit_cost": float(row["unit_cost"])})


@manage_bp.delete("/materials/<int:mid>")
@login_required
def delete_material(mid):
    db.execute("UPDATE materials SET is_active = 0 WHERE id = %s", (mid,))
    return jsonify({"id": mid, "deleted": True})


# ---------------------------------------------------------------- 產品管理
def _product_costs():
    """每項商品的材料成本 = Σ(配方用量 × 材料單位成本)。"""
    rows = db.query(
        "SELECT pm.product_id, SUM(pm.quantity * m.unit_cost) AS cost"
        " FROM product_materials pm JOIN materials m ON m.id = pm.material_id"
        " GROUP BY pm.product_id")
    return {r["product_id"]: float(r["cost"]) for r in rows}


@manage_bp.get("/products")
@login_required
def admin_list_products():
    costs = _product_costs()
    rows = db.query(
        "SELECT id, name, description, spec, category, price, image, tag, is_active, created_at"
        " FROM products ORDER BY id DESC")
    for r in rows:
        r["created_at"] = r["created_at"].strftime("%Y-%m-%d")
        r["material_cost"] = round(costs.get(r["id"], 0), 1)
    return jsonify({"products": rows})


def _save_upload(file):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXT:
        raise ValueError("僅接受 jpg / png / webp / gif 圖片")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    base = secure_filename(Path(file.filename).stem) or "product"
    base = re.sub(r"[^A-Za-z0-9_-]", "", base)[:40] or "product"
    filename = f"{base}_{int(time.time())}{ext}"
    file.save(UPLOAD_DIR / filename)
    return f"/assets/uploads/{filename}"


@manage_bp.post("/products")
@login_required
def admin_create_product():
    # multipart form:文字欄位 + image 檔案
    form = request.form
    name = (form.get("name") or "").strip()
    try:
        price = int(form.get("price", ""))
    except ValueError:
        return jsonify({"error": "價格必須為整數"}), 400
    if not name or price < 0:
        return jsonify({"error": "請填寫商品名稱與價格"}), 400

    image_path = ""
    if "image" in request.files and request.files["image"].filename:
        try:
            image_path = _save_upload(request.files["image"])
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

    pid = db.execute(
        "INSERT INTO products (name, description, spec, category, price, image, tag, is_active)"
        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        (name, form.get("description") or "", form.get("spec") or "",
         form.get("category") or "其他", price, image_path, form.get("tag") or "",
         1 if form.get("is_active", "1") in ("1", "true", "on") else 0),
    )
    return jsonify({"id": pid, "image": image_path}), 201


@manage_bp.post("/products/<int:pid>/update")
@login_required
def admin_update_product(pid):
    """更新商品 (multipart;image 為選填,有附檔才更換圖片)。"""
    if not db.query_one("SELECT id FROM products WHERE id = %s", (pid,)):
        return jsonify({"error": "查無商品"}), 404
    form = request.form
    fields, args = [], []
    for col in ("name", "description", "spec", "category", "tag"):
        if col in form:
            fields.append(f"{col} = %s")
            args.append(form.get(col))
    if "price" in form:
        try:
            fields.append("price = %s")
            args.append(int(form.get("price")))
        except ValueError:
            return jsonify({"error": "價格必須為整數"}), 400
    if "is_active" in form:
        fields.append("is_active = %s")
        args.append(1 if form.get("is_active") in ("1", "true", "on") else 0)
    if "image" in request.files and request.files["image"].filename:
        try:
            fields.append("image = %s")
            args.append(_save_upload(request.files["image"]))
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
    if not fields:
        return jsonify({"error": "沒有要更新的欄位"}), 400
    db.execute(f"UPDATE products SET {', '.join(fields)} WHERE id = %s", args + [pid])
    return jsonify({"id": pid, "updated": True})


@manage_bp.patch("/products/<int:pid>/active")
@login_required
def toggle_product_active(pid):
    data = request.get_json(silent=True) or {}
    active = 1 if data.get("is_active") else 0
    if not db.query_one("SELECT id FROM products WHERE id = %s", (pid,)):
        return jsonify({"error": "查無商品"}), 404
    db.execute("UPDATE products SET is_active = %s WHERE id = %s", (active, pid))
    return jsonify({"id": pid, "is_active": active})


# ---------------------------------------------------------------- 用戶權限管理
@manage_bp.get("/users")
@role_required("super")
def list_users():
    rows = db.query(
        "SELECT id, username, display_name, email, role, is_active, last_login, created_at"
        " FROM admins ORDER BY id")
    for r in rows:
        r["role"] = r["role"] or "staff"
        r["role_label"] = ROLE_LABELS.get(r["role"], "管理員")
        r["last_login"] = r["last_login"].strftime("%Y-%m-%d %H:%M") if r["last_login"] else None
        r["created_at"] = r["created_at"].strftime("%Y-%m-%d")
    return jsonify({
        "users": rows,
        "stats": {
            "total": len(rows),
            "active": sum(1 for r in rows if r["is_active"]),
            "disabled": sum(1 for r in rows if not r["is_active"]),
        },
    })


@manage_bp.post("/users")
@role_required("super")
def create_user():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if not username or len(password) < 8:
        return jsonify({"error": "請填寫帳號,密碼至少 8 碼"}), 400
    if data.get("role") not in ROLE_LABELS:
        return jsonify({"error": "角色不正確"}), 400
    if db.query_one("SELECT id FROM admins WHERE username = %s", (username,)):
        return jsonify({"error": "帳號已存在"}), 400
    uid = db.execute(
        "INSERT INTO admins (username, password_hash, display_name, email, role) VALUES (%s,%s,%s,%s,%s)",
        (username, generate_password_hash(password),
         data.get("display_name") or username, data.get("email") or "", data["role"]),
    )
    return jsonify({"id": uid}), 201


@manage_bp.patch("/users/<int:uid>")
@role_required("super")
def update_user(uid):
    data = request.get_json(silent=True) or {}
    target = db.query_one("SELECT id, username FROM admins WHERE id = %s", (uid,))
    if not target:
        return jsonify({"error": "查無用戶"}), 404
    fields, args = [], []
    if "display_name" in data:
        fields.append("display_name = %s"); args.append(data["display_name"])
    if "email" in data:
        fields.append("email = %s"); args.append(data["email"])
    if data.get("role"):
        if data["role"] not in ROLE_LABELS:
            return jsonify({"error": "角色不正確"}), 400
        fields.append("role = %s"); args.append(data["role"])
    if "is_active" in data:
        # 避免把自己停權
        if str(uid) == request.admin.get("sub") and not data["is_active"]:
            return jsonify({"error": "不能停用自己的帳號"}), 400
        fields.append("is_active = %s"); args.append(1 if data["is_active"] else 0)
    if data.get("password"):
        if len(data["password"]) < 8:
            return jsonify({"error": "密碼至少 8 碼"}), 400
        fields.append("password_hash = %s"); args.append(generate_password_hash(data["password"]))
    if not fields:
        return jsonify({"error": "沒有要更新的欄位"}), 400
    db.execute(f"UPDATE admins SET {', '.join(fields)} WHERE id = %s", args + [uid])
    return jsonify({"id": uid, "updated": True})


# ---------------------------------------------------------------- 財務管理
PERIODS = {"month": 30, "quarter": 90, "year": 365}


@manage_bp.get("/finance")
@role_required("finance")
def finance_summary():
    days = PERIODS.get(request.args.get("period", "month"), 30)
    since = date.today() - timedelta(days=days)
    costs = _product_costs()

    revenue = db.query_one(
        "SELECT COALESCE(SUM(total),0) AS v FROM orders"
        " WHERE created_at >= %s AND status != 'cancelled'", (since,))["v"]
    order_count = db.query_one(
        "SELECT COUNT(*) AS v FROM orders WHERE created_at >= %s AND status != 'cancelled'",
        (since,))["v"]

    # 材料成本 = Σ(訂單品項數量 × 該商品配方成本)
    items = db.query(
        "SELECT oi.product_id, SUM(oi.quantity) AS qty FROM order_items oi"
        " JOIN orders o ON o.id = oi.order_id"
        " WHERE o.created_at >= %s AND o.status != 'cancelled'"
        " GROUP BY oi.product_id", (since,))
    cost = sum(costs.get(r["product_id"], 0) * float(r["qty"]) for r in items)

    revenue = int(revenue)
    cost = round(cost)
    profit = revenue - cost
    margin = round(profit / revenue * 100, 1) if revenue else 0

    # 近 4 週明細
    weeks = []
    today = date.today()
    for i in range(4):
        # end 為區間隔日 0 點,確保「本週」含今天整天
        end = today + timedelta(days=1) - timedelta(days=7 * i)
        start = end - timedelta(days=7)
        row = db.query_one(
            "SELECT COUNT(*) AS cnt, COALESCE(SUM(total),0) AS rev FROM orders"
            " WHERE created_at >= %s AND created_at < %s AND status != 'cancelled'",
            (start, end))
        witems = db.query(
            "SELECT oi.product_id, SUM(oi.quantity) AS qty FROM order_items oi"
            " JOIN orders o ON o.id = oi.order_id"
            " WHERE o.created_at >= %s AND o.created_at < %s AND o.status != 'cancelled'"
            " GROUP BY oi.product_id", (start, end))
        wcost = sum(costs.get(r["product_id"], 0) * float(r["qty"]) for r in witems)
        wrev = int(row["rev"])
        weeks.append({
            "label": ["本週", "上週", "兩週前", "三週前"][i],
            "orders": row["cnt"],
            "avg_order": round(wrev / row["cnt"]) if row["cnt"] else 0,
            "revenue": wrev,
            "margin": round((wrev - wcost) / wrev * 100, 1) if wrev else 0,
        })

    return jsonify({
        "revenue": revenue,
        "cost": cost,
        "profit": profit,
        "margin": margin,
        "order_count": order_count,
        "weeks": weeks,
    })
