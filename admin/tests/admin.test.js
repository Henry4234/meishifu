const { beforeEach, test } = require("node:test");
const assert = require("node:assert/strict");

class MemoryStorage {
  constructor() { this.values = new Map(); }
  getItem(key) { return this.values.has(key) ? this.values.get(key) : null; }
  setItem(key, value) { this.values.set(key, String(value)); }
  removeItem(key) { this.values.delete(key); }
  clear() { this.values.clear(); }
}

const elements = new Map();
global.localStorage = new MemoryStorage();
global.window = { location: { href: "" } };
global.document = {
  getElementById(id) { return elements.get(id) || null; },
};

const admin = require("../js/admin.js");

beforeEach(() => {
  localStorage.clear();
  elements.clear();
  window.location.href = "";
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
