const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const lp = require("../js/label-print.js");

const LABELS_DIR = path.join(__dirname, "..", "labels");
const loadLabel = (name) =>
  JSON.parse(fs.readFileSync(path.join(LABELS_DIR, `${name}.json`), "utf8"));

const ORDER = {
  order_no: "MO202609061512251314",
  customer_name: "王小明",
  shipping_label: "7-11 交貨便",
  shipping_method: "unimart",
  store_name: "美麗門市",
  store_id: "991182",
  items: [
    { product_name: "珍味禮盒", quantity: 2 },
    { product_name: "賞味禮盒", quantity: 1 },
  ],
};

const NUTRITION = ["享味禮盒", "品味禮盒", "巧味禮盒", "珍味禮盒", "美味禮盒", "賞味禮盒"];

test("訂單資訊模板的綁定欄位與程式提供的值完全對應", () => {
  const template = loadLabel(lp.ORDER_LABEL);
  const cols = lp.boundColumns(template).sort();
  assert.deepEqual(cols, ["customer_name", "order_no", "shipping_method", "store_id"]);
  // 模板要什麼、程式就得給什麼,少一個標籤上就會開天窗
  assert.deepEqual(Object.keys(lp.orderLabelValues(ORDER)).sort(), cols);
});

test("每一款上架禮盒都要有對應的營養標示模板", () => {
  const files = fs.readdirSync(LABELS_DIR)
    .filter((f) => f.endsWith(".json"))
    .map((f) => f.replace(/\.json$/, ""));
  NUTRITION.forEach((name) => assert.ok(files.includes(name), `缺少 ${name}.json`));
  assert.ok(files.includes(lp.ORDER_LABEL));
});

test("fillTemplate 把綁定格改寫為固定文字,且不動到原模板", () => {
  const template = loadLabel(lp.ORDER_LABEL);
  const before = JSON.stringify(template);
  const values = lp.orderLabelValues(ORDER);
  const { template: filled, missing, filled: done } = lp.fillTemplate(template, values, {
    itemsText: lp.formatOrderItems(ORDER.items),
  });

  assert.equal(JSON.stringify(template), before, "原模板不可被修改");
  assert.deepEqual(missing, []);
  assert.equal(done.length, 4);
  // 綁定欄位應全部消失,改為固定文字
  assert.deepEqual(lp.boundColumns(filled), []);

  const texts = [];
  lp.eachElement(filled, (el) => { if (el.content) texts.push(el.content); });
  assert.ok(texts.includes("王小明"));
  assert.ok(texts.includes("MO202609061512251314"));
  assert.ok(texts.includes("7-11 交貨便"));
  assert.ok(texts.includes("美麗門市 (991182)"));
  assert.ok(texts.includes("珍味禮盒 * 2\n賞味禮盒 * 1"));

  lp.eachElement(filled, (el) => {
    if (el.content && el.dataColumnName === undefined) {
      assert.equal(el.contentType, lp.CONTENT_STATIC);
    }
  });
});

test("品項寫進合併的那一格,並開啟自動換行", () => {
  const { template: filled } = lp.fillTemplate(
    loadLabel(lp.ORDER_LABEL), lp.orderLabelValues(ORDER),
    { itemsText: lp.formatOrderItems(ORDER.items) });
  const table = filled.Page.find((p) => p.layerClass === "Table");
  const cell = table.Cells[lp.ITEMS_CELL_INDEX];
  assert.match(cell.content, /珍味禮盒 \* 2/);
  assert.equal(cell.autoReturn, 1);   // 品項多時要能換行
  assert.equal(cell.horAlignment, 0);

  // 合併範圍確認:group 內含 "5,3,7,5" 表示第 5~7 列、3~5 欄合併成一格
  assert.ok(table.group.split(";").includes("5,3,7,5"));
});

test("缺少值的綁定欄位會被回報,不會靜默印出空白", () => {
  const { missing } = lp.fillTemplate(loadLabel(lp.ORDER_LABEL), { order_no: "X" });
  assert.deepEqual(missing.sort(), ["customer_name", "shipping_method", "store_id"]);
});

test("formatStore 與 formatOrderItems 的邊界情況", () => {
  assert.equal(lp.formatStore({ store_name: "美麗門市", store_id: "991182" }), "美麗門市 (991182)");
  assert.equal(lp.formatStore({ store_name: "美麗門市" }), "美麗門市");
  assert.equal(lp.formatStore({ address: "台北市" }), "-");   // 宅配沒有門市
  assert.equal(lp.formatOrderItems([]), "-");
  assert.equal(lp.formatOrderItems(undefined), "-");
  assert.equal(lp.formatOrderItems([{ package_name: "美味禮盒", quantity: 3 }]), "美味禮盒 * 3");
});

test("buildPrintPlan 依訂購盒數決定營養標示份數", () => {
  const plan = lp.buildPrintPlan(ORDER, NUTRITION);
  assert.equal(plan.jobs.length, 3);
  assert.deepEqual(plan.jobs.map((j) => [j.kind, j.label, j.copies]), [
    ["order", "訂單資訊", 1],
    ["nutrition", "珍味禮盒", 2],
    ["nutrition", "賞味禮盒", 1],
  ]);
  assert.equal(plan.totalLabels, 4);       // 1 張訂單 + 3 張營養標示
  assert.deepEqual(plan.missingLabels, []);
  // 檔名要 URL 編碼,中文檔名才抓得到
  assert.equal(plan.jobs[1].file, "labels/" + encodeURIComponent("珍味禮盒") + ".json");
});

test("沒有營養標示模板的禮盒要被列出,而不是默默略過", () => {
  const plan = lp.buildPrintPlan(
    { order_no: "X", items: [{ product_name: "未知禮盒", quantity: 1 }] }, NUTRITION);
  assert.deepEqual(plan.missingLabels, ["未知禮盒"]);
  assert.equal(plan.jobs.length, 1);       // 只剩訂單資訊
  assert.equal(plan.totalLabels, 1);
});

test("每個營養標示模板都是有效的 60x40mm 標籤且無未填欄位", () => {
  NUTRITION.forEach((name) => {
    const t = loadLabel(name);
    assert.equal(t.layerClass, "LPAPI", name);
    assert.equal(t.labelWidth, 60, name);
    assert.equal(t.labelHeight, 40, name);
    // 營養標示是固定內容,不該有需要填值的綁定欄位
    assert.deepEqual(lp.boundColumns(t), [], `${name} 不應有綁定欄位`);
  });

  const order = loadLabel(lp.ORDER_LABEL);
  assert.equal(order.labelWidth, 40);
  assert.equal(order.labelHeight, 60);
});
