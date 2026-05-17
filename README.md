<p align="center">
  <img src="docs/assets/fruit-market-icon.png" alt="Fruit Market app icon" width="180">
</p>

Fruit Market is a physical AI agent for market stall operators that tracks stock, takes calls, and processes transactions.

## How the two models split the work

Two LLMs, two responsibilities:

- **PaliGemma 2 3B mix-224** runs on the Mac via MLX. It does the
  perception: every ~3 s it snapshots the USB webcam and counts
  the active item with the model's native `count {noun}\n` task.
  ~0.5 s per count, deterministic, no cloud round-trip.
- **Gemini 3.1 Flash Lite** runs in the cloud. It's the phone
  brain: hears the transcript from AgentPhone, picks a tool
  (`resolve_item`, `quote_order`, `reserve_order`,
  `create_checkout`…), and writes the one-sentence reply that
  goes back to the caller. ~0.7 s end-to-end.
  Because it uses Gemini Flash Lite, the same phone flow is multilingual:
  callers can speak naturally without a language-specific script.

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

The phone brain should sound like a good market seller, not just a
database lookup. The apple and banana knowledge includes variety,
tasting notes, best uses, ripeness cues, pairings, and a short sales
tip. Those fields are returned by the Gemini catalog tools, and the
same content is packaged for Moss in `docs/moss_fruit_catalog.json`.
