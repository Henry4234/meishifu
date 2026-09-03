"""集中管理環境設定,連線資訊一律從專案根目錄的 .env 讀取。"""
import os
from pathlib import Path
from urllib.parse import urlparse

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

# 對外網址 (組合金流回呼網址用)。正式環境請覆寫為實際網域。
BACKEND_BASE_URL = os.getenv("BACKEND_BASE_URL", "http://localhost:5001").rstrip("/")
FRONTEND_BASE_URL = os.getenv("FRONTEND_BASE_URL", "http://localhost:5500/frontend").rstrip("/")

# 消費者瀏覽器看到的站台來源 (前台與 /api 同源;電子地圖選店以 postMessage 回傳
# 給購物車頁,兩邊必須同源才收得到)
_frontend = urlparse(FRONTEND_BASE_URL)
PUBLIC_ORIGIN = f"{_frontend.scheme}://{_frontend.netloc}" if _frontend.netloc else FRONTEND_BASE_URL

# ---------------------------------------------------------------- 金流 (綠界 ECPay)
# 預設值為綠界官方「測試商店」參數,可直接用測試信用卡完成全流程;
# 正式上線時於 .env 覆寫 ECPAY_MERCHANT_ID / ECPAY_HASH_KEY / ECPAY_HASH_IV
# 並將 ECPAY_ENV 設為 production。
ECPAY_MERCHANT_ID = os.getenv("ECPAY_MERCHANT_ID", "2000132")
ECPAY_HASH_KEY = os.getenv("ECPAY_HASH_KEY", "5294y06JbISpM5x9")
ECPAY_HASH_IV = os.getenv("ECPAY_HASH_IV", "v77hoKGq4kWxNNIS")
ECPAY_ENV = os.getenv("ECPAY_ENV", "stage").lower()          # stage | production
if ECPAY_ENV not in ("stage", "production"):
    raise RuntimeError("ECPAY_ENV must be 'stage' or 'production'")

ECPAY_AIO_URL = (
    "https://payment.ecpay.com.tw/Cashier/AioCheckOut/V5"
    if ECPAY_ENV == "production"
    else "https://payment-stage.ecpay.com.tw/Cashier/AioCheckOut/V5"
)

# 綠界 server-to-server 付款結果通知 (必須是公開網址,本機開發收不到)
PAY_NOTIFY_URL = os.getenv("PAY_NOTIFY_URL", BACKEND_BASE_URL + "/api/payment/notify")
# 綠界付款完成後,由消費者瀏覽器 POST 回來的網址 (本後端驗章後再導回前台)
PAY_RESULT_URL = os.getenv("PAY_RESULT_URL", BACKEND_BASE_URL + "/api/payment/result")
# ATM/CVS/BARCODE 取號結果通知；可與 ReturnURL 共用同一個驗章端點。
PAY_INFO_URL = os.getenv("PAY_INFO_URL", BACKEND_BASE_URL + "/api/payment/notify")
# 前台結帳結果頁
PAY_RETURN_URL = os.getenv("PAY_RETURN_URL", FRONTEND_BASE_URL + "/cart.html")

# ---------------------------------------------------------------- 物流 (綠界電子地圖)
# 預設為綠界 C2C 測試特店 (2000933)。正式環境須改用賣家的綠界會員編號，
# 並確認已開通全家店到店 / 7-11 交貨便對應的 C2C 物流服務。
ECPAY_LOGISTICS_MERCHANT_ID = os.getenv("ECPAY_LOGISTICS_MERCHANT_ID", "2000933")
ECPAY_LOGISTICS_ENV = os.getenv("ECPAY_LOGISTICS_ENV", "stage").lower()
if ECPAY_LOGISTICS_ENV not in ("stage", "production"):
    raise RuntimeError("ECPAY_LOGISTICS_ENV must be 'stage' or 'production'")

ECPAY_MAP_URL = (
    "https://logistics.ecpay.com.tw/Express/map"
    if ECPAY_LOGISTICS_ENV == "production"
    else "https://logistics-stage.ecpay.com.tw/Express/map"
)
# 消費者在電子地圖選好門市後,綠界會 POST 到這個網址 (需公開可連)。
# 預設走前台同源的 /api,選店視窗才能用 postMessage 把門市送回購物車頁。
ECPAY_MAP_REPLY_URL = os.getenv("ECPAY_MAP_REPLY_URL", PUBLIC_ORIGIN + "/api/logistics/map-reply")

# ---------------------------------------------------------------- 訂單通知信 (SMTP)
# 未設定 SMTP_HOST 時不寄信,只在後端 log 印出信件內容 (本機開發用)。
MAIL = {
    "host": os.getenv("SMTP_HOST", ""),
    "port": int(os.getenv("SMTP_PORT", "587")),
    "user": os.getenv("SMTP_USER", ""),
    "password": os.getenv("SMTP_PASSWORD", ""),
    "use_tls": os.getenv("SMTP_USE_TLS", "true").lower() in ("1", "true", "yes"),
    "use_ssl": os.getenv("SMTP_USE_SSL", "").lower() in ("1", "true", "yes"),
    "sender": os.getenv(
        "MAIL_FROM", os.getenv("SMTP_USER", "orders@order.meishifu.org")),
    "sender_name": os.getenv("MAIL_FROM_NAME", "美師傅 meishifu"),
    "reply_to": os.getenv("MAIL_REPLY_TO", ""),
    "timeout": int(os.getenv("SMTP_TIMEOUT", "15")),
}

# Cloud Tasks 讓訂單狀態信在 HTTP 回應結束後仍可可靠執行與自動重試。
# 未設定 queue 時會同步寄送，方便本機開發與測試。
MAIL_TASKS = {
    "project": os.getenv("MAIL_TASKS_PROJECT", ""),
    "location": os.getenv("MAIL_TASKS_LOCATION", "asia-east1"),
    "queue": os.getenv("MAIL_TASKS_QUEUE", ""),
    "url": os.getenv(
        "MAIL_TASKS_URL", BACKEND_BASE_URL + "/api/internal/mail/order-status"),
}

# 運費一律收取,不設免運門檻
SHIPPING_FEE = 130          # 宅配運費
CVS_SHIPPING_FEE = 65       # 超商店到店運費 (全家 / 7-11 交貨便)

# 配送方式代碼 → 顯示名稱 (前後台與通知信共用)
SHIPPING_LABELS = {
    "delivery": "宅配到府",
    "fami": "全家店到店",
    "unimart": "7-11 交貨便",
    "pickup": "門市自取",      # 舊訂單相容,前台已不再提供
}

PAYMENT_LABELS = {
    "credit": "信用卡",
    "transfer": "銀行 ATM 轉帳",
}

# 商品分類 (前後台共用的標準清單,順序即前台側邊選單的顯示順序)
PACKAGE_CATEGORIES = [
    "蛋黃酥系列",
    "鳳凰酥系列",
    "堅果塔系列",
    "綜合系列",
]
