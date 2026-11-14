# Tapstate 0.8.0 beside 0.7.0: hands-on findings (2026-11-14)

Stack: `stack/docker-compose-0.8.yml` (project mfg-bench08). It has its own store MongoDB and reads the
same six source databases as 0.7 (attached by `env08.sh`). It runs the same ten pipelines (`dab/tapstate/`) unchanged.

## Results

| Check | 0.7.0 | 0.8.0 |
|---|---|---|
| Ten pipelines rebuild, four state views complete | yes | yes (504 / 72,591 / 31,465 / 4,012) |
| `verify_tapstate.py`, field for field vs raw tables | clean | clean after repair (see 1), and clean again after a later clean restart |
| Postgres live change, price 2294.99 → 2199.99 → back | 0.48 s / 0.48 s | 0.47 s / 0.34 s |
| MongoDB update with pre-images, stock +10 → back (two pipelines) | works | works, 0.53 s / 0.26 s |
| MySQL CDC update/insert of `DATE`/`DATETIME` | lands as epoch numbers | BSON dates (fixed) |
| Postgres update of a date-keyed array element (`arrayKey: [start_date]`) | adds a duplicate element | replaces the element (fixed) |
| Postgres CDC update of a row with `DATE` columns | BSON dates | BSON dates |

The throwaway tests used databases `dt08` on the shared MySQL and Postgres, with pipelines `dt08_m` and
`dt08_p`. The 0.7 stack does not read them.

## 1. Stale references after the initial build (repaired; the cause is probably the fence plus restart)

The first full rebuild ran under memory pressure. The engine was fenced at 08:01
(`io.workload-claim-fenced`, Hazelcast SHUTDOWN, TransientTransactionError) and the server was restarted at 08:24.
After that restart, verify found 1,959 differences, all empty fields reached through a builtin `LEFT JOIN` and then a nest embed:

- 4 products had their subcategory but `category: null` (`mfg_product_ref`). This spread to 1,527 work orders,
  360 sales-order lines and 50 PO lines.
- 18 sales orders had `customer.store.name: null` (`mfg_sales_orders`, customers ⋈ stores, embedded as `customer`).

The same subcategory was correct for some products and stale for others. One customer had 7 orders with
the store name and 1 without.

Repair:

- **The 4 products:** a no-op source update (`UPDATE subcategories SET name = name`) repaired them,
  downstream included, within seconds.
- **Stores:** the same touch on `stores` fixed only some orders, and a second touch fixed none.
- **The 18 orders:** a no-op update on each order row (`UPDATE sales_orders SET customer_id = customer_id`)
  repaired all of them.

So the reverse links from a referenced row to some of its parents were missing, and later changes could not repair them.
A clean restart at 09:07 created no new gaps (verify was clean afterwards). This has not yet been tested with a rebuild
that runs without memory pressure or a fence.

## 2. Lifecycle commands fenced after the restart (open; reproducible)

After the 08:24 restart, data kept flowing, but these were refused:

- `stop <p> -y`, `pause <p>` and `restart <p>` on `dt08_m`
- some control-plane reconcile passes ("Reconcile pass for pipeline mfg_purchase_orders failed 1 time(s) in a row", which retried and recovered)

The error each time:

    error: io.workload-claim-fenced
      The store rejected a write from an expired or superseded workload owner.
      Let the current cluster owner continue the workload; do not retry from the fenced generation.

A clean `docker restart` of the server at 09:07 came back without re-authorization. Afterwards
`stop dt08_p -y` worked and `delete dt08_p` worked. `dt08_m`, however, is stuck:

- `status`: `failed` (`engine.execution-not-authorized`)
- `start`: `lifecycle.illegal-transition` ("Cannot start a pipeline in state RUNNING")
- `stop -y`, `restart`: still `io.workload-claim-fenced`
- `delete`: "pipeline state: actual=FAILED, desired=RUNNING; run `stop dt08_m`"

Repro, as far as we have it:

1. A single-node 0.8.0 (`--role=all`) whose engine was fenced under memory pressure, then restarted.
2. Apply a new pipeline and start it.
3. Change its js transform and apply. The apply warns `control.schema-derivation-incomplete` /
   `actuation.schema-sync-while-running`.
4. `stop -y`, then `pause`: both are refused with `io.workload-claim-fenced`.

Left in place for the Tapstate team:

- pipeline `dt08_m` and source `dt08_mysql` on the 0.8 stack;
- MySQL database `dt08` (3 rows);
- `views.dt08_m`.

The Postgres `dt08` database, its replication slot, `dt08_p` and `dt08_pg` were removed.

## 3. Other notes

- An integral Postgres `numeric(10,2)` (10.00) arrives as a Long (`10`), and 12.50 as a double. The values are
  the same, but the types differ.
- `PostgresCdcRunner [processFlushOffset error: Cannot invoke "Object.toString()" ... Map.get(Object) is null]`
  is logged as a WARN after a Postgres change. It has no visible effect.
- `status` reports `monitor.no-observation` for about 1–2 minutes after a server start.

## Not yet done

- Whether a `js` step can read a `DATE` (refused/opaque on 0.7).
- The pipeline diff, editor localization and language on reload in the 0.8 UI. Browser sign-in needs HTTPS, or the
  insecure-loopback opt-in plus a loopback peer.
- Read-only MCP with Claude Code. This needs a read-scoped `TAPSTATE_TOKEN`, which waits on the user's decision.
