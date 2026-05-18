<p align="center">
  <img src="docs/assets/fruit-market-icon.png" alt="Fruit Market app icon" width="180">
</p>

# Fruit Market

A working physical-AI agent for a one-operator fruit stall.
Live edge vision counts the shelf, a phone agent takes orders,
Stripe collects payment, AgentMail closes the loop, and a Pico
keypad gives the operator a one-press dashboard.

**Status: working end-to-end on a 2024 Mac mini M4 + Logitech C920 +
Raspberry Pi Pico 2 W.** 150 unit + contract tests passing on every
push; ruff + mypy strict clean.

```
phone call ─▶ AgentPhone ─▶ /webhooks/phone ─▶ Gemini Flash Lite (tool-using brain)
                                                 │
                            PaliGemma 2 ◀────── live inventory ─▶ Stripe Checkout
                            (edge vision)                            │
                                                                     ▼
                                  ┌──── AgentMail ◀── /webhooks/stripe ──┐
                                  │                                       │
                              customer receipt                      mark paid
                              + operator pickup email
                                                                     │
                              stock-low ──▶ supplier basket ──▶ PaySponge payment
                                            (kiosk + Pico LED)
```

## What the two models do

Two LLMs, two responsibilities, both fast enough to feel real-time:

- **PaliGemma 2 3B mix-224** runs on the Mac via MLX. Native
  `count {noun}\n` task, deterministic greedy decoding,
  **~450 ms per count warm**. The image is pre-cropped to a
  bottom-center ROI before inference so the 224×224 encoder gets
  ~4× more pixels per fruit — better accuracy on clustered
  scenes. Per-item single-noun calls (no batched-class confusion).
- **Gemini 3.1 Flash Lite** runs in the cloud. The phone brain:
  hears the AgentPhone transcript, picks a tool (`resolve_item`,
  `quote_order`, `reserve_order`, `create_checkout`,
  `send_sms`…), and writes the one-sentence reply that goes
  back to the caller. ~0.7 s end-to-end, multilingual out of the
  box (no per-language scripting).

Flash Lite was picked over Flash, Pro, or Live because the brain
isn't reasoning about images — perception already happened at the
edge. Routing intent → tool → response is what small fast models
are good at. Override the model with `GEMINI_MODEL` in `.env` if
a demo specifically benefits from richer reasoning.

See [docs/architecture.md §1a](docs/architecture.md#1a-model-choices--edge-vs-cloud)
for the full rationale.

## Full demo path

The demo is one continuous loop from physical stock to phone order to
restock:

1. **Launch the stall app.** The camera stream and local edge model
   warm up on the Mac. The operator sets the apples on the counter.
2. **Operator opens the store.** The operator presses the Pico
   `ready` / store-open button. This opens the model inference gate;
   the first PaliGemma count becomes the starting stock count.
3. **Customer calls the market number.** AgentPhone answers the call
   and sends the transcript to `/webhooks/phone`.
4. **Gemini is the phone brain.** Gemini Flash Lite reads the
   transcript, calls tools against live catalog/inventory, reserves
   stock, creates a Stripe Checkout link, and tells AgentPhone what
   to say or text back.
5. **Customer pays.** The customer receives the Stripe link by SMS,
   pays, and enters an email address in Stripe Checkout.
6. **Receipt and operator notice go out.** Stripe calls
   `/webhooks/stripe`; the backend marks the order paid, AgentMail
   sends the customer receipt, and AgentMail emails the operator what
   fruit to set aside.
7. **Pico/kiosk alerts the operator.** The paid order appears as a
   pending packing action. The operator sets the fruit aside and
   confirms it on the Pico/kiosk.
8. **Low stock triggers restock.** When the edge count reaches the
   reorder threshold, the backend stages a supplier basket.
9. **Operator approves restock.** AgentMail emails the operator the
   supplier basket details, AgentPhone SMS sends the same restock
   summary, and the Pico `supply_buy` action lights up.
10. **PaySponge pays only after approval.** When the operator presses
    the Pico restock approval button, the backend approves the locked
    PaySponge plan and performs the paid supplier request.

## Sponsor and model map

| Sponsor / system | Where it appears in the demo |
|---|---|
| **AgentPhone** | Owns the phone number, transcribes calls, sends the caller SMS with the Stripe link, and sends operator restock SMS. |
| **Gemini 3.1 Flash Lite** | The cloud phone brain. It routes caller intent into tools such as `resolve_item`, `get_inventory`, `reserve_order`, `create_checkout`, and `send_sms`. |
| **PaliGemma 2 via MLX** | The local edge AI model. It counts visible fruit from camera frames; it is not the phone brain. |
| **Stripe** | Checkout link, card payment, and payment webhook back into the backend. |
| **AgentMail** | Customer receipt email and operator emails for paid orders/restock approvals. |
| **PaySponge** | Locked restock payment plan and paid supplier request after Pico approval. |
| **Moss** | Product knowledge and optional semantic item resolution: apple/banana varieties, tasting notes, ripeness cues, pairings, and sales guidance. The Moss-ready catalog lives in `docs/moss_fruit_catalog.json`. |
| **Pico keypad** | Local operator approval surface: store open, pack paid order, approve restock. |

## Moss product knowledge

The phone brain sounds like a good market seller, not a database
lookup. Thirteen fruits ship with full sales-grade detail today
(apple, banana, orange, lemon, pear, grape, strawberry, cherry,
peach, watermelon, pineapple, mango, avocado) — variety, tasting
notes, best uses, ripeness cues, pairings, and a sales tip per
fruit, plus shorthand aliases ("nanas" → banana, "avo" → avocado).
The Gemini catalog tools surface these fields on every call; the
same content is also packaged for Moss in `docs/moss_fruit_catalog.json`.

## What works today (no env config needed)

The kiosk and edge model run with zero configuration on a fresh
clone:

- **Live camera feed** — Mac daemon (`FruitMarketCamera.app`) captures
  at ~15 FPS in its own process so Python is decoupled from TCC
  sandboxing. The kiosk polls a cache every 200 ms; you see a fresh
  frame 5× per second with a visible frame-counter that proves it's
  alive even on a still scene.
- **Edge AI counting** — PaliGemma 2 runs from boot, no gate, no
  start button. Counts every taught item on a 0.5 s poll, writes
  into inventory within ~1.5 s of any scene change.
- **Auto-seeded catalog** — apple ($1.00) + banana ($0.75) are
  seeded into the catalog on first boot, so the watcher has
  something to count from second 1. No teach step required for
  the basic demo.
- **Operator controls** — the kiosk shows fruit cards with stock,
  paid/packed lanes, an Activity panel for count changes, an
  Edge AI log, and an "Open for orders" toggle. The Pico keypad
  mirrors the same five operator actions (READY · PACKED ·
  CANCEL · COUNT_NOW · SUPPLY_BUY) with server-side debouncing.

## What works when sponsor APIs are configured (`.env`)

The boot log shows an `env audit` line listing exactly which steps
are armed:

```
env audit: 10/10 sponsor knobs configured (AGENTPHONE_API_KEY, ...)
```

If any are missing, the app still boots — the corresponding step
just silently no-ops, with a warning at startup so the operator
sees the gap before traffic lands.

| Sponsor knob | What it arms |
|---|---|
| `GEMINI_API_KEY` | Phone-agent brain (step 7) |
| `AGENTPHONE_API_KEY` + `AGENTPHONE_WEBHOOK_SECRET` | `/webhooks/phone` signed inbound (step 6) |
| `AGENTPHONE_SEND_MODE=live` | Customer SMS for Stripe link (step 13) + operator SMS (steps 25, failures) |
| `STRIPE_API_KEY` + `STRIPE_WEBHOOK_SECRET` | Checkout sessions + paid/failed webhooks (steps 12, 15) |
| `AGENTMAIL_ENABLED=1` + `AGENTMAIL_API_KEY` | Receipt + operator emails (steps 17, 18, 24) |
| `OPERATOR_EMAIL` + `OPERATOR_PHONE` | Where the operator notifications go (steps 18, 25) |
| `RESTOCK_ENABLED=1` + PaySponge creds | Restock proposals + paid supplier orders (steps 22-29) |

## Reliability features built in

- **Deterministic customer SMS** — `create_checkout` always texts
  the Stripe link to the customer; we don't trust the model to
  remember (step 13 of the demo chain never silently drops).
- **Stripe failure handling** — `payment_failed`, `expired`,
  `async_payment_failed`, `charge.failed` all release the
  reservation and SMS the operator.
- **PaySponge failure SMS** — restock failures (supplier quote,
  caps, expiry, payment, plan submission) all SMS the operator
  with the failure stage and reason.
- **Pico debouncing** — `packed`, `cancel`, `confirm` debounced
  at 1.5 s, `supply_buy` at 2.5 s (money-moving action gets extra
  room).
- **Order cancellation** — both reserved and paid orders can be
  cancelled from the kiosk or the Pico CANCEL key (paid-order
  refunds handled out-of-band).
- **Teach undo** — `DELETE /api/teach/{item_id}` zeroes an item's
  stock + clears active-item pointer for recovery from a typo
  without rebuilding the event log.

## Tuning knobs

All vision tuning is via env vars — defaults are tuned for a
typical Mac + C920 setup. Override in `.env` only if your camera
angle or lighting demands it.

| Var | Default | Effect |
|---|---|---|
| `FM_VISION_COUNT_MODE` | `count` | Inference task. `count` = integer (~450 ms, default). `detect` = per-box (~1 s, cross-class dedup, more compute). |
| `FM_VISION_ROI` | `bottom-center` | Pre-crop preset. Other options: `off`, `full`, `tight`, or a raw `y0,x0,y1,x1` tuple. |
| `FM_VISION_POLL_SECONDS` | `0.5` | How often the watcher checks the streamer for motion. |
| `FM_VISION_MOTION_THRESHOLD` | `3.0` | Byte-length delta % to count as scene change. Lower = more sensitive. |
| `FM_VISION_HEARTBEAT_SECONDS` | `5` | Force a re-count if nothing's changed for this long (catches frozen cameras). |
| `FM_VISION_STABILITY_TICKS` | `1` | Require count to repeat for N ticks before committing. >1 = laggy but steadier. |
| `FM_VISION_MAX_ITEMS` | `4` | Cap items per tick (per-item model calls). |
| `FM_CAMERA_BACKEND` | `cv2` | Capture backend. Set to `daemon` to read from `FruitMarketCamera.app` (recommended on Mac). |
| `FM_CAMERA_DAEMON_URL` | `http://127.0.0.1:8765` | Daemon endpoint when `FM_CAMERA_BACKEND=daemon`. |

## Quickstart

```
make install         # uv sync; one-time
make app             # build the camera daemon .app (one-time)
open apps/fm-camera/FruitMarketCamera.app --args --daemon --device "C920"
FM_CAMERA_BACKEND=daemon make dev    # uvicorn on :8000
make pico            # (optional) Pico USB bridge in another terminal
```

Open `http://127.0.0.1:8000/` — fresh boot shows 🍎 0 · 🍌 0
(seeded catalog) and within 3-5 seconds (model warmup + first
tick) the counts catch up to whatever's on the camera. Move
fruit, watch counts update in 1.5-2 seconds.

## Tests

```
make test    # 150 unit + contract tests
make check   # ruff + mypy strict
```

Tests run without any sponsor keys (mocked at the IO boundary).
