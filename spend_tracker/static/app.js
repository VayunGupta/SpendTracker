const money = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
});

const statusEl = document.querySelector("#status");
const transactionsEl = document.querySelector("#transactions");
const transferReviewEl = document.querySelector("#transfer-review");
const reimbursementsEl = document.querySelector("#reimbursements");
const state = {
  preset: "all",
  start: "",
  end: "",
  page: 1,
  limit: 100,
  totalPages: 1,
};
const chartColors = ["#1f7a63", "#bb5a3a", "#315f9f", "#9a6b13", "#6b5aa6", "#2f7f9f", "#7a3f62", "#59723a"];

function setStatus(message) {
  statusEl.textContent = message || "";
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(body.detail || `Request failed: ${response.status}`);
  }
  return body;
}

async function refreshDashboard() {
  const params = new URLSearchParams({
    preset: state.preset,
    page: String(state.page),
    limit: String(state.limit),
  });
  if (state.start) params.set("start", state.start);
  if (state.end) params.set("end", state.end);
  const data = await api(`/api/dashboard?${params.toString()}`);
  state.totalPages = data.summary.total_pages;
  document.querySelector("#real-spend").textContent = money.format(data.summary.real_spend);
  document.querySelector("#raw-bank-spend").textContent = money.format(data.summary.raw_bank_spend);
  document.querySelector("#bank-count").textContent = data.summary.bank_transactions;
  document.querySelector("#splitwise-count").textContent = data.summary.splitwise_expenses;
  document.querySelector("#row-count").textContent =
    `Showing ${data.summary.displayed_rows} of ${data.summary.reconciled_rows}`;
  document.querySelector("#page-count").textContent =
    `Page ${data.summary.page} of ${data.summary.total_pages}`;
  document.querySelector("#prev-page").disabled = data.summary.page <= 1;
  document.querySelector("#next-page").disabled = data.summary.page >= data.summary.total_pages;
  renderAnalytics(data.analytics);

  transactionsEl.innerHTML = "";
  if (data.transactions.length === 0) {
    transactionsEl.innerHTML = '<tr><td class="empty" colspan="7">No reconciled spend yet.</td></tr>';
    return;
  }

  for (const row of data.transactions) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${row.date}</td>
      <td>${escapeHtml(row.description)}</td>
      <td><span class="source">${escapeHtml(row.source)}</span></td>
      <td>${escapeHtml(row.category)}</td>
      <td class="amount">${money.format(row.original_amount)}</td>
      <td class="amount">${money.format(row.adjusted_amount)}</td>
      <td>${escapeHtml(row.note || "")}</td>
    `;
    transactionsEl.appendChild(tr);
  }
}

function renderAnalytics(analytics) {
  renderCategoryBreakdown(analytics.categories || []);
  renderSourceBreakdown(analytics.sources || []);
  renderMonthlyTrend(analytics.monthly || []);
}

function renderCategoryBreakdown(categories) {
  const list = document.querySelector("#category-list");
  const donut = document.querySelector("#category-donut");
  const total = categories.reduce((sum, item) => sum + Math.abs(item.amount), 0);
  document.querySelector("#category-total").textContent = total ? money.format(total) : "";
  list.innerHTML = "";
  if (!total) {
    donut.style.background = "#edf0ec";
    list.innerHTML = '<div class="empty-small">No category data</div>';
    return;
  }

  let cursor = 0;
  const segments = categories.slice(0, 8).map((item, index) => {
    const start = cursor;
    const size = (Math.abs(item.amount) / total) * 100;
    cursor += size;
    return `${chartColors[index % chartColors.length]} ${start}% ${cursor}%`;
  });
  donut.style.background = `conic-gradient(${segments.join(", ")})`;

  for (const [index, item] of categories.slice(0, 8).entries()) {
    const percent = Math.round((Math.abs(item.amount) / total) * 100);
    const row = document.createElement("div");
    row.className = "category-row";
    row.innerHTML = `
      <span class="swatch" style="background:${chartColors[index % chartColors.length]}"></span>
      <span class="category-name">${escapeHtml(item.label)}</span>
      <span class="category-amount">${money.format(item.amount)}</span>
      <span class="category-bar"><i style="width:${percent}%"></i></span>
    `;
    list.appendChild(row);
  }
}

function renderSourceBreakdown(sources) {
  const list = document.querySelector("#source-list");
  const total = sources.reduce((sum, item) => sum + Math.abs(item.amount), 0);
  list.innerHTML = "";
  if (!total) {
    list.innerHTML = '<div class="empty-small">No source data</div>';
    return;
  }
  for (const [index, item] of sources.entries()) {
    const percent = Math.round((Math.abs(item.amount) / total) * 100);
    const row = document.createElement("div");
    row.className = "source-row";
    row.innerHTML = `
      <div>
        <span class="source-dot" style="background:${chartColors[index % chartColors.length]}"></span>
        <strong>${escapeHtml(item.label)}</strong>
      </div>
      <span>${money.format(item.amount)}</span>
      <i><b style="width:${percent}%"></b></i>
    `;
    list.appendChild(row);
  }
}

function renderMonthlyTrend(months) {
  const chart = document.querySelector("#monthly-chart");
  chart.innerHTML = "";
  if (months.length === 0) {
    chart.innerHTML = '<div class="empty-small">No trend data</div>';
    return;
  }
  const max = Math.max(...months.map((item) => Math.abs(item.amount)), 1);
  for (const item of months.slice(-8)) {
    const height = Math.max(8, Math.round((Math.abs(item.amount) / max) * 130));
    const bar = document.createElement("div");
    bar.className = "month-bar";
    bar.innerHTML = `
      <span class="bar-value">${money.format(item.amount)}</span>
      <i style="height:${height}px"></i>
      <span>${escapeHtml(item.month.slice(5))}</span>
    `;
    chart.appendChild(bar);
  }
}

async function refreshTransferReview() {
  const data = await api("/api/transfer-review");
  document.querySelector("#transfer-count").textContent = `${data.transfers.length} excluded transfers`;
  transferReviewEl.innerHTML = "";
  if (data.transfers.length === 0) {
    transferReviewEl.innerHTML = '<tr><td class="empty" colspan="6">No excluded transfers to review.</td></tr>';
    return;
  }

  for (const transfer of data.transfers) {
    const tr = document.createElement("tr");
    tr.dataset.transferId = transfer.id;
    tr.dataset.manualId = transfer.manual?.id || "";
    tr.innerHTML = `
      <td>${transfer.date}</td>
      <td>${escapeHtml(transfer.description)}</td>
      <td class="amount">${money.format(transfer.amount)}</td>
      <td><input class="manual-amount" type="number" min="0" step="0.01" value="${transfer.manual?.amount ?? transfer.amount}" /></td>
      <td><input class="manual-description" type="text" value="${escapeHtml(transfer.manual?.description || "")}" placeholder="Description" /></td>
      <td class="transfer-actions">
        <button class="save-manual" type="button">${transfer.manual ? "Update" : "Record"}</button>
        ${transfer.manual ? '<button class="clear-manual" type="button">Clear</button>' : ""}
      </td>
    `;
    transferReviewEl.appendChild(tr);
  }
}

async function refreshReimbursements() {
  const data = await api("/api/reimbursements");
  document.querySelector("#reimbursement-count").textContent = `${data.reimbursements.length} credits`;
  reimbursementsEl.innerHTML = "";
  if (data.reimbursements.length === 0) {
    reimbursementsEl.innerHTML = '<tr><td class="empty" colspan="3">No incoming reimbursements found.</td></tr>';
    return;
  }
  for (const reimbursement of data.reimbursements) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${reimbursement.date}</td>
      <td>${escapeHtml(reimbursement.description)}</td>
      <td class="amount">${money.format(reimbursement.amount)}</td>
    `;
    reimbursementsEl.appendChild(tr);
  }
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function withButton(button, label, action) {
  const original = button.textContent;
  button.disabled = true;
  button.textContent = label;
  setStatus("");
  try {
    await action();
    await refreshDashboard();
    await refreshTransferReview();
    await refreshReimbursements();
  } catch (error) {
    setStatus(error.message);
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

document.querySelector("#connect-plaid").addEventListener("click", async (event) => {
  await withButton(event.currentTarget, "Opening...", async () => {
    const token = await api("/api/plaid/link-token", { method: "POST" });
    const handler = Plaid.create({
      token: token.link_token,
      onSuccess: async (publicToken, metadata) => {
        await api("/api/plaid/exchange-public-token", {
          method: "POST",
          body: JSON.stringify({
            public_token: publicToken,
            institution_name: metadata?.institution?.name || null,
          }),
        });
        await refreshDashboard();
      },
      onExit: (err) => {
        if (err) setStatus(err.display_message || err.error_message || "Plaid Link exited");
      },
    });
    handler.open();
  });
});

document.querySelector("#sync-plaid").addEventListener("click", async (event) => {
  await withButton(event.currentTarget, "Syncing...", async () => {
    await api("/api/sync/plaid", { method: "POST" });
  });
});

document.querySelector("#sync-splitwise").addEventListener("click", async (event) => {
  await withButton(event.currentTarget, "Syncing...", async () => {
    await api("/api/sync/splitwise", { method: "POST" });
  });
});

document.querySelector("#filters").addEventListener("submit", async (event) => {
  event.preventDefault();
  await applyFilters();
});

async function applyFilters() {
  state.preset = document.querySelector("#preset").value;
  state.start = document.querySelector("#start-date").value;
  state.end = document.querySelector("#end-date").value;
  state.page = 1;
  await refreshDashboard();
}

document.querySelector("#preset").addEventListener("change", async (event) => {
  const custom = event.currentTarget.value === "custom";
  document.querySelector("#start-date").disabled = !custom;
  document.querySelector("#end-date").disabled = !custom;
  if (!custom) {
    document.querySelector("#start-date").value = "";
    document.querySelector("#end-date").value = "";
  }
  await applyFilters();
});

document.querySelector("#start-date").addEventListener("change", applyFilters);
document.querySelector("#end-date").addEventListener("change", applyFilters);

document.querySelector("#prev-page").addEventListener("click", async () => {
  if (state.page <= 1) return;
  state.page -= 1;
  await refreshDashboard();
});

document.querySelector("#next-page").addEventListener("click", async () => {
  if (state.page >= state.totalPages) return;
  state.page += 1;
  await refreshDashboard();
});

transferReviewEl.addEventListener("click", async (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  const row = button.closest("tr");
  const bankTransactionId = Number(row.dataset.transferId);
  const manualId = Number(row.dataset.manualId);

  if (button.classList.contains("save-manual")) {
    await withButton(button, "Saving...", async () => {
      const amount = Number(row.querySelector(".manual-amount").value);
      const description = row.querySelector(".manual-description").value.trim();
      if (!description) throw new Error("Add a description before recording manual spend");
      await api("/api/manual-spend", {
        method: "POST",
        body: JSON.stringify({
          bank_transaction_id: bankTransactionId,
          amount,
          description,
          note: "Manual actual spend from excluded transfer",
        }),
      });
    });
  }

  if (button.classList.contains("clear-manual") && manualId) {
    await withButton(button, "Clearing...", async () => {
      await api(`/api/manual-spend/${manualId}`, { method: "DELETE" });
    });
  }
});

document.querySelector("#start-date").disabled = true;
document.querySelector("#end-date").disabled = true;
Promise.all([refreshDashboard(), refreshTransferReview(), refreshReimbursements()]).catch((error) => setStatus(error.message));
