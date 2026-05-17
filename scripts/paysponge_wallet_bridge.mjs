#!/usr/bin/env node
/*
 * PaySponge Wallet bridge for the optional restock agent.
 *
 * Python invokes this script only when RESTOCK_PAYMENT_MODE=staging_live.
 * Keep stdout as a single JSON object; diagnostics go to stderr.
 */

import { SpongeWallet } from "@paysponge/sdk";

const command = process.argv[2] || "probe";

try {
  const input = await readJsonStdin();
  const wallet = await connectWallet();

  if (command === "probe") {
    writeJson({
      ok: true,
      tools: {
        submitPlan: typeof wallet.submitPlan === "function",
        approvePlan: typeof wallet.approvePlan === "function",
        paidFetch: typeof wallet.paidFetch === "function",
        x402Fetch: typeof wallet.x402Fetch === "function",
        mppFetch: typeof wallet.mppFetch === "function",
      },
    });
  } else if (command === "submit_plan") {
    const raw = await wallet.submitPlan(buildPlan(input));
    writeJson({ ok: true, plan_id: extractId(raw), raw: await normalize(raw) });
  } else if (command === "approve_plan") {
    const planId = requireString(input.plan_id, "plan_id");
    const raw = await wallet.approvePlan(planId);
    writeJson({ ok: true, raw: await normalize(raw) });
  } else if (command === "paid_fetch") {
    const args = buildFetchArgs(input);
    const raw = await wallet.paidFetch(args);
    const normalized = await normalize(raw);
    writeJson({
      ok: true,
      payment_id: extractPaymentId(normalized),
      receipt: extractReceipt(normalized),
      response: extractResponse(normalized),
      raw: normalized,
    });
  } else if (command === "x402_fetch") {
    const args = buildFetchArgs(input);
    const raw = await wallet.x402Fetch(args);
    const normalized = await normalize(raw);
    writeJson({
      ok: true,
      payment_id: extractPaymentId(normalized),
      receipt: extractReceipt(normalized),
      response: extractResponse(normalized),
      raw: normalized,
    });
  } else {
    throw new Error(`unknown command: ${command}`);
  }
} catch (error) {
  writeJson({
    ok: false,
    error: error instanceof Error ? error.message : String(error),
  });
  process.exitCode = 1;
}

async function connectWallet() {
  const apiKey = process.env.SPONGE_API_KEY;
  if (!apiKey) {
    throw new Error("SPONGE_API_KEY is required");
  }
  const options = { apiKey };
  if (process.env.SPONGE_API_BASE) {
    options.baseUrl = process.env.SPONGE_API_BASE;
  }
  return SpongeWallet.connect(options);
}

async function readJsonStdin() {
  let raw = "";
  for await (const chunk of process.stdin) {
    raw += chunk;
  }
  if (!raw.trim()) {
    return {};
  }
  return JSON.parse(raw);
}

function buildPlan(input) {
  const proposal = requireObject(input.proposal, "proposal");
  const body = requireObject(input.body, "body");
  const amount = proposal.amount_cents;
  const payloadHash = requireString(proposal.payload_hash, "proposal.payload_hash");

  return {
    title: `Restock ${proposal.qty} ${proposal.item_name}`,
    steps: [
      {
        type: "paid_fetch",
        args: buildFetchArgs({ proposal, body }),
      },
    ],
    metadata: {
      proposal_id: requireString(proposal.proposal_id, "proposal.proposal_id"),
      payload_hash: payloadHash,
      amount_cents: amount,
    },
  };
}

function buildFetchArgs(input) {
  const proposal = requireObject(input.proposal, "proposal");
  const body = requireObject(input.body, "body");
  const chain = input.preferred_chain || input.preferredChain || process.env.RESTOCK_SPONGE_PREFERRED_CHAIN || "base";
  return {
    url: requireString(proposal.gateway_url, "proposal.gateway_url"),
    method: "POST",
    body,
    chain,
    preferredChain: chain,
  };
}

async function normalize(value) {
  if (value && typeof value === "object" && typeof value.json === "function") {
    const headers = {};
    if (value.headers && typeof value.headers.forEach === "function") {
      value.headers.forEach((headerValue, key) => {
        headers[key] = headerValue;
      });
    }
    let body = null;
    try {
      body = await value.clone().json();
    } catch {
      body = await value.clone().text();
    }
    return {
      status: value.status,
      ok: value.ok,
      headers,
      body,
    };
  }
  try {
    return JSON.parse(JSON.stringify(value));
  } catch {
    return { value: String(value) };
  }
}

function extractId(raw) {
  const value = raw?.plan_id ?? raw?.planId ?? raw?.id ?? raw?.data?.plan_id ?? raw?.data?.planId ?? raw?.data?.id;
  if (!value) {
    throw new Error("PaySponge submitPlan response did not include a plan id");
  }
  return String(value);
}

function extractPaymentId(raw) {
  return String(raw?.payment_id ?? raw?.paymentId ?? raw?.id ?? raw?.data?.payment_id ?? raw?.data?.paymentId ?? "");
}

function extractReceipt(raw) {
  const direct = raw?.payment_receipt ?? raw?.paymentReceipt ?? raw?.receipt;
  const header = raw?.headers?.["payment-receipt"] ?? raw?.headers?.["Payment-Receipt"];
  const value = direct ?? header ?? null;
  return value === null ? null : String(value);
}

function extractResponse(raw) {
  if (raw?.body !== undefined) {
    return raw.body;
  }
  if (raw?.response !== undefined) {
    return raw.response;
  }
  if (raw?.data !== undefined) {
    return raw.data;
  }
  if (raw?.result !== undefined) {
    return raw.result;
  }
  return raw;
}

function requireObject(value, name) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${name} must be an object`);
  }
  return value;
}

function requireString(value, name) {
  if (typeof value !== "string" || value.length === 0) {
    throw new Error(`${name} must be a non-empty string`);
  }
  return value;
}

function writeJson(value) {
  process.stdout.write(`${JSON.stringify(value)}\n`);
}
