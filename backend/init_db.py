"""建立資料表、執行結構遷移並寫入種子資料。

用法:  uv run python init_db.py
可重複執行 (CREATE TABLE IF NOT EXISTS + 具名遷移紀錄;種子資料僅在資料表為空時寫入)。

資料層級:
    materials (材料)  ←  product_materials (配方)  ←  products (單一產品)
                                                          ↑
                                            package_products_map (禮盒內容)
                                                          ↑
                                                     package (禮盒 = 販售單位)
"""
import os

from werkzeug.security import generate_password_hash

from db import get_connection

SCHEMA = [
    # 單一產品 (單顆蛋黃酥、單片方塊酥…),不直接販售,是配方與成本的計算單位
    """
    CREATE TABLE IF NOT EXISTS products (
        id INT AUTO_INCREMENT PRIMARY KEY,
        name VARCHAR(100) NOT NULL,
        description TEXT,
        category VARCHAR(50) DEFAULT '其他',
        unit VARCHAR(20) NOT NULL DEFAULT '顆',
        is_active TINYINT(1) DEFAULT 1,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    # 禮盒:實際上架販售的單位
    """
    CREATE TABLE IF NOT EXISTS package (
        id INT AUTO_INCREMENT PRIMARY KEY,
        name VARCHAR(100) NOT NULL,
        description TEXT,
        spec VARCHAR(100) DEFAULT '',
        category VARCHAR(50) DEFAULT '其他',
        price INT NOT NULL,
        image VARCHAR(255) DEFAULT '',
        tag VARCHAR(50) DEFAULT '',
        packaging_material_id INT DEFAULT NULL,
        packaging_qty DECIMAL(12,3) NOT NULL DEFAULT 1,
        sort_order INT NOT NULL DEFAULT 0,
        is_active TINYINT(1) DEFAULT 1,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    # 禮盒的「次要分類」:主要分類存在 package.category,一個禮盒可再歸屬多個系列
    """
    CREATE TABLE IF NOT EXISTS package_categories (
        id INT AUTO_INCREMENT PRIMARY KEY,
        package_id INT NOT NULL,
        category VARCHAR(50) NOT NULL,
        UNIQUE KEY uq_pc (package_id, category),
        FOREIGN KEY (package_id) REFERENCES package(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    # 禮盒內容:一個禮盒包含哪些單一產品、各幾入
    """
    CREATE TABLE IF NOT EXISTS package_products_map (
        id INT AUTO_INCREMENT PRIMARY KEY,
        package_id INT NOT NULL,
        product_id INT NOT NULL,
        quantity DECIMAL(12,3) NOT NULL DEFAULT 1,
        UNIQUE KEY uq_ppm (package_id, product_id),
        FOREIGN KEY (package_id) REFERENCES package(id) ON DELETE CASCADE,
        FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS orders (
        id INT AUTO_INCREMENT PRIMARY KEY,
        order_no VARCHAR(32) NOT NULL UNIQUE,
        source ENUM('online','manual') NOT NULL DEFAULT 'online',
        customer_name VARCHAR(100) NOT NULL,
        -- 手動建立的內部訂單多為自取/親送,電話與 Email 允許留空 (存空字串,
        -- 不用 NULL,避免多一種「空值」狀態);線上訂單則由 API 強制必填。
        phone VARCHAR(30) NOT NULL DEFAULT '',
        email VARCHAR(120) NOT NULL DEFAULT '',
        address VARCHAR(255) DEFAULT '',
        store_id VARCHAR(20) DEFAULT '',
        store_name VARCHAR(60) DEFAULT '',
        store_address VARCHAR(120) DEFAULT '',
        shipping_method ENUM('delivery','fami','unimart','pickup') DEFAULT 'delivery',
        payment_method ENUM('credit','transfer','cash') DEFAULT 'credit',
        payment_status ENUM('unpaid','paid','refunded') DEFAULT 'unpaid',
        status ENUM('pending','paid','shipped','completed','cancelled') DEFAULT 'pending',
        subtotal INT NOT NULL DEFAULT 0,
        shipping_fee INT NOT NULL DEFAULT 0,
        total INT NOT NULL DEFAULT 0,
        note VARCHAR(255) DEFAULT '',
        trade_no VARCHAR(32) DEFAULT '',
        payment_info VARCHAR(255) DEFAULT '',
        paid_at DATETIME DEFAULT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS order_items (
        id INT AUTO_INCREMENT PRIMARY KEY,
        order_id INT NOT NULL,
        package_id INT NOT NULL,
        package_name VARCHAR(100) NOT NULL,
        unit_price INT NOT NULL,
        quantity INT NOT NULL,
        subtotal INT NOT NULL,
        FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS admins (
        id INT AUTO_INCREMENT PRIMARY KEY,
        username VARCHAR(50) NOT NULL UNIQUE,
        password_hash VARCHAR(255) NOT NULL,
        display_name VARCHAR(50) DEFAULT '管理員',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS materials (
        id INT AUTO_INCREMENT PRIMARY KEY,
        name VARCHAR(100) NOT NULL,
        category VARCHAR(50) DEFAULT '其他',
        batch_no VARCHAR(50) DEFAULT '',
        unit VARCHAR(20) NOT NULL DEFAULT 'kg',
        stock DECIMAL(12,2) NOT NULL DEFAULT 0,
        safety_stock DECIMAL(12,2) NOT NULL DEFAULT 0,
        unit_cost DECIMAL(12,2) NOT NULL DEFAULT 0,
        expiry_date DATE DEFAULT NULL,
        is_active TINYINT(1) DEFAULT 1,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    # 單一產品的材料配方 (BOM)
    """
    CREATE TABLE IF NOT EXISTS product_materials (
        id INT AUTO_INCREMENT PRIMARY KEY,
        product_id INT NOT NULL,
        material_id INT NOT NULL,
        quantity DECIMAL(12,3) NOT NULL DEFAULT 0,
        UNIQUE KEY uq_pm (product_id, material_id),
        FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE,
        FOREIGN KEY (material_id) REFERENCES materials(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS material_logs (
        id INT AUTO_INCREMENT PRIMARY KEY,
        material_id INT NOT NULL,
        type ENUM('purchase','consume','adjust') NOT NULL,
        quantity DECIMAL(12,2) NOT NULL,
        unit_cost DECIMAL(12,2) DEFAULT NULL,
        note VARCHAR(255) DEFAULT '',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (material_id) REFERENCES materials(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS schema_migrations (
        name VARCHAR(80) PRIMARY KEY,
        applied_at DATETIME DEFAULT CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
]

# 綠界金流串接後 orders 需要的欄位 (既有資料庫以 ALTER TABLE 補上)
ORDER_COLUMNS = [
    # online = 前台經綠界成立;manual = 後台手動建立的內部訂單
    ("source", "ENUM('online','manual') NOT NULL DEFAULT 'online' AFTER order_no"),
    ("email", "VARCHAR(120) NOT NULL DEFAULT '' AFTER phone"),
    ("store_id", "VARCHAR(20) DEFAULT '' AFTER address"),        # 超商店號
    ("store_name", "VARCHAR(60) DEFAULT '' AFTER store_id"),     # 超商門市名稱
    ("store_address", "VARCHAR(120) DEFAULT '' AFTER store_name"),  # 電子地圖回傳的門市地址
    ("trade_no", "VARCHAR(32) DEFAULT '' AFTER note"),           # 綠界交易編號
    ("payment_info", "VARCHAR(255) DEFAULT '' AFTER trade_no"),  # ATM 虛擬帳號等資訊
    ("paid_at", "DATETIME DEFAULT NULL AFTER payment_info"),
]

ADMIN_COLUMNS = [
    ("email", "VARCHAR(100) DEFAULT ''"),
    ("role", "ENUM('super','order','finance','staff') DEFAULT 'staff'"),
    ("is_active", "TINYINT(1) DEFAULT 1"),
    ("last_login", "DATETIME DEFAULT NULL"),
]

MATERIALS = [
    ("特級鹹蛋黃", "餡料", "YK-202608", "顆", 150, 300, 9.5),
    ("萬丹特選紅豆", "餡料", "RB-202607", "kg", 15, 12, 180),
    ("在地土鳳梨餡", "餡料", "PA-202608", "kg", 45, 18, 220),
    ("無水奶油 (法國進口)", "麵粉/油脂", "BT-202606", "kg", 80, 25, 450),
    ("特級低筋麵粉", "麵粉/油脂", "FL-202608", "kg", 120, 40, 38),
    ("烏豆沙", "餡料", "BP-202608", "kg", 30, 15, 160),
    ("典雅禮盒包材", "包材", "BX-202605", "個", 200, 50, 35),
    # 依實際配方新增;單位成本待採購資料填入 (材料管理可編輯)
    ("糖粉", "麵粉/油脂", "", "kg", 0, 0, 0),
    ("細砂糖", "麵粉/油脂", "", "kg", 0, 0, 0),
    ("水麥芽糖", "麵粉/油脂", "", "kg", 0, 0, 0),
    ("安佳奶油", "麵粉/油脂", "", "kg", 0, 0, 0),
    ("動物鮮奶油", "麵粉/油脂", "", "kg", 0, 0, 0),
    ("全脂奶粉", "其他", "", "kg", 0, 0, 0),
    ("鹽", "其他", "", "kg", 0, 0, 0),
    ("泡打粉", "其他", "", "kg", 0, 0, 0),
    ("全蛋", "其他", "", "顆", 0, 0, 0),
    ("蛋黃液", "其他", "", "kg", 0, 0, 0),
    ("金鑽鳳梨餡", "餡料", "", "kg", 0, 0, 0),
    ("綠豆沙", "餡料", "", "kg", 0, 0, 0),
    ("夏威夷豆", "堅果", "", "kg", 0, 0, 0),
    ("腰果", "堅果", "", "kg", 0, 0, 0),
    ("杏仁果", "堅果", "", "kg", 0, 0, 0),
    ("核桃", "堅果", "", "kg", 0, 0, 0),
    ("蔓越莓", "堅果", "", "kg", 0, 0, 0),
]

# 單一產品:(名稱, 說明, 分類, 單位)
PRODUCTS = [
    ("鳳凰酥", "金鑽鳳梨餡包裹飽滿鹹蛋黃,鹹甜交織的絕妙滋味。", "鳳梨酥", "顆"),
    ("堅果塔", "腰果、核桃、杏仁果與蔓越莓,佐手工奶香塔皮。", "塔類", "個"),
    ("紅豆蛋黃酥", "烏豆沙與綠豆沙包裹紅土鹹蛋黃,層層酥皮手工揉製。", "蛋黃酥", "顆"),
]

# 單品配方 (依實際製作配方表登錄)
#   yield  : 該批配方可製作的成品數量
#   items  : (材料名, 該批用量, 計量方式)
#            "g"        → 公克,換算為材料單位 (kg) 後再除以 yield
#            "batch"    → 材料本身單位的整批用量,除以 yield
#            "each"     → 已是「每 1 個成品」的用量,不需除以 yield
RECIPES = {
    "鳳凰酥": {
        "yield": 80,
        "items": [
            ("無水奶油 (法國進口)", 720, "g"),
            ("糖粉", 240, "g"),
            ("鹽", 5, "g"),
            ("全蛋", 3, "batch"),          # 3 顆 / 80 個
            ("全脂奶粉", 100, "g"),
            ("特級低筋麵粉", 1100, "g"),
            ("金鑽鳳梨餡", 1500, "g"),
            ("特級鹹蛋黃", 1, "each"),      # 每顆包 1 個鹹蛋黃
        ],
    },
    "堅果塔": {
        "yield": 36,
        "items": [
            # 內餡
            ("細砂糖", 60, "g"),
            ("動物鮮奶油", 75, "g"),
            ("水麥芽糖", 60, "g"),
            ("夏威夷豆", 70, "g"),
            ("腰果", 360, "g"),
            ("杏仁果", 100, "g"),
            ("核桃", 120, "g"),
            ("蔓越莓", 100, "g"),
            # 塔皮 (安佳奶油 10g 內餡 + 87g 塔皮 = 97g)
            ("安佳奶油", 97, "g"),
            ("糖粉", 48, "g"),
            ("鹽", 1, "g"),
            ("泡打粉", 0.5, "g"),
            ("全蛋", 0.7, "batch"),         # 35g ÷ 50g/顆 = 0.7 顆
            ("特級低筋麵粉", 190, "g"),
        ],
    },
    "紅豆蛋黃酥": {
        "yield": 50,
        "items": [
            ("特級低筋麵粉", 965, "g"),     # 油皮
            ("糖粉", 240, "g"),
            ("無水奶油 (法國進口)", 540, "g"),
            ("烏豆沙", 625, "g"),
            ("綠豆沙", 625, "g"),
            ("蛋黃液", 290, "g"),
            ("動物鮮奶油", 200, "g"),
            ("特級鹹蛋黃", 1, "each"),
        ],
    },
}


def bom_rows(material_units):
    """把 RECIPES 換算為 (產品名, 材料名, 每 1 單位成品用量)。"""
    rows = []
    for product, recipe in RECIPES.items():
        yield_count = recipe["yield"]
        for material, amount, mode in recipe["items"]:
            if mode == "each":
                qty = amount
            elif mode == "g":
                # 材料以 kg 計價時把公克換算為公斤
                qty = (amount / 1000 if material_units.get(material) == "kg" else amount) / yield_count
            else:  # batch
                qty = amount / yield_count
            rows.append((product, material, round(qty, 6)))
    return rows

# 禮盒 (販售單位):(名稱, 說明, 規格, 分類, 售價, 圖片, 標籤)
PACKAGES = [
    ("經典紅豆蛋黃酥禮盒", "嚴選屏東萬丹紅豆,耗時熬煮成綿密微甜的紅豆泥,包裹著圓潤飽滿的宜蘭紅土鹹蛋黃。",
     "6入裝", "蛋黃酥系列", 480, "/assets/IMG_0002.jpeg", "熱銷 No.1"),
    ("經典金沙蛋黃酥禮盒", "嚴選屏東紅土鹹蛋黃,搭配綿密細緻的烏豆沙,外層酥皮層次分明。",
     "6入裝", "蛋黃酥系列", 520, "/assets/IMG_0005.jpeg", ""),
    ("金賞鳳梨酥禮盒", "嚴選在地土鳳梨與金鑽鳳梨黃金比例調配,外皮酥鬆散發濃郁奶香。",
     "10入裝", "鳳梨酥系列", 450, "/assets/IMG_0006.jpeg", "新品上市"),
    ("經典鳳梨鳳凰酥禮盒", "鳳梨酥與鳳凰酥各半,鹹甜交織的絕妙滋味,是送禮的最佳選擇。",
     "10入裝", "鳳梨酥系列", 550, "/assets/IMG_0011.jpeg", ""),
    ("中秋詠月禮盒", "集結店內最受歡迎的四款招牌糕點,搭配特製質感禮盒。",
     "8入綜合裝", "節慶禮盒", 880, "/assets/IMG_0017.jpeg", "節慶首選"),
    ("綜合手工禮盒", "一次品嚐多種經典風味,送禮自用兩相宜。",
     "10入綜合裝", "節慶禮盒", 720, "/assets/IMG_0018.jpeg", ""),
]

# 禮盒內容:(禮盒名, 單品名, 入數)
PACKAGE_MAP = [
    ("經典紅豆蛋黃酥禮盒", "紅豆蛋黃酥", 6),
    ("經典金沙蛋黃酥禮盒", "紅豆蛋黃酥", 6),
    ("金賞鳳梨酥禮盒", "鳳凰酥", 10),
    ("經典鳳梨鳳凰酥禮盒", "鳳凰酥", 10),
    ("中秋詠月禮盒", "紅豆蛋黃酥", 4), ("中秋詠月禮盒", "鳳凰酥", 4),
    ("綜合手工禮盒", "紅豆蛋黃酥", 3), ("綜合手工禮盒", "鳳凰酥", 3),
    ("綜合手工禮盒", "堅果塔", 4),
]

DEFAULT_ADMIN = (
    os.getenv("DEFAULT_ADMIN_USERNAME", "admin"),
    os.getenv("DEFAULT_ADMIN_PASSWORD", "meishifu2026"),
    os.getenv("DEFAULT_ADMIN_DISPLAY_NAME", "管理員 A"),
    os.getenv("DEFAULT_ADMIN_EMAIL", "admin@meishifu.com"),
    "super",
)


# ---------------------------------------------------------------- 工具
def columns_of(cur, table):
    cur.execute(
        "SELECT COLUMN_NAME FROM information_schema.COLUMNS"
        " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s", (table,))
    return {r["COLUMN_NAME"] for r in cur.fetchall()}


def migration_done(cur, name):
    cur.execute("SELECT name FROM schema_migrations WHERE name = %s", (name,))
    return cur.fetchone() is not None


def mark_migration(cur, name):
    cur.execute("INSERT IGNORE INTO schema_migrations (name) VALUES (%s)", (name,))


# ---------------------------------------------------------------- 遷移
def migrate_admin_columns(cur):
    existing = columns_of(cur, "admins")
    for col, ddl in ADMIN_COLUMNS:
        if col not in existing:
            cur.execute(f"ALTER TABLE admins ADD COLUMN {col} {ddl}")
            print(f"  admins 新增欄位: {col}")


def migrate_order_ecpay_columns(cur):
    """訂單新增 Email / 超商門市 / 綠界交易欄位,並把配送方式擴充為三種。"""
    existing = columns_of(cur, "orders")
    for col, ddl in ORDER_COLUMNS:
        if col not in existing:
            cur.execute(f"ALTER TABLE orders ADD COLUMN {col} {ddl}")
            print(f"  orders 新增欄位: {col}")

    cur.execute(
        "SELECT COLUMN_TYPE AS t FROM information_schema.COLUMNS"
        " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'orders'"
        " AND COLUMN_NAME = 'shipping_method'")
    row = cur.fetchone()
    if row and "fami" not in row["t"]:
        # 保留 pickup 讓舊訂單不失效,前台已改為只提供宅配 / 全家 / 7-11
        cur.execute(
            "ALTER TABLE orders MODIFY COLUMN shipping_method"
            " ENUM('delivery','fami','unimart','pickup') DEFAULT 'delivery'")
        print("  orders.shipping_method 擴充為 宅配 / 全家店到店 / 7-11 交貨便")


def migrate_manual_orders(cur):
    """後台手動建立內部訂單所需的欄位調整。

    1. payment_method 補上 cash (現場收現),供自取 / 親送的內部訂單使用
    2. phone 補上 DEFAULT ''。欄位維持 NOT NULL:限制留在 API 層依訂單來源判斷
       (線上訂單必填、內部訂單可留空),資料庫端不會出現 NULL 與 '' 兩種空值。
    """
    cur.execute(
        "SELECT COLUMN_TYPE AS t, COLUMN_DEFAULT AS d FROM information_schema.COLUMNS"
        " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'orders'"
        " AND COLUMN_NAME = 'payment_method'")
    row = cur.fetchone()
    if row and "cash" not in row["t"]:
        cur.execute(
            "ALTER TABLE orders MODIFY COLUMN payment_method"
            " ENUM('credit','transfer','cash') DEFAULT 'credit'")
        print("  orders.payment_method 擴充為 信用卡 / 轉帳 / 現金")

    cur.execute(
        "SELECT COLUMN_DEFAULT AS d FROM information_schema.COLUMNS"
        " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'orders'"
        " AND COLUMN_NAME = 'phone'")
    row = cur.fetchone()
    if row and row["d"] is None:
        cur.execute("ALTER TABLE orders MODIFY COLUMN phone VARCHAR(30) NOT NULL DEFAULT ''")
        print("  orders.phone 補上預設值 '' (內部訂單可不填電話)")


def migrate_order_items(cur):
    """order_items.product_id/product_name → package_id/package_name。"""
    cols = columns_of(cur, "order_items")
    if "product_id" in cols and "package_id" not in cols:
        cur.execute("ALTER TABLE order_items CHANGE COLUMN product_id package_id INT NOT NULL")
        cur.execute("ALTER TABLE order_items CHANGE COLUMN product_name package_name VARCHAR(100) NOT NULL")
        print("  order_items 欄位已更名為 package_id / package_name")


def migrate_products_to_package(cur):
    """舊 products 實際上存的是販售用禮盒 → 整批搬到 package (保留 id 讓既有訂單不失效),
    再把 products 重建為單一產品。"""
    name = "2026_08_split_package_product"
    if migration_done(cur, name):
        return
    old_cols = columns_of(cur, "products")
    is_old_shape = {"price", "image", "spec", "tag"} <= old_cols

    if is_old_shape:
        cur.execute("SELECT COUNT(*) AS c FROM products")
        old_count = cur.fetchone()["c"]
        if old_count:
            cur.execute(
                "INSERT INTO package (id, name, description, spec, category, price, image, tag, is_active, created_at)"
                " SELECT id, name, description, spec, category, price, image, tag, is_active, created_at FROM products"
                " ON DUPLICATE KEY UPDATE package.name = package.name")
            print(f"  已將 {old_count} 筆舊商品搬移為禮盒 (package,保留原 id)")
        # 舊配方是「禮盒→材料」,新結構改為「單品→材料」,清空重建
        cur.execute("DELETE FROM product_materials")
        cur.execute("DELETE FROM products")
        for col in ("price", "image", "spec", "tag"):
            if col in old_cols:
                cur.execute(f"ALTER TABLE products DROP COLUMN {col}")
        if "unit" not in old_cols:
            cur.execute("ALTER TABLE products ADD COLUMN unit VARCHAR(20) NOT NULL DEFAULT '顆' AFTER category")
        print("  products 已重建為單一產品結構")

    if "packaging_material_id" not in columns_of(cur, "package"):
        cur.execute("ALTER TABLE package ADD COLUMN packaging_material_id INT DEFAULT NULL")
        cur.execute("ALTER TABLE package ADD COLUMN packaging_qty DECIMAL(12,3) NOT NULL DEFAULT 1")
    mark_migration(cur, name)


def migrate_bom_precision(cur):
    """配方用量原為 DECIMAL(12,3),換算成每 1 單位成品後會小於 0.001 (例:鹽 0.0000625 kg),
    需提高到 6 位小數才不會被截成 0。"""
    cur.execute(
        "SELECT NUMERIC_SCALE AS s FROM information_schema.COLUMNS"
        " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'product_materials'"
        " AND COLUMN_NAME = 'quantity'")
    row = cur.fetchone()
    if row and int(row["s"]) < 6:
        cur.execute("ALTER TABLE product_materials MODIFY COLUMN quantity DECIMAL(14,6) NOT NULL DEFAULT 0")
        print("  product_materials.quantity 精度提高為 DECIMAL(14,6)")


def upsert_materials(cur):
    """依 MATERIALS 補齊缺少的材料 (已存在者不覆寫庫存與成本)。"""
    cur.execute("SELECT name FROM materials")
    existing = {r["name"] for r in cur.fetchall()}
    new_rows = [m for m in MATERIALS if m[0] not in existing]
    if new_rows:
        cur.executemany(
            "INSERT INTO materials (name, category, batch_no, unit, stock, safety_stock, unit_cost)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s)", new_rows)
        print(f"  新增 {len(new_rows)} 筆材料: {', '.join(m[0] for m in new_rows)}")


def apply_recipes(cur):
    """依 PRODUCTS / RECIPES 覆寫單品與配方,並移除不在清單內的舊產品。"""
    cur.execute("SELECT id, name, unit FROM materials")
    mats = cur.fetchall()
    mid = {m["name"]: m["id"] for m in mats}
    munit = {m["name"]: m["unit"] for m in mats}

    keep = [p[0] for p in PRODUCTS]
    cur.execute("SELECT id, name FROM products")
    current = {r["name"]: r["id"] for r in cur.fetchall()}

    # 移除不再需要的單品 (連同其禮盒內容對應)
    obsolete = [(n, i) for n, i in current.items() if n not in keep]
    for pname, pid_ in obsolete:
        cur.execute("SELECT COUNT(*) AS c FROM package_products_map WHERE product_id = %s", (pid_,))
        used = cur.fetchone()["c"]
        cur.execute("DELETE FROM package_products_map WHERE product_id = %s", (pid_,))
        cur.execute("DELETE FROM products WHERE id = %s", (pid_,))
        print(f"  移除單品「{pname}」" + (f" (同時解除 {used} 筆禮盒內容對應)" if used else ""))

    # 建立或更新保留的單品
    for name_, desc, category, unit in PRODUCTS:
        if name_ in current:
            cur.execute(
                "UPDATE products SET description = %s, category = %s, unit = %s WHERE id = %s",
                (desc, category, unit, current[name_]))
        else:
            cur.execute(
                "INSERT INTO products (name, description, category, unit) VALUES (%s,%s,%s,%s)",
                (name_, desc, category, unit))
            current[name_] = cur.lastrowid
            print(f"  新增單品「{name_}」")

    # 覆寫配方
    for product, material, qty in bom_rows(munit):
        if product not in current or material not in mid:
            continue
        cur.execute(
            "INSERT INTO product_materials (product_id, material_id, quantity) VALUES (%s,%s,%s)"
            " ON DUPLICATE KEY UPDATE quantity = VALUES(quantity)",
            (current[product], mid[material], qty))
    # 清掉配方裡已不在 RECIPES 的殘留材料
    for product, recipe in RECIPES.items():
        if product not in current:
            continue
        used_ids = [mid[m] for m, _, _ in recipe["items"] if m in mid]
        if used_ids:
            placeholders = ",".join(["%s"] * len(used_ids))
            cur.execute(
                f"DELETE FROM product_materials WHERE product_id = %s AND material_id NOT IN ({placeholders})",
                [current[product]] + used_ids)
    print(f"  已套用 {len(RECIPES)} 份配方: {', '.join(RECIPES)}")


def migrate_recipes(cur):
    name = "2026_08_real_recipes"
    if migration_done(cur, name):
        return
    upsert_materials(cur)
    apply_recipes(cur)
    mark_migration(cur, name)


# 單品名稱關鍵字 → 所屬系列 (用於推導禮盒的次要分類起始值)
PRODUCT_SERIES = [
    ("蛋黃酥", "蛋黃酥系列"),
    ("鳳凰酥", "鳳凰酥系列"),
    ("堅果塔", "堅果塔系列"),
]


def migrate_multi_category(cur):
    """禮盒改為可同時歸屬多個系列:主要分類仍存在 package.category,
    次要分類存於 package_categories;另加 sort_order 供前台排序。"""
    name = "2026_08_package_multi_category"
    if migration_done(cur, name):
        return

    if "sort_order" not in columns_of(cur, "package"):
        cur.execute("ALTER TABLE package ADD COLUMN sort_order INT NOT NULL DEFAULT 0 AFTER packaging_qty")
        print("  package 新增欄位: sort_order")

    # 舊分類名稱對齊標準清單
    cur.execute("UPDATE package SET category = '鳳凰酥系列' WHERE category = '鳳梨酥系列'")

    # 依禮盒內容物推導次要分類作為起始值 (之後可在後台自行調整)
    cur.execute("SELECT COUNT(*) AS c FROM package_categories")
    if cur.fetchone()["c"] == 0:
        cur.execute(
            "SELECT m.package_id, k.category AS primary_cat, p.name"
            " FROM package_products_map m"
            " JOIN products p ON p.id = m.product_id"
            " JOIN package k ON k.id = m.package_id")
        derived = {}
        for r in cur.fetchall():
            for keyword, series in PRODUCT_SERIES:
                if keyword in r["name"] and series != r["primary_cat"]:
                    derived.setdefault(r["package_id"], set()).add(series)
        rows = [(pid, cat) for pid, cats in derived.items() for cat in sorted(cats)]
        if rows:
            cur.executemany(
                "INSERT IGNORE INTO package_categories (package_id, category) VALUES (%s,%s)", rows)
            print(f"  依內容物推導出 {len(rows)} 筆次要分類 (可於後台調整)")

    mark_migration(cur, name)


# ---------------------------------------------------------------- 種子
def seed(cur):
    cur.execute("SELECT COUNT(*) AS c FROM materials")
    if cur.fetchone()["c"] == 0:
        cur.executemany(
            "INSERT INTO materials (name, category, batch_no, unit, stock, safety_stock, unit_cost)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s)", MATERIALS)
        print(f"  已寫入 {len(MATERIALS)} 筆材料")

    cur.execute("SELECT COUNT(*) AS c FROM products")
    if cur.fetchone()["c"] == 0:
        cur.executemany(
            "INSERT INTO products (name, description, category, unit) VALUES (%s,%s,%s,%s)", PRODUCTS)
        print(f"  已寫入 {len(PRODUCTS)} 筆單一產品")

    cur.execute("SELECT id, name FROM products")
    pid = {r["name"]: r["id"] for r in cur.fetchall()}
    cur.execute("SELECT id, name, unit FROM materials")
    mats = cur.fetchall()
    mid = {r["name"]: r["id"] for r in mats}
    munit = {r["name"]: r["unit"] for r in mats}

    cur.execute("SELECT COUNT(*) AS c FROM product_materials")
    if cur.fetchone()["c"] == 0:
        rows = [(pid[p], mid[m], q) for p, m, q in bom_rows(munit) if p in pid and m in mid]
        cur.executemany(
            "INSERT INTO product_materials (product_id, material_id, quantity) VALUES (%s,%s,%s)", rows)
        print(f"  已寫入 {len(rows)} 筆單品配方")

    cur.execute("SELECT COUNT(*) AS c FROM package")
    if cur.fetchone()["c"] == 0:
        cur.executemany(
            "INSERT INTO package (name, description, spec, category, price, image, tag)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s)", PACKAGES)
        print(f"  已寫入 {len(PACKAGES)} 筆禮盒")

    # 包材預設綁定到每個禮盒
    box_id = mid.get("典雅禮盒包材")
    if box_id:
        cur.execute("UPDATE package SET packaging_material_id = %s WHERE packaging_material_id IS NULL", (box_id,))

    cur.execute("SELECT COUNT(*) AS c FROM package_products_map")
    if cur.fetchone()["c"] == 0:
        cur.execute("SELECT id, name FROM package")
        pkgs = cur.fetchall()
        exact = {r["name"]: r["id"] for r in pkgs}
        # 舊資料的禮盒名稱可能沒有「禮盒」二字,做寬鬆比對
        loose = {r["name"].replace("禮盒", ""): r["id"] for r in pkgs}

        def kid(name):
            return exact.get(name) or loose.get(name.replace("禮盒", ""))

        rows = [(kid(k), pid[p], q) for k, p, q in PACKAGE_MAP if kid(k) and p in pid]
        if rows:
            cur.executemany(
                "INSERT INTO package_products_map (package_id, product_id, quantity) VALUES (%s,%s,%s)", rows)
            print(f"  已寫入 {len(rows)} 筆禮盒內容對應")

    cur.execute("SELECT COUNT(*) AS c FROM admins")
    if cur.fetchone()["c"] == 0:
        username, password, display, email, role = DEFAULT_ADMIN
        cur.execute(
            "INSERT INTO admins (username, password_hash, display_name, email, role) VALUES (%s,%s,%s,%s,%s)",
            (username, generate_password_hash(password), display, email, role))
        print(f"  已建立預設管理員: {username} / {password}")
    else:
        cur.execute("UPDATE admins SET role = 'super' WHERE username = 'admin' AND (role IS NULL OR role = 'staff')")


def main():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            print("建立資料表...")
            for ddl in SCHEMA:
                cur.execute(ddl)
            print("執行遷移...")
            migrate_admin_columns(cur)
            migrate_order_ecpay_columns(cur)
            migrate_manual_orders(cur)
            migrate_order_items(cur)
            migrate_products_to_package(cur)
            migrate_bom_precision(cur)
            migrate_recipes(cur)
            migrate_multi_category(cur)
            print("寫入種子資料...")
            seed(cur)
        conn.commit()
        print("資料庫初始化完成")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
