# 美師傅 Meishifu 購物網站

前後端分離架構的烘焙坊電商網站,依 `stitch_meishifu_official_bakery_site` 模板與
`artisanal_warmth/DESIGN.md` 設計系統建置。Python 套件以 **uv** 管理。

## 專案結構

```
website/
├─ .env                  # 資料庫連線設定 (DB_HOST / DB_port / DB_AC / DB_PW)
├─ assets/               # LOGO 與商品照片 (uploads/ 為後台上傳的商品圖)
├─ frontend/             # 前台 (純靜態 HTML + JS,透過 API 取得資料)
│  ├─ index.html         # 首頁 (模板 meishifu_1)
│  ├─ products.html      # 精選商品 (meishifu_2,禮盒由 API 動態載入)
│  ├─ about.html         # 關於美師傅 (meishifu_3)
│  ├─ news.html          # 最新消息 (meishifu_4)
│  ├─ faq.html           # 常見問題 (meishifu_5)
│  ├─ cart.html          # 購物車/結帳 (meishifu_6,下單走 API)
│  ├─ js/site.js         # API 呼叫 + localStorage 購物車
│  └─ js/animate.js      # 捲動進場動畫、手機漢堡選單、行動版樣式
├─ admin/                # 後台管理系統 (桌面版,不做 RWD)
│  ├─ login.html         # 登入介面 (JWT)
│  ├─ dashboard.html     # 管理面板 (營收/訂單統計/銷售趨勢)
│  ├─ orders.html        # 訂單管理 (搜尋/篩選/分頁/詳情/狀態更新)
│  ├─ products.html      # 產品管理 (禮盒 + 單品配方兩個分頁)
│  ├─ materials.html     # 材料管理 (庫存/需求預估/採購入庫,後台模板 meishifu_4)
│  ├─ finance.html       # 財務管理 (營收/材料成本/淨利,後台模板 meishifu_6)
│  ├─ users.html         # 用戶權限管理 (角色/啟停用,後台模板 meishifu_5)
│  ├─ js/admin.js        # Token 管理與 API 呼叫
│  └─ js/admin-anim.js   # 後台動畫 (進場/數字 count-up/Modal/Toast)
└─ backend/              # Flask REST API (uv 專案)
   ├─ pyproject.toml     # 依賴定義 (uv sync 安裝)
   ├─ app.py             # 進入點 (port 5001)
   ├─ config.py          # 讀取 .env、金流參數
   ├─ db.py              # PyMySQL 連線輔助
   ├─ init_db.py         # 建表 + 種子資料 (可重複執行,含欄位遷移)
   └─ routes/
      ├─ shop.py         # 商品查詢、建立訂單 (價格以 DB 為準)
      ├─ admin.py        # 登入 (JWT + 角色)、儀表板、訂單管理
      ├─ manage.py       # 材料 / 產品 / 用戶權限 / 財務 API
      └─ payment.py      # 金流對接預留 (notify webhook + 開發用 mock-pay)
```

## 啟動方式 (uv)

```powershell
cd backend

# 1. 安裝依賴 (自動建立 .venv)
uv sync

# 2. 初始化資料庫 (連 .env 中的 MySQL;建表、種子商品/材料/配方、預設管理員)
uv run python init_db.py

# 3. 啟動後端 API (http://localhost:5001)
uv run python app.py

# 4. 另開終端,於 website 根目錄啟動前端靜態伺服器
cd ..
python -m http.server 5500
```

- 前台:http://localhost:5500/frontend/index.html
- 後台:http://localhost:5500/admin/login.html
- 本機預設管理員帳號:`admin` / `meishifu2026`(角色:超級管理員;上線前請修改)。
  Cloud Run 部署會把隨機初始密碼存入 Secret Manager 的 `meishifu-admin-password`。

> 注意:本機 port 5000 常被 Docker/AirPlay 等服務佔用,故後端使用 5001。

## 啟動方式 (Docker)

三個服務各自一個容器,MySQL 仍使用 `.env` 指定的外部資料庫(不在 compose 內啟動):

| 服務 | 內容 | 預設對外埠 |
|---|---|---|
| `backend` | Flask API,以 gunicorn 執行 (`backend/Dockerfile`) | 5001 |
| `frontend` | 前台靜態站,nginx (`frontend/Dockerfile` + `frontend/nginx.conf`) | 5500 |
| `admin` | 後台靜態站,nginx (`admin/Dockerfile` + `admin/nginx.conf`) | 5501 |

```bash
cp .env.sample .env          # 首次使用,填入 DB 連線資訊
docker compose build
docker compose up -d

# 初始化資料庫 (建表 + 種子資料,可重複執行)
docker compose --profile init run --rm db-init

docker compose logs -f backend
docker compose down
```

- 前台:http://localhost:5500/frontend/index.html (`/` 會自動導向)
- 後台:http://localhost:5501/admin/login.html
- API:http://localhost:5001/api/health

補充說明:

- 埠號可用環境變數覆寫:`FRONTEND_PORT` / `ADMIN_PORT` / `BACKEND_PORT`。
- `./assets` 以 volume 掛進三個容器,後台上傳的商品圖前台立即可見。
- 兩個 nginx 都已把 `/api/` 反向代理到 backend，前後台的 `API_BASE` 使用 `"/api"`
  同源呼叫；Cloudflare edge router 會在正式環境把 `/api/*` 直接送到 backend Cloud Run。
- 資料庫若在 Docker 宿主機上,`.env` 的 `DB_HOST` 請填 `host.docker.internal`。
- 正式環境務必以環境變數覆寫 `SECRET_KEY`。
- GCP 部署腳本預設不修改既有資料庫；只有在全新資料庫需要建表時，才以
  `RUN_DB_INIT=true` 明確執行 Cloud Run 初始化工作。

## 正式環境部署

- GCP project:`meishifu`，region:`asia-east1`
- Cloud Run services:`meishifu-frontend` / `meishifu-admin` / `meishifu-backend`
- Cloudflare Worker:`meishifu-edge`
- 公開路由:`https://meishifu.org/`、`https://meishifu.org/management/`、
  `https://meishifu.org/api/*`
- 商品上傳圖片儲存在私有 bucket:`meishifu-uploads-729707774647`
- DB/JWT 憑證只存在 Secret Manager，不寫入 image 或 repository。
- Backend 透過 Tailscale userspace SOCKS5 與 container-local TCP proxy 連私有 MySQL；
  不再由 production 設定直接連公開 DB IP。詳見
  [Cloud Run 透過 Tailscale 連 MySQL](docs/TAILSCALE-DB.md)。

部署定義位於 `deploy/cloudbuild.yaml`、`deploy/gcp/deploy.sh` 與
`deploy/cloudflare/`。所有 GCP 操作都由 `gcloud` SDK 執行；GCP 腳本需要由環境
提供 `DB_AC` / `DB_PW`，且預設保留既有 JWT key 與管理員密碼，不會因重部署而
隨機輪替。Cloudflare origin 與 custom domain 以 `deploy/cloudflare/wrangler.jsonc`
為設定來源，使用 Wrangler CLI 發布：

```bash
# 已完成 gcloud auth login 與 gcloud config set project meishifu 後
DB_AC='<db-user>' DB_PW='<db-password>' GCLOUD_BIN=gcloud \
  deploy/gcp/deploy.sh

# 已完成 wrangler login 後
WRANGLER_BIN=wrangler deploy/cloudflare/deploy.sh
```

## CI/CD

GitHub Actions 會在所有分支 push，以及針對 `main` 的 Pull Request，分別執行
frontend、admin、backend 測試；任一 container coverage 低於 70% 都會失敗，並產生
包含三者結果的合併報告。只有 `main` 測試成功後才會透過 OIDC／Workload Identity
Federation 部署三個 Cloud Run services，不使用長效 GCP JSON key。

完整設定、GitHub Variables、權限、回滾與 `deploy/` 版本控制原則請見
[`docs/CI-CD.md`](docs/CI-CD.md)。

## 權限角色

| 角色 | 說明 |
|---|---|
| super 超級管理員 | 全部功能,唯一可管理用戶權限 |
| finance 財務管理員 | 可檢視財務管理 |
| order 訂單管理員 / staff 一般管理員 | 一般後台操作 (訂單/商品/材料) |

## API 一覽

### 前台公開
| Method | Path | 說明 |
|---|---|---|
| GET | /api/health | 健康檢查 |
| GET | /api/packages | 上架禮盒列表,含內容物 (可帶 `?category=`) |
| GET | /api/packages/:id | 單一禮盒 |
| POST | /api/orders | 建立訂單 (items 以 `package_id` 指定,後端重新計價) |
| GET | /api/orders/:order_no?phone= | 顧客訂單查詢 |
| POST | /api/payment/notify | 金流商付款結果回呼 (預留) |
| POST | /api/payment/mock-pay | 開發用模擬付款 (上線前移除) |

### 後台 (JWT,`Authorization: Bearer <token>`)
| Method | Path | 說明 |
|---|---|---|
| POST | /api/admin/login | 登入,回傳 JWT (含角色) |
| GET | /api/admin/dashboard | 儀表板統計 |
| GET/GET/PATCH | /api/admin/orders … /:id/status | 訂單列表/詳情/狀態更新 |
| GET/POST/PATCH | /api/admin/materials … /:id | 材料查詢與新增/編輯 (含需求預估與狀態) |
| DELETE | /api/admin/materials/:id | 刪除材料;仍被配方或禮盒包材使用時回 400 並列出使用處 |
| POST | /api/admin/materials/:id/purchase | 採購入庫 (更新庫存與最新進價) |
| GET | /api/admin/products | 單一產品 + 配方 + 單位成本 |
| POST/PATCH/DELETE | /api/admin/products … /:id | 單品 CRUD (`materials` 陣列整批覆寫配方) |
| GET | /api/admin/packages | 全部禮盒 (含未上架、內容物、成本/毛利) |
| POST | /api/admin/packages | 新增禮盒 (multipart,`image` 附檔、`items` JSON 為內容物) |
| POST | /api/admin/packages/:id/update | 更新禮盒 (multipart,附 `image` 才換圖) |
| PATCH | /api/admin/packages/:id/active | 上/下架切換 (前台即時生效) |
| GET/POST/PATCH | /api/admin/users … /:id | 用戶權限管理 (僅 super) |
| GET | /api/admin/finance?period=month\|quarter\|year | 財務統計 (營收/成本/淨利/近4週) |

## 資料表

販售單位是**禮盒**,禮盒由**單一產品**組成,單一產品再對應**材料**,共三層:

```
materials (材料,有單位成本)
    ↑ product_materials (單品配方:每 1 單位產品用多少材料)
products (單一產品,如「紅豆蛋黃酥 1 顆」,不直接販售)
    ↑ package_products_map (禮盒內容:一盒裝幾入哪些產品)
package (禮盒 = 上架販售的商品) ← order_items 指向這裡
```

| 資料表 | 說明 |
|---|---|
| package | 禮盒:名稱/描述/規格/分類/售價/圖片/標籤/包材/是否上架 |
| package_products_map | 禮盒內容:package_id × product_id × 入數 |
| products | 單一產品:名稱/描述/分類/單位 (顆、片…),成本計算單位 |
| product_materials | 單品配方 BOM:每 1 單位產品的材料用量 |
| materials | 材料主檔 (分類/批號/單位/庫存/安全水位/單位成本/效期) |
| material_logs | 材料異動紀錄 (採購/消耗/盤點調整) |
| orders / order_items | 訂單主檔與明細 (`package_id` / `package_name` 為下單當下快照) |
| admins | 後台用戶 (密碼 hash、email、角色、啟停用、最後登入) |
| schema_migrations | 已套用的結構遷移紀錄,讓 init_db.py 可重複執行 |

### 成本與財務計算

- 單品成本 = Σ(配方用量 × 材料單位成本)
- 禮盒成本 = Σ(內容物入數 × 單品成本) + 包材成本
- 財務「總成本」= Σ(訂單禮盒數量 × 該禮盒成本);淨利 = 營收 − 成本
- 材料「預估需求量」= 未出貨訂單 (pending/paid) → 禮盒內容 → 單品配方 逐層展開
- 庫存狀態:庫存 < 需求量或低於安全水位一半 → 嚴重短缺;低於安全水位 → 水位偏低

## 產品管理操作

後台「產品管理」有兩個分頁:

1. **禮盒 (販售單位)** — 新增/編輯禮盒、上傳照片 (jpg/png/webp/gif,存於 `assets/uploads/`)、
   設定內容物與包材、即時預覽材料成本、上下架切換 (前台立即生效)
2. **單一產品與配方** — 建立單品 (如「紅豆蛋黃酥」) 並設定其材料配方,編輯時即時試算單位成本

> 新增禮盒後請記得設定內容物,否則成本會只計包材。
> 舊資料遷移後,原本自行新增的禮盒內容物為空,需手動補上。

## 金流串接 (預留)

`backend/routes/payment.py` 已預留完整對接點,正式串接 (綠界/藍新等) 時:

1. 在 `.env` 補上 `PAY_PROVIDER`、`PAY_MERCHANT_ID`、`PAY_HASH_KEY`、`PAY_HASH_IV`、`PAY_API_URL`
2. 在 `build_payment_payload()` 依金流商規格產生加密欄位與付款頁 URL
3. 在 `/api/payment/notify` 實作簽章驗證後更新訂單付款狀態
4. 前端 `cart.html` 已支援:`payment.payment_url` 有值時自動導向金流付款頁

運費規則:宅配滿 NT$2,000 免運,未滿收 NT$120;門市自取免運。

## 介面動畫

- **前台** (`frontend/js/animate.js`):捲動進場交錯浮現、頁首捲動陰影、購物車徽章跳動、
  1024px 以下自動生成漢堡選單,並針對手機調整標題字級與購物車版面
- **後台** (`admin/js/admin-anim.js`):側邊欄交錯滑入、卡片與表格列浮現、統計數字 count-up、
  Modal 縮放淡入、全域 Toast (`adminToast(msg, isError)`)、儀表板折線圖繪製動畫

兩者皆偵測 `prefers-reduced-motion`,使用者關閉動畫偏好時自動停用。
