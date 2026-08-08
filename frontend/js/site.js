/* 美師傅前台共用腳本:API 呼叫與購物車 (localStorage) */
const API_BASE = "http://localhost:5001/api";
// v2:購物車改以禮盒 (package) 為單位,舊格式自動作廢
const CART_KEY = "meishifu_cart_v2";

/* ---------- API ---------- */
async function apiGet(path) {
  const res = await fetch(API_BASE + path);
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || "API 錯誤");
  return res.json();
}

async function apiPost(path, body) {
  const res = await fetch(API_BASE + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || "API 錯誤");
  return data;
}

/* ---------- 購物車 ---------- */
function getCart() {
  try { return JSON.parse(localStorage.getItem(CART_KEY)) || []; }
  catch { return []; }
}

function saveCart(cart) {
  localStorage.setItem(CART_KEY, JSON.stringify(cart));
  updateCartBadge();
}

function addToCart(pkg, qty = 1) {
  const cart = getCart();
  const found = cart.find((i) => i.package_id === pkg.id);
  if (found) found.quantity += qty;
  else cart.push({
    package_id: pkg.id,
    name: pkg.name,
    spec: pkg.spec || "",
    price: pkg.price,
    image: pkg.image || "",
    quantity: qty,
  });
  saveCart(cart);
  showToast(`已將「${pkg.name}」加入購物車`);
}

function setQuantity(packageId, qty) {
  let cart = getCart();
  const item = cart.find((i) => i.package_id === packageId);
  if (!item) return;
  item.quantity = qty;
  if (item.quantity <= 0) cart = cart.filter((i) => i.package_id !== packageId);
  saveCart(cart);
}

function removeFromCart(packageId) {
  saveCart(getCart().filter((i) => i.package_id !== packageId));
}

function clearCart() { saveCart([]); }

function cartCount() {
  return getCart().reduce((s, i) => s + i.quantity, 0);
}

function updateCartBadge() {
  const el = document.getElementById("cart-count");
  if (!el) return;
  const n = cartCount();
  el.textContent = n;
  el.classList.toggle("hidden", n === 0);
}

/* ---------- 小工具 ---------- */
function formatPrice(n) { return "NT$ " + Number(n).toLocaleString("zh-TW"); }

function showToast(msg) {
  let toast = document.getElementById("site-toast");
  if (!toast) {
    toast = document.createElement("div");
    toast.id = "site-toast";
    toast.className =
      "fixed bottom-8 left-1/2 -translate-x-1/2 z-[100] bg-primary text-on-primary " +
      "px-6 py-3 rounded-full shadow-lg font-label-md transition-opacity duration-300 opacity-0 pointer-events-none";
    document.body.appendChild(toast);
  }
  toast.textContent = msg;
  toast.style.opacity = "1";
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (toast.style.opacity = "0"), 2000);
}

document.addEventListener("DOMContentLoaded", updateCartBadge);
