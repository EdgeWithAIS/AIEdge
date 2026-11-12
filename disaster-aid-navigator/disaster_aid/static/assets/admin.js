const $ = (id) => document.getElementById(id);
const money = (n) => "$" + (Number(n) || 0).toFixed(4);
const count = (n) => (Number(n) || 0).toLocaleString("en-US");

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

async function api(url, options) {
  const response = await fetch(url, { credentials: "same-origin", ...options });
  if (response.status === 401) {
    window.location.assign("/login");
    throw new Error("Signed out");
  }
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Request failed.");
  return data;
}

function row(cells) {
  const tr = el("tr");
  for (const [text, num] of cells) tr.append(el("td", num ? "num" : null, text));
  return tr;
}

function fill(tbody, rows, empty, columns) {
  tbody.replaceChildren();
  if (!rows.length) {
    const td = el("td", null, empty);
    td.colSpan = columns;
    const tr = el("tr");
    tr.append(td);
    tbody.append(tr);
    return;
  }
  for (const r of rows) tbody.append(r);
}

function showUsage(data) {
  $("days").textContent = data.days;
  const t = data.totals;
  const tiles = [
    ["Questions", count(t.questions)],
    ["Claude answers", count(t.claude_answers)],
    ["Errors", count(t.errors)],
    ["Tokens", count(t.input_tokens + t.output_tokens)],
    ["Estimated cost", money(t.cost_usd)],
  ];
  $("tiles").replaceChildren(...tiles.map(([label, value]) => {
    const tile = el("div", "tile");
    tile.append(el("span", "label", label), el("span", "value", value));
    return tile;
  }));
  $("cost-note").textContent = "Cost is estimated from the token prices in Settings, at the time of each question.";
  fill($("by-day"), data.by_day.map((d) => row([
    [d.day], [count(d.questions), 1], [count(d.claude_answers), 1], [count(d.errors), 1],
    [count(d.input_tokens), 1], [count(d.output_tokens), 1], [money(d.cost_usd), 1],
  ])), "No questions yet.", 7);
  fill($("users"), data.top_users.map((u) => row([
    [u.email || u.sub], [u.role], [count(u.questions), 1], [count(u.tokens), 1], [money(u.cost_usd), 1],
  ])), "No users yet.", 5);
  fill($("errors"), data.recent_errors.map((e) => row([
    [e.at.replace("T", " ").slice(0, 16)], [e.email || ""], [e.kind], [e.error || ""],
  ])), "No errors.", 4);
}

function showSettings(data) {
  const s = data.settings;
  $("claude_enabled").checked = s.claude_enabled;
  $("agent_enabled").checked = s.agent_enabled;
  $("model").value = s.model;
  $("daily_limit").value = s.daily_limit;
  $("price_input_per_mtok").value = s.price_input_per_mtok;
  $("price_output_per_mtok").value = s.price_output_per_mtok;
  $("claude-note").textContent = data.claude_available
    ? "When this is off, answers use the offline template."
    : "Claude is not available on this server (it was started without --claude or without an API key), so answers use the offline template.";
}

function setStatus(text, good) {
  const line = $("save-status");
  line.textContent = text;
  line.className = "status-line " + (good ? "ok" : "bad");
}

$("settings").addEventListener("submit", async (event) => {
  event.preventDefault();
  const body = {
    claude_enabled: $("claude_enabled").checked,
    agent_enabled: $("agent_enabled").checked,
    model: $("model").value.trim(),
    daily_limit: Number.parseInt($("daily_limit").value, 10),
    price_input_per_mtok: Number($("price_input_per_mtok").value),
    price_output_per_mtok: Number($("price_output_per_mtok").value),
  };
  try {
    const data = await api("/api/admin/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    showSettings(data);
    setStatus("Saved.", true);
  } catch (err) {
    setStatus(err.message, false);
  }
});

async function init() {
  try {
    const data = await api("/api/admin/usage?days=14");
    showUsage(data);
    showSettings(data);
  } catch (err) {
    setStatus(err.message, false);
  }
}

init();
