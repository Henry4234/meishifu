const { beforeEach, test } = require("node:test");
const assert = require("node:assert/strict");

class MemoryStorage {
  constructor() { this.values = new Map(); }
  getItem(key) { return this.values.has(key) ? this.values.get(key) : null; }
  setItem(key, value) { this.values.set(key, String(value)); }
  removeItem(key) { this.values.delete(key); }
  clear() { this.values.clear(); }
}

function fakeElement(tag = "div") {
  const classes = new Set();
  const el = {
    tagName: tag.toUpperCase(),
    id: "",
    className: "",
    title: "",
    textContent: "",
    children: [],
    listeners: {},
    classList: {
      contains(name) { return classes.has(name); },
      toggle(name, force) {
        const enabled = force === undefined ? !classes.has(name) : Boolean(force);
        if (enabled) classes.add(name); else classes.delete(name);
        return enabled;
      },
    },
    appendChild(child) { el.children.push(child); return child; },
    insertBefore(child) { el.children.unshift(child); return child; },
    addEventListener(type, fn) { el.listeners[type] = fn; },
  };
  Object.defineProperty(el, "firstElementChild", { get: () => el.children[0] || null });
  Object.defineProperty(el, "lastElementChild", {
    get: () => el.children[el.children.length - 1] || null,
  });
  return el;
}

const elements = new Map();
let header = null;
const documentListeners = {};
global.localStorage = new MemoryStorage();
global.window = { location: { href: "", pathname: "/admin/dashboard.html" } };
global.document = {
  getElementById(id) { return elements.get(id) || null; },
  createElement(tag) { return fakeElement(tag); },
  querySelector(selector) { return selector === "header" ? header : null; },
  addEventListener(type, fn) { documentListeners[type] = fn; },
};

const ADMIN_PATH = require.resolve("../js/admin.js");
const admin = require(ADMIN_PATH);

/* 鈴鐺元素會快取在模組內,需要重新載入才能拿到乾淨狀態 */
function reloadAdmin() {
  delete require.cache[ADMIN_PATH];
  return require(ADMIN_PATH);
}

beforeEach(() => {
  localStorage.clear();
  elements.clear();
  header = null;
  window.location.href = "";
  window.location.pathname = "/admin/dashboard.html";
  delete window.adminToast;
  delete window.onNewOrders;
  global.fetch = undefined;
});

test("getToken and requireLogin enforce authentication", () => {
  assert.equal(admin.getToken(), null);
  admin.requireLogin();
  assert.equal(window.location.href, "login.html");

  window.location.href = "dashboard.html";
  localStorage.setItem(admin.TOKEN_KEY, "token-123");
  assert.equal(admin.getToken(), "token-123");
  admin.requireLogin();
  assert.equal(window.location.href, "dashboard.html");
});

test("logout clears credentials and redirects", () => {
  localStorage.setItem(admin.TOKEN_KEY, "token");
  localStorage.setItem(admin.ADMIN_NAME_KEY, "王師傅");
  admin.logout();
  assert.equal(localStorage.getItem(admin.TOKEN_KEY), null);
  assert.equal(localStorage.getItem(admin.ADMIN_NAME_KEY), null);
  assert.equal(window.location.href, "login.html");
});

test("adminFetch sends JWT and merges request headers", async () => {
  localStorage.setItem(admin.TOKEN_KEY, "jwt-value");
  let request;
  global.fetch = async (url, options) => {
    request = { url, options };
    return { ok: true, status: 200, json: async () => ({ rows: [1] }) };
  };

  const data = await admin.adminFetch("/manage/orders", {
    method: "PATCH",
    headers: { "X-Trace": "trace-id" },
  });
  assert.deepEqual(data, { rows: [1] });
  assert.equal(request.url, "/api/manage/orders");
  assert.equal(request.options.method, "PATCH");
  assert.equal(request.options.headers.Authorization, "Bearer jwt-value");
  assert.equal(request.options.headers["Content-Type"], "application/json");
  assert.equal(request.options.headers["X-Trace"], "trace-id");
});

test("adminFetch handles unauthorized, API, and invalid JSON errors", async () => {
  localStorage.setItem(admin.TOKEN_KEY, "expired");
  global.fetch = async () => ({ status: 401, ok: false, json: async () => ({}) });
  await assert.rejects(admin.adminFetch("/manage/orders"), /登入逾期/);
  assert.equal(localStorage.getItem(admin.TOKEN_KEY), null);
  assert.equal(window.location.href, "login.html");

  global.fetch = async () => ({ status: 400, ok: false, json: async () => ({ error: "資料錯誤" }) });
  await assert.rejects(admin.adminFetch("/manage/orders"), /資料錯誤/);

  global.fetch = async () => ({ status: 500, ok: false, json: async () => { throw new Error("bad json"); } });
  await assert.rejects(admin.adminFetch("/manage/orders"), /API 錯誤/);
});

test("formatting and admin name helpers update the UI", () => {
  assert.match(admin.formatPrice(98765), /^NT\$\s*98,765$/);
  admin.fillAdminName();

  const name = { textContent: "" };
  elements.set("admin-name", name);
  admin.fillAdminName();
  assert.equal(name.textContent, "管理員");
  localStorage.setItem(admin.ADMIN_NAME_KEY, "林師傅");
  admin.fillAdminName();
  assert.equal(name.textContent, "林師傅");
});

test("statusBadge renders known and fallback order statuses", () => {
  assert.match(admin.statusBadge("paid"), /已付款/);
  assert.match(admin.statusBadge("paid"), /bg-primary-fixed/);
  assert.match(admin.statusBadge("refunded"), /refunded/);
  assert.match(admin.statusBadge("refunded"), /bg-surface-variant/);
});

/* ---------- 新訂單通知 ---------- */
function buildHeader() {
  header = fakeElement("header");
  header.appendChild(fakeElement("div"));        // 麵包屑
  const group = fakeElement("div");              // 右側管理員資訊
  group.appendChild(fakeElement("p"));
  header.appendChild(group);
  return group;
}

function stubUpdates(response) {
  const requests = [];
  global.fetch = async (url, options) => {
    requests.push(url);
    return { ok: true, status: 200, json: async () => response, options };
  };
  return requests;
}

test("order bell is injected into the header only once", () => {
  const mod = reloadAdmin();
  assert.equal(mod.ensureOrderBell(), null);      // 沒有 header 就不建立

  const group = buildHeader();
  const bell = mod.ensureOrderBell();
  assert.equal(bell.id, "order-bell");
  assert.equal(group.firstElementChild, bell);
  assert.equal(mod.ensureOrderBell(), bell);
  assert.equal(group.children.length, 2);
});

test("polling records a baseline before announcing new orders", async () => {
  const mod = reloadAdmin();
  buildHeader();
  assert.equal(await mod.pollNewOrders(), null);   // 未登入不輪詢

  localStorage.setItem(mod.TOKEN_KEY, "jwt");
  const order = { id: 9, order_no: "MS1", customer_name: "王小明", total: 1120 };
  const requests = stubUpdates({ new_orders: [order], latest_id: 9, pending_orders: 2 });

  // 首次載入只同步基準 id,不把既有訂單當成新通知
  const toasts = [];
  window.adminToast = (msg) => toasts.push(msg);
  await mod.pollNewOrders();
  assert.equal(requests[0], "/api/admin/orders/updates?since_id=0");
  assert.equal(localStorage.getItem(mod.LAST_ORDER_ID_KEY), "9");
  assert.equal(mod.getUnseenOrders(), 0);
  assert.equal(toasts.length, 0);
});

test("new orders raise a toast, a badge count, and the page hook", async () => {
  const mod = reloadAdmin();
  buildHeader();
  localStorage.setItem(mod.TOKEN_KEY, "jwt");
  localStorage.setItem(mod.LAST_ORDER_ID_KEY, "8");

  const toasts = [];
  const hooked = [];
  window.adminToast = (msg) => toasts.push(msg);
  window.onNewOrders = (orders) => hooked.push(orders.length);
  stubUpdates({
    new_orders: [{ id: 9, order_no: "MS1", customer_name: "王小明", total: 1120 }],
    latest_id: 9,
    pending_orders: 2,
  });

  await mod.pollNewOrders();
  assert.equal(mod.getUnseenOrders(), 1);
  assert.match(toasts[0], /新訂單 #MS1/);
  assert.match(toasts[0], /NT\$\s*1,120/);
  assert.deepEqual(hooked, [1]);

  // 多筆時改為顯示總數
  stubUpdates({
    new_orders: [{ id: 11, order_no: "MS3", customer_name: "李", total: 500 },
                 { id: 10, order_no: "MS2", customer_name: "陳", total: 800 }],
    latest_id: 11,
    pending_orders: 4,
  });
  await mod.pollNewOrders();
  assert.equal(mod.getUnseenOrders(), 3);
  assert.match(toasts[1], /有 2 筆新訂單/);

  mod.clearUnseenOrders();
  assert.equal(mod.getUnseenOrders(), 0);
});

test("bell click clears the badge and opens the order page", async () => {
  const mod = reloadAdmin();
  const group = buildHeader();
  localStorage.setItem(mod.TOKEN_KEY, "jwt");
  localStorage.setItem(mod.UNSEEN_ORDER_KEY, "3");
  const bell = mod.ensureOrderBell();
  mod.renderOrderBell();

  bell.listeners.click();
  assert.equal(mod.getUnseenOrders(), 0);
  assert.equal(window.location.href, "orders.html");
  assert.equal(group.firstElementChild, bell);

  // 已經在訂單頁時只清除標記,不再重新導向
  window.location.href = "";
  window.location.pathname = "/admin/orders.html";
  bell.listeners.click();
  assert.equal(window.location.href, "");
});

test("watcher starts only when logged in and survives API errors", async () => {
  const mod = reloadAdmin();
  buildHeader();
  assert.equal(mod.startOrderWatcher(), null);

  localStorage.setItem(mod.TOKEN_KEY, "jwt");
  global.fetch = async () => { throw new Error("network down"); };
  assert.equal(await mod.pollNewOrders(), null);

  stubUpdates({ new_orders: [], latest_id: 0, pending_orders: 0 });
  const timer = mod.startOrderWatcher();
  assert.notEqual(timer, null);
  clearInterval(timer);
});
