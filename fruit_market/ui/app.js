const state = {
  catalog: [],
  active_item_id: null,
  orders: [],
  pending: {},
  restock: null,
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
  render();
  setStatus("Live", "online");
}

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
  els.restockPanel.className = `restock-panel ${restock.status}`;
  els.restockPanel.innerHTML = `
    <div class="row-main">
      <strong>${escapeHtml(restock.item_name)} restock</strong>
      <span>${dollars(restock.amount_cents)}</span>
    </div>
    <div class="row-meta">
      <span>${restock.qty} from ${escapeHtml(restock.supplier_name)}</span>
      <span>${statusLabel(restock.status)}</span>
    </div>
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
