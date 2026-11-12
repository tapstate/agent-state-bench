# Manufacturing benchmark: preliminary report

2026-11-12. **Status: preliminary.** Everything except the main run is built. The 200-run batch is
on hold, so the only agent numbers so far come from a 2-run smoke test: one run per setup, on one
question. They show the direction of the effect, not its size. Design details are in
[SOURCE_SPLIT.md](SOURCE_SPLIT.md).

## Question

Does a data agent answer manufacturing questions more accurately, and with fewer tokens, when it
queries Tapstate's consolidated state (setup C) rather than the company's live source systems
(setup A)? This is the bank benchmark's question, asked of a second domain. There is no warehouse
setup, and there is no hints setup B. The bank benchmark found that hints changed neither accuracy
nor tokens measurably.

## Data and setups

The data is Microsoft's AdventureWorks OLTP sample (MIT licence): a fictitious bicycle maker with
504 products, 72,591 work orders, 31,465 sales orders with 121,317 lines, 4,012 purchase orders,
and stock in 14 locations.

**Setup A, live sources.** Six systems on three database servers, each system writing the product
key in its own format:

| System | Engine | Product key |
|---|---|---|
| ERP sales | Postgres | `BK-R93R-62` (canonical) |
| PLM (product master, BOM) | Postgres | canonical; the only system that holds names, subcategories and categories |
| MES (work orders, routings) | MySQL 8 | `bk-r93r-62` |
| WMS (stock) | MongoDB (its own server) | `AW:BK-R93R-62` |
| Procurement (vendors, POs) | MySQL 8 | `BK_R93R_62` |
| Costing | Postgres | canonical |

MySQL compares text case-sensitively (`utf8mb4_0900_bin`), as Postgres does. An earlier layout put
PLM in SQLite and MES and procurement in DuckDB; the smoke-test numbers below cover both layouts.

**Setup C, Tapstate.** One MongoDB database with four collections built by Tapstate 0.7.0 from the
six source databases themselves (Postgres logical replication, MySQL binlog, MongoDB change streams):
`products` (BOM, costs, prices, inventory and vendors embedded), `work_orders`, `sales_orders`
and `purchase_orders`. Each order and work order carries its product's number, name, subcategory
and category.

## Questions

There are 20 questions, frozen before any run (`dab/questions.json`), with answers computed only
from the raw AdventureWorks tables:

| Category | Count | Example |
|---|---:|---|
| Single-source controls | 4 | Work orders started in 2024 that scrapped at least one unit |
| Two sources | 6 | Units scrapped in 2024 for 'Wheels' products (MES + PLM) |
| Three or more sources | 6 | Purchased components below safety stock, with the shortest vendor lead time of each (PLM + WMS + procurement) |
| Portfolio aggregates | 4 | 2023 gross margin on Bikes at the standard cost in effect on each order date (ERP + PLM + costing) |

## Results so far

Smoke test, question 14 (purchased Components below safety stock, with the shortest vendor lead
time of each; PLM + WMS + procurement), Claude Opus 5.5:

| Layout | Setup | Runs | Correct | Tokens |
|---|---|---:|---:|---:|
| SQLite / DuckDB / Postgres / MongoDB | A: six live sources | 1 | 1 | 94k |
| | C: Tapstate state | 1 | 1 | 32k |
| Postgres / MySQL / MongoDB | A: six live sources | 1 | 1 | 126k |
| | C: Tapstate state | 1 | 1 | 39k |

All four runs answered correctly. The Tapstate setup used about a third of the tokens (2.9× on the
first layout, 3.2× on the second). That is close to the bank benchmark's 3.5× at the median over
720 runs, but one run per setup has no confidence interval. Treat it as a sign the setup works, not
as a result.

## What we learned about Tapstate 0.7.0

The consolidated store needed 10 pipelines instead of the 4 planned, because of three limits found
while building it:

1. **A silent data loss bug.** A many-to-one lookup nested under an array element, such as the
   location inside each inventory entry, attaches to only one element of the whole level.
   Inventory came out as 14 elements instead of 1,069, and vendor offers as 86 instead of 460, with
   no error. Small staging pipelines work around it by resolving these lookups at the document
   root. This should be reported upstream.
2. **A referenced level cannot carry embeds**, so product → subcategory → category can't be one
   chain. A join of subcategories and categories is used instead.
3. **A join cannot feed a `js` step**, and a join across a Postgres and a MongoDB source failed at
   run time. Joins therefore read one source and feed a nest directly.

Two other findings:

- A SQL `DATE` can't be turned into text by any step: in a `js` step it is an opaque object, and a
  `map` step refuses a CEL expression on it. Passed through untouched it lands in MongoDB as a BSON
  date, so setup C stores BSON dates and its description says how to write one in a query. After the
  initial load, a MySQL change event carries a date as an epoch number (milliseconds for `DATE`,
  seconds for `DATETIME`), and a Postgres update to an array element keyed by a date adds a second
  element instead of replacing it. Neither happens here, since the sources are loaded once.
- The MySQL connector is not bundled with the 0.7.0 server; `tapstate register mysql` fetches it.
- The default nest memory budget made the 121k-line staging pipeline run at about 40 rows a
  second. Raising `entries_in_memory` made it several times faster.

## Still to do

- Confirm that Tapstate's four collections equal the reference build, document for document. This
  check was running when the batch was put on hold. Until it passes, the "verified" status line in
  SOURCE_SPLIT.md is a target, not a result.
- The main batch: 20 questions × 2 setups × 5 runs = 200 runs. Based on the bank batch, expect
  about 40 minutes and roughly $25 at API-equivalent prices.
- A PR with the results and the Tapstate findings.

## On splitting into more databases

The six-system split is already finer than the earlier ERP-and-inventory split. In the bank
benchmark, the token gap was absent on single-source questions and largest on questions that cross
three or more systems. A finer split is therefore likely to widen the gap, for example separating
the BOM from the PLM, or the customers and territories from the ERP. It would also make the
benchmark more synthetic. That is best judged after the 200-run batch, from the by-category numbers.
