/* 訂單標籤列印 (德佟 DP235 / dtpweb 打印助手)
 *
 * 架構重點:dtpweb 是「瀏覽器 → 本機打印助手」的橋接,印表機接在操作人員的電腦上,
 * 所以列印一定在前端執行,後端只負責提供訂單資料。標籤模板 (微打匯出的 .json)
 * 則放在 admin/labels/ 由網站提供,任何一台後台電腦都能取得,不必在每台機器
 * 各自維護 E:\微打 資料夾。
 *
 * 一次列印會產生兩種標籤:
 *   1. 訂單資訊 (40×60mm) — 每張訂單 1 張,填入訂購人/單號/寄送方式/超商/品項
 *   2. 營養標示 (60×40mm) — 每款禮盒各一種模板,依訂購盒數列印相同份數
 */

const LABEL_DIR = "labels";
const ORDER_LABEL = "訂單資訊";

/* 實際使用的標籤紙規格 (mm)。換紙時只要改這裡。
   營養標示模板本來就是 60×40,訂單資訊模板是 40×60 (直向),
   直接印會超出紙張,必須旋轉 90° 才貼得上這捲紙。 */
const LABEL_STOCK = { width: 60, height: 40 };
const SIZE_TOLERANCE = 0.5;   // mm,模板尺寸可能有小數誤差

const near = (a, b) => Math.abs(a - b) <= SIZE_TOLERANCE;

/**
 * 這張標籤要旋轉幾度才貼合標籤紙?
 *   0    尺寸相符,不旋轉
 *   90   模板與標籤紙長寬相反,轉 90° 後相符
 *   null 尺寸對不上,光靠旋轉解決不了 (模板要重做)
 */
function orientationFor(template, stock = LABEL_STOCK) {
  const w = template.labelWidth;
  const h = template.labelHeight;
  if (near(w, stock.width) && near(h, stock.height)) return 0;
  if (near(w, stock.height) && near(h, stock.width)) return 90;
  return null;
}

/* 微打標籤格式的 contentType:0 = 固定文字,2 = 綁定資料來源欄位。
   我們直接把綁定格改寫成固定文字,結果才不依賴打印助手怎麼解析資料來源。 */
const CONTENT_STATIC = 0;
const CONTENT_BOUND = 2;

/* 訂單資訊模板中「訂單資訊」那一列的值儲存格。
   表格 group 為 "5,3,7,5" → 第 5~7 列、第 3~5 欄合併 (1-based),
   對應 7 欄×5 列中的 index 22,可用高度約 20mm。 */
const ITEMS_CELL_INDEX = 22;

/* 走訪標籤模板裡所有繪圖元件 (Page 與 Table 的 Cells 都是巢狀陣列) */
function eachElement(node, fn) {
  if (Array.isArray(node)) {
    node.forEach((n) => eachElement(n, fn));
  } else if (node && typeof node === "object") {
    if (node.layerClass) fn(node);
    Object.values(node).forEach((v) => {
      if (v && typeof v === "object") eachElement(v, fn);
    });
  }
}

/* 模板中所有綁定的資料欄位名稱 (供檢查模板與程式是否對得起來) */
function boundColumns(template) {
  const cols = [];
  eachElement(template, (el) => {
    if (el.dataColumnName) cols.push(el.dataColumnName);
  });
  return cols;
}

/* 把訂單品項排成標籤上的文字:一行一款「禮盒 * 數量」 */
function formatOrderItems(items) {
  if (!items || !items.length) return "-";
  return items
    .map((i) => `${i.product_name || i.package_name || ""} * ${i.quantity}`)
    .join("\n");
}

/* 超商取貨才有門市;宅配/自取顯示 "-" */
function formatStore(order) {
  const name = (order.store_name || "").trim();
  if (!name) return "-";
  const id = (order.store_id || "").trim();
  return id ? `${name} (${id})` : name;
}

/* 訂單 → 模板資料欄位。key 需與 微打 模板中的 dataColumnName 一致。 */
function orderLabelValues(order) {
  return {
    customer_name: order.customer_name || "-",
    order_no: order.order_no || "-",
    shipping_method: order.shipping_label || order.shipping_method || "-",
    store_id: formatStore(order),
  };
}

/* 依 values 把模板的綁定格改寫成固定文字,回傳新的模板物件 (不改動原始模板)。
   回傳的 missing 為模板有綁定、但我們沒有提供值的欄位。 */
function fillTemplate(template, values, options = {}) {
  const filled = JSON.parse(JSON.stringify(template));
  const missing = [];
  const used = new Set();

  eachElement(filled, (el) => {
    const col = el.dataColumnName;
    if (!col) return;
    if (!(col in values)) {
      missing.push(col);
      return;
    }
    el.contentType = CONTENT_STATIC;
    el.content = String(values[col] ?? "");
    delete el.dataColumnName;
    used.add(col);
  });

  // 訂單品項沒有對應的 dataColumnName,寫進表格中合併的那一格
  if (options.itemsText !== undefined) {
    const table = (filled.Page || []).find((p) => p.layerClass === "Table");
    const cell = table && table.Cells && table.Cells[ITEMS_CELL_INDEX];
    if (cell) {
      cell.contentType = CONTENT_STATIC;
      cell.content = options.itemsText;
      cell.autoReturn = 1;      // 讓過長的品項自動換行,不會被裁掉
      cell.horAlignment = 0;    // 靠左,多行才好讀
    }
  }
  return { template: filled, missing: [...new Set(missing)], filled: [...used] };
}

/* 產生列印計畫:要印哪些標籤、各幾份。
   nutritionLabels 為可用的營養標示模板檔名集合 (不含副檔名)。 */
function buildPrintPlan(order, nutritionLabels) {
  const available = new Set(nutritionLabels || []);
  const jobs = [{
    kind: "order",
    label: ORDER_LABEL,
    file: `${LABEL_DIR}/${encodeURIComponent(ORDER_LABEL)}.json`,
    copies: 1,
    title: `訂單資訊 #${order.order_no}`,
  }];

  const missingLabels = [];
  (order.items || []).forEach((item) => {
    const name = item.product_name || item.package_name || "";
    if (!available.has(name)) {
      missingLabels.push(name);
      return;
    }
    jobs.push({
      kind: "nutrition",
      label: name,
      file: `${LABEL_DIR}/${encodeURIComponent(name)}.json`,
      copies: item.quantity,
      title: `${name} 營養標示`,
    });
  });

  return {
    jobs,
    missingLabels,
    totalLabels: jobs.reduce((s, j) => s + j.copies, 0),
  };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    LABEL_DIR,
    ORDER_LABEL,
    LABEL_STOCK,
    orientationFor,
    ITEMS_CELL_INDEX,
    CONTENT_STATIC,
    CONTENT_BOUND,
    eachElement,
    boundColumns,
    formatOrderItems,
    formatStore,
    orderLabelValues,
    fillTemplate,
    buildPrintPlan,
  };
}
