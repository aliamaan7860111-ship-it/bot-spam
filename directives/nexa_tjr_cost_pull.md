# Nexa: TJR cost sync

**What:** hourly copy of each TJR parcel's driver commission and each TJR
driver expense into GRQ OS, so Nexa's profit per order includes TJR.

**Script:** `execution/nexa_tjr_cost_pull.py` (systemd `nexa-tjr-cost-pull.timer`, :20 every hour, VM).

**Reads (TJR, oltpxkwqoeghekoluavv):** view `parcel_costs` (commission by the
TJR ledger rule, `tjr-logistics/supabase/migrations/0008_parcel_costs.sql`),
table `driver_expenses`. Credentials: `/home/bilal/tjr-logistics/.env`.

**Writes (GRQ OS):** `nexa_replace_tjr_costs(parcels, expenses)` → tables
`courier_charges`, `courier_expenses` (migration 0185). Full replace each run.

**Edge cases:**
- TJR returns no parcels → the run aborts (EmptyRead), nothing is wiped.
- A parcel whose `order_ref` matches no CRM order is kept with no order (it
  still shares its driver's expenses) and listed as unmatched in the log.
- The commission rule also lives in tjr-dashboard `src/lib/pay.ts`. If TJR
  changes its rates, change the view in the same commit.

**Run by hand:** `venv/bin/python execution/nexa_tjr_cost_pull.py` (VM), or on
the laptop with `--tjr-env tjr-logistics/.env` and NEXA_* set from `grq-os/.env.local`.
**Log:** `/home/bilal/automation/.tmp/nexa_tjr_cost_pull.log`.
