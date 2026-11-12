# Manufacturing dataset as a DAB dataset: source-split design

Status, 2026-11-12: built (code in [`dab/`](dab/README.md)). Setup C is Tapstate's output, verified
field for field against a reference built from the raw AdventureWorks tables (§3). Run with Claude
Opus 5.5 complete; results are in §9. §8 lists where the build differs from the design brief.

Goal: the bank benchmark's question on a second domain. Does a data agent answer manufacturing
questions more accurately and with fewer tokens when it queries one consolidated store (setup C)
instead of the company's separate source systems (setup A)? The data is Microsoft's
[AdventureWorks](https://github.com/microsoft/sql-server-samples) OLTP sample (MIT licence): a
fictitious bicycle manufacturer with sales, production, purchasing and inventory, split here into
the systems such a company runs separately. There is no setup B (key hints) in this benchmark: the
bank benchmark found that hints changed neither accuracy nor tokens measurably.

## 1. Data facts that constrain the design

The OLTP install script's CSV files (release `adventureworks`, archive sha256
`58962e94…6c5bd42`, `dab/fetch_data.py`), read with column names from the script's own
`instawdb.sql` (`dab/aw.py`). This release has its dates shifted: work orders run 2022-06-02 to
2025-06-01 (start dates), sales orders 2022-05-30 to 2025-06-29, purchase orders 2022-04-15 to
2025-09-21.

| Entity | Rows | Notes |
|---|---:|---|
| products | 504 | 295 sold (finished goods), 209 components and materials with no subcategory |
| subcategories / categories / models | 37 / 4 / 128 | categories: Bikes, Components, Clothing, Accessories |
| bill of materials | 2,679 | 103 level-0 rows name no assembly |
| work orders / routing operations | 72,591 / 67,131 | routing at work centers (locations 10–60) |
| stock rows (product × location) | 1,069 | 14 locations |
| vendors / vendor offers | 104 / 460 | offers only for purchased products (make_flag 0) |
| purchase orders / lines | 4,012 / 8,845 | |
| standard cost / list price history | 395 / 395 | current values on the product (504) |
| sales orders / lines | 31,465 / 121,317 | 19,820 customers, 701 stores, 10 territories |

- **The product is the hub.** Every system refers to products, so the product key is what the
  agent must translate. `ProductNumber` (e.g. `BK-R93R-62`) is unique, and contains no lower-case
  letter, `_` or `:`, so each system can write it in its own reversible format.
- **Integer ids are not used across systems.** AdventureWorks joins on `ProductID`; no split
  system exposes it, so an agent cannot join on a shared surrogate key.

## 2. Source systems

Six systems, laid out the way a manufacturer commonly separates them, on DAB's engine mix:

| System | Engine (setup A) | Tables / collections | Product key, as that system writes it |
|---|---|---|---|
| **ERP sales** | Postgres `mfg_erp` | `territories`, `stores`, `customers`, `sales_orders`, `sales_order_lines` | `sku` = `BK-R93R-62` (canonical) |
| **PLM** | SQLite `plm.db` | `products` (attributes, safety stock, reorder point, make/finished flags, subcategory, model, sell dates), `subcategories`, `categories`, `models`, `bom` | `product_number` (canonical); `bom.assembly_number`, `component_number` |
| **MES** | DuckDB `mes.duckdb` | `work_orders`, `routings`, `work_centers`, `scrap_reasons` | `part_no` = `bk-r93r-62` (lower case) |
| **WMS** | MongoDB `mfg_wms` | `stock` (location, shelf, bin, quantity), `locations` | `item` = `AW:BK-R93R-62` |
| **Procurement** | DuckDB `procurement.duckdb` | `vendors`, `vendor_items`, `purchase_orders`, `po_lines`, `ship_methods` | `part_ref` = `BK_R93R_62` (`-` replaced by `_`) |
| **Costing** | Postgres `mfg_costing` | `standard_cost_history`, `list_price_history`, `item_prices` (current standard cost and list price) | `product_number` (canonical) |

```
              ┌── ERP sku ── sales_order_lines ── sales_orders ── customers, stores, territories
              ├── MES part_no (lower) ── work_orders ── routings ── work_centers
PLM products ─┼── WMS item ('AW:') ── stock ── locations
 (canonical)  ├── procurement part_ref ('_') ── vendor_items, po_lines ── vendors, purchase_orders
              └── costing product_number ── cost and price history, current prices
```

Product names, subcategories and categories live only in the PLM, so any question that names a
product, model, subcategory or category needs the PLM plus the system holding the facts.

**Left out, documented:** `Person` and the address, phone, e-mail and credit card tables (customer
names and addresses; no question needs them, and stores carry names); `TransactionHistory` (an
inventory movement log that duplicates order lines); `SpecialOffer`, `SalesPerson`, `Employee`
and the HR tables (ids are kept where orders carry them). Large XML/text columns (product model
catalog descriptions, store demographics) are dropped.

**Deliberately not done:** no conflicting values between systems, no fuzzy matching, no freshness
timing; as in the bank benchmark, the joins test key handling.

## 3. Setups

| Setup | What the agent queries | Guidance file |
|---|---|---|
| A | the six source databases | `db_description.txt`: tables and fields per system, no key hints |
| C | one MongoDB database, `mfg_state`, with four collections | fields and guarantees only (`descriptions/setup_c.txt`) |

Setup C's collections:

- `products` (504): the PLM product with `subcategory` → `category` and `model` as objects,
  current `standard_cost` and `list_price`, `standard_cost_history`, `list_price_history`, `bom`
  (the components of the product), `inventory` (one element per location, with the location's
  name) and `vendors` (one element per offer, with the vendor).
- `work_orders` (72,591): with `product` (number, name, subcategory, category), `scrap_reason`,
  and `routings` (with the work center).
- `sales_orders` (31,465): with `customer` (and its store), `territory`, and `lines` (each with
  its `product`).
- `purchase_orders` (4,012): with `vendor`, `ship_method`, and `lines` (each with its `product`).

The shape follows AdventureWorks' own entities; no field was designed around a question. The
product reference carried by orders and work orders (number, name, subcategory, category) is
what a consolidated order or production record normally embeds.

**Building setup C with Tapstate.** `dab/load_mfg_to_postgres.py` loads the six systems, values
and key formats unchanged, into Postgres `mfgsrc` (one schema per system; dates as ISO text, see
below). The workspace `dab/tapstate/` holds ten pipelines; `dab/run_tapstate.sh` applies and
starts them in dependency order:

| Pipeline | Kind | Output (MongoDB `views`) |
|---|---|---|
| `mfg_product_ref` | reference | one flat row per product: number, name, subcategory, category, and the product's key in the MES, WMS and procurement formats |
| `mfg_stock`, `mfg_offer`, `mfg_routing` | staging | stock rows with product number and location; vendor offers with product number and vendor; routing operations with work center |
| `mfg_sales_line`, `mfg_po_line` | staging | order lines with their product (matched on the ERP sku, or on the procurement part_ref through `mfg_product_ref`) |
| `mfg_products`, `mfg_work_orders`, `mfg_sales_orders`, `mfg_purchase_orders` | setup C | the four collections above |

Key mapping: `js` steps map the WMS item and the procurement part_ref back to the product number
(`mfg_stock`, `mfg_offer`); `mfg_product_ref` computes each system's format from the canonical
number, so a work order's lower-case `part_no` and a purchase line's `part_ref` match the product
directly. Category reaches the product through a join of subcategories with categories.

Three Tapstate 0.7.0 behaviours shaped this layout, all found while building it:

1. **A many-to-one (referenced) embed under an array element attaches to only one element of the
   whole level.** Embedding the location under each `inventory` element produced 14 inventory
   elements in total (one per location) instead of 1,069; the vendor under each offer produced 86
   (one per vendor) instead of 460; `as: flat` behaves the same. The run reports no error. At the
   root of a document the same embed works (295 products share 37 subcategories correctly). Every
   such lookup therefore happens on the root of a staging pipeline, whose flat output is read back
   as a source. Worth reporting upstream.
2. **A referenced level cannot carry embeds** (`nest.referenced-level-carries-embeds`), and a join
   can match its dimensions only against the driving table, so product → subcategory → category
   cannot be one embed chain. A join of subcategories with categories is embedded instead.
3. **A join cannot feed a `js` step or read from another source's view in the same nest path**:
   join → js → nest fails at run time ("event on j_stock reached a stateful node with no order"),
   and so did a join across a Postgres and a MongoDB source feeding a nest. Joins here therefore
   read one source and feed a nest directly.

Also, as on 0.6.0 for the bank, a SQL `DATE` is unreadable in a `js` step, so `mfgsrc` stores dates
as ISO text (the agents' databases keep their `DATE` types). An array element's key must not be the
field a nested lookup joins on (the level then partitions by it), and the default nest memory
budget (4,000 entries per level, the rest served from MongoDB) made the 121,317-line staging
pipeline run at about 40 rows a second; `entries_in_memory: 250000` on the large nests made it
several times faster.

**Acceptance check for setup C.** `dab/common.py` builds the four collections straight from the
raw AdventureWorks tables, joined on their integer ids (`reference_state`). Tapstate's views must
equal it, document for document and field for field (`dab/verify_tapstate.py`), before they are
installed as setup C. `dab/build_consolidated.py` is the Python port of the pipelines' logic: it
reads the six source systems (not the raw tables), maps the key formats, and is checked against the
same reference. A test confirms the check fails when one source value or key is changed. Level-0
BOM rows (no assembly) are the only source rows not in setup C: they mark a top-level product, not
a component of one, and no question uses them.

## 4. Questions

20 questions, written before any run and frozen in `dab/questions.json`; a test fails if the
questions or their answers change. Answers are computed from the raw AdventureWorks tables only
(`dab/questions.py`), never from the split sources or setup C. No answer is zero, and no "which is
largest" question has a tie (both asserted).

| Category | Count | Systems | Example (question number) |
|---|---:|---|---|
| Single-source controls | 4 | MES, ERP, WMS, procurement | How many work orders that started in 2024 scrapped at least one unit? (1) |
| Two sources | 6 | PLM + MES, ERP, WMS, procurement; MES + costing; procurement + WMS | How many units were scrapped by 2024 work orders for products in the 'Wheels' subcategory? (5) |
| Three or more sources | 6 | e.g. MES + PLM + WMS; PLM + WMS + procurement | Which purchased Components are below safety stock, with the shortest vendor lead time of each? (14) |
| Portfolio aggregates | 4 | PLM with MES, ERP, WMS + costing, ERP + costing | Gross margin on Bikes sold in 2023, at the standard cost in effect on each order date (20) |

Validators use DAB's format and are identical across setups. Each embeds `dab/answer_check.py`,
the bank benchmark's checker (version 2) unchanged: counts as standalone numbers, money within
$1 (lead-time mean within 0.01), names as text, product numbers as ids, labeled values for
multi-part answers. Every validator accepts its reference answer.

## 5. Run

- Agent: Claude Opus 5.5 (`claude-opus-5-5`) through `harness/run_claude_arms.py` (Claude Code,
  DAB's four tools only), on the subscription login. Costs are Claude Code's API-equivalent
  estimates.
- Volume: 20 questions × 2 setups × 5 runs = 200 runs, 3 workers. A model-free dry run
  (`dab/dry_run.py`) and a 2-run smoke test passed first.
- Report: pass@1 with a question-clustered bootstrap interval, median and mean tokens, turns, and
  API-equivalent cost per correct answer, overall and by category (`dab/analyze_runs.py`).

## 6. What was built

All under `dab/`; [`dab/README.md`](dab/README.md) has the file list and the project
[README](README.md#reproduce-it) the commands.

## 7. Disclosures

- The source split and key formats are our construction, following common manufacturing system
  boundaries and DAB's ill-formatted-key pattern; AdventureWorks itself is one database.
- The questions are ours. The data and its licence (MIT) are Microsoft's.
- Setup C was built by Tapstate, but its layout (staging pipelines, a reference view read back as a
  source) is shaped by the three 0.7.0 limits in §3; the documents it produces are the intended
  shape and are verified exactly.

## 8. Changes from the design brief

RESULTS_PLACEHOLDER_8

## 9. Results: Claude Opus 5.5

RESULTS_PLACEHOLDER_9
