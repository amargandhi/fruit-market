const state = {
  catalog: [],
  active_item_id: null,
  orders: [],
  pending: {},
  restock: null,
  demo_active: false,
};

const els = {
  status: document.querySelector("#connection-status"),
  refresh: document.querySelector("#refresh-button"),
  activeCount: document.querySelector("#active-count"),
  activeLabel: document.querySelector("#active-label"),
  stockAlert: document.querySelector("#stock-alert"),
  restockPanel: document.querySelector("#restock-panel"),
  catalogList: document.querySelector("#catalog-list"),
  reserved: document.querySelector("#orders-reserved"),
  paid: document.querySelector("#orders-paid"),
  packed: document.querySelector("#orders-packed"),
  teachForm: document.querySelector("#teach-form"),
  teachInput: document.querySelector("#teach-input"),
  teachProposal: document.querySelector("#teach-proposal"),
  cameraFeed: document.querySelector("#camera-feed"),
  cameraStatus: document.querySelector("#camera-status"),
  cameraCounts: document.querySelector("#camera-counts"),
  cameraOverlayEmpty: document.querySelector("#camera-overlay-empty"),
  demoNotStarted: document.querySelector("#demo-not-started"),
  demoStatePill: document.querySelector("#demo-state-pill"),
  startDemoButton: document.querySelector("#start-demo-button"),
  logList: document.querySelector("#log-list"),
  logMeta: document.querySelector("#log-meta"),
  cameraPoll: document.querySelector("#camera-poll"),
};

let eventSource = null;
let activeProposal = null;

function dollars(cents) {
  return `$${(cents / 100).toFixed(2)}`;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `Request failed: ${response.status}`);
  }
  return response.json();
}

async function loadState() {
  setStatus("Refreshing", "");
  const next = await api("/api/state");
  state.catalog = next.catalog || [];
  state.active_item_id = next.active_item_id || null;
  state.orders = next.orders || [];
  state.pending = next.pending || {};
  state.restock = next.restock || null;
  state.demo_active = !!next.demo_active;
  render();
  renderDemoGate();
  setStatus("Live", "online");
}

function renderDemoGate() {
  if (els.demoNotStarted) {
    els.demoNotStarted.hidden = state.demo_active;
  }
  if (els.demoStatePill) {
    els.demoStatePill.dataset.state = state.demo_active ? "on" : "off";
    els.demoStatePill.innerHTML = state.demo_active
      ? `<span class="dot"></span> Demo running`
      : `<span class="dot"></span> Demo idle`;
  }
  if (els.startDemoButton) {
    els.startDemoButton.hidden = state.demo_active;
  }
}

async function startDemoFromKiosk() {
  try {
    const res = await api("/api/demo/start", { method: "POST" });
    state.demo_active = !!res.demo_active;
    renderDemoGate();
  } catch (err) {
    console.error("failed to start demo:", err);
  }
}

if (els.startDemoButton) {
  els.startDemoButton.addEventListener("click", () => void startDemoFromKiosk());
}

// Poll demo state every 1.5s so the kiosk picks up Pico-driven starts
// (the Pico bridge POSTs /api/pico/action, which sets demo_active
// on the server; the SSE stream doesn't push that yet).
setInterval(async () => {
  try {
    const res = await api("/api/demo/active");
    if (!!res.demo_active !== state.demo_active) {
      state.demo_active = !!res.demo_active;
      renderDemoGate();
    }
  } catch {
    // silent — connection-status pill already shows offline
  }
}, 1500);

function connectStream() {
  if (eventSource) {
    eventSource.close();
  }
  eventSource = new EventSource("/api/state/stream");
  eventSource.onopen = () => setStatus("Live", "online");
  eventSource.onerror = () => setStatus("Reconnecting", "offline");
  eventSource.addEventListener("state.catalog", (event) => {
    state.catalog = JSON.parse(event.data).catalog || [];
    render();
  });
  eventSource.addEventListener("state.inventory", (event) => {
    state.catalog = JSON.parse(event.data).catalog || state.catalog;
    render();
  });
  eventSource.addEventListener("state.orders", (event) => {
    state.orders = JSON.parse(event.data).orders || [];
    renderOrders();
  });
  eventSource.addEventListener("state.active_item", (event) => {
    state.active_item_id = JSON.parse(event.data).active_item_id || null;
    renderCatalog();
  });
  eventSource.addEventListener("state.stock_low", (event) => {
    const lowItems = JSON.parse(event.data).items || [];
    els.stockAlert.hidden = lowItems.length === 0;
  });
  eventSource.addEventListener("state.restock", (event) => {
    const payload = JSON.parse(event.data);
    state.pending = payload.pending || {};
    state.restock = payload.restock || null;
    renderRestock();
  });
}

function setStatus(text, className) {
  els.status.textContent = text;
  els.status.className = `status ${className}`.trim();
}

function render() {
  renderCatalog();
  renderOrders();
  renderRestock();
}

function renderCatalog() {
  const active = state.catalog.find((item) => item.item_id === state.active_item_id);
  els.activeCount.textContent = active ? active.physical_count : 0;
  els.activeLabel.textContent = active ? active.name : "No active item";
  els.stockAlert.hidden = !state.catalog.some((item) => item.is_low);

  if (state.catalog.length === 0) {
    els.catalogList.innerHTML = `<div class="empty">No items taught</div>`;
    return;
  }
  els.catalogList.innerHTML = state.catalog
    .map(
      (item) => `
        <article class="item-row ${item.item_id === state.active_item_id ? "active" : ""}">
          <div class="row-main">
            <strong>${escapeHtml(item.name)}</strong>
            <span>${item.physical_count}</span>
          </div>
          <div class="row-meta">
            <span>${dollars(item.price_cents)}</span>
            <span>${item.is_low ? "Low" : "Stocked"}</span>
          </div>
          <button class="row-action" type="button" data-switch="${item.item_id}" ${
            item.item_id === state.active_item_id ? "disabled" : ""
          }>${item.item_id === state.active_item_id ? "Active" : "Switch"}</button>
        </article>
      `,
    )
    .join("");
}

function renderRestock() {
  const restock = state.restock;
  if (!restock) {
    els.restockPanel.hidden = true;
    els.restockPanel.innerHTML = "";
    return;
  }
  els.restockPanel.hidden = false;
  const pending = restock.status === "pending_approval";
  const failed = restock.status === "failed" || restock.status === "rejected";
  const eta = restock.eta_minutes ? `${restock.eta_minutes}m ETA` : "ETA pending";
  const emailLabel = restockEmailLabel(restock);
  const basketLink = restock.basket_url
    ? `<a class="restock-link" href="${escapeHtml(restock.basket_url)}" target="_blank" rel="noreferrer">Open supplier basket</a>`
    : "";
  els.restockPanel.className = `restock-panel ${restock.status}`;
  els.restockPanel.innerHTML = `
    <div class="row-main">
      <strong>${escapeHtml(restock.item_name)} restock</strong>
      <span>${dollars(restock.amount_cents)}</span>
    </div>
    <div class="row-meta">
      <span>${restock.qty} from ${escapeHtml(restock.supplier_name)}</span>
      <span>${statusLabel(restock.status)} · ${escapeHtml(eta)}</span>
    </div>
    <div class="restock-demo-flow">
      <span>Basket staged</span>
      <span>${escapeHtml(emailLabel)}</span>
      <span>${pending ? "Waiting for Pico" : statusLabel(restock.status)}</span>
    </div>
    ${basketLink}
    ${
      failed && restock.failure_reason
        ? `<div class="restock-error">${escapeHtml(restock.failure_reason)}</div>`
        : ""
    }
    ${
      pending
        ? `<div class="proposal-actions">
            <button type="button" data-approve-restock>Approve</button>
            <button class="secondary danger-secondary" type="button" data-reject-restock>Reject</button>
          </div>`
        : ""
    }
  `;
}

function restockEmailLabel(restock) {
  if (restock.email_status === "sent") {
    return restock.operator_email ? `Email sent to ${restock.operator_email}` : "Email sent";
  }
  if (restock.email_status === "failed") {
    return "Email failed";
  }
  return "Email not configured";
}

function renderOrders() {
  const groups = {
    reserved: state.orders.filter((order) => order.status === "reserved"),
    paid: state.orders.filter((order) => order.status === "paid"),
    packed: state.orders.filter((order) => order.status === "packed"),
  };
  renderOrderGroup(els.reserved, groups.reserved);
  renderOrderGroup(els.paid, groups.paid);
  renderOrderGroup(els.packed, groups.packed);
}

function renderOrderGroup(container, orders) {
  if (orders.length === 0) {
    container.innerHTML = `<div class="empty">None</div>`;
    return;
  }
  container.innerHTML = orders
    .map(
      (order) => `
        <article class="order-row">
          <div class="row-main">
            <strong>${escapeHtml(order.item_name)}</strong>
            <span>x${order.qty}</span>
          </div>
          <div class="row-meta">
            <span>${dollars(order.total_cents)}</span>
            <span>${escapeHtml(order.customer_phone)}</span>
          </div>
          ${
            order.status === "paid"
              ? `<button class="row-action" type="button" data-pack="${order.order_id}">Pack</button>`
              : ""
          }
        </article>
      `,
    )
    .join("");
}

function renderProposal() {
  if (!activeProposal) {
    els.teachProposal.hidden = true;
    els.teachProposal.innerHTML = "";
    return;
  }
  els.teachProposal.hidden = false;
  els.teachProposal.innerHTML = `
    <div class="row-main">
      <strong>${escapeHtml(activeProposal.name)}</strong>
      <span>${dollars(activeProposal.price_cents)}</span>
    </div>
    <div class="row-meta">
      <span>${activeProposal.initial_count} in stock</span>
      <span>Restock at ${activeProposal.reorder_threshold}</span>
    </div>
    <div class="proposal-actions">
      <button type="button" data-confirm-teach>Confirm</button>
      <button class="secondary" type="button" data-reject-teach>Reject</button>
    </div>
  `;
}

async function proposeTeach(event) {
  event.preventDefault();
  const transcript = els.teachInput.value.trim();
  if (!transcript) return;
  activeProposal = (await api("/api/teach", {
    method: "POST",
    body: JSON.stringify({ transcript }),
  })).proposal;
  renderProposal();
}

async function confirmTeach() {
  if (!activeProposal) return;
  await api("/api/teach/confirm", {
    method: "POST",
    body: JSON.stringify({ proposal_id: activeProposal.proposal_id }),
  });
  activeProposal = null;
  els.teachInput.value = "";
  renderProposal();
  await loadState();
}

async function switchActive(itemId) {
  await api("/api/active-item", {
    method: "POST",
    body: JSON.stringify({ item_id: itemId }),
  });
  await loadState();
}

async function packOrder(orderId) {
  await api(`/api/orders/${encodeURIComponent(orderId)}/pack`, { method: "POST" });
  await loadState();
}

async function picoAction(action) {
  await api("/api/pico/action", {
    method: "POST",
    body: JSON.stringify({ action }),
  });
  await loadState();
}

function statusLabel(status) {
  const labels = {
    pending_approval: "Approval needed",
    approved: "Approved",
    payment_started: "Ordering",
    ordered: "Ordered",
    received: "Received",
    rejected: "Rejected",
    failed: "Failed",
  };
  return labels[status] || status;
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => {
    const entities = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
    return entities[char];
  });
}

document.addEventListener("click", (event) => {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return;
  const switchId = target.dataset.switch;
  const packId = target.dataset.pack;
  if (switchId) void switchActive(switchId);
  if (packId) void packOrder(packId);
  if (target.dataset.confirmTeach !== undefined) void confirmTeach();
  if (target.dataset.approveRestock !== undefined) void picoAction("supply_buy");
  if (target.dataset.rejectRestock !== undefined) void picoAction("cancel");
  if (target.dataset.rejectTeach !== undefined) {
    activeProposal = null;
    renderProposal();
  }
});

els.refresh.addEventListener("click", () => void loadState());
els.teachForm.addEventListener("submit", (event) => void proposeTeach(event));

void loadState();
connectStream();

// ─── Live camera feed ───────────────────────────────────────────────
// The streamer captures continuously at 1-5 FPS (per backend) into a
// JPEG cache. We poll the cache every 500 ms — so the browser sees
// fresh frames at the streamer's rate, decoupled from the model loop.
// Each PaliGemma tick (~3 s) updates the catalog counts via SSE;
// those drive the overlay chips.

const CAMERA_POLL_MS = 500;

const FRUIT_ICONS = {
  apple: "🍎",
  banana: "🍌",
  orange: "🍊",
  lemon: "🍋",
  pear: "🍐",
  grape: "🍇",
  strawberry: "🍓",
  cherry: "🍒",
  peach: "🍑",
  watermelon: "🍉",
  pineapple: "🍍",
  mango: "🥭",
  avocado: "🥑",
  tomato: "🍅",
  carrot: "🥕",
};

function fruitIcon(name) {
  const lower = (name || "").toLowerCase().trim();
  // Match singular and plural ("apple" / "apples").
  for (const key of Object.keys(FRUIT_ICONS)) {
    if (lower === key || lower === `${key}s`) return FRUIT_ICONS[key];
  }
  return "🛒";
}

function refreshCameraFeed() {
  if (!els.cameraFeed) return;
  // Bust caches with a timestamp; the endpoint sets no-store but
  // some browsers still cache aggressively on identical URLs.
  els.cameraFeed.src = `/api/camera/frame.jpg?t=${Date.now()}`;
}

function refreshCameraOverlay() {
  if (!els.cameraCounts) return;
  const empty = !state.catalog || state.catalog.length === 0;
  if (els.cameraOverlayEmpty) els.cameraOverlayEmpty.hidden = !empty;
  if (empty) {
    els.cameraCounts.innerHTML = "";
    return;
  }
  const activeId = state.active_item_id;
  const html = state.catalog
    .map((item) => {
      const active = item.item_id === activeId ? "true" : "false";
      const icon = fruitIcon(item.name);
      return `
        <span class="camera-count-chip" data-active="${active}">
          <span class="icon">${icon}</span>
          <span class="noun">${item.name}</span>
          <span class="num">${item.physical_count}</span>
        </span>`;
    })
    .join("");
  els.cameraCounts.innerHTML = html;
}

if (els.cameraFeed) {
  els.cameraFeed.addEventListener("error", () => {
    if (els.cameraStatus) {
      els.cameraStatus.textContent = "starting…";
      els.cameraStatus.dataset.state = "off";
    }
  });
  els.cameraFeed.addEventListener("load", () => {
    if (els.cameraStatus) {
      els.cameraStatus.textContent = "live";
      els.cameraStatus.dataset.state = "live";
    }
  });
  refreshCameraFeed();
  refreshCameraOverlay();
  setInterval(refreshCameraFeed, CAMERA_POLL_MS);
  setInterval(refreshCameraOverlay, 400);
}

// ─── Edge AI Log ────────────────────────────────────────────────────
// Polls /api/vision/log every 1.5 s. Shows the last N count events with
// time + noun + integer. Recent rows (last 4 s) get a left accent to
// draw the eye to fresh model output.

const LOG_POLL_MS = 1500;
const LOG_FRESH_WINDOW_MS = 4000;
const LOG_MAX_ROWS = 12;

let lastLogTs = null;

function formatTime(iso) {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

async function refreshLog() {
  if (!els.logList) return;
  let entries;
  try {
    const res = await fetch("/api/vision/log?limit=" + LOG_MAX_ROWS);
    if (!res.ok) return;
    const data = await res.json();
    entries = Array.isArray(data.entries) ? data.entries : [];
  } catch {
    return;
  }
  const now = Date.now();
  const html = entries
    .slice(0, LOG_MAX_ROWS)
    .map((e) => {
      const ts = new Date(e.ts).getTime();
      const fresh = !isNaN(ts) && now - ts < LOG_FRESH_WINDOW_MS;
      const icon = fruitIcon(e.item_name);
      return `
        <div class="log-row${fresh ? " new" : ""}">
          <span class="ts">${formatTime(e.ts)}</span>
          <span class="noun">${icon} <strong>${e.item_name}</strong> &rarr;</span>
          <span class="num">${e.count}</span>
        </div>`;
    })
    .join("");
  els.logList.innerHTML = html;
  if (els.logMeta) {
    els.logMeta.textContent = entries.length
      ? `${entries.length} recent count${entries.length === 1 ? "" : "s"}`
      : "";
  }
  if (entries.length > 0) lastLogTs = entries[0].ts;
}

if (els.logList) {
  void refreshLog();
  setInterval(() => void refreshLog(), LOG_POLL_MS);
}
