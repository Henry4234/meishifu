/* 美師傅前台動畫與 RWD 增強
 * - 捲動進場動畫 (IntersectionObserver + 交錯延遲,支援動態載入的商品卡片)
 * - 頁首捲動陰影 / 手機漢堡選單
 * - 購物車徽章跳動
 * - 行動版樣式修正 (字級、購物車卡片直排)
 * 尊重 prefers-reduced-motion。
 */
(function () {
  "use strict";

  const REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ---------- 注入樣式 ---------- */
  const style = document.createElement("style");
  style.textContent = `
    /* 進場動畫 */
    .anim-reveal { opacity: 0; transform: translateY(28px); }
    .anim-reveal.anim-in {
      opacity: 1; transform: none;
      transition: opacity .7s cubic-bezier(.22,.61,.36,1), transform .7s cubic-bezier(.22,.61,.36,1);
      transition-delay: var(--anim-delay, 0ms);
    }
    /* 頁面淡入 */
    body { animation: pageFade .5s ease-out; }
    @keyframes pageFade { from { opacity: 0 } to { opacity: 1 } }
    /* 頁首捲動狀態 */
    header.is-scrolled { box-shadow: 0 8px 32px rgba(157,129,137,.14) !important; }
    /* 購物車徽章跳動 */
    @keyframes badgePop { 0%{transform:scale(.4)} 60%{transform:scale(1.35)} 100%{transform:scale(1)} }
    .badge-pop { animation: badgePop .35s cubic-bezier(.22,.61,.36,1); }
    /* 手機選單 */
    .mobile-menu-panel {
      position: fixed; inset: 80px 0 auto 0; z-index: 60;
      background: rgba(255,248,247,.97); backdrop-filter: blur(16px);
      box-shadow: 0 24px 48px rgba(157,129,137,.18);
      border-radius: 0 0 1.5rem 1.5rem; overflow: hidden;
      transform: translateY(-12px); opacity: 0; pointer-events: none;
      transition: transform .35s cubic-bezier(.22,.61,.36,1), opacity .35s ease;
    }
    .mobile-menu-panel.open { transform: none; opacity: 1; pointer-events: auto; }
    .mobile-menu-panel a {
      display: flex; align-items: center; justify-content: space-between;
      padding: 16px 28px; font-size: 15px; font-weight: 600; letter-spacing: .05em;
      color: #4e4447; border-bottom: 1px solid rgba(255,202,212,.4);
      opacity: 0; transform: translateX(-16px);
      transition: opacity .4s ease, transform .4s cubic-bezier(.22,.61,.36,1), background .2s;
      transition-delay: var(--anim-delay, 0ms);
    }
    .mobile-menu-panel.open a { opacity: 1; transform: none; }
    .mobile-menu-panel a:active { background: #ffe8eb; }
    .mobile-menu-panel a[aria-current="page"] { color: #874e58; }
    .mobile-menu-backdrop {
      position: fixed; inset: 0; z-index: 55; background: rgba(72,38,46,.25);
      opacity: 0; pointer-events: none; transition: opacity .3s ease;
    }
    .mobile-menu-backdrop.open { opacity: 1; pointer-events: auto; }
    .menu-toggle-btn { display: none; }
    @media (max-width: 1023.98px) { .menu-toggle-btn { display: inline-flex; } }
    /* ---------- RWD 行動版修正 ---------- */
    @media (max-width: 640px) {
      .text-display { font-size: 34px !important; line-height: 1.25 !important; }
      .font-display.text-display { letter-spacing: -0.01em; }
      .text-headline-lg { font-size: 26px !important; }
      /* 購物車商品卡改直排 */
      #cart-items > div { flex-direction: column; align-items: stretch; gap: 16px; padding: 16px; }
      #cart-items > div > div:first-child { width: 100%; height: 176px; }
      /* 結帳卡內距縮小 */
      #cart-view .sticky { position: static; }
    }
    @media (prefers-reduced-motion: reduce) {
      .anim-reveal, .anim-reveal.anim-in, body { animation: none !important; transition: none !important; opacity: 1; transform: none; }
      .mobile-menu-panel, .mobile-menu-panel a { transition: none; }
    }
  `;
  document.head.appendChild(style);

  document.addEventListener("DOMContentLoaded", () => {
    initHeaderEffects();
    initMobileMenu();
    if (!REDUCED) {
      initReveal();
      initBadgePop();
    }
  });

  /* ---------- 捲動進場 ---------- */
  let observer;

  function markReveal(el, delay) {
    if (el.classList.contains("anim-reveal")) return;
    el.classList.add("anim-reveal");
    el.style.setProperty("--anim-delay", (delay || 0) + "ms");
    observer.observe(el);
  }

  function initReveal() {
    observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add("anim-in");
          observer.unobserve(entry.target);
        }
      });
    }, { threshold: 0.12, rootMargin: "0px 0px -40px 0px" });

    // 靜態內容:區塊與卡片,兄弟元素交錯 80ms
    const groups = document.querySelectorAll(
      "main section, main article, .faq-item, footer .grid > div");
    let lastParent = null, idx = 0;
    groups.forEach((el) => {
      if (el.parentElement !== lastParent) { lastParent = el.parentElement; idx = 0; }
      markReveal(el, Math.min(idx * 80, 400));
      idx += 1;
    });

    // 動態載入的商品卡片 (API render 後出現)
    const mo = new MutationObserver((mutations) => {
      mutations.forEach((m) => {
        m.addedNodes.forEach((node) => {
          if (!(node instanceof HTMLElement)) return;
          const cards = node.matches("article") ? [node] : Array.from(node.querySelectorAll?.("article") || []);
          cards.forEach((card, i) => markReveal(card, Math.min(i * 80, 400)));
        });
      });
    });
    ["popular-products", "product-grid"].forEach((id) => {
      const box = document.getElementById(id);
      if (box) mo.observe(box, { childList: true });
    });
  }

  /* ---------- 頁首效果 ---------- */
  function initHeaderEffects() {
    const header = document.querySelector("header");
    if (!header) return;
    const onScroll = () => header.classList.toggle("is-scrolled", window.scrollY > 12);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
  }

  /* ---------- 手機漢堡選單 ---------- */
  function initMobileMenu() {
    const header = document.querySelector("header");
    const nav = header?.querySelector("nav");
    const iconGroup = header?.querySelector(".flex.items-center.gap-6");
    if (!header || !nav || !iconGroup) return;

    // 漢堡按鈕
    const btn = document.createElement("button");
    btn.className = "menu-toggle-btn items-center justify-center w-10 h-10 rounded-full hover:bg-surface-container transition-colors";
    btn.setAttribute("aria-label", "開啟選單");
    btn.innerHTML = '<span class="material-symbols-outlined text-on-surface-variant">menu</span>';
    iconGroup.appendChild(btn);

    // 背景遮罩 + 選單面板 (複製桌面版連結)
    const backdrop = document.createElement("div");
    backdrop.className = "mobile-menu-backdrop";
    const panel = document.createElement("div");
    panel.className = "mobile-menu-panel";
    Array.from(nav.querySelectorAll("a")).forEach((a, i) => {
      const link = document.createElement("a");
      link.href = a.getAttribute("href");
      if (a.getAttribute("aria-current")) link.setAttribute("aria-current", "page");
      link.style.setProperty("--anim-delay", 60 + i * 50 + "ms");
      link.innerHTML = `<span>${a.textContent.trim()}</span><span class="material-symbols-outlined" style="font-size:18px;color:#c1a2ab">arrow_forward_ios</span>`;
      panel.appendChild(link);
    });
    document.body.appendChild(backdrop);
    document.body.appendChild(panel);

    const icon = btn.querySelector(".material-symbols-outlined");
    let open = false;
    const setOpen = (v) => {
      open = v;
      panel.classList.toggle("open", open);
      backdrop.classList.toggle("open", open);
      icon.textContent = open ? "close" : "menu";
      btn.setAttribute("aria-label", open ? "關閉選單" : "開啟選單");
      document.body.style.overflow = open ? "hidden" : "";
    };
    btn.addEventListener("click", () => setOpen(!open));
    backdrop.addEventListener("click", () => setOpen(false));
    panel.addEventListener("click", (e) => { if (e.target.closest("a")) setOpen(false); });
    window.matchMedia("(min-width: 1024px)").addEventListener("change", (e) => {
      if (e.matches) setOpen(false);
    });
  }

  /* ---------- 購物車徽章跳動 ---------- */
  function initBadgePop() {
    const badge = document.getElementById("cart-count");
    if (!badge) return;
    const mo = new MutationObserver(() => {
      if (badge.classList.contains("hidden")) return;
      badge.classList.remove("badge-pop");
      void badge.offsetWidth; // 重新觸發動畫
      badge.classList.add("badge-pop");
    });
    mo.observe(badge, { childList: true, characterData: true, subtree: true });
  }
})();
