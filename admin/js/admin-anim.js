/* 美師傅後台動畫 (不含 RWD)
 * - 頁面/側邊欄載入進場
 * - 卡片與表格列捲動/載入交錯浮現
 * - 統計數字 count-up (自動偵測數值變化)
 * - Modal 開關縮放淡入
 * - 全域 toast 提示 adminToast()
 * 尊重 prefers-reduced-motion。
 */
(function () {
  "use strict";

  const REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const style = document.createElement("style");
  style.textContent = `
    /* 進場 */
    .aw-reveal { opacity: 0; transform: translateY(20px); }
    .aw-reveal.aw-in {
      opacity: 1; transform: none;
      transition: opacity .55s cubic-bezier(.22,.61,.36,1), transform .55s cubic-bezier(.22,.61,.36,1);
      transition-delay: var(--aw-delay, 0ms);
    }
    main { animation: awFade .45s ease-out both; }
    @keyframes awFade { from { opacity: 0; transform: translateY(8px) } to { opacity: 1; transform: none } }

    /* 側邊欄 */
    aside nav a { position: relative; animation: awSlide .5s cubic-bezier(.22,.61,.36,1) both; }
    @keyframes awSlide { from { opacity: 0; transform: translateX(-14px) } to { opacity: 1; transform: none } }
    aside nav a > .material-symbols-outlined { transition: transform .3s cubic-bezier(.34,1.56,.64,1); }
    aside nav a:hover > .material-symbols-outlined { transform: scale(1.18) rotate(-6deg); }
    aside nav a[aria-current="page"]::before {
      content: ""; position: absolute; left: -12px; top: 50%; width: 4px; height: 0; border-radius: 4px;
      background: #874e58; transform: translateY(-50%);
      animation: awBar .45s .25s cubic-bezier(.22,.61,.36,1) forwards;
    }
    @keyframes awBar { to { height: 60% } }

    /* 卡片 hover 提升 */
    main .rounded-xl, main .rounded-2xl, main .rounded-\\[24px\\], main .rounded-3xl {
      transition: transform .3s cubic-bezier(.22,.61,.36,1), box-shadow .3s ease;
    }

    /* 表格列 */
    tbody tr.aw-row { animation: awRow .45s cubic-bezier(.22,.61,.36,1) both; animation-delay: var(--aw-delay, 0ms); }
    @keyframes awRow { from { opacity: 0; transform: translateY(10px) } to { opacity: 1; transform: none } }
    tbody tr { transition: background-color .2s ease; }

    /* 數字 */
    .aw-num { font-variant-numeric: tabular-nums; }

    /* Modal */
    [id$="-modal"] { transition: opacity .25s ease; }
    [id$="-modal"].aw-modal-hidden { opacity: 0; }
    [id$="-modal"] > div {
      transition: transform .32s cubic-bezier(.22,.61,.36,1), opacity .32s ease;
    }
    [id$="-modal"].aw-modal-hidden > div { transform: scale(.94) translateY(14px); opacity: 0; }

    /* 按鈕按壓回饋 */
    button:not(:disabled):active { transform: scale(.97); }
    button { transition: transform .12s ease, box-shadow .25s ease, background-color .2s ease; }

    /* Toast */
    #aw-toast {
      position: fixed; bottom: 32px; left: 50%; z-index: 200;
      padding: 14px 28px; border-radius: 9999px; font-weight: 600; letter-spacing: .03em;
      background: #6e555d; color: #fff; box-shadow: 0 12px 32px rgba(72,38,46,.28);
      transform: translate(-50%, 20px); opacity: 0; pointer-events: none;
      transition: transform .35s cubic-bezier(.22,.61,.36,1), opacity .35s ease;
    }
    #aw-toast.show { transform: translate(-50%, 0); opacity: 1; }
    #aw-toast.error { background: #ba1a1a; }

    /* 載入骨架 */
    .aw-skeleton {
      background: linear-gradient(90deg, #ffe8eb 25%, #fff0f1 50%, #ffe8eb 75%);
      background-size: 200% 100%; animation: awShimmer 1.4s infinite;
    }
    @keyframes awShimmer { from { background-position: 200% 0 } to { background-position: -200% 0 } }

    @media (prefers-reduced-motion: reduce) {
      *, *::before { animation: none !important; transition: none !important; }
      .aw-reveal { opacity: 1; transform: none; }
    }
  `;
  document.head.appendChild(style);

  /* ---------- Toast (全域) ---------- */
  window.adminToast = function (msg, isError) {
    let el = document.getElementById("aw-toast");
    if (!el) {
      el = document.createElement("div");
      el.id = "aw-toast";
      document.body.appendChild(el);
    }
    el.textContent = msg;
    el.classList.toggle("error", !!isError);
    requestAnimationFrame(() => el.classList.add("show"));
    clearTimeout(el._t);
    el._t = setTimeout(() => el.classList.remove("show"), 2600);
  };

  document.addEventListener("DOMContentLoaded", () => {
    initSidebar();
    initModals();
    if (REDUCED) return;
    initReveal();
    initRowStagger();
    initCountUp();
  });

  /* ---------- 側邊欄交錯進場 ---------- */
  function initSidebar() {
    document.querySelectorAll("aside nav a").forEach((a, i) => {
      a.style.animationDelay = 60 + i * 55 + "ms";
    });
  }

  /* ---------- 區塊浮現 ---------- */
  let observer;
  function reveal(el, delay) {
    if (!el || el.classList.contains("aw-reveal")) return;
    el.classList.add("aw-reveal");
    el.style.setProperty("--aw-delay", (delay || 0) + "ms");
    observer.observe(el);
  }

  function initReveal() {
    observer = new IntersectionObserver((entries) => {
      entries.forEach((e) => {
        if (e.isIntersecting) {
          e.target.classList.add("aw-in");
          observer.unobserve(e.target);
        }
      });
    }, { threshold: 0.08, rootMargin: "0px 0px -30px 0px" });

    // 統計卡片與內容區塊 (grid 直接子層 + section)
    const targets = document.querySelectorAll(
      "main section, main > div > .grid > div, main .grid > div, main article");
    let lastParent = null, idx = 0;
    targets.forEach((el) => {
      if (el.closest(".aw-reveal")) return;         // 避免巢狀重複
      if (el.parentElement !== lastParent) { lastParent = el.parentElement; idx = 0; }
      reveal(el, Math.min(idx * 70, 350));
      idx += 1;
    });
  }

  /* ---------- 表格列交錯 ---------- */
  function initRowStagger() {
    document.querySelectorAll("tbody").forEach((tbody) => {
      const mo = new MutationObserver(() => {
        Array.from(tbody.rows).forEach((tr, i) => {
          tr.classList.remove("aw-row");
          void tr.offsetWidth;
          tr.style.setProperty("--aw-delay", Math.min(i * 45, 400) + "ms");
          tr.classList.add("aw-row");
        });
      });
      mo.observe(tbody, { childList: true });
    });
  }

  /* ---------- 數字 count-up ---------- */
  const NUM_RE = /^(\D*?)([\d,]+(?:\.\d+)?)(\D*)$/;

  function countUp(el, text) {
    const m = String(text).match(NUM_RE);
    if (!m) return;
    const [, prefix, rawNum, suffix] = m;
    const target = parseFloat(rawNum.replace(/,/g, ""));
    if (!isFinite(target) || target === 0) return;
    const decimals = (rawNum.split(".")[1] || "").length;
    const grouped = rawNum.includes(",");
    const from = 0;
    const dur = 900;
    const t0 = performance.now();
    el.__awAnimating = true;
    el.classList.add("aw-num");

    function frame(now) {
      const p = Math.min((now - t0) / dur, 1);
      const eased = 1 - Math.pow(1 - p, 3);
      const val = from + (target - from) * eased;
      const shown = grouped
        ? val.toLocaleString("zh-TW", { minimumFractionDigits: decimals, maximumFractionDigits: decimals })
        : val.toFixed(decimals);
      el.textContent = prefix + shown + suffix;
      if (p < 1) requestAnimationFrame(frame);
      else { el.textContent = prefix + rawNum + suffix; el.__awAnimating = false; }
    }
    requestAnimationFrame(frame);
  }

  function initCountUp() {
    const SEL = ".font-display, .text-display, .text-headline-lg, .font-headline-lg";
    const seen = new WeakMap();
    const mo = new MutationObserver((muts) => {
      muts.forEach((mu) => {
        const el = mu.target.nodeType === 3 ? mu.target.parentElement : mu.target;
        if (!el || el.__awAnimating || !el.matches?.(SEL)) return;
        if (el.children.length) return;                   // 只處理純文字節點
        const text = el.textContent.trim();
        if (!text || text === seen.get(el) || text.includes("--")) return;
        seen.set(el, text);
        countUp(el, text);
      });
    });
    const main = document.querySelector("main");
    if (main) mo.observe(main, { childList: true, characterData: true, subtree: true });
  }

  /* ---------- Modal 縮放淡入 ---------- */
  function initModals() {
    document.querySelectorAll('[id$="-modal"]').forEach((modal) => {
      if (modal.classList.contains("hidden")) modal.classList.add("aw-modal-hidden");
      const mo = new MutationObserver(() => {
        const hidden = modal.classList.contains("hidden");
        if (!hidden && modal.classList.contains("aw-modal-hidden")) {
          requestAnimationFrame(() => modal.classList.remove("aw-modal-hidden"));
        } else if (hidden && !modal.classList.contains("aw-modal-hidden")) {
          modal.classList.add("aw-modal-hidden");
        }
      });
      mo.observe(modal, { attributes: true, attributeFilter: ["class"] });
    });
  }
})();
