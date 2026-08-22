const { beforeEach, test } = require("node:test");
const assert = require("node:assert/strict");

class MemoryStorage {
  constructor() { this.values = new Map(); }
  getItem(key) { return this.values.has(key) ? this.values.get(key) : null; }
  setItem(key, value) { this.values.set(key, String(value)); }
  removeItem(key) { this.values.delete(key); }
  clear() { this.values.clear(); }
}

function fakeElement(id = "") {
  const classes = new Set();
  return {
    id,
    className: "",
    textContent: "",
    _t: null,
    style: {
      opacity: "",
      setProperty(name, value) { this[name] = value; },
    },
    classList: {
      contains(name) { return classes.has(name); },
      toggle(name, force) {
        const enabled = force === undefined ? !classes.has(name) : Boolean(force);
        if (enabled) classes.add(name); else classes.delete(name);
        return enabled;
      },
    },
  };
}

const elements = new Map();
global.localStorage = new MemoryStorage();
global.document = {
  addEventListener() {},
  getElementById(id) { return elements.get(id) || null; },
  createElement() { return fakeElement(); },
  body: {
    appendChild(element) { elements.set(element.id, element); },
  },
};

const site = require("../js/site.js");

beforeEach(() => {
  localStorage.clear();
  elements.clear();
  global.fetch = undefined;
  global.clearTimeout = () => {};
  global.setTimeout = () => 123;
});

test("apiGet and getPackage return JSON and encode product ids", async () => {
  const calls = [];
  global.fetch = async (url) => {
    calls.push(url);
    return { ok: true, json: async () => ({ id: 8 }) };
  };

  assert.deepEqual(await site.apiGet("/products"), { id: 8 });
  assert.deepEqual(await site.getPackage("禮盒 A/B"), { id: 8 });
  assert.deepEqual(calls, ["/api/products", "/api/package?product_id=%E7%A6%AE%E7%9B%92%20A%2FB"]);
});

test("apiGet reports API and fallback errors", async () => {
  global.fetch = async () => ({ ok: false, json: async () => ({ error: "不存在" }) });
  await assert.rejects(site.apiGet("/missing"), /不存在/);

  global.fetch = async () => ({ ok: false, json: async () => { throw new Error("bad json"); } });
  await assert.rejects(site.apiGet("/missing"), /API 錯誤/);
});

test("apiPost serializes the request and handles errors", async () => {
  let request;
  global.fetch = async (url, options) => {
    request = { url, options };
    return { ok: true, json: async () => ({ order_id: 10 }) };
  };

  assert.deepEqual(await site.apiPost("/orders", { total: 500 }), { order_id: 10 });
  assert.equal(request.url, "/api/orders");
  assert.equal(request.options.method, "POST");
  assert.equal(request.options.headers["Content-Type"], "application/json");
  assert.equal(request.options.body, '{"total":500}');

  global.fetch = async () => ({ ok: false, json: async () => ({ error: "庫存不足" }) });
  await assert.rejects(site.apiPost("/orders", {}), /庫存不足/);
});

test("cart storage tolerates invalid data", () => {
  assert.deepEqual(site.getCart(), []);
  localStorage.setItem(site.CART_KEY, "not-json");
  assert.deepEqual(site.getCart(), []);
});

test("cart operations add, merge, update, remove, and clear packages", () => {
  const badge = fakeElement("cart-count");
  elements.set("cart-count", badge);
  const pkg = { id: 7, name: "經典禮盒", spec: "6 入", price: 680, image: "/gift.jpg" };

  site.addToCart(pkg, 2);
  site.addToCart(pkg, 1);
  assert.equal(site.cartCount(), 3);
  assert.deepEqual(site.getCart(), [{
    package_id: 7,
    name: "經典禮盒",
    spec: "6 入",
    price: 680,
    image: "/gift.jpg",
    quantity: 3,
  }]);
  assert.equal(badge.textContent, 3);
  assert.equal(badge.classList.contains("hidden"), false);

  site.setQuantity(999, 1);
  site.setQuantity(7, 5);
  assert.equal(site.cartCount(), 5);
  site.setQuantity(7, 0);
  assert.deepEqual(site.getCart(), []);

  site.addToCart({ id: 8, name: "小禮盒", price: 300 });
  site.removeFromCart(8);
  assert.deepEqual(site.getCart(), []);
  site.saveCart([{ package_id: 1, quantity: 4 }]);
  site.clearCart();
  assert.equal(site.cartCount(), 0);
});

test("badge handles missing elements and toast is reused", () => {
  site.updateCartBadge();
  site.showToast("第一次");
  const toast = elements.get("site-toast");
  assert.equal(toast.textContent, "第一次");
  assert.equal(toast.style.opacity, "1");
  assert.equal(toast._t, 123);

  site.showToast("第二次");
  assert.equal(elements.get("site-toast"), toast);
  assert.equal(toast.textContent, "第二次");
});

test("formatPrice formats New Taiwan dollars", () => {
  assert.match(site.formatPrice(123456), /^NT\$\s*123,456$/);
});
