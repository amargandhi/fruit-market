# Parallel Build Guide

How to ship the Fruit Market MVP in ~4–6 hours of clock time by
running one Claude session and one Codex (5.5) session against the
same repo at the same time. Each session owns disjoint directories,
both branch off a contracts skeleton, and they merge into `main`
with a one-line import swap at the end.

Two layouts are documented:

- **Recommended**: 2 sessions. One Codex 5.5 session fans out into
  4 internal sub-workers; one Claude session does the vision +
  state + services track. Lowest babysitting overhead.
- **Maximum isolation**: 5 sessions. Four separate Codex 5.5
  sessions, one Claude session. Use when you want each sub-tree's
  output reviewable independently before merge.

---

## Why Codex 5.5 for the parallel side

Codex 5.5 is what you want for Track B because:

- Large context window holds all the contract files at once
  (`events.py`, `protocols.py`, `_stubs.py`, `tool_specs.py`,
  `schemas.py`) without forgetting them mid-task.
- Tool-using subagent dispatch means a single prompt can fan out
  to 4 parallel file-writers internally.
- Strong at boilerplate-heavy SDK integration (Stripe SDK, Gemini
  SDK, httpx clients) — exactly Track B's shape.

Settings to flip on before starting:

- Max thinking budget
- Multi-file edits enabled
- Subagent dispatch / parallel tool calls allowed
- Working directory: `/path/to/fruit-market`
- Branch: create `track/codex-b` off `main`

---

## Pre-requisites

1. **Phase 1 contracts skeleton is on `main`.** That's the commit
   titled "Phase 1: contracts skeleton — both tracks can fork from
   here." Without it, both tracks have nothing to import.
2. **`.env` is populated** and `make check` green. The Phase 1
   commit includes `scripts/check_sponsors.py` — running it should
   return 5 × HTTP 200.
3. **Both sessions can push to `origin`.** SSH key or HTTPS token,
   doesn't matter — just confirm `git push --dry-run` works in
   both.
4. **You have ~5 hours uninterrupted.** Most of it is watching the
   sessions work; the merge at the end needs your attention.

---

## Recipe (2-session layout)

```
T+0:00   Phase 1 contracts skeleton lands on main (Claude does this)
T+0:01   Open Codex 5.5 session, paste Prompt B (below)
T+0:02   Open Claude session, paste Prompt A (below)
         [4-6 hours of parallel work, sessions don't talk]
T+5:00   Both sessions open PRs against main
T+5:10   Track A merged first (squash)
T+5:15   Track B rebased onto main, _stubs import swapped to real
T+5:30   make check && pytest all green
T+5:45   Live integration test: real call → real Stripe → kiosk
T+6:00   Tag v0.1-mvp
```

---

## Prompt A — Claude session (Track A: vision + state + services)

Paste this into a fresh Claude Code session in the repo root. If
you're continuing the session that wrote the contracts skeleton,
it already has the context — just say "start Track A now" and it
will pick up.

```text
You're contributing to https://github.com/amargandhi/fruit-market on
a new branch `track/claude-vision-state-services`. The contracts are
on `main` from the commit titled "Phase 1: contracts skeleton —
both tracks can fork from here."

READ FIRST (in parallel):
- docs/architecture.md (sections 1, 3, 6)
- docs/parallel-build-guide.md (this file — for the merge sequence)
- fruit_market/state/events.py
- fruit_market/services/protocols.py
- fruit_market/services/_stubs.py (your reference for the Protocol
  shapes — you're writing real implementations of these)
- pyproject.toml
- .env.example

YOUR BRIEF
Implement the canonical state layer and the vision loop. Four
sub-trees, in this order so each one unblocks the next:

  1. fruit_market/state/
     - store.py: SQLite WAL append-only log. append(event),
       replay(since_offset). One row per event, JSON payload.
     - projections.py: CatalogProjection, InventoryProjection,
       OrdersProjection. Each is a pure function (events) -> dict.

  2. fruit_market/services/
     - catalog.py, inventory.py, orders.py, pricing.py,
       reservations.py, teach.py — real implementations of the
       Protocols in protocols.py, backed by state/store + projections.
     - Pricing: integer cents only. Reject floats at the boundary.
     - Reservations: locked inside a SQLite transaction so two
       concurrent reservations of the last banana can't both win.
     - Inventory.reconcile_physical_count(item_id, count, source,
       confidence) appends a CountSet event and emits StockLow when
       count <= reorder_threshold.
     - Teach.propose(transcript) -> TeachProposal: regex+rule parse
       of free text like "These are apples, $1.50, 6 of them".
     - Teach.confirm(proposal_id) appends ItemTaught and sets that
       item as the active item.

  3. fruit_market/vision/
     - camera.py: AVFoundation snapshot via opencv-python (fallback:
       small Swift subprocess if cv2 is unreliable). Returns JPEG
       bytes.
     - model.py: MLX-VLM wrapper for paligemma2-3b-mix-224.
       count(image_bytes, noun) -> int using prompt template
       "count {noun}\n", max_new_tokens=8.
     - watcher.py: asyncio task. Every 3s while an active item is
       set: snapshot → model.count(image, active_item.name) →
       inventory.reconcile_physical_count(...). Motion gate skips a
       tick if the image hasn't changed (cv2 frame diff).

RULES
- NEVER import from fruit_market/api/, brain/, integrations/, or
  ui/. Those are the parallel track's territory.
- All Money is integer cents.
- All side-effecting writes go through state/store.append(event).
  Services never mutate projections directly.
- Vision watcher must be tolerant of camera/model failures — log
  and continue, don't crash the asyncio loop.

VERIFICATION (must pass before opening the PR)
- pytest tests/unit/test_state_*.py tests/unit/test_services_*.py
  tests/unit/test_vision_*.py tests/unit/test_teach_*.py — all
  green. Mock the camera and model at the seam.
- One e2e: tests/e2e/test_teach_to_count.py — uses a fixture image
  checked into tests/fixtures/two_bananas.jpg, runs PaliGemma for
  real (gated behind FRUITMARKET_RUN_REAL_MODEL=1 env so CI skips),
  asserts count == 2.
- mypy strict on fruit_market/{state,services,vision}/.

DELIVERABLE
One PR titled "Track A: vision + state + services" with the four
sub-trees. PR body: one paragraph per sub-tree, plus a 1-line note
about any contract drift you discovered so Codex's rebase doesn't
surprise.
```

---

## Prompt B — Codex 5.5 session (Track B: API + brain + sponsors + UI)

Paste into a fresh Codex 5.5 session. This is the "fan-out" prompt
that asks Codex to spawn 4 internal sub-workers.

```text
You're contributing to https://github.com/amargandhi/fruit-market on
a new branch `track/codex-api-brain-sponsors-ui`. The contracts you
must conform to are on `main` from the commit titled "Phase 1:
contracts skeleton — both tracks can fork from here."

READ FIRST (in parallel):
- docs/architecture.md (whole file — note Sponge stretch in §5)
- docs/parallel-build-guide.md (this file — for the merge sequence)
- fruit_market/state/events.py
- fruit_market/services/protocols.py
- fruit_market/services/_stubs.py (use this as your backend until
  the parallel track lands its real implementations)
- fruit_market/brain/tool_specs.py
- fruit_market/brain/restock_agent_specs.py (Sponge stretch)
- fruit_market/api/schemas.py
- fruit_market/integrations/_sponge_mcp_protocol.py
- fruit_market/integrations/_supplier_mcp_protocol.py
- scripts/check_sponsors.py
- pyproject.toml
- .env.example

YOUR BRIEF
Spawn 4 sub-workers IN PARALLEL. Each owns one sub-tree exclusively:

  Worker 1 — fruit_market/api/ + fruit_market/brain/
    - api/app.py: FastAPI bootstrap; lifespan wires services from
      _stubs (real ones come in via rebase later).
    - api/phone_webhook.py: verify AgentPhone signature, route to
      brain. 401 on bad signature before any business logic.
    - api/stripe_webhook.py: verify Stripe signature, route to
      OrdersService.mark_paid.
    - api/routes.py: /api/state (snapshot), /api/state/stream (SSE),
      /api/teach (POST), /api/orders/{id}/pack (POST).
    - brain/gemini.py: google-genai SDK tool-calling loop.
    - brain/tools.py: one Python function per entry in tool_specs.py,
      each calling a service Protocol.
    - brain/prompts.py: system prompt; venue answers read from .env.
    Imports allowed from: services/protocols, services/_stubs,
      brain/tool_specs, api/schemas, integrations/.

  Worker 2 — fruit_market/integrations/agentphone.py
    - Bearer-token httpx client with a real browser User-Agent
      (Cloudflare blocks urllib's default; we got HTTP 403 during
      the sponsor probe with default UA).
    - Methods:
        send_sms(to: str, body: str) -> str  # returns message_id
        send_imessage(to, body) -> str       # uses AGENTPHONE_IMESSAGE_NUMBER_ID
        verify_webhook(headers, body) -> bool  # AGENTPHONE_WEBHOOK_SECRET
    - One contract test: tests/contract/test_agentphone_webhook.py
      against a recorded envelope in tests/fixtures/.

  Worker 3 — fruit_market/integrations/stripe_checkout.py
             + fruit_market/integrations/agentmail.py
    - stripe_checkout.create(item_name, qty, unit_amount_cents,
      success_url, cancel_url) -> CheckoutSession via the `stripe`
      Python SDK in test mode.
    - stripe_checkout.verify_webhook(headers, body) -> Event using
      STRIPE_WEBHOOK_SECRET.
    - agentmail.send_receipt(to_email, order) -> message_id via
      POST {AGENTMAIL_API_BASE}/inboxes/{AGENTMAIL_ADDRESS}/messages
      with Bearer auth.
    - Contract tests for both webhooks in tests/contract/.

  Worker 4 — fruit_market/ui/
    - index.html, app.js, styles.css (vanilla, no framework).
    - Connects to /api/state/stream over Server-Sent Events.
    - Three panels:
      • Catalog: list taught items, big number for active item's
        physical_count, button to switch active item.
      • Orders: three lanes (reserved / paid / packed). Clicking a
        paid order POSTs /api/orders/{id}/pack.
      • Teach: free-text input ("These are apples, $1.50, 6 of
        them"), POST /api/teach, render proposal + confirm button.
    - SSE event types: state.catalog, state.inventory, state.orders.

RULES
- NEVER import from fruit_market/state/, fruit_market/services/
  (use services/_stubs), or fruit_market/vision/. Those are the
  parallel track's territory.
- Every external HTTP call goes through httpx with a real
  User-Agent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)
  AppleWebKit/537.36 Chrome/126 Safari/537.36".
- All Pydantic models live in api/schemas.py or brain/tool_specs.py.
  Don't define ad-hoc dataclasses in router code.
- Money is integer cents everywhere. Reject floats at the schema
  layer.
- Webhook signature verification is mandatory on both /webhooks/*
  routes — return 401 on bad signature before any business logic.
- Write contract tests in tests/contract/ for every webhook and
  every router with side effects.

VERIFICATION (must pass before opening the PR)
- `make check` (mypy strict + ruff) green
- `pytest tests/contract/ tests/unit/test_brain_*.py
   tests/unit/test_api_*.py` green
- `uvicorn fruit_market.api.app:app --port 8000` boots without
  error on a machine with a populated .env
- One e2e smoke in tests/e2e/test_kiosk_renders.py: boot the app,
  hit /api/state, assert catalog and orders panels load.

DELIVERABLE
One PR titled "Track B: api + brain + integrations + ui" with all
four sub-trees. Include a 10-line PR body summarizing what's in
each sub-tree and which tests verify it.
```

---

## 5-session layout (maximum isolation)

Use this if you want each Codex sub-tree to land as its own PR for
independent review. The Claude prompt is unchanged. The single
Codex prompt above splits into four standalone prompts, one per
worker. Each worker creates its own branch:

- `track/codex-b1-api-brain`
- `track/codex-b2-agentphone`
- `track/codex-b3-stripe-agentmail`
- `track/codex-b4-ui`

To produce these from the fan-out prompt, take its "Worker N —"
block + the RULES + VERIFICATION sections + a single-deliverable
DELIVERABLE block. Each session reads the same contracts.

Trade-off: 4 separate PRs = 4 separate reviews + 4 separate
rebases. Slower at merge time but each PR is small enough to read
in one pass.

---

## Merge sequence (both layouts)

The PR order minimizes rebase friction.

```
1.  Track A merges first.        [PR #1]
    ├─ State + services + vision land on main.
    ├─ The Protocols are now backed by real implementations.
    └─ services/__init__.py exports make_services() pointing at
       the real impls instead of _stubs.

2.  Track B rebases onto main.
    ├─ One import swap, repo-wide:
    │     from fruit_market.services._stubs import make_services
    │   →
    │     from fruit_market.services import make_services
    ├─ Run `make check && pytest`. Fix any Pydantic field
    │  mismatches (rare; both tracks read the same Phase 1 schema).
    └─ Push.                     [PR #2]

3.  Merge PR #2. Tag v0.1-mvp.
```

**5-session merge:** same idea, but Codex sub-tree PRs land in this
order to minimize rebases:

```
B2 (AgentPhone, standalone)  → main
B3 (Stripe + AgentMail, standalone) → main
B1 (api + brain — depends on B2 + B3) → rebase → main
B4 (ui — depends on B1's /api/state/stream shape) → rebase → main
```

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Codex worker can't import `services._stubs` | Phase 1 not on its branch | `git fetch && git rebase origin/main` |
| Pydantic v2 ValidationError on a webhook envelope | Schema drift between Phase 1 and worker code | Open the PR, run `make check`; mypy will pinpoint the field |
| AgentPhone returns HTTP 403 | Default urllib User-Agent | Verify httpx with explicit Mozilla UA in `integrations/agentphone.py` |
| PaliGemma count is wildly wrong | Wrong noun or low-light image | Print the raw model output in `vision/model.py`; check it's an integer; tighten `max_new_tokens` |
| Stripe webhook signature fails locally | Wrong webhook secret for the tunnel | Refresh `STRIPE_WEBHOOK_SECRET` in `.env` from `stripe listen --print-secret` |
| Two tracks both edit `tests/conftest.py` | Phase 1 should own conftest | If conflict, take Phase 1's version; add track-specific fixtures in `tests/{track_a,track_b}/conftest.py` instead |
| Rebase shows import errors after stub→real swap | The real service has a slightly different signature than the Protocol | Look at `mypy` output first; usually a Optional vs. required field |

---

## What this guide deliberately doesn't cover

- **Sponge stretch implementation.** The contracts are in Phase 1
  but the real Sponge MCP client is post-MVP. The UI can show
  "RESTOCK ON THE WAY" stubs that won't fire until the MCP client
  is wired.
- **Pico keypad.** Hardware integration is a separate, lower-
  priority track — drop it until MVP works end-to-end.
- **Cloudflared tunnel setup.** Do this manually after both PRs
  merge, when you're ready for the live integration test.
- **CI.** Add GitHub Actions after v0.1-mvp ships. For the demo
  build, `make check && pytest` on your laptop is the bar.
