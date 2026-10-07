# Nexa ad pull (SOP)

**What:** copies ad spend from Triple Whale into Nexa's `ad_spend_daily`
table (GRQ OS Supabase) for every store with an active row in `ad_sources`.

**Script:** `execution/nexa_ad_pull.py --mode hourly|nightly|initial`
**Runs on:** GCP VM, `/home/bilal/automation`, systemd timers
`nexa-ad-pull.timer` (hourly at :05) and `nexa-ad-pull-nightly.timer`
(03:00 Dubai). Log: `.tmp/nexa_ad_pull.log`.
**Env (VM .env):** `TRIPLEWHALE_API_KEY`, `NEXA_SUPABASE_URL`,
`NEXA_SUPABASE_SERVICE_KEY`.

**Windows:** hourly = today + 3 days back; nightly = 30 days back; initial =
from `data_from` (2026-10-01). Never before `data_from`.

**How it writes:** per store, `nexa_replace_ad_spend(brand, from, to, rows)`
deletes that store's window and inserts the fresh rows. Re-running is always
safe.

**Failures:** 3 attempts per store (5 s, 10 s backoff) for network/5xx;
none for access lost (401/403), non-AED spend or a malformed reply. Each
outcome is a row in `ad_pull_runs`. Any failure raises one central error
alert (`NexaAdPullFailed`). Nexa marks a store stale after 3 hours without
a good pull and never shows its spend as zero.

**Adding a store:** Nexa → Settings → Stores → link the store's
`*.myshopify.com` domain. The next hourly run picks it up. The Triple Whale
key must have access to that shop (a 403 alert means it does not).

**Learned:**
- Triple Whale API keys are scoped per shop; widening access is done by the
  operator in Triple Whale. (2026-10-05)
- `ads_table` rows are hourly (`event_hour`); `event_date` is the shop's day.
- Spend is reported in AED for these shops; anything else is refused.
- Triple Whale revenue/COGS/profit are not used anywhere: revenue counts every
  COD order placed and COGS is empty.
