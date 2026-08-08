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
        is_active TINYINT(1) DEFAULT 1,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
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
        customer_name VARCHAR(100) NOT NULL,
        phone VARCHAR(30) NOT NULL,
        address VARCHAR(255) DEFAULT '',
        shipping_method ENUM('delivery','pickup') DEFAULT 'delivery',
        payment_method ENUM('credit','transfer') DEFAULT 'credit',
        payment_status ENUM('unpaid','paid','refunded') DEFAULT 'unpaid',
        status ENUM('pending','paid','shipped','completed','cancelled') DEFAULT 'pending',
        subtotal INT NOT NULL DEFAULT 0,
        shipping_fee INT NOT NULL DEFAULT 0,
        total INT NOT NULL DEFAULT 0,
        note VARCHAR(255) DEFAULT '',
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
]

# 單一產品:(名稱, 說明, 分類, 單位)
PRODUCTS = [
    ("紅豆蛋黃酥", "萬丹紅豆泥包裹宜蘭紅土鹹蛋黃,層層酥皮手工揉製。", "蛋黃酥", "顆"),
    ("金沙蛋黃酥", "綿密烏豆沙包裹屏東紅土鹹蛋黃,入口即化。", "蛋黃酥", "顆"),
    ("金賞鳳梨酥", "在地土鳳梨與金鑽鳳梨黃金比例,酸甜帶纖維感。", "鳳梨酥", "顆"),
    ("鳳凰酥", "鳳梨餡再加入飽滿鹹蛋黃,鹹甜交織。", "鳳梨酥", "顆"),
    ("手工方塊酥", "層次分明的奶香酥餅,越嚼越香。", "餅乾", "片"),
]

# 單品配方:(產品名, 材料名, 每單位用量)
PRODUCT_BOM = [
    ("紅豆蛋黃酥", "特級鹹蛋黃", 1), ("紅豆蛋黃酥", "萬丹特選紅豆", 0.05),
    ("紅豆蛋黃酥", "無水奶油 (法國進口)", 0.02), ("紅豆蛋黃酥", "特級低筋麵粉", 0.05),
    ("金沙蛋黃酥", "特級鹹蛋黃", 1), ("金沙蛋黃酥", "烏豆沙", 0.05),
    ("金沙蛋黃酥", "無水奶油 (法國進口)", 0.02), ("金沙蛋黃酥", "特級低筋麵粉", 0.05),
    ("金賞鳳梨酥", "在地土鳳梨餡", 0.04),
    ("金賞鳳梨酥", "無水奶油 (法國進口)", 0.015), ("金賞鳳梨酥", "特級低筋麵粉", 0.04),
    ("鳳凰酥", "在地土鳳梨餡", 0.035), ("鳳凰酥", "特級鹹蛋黃", 1),
    ("鳳凰酥", "無水奶油 (法國進口)", 0.015), ("鳳凰酥", "特級低筋麵粉", 0.04),
    ("手工方塊酥", "無水奶油 (法國進口)", 0.008), ("手工方塊酥", "特級低筋麵粉", 0.025),
]

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
    ("經典金沙蛋黃酥禮盒", "金沙蛋黃酥", 6),
    ("金賞鳳梨酥禮盒", "金賞鳳梨酥", 10),
    ("經典鳳梨鳳凰酥禮盒", "金賞鳳梨酥", 5), ("經典鳳梨鳳凰酥禮盒", "鳳凰酥", 5),
    ("中秋詠月禮盒", "紅豆蛋黃酥", 2), ("中秋詠月禮盒", "金沙蛋黃酥", 2),
    ("中秋詠月禮盒", "金賞鳳梨酥", 2), ("中秋詠月禮盒", "鳳凰酥", 2),
    ("綜合手工禮盒", "金沙蛋黃酥", 3), ("綜合手工禮盒", "金賞鳳梨酥", 3),
    ("綜合手工禮盒", "手工方塊酥", 4),
]

DEFAULT_ADMIN = ("admin", "meishifu2026", "管理員 A", "admin@meishifu.com", "super")


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
    cur.execute("SELECT id, name FROM materials")
    mid = {r["name"]: r["id"] for r in cur.fetchall()}

    cur.execute("SELECT COUNT(*) AS c FROM product_materials")
    if cur.fetchone()["c"] == 0:
        rows = [(pid[p], mid[m], q) for p, m, q in PRODUCT_BOM if p in pid and m in mid]
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
            migrate_order_items(cur)
            migrate_products_to_package(cur)
            print("寫入種子資料...")
            seed(cur)
        conn.commit()
        print("資料庫初始化完成")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
