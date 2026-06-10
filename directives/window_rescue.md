# 24h Window Rescue (`grq-rescue`)

When a customer messages a brand number and nobody answers, Meta's 24h
customer-service window closes and only paid templates can reach them. At the
23h mark, `grq-rescue` fires that brand's **Window Rescue** bot flow — a free
session message:

> "Apologies for the delayed response, how may we help you?"
> [Connect with Agent] [Not Interested]

A button tap is a customer inbound → the 24h window re-opens for free.

Spec: `docs/superpowers/specs/2026-06-10-window-rescue-design.md`
Code: `execution/rescue_server.py`, `execution/rescue_store.py`, tee in
`execution/rpgrq_webhook_server.py`. Service: `grq-rescue` (127.0.0.1:8085).

## Rules (locked with user, 2026-06-10)

- Fire at 23h00m–23h45m after the customer's last *real* message; never later
  (missed window = skip — trigger-bot "succeeds" even when Meta then drops the
  message).
- Only unanswered conversations (no outbound of any kind since their message).
- One rescue per conversation; only a new real customer message re-arms.
- Max 3 trigger attempts, then stop (OFD retry-forever lesson).
- Both buttons assign the conversation to an agent in WhatChimp.

## WhatChimp UI setup (once per bot × 5)

1. Bot Manager → <bot> → Bot Reply → new flow named **Window Rescue v1**
   (don't rename after go-live; version instead).
2. First node — text: `Apologies for the delayed response, how may we help you?`
3. Quick-reply button **Connect with Agent** →
   reply node: `You're connected — an agent will be with you shortly.`
   + side-effect: *Assign Conversation* to the brand's agent/team.
4. Quick-reply button **Not Interested** →
   reply node: `No problem — feel free to reach out anytime!`
   + side-effect: *Assign Conversation* to the brand's agent/team.
5. Copy the flow's `bot_flow_unique_id` from Bot Manager into the registry
   below and into `RESCUE_CONFIG` in `execution/rescue_server.py`.

## Flow ID registry

| Brand | whatsapp_bot_id | phone_number_id | bot_flow_unique_id | enabled |
|---|---|---|---|---|
| VIREX UAE | 381990 | 1073890042476443 | 1900212 | no |
| DIALO UAE | 382073 | 1002123586328400 | 1900210 | no |
| AMARA | 382036 | 1045332455333591 | 1900209 | no |
| ELARA | 352261 | 1031340813395459 | 1900207 | no |
| LUNE | _(unknown — config keyed by phone_number_id; `resolve_config` falls back)_ | 1138942462625909 | 1900211 | no |

Flow IDs provided by user 2026-06-10 after building all 5 flows in the UI.
`/user/myInfo` confirms the bot roster but does NOT return `whatsapp_bot_id` —
only `phone_number_id` — hence the LUNE fallback.

## Go-live checklist (per brand — pilot first, then the rest)

1. Build the flow (above), fill `bot_flow_unique_id`, keep `enabled: False`.
2. **Capture button payloads** (payload shapes are undocumented):
   from a test phone, message the brand number, manually trigger the flow
   (`trigger-bot` curl or a temporary low `RESCUE_FIRE_AFTER_HOURS`), tap each
   button, then read the teed payloads in `rescue.log` (`event in: {...}` lines,
   DEBUG level). Note the exact inbound text/postback of each button.
3. If tap text ≠ the button labels, add the captured strings to
   `RESCUE_EXTRA_BUTTON_TEXTS` (comma-separated, in `.env` on the VM).
   **This step is mandatory** — a misclassified tap re-arms the clock and can
   cause a second apology message.
4. Confirm the inbound text field name matches `TEXT_KEYS` in
   `rescue_server.py`; if WhatChimp uses a different key, add it.
5. Set `enabled: True` for the brand, commit, deploy, restart `grq-rescue`.
6. End-to-end: real message from test phone → no reply → confirm rescue
   arrives at 23h (or temporarily lower `RESCUE_FIRE_AFTER_HOURS`), tap
   *Connect with Agent*, confirm reply + agent assignment + no second rescue.

## Deploy

```
git push  (bot-spam repo / main)
ssh <vm>
cd ~/automation && git pull
sudo cp execution/grq-rescue.service.template /etc/systemd/system/grq-rescue.service  # first time; edit user paths
sudo systemctl daemon-reload && sudo systemctl enable grq-rescue                       # first time
sudo systemctl restart grq-rescue
sudo systemctl restart <rpgrq service>   # picks up the tee
```

Health: `curl http://127.0.0.1:8085/` → `rescue up`. Logs: `~/automation/rescue.log`.

## Env knobs

| Var | Default | Meaning |
|---|---|---|
| `RESCUE_PORT` | 8085 | listen port (127.0.0.1 only) |
| `RESCUE_DB_PATH` | `<repo>/rescue.db` | SQLite state |
| `RESCUE_POLL_SECONDS` | 60 | scheduler interval |
| `RESCUE_FIRE_AFTER_HOURS` | 23 | clock age to fire at |
| `RESCUE_FIRE_WINDOW_MINUTES` | 45 | firing window width |
| `RESCUE_EXTRA_BUTTON_TEXTS` | _(empty)_ | captured postback strings, comma-sep |
| `RESCUE_EVENTS_URL` | `http://127.0.0.1:8085/events` | tee target (set empty in rpgrq env to disable tee) |

## Known gaps (accepted in spec)

- Conversations already mid-window at first deploy are invisible until the
  customer's next message.
- Any outbound counts as "answered", including OFD/abandoned-cart templates.
- Events arriving while grq-rescue is down are lost (tee is fire-and-forget).
