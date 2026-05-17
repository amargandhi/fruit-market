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

Flash Lite was picked over Flash, Pro, or Live because the brain
isn't reasoning about images — perception already happened at the
edge. Routing intent → tool → response is what small fast models
are good at. Override the model with `GEMINI_MODEL` in `.env` if
a demo specifically benefits from richer reasoning.

See [docs/architecture.md §1a](docs/architecture.md#1a-model-choices--edge-vs-cloud)
for the full rationale.

## PaySponge setup

PaySponge is optional for the stretch restock wallet flow. To enable the read-only readiness check, set these values in `.env`:

```env
SPONGE_ENABLED=1
SPONGE_API_BASE=https://api.wallet.paysponge.com
SPONGE_MCP_URL=https://api.wallet.paysponge.com/mcp
SPONGE_API_KEY=sponge_live_...
```

Then run `make check-sponsors`. The PaySponge probe calls `GET /api/agents/me`; it does not transfer funds or create payments.

The restock agent is separate and off by default. For a staged demo,
run `uvicorn fruit_market.restock.demo_supplier_app:app --port 8001`,
wrap its `POST /orders` route with PaySponge Gateway/x402, then set
`RESTOCK_ENABLED=1`, `RESTOCK_PAYMENT_MODE=staging_live`, and
`RESTOCK_SUPPLIER_GATEWAY_URL` to the Gateway order URL.
