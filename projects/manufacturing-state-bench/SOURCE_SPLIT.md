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

Six systems, laid out the way a manufacturer commonly separates them, on three engines (one server
per engine, `stack/docker-compose.yml`):

| System | Engine (setup A) | Tables / collections | Product key, as that system writes it |
|---|---|---|---|
| **ERP sales** | Postgres `mfg_erp` | `territories`, `stores`, `customers`, `sales_orders`, `sales_order_lines` | `sku` = `BK-R93R-62` (canonical) |
| **PLM** | Postgres `mfg_plm` | `products` (attributes, safety stock, reorder point, make/finished flags, subcategory, model, sell dates), `subcategories`, `categories`, `models`, `bom` | `product_number` (canonical); `bom.assembly_number`, `component_number` |
| **MES** | MySQL 8 `mfg_mes` | `work_orders`, `routings`, `work_centers`, `scrap_reasons` | `part_no` = `bk-r93r-62` (lower case) |
| **WMS** | MongoDB `mfg_wms` (its own server, not Tapstate's store) | `stock` (location, shelf, bin, quantity), `locations` | `item` = `AW:BK-R93R-62` |
| **Procurement** | MySQL 8 `mfg_procurement` | `vendors`, `vendor_items`, `purchase_orders`, `po_lines`, `ship_methods` | `part_ref` = `BK_R93R_62` (`-` replaced by `_`) |
| **Costing** | Postgres `mfg_costing` | `standard_cost_history`, `list_price_history`, `item_prices` (current standard cost and list price) | `product_number` (canonical) |

```
              ┌── ERP sku ── sales_order_lines ── sales_orders ── customers, stores, territories
              ├── MES part_no (lower) ── work_orders ── routings ── work_centers
PLM products ─┼── WMS item ('AW:') ── stock ── locations
 (canonical)  ├── procurement part_ref ('_') ── vendor_items, po_lines ── vendors, purchase_orders
              └── costing product_number ── cost and price history, current prices
```

Dates are SQL `DATE`s in every SQL system. MySQL uses the binary collation `utf8mb4_0900_bin`, so
text compares case- and accent-sensitively as in Postgres: the MES's lower-case `part_no` does not
match a canonical product number by accident.

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

**Building setup C with Tapstate.** Tapstate reads the six databases the agent in setup A queries,
with no copy in between: Postgres `mfg_erp`, `mfg_plm` and `mfg_costing` through logical
replication, MySQL `mfg_mes` and `mfg_procurement` through the ROW binlog (the MySQL connector is
registered from Tapstate's published connectors, `tapstate register mysql`), and MongoDB `mfg_wms`
through change streams (`dab/tapstate/source/`). The workspace `dab/tapstate/` holds ten pipelines; `dab/run_tapstate.sh` applies and
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

**Dates.** On 0.7.0 a SQL `DATE` cannot be turned into text by any step: in a `js` step a snapshot
row's date is an opaque `java.time.LocalDateTime` (`String()` gives `[object Object]`, no method is
callable), and a `map` step refuses a CEL expression on a `DATE` column
(`dsl.row-expression-type-unsupported`). Passed through untouched, a date reaches MongoDB as a BSON
date (midnight UTC), so setup C stores dates as BSON dates and its description tells the agent to
write them in Extended JSON (`{"$date": "2025-04-01T00:00:00Z"}`, which DAB's patched Mongo tool
parses). The pipelines only pass dates through. Two date defects appear only when a source row
changes after the initial load, which the benchmark never does: a MySQL change event carries a
`DATE` as epoch milliseconds and a `DATETIME` as epoch seconds, which land in MongoDB as numbers;
and a Postgres update to a history row keyed by a date (`start_date` in `standard_cost_history`,
`list_price_history`) adds a second array element instead of replacing the one the snapshot
loaded. An earlier layout loaded a Postgres copy of the six systems with dates as text (`mfgsrc`);
it is retired.

**Updates from MongoDB need pre-images.** A nest that embeds a referenced row needs an update's earlier
row to know where it pointed before; without it the pipeline stops at the first update
(`nest.reference-tracking-requires-before-image`), which is the right refusal. Postgres sends the
earlier row (`REPLICA IDENTITY FULL`) and so does MySQL's ROW binlog with full row images; a MongoDB
change stream sends it only when the collection records pre-images (`changeStreamPreAndPostImages`)
and the Tapstate source asks for them (`preImage: true` in its config). The initial load (inserts
only) works without either, which is why the first build and the benchmark never hit it; the demo's
live stock change did. `split_sources.py` turns pre-images on for the WMS collections,
`run_tapstate.sh` for the staging and reference views that `src_mfg_stage` and `src_mfg_ref` read, and
the three MongoDB sources set `preImage: true`. With that, a stock change in the WMS reaches
`views.mfg_product` through two pipelines in about 0.1 s.

An array element's key must not be the
field a nested lookup joins on (the level then partitions by it), and the default nest memory
budget (4,000 entries per level, the rest served from MongoDB) made the 121,317-line staging
pipeline run at about 40 rows a second; `entries_in_memory: 250000` on the large nests made it
several times faster.

**Acceptance check for setup C.** `dab/common.py` builds the four collections straight from the
raw AdventureWorks tables, joined on their integer ids (`reference_state`). Tapstate's views must
equal it, document for document and field for field (`dab/verify_tapstate.py`; dates compared as
`YYYY-MM-DD`), before they are
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
- The engines (Postgres, MySQL, MongoDB) are our choice; AdventureWorks itself is SQL Server.
- Setup C was built by Tapstate, but its layout (staging pipelines, a reference view read back as a
  source) is shaped by the three 0.7.0 limits in §3; the documents it produces are the intended
  shape and are verified exactly.

## 8. Changes from the design brief

- **Tapstate layout: ten pipelines, not four.** The brief planned one nest pipeline per collection,
  with location, vendor and product as referenced embeds inside arrays, and a `js` step per system
  mapping its key. Tapstate 0.7.0's three limits in §3 made that impossible: lookups inside arrays
  are resolved on the roots of five staging pipelines, and `mfg_product_ref` (read back as a source)
  supplies the product reference. The MES `part_no` and procurement `part_ref` on purchase lines
  are matched against the formats `mfg_product_ref` carries, rather than mapped in a `js` step; the
  WMS item and procurement offers are mapped in `js` steps as planned. The documents are the
  planned shape.
- **Level-0 BOM rows are not in setup C** (§3).
- **Question 14** (components below safety stock, with vendor lead times) is in the three-or-more
  category, not the portfolio one; the portfolio questions are scrap by category, Bikes revenue by
  territory, stock value by category, and margin at the order-date cost. Question 15 first asked
  about Q1 2025, where two bikes tie for most units sold; it was moved to Q4 2024 before the
  questions were frozen.
- **The validator was corrected after the first scoring** (§9), as in the bank benchmark.
- **Setup C's integers are stored as 64-bit and its fields in alphabetical order** (Tapstate's
  output); values equal the Python build's exactly. The agent's query tool returns every value as
  a string in both setups.

## 9. Results: Claude Opus 5.5

Measured on the first layout (PLM in SQLite, MES and procurement in DuckDB, Tapstate reading a
Postgres copy of the six systems), before the move to three engines; see the project README.

200 of 200 runs completed on 2026-11-12: 20 questions × 2 setups × 5 runs, Claude Code 2.1.322,
3 workers, 19 minutes, no usage limits hit. Full tables, by category and per question:
[`dab/RESULTS_opus.md`](dab/RESULTS_opus.md); raw scores:
[`dab/results/opus_scores.csv`](dab/results/opus_scores.csv).

| Setup | pass@1 (95% CI) | Median tokens per run | Mean tokens per run | Median turns | API-equivalent $ per correct answer |
|---|---|---:|---:|---:|---:|
| A: six source systems | 0.980 (0.95–1.00) | 63,818 | 74,800 | 6 | 0.150 |
| C: consolidated state | 0.980 (0.95–1.00) | 28,432 | 34,515 | 5 | 0.067 |

| Category | A median tokens | C median tokens | A / C | A pass@1 | C pass@1 |
|---|---:|---:|---:|---:|---:|
| Single-source controls (4) | 28,288 | 24,776 | 1.1× | 0.95 | 1.00 |
| Two sources (6) | 57,487 | 25,158 | 2.3× | 1.00 | 1.00 |
| Three or more sources (6) | 69,564 | 30,368 | 2.3× | 0.97 | 0.97 |
| Portfolio aggregates (4) | 133,462 | 42,047 | 3.2× | 1.00 | 0.95 |

- **Tokens:** setup C used 2.2× fewer tokens than A (median and mean), about one turn fewer, and
  45% of A's API-equivalent cost per correct answer. As designed, the single-source controls show
  no gap. The gap grows with the rows the agent must move between databases: largest on portfolio
  aggregates, where A pulls whole tables from three systems into Python (question 19, stock value
  by category: 162k tokens in A, 49k in C).
- **Accuracy:** no difference. All four failures (two per setup) are protocol misses: a correct
  final text without a `return_answer` call. Opus 5.5 is at the ceiling in both setups, so this run
  supports a token and cost claim, not an accuracy claim.
- **Smaller gap than the bank's 3.5×,** most likely for two reasons. Several systems hold
  their own joins (the PLM has products, subcategories and categories in one database; the ERP
  has orders and lines in one Postgres database), so those run in SQL and only the joins between
  systems go through the agent; and the key formats are easy to discover (one sample row shows each).
  Setup C, in turn, still needs a cross-collection step when a question combines order lines with
  product costs (questions 12 and 20), because the product reference in a line carries no cost.
- **Validator correction, disclosed.** The bank's checker (version 2) failed 7 correct answers to
  question 17 that put the number before its label ("58 Bikes units and 886 Components units").
  Version 3 also accepts the last number before the label on the same line, identically for both
  setups. Rescoring changed only those 7 verdicts, all from fail to pass. Before: A 0.930, C 0.960
  ([`dab/results/opus_scores_validator_v2.csv`](dab/results/opus_scores_validator_v2.csv)).

**Would splitting AdventureWorks into more databases widen the gap?** Probably, if the split adds
cross-database hops to the questions rather than just more databases. Setup A's tokens track the
hops and the rows moved per question (1.1× for one system, 2.5× for two, 2.6× for three, 3.2× for
portfolio questions), while setup C's documents, and so its cost, stay the same whatever the
sources look like. Two realistic splits would add hops to the most common paths:

1. **By region or site (horizontal):** an ERP instance per sales region (North America, Europe,
   Pacific, as AdventureWorks' territory groups suggest) and an MES per plant. Every portfolio
   question then has to union three databases first: the category with the largest gap already.
2. **Product master apart from the catalog (vertical):** a separate product-catalog system holding
   subcategories, categories and models, with the PLM keeping only engineering data, and costing
   split into standard cost and pricing. Nearly every multi-source question passes through product
   → category, so this adds a hop to most of them.

Splitting tables that are always queried together (orders from their lines, say) would only add
noise. Either variant can be measured as a new setup A against the same setup C, since setup C does
not change.
