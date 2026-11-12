// Demo step 2: one mfg_state.products document, printed compactly (one line per embedded element).
const d = db.products.findOne({ _id: "BK-M68B-38" });
const day = (x) => (x ? x.toISOString().slice(0, 10) : null);
print(`_id: ${d._id}   name: ${d.name}   list_price: ${d.list_price}   category: ${d.subcategory.category.name} / ${d.subcategory.name}`);
print(`bom (${d.bom.length} components):`);
d.bom.slice(0, 6).forEach((b) => print(`  ${b.component_number.padEnd(10)} qty ${b.per_assembly_qty}  from ${day(b.start_date)}  until ${day(b.end_date)}`));
if (d.bom.length > 6) print(`  ... ${d.bom.length - 6} more`);
print(`inventory:`);
d.inventory.forEach((i) => print(`  ${i.location.name.padEnd(24)} quantity ${i.quantity}`));
print(`vendors: ${d.vendors.length ? "" : "[]  (built in-house; bought-in components carry their own vendors:)"}`);
const c = db.products.findOne({ _id: d.bom.find((b) => db.products.findOne({ _id: b.component_number, make_flag: 0, "vendors.0": { $exists: true } })).component_number });
print(`  ${c._id} ${c.name}:`);
c.vendors.forEach((v) => print(`    ${v.vendor.name.padEnd(30)} lead time ${v.average_lead_time} days   standard price ${v.standard_price}`));
