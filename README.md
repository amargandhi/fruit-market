Fruit Market is a physical AI agent for market stall operators that tracks stock, takes calls, and processes transactions.

## PaySponge setup

PaySponge is optional for the stretch restock wallet flow. To enable the read-only readiness check, set these values in `.env`:

```env
SPONGE_ENABLED=1
SPONGE_API_BASE=https://api.wallet.paysponge.com
SPONGE_MCP_URL=https://api.wallet.paysponge.com/mcp
SPONGE_API_KEY=sponge_live_...
```

Then run `make check-sponsors`. The PaySponge probe calls `GET /api/agents/me`; it does not transfer funds or create payments.
