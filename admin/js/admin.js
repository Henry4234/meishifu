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

// Node.js test runner 使用；瀏覽器端沒有 module，因此不影響正式管理系統。
if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    API_BASE,
    TOKEN_KEY,
    ADMIN_NAME_KEY,
    STATUS_STYLE,
    getToken,
    requireLogin,
    logout,
    adminFetch,
    formatPrice,
    fillAdminName,
    statusBadge,
  };
}
