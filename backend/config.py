"""集中管理環境設定,連線資訊一律從專案根目錄的 .env 讀取。"""
import os
from pathlib import Path

from dotenv import load_dotenv

# .env 位於 website 專案根目錄 (backend 的上一層)
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# Cloud Run 透過容器內的 Tailscale SOCKS5 TCP proxy 連 MySQL；本機開發可在
# .env 直接覆寫為實際 DB host/port。
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_port", os.getenv("DB_PORT", "13306")))
DB_USER = os.getenv("DB_AC", "meishifu")
DB_PASSWORD = os.getenv("DB_PW", "")
DB_NAME = os.getenv("DB_NAME", "meishifu")
DB_POOL_SIZE = max(1, int(os.getenv("DB_POOL_SIZE", "4")))

# Cloud Run 的本機檔案系統不保證持久化。正式環境設定此值後，
# 後台上傳的商品圖會寫入 Cloud Storage。
UPLOAD_BUCKET = os.getenv("UPLOAD_BUCKET", "")

# edge router 與前後台均透過同網域存取 /api，預設不需要 CORS。
# 若要讓其他來源直接呼叫 API，可用逗號分隔來源網址。
CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "").split(",")
    if origin.strip()
]

# JWT 簽章金鑰 (正式環境請改由環境變數提供)
SECRET_KEY = os.getenv("SECRET_KEY", "meishifu-dev-secret-change-me")
JWT_EXPIRE_HOURS = 8

# 金流公司對接設定 (預留;正式串接時填入金流商提供的參數)
PAYMENT_GATEWAY = {
    "provider": os.getenv("PAY_PROVIDER", "TBD"),          # 例: ecpay / newebpay
    "merchant_id": os.getenv("PAY_MERCHANT_ID", ""),
    "hash_key": os.getenv("PAY_HASH_KEY", ""),
    "hash_iv": os.getenv("PAY_HASH_IV", ""),
    "api_url": os.getenv("PAY_API_URL", ""),
    # 金流商付款完成後回呼本後端的網址
    "notify_url": os.getenv("PAY_NOTIFY_URL", "http://localhost:5001/api/payment/notify"),
    # 付款完成後導回前端的網址
    "return_url": os.getenv("PAY_RETURN_URL", "http://localhost:5500/frontend/cart.html"),
}

FREE_SHIPPING_THRESHOLD = 2000
SHIPPING_FEE = 120

# 商品分類 (前後台共用的標準清單,順序即前台側邊選單的顯示順序)
PACKAGE_CATEGORIES = [
    "蛋黃酥系列",
    "鳳凰酥系列",
    "堅果塔系列",
    "綜合系列",
]
