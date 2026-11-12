// Demo step 2: one mfg_state.products document, printed compactly (one line per embedded element).
// Labels in English, or in Simplified Chinese when DEMO_LANG=zh; values are printed as stored.
const zh = (process.env.DEMO_LANG || "") === "zh";
const L = zh
  ? { name: "名称", price: "标价", cat: "类别", bom: (n) => `物料清单（${n} 个组件）：`, qty: "数量", from: "起始", until: "截止",
      more: (n) => `  …… 另有 ${n} 个`, inv: "库存：", vend0: "供应商：[]（自制产品；外购组件各自带有供应商：）",
      lead: "交期", days: "天", std: "标准价" }
  : { name: "name", price: "list_price", cat: "category", bom: (n) => `bom (${n} components):`, qty: "qty", from: "from",
      until: "until", more: (n) => `  ... ${n} more`, inv: "inventory:",
      vend0: "vendors: []  (built in-house; bought-in components carry their own vendors:)", lead: "lead time",
      days: "days", std: "standard price" };
const C = zh ? "：" : ": ";
const d = db.products.findOne({ _id: "BK-M68B-38" });
const day = (x) => (x ? x.toISOString().slice(0, 10) : null);
print(`_id${C}${d._id}   ${L.name}${C}${d.name}   ${L.price}${C}${d.list_price}   ${L.cat}${C}${d.subcategory.category.name} / ${d.subcategory.name}`);
print(L.bom(d.bom.length));
d.bom.slice(0, 6).forEach((b) => print(`  ${b.component_number.padEnd(10)} ${L.qty} ${b.per_assembly_qty}  ${L.from} ${day(b.start_date)}  ${L.until} ${day(b.end_date)}`));
if (d.bom.length > 6) print(L.more(d.bom.length - 6));
print(L.inv);
d.inventory.forEach((i) => print(`  ${i.location.name.padEnd(24)} ${L.qty} ${i.quantity}`));
print(d.vendors.length ? "vendors:" : L.vend0);
const c = db.products.findOne({ _id: d.bom.find((b) => db.products.findOne({ _id: b.component_number, make_flag: 0, "vendors.0": { $exists: true } })).component_number });
print(`  ${c._id} ${c.name}${zh ? "：" : ":"}`);
c.vendors.forEach((v) => print(`    ${v.vendor.name.padEnd(30)} ${L.lead} ${v.average_lead_time} ${L.days}   ${L.std} ${v.standard_price}`));
