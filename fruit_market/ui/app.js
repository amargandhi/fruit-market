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
  stockAlert: document.querySelector("#stock-alert"),
  restockPanel: document.querySelector("#restock-panel"),
  catalogList: document.querySelector("#catalog-list"),
  reserved: document.querySelector("#orders-reserved"),
  paid: document.querySelector("#orders-paid"),
  packed: document.querySelector("#orders-packed"),
  cameraFeed: document.querySelector("#camera-feed"),
  cameraStatus: document.querySelector("#camera-status"),
  cameraCounts: document.querySelector("#camera-counts"),
  demoStatePill: document.querySelector("#demo-state-pill"),
  startDemoButton: document.querySelector("#start-demo-button"),
  logList: document.querySelector("#log-list"),
  logMeta: document.querySelector("#log-meta"),
  activityList: document.querySelector("#activity-list"),
  activityMeta: document.querySelector("#activity-meta"),
  cameraPoll: document.querySelector("#camera-poll"),
};

let eventSource = null;

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
  // demo_active is a SEMANTIC flag now ("open for phone orders"),
  // not a video/inference gate. The camera streams and PaliGemma
  // counts from boot regardless. This function just updates the
  // pill + button so the operator can see + flip the signal.
  if (els.demoStatePill) {
    els.demoStatePill.dataset.state = state.demo_active ? "on" : "off";
    els.demoStatePill.innerHTML = state.demo_active
      ? `<span class="dot"></span> Open for orders`
      : `<span class="dot"></span> Stocking`;
  }
  if (els.startDemoButton) {
    els.startDemoButton.textContent = state.demo_active
      ? "↩ Back to stocking"
      : "✓ Open for orders";
  }
}

async function toggleOpenForOrders() {
  try {
    const path = state.demo_active ? "/api/demo/stop" : "/api/demo/start";
    const res = await api(path, { method: "POST" });
    state.demo_active = !!res.demo_active;
    renderDemoGate();
  } catch (err) {
    console.error("failed to toggle open-for-orders:", err);
  }
}

if (els.startDemoButton) {
  els.startDemoButton.addEventListener("click", () => void toggleOpenForOrders());
}

// Poll demo state every 1.5s so the kiosk picks up Pico-driven
// "open for orders" flips (the Pico bridge POSTs /api/pico/action,
// which sets demo_active on the server; the SSE stream doesn't
// push that yet). Video + AI counting are unaffected — this poll
// only keeps the pill/button in sync.
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
  els.status.className = "status-pill";
  els.status.dataset.state = className === "online" ? "online" : "off";
}

function render() {
  renderCatalog();
  renderOrders();
  renderRestock();
}

function renderCatalog() {
  els.stockAlert.hidden = !state.catalog.some((item) => item.is_low);

  if (state.catalog.length === 0) {
    els.catalogList.innerHTML = `<div class="empty">Waiting for first AI count…</div>`;
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

// Teach panel was removed from the UI — apple + banana are seeded
// at boot. The POST /api/teach endpoint still works for power
// users (or future re-introduction).

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
  if (target.dataset.approveRestock !== undefined) void picoAction("supply_buy");
  if (target.dataset.rejectRestock !== undefined) void picoAction("cancel");
});

els.refresh.addEventListener("click", () => void loadState());

void loadState();
connectStream();

// ─── Live camera feed ───────────────────────────────────────────────
// The streamer captures continuously at 5-10 FPS (per backend) into a
// JPEG cache. We poll the cache every 200 ms — the streamer's daemon
// backend produces 10 FPS, so polling at 5 Hz gives the browser
// roughly half of those frames. (Faster than 200 ms wastes bandwidth
// without visible benefit; slower than 250 ms makes a static scene
// look frozen to operators.)
//
// Every successful frame load bumps a visible counter + age display
// so the operator can SEE the feed is live even when nothing in the
// scene is moving. Without that, a still bowl of fruit looks frozen.

const CAMERA_POLL_MS = 200;
let cameraFrameCount = 0;
let cameraLastSwapMs = 0;
const cameraFrameCountEl = document.querySelector("#camera-frame-count");
const cameraFrameAgeEl = document.querySelector("#camera-frame-age");
const cameraFrameMeterEl = document.querySelector("#camera-frame-meter");

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
  if (!state.catalog || state.catalog.length === 0) {
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
    els.cameraFeed.dataset.state = "offline";
    if (els.cameraStatus) {
      els.cameraStatus.textContent = "starting…";
      els.cameraStatus.dataset.state = "off";
    }
  });
  els.cameraFeed.addEventListener("load", () => {
    els.cameraFeed.dataset.state = "live";
    if (els.cameraStatus) {
      els.cameraStatus.textContent = "live";
      els.cameraStatus.dataset.state = "live";
    }
    // Every successful frame load bumps the meter so the operator
    // sees the feed is alive even when the scene is static. The
    // dot pulse fires via CSS animation re-trigger.
    const now = performance.now();
    cameraFrameCount += 1;
    if (cameraFrameCountEl) cameraFrameCountEl.textContent = cameraFrameCount.toString();
    if (cameraFrameAgeEl) {
      const dtMs = cameraLastSwapMs ? Math.round(now - cameraLastSwapMs) : 0;
      cameraFrameAgeEl.textContent = `${dtMs}ms gap`;
    }
    cameraLastSwapMs = now;
    if (cameraFrameMeterEl) {
      cameraFrameMeterEl.classList.remove("pulse");
      // Force reflow so the animation actually re-triggers
      void cameraFrameMeterEl.offsetWidth;
      cameraFrameMeterEl.classList.add("pulse");
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

// ─── Activity panel (count CHANGES only) ───────────────────────────
// Polls /api/vision/activity every 1s. Each entry has a kind:
//   added → 🍎 banana: 3 → 4 (ADDED 1)        green
//   removed → 🍌 banana: 3 → 2 (REMOVED 1)    amber
//   out → 🍎 apple: 1 → 0 (OUT OF STOCK)      red
//   restocked → 🍎 apple: 0 → 4 (RESTOCKED)   green pulse
//   first_seen → 🍌 banana: — → 3 (FIRST SEEN) grey

const ACTIVITY_POLL_MS = 1000;
const ACTIVITY_FRESH_WINDOW_MS = 3000;
const ACTIVITY_MAX_ROWS = 10;

const KIND_LABEL = {
  added: "Added",
  removed: "Removed",
  out: "Out of stock",
  restocked: "Restocked",
  first_seen: "First seen",
};

function activityNarrative(entry) {
  const prev = entry.prev === null || entry.prev === undefined ? "—" : entry.prev;
  const next = entry.new;
  const delta = entry.delta;
  switch (entry.kind) {
    case "added":     return `Added ${delta}`;
    case "removed":   return `Removed ${Math.abs(delta)}`;
    case "out":       return `Out of stock`;
    case "restocked": return `Restocked +${delta}`;
    default:          return KIND_LABEL[entry.kind] || entry.kind;
  }
}

async function refreshActivity() {
  if (!els.activityList) return;
  let entries;
  try {
    const res = await fetch("/api/vision/activity?limit=" + ACTIVITY_MAX_ROWS);
    if (!res.ok) return;
    const data = await res.json();
    entries = Array.isArray(data.entries) ? data.entries : [];
  } catch {
    return;
  }
  const now = Date.now();
  const html = entries
    .slice(0, ACTIVITY_MAX_ROWS)
    .map((e) => {
      const ts = e.ts ? e.ts * 1000 : 0;
      const fresh = ts && now - ts < ACTIVITY_FRESH_WINDOW_MS;
      const icon = fruitIcon(e.item_name);
      const prev = e.prev === null || e.prev === undefined ? "—" : e.prev;
      return `
        <div class="activity-row${fresh ? " fresh" : ""}" data-kind="${e.kind}">
          <span class="ts">${formatTime(new Date(ts).toISOString())}</span>
          <div class="narrative">
            <span class="item">${icon} ${e.item_name}</span>
            <span class="detail">${activityNarrative(e)}</span>
          </div>
          <span class="arrow">${prev} → ${e.new}</span>
        </div>`;
    })
    .join("");
  els.activityList.innerHTML = html;
  if (els.activityMeta) {
    els.activityMeta.textContent = entries.length
      ? `${entries.length} change${entries.length === 1 ? "" : "s"}`
      : "";
  }
}

if (els.activityList) {
  void refreshActivity();
  setInterval(() => void refreshActivity(), ACTIVITY_POLL_MS);
}
