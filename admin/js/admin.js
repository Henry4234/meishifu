/* 後台共用腳本:JWT 驗證與 API 呼叫 */
const API_BASE = "/api";
const TOKEN_KEY = "meishifu_admin_token";
const ADMIN_NAME_KEY = "meishifu_admin_name";

function getToken() { return localStorage.getItem(TOKEN_KEY); }

function requireLogin() {
  if (!getToken()) window.location.href = "login.html";
}

function logout() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(ADMIN_NAME_KEY);
  window.location.href = "login.html";
}

async function adminFetch(path, options = {}) {
  const res = await fetch(API_BASE + path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      Authorization: "Bearer " + getToken(),
      ...(options.headers || {}),
    },
  });
  if (res.status === 401) { logout(); throw new Error("登入逾期"); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || "API 錯誤");
  return data;
}

function formatPrice(n) { return "NT$ " + Number(n).toLocaleString("zh-TW"); }

function fillAdminName() {
  const el = document.getElementById("admin-name");
  if (el) el.textContent = localStorage.getItem(ADMIN_NAME_KEY) || "管理員";
}

/* 訂單狀態 → 樣式 */
const STATUS_STYLE = {
  pending:   { label: "待處理", cls: "bg-error-container text-on-error-container" },
  paid:      { label: "已付款", cls: "bg-primary-fixed text-on-primary-fixed-variant" },
  shipped:   { label: "已出貨", cls: "bg-secondary-fixed text-on-secondary-fixed-variant" },
  completed: { label: "已完成", cls: "bg-tertiary-fixed text-on-tertiary-fixed-variant" },
  cancelled: { label: "已取消", cls: "bg-surface-variant text-on-surface-variant" },
};

function statusBadge(status) {
  const s = STATUS_STYLE[status] || { label: status, cls: "bg-surface-variant text-on-surface-variant" };
  return `<span class="inline-flex items-center px-2 py-1 rounded-full ${s.cls} font-caption text-caption gap-1">${s.label}</span>`;
}

/* ---------- 新訂單通知 ----------
   前台結帳成功後訂單直接寫入資料庫，後台各頁定期輪詢 /admin/orders/updates，
   有新訂單時跳出提示並在頂部鈴鐺顯示未讀數量。 */
const LAST_ORDER_ID_KEY = "meishifu_admin_last_order_id";
const UNSEEN_ORDER_KEY = "meishifu_admin_unseen_orders";
const ORDER_POLL_INTERVAL = 30000;

function getLastOrderId() { return Number(localStorage.getItem(LAST_ORDER_ID_KEY) || 0); }
function getUnseenOrders() { return Number(localStorage.getItem(UNSEEN_ORDER_KEY) || 0); }

let orderBell = null;
let orderBellBadge = null;

/* 在後台頁首插入通知鈴鐺 (各頁 HTML 不需另外改動) */
function ensureOrderBell() {
  if (orderBell) return orderBell;
  if (typeof document === "undefined" || !document.querySelector) return null;
  const header = document.querySelector("header");
  if (!header) return null;

  const bell = document.createElement("button");
  bell.id = "order-bell";
  bell.title = "新訂單通知";
  bell.className =
    "relative w-10 h-10 rounded-full flex items-center justify-center " +
    "text-on-surface-variant hover:bg-surface-container-high transition-colors";
  const icon = document.createElement("span");
  icon.className = "material-symbols-outlined";
  icon.textContent = "notifications";
  const badge = document.createElement("span");
  badge.id = "order-bell-badge";
  badge.className =
    "hidden absolute -top-0.5 -right-0.5 min-w-[18px] h-[18px] px-1 bg-error text-on-error " +
    "rounded-full items-center justify-center font-caption text-[10px] font-bold";
  bell.appendChild(icon);
  bell.appendChild(badge);
  bell.addEventListener("click", () => {
    clearUnseenOrders();
    if (!String(window.location.pathname || "").endsWith("orders.html")) {
      window.location.href = "orders.html";
    }
  });

  const group = header.lastElementChild;
  if (group && group.tagName === "DIV") group.insertBefore(bell, group.firstElementChild);
  else header.appendChild(bell);
  orderBell = bell;
  orderBellBadge = badge;
  return bell;
}

function renderOrderBell() {
  if (!ensureOrderBell()) return;
  const n = getUnseenOrders();
  orderBellBadge.textContent = n > 99 ? "99+" : String(n);
  orderBellBadge.classList.toggle("hidden", n === 0);
  orderBellBadge.classList.toggle("flex", n > 0);
}

function clearUnseenOrders() {
  localStorage.setItem(UNSEEN_ORDER_KEY, "0");
  renderOrderBell();
}

async function pollNewOrders() {
  if (!getToken()) return null;
  const lastId = getLastOrderId();
  let data;
  try { data = await adminFetch("/admin/orders/updates?since_id=" + lastId); }
  catch { return null; }

  localStorage.setItem(LAST_ORDER_ID_KEY, String(data.latest_id));
  // 首次載入 (尚未記錄過 id) 只同步基準值，不把既有訂單當成新通知
  if (lastId && data.new_orders.length) {
    localStorage.setItem(UNSEEN_ORDER_KEY, String(getUnseenOrders() + data.new_orders.length));
    const newest = data.new_orders[0];
    if (typeof window !== "undefined" && window.adminToast) {
      window.adminToast(
        data.new_orders.length === 1
          ? `新訂單 #${newest.order_no}（${newest.customer_name}，${formatPrice(newest.total)}）`
          : `有 ${data.new_orders.length} 筆新訂單`);
    }
    if (typeof window !== "undefined" && typeof window.onNewOrders === "function") {
      window.onNewOrders(data.new_orders);
    }
  }
  renderOrderBell();
  return data;
}

function startOrderWatcher() {
  if (!getToken()) return null;
  renderOrderBell();
  pollNewOrders();
  return setInterval(pollNewOrders, ORDER_POLL_INTERVAL);
}

if (typeof document !== "undefined" && document.addEventListener) {
  document.addEventListener("DOMContentLoaded", startOrderWatcher);
}

// Node.js test runner 使用；瀏覽器端沒有 module，因此不影響正式管理系統。
if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    API_BASE,
    TOKEN_KEY,
    ADMIN_NAME_KEY,
    LAST_ORDER_ID_KEY,
    UNSEEN_ORDER_KEY,
    STATUS_STYLE,
    getToken,
    requireLogin,
    logout,
    adminFetch,
    formatPrice,
    fillAdminName,
    statusBadge,
    getUnseenOrders,
    clearUnseenOrders,
    pollNewOrders,
    ensureOrderBell,
    renderOrderBell,
    startOrderWatcher,
  };
}
