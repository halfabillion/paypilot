const SUGGESTIONS = [
  ["Invoice £250", "Invoice buyer@example.com, Northwind Studio, £250 for website work"],
  ["Send invoice", "Send that invoice"],
  ["Check status", "What is the status of that invoice?"],
  ["List invoices", "List invoices"],
  ["Payment link", "Give me a reusable £19.99 pay link for the workshop"],
  ["Refund capture", "Refund capture CAPTURE-MOCK-DEMO for £19.99"],
  ["Record refund", "Record an external refund of £10 on INV2-MOCK-PAID-DEMO"],
  ["Bad amount", "Invoice buyer@example.com £-5 for nothing"],
];

const state = { sessionId: null, busy: false, mode: "mock" };

const transcript = document.querySelector("#transcript");
const activity = document.querySelector("#activity");
const banner = document.querySelector("#banner");
const disclosure = document.querySelector("#disclosure");
const provider = document.querySelector("#provider");
const form = document.querySelector("#composer");
const message = document.querySelector("#message");
const formError = document.querySelector("#form-error");
const send = document.querySelector("#send");

document.querySelector("#suggestions").append(
  ...SUGGESTIONS.map(([label, text]) => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = label;
    button.addEventListener("click", () => {
      message.value = text;
      message.focus();
    });
    return button;
  }),
);

document.querySelector("#new-session").addEventListener("click", () => startSession(true));
form.addEventListener("submit", onSubmit);

boot();

async function boot() {
  const existing = localStorage.getItem("paypilot.session");
  if (existing) {
    const data = await api(`/api/sessions/${existing}`);
    if (data && !data.error) {
      state.sessionId = existing;
      render(data);
      return;
    }
    localStorage.removeItem("paypilot.session");
  }
  await startSession(false);
}

async function startSession(force) {
  if (force) localStorage.removeItem("paypilot.session");
  const data = await api("/api/sessions", { method: "POST" });
  if (!data || data.error) {
    showError(data?.error?.message || "Could not start a session.");
    return;
  }
  state.sessionId = data.session_id;
  localStorage.setItem("paypilot.session", data.session_id);
  render(data);
}

async function onSubmit(event) {
  event.preventDefault();
  const text = message.value.trim();
  if (!text || state.busy) return;
  await act(`/api/sessions/${state.sessionId}/messages`, { text }, () => {
    message.value = "";
  });
}

async function act(path, body, after) {
  state.busy = true;
  send.disabled = true;
  formError.hidden = true;
  const data = await api(path, { method: "POST", json: body });
  state.busy = false;
  send.disabled = false;
  if (!data) {
    showError("The server did not respond.");
    return;
  }
  if (data.session) render(data.session);
  else if (data.session_id) render(data);
  if (data.error) showError(data.error.message);
  else if (after) after();
}

function render(data) {
  state.mode = data.mode || state.mode;
  banner.textContent = data.banner || "";
  banner.className = `banner ${state.mode === "sandbox" ? "sandbox" : "mock"}`;
  disclosure.textContent = data.disclosure || "";
  provider.textContent = data.intent_label ? `Intent provider: ${data.intent_label}.` : "";
  renderTranscript(data.messages || []);
  renderActivity(data.activity || []);
}

function renderTranscript(messages) {
  transcript.replaceChildren();
  if (!messages.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    const title = document.createElement("strong");
    title.textContent = "No PayPal calls yet.";
    const copy = document.createElement("p");
    copy.textContent = "Describe an invoice, a payment link, or a refund. A proposal stays here until you confirm it. Reads such as status run immediately and show up in the activity feed.";
    empty.append(title, copy);
    transcript.append(empty);
    return;
  }
  for (const item of messages) {
    const bubble = document.createElement("article");
    bubble.className = `bubble ${item.role}`;
    const who = document.createElement("p");
    who.className = "who";
    who.textContent = item.role === "user" ? "You" : "PayPilot";
    const body = document.createElement("p");
    body.textContent = item.body;
    bubble.append(who, body);
    if (item.proposal) bubble.append(proposalCard(item.proposal));
    transcript.append(bubble);
  }
  transcript.scrollTop = transcript.scrollHeight;
}

function proposalCard(proposal) {
  const card = document.createElement("div");
  card.className = "card";
  const superseded = proposal.status === "superseded";
  if (superseded) card.classList.add("superseded");
  const title = document.createElement("h3");
  title.textContent = proposal.action_label;
  const status = document.createElement("p");
  status.className = "mono";
  status.textContent = proposal.paypal_call;
  const list = document.createElement("ul");
  list.className = "preview";
  for (const row of proposal.preview || []) {
    const li = document.createElement("li");
    const label = document.createElement("span");
    label.textContent = row.label;
    const value = document.createElement("strong");
    value.textContent = row.value;
    li.append(label, value);
    list.append(li);
  }
  card.append(title, status, list);
  if (proposal.warnings?.length) {
    const warnings = document.createElement("ul");
    warnings.className = "warnings";
    for (const warning of proposal.warnings) {
      const li = document.createElement("li");
      li.textContent = warning;
      warnings.append(li);
    }
    card.append(warnings);
  }
  let nameInput = null;
  if (proposal.action === "create_invoice" && proposal.status === "pending" && !superseded) {
    const nameLabel = document.createElement("label");
    nameLabel.textContent = proposal.recipient_name_derived
      ? "Buyer name, taken from the email. Edit it before confirming."
      : "Buyer name";
    nameInput = document.createElement("input");
    nameInput.value = proposal.recipient_name || "";
    nameInput.maxLength = 120;
    nameInput.setAttribute("aria-label", "Buyer name");
    nameLabel.append(nameInput);
    card.append(nameLabel);
  }
  if (proposal.status === "pending" && proposal.requires_confirmation && !superseded) {
    const actions = document.createElement("div");
    actions.className = "actions";
    if (!proposal.confirm_blocked) {
      const confirm = document.createElement("button");
      confirm.className = "primary";
      confirm.type = "button";
      confirm.textContent = state.mode === "sandbox" ? "Confirm sandbox call" : "Confirm fixture call";
      confirm.addEventListener("click", () => {
        const body = nameInput ? { recipient_name: nameInput.value } : {};
        act(`/api/sessions/${state.sessionId}/proposals/${proposal.id}/confirm`, body);
      });
      actions.append(confirm);
    } else {
      const blocked = document.createElement("p");
      blocked.textContent = "Confirm is blocked. This proposal does not match your message.";
      card.append(blocked);
    }
    const reject = document.createElement("button");
    reject.className = "quiet";
    reject.type = "button";
    reject.textContent = "Reject";
    reject.addEventListener("click", () =>
      act(`/api/sessions/${state.sessionId}/proposals/${proposal.id}/reject`),
    );
    actions.append(reject);
    card.append(actions);
  } else if (superseded) {
    const note = document.createElement("p");
    note.textContent = "Superseded by a later request. Confirm is disabled.";
    card.append(note);
  } else {
    const note = document.createElement("p");
    note.textContent = statusLine(proposal);
    card.append(note);
  }
  return card;
}

function statusLine(proposal) {
  if (proposal.status === "executed") return "Confirmed. The result is in the message above and in the activity feed.";
  if (proposal.status === "rejected") return "Rejected. PayPal was not called.";
  if (proposal.status === "failed") return proposal.error || "The call failed.";
  if (proposal.status === "executing") return "Running.";
  return proposal.status;
}

function renderActivity(rows) {
  activity.replaceChildren();
  if (!rows.length) {
    const empty = document.createElement("li");
    empty.className = "empty";
    empty.textContent = "No PayPal calls yet. Confirming a proposal is what writes a row.";
    activity.append(empty);
    return;
  }
  for (const row of rows) {
    const item = document.createElement("li");
    item.className = "event";
    const header = document.createElement("header");
    const summary = document.createElement("strong");
    summary.className = "path";
    summary.textContent = row.summary;
    const badge = document.createElement("span");
    badge.className = `badge ${row.mock ? "mock" : "live"}`;
    badge.textContent = row.mock ? "MOCK" : "SANDBOX";
    header.append(summary, badge);
    const meta = document.createElement("p");
    const money = row.moves_money ? "Moves money in this mode." : "Does not move money.";
    meta.textContent = `${row.method} ${row.status_code} · ${money}`;
    item.append(header, meta);
    if (row.resource_id) {
      const id = document.createElement("p");
      id.className = "mono";
      id.textContent = row.resource_id;
      item.append(id);
    }
    const details = document.createElement("details");
    const summaryNode = document.createElement("summary");
    summaryNode.textContent = "Request and response";
    const pre = document.createElement("pre");
    pre.textContent = JSON.stringify(row.detail, null, 2);
    details.append(summaryNode, pre);
    item.append(details);
    activity.append(item);
  }
}

function showError(text) {
  formError.hidden = false;
  formError.textContent = text;
}

async function api(path, options = {}) {
  const headers = { Accept: "application/json" };
  const init = { method: options.method || "GET", headers };
  if (options.json) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(options.json);
  }
  try {
    const response = await fetch(path, init);
    return await response.json();
  } catch (_error) {
    return null;
  }
}
