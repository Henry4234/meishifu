"""建立資料表並寫入種子資料。

用法:  uv run python init_db.py
可重複執行 (CREATE TABLE IF NOT EXISTS + 欄位補齊;種子資料僅在資料表為空時寫入)。
"""
from werkzeug.security import generate_password_hash

from db import get_connection

SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS products (
        id INT AUTO_INCREMENT PRIMARY KEY,
        name VARCHAR(100) NOT NULL,
        description TEXT,
        spec VARCHAR(100) DEFAULT '',
        category VARCHAR(50) DEFAULT '其他',
        price INT NOT NULL,
        image VARCHAR(255) DEFAULT '',
        tag VARCHAR(50) DEFAULT '',
        is_active TINYINT(1) DEFAULT 1,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
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
        product_id INT NOT NULL,
        product_name VARCHAR(100) NOT NULL,
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
    # 材料主檔:庫存與單位成本 (計算商品成本的基礎)
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
    # 商品配方 (BOM):每一單位商品消耗多少材料 → 商品材料成本
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
    # 材料異動紀錄:採購 (purchase) / 消耗 (consume) / 盤點調整 (adjust)
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
]

# admins 表補齊權限管理所需欄位 (可重複執行)
ADMIN_COLUMNS = [
    ("email", "VARCHAR(100) DEFAULT ''"),
    ("role", "ENUM('super','order','finance','staff') DEFAULT 'staff'"),
    ("is_active", "TINYINT(1) DEFAULT 1"),
    ("last_login", "DATETIME DEFAULT NULL"),
]

PRODUCTS = [
    ("經典紅豆蛋黃酥", "嚴選屏東萬丹紅豆,耗時熬煮成綿密微甜的紅豆泥,包裹著圓潤飽滿的宜蘭紅土鹹蛋黃,外層是層層堆疊的香酥外皮。",
     "6入裝", "蛋黃酥系列", 480, "/assets/IMG_0002.jpeg", "熱銷 No.1"),
    ("經典金沙蛋黃酥", "嚴選屏東紅土鹹蛋黃,搭配綿密細緻的烏豆沙,外層酥皮層次分明,入口即化。",
     "6入裝", "蛋黃酥系列", 520, "/assets/IMG_0005.jpeg", ""),
    ("金賞鳳梨酥", "嚴選在地土鳳梨與金鑽鳳梨黃金比例調配,內餡酸甜適中且帶有纖維感,外皮酥鬆散發濃郁奶香。",
     "10入裝", "鳳梨酥系列", 450, "/assets/IMG_0006.jpeg", "新品上市"),
    ("經典鳳梨鳳凰酥", "嚴選在地土鳳梨,酸甜適中。鳳凰酥更加入飽滿紅土鹹蛋黃,鹹甜交織的絕妙滋味,是送禮的最佳選擇。",
     "10入裝", "鳳梨酥系列", 550, "/assets/IMG_0011.jpeg", ""),
    ("中秋詠月禮盒", "集結店內最受歡迎的四款招牌糕點,搭配特製質感禮盒,是節慶送禮、傳遞心意的最佳選擇。",
     "典雅禮盒裝", "節慶禮盒", 880, "/assets/IMG_0017.jpeg", "節慶首選"),
    ("綜合手工禮盒", "一次品嚐多種經典風味。內含金沙蛋黃酥、特製方塊酥及季節限定小點,送禮自用兩相宜。",
     "典雅禮盒裝", "節慶禮盒", 720, "/assets/IMG_0018.jpeg", ""),
]

# (名稱, 分類, 批號, 單位, 庫存, 安全水位, 單位成本)
MATERIALS = [
    ("特級鹹蛋黃", "餡料", "YK-202608", "顆", 150, 300, 9.5),
    ("萬丹特選紅豆", "餡料", "RB-202607", "kg", 15, 12, 180),
    ("在地土鳳梨餡", "餡料", "PA-202608", "kg", 45, 18, 220),
    ("無水奶油 (法國進口)", "麵粉/油脂", "BT-202606", "kg", 80, 25, 450),
    ("特級低筋麵粉", "麵粉/油脂", "FL-202608", "kg", 120, 40, 38),
    ("烏豆沙", "餡料", "BP-202608", "kg", 30, 15, 160),
    ("典雅禮盒包材", "包材", "BX-202605", "個", 200, 50, 35),
]

# (product_name, material_name, 每一單位商品消耗量)
BOM = [
    ("經典紅豆蛋黃酥", "特級鹹蛋黃", 6), ("經典紅豆蛋黃酥", "萬丹特選紅豆", 0.3),
    ("經典紅豆蛋黃酥", "無水奶油 (法國進口)", 0.12), ("經典紅豆蛋黃酥", "特級低筋麵粉", 0.3),
    ("經典金沙蛋黃酥", "特級鹹蛋黃", 6), ("經典金沙蛋黃酥", "烏豆沙", 0.3),
    ("經典金沙蛋黃酥", "無水奶油 (法國進口)", 0.12), ("經典金沙蛋黃酥", "特級低筋麵粉", 0.3),
    ("金賞鳳梨酥", "在地土鳳梨餡", 0.4), ("金賞鳳梨酥", "無水奶油 (法國進口)", 0.15),
    ("金賞鳳梨酥", "特級低筋麵粉", 0.4),
    ("經典鳳梨鳳凰酥", "在地土鳳梨餡", 0.35), ("經典鳳梨鳳凰酥", "特級鹹蛋黃", 5),
    ("經典鳳梨鳳凰酥", "無水奶油 (法國進口)", 0.15), ("經典鳳梨鳳凰酥", "特級低筋麵粉", 0.4),
    ("中秋詠月禮盒", "特級鹹蛋黃", 8), ("中秋詠月禮盒", "萬丹特選紅豆", 0.2),
    ("中秋詠月禮盒", "在地土鳳梨餡", 0.2), ("中秋詠月禮盒", "無水奶油 (法國進口)", 0.2),
    ("中秋詠月禮盒", "特級低筋麵粉", 0.5), ("中秋詠月禮盒", "典雅禮盒包材", 1),
    ("綜合手工禮盒", "特級鹹蛋黃", 6), ("綜合手工禮盒", "烏豆沙", 0.2),
    ("綜合手工禮盒", "無水奶油 (法國進口)", 0.18), ("綜合手工禮盒", "特級低筋麵粉", 0.45),
    ("綜合手工禮盒", "典雅禮盒包材", 1),
]

DEFAULT_ADMIN = ("admin", "meishifu2026", "管理員 A", "admin@meishifu.com", "super")


def ensure_columns(cur):
    cur.execute(
        "SELECT COLUMN_NAME FROM information_schema.COLUMNS"
        " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'admins'")
    existing = {r["COLUMN_NAME"] for r in cur.fetchall()}
    for col, ddl in ADMIN_COLUMNS:
        if col not in existing:
            cur.execute(f"ALTER TABLE admins ADD COLUMN {col} {ddl}")
            print(f"admins 表已新增欄位: {col}")


def main():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for ddl in SCHEMA:
                cur.execute(ddl)
            ensure_columns(cur)

            cur.execute("SELECT COUNT(*) AS c FROM products")
            if cur.fetchone()["c"] == 0:
                cur.executemany(
                    "INSERT INTO products (name, description, spec, category, price, image, tag)"
                    " VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    PRODUCTS,
                )
                print(f"已寫入 {len(PRODUCTS)} 筆商品種子資料")

            cur.execute("SELECT COUNT(*) AS c FROM materials")
            if cur.fetchone()["c"] == 0:
                cur.executemany(
                    "INSERT INTO materials (name, category, batch_no, unit, stock, safety_stock, unit_cost)"
                    " VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    MATERIALS,
                )
                print(f"已寫入 {len(MATERIALS)} 筆材料種子資料")

            cur.execute("SELECT COUNT(*) AS c FROM product_materials")
            if cur.fetchone()["c"] == 0:
                cur.execute("SELECT id, name FROM products")
                pid = {r["name"]: r["id"] for r in cur.fetchall()}
                cur.execute("SELECT id, name FROM materials")
                mid = {r["name"]: r["id"] for r in cur.fetchall()}
                rows = [(pid[p], mid[m], q) for p, m, q in BOM if p in pid and m in mid]
                cur.executemany(
                    "INSERT INTO product_materials (product_id, material_id, quantity) VALUES (%s,%s,%s)",
                    rows,
                )
                print(f"已寫入 {len(rows)} 筆商品配方 (BOM)")

            cur.execute("SELECT COUNT(*) AS c FROM admins")
            if cur.fetchone()["c"] == 0:
                username, password, display, email, role = DEFAULT_ADMIN
                cur.execute(
                    "INSERT INTO admins (username, password_hash, display_name, email, role)"
                    " VALUES (%s,%s,%s,%s,%s)",
                    (username, generate_password_hash(password), display, email, role),
                )
                print(f"已建立預設管理員帳號: {username} / {password}")
            else:
                # 既有 admin 補上 super 角色
                cur.execute("UPDATE admins SET role = 'super' WHERE username = 'admin' AND (role IS NULL OR role = 'staff')")
        conn.commit()
        print("資料庫初始化完成")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
