"""後台管理 API:材料、單一產品與配方、禮盒 (含圖片上傳)、用戶權限、財務統計。

成本結構 (三層):
    材料 unit_cost → 單品成本 Σ(配方用量 × 材料單價)
                   → 禮盒成本 Σ(內容物入數 × 單品成本) + 包材成本
"""
import random
import string
from datetime import date, datetime, timedelta

from flask import Blueprint, jsonify, request
from werkzeug.security import generate_password_hash

import config
import db
from image_storage import save_upload
from routes.admin import ROLE_LABELS, login_required, role_required
from routes.shop import EMAIL_RE

manage_bp = Blueprint("manage", __name__)

ACTIVE_ORDER_STATUSES = ("pending", "paid")  # 尚未出貨 → 仍會消耗材料
UNFINISHED_ORDER_STATUSES = ("pending", "paid", "shipped")  # 尚未結案 → 商品仍在流程中


# ---------------------------------------------------------------- 成本計算
def product_costs():
    """單品材料成本 = Σ(配方用量 × 材料單位成本)。"""
    rows = db.query(
        "SELECT pm.product_id, SUM(pm.quantity * m.unit_cost) AS cost"
        " FROM product_materials pm JOIN materials m ON m.id = pm.material_id"
        " GROUP BY pm.product_id")
    return {r["product_id"]: float(r["cost"]) for r in rows}


def package_costs():
    """禮盒成本 = Σ(內容物入數 × 單品成本) + 包材成本。"""
    pcost = product_costs()
    costs = {}
    for r in db.query("SELECT package_id, product_id, quantity FROM package_products_map"):
        costs[r["package_id"]] = costs.get(r["package_id"], 0) + \
            pcost.get(r["product_id"], 0) * float(r["quantity"])
    for r in db.query(
            "SELECT k.id, k.packaging_qty, m.unit_cost FROM package k"
            " JOIN materials m ON m.id = k.packaging_material_id"):
        costs[r["id"]] = costs.get(r["id"], 0) + float(r["unit_cost"]) * float(r["packaging_qty"])
    return costs


def _material_demand():
    """依未出貨訂單 → 禮盒內容 → 單品配方,計算每項材料的預估需求量。
    包材另計 (每盒 packaging_qty)。"""
    demand = {}
    rows = db.query(
        "SELECT pm.material_id, SUM(oi.quantity * ppm.quantity * pm.quantity) AS d"
        " FROM order_items oi"
        " JOIN orders o ON o.id = oi.order_id AND o.status IN %s"
        " JOIN package_products_map ppm ON ppm.package_id = oi.package_id"
        " JOIN product_materials pm ON pm.product_id = ppm.product_id"
        " GROUP BY pm.material_id",
        (ACTIVE_ORDER_STATUSES,))
    for r in rows:
        demand[r["material_id"]] = float(r["d"])
    box = db.query(
        "SELECT k.packaging_material_id AS mid, SUM(oi.quantity * k.packaging_qty) AS d"
        " FROM order_items oi"
        " JOIN orders o ON o.id = oi.order_id AND o.status IN %s"
        " JOIN package k ON k.id = oi.package_id"
        " WHERE k.packaging_material_id IS NOT NULL"
        " GROUP BY k.packaging_material_id",
        (ACTIVE_ORDER_STATUSES,))
    for r in box:
        demand[r["mid"]] = demand.get(r["mid"], 0) + float(r["d"])
    return demand


# ---------------------------------------------------------------- 待出貨統計
@manage_bp.get("/fulfillment")
@login_required
def fulfillment_summary():
    """尚未出貨的訂單 (待處理 / 已付款) 要備哪些貨。

    兩個層級:
      packages — 每款禮盒各要出幾盒 (揀貨、包裝用)
      products — 換算成單一產品各要做幾個 (生產排程用),
                 數量 = Σ(禮盒訂購盒數 × 該禮盒的內容物入數)

    採用與材料需求相同的 ACTIVE_ORDER_STATUSES,兩邊口徑一致。
    """
    orders = db.query_one(
        "SELECT COUNT(*) AS c FROM orders WHERE status IN %s", (ACTIVE_ORDER_STATUSES,))["c"]

    # 禮盒:同時給出待處理 / 已付款的拆分,方便判斷哪些是已收款該優先做的
    rows = db.query(
        "SELECT oi.package_id,"
        " MAX(oi.package_name) AS snapshot_name,"
        " SUM(oi.quantity) AS qty,"
        " SUM(CASE WHEN o.status = 'pending' THEN oi.quantity ELSE 0 END) AS pending_qty,"
        " SUM(CASE WHEN o.status = 'paid' THEN oi.quantity ELSE 0 END) AS paid_qty"
        " FROM order_items oi"
        " JOIN orders o ON o.id = oi.order_id AND o.status IN %s"
        " GROUP BY oi.package_id ORDER BY qty DESC",
        (ACTIVE_ORDER_STATUSES,))

    # 禮盒可能已改名或下架,名稱以現行資料為準,查不到才用下單當下的快照
    current = {
        r["id"]: r for r in db.query("SELECT id, name, spec, image, is_active FROM package")}
    packages = []
    for r in rows:
        pkg = current.get(r["package_id"])
        packages.append({
            "package_id": r["package_id"],
            "name": (pkg or {}).get("name") or r["snapshot_name"],
            "spec": (pkg or {}).get("spec") or "",
            "image": (pkg or {}).get("image") or "",
            "is_active": bool((pkg or {}).get("is_active", 0)),
            "quantity": int(r["qty"]),
            "pending_quantity": int(r["pending_qty"]),
            "paid_quantity": int(r["paid_qty"]),
        })

    # 單一產品:禮盒盒數 × 內容物入數。禮盒若沒設定內容物就不會出現在這裡。
    products = [
        {
            "product_id": r["product_id"],
            "name": r["name"],
            "unit": r["unit"],
            "quantity": round(float(r["qty"]), 2),
        }
        for r in db.query(
            "SELECT ppm.product_id, p.name, p.unit,"
            " SUM(oi.quantity * ppm.quantity) AS qty"
            " FROM order_items oi"
            " JOIN orders o ON o.id = oi.order_id AND o.status IN %s"
            " JOIN package_products_map ppm ON ppm.package_id = oi.package_id"
            " JOIN products p ON p.id = ppm.product_id"
            " GROUP BY ppm.product_id, p.name, p.unit ORDER BY qty DESC",
            (ACTIVE_ORDER_STATUSES,))
    ]

    return jsonify({
        "statuses": list(ACTIVE_ORDER_STATUSES),
        "orders": orders,
        "packages": packages,
        "products": products,
        "total_packages": sum(p["quantity"] for p in packages),
        "total_products": round(sum(p["quantity"] for p in products), 2),
    })


# ---------------------------------------------------------------- 手動建立訂單
# 後台自行建立的內部訂單 (市集、親友、批發自取…)。不經綠界金流,只是把實際出貨
# 的禮盒記錄下來,讓它跟線上訂單一樣進入成本統計與材料需求計算。
MANUAL_SHIPPING_METHODS = ("pickup", "delivery")
MANUAL_PAYMENT_METHODS = ("cash", "transfer", "credit")
MANUAL_STATUSES = ("pending", "paid", "shipped", "completed")
MAX_MANUAL_ITEMS = 50


def _gen_manual_order_no():
    """MO + yyyymmddHHMMSS + 4 碼亂數。前綴與線上訂單 (MS) 區隔,
    一眼就能分辨,也不會和綠界的 MerchantTradeNo 撞號。"""
    return "MO" + datetime.now().strftime("%Y%m%d%H%M%S") + \
        "".join(random.choices(string.digits, k=4))


def _manual_items(raw):
    """驗證並以資料庫的禮盒資料組出訂單明細。回傳 (items, 小計) 或 (None, 錯誤訊息)。"""
    if not isinstance(raw, list) or not raw:
        return None, "請至少加入一項商品"
    if len(raw) > MAX_MANUAL_ITEMS:
        return None, f"單筆訂單最多 {MAX_MANUAL_ITEMS} 項商品"

    items, subtotal, seen = [], 0, set()
    for entry in raw:
        if not isinstance(entry, dict):
            return None, "商品格式不正確"
        try:
            pkg_id = int(entry.get("package_id"))
            qty = int(entry.get("quantity"))
        except (TypeError, ValueError):
            return None, "商品格式不正確"
        if qty <= 0 or qty > 999:
            return None, "商品數量須介於 1 至 999"
        if pkg_id in seen:
            return None, "同一項禮盒重複出現,請合併數量"
        seen.add(pkg_id)

        pkg = db.query_one("SELECT id, name, price FROM package WHERE id = %s", (pkg_id,))
        if not pkg:
            return None, f"禮盒 {pkg_id} 不存在"

        # 單價預設取資料庫售價;內部訂單常有批發或優惠價,允許改寫
        if entry.get("unit_price") in (None, ""):
            unit_price = int(pkg["price"])
        else:
            try:
                unit_price = int(entry["unit_price"])
            except (TypeError, ValueError):
                return None, "單價格式不正確"
            if unit_price < 0 or unit_price > 999999:
                return None, "單價須介於 0 至 999,999"

        line_total = unit_price * qty
        subtotal += line_total
        items.append((pkg["id"], pkg["name"], unit_price, qty, line_total))
    return items, subtotal


@manage_bp.post("/orders")
@role_required("order")
def create_manual_order():
    """後台手動建立內部訂單 (source = manual)。

    與綠界完全無關:不產生付款參數、不寄送訂單通知信。建立後即與線上訂單共用
    同一張 orders 表,因此會自動計入財務成本統計與材料需求。

    電話與 Email 為選填 (自取/親送常常沒有);線上訂單的必填規則不受影響,
    仍由 routes/shop.py 把關。
    """
    data = request.get_json(silent=True) or {}

    name = (data.get("customer_name") or "").strip()[:100]
    if not name:
        return jsonify({"error": "請填寫訂購人姓名"}), 400

    phone = (data.get("phone") or "").strip()[:30]
    email = (data.get("email") or "").strip()[:120]
    if email and not EMAIL_RE.match(email):
        return jsonify({"error": "Email 格式不正確 (可留空)"}), 400

    shipping_method = data.get("shipping_method") or "pickup"
    payment_method = data.get("payment_method") or "cash"
    status = data.get("status") or "pending"
    if shipping_method not in MANUAL_SHIPPING_METHODS:
        return jsonify({"error": "配送方式不正確"}), 400
    if payment_method not in MANUAL_PAYMENT_METHODS:
        return jsonify({"error": "付款方式不正確"}), 400
    if status not in MANUAL_STATUSES:
        return jsonify({"error": "訂單狀態不正確"}), 400

    address = (data.get("address") or "").strip()[:255]
    if shipping_method == "delivery" and not address:
        return jsonify({"error": "配送到府請填寫地址"}), 400

    items, subtotal = _manual_items(data.get("items"))
    if items is None:
        return jsonify({"error": subtotal}), 400

    try:
        shipping_fee = int(data.get("shipping_fee") or 0)
    except (TypeError, ValueError):
        return jsonify({"error": "運費格式不正確"}), 400
    if shipping_fee < 0 or shipping_fee > 99999:
        return jsonify({"error": "運費須介於 0 至 99,999"}), 400

    paid = bool(data.get("paid"))
    payment_status = "paid" if paid else "unpaid"
    total = subtotal + shipping_fee
    order_no = _gen_manual_order_no()

    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO orders
                   (order_no, source, customer_name, phone, email, address,
                    shipping_method, payment_method, payment_status, status,
                    subtotal, shipping_fee, total, note, paid_at)
                   VALUES (%s,'manual',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (order_no, name, phone, email, address,
                 shipping_method, payment_method, payment_status, status,
                 subtotal, shipping_fee, total,
                 (data.get("note") or "").strip()[:255],
                 datetime.now() if paid else None),
            )
            order_id = cur.lastrowid
            cur.executemany(
                """INSERT INTO order_items
                   (order_id, package_id, package_name, unit_price, quantity, subtotal)
                   VALUES (%s,%s,%s,%s,%s,%s)""",
                [(order_id, *row) for row in items],
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return jsonify({
        "id": order_id,
        "order_no": order_no,
        "source": "manual",
        "subtotal": subtotal,
        "shipping_fee": shipping_fee,
        "total": total,
        "status": status,
        "payment_status": payment_status,
    }), 201


# ---------------------------------------------------------------- 訂單刪除
@manage_bp.delete("/orders/<int:order_id>")
@login_required
def delete_order(order_id):
    """刪除訂單 (含明細)。

    只允許未付款、或訂單狀態仍是「待處理」的訂單刪除;已收款且已進入
    出貨流程的訂單必須保留,否則後台與金流端的紀錄會對不起來。
    """
    order = db.query_one(
        "SELECT id, order_no, customer_name, payment_status, status FROM orders"
        " WHERE id = %s", (order_id,))
    if not order:
        return jsonify({"error": "查無訂單"}), 404

    if order["payment_status"] != "unpaid" and order["status"] != "pending":
        return jsonify({
            "error": "只有未付款或待處理的訂單可以刪除,此訂單已付款且進入處理流程",
            "payment_status": order["payment_status"],
            "status": order["status"],
        }), 400

    with db.db_cursor(commit=True) as cur:
        # order_items 已有 ON DELETE CASCADE,這裡明確刪除以相容尚未套用外鍵的資料庫
        cur.execute("DELETE FROM order_items WHERE order_id = %s", (order_id,))
        cur.execute("DELETE FROM orders WHERE id = %s", (order_id,))

    return jsonify({
        "id": order_id, "order_no": order["order_no"],
        "customer_name": order["customer_name"], "deleted": True,
    })


# ---------------------------------------------------------------- 材料管理
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
    """停用材料 (soft delete)。仍被配方或禮盒包材使用時拒絕,
    避免已刪除的材料繼續默默計入成本。"""
    m = db.query_one("SELECT id, name FROM materials WHERE id = %s AND is_active = 1", (mid,))
    if not m:
        return jsonify({"error": "查無材料"}), 404

    used_by = [r["name"] for r in db.query(
        "SELECT p.name FROM product_materials pm JOIN products p ON p.id = pm.product_id"
        " WHERE pm.material_id = %s ORDER BY p.id", (mid,))]
    used_as_packaging = [r["name"] for r in db.query(
        "SELECT name FROM package WHERE packaging_material_id = %s ORDER BY id", (mid,))]

    if used_by or used_as_packaging:
        parts = []
        if used_by:
            parts.append("配方:" + "、".join(used_by))
        if used_as_packaging:
            parts.append("禮盒包材:" + "、".join(used_as_packaging))
        return jsonify({
            "error": f"「{m['name']}」仍被使用中,請先移除後再刪除 ({';'.join(parts)})",
            "used_by_products": used_by,
            "used_as_packaging": used_as_packaging,
        }), 400

    db.execute("UPDATE materials SET is_active = 0 WHERE id = %s", (mid,))
    return jsonify({"id": mid, "name": m["name"], "deleted": True})


# ---------------------------------------------------------------- 單一產品與配方
@manage_bp.get("/products")
@login_required
def list_products():
    costs = product_costs()
    rows = db.query(
        "SELECT id, name, description, category, unit, is_active FROM products ORDER BY id")
    boms = db.query(
        "SELECT pm.product_id, pm.material_id, pm.quantity, m.name, m.unit, m.unit_cost"
        " FROM product_materials pm JOIN materials m ON m.id = pm.material_id ORDER BY pm.id")
    by_product = {}
    for b in boms:
        by_product.setdefault(b["product_id"], []).append({
            "material_id": b["material_id"], "name": b["name"], "unit": b["unit"],
            "quantity": float(b["quantity"]),
            "cost": round(float(b["quantity"]) * float(b["unit_cost"]), 2),
        })
    for r in rows:
        r["materials"] = by_product.get(r["id"], [])
        r["cost"] = round(costs.get(r["id"], 0), 2)
    return jsonify({"products": rows})


@manage_bp.post("/products")
@login_required
def create_product():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "請填寫產品名稱"}), 400
    pid = db.execute(
        "INSERT INTO products (name, description, category, unit) VALUES (%s,%s,%s,%s)",
        (name, data.get("description") or "", data.get("category") or "其他",
         data.get("unit") or "顆"))
    _replace_bom(pid, data.get("materials"))
    return jsonify({"id": pid}), 201


@manage_bp.patch("/products/<int:pid>")
@login_required
def update_product(pid):
    if not db.query_one("SELECT id FROM products WHERE id = %s", (pid,)):
        return jsonify({"error": "查無產品"}), 404
    data = request.get_json(silent=True) or {}
    fields, args = [], []
    for col in ("name", "description", "category", "unit"):
        if col in data:
            fields.append(f"{col} = %s")
            args.append(data[col])
    if "is_active" in data:
        fields.append("is_active = %s")
        args.append(1 if data["is_active"] else 0)
    if fields:
        db.execute(f"UPDATE products SET {', '.join(fields)} WHERE id = %s", args + [pid])
    if data.get("materials") is not None:
        _replace_bom(pid, data["materials"])
    return jsonify({"id": pid, "updated": True})


def _replace_bom(pid, materials):
    """整批覆寫單品配方。materials = [{material_id, quantity}, ...]"""
    if not materials:
        return
    rows = []
    for m in materials:
        try:
            mid, qty = int(m["material_id"]), float(m["quantity"])
        except (KeyError, TypeError, ValueError):
            continue
        if qty > 0:
            rows.append((pid, mid, qty))
    db.execute("DELETE FROM product_materials WHERE product_id = %s", (pid,))
    if rows:
        with db.db_cursor(commit=True) as cur:
            cur.executemany(
                "INSERT INTO product_materials (product_id, material_id, quantity) VALUES (%s,%s,%s)",
                rows)


@manage_bp.delete("/products/<int:pid>")
@login_required
def delete_product(pid):
    used = db.query_one(
        "SELECT COUNT(*) AS c FROM package_products_map WHERE product_id = %s", (pid,))["c"]
    if used:
        return jsonify({"error": f"此產品仍被 {used} 個禮盒使用,請先移除禮盒內容"}), 400
    db.execute("DELETE FROM products WHERE id = %s", (pid,))
    return jsonify({"id": pid, "deleted": True})


# ---------------------------------------------------------------- 禮盒管理 (販售單位)
def _all_categories():
    """標準分類清單 ∪ 資料庫既有值 (避免舊資料的分類在後台選單消失)。"""
    used = set()
    for r in db.query("SELECT DISTINCT category FROM package"):
        if r["category"]:
            used.add(r["category"])
    for r in db.query("SELECT DISTINCT category FROM package_categories"):
        used.add(r["category"])
    extra = sorted(c for c in used if c not in config.PACKAGE_CATEGORIES)
    return list(config.PACKAGE_CATEGORIES) + extra


@manage_bp.get("/packages")
@login_required
def list_packages():
    costs = package_costs()
    rows = db.query(
        "SELECT id, name, description, spec, category, price, image, tag,"
        " packaging_material_id, packaging_qty, sort_order, is_active, created_at"
        " FROM package ORDER BY sort_order, id")
    contents = db.query(
        "SELECT m.package_id, m.product_id, m.quantity, p.name, p.unit"
        " FROM package_products_map m JOIN products p ON p.id = m.product_id ORDER BY m.id")
    by_pkg = {}
    for c in contents:
        by_pkg.setdefault(c["package_id"], []).append({
            "product_id": c["product_id"], "name": c["name"],
            "unit": c["unit"], "quantity": float(c["quantity"]),
        })
    secondary = {}
    for r in db.query("SELECT package_id, category FROM package_categories ORDER BY id"):
        secondary.setdefault(r["package_id"], []).append(r["category"])

    for r in rows:
        r["created_at"] = r["created_at"].strftime("%Y-%m-%d")
        r["packaging_qty"] = float(r["packaging_qty"])
        r["items"] = by_pkg.get(r["id"], [])
        r["secondary_categories"] = secondary.get(r["id"], [])
        r["cost"] = round(costs.get(r["id"], 0), 1)
        r["profit"] = round(r["price"] - r["cost"], 1)
        r["margin"] = round((r["price"] - r["cost"]) / r["price"] * 100, 1) if r["price"] else 0
    return jsonify({"packages": rows, "all_categories": _all_categories()})


def _replace_package_categories(pkg_id, raw, primary):
    """整批覆寫次要分類。raw 為 JSON 字串或 list;與主要分類重複者會被略過。"""
    if raw is None:
        return
    if isinstance(raw, str):
        import json
        try:
            raw = json.loads(raw)
        except ValueError:
            return
    cats = []
    for c in raw or []:
        c = str(c).strip()
        if c and c != primary and c not in cats:
            cats.append(c)
    db.execute("DELETE FROM package_categories WHERE package_id = %s", (pkg_id,))
    if cats:
        with db.db_cursor(commit=True) as cur:
            cur.executemany(
                "INSERT INTO package_categories (package_id, category) VALUES (%s,%s)",
                [(pkg_id, c) for c in cats])


def _replace_package_items(pkg_id, raw):
    """整批覆寫禮盒內容。raw 為 JSON 字串或 list: [{product_id, quantity}, ...]"""
    if raw is None:
        return
    if isinstance(raw, str):
        import json
        try:
            raw = json.loads(raw)
        except ValueError:
            return
    rows = []
    for it in raw or []:
        try:
            pid, qty = int(it["product_id"]), float(it["quantity"])
        except (KeyError, TypeError, ValueError):
            continue
        if qty > 0:
            rows.append((pkg_id, pid, qty))
    db.execute("DELETE FROM package_products_map WHERE package_id = %s", (pkg_id,))
    if rows:
        with db.db_cursor(commit=True) as cur:
            cur.executemany(
                "INSERT INTO package_products_map (package_id, product_id, quantity) VALUES (%s,%s,%s)",
                rows)


@manage_bp.post("/packages")
@login_required
def create_package():
    form = request.form
    name = (form.get("name") or "").strip()
    try:
        price = int(form.get("price", ""))
    except ValueError:
        return jsonify({"error": "價格必須為整數"}), 400
    if not name or price < 0:
        return jsonify({"error": "請填寫禮盒名稱與售價"}), 400

    image_path = ""
    if "image" in request.files and request.files["image"].filename:
        try:
            image_path = save_upload(request.files["image"])
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

    primary = form.get("category") or "其他"
    pkg_id = db.execute(
        "INSERT INTO package (name, description, spec, category, price, image, tag,"
        " packaging_material_id, packaging_qty, sort_order, is_active)"
        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (name, form.get("description") or "", form.get("spec") or "",
         primary, price, image_path, form.get("tag") or "",
         form.get("packaging_material_id") or None, form.get("packaging_qty") or 1,
         form.get("sort_order") or 0,
         1 if form.get("is_active", "1") in ("1", "true", "on") else 0),
    )
    _replace_package_items(pkg_id, form.get("items"))
    _replace_package_categories(pkg_id, form.get("secondary_categories"), primary)
    return jsonify({"id": pkg_id, "image": image_path}), 201


@manage_bp.post("/packages/<int:pkg_id>/update")
@login_required
def update_package(pkg_id):
    """更新禮盒 (multipart;image 為選填,有附檔才更換圖片)。"""
    existing = db.query_one("SELECT id, category FROM package WHERE id = %s", (pkg_id,))
    if not existing:
        return jsonify({"error": "查無禮盒"}), 404
    form = request.form
    fields, args = [], []
    for col in ("name", "description", "spec", "category", "tag"):
        if col in form:
            fields.append(f"{col} = %s")
            args.append(form.get(col))
    if "sort_order" in form:
        try:
            fields.append("sort_order = %s")
            args.append(int(form.get("sort_order") or 0))
        except ValueError:
            return jsonify({"error": "排序權重必須為整數"}), 400
    if "price" in form:
        try:
            fields.append("price = %s")
            args.append(int(form.get("price")))
        except ValueError:
            return jsonify({"error": "價格必須為整數"}), 400
    if "packaging_material_id" in form:
        fields.append("packaging_material_id = %s")
        args.append(form.get("packaging_material_id") or None)
    if "packaging_qty" in form:
        fields.append("packaging_qty = %s")
        args.append(form.get("packaging_qty") or 1)
    if "is_active" in form:
        fields.append("is_active = %s")
        args.append(1 if form.get("is_active") in ("1", "true", "on") else 0)
    if "image" in request.files and request.files["image"].filename:
        try:
            fields.append("image = %s")
            args.append(save_upload(request.files["image"]))
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
    if fields:
        db.execute(f"UPDATE package SET {', '.join(fields)} WHERE id = %s", args + [pkg_id])
    _replace_package_items(pkg_id, form.get("items"))
    _replace_package_categories(
        pkg_id, form.get("secondary_categories"), form.get("category") or existing["category"])
    return jsonify({"id": pkg_id, "updated": True})


@manage_bp.delete("/packages/<int:pkg_id>")
@login_required
def delete_package(pkg_id):
    """刪除禮盒 (含內容物與次要分類)。

    兩個條件都要成立:
    1. 禮盒已下架 — 避免刪掉前台還在賣的商品
    2. 尚未結案的訂單 (待處理/已付款/已出貨) 都沒有這個禮盒 — 這些訂單還要
       據此揀貨出貨,商品被刪掉就對不出內容

    已完成/已取消的舊訂單不擋:order_items 存的是下單當下的 package_name 與
    單價快照,歷史訂單明細不會因為刪除禮盒而消失。
    """
    pkg = db.query_one("SELECT id, name, is_active FROM package WHERE id = %s", (pkg_id,))
    if not pkg:
        return jsonify({"error": "查無禮盒"}), 404
    if pkg["is_active"]:
        return jsonify({"error": f"「{pkg['name']}」仍在上架中,請先下架再刪除"}), 400

    blocking = db.query(
        "SELECT o.order_no, o.status FROM order_items oi"
        " JOIN orders o ON o.id = oi.order_id"
        " WHERE oi.package_id = %s AND o.status IN %s ORDER BY o.id",
        (pkg_id, UNFINISHED_ORDER_STATUSES))
    if blocking:
        order_nos = [r["order_no"] for r in blocking]
        shown = "、".join(order_nos[:5]) + ("…" if len(order_nos) > 5 else "")
        return jsonify({
            "error": f"「{pkg['name']}」還在 {len(order_nos)} 筆未完成訂單中,"
                     f"請先出貨或取消後再刪除 ({shown})",
            "blocking_orders": order_nos,
        }), 400

    with db.db_cursor(commit=True) as cur:
        # 兩張關聯表已有 ON DELETE CASCADE,明確刪除以相容尚未套用外鍵的資料庫
        cur.execute("DELETE FROM package_products_map WHERE package_id = %s", (pkg_id,))
        cur.execute("DELETE FROM package_categories WHERE package_id = %s", (pkg_id,))
        cur.execute("DELETE FROM package WHERE id = %s", (pkg_id,))

    return jsonify({"id": pkg_id, "name": pkg["name"], "deleted": True})


@manage_bp.patch("/packages/<int:pkg_id>/active")
@login_required
def toggle_package_active(pkg_id):
    data = request.get_json(silent=True) or {}
    active = 1 if data.get("is_active") else 0
    if not db.query_one("SELECT id FROM package WHERE id = %s", (pkg_id,)):
        return jsonify({"error": "查無禮盒"}), 404
    db.execute("UPDATE package SET is_active = %s WHERE id = %s", (active, pkg_id))
    return jsonify({"id": pkg_id, "is_active": active})


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


def _cost_of_orders(costs, since, until=None):
    sql = ("SELECT oi.package_id, SUM(oi.quantity) AS qty FROM order_items oi"
           " JOIN orders o ON o.id = oi.order_id"
           " WHERE o.created_at >= %s AND o.status != 'cancelled'")
    args = [since]
    if until:
        sql += " AND o.created_at < %s"
        args.append(until)
    sql += " GROUP BY oi.package_id"
    return sum(costs.get(r["package_id"], 0) * float(r["qty"]) for r in db.query(sql, args))


@manage_bp.get("/finance")
@role_required("finance")
def finance_summary():
    days = PERIODS.get(request.args.get("period", "month"), 30)
    since = date.today() - timedelta(days=days)
    costs = package_costs()

    revenue = int(db.query_one(
        "SELECT COALESCE(SUM(total),0) AS v FROM orders"
        " WHERE created_at >= %s AND status != 'cancelled'", (since,))["v"])
    order_count = db.query_one(
        "SELECT COUNT(*) AS v FROM orders WHERE created_at >= %s AND status != 'cancelled'",
        (since,))["v"]
    cost = round(_cost_of_orders(costs, since))
    profit = revenue - cost
    margin = round(profit / revenue * 100, 1) if revenue else 0

    # 近 4 週明細
    weeks = []
    today = date.today()
    for i in range(4):
        end = today + timedelta(days=1) - timedelta(days=7 * i)
        start = end - timedelta(days=7)
        row = db.query_one(
            "SELECT COUNT(*) AS cnt, COALESCE(SUM(total),0) AS rev FROM orders"
            " WHERE created_at >= %s AND created_at < %s AND status != 'cancelled'",
            (start, end))
        wrev = int(row["rev"])
        wcost = _cost_of_orders(costs, start, end)
        weeks.append({
            "label": ["本週", "上週", "兩週前", "三週前"][i],
            "orders": row["cnt"],
            "avg_order": round(wrev / row["cnt"]) if row["cnt"] else 0,
            "revenue": wrev,
            "margin": round((wrev - wcost) / wrev * 100, 1) if wrev else 0,
        })

    return jsonify({
        "revenue": revenue, "cost": cost, "profit": profit, "margin": margin,
        "order_count": order_count, "weeks": weeks,
    })
