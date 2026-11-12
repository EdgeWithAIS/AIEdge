const transcript = document.querySelector("#transcript");
const form = document.querySelector("#ask");
const questionEl = document.querySelector("#question");
const questionBox = document.querySelector("#question-box");
const modeEl = document.querySelector("#mode");
const userbar = document.querySelector("#userbar");
const names = new Map();
const DISCLAIMER_SEEN_KEY = "dan_disclaimer_seen_login";
let appConfig = null;
let busy = false;
let loginAt = null;
// Agent mode (an admin setting): Claude picks the tools (server: disaster_aid/agent.py) and sees recent turns.
const AGENT_HISTORY = 6;
let agentHistory = [];
let agentSituation = {};   // intake the agent recorded (update_situation); sent back each turn
function agentOn() { return Boolean(appConfig && appConfig.agent); }

const UI = {
  en: {
    locale: "en-US",
    askQuestion: (text) => text,
    reading: "Reading your question…",
    checking: (place, state) => `Checking declarations for ${place}, ${state}…`,
    open: "Open", closed: "Closed", na: "Not for families",
    types: { "Major Disaster": "Major Disaster", "Emergency": "Emergency", "Fire Management": "Fire Management" },
    declared: (when) => `declared ${when}`,
    link: "Declaration on FEMA.gov",
    warn: "This answer failed the fact check. Do not use it to decide a deadline. Confirm at DisasterAssistance.gov or 1-800-621-3362.",
    passed: "Fact check passed. Every date in this answer is in the declaration data.",
    failed: "Fact check failed.",
    refreshed: (when) => ` FEMA data refreshed ${when}.`,
    checkFailed: "The check failed.",
    unreachable: "Could not reach the app. Keep the terminal that started it open, then try again.",
    denialButton: "FEMA said no to me",
    suggestTitle: "Try asking",
    suggestions: [
      "Can families in St. Louis County, MO apply for FEMA help, and by when?",
      "What should I do next after a disaster?",
      "¿Pueden las familias de Harris County, TX pedir ayuda de FEMA?",
    ],
    matched: (address) => `Matched address: ${address}`,
    sourcesTitle: "Sources",
    updated: (when) => `updated ${when}`,
    inEnglish: "",
    kbPassed: "Source check passed. Every number and web address in this answer is in the official pages it cites.",
    kbWarn: "This answer did not pass the source check, so some details may be wrong. Confirm with the agency, at DisasterAssistance.gov, or at 1-800-621-3362.",
    kbSuggestion: "Will FEMA pay for a hotel after a disaster?",
    agentSteps: (n) => `How I checked (${n} ${n === 1 ? "step" : "steps"})`,
    agentPassed: "Fact check passed. Every date, number and web address in this answer came from FEMA data or the official pages it cites.",
    agentWarn: "This answer did not pass the fact check, so some details may be wrong. Confirm at DisasterAssistance.gov or 1-800-621-3362.",
    agentFixed: "The first draft failed the fact check and was rewritten.",
    tools: { check_fema_aid: "FEMA declarations", search_help_pages: "Official help pages",
             show_checklist: "Checklist", fact_check: "Fact check",
             update_situation: "Your situation", write_plan: "Plan" },
    planSuggestion: "Help me make a recovery plan. My home was damaged.",
    planSources: (ns) => `Source ${ns.map((n) => `[${n}]`).join(" ")}`,
  },
  es: {
    locale: "es-US",
    askQuestion: (text) => text,
    reading: "Leyendo su pregunta…",
    checking: (place, state) => `Revisando las declaraciones de ${place}, ${state}…`,
    open: "Abierta", closed: "Cerrada", na: "No es para familias",
    types: { "Major Disaster": "Desastre mayor", "Emergency": "Emergencia", "Fire Management": "Incendio" },
    declared: (when) => `declarado el ${when}`,
    link: "Declaración en FEMA.gov (en inglés)",
    warn: "Esta respuesta no pasó la revisión de datos. No la use para decidir una fecha límite. Confirme en DisasterAssistance.gov o al 1-800-621-3362.",
    passed: "Revisión de datos aprobada. Cada fecha de esta respuesta está en los datos de la declaración.",
    failed: "La revisión de datos falló.",
    refreshed: (when) => ` Datos de FEMA actualizados el ${when}.`,
    checkFailed: "La búsqueda falló.",
    unreachable: "No se pudo conectar con la aplicación. Deje abierta la terminal que la inició y vuelva a intentarlo.",
    denialButton: "FEMA me dijo que no",
    suggestTitle: "Try asking",
    suggestions: [],
    matched: (address) => `Dirección encontrada: ${address}`,
    sourcesTitle: "Fuentes",
    updated: (when) => `actualizada el ${when}`,
    inEnglish: "en inglés",
    kbPassed: "Revisión de fuentes aprobada. Cada número y dirección web de esta respuesta está en las páginas oficiales que cita.",
    kbWarn: "Esta respuesta no pasó la revisión de fuentes, así que algunos datos pueden estar mal. Confirme con la agencia, en DisasterAssistance.gov o al 1-800-621-3362.",
    agentSteps: (n) => `Cómo lo revisé (${n} ${n === 1 ? "paso" : "pasos"})`,
    agentPassed: "Revisión aprobada. Cada fecha, número y dirección web de esta respuesta viene de los datos de FEMA o de las páginas oficiales que cita.",
    agentWarn: "Esta respuesta no pasó la revisión, así que algunos datos pueden estar mal. Confirme en DisasterAssistance.gov o al 1-800-621-3362.",
    agentFixed: "El primer borrador no pasó la revisión y se volvió a escribir.",
    tools: { check_fema_aid: "Declaraciones de FEMA", search_help_pages: "Páginas oficiales de ayuda",
             show_checklist: "Lista", fact_check: "Revisión",
             update_situation: "Su situación", write_plan: "Plan" },
    planSuggestion: "Ayúdeme a hacer un plan de recuperación. Mi casa sufrió daños.",
    planSources: (ns) => `Fuente ${ns.map((n) => `[${n}]`).join(" ")}`,
  },
};

// Mirror server detect_lang: Spanish question (or "in Spanish") → Spanish UI chrome.
function detectLang(text) {
  const raw = (text || "").trim();
  if (!raw) return "en";
  if (/\b(in\s+spanish|en\s+espa[nñ]ol)\b/i.test(raw)) return "es";
  if (/[¿¡áéíóúüñÁÉÍÓÚÜÑ]/.test(raw)) return "es";
  if (/\b(español|espanol|familia[s]?|pueden|puedo|puede|ayuda|desastre|condado|parroquia|ciudad|código|codigo|postal|después|despues|qué|que|cómo|como|cuándo|cuando|dónde|donde|hasta|cuánto|cuanto|necesito|quiero|solicitar|reclamo|negaron|dijo)\b/i.test(raw)) {
    return "es";
  }
  return "en";
}

function ui(code) { return UI[code] || UI.en; }

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
  return response;
}

function syncDisclaimerForLogin(stamp) {
  loginAt = stamp == null || stamp === "" ? null : String(stamp);
  if (!questionBox) return;
  // Show the AI note until the first question of this login; hide after that.
  if (loginAt && sessionStorage.getItem(DISCLAIMER_SEEN_KEY) === loginAt) {
    questionBox.classList.add("is-ready");
  } else {
    questionBox.classList.remove("is-ready");
  }
}

function markDisclaimerSeen() {
  if (loginAt) sessionStorage.setItem(DISCLAIMER_SEEN_KEY, loginAt);
  questionBox?.classList.add("is-ready");
}

async function loadMe() {
  if (!userbar) return;
  try {
    const response = await api("/api/me");
    if (!response.ok) return;
    const me = await response.json();
    syncDisclaimerForLogin(me.login_at);
    userbar.replaceChildren();
    userbar.append(el("span", "who", `Signed in as ${me.email || me.name || "you"}`));
    userbar.append(el("span", "role " + (me.is_admin ? "admin" : "standard"), me.is_admin ? "Admin" : "Standard"));
    if (me.daily_limit != null) {
      const left = Math.max(me.daily_limit - me.used_today, 0);
      userbar.append(el("span", "quota", `${left} of ${me.daily_limit} questions left today`));
    } else {
      userbar.append(el("span", "quota", "No daily limit"));
    }
    if (me.is_admin) {
      const admin = el("a", null, "Admin");
      admin.href = "/admin";
      userbar.append(admin);
    }
    const out = el("a", null, "Sign out");
    out.href = "/logout";
    out.addEventListener("click", () => sessionStorage.removeItem(DISCLAIMER_SEEN_KEY));
    userbar.append(out);
  } catch (err) {
    // Server already rendered the bar; keep that if refresh fails.
  }
}

function fmtDate(iso, code = "en") {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || "");
  if (!match) return iso || "";
  const when = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
  return when.toLocaleDateString(ui(code).locale, { month: "long", day: "numeric", year: "numeric" });
}

function scrollChat() {
  const thread = document.querySelector(".chat-thread");
  if (thread) thread.scrollTop = thread.scrollHeight;
}

function addUser(message) {
  markDisclaimerSeen();
  const item = el("li", "user");
  item.append(el("p", null, message));
  transcript.append(item);
  scrollChat();
}

function addAssistant(node, code) {
  const item = el("li", "assistant");
  if (code) item.lang = code;
  item.append(node);
  transcript.append(item);
  scrollChat();
  return item;
}

function pill(status, t) {
  if (status === "open") return el("span", "pill open", t.open);
  if (status === "closed") return el("span", "pill closed", t.closed);
  return el("span", "pill na", t.na);
}

function renderCard(row, code) {
  const t = ui(code);
  const card = el("article", "decl");
  const top = el("div", "decl-top");
  top.append(el("span", "code", row.code || "Declaration"));
  top.append(pill(row.ia_status, t));
  card.append(top);
  const heading = row.heading || row.title;
  if (heading) card.append(el("p", "title", heading));
  const bits = [t.types[row.declaration_type] || row.declaration_type];
  if (code === "en") bits.push(row.incident_type);
  if (row.declaration_date) bits.push(t.declared(fmtDate(row.declaration_date, code)));
  const shown = bits.filter(Boolean);
  if (shown.length) card.append(el("p", "sub", shown.join(" · ")));
  if (row.status_line) card.append(el("p", "deadline", row.status_line));
  if (typeof row.url === "string" && row.url.startsWith("https://www.fema.gov/")) {
    const link = el("a", null, t.link);
    link.href = row.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    const line = el("p", "sub");
    line.append(link);
    card.append(line);
  }
  return card;
}

function renderSteps(block) {
  const box = el("details", "help");
  box.open = true;
  box.append(el("summary", null, block.title));
  const list = el("ul", "checklist");
  for (const item of block.items || []) {
    const label = el("label");
    const tick = el("input");
    tick.type = "checkbox";
    label.append(tick, el("span", null, item));
    const li = el("li");
    li.append(label);
    list.append(li);
  }
  box.append(list);
  return box;
}

function renderDenial(help, open = false) {
  const box = el("details", "help");
  box.open = open;
  box.append(el("summary", null, help.title));
  box.append(el("p", null, help.intro));
  const steps = el("ol");
  for (const step of help.steps || []) steps.append(el("li", null, step));
  box.append(steps);
  box.append(el("h3", null, help.reasons_title));
  const reasons = el("ul");
  for (const reason of help.reasons || []) {
    if (typeof reason === "string") {
      reasons.append(el("li", null, reason));
      continue;
    }
    const item = el("li", "denial-reason");
    item.append(el("strong", null, reason.name || ""));
    if (reason.means) item.append(el("p", null, reason.means));
    if (reason.send && reason.send.length) {
      const papers = el("ul");
      for (const paper of reason.send) papers.append(el("li", null, paper));
      item.append(papers);
    }
    reasons.append(item);
  }
  box.append(reasons);
  box.append(el("p", null, help.legal));
  return box;
}

function choiceButtons(items, onPick) {
  const choices = el("div", "choices");
  for (const item of items) {
    const button = el("button", null, item.label);
    button.type = "button";
    button.addEventListener("click", () => onPick(item));
    choices.append(button);
  }
  return choices;
}

function pickCounty(state, county) {
  const stateName = names.get(state) || state;
  const text = `Can families in ${county}, ${state} get FEMA help?`;
  questionEl.value = text;
  ask({ question: text, pending: ui("en").checking(county, stateName) });
}

function renderSources(sources, t, code) {
  const box = el("div", "sources");
  box.append(el("p", "sources-title", t.sourcesTitle));
  const list = el("ol");
  for (const source of sources) {
    const item = el("li");
    item.value = source.n;
    const url = String(source.url || "");
    if (url.startsWith("https://")) {
      const link = el("a", null, source.title || source.site);
      link.href = url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      item.append(link);
    } else {
      item.append(el("span", null, source.title || source.site));
    }
    const details = [source.site];
    if (source.published) details.push(t.updated(fmtDate(source.published, code)));
    if (t.inEnglish && source.lang === "en") details.push(t.inEnglish);
    item.append(el("span", "sub", " · " + details.filter(Boolean).join(" · ")));
    list.append(item);
  }
  box.append(list);
  return box;
}

// A general question answered from the knowledge base: text, sources, source check.
function renderKnowledge(data) {
  const code = data.lang === "es" ? "es" : "en";
  const t = ui(code);
  const wrap = el("div");
  if (!data.grounding_ok) {
    wrap.append(el("p", "warn", t.kbWarn));
    const list = el("ul", "problems");
    for (const problem of data.problems || []) list.append(el("li", null, problem));
    wrap.append(list);
  }
  wrap.append(el("p", "answer", data.text));
  if (data.sources && data.sources.length) {
    wrap.append(renderSources(data.sources, t, code));
    wrap.append(el("p", "meta", data.grounding_ok ? t.kbPassed : t.failed));
  }
  if (data.follow_up) wrap.append(el("p", "follow-up", data.follow_up));
  return wrap;
}

// Agent mode: the steps the agent took, then the answer, cards, sources and checklists.
function renderAgent(data) {
  const code = data.lang === "es" ? "es" : "en";
  const t = ui(code);
  const wrap = el("div", "agent-answer");
  const steps = data.steps || [];
  if (steps.length) {
    const box = el("details", "agent-steps");
    box.append(el("summary", null, t.agentSteps(steps.length)));
    const list = el("ol");
    for (const step of steps) {
      const item = el("li", step.error ? "step-error" : null);
      item.append(el("strong", null, (t.tools[step.tool] || step.tool) + ": "));
      item.append(el("span", null, step.note || ""));
      list.append(item);
    }
    box.append(list);
    wrap.append(box);
  }
  if (!data.grounding_ok) {
    wrap.append(el("p", "warn", t.agentWarn));
    const list = el("ul", "problems");
    for (const problem of data.problems || []) list.append(el("li", null, problem));
    wrap.append(list);
  }
  wrap.append(el("p", "answer", data.text));
  const choices = data.located && data.located.choices;
  if (choices && choices.length > 1) {
    wrap.append(choiceButtons(choices, (c) => pickCounty(c.state, c.county)));
  }
  if (data.ambiguous_areas && data.ambiguous_areas.length && data.state) {
    wrap.append(choiceButtons(data.ambiguous_areas.map((name) => ({ label: name })),
      (c) => pickCounty(data.state, c.label)));
  }
  if (data.plan) wrap.append(renderPlan(data.plan, t));
  for (const row of data.declarations || []) wrap.append(renderCard(row, code));
  if (data.sources && data.sources.length) wrap.append(renderSources(data.sources, t, code));
  if (data.next_steps) wrap.append(renderSteps(data.next_steps));
  if (data.denial_help) wrap.append(renderDenial(data.denial_help));
  let meta = data.grounding_ok ? t.agentPassed : t.failed;
  if (data.repaired) meta += " " + t.agentFixed;
  if (data.data_refreshed) meta += t.refreshed(fmtDate(data.data_refreshed, code));
  wrap.append(el("p", "meta", meta));
  return wrap;
}

// A recovery plan: numbered steps; deadlines were added by code from FEMA data.
function renderPlan(plan, t) {
  const box = el("section", "plan");
  box.append(el("h3", "plan-title", plan.title));
  const list = el("ol", "plan-steps");
  for (const step of plan.steps || []) {
    const item = el("li");
    item.append(el("p", "plan-step", step.step));
    if (step.deadline) item.append(el("p", "plan-deadline", `${step.deadline.code} · ${step.deadline.text}`));
    if (step.cites && step.cites.length) item.append(el("p", "sub", t.planSources(step.cites)));
    list.append(item);
  }
  box.append(list);
  return box;
}

function rememberAgentTurn(question, answer) {
  agentHistory.push({ role: "user", text: question }, { role: "assistant", text: answer });
  agentHistory = agentHistory.slice(-AGENT_HISTORY);
}

function renderAnswer(data) {
  if (data.kind === "agent") return renderAgent(data);
  if (data.kind === "knowledge") return renderKnowledge(data);
  const code = data.lang === "es" ? "es" : "en";
  const t = ui(code);
  const wrap = el("div");
  const located = data.located;
  if (located && data.located_text) {
    const line = located.matched_address
      ? `${data.located_text} ${t.matched(located.matched_address)}`
      : data.located_text;
    wrap.append(el("p", "located", line));
  }
  if (!data.grounding_ok) {
    wrap.append(el("p", "warn", t.warn));
    const list = el("ul", "problems");
    for (const problem of data.problems || []) list.append(el("li", null, problem));
    wrap.append(list);
  }
  wrap.append(el("p", "answer", data.text));
  if (located && located.choices && located.choices.length > 1) {
    wrap.append(choiceButtons(located.choices, (c) => pickCounty(c.state, c.county)));
  }
  if (data.ambiguous_areas && data.ambiguous_areas.length) {
    const state = data.state;
    wrap.append(choiceButtons(data.ambiguous_areas.map((name) => ({ label: name })),
      (c) => pickCounty(state, c.label)));
  }
  for (const row of data.declarations || []) wrap.append(renderCard(row, code));
  if (data.next_steps) wrap.append(renderSteps(data.next_steps));
  if (data.denial_help) wrap.append(renderDenial(data.denial_help));
  if (!data.need_location && (!located || !located.choices || located.choices.length < 2)) {
    const refreshed = data.data_refreshed ? t.refreshed(fmtDate(data.data_refreshed, code)) : "";
    wrap.append(el("p", "meta", (data.grounding_ok ? t.passed : t.failed) + refreshed));
  }
  return wrap;
}

function setBusy(on) {
  busy = on;
  questionEl.disabled = on;
  questionEl.placeholder = on
    ? "Checking…"
    : "Can families in Harris County, TX get FEMA help? Or try a ZIP like 77002.";
  for (const btn of document.querySelectorAll("#suggestions button")) btn.disabled = on;
}

async function ask(query) {
  if (busy) return;
  const text = (query.question || "").trim();
  if (!text) return;
  const code = detectLang(text);
  const t = ui(code);
  let pendingText = query.pending || t.reading;
  const low = text.toLowerCase();
  const useAgent = agentOn();
  if (!useAgent && /what should i do|what do i do next|qu[eé] debo hacer|despu[eé]s de un desastre/.test(low)) {
    questionEl.value = "";
    showSteps(text, code);
    return;
  }
  if (!useAgent && /said no|denied|appeal|dijo que no|me negaron/.test(low)) {
    questionEl.value = "";
    showDenialHelp(code);
    return;
  }
  questionEl.value = "";
  addUser(t.askQuestion(text));
  const pending = addAssistant(el("p", "pending", pendingText), code);
  setBusy(true);
  try {
    const response = await api(useAgent ? "/api/agent" : "/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(useAgent
        ? { question: text, history: agentHistory, situation: agentSituation }
        : { question: text }),
    });
    const data = await response.json();
    pending.remove();
    if (!response.ok) {
      addAssistant(el("p", "answer", data.error || t.checkFailed), code);
      return;
    }
    addAssistant(renderAnswer(data), data.lang === "es" ? "es" : "en");
    if (useAgent) {
      rememberAgentTurn(text, data.text);
      if (data.situation && typeof data.situation === "object") agentSituation = data.situation;
    }
    if (data.need_location) {
      questionEl.focus({ preventScroll: true });
    }
  } catch (err) {
    pending.remove();
    addAssistant(el("p", "answer", t.unreachable), code);
  } finally {
    setBusy(false);
    questionEl.focus({ preventScroll: true });
    loadMe();
  }
}

async function showSteps(question, code = "en") {
  if (busy) return;
  addUser(question);
  try {
    const response = await api("/api/steps?lang=" + code);
    const steps = await response.json();
    if (!response.ok) throw new Error(steps.error);
    addAssistant(renderSteps(steps), code);
  } catch (err) {
    addAssistant(el("p", "answer", ui(code).unreachable), code);
  }
}

function renderSuggestions() {
  const t = ui("en");
  document.querySelector("#suggest-title").textContent = t.suggestTitle;
  const actions = [
    () => {
      questionEl.value = t.suggestions[0];
      ask({ question: questionEl.value });
    },
    (text) => showSteps(text, "en"),
    () => {
      questionEl.value = t.suggestions[2];
      ask({ question: questionEl.value });
    },
  ];
  const host = document.querySelector("#suggestions");
  host.lang = "en";
  host.replaceChildren(...t.suggestions.map((text, i) => {
    const button = el("button", null, text);
    button.type = "button";
    button.disabled = busy;
    if (detectLang(text) === "es") button.lang = "es";
    button.addEventListener("click", () => actions[i](text));
    return button;
  }));
}

// Only shown when the server has a knowledge base for general questions.
function addKnowledgeSuggestion() {
  const text = ui("en").kbSuggestion;
  const button = el("button", null, text);
  button.type = "button";
  button.disabled = busy;
  button.addEventListener("click", () => {
    questionEl.value = text;
    ask({ question: text });
  });
  document.querySelector("#suggestions").append(button);
}

async function showDenialHelp(code = "en") {
  try {
    const response = await api("/api/help?lang=" + code);
    const help = await response.json();
    addUser(ui(code).denialButton);
    addAssistant(renderDenial(help, true), code);
  } catch (err) {
    addAssistant(el("p", "answer", ui(code).unreachable), code);
  }
}

function showMode(config) {
  modeEl.hidden = false;
  if (config.source === "fixtures") {
    modeEl.classList.add("sample");
    const when = config.as_of ? ` as of ${fmtDate(config.as_of)}` : "";
    modeEl.textContent = `Sample declarations${when}. This is not a live FEMA lookup.`;
  } else {
    modeEl.textContent = "Live FEMA declarations. Deadlines are calculated from that data and checked before they are shown.";
  }
  if (config.agent) {
    modeEl.textContent += " Answers can combine checks, follow up on your earlier messages, and include a recovery plan.";
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  ask({ question: questionEl.value });
});
questionBox?.addEventListener("click", (event) => {
  if (event.target.closest("a, button, input, textarea, summary, label")) return;
  questionEl.focus({ preventScroll: true });
});
questionEl.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    form.requestSubmit();
  }
});

document.querySelector("#sign-out")?.addEventListener("click", () => {
  sessionStorage.removeItem(DISCLAIMER_SEEN_KEY);
});

// In agent mode, offer a recovery plan as a starting point.
function showPlanSuggestion() {
  const list = document.querySelector("#suggestions");
  if (!list) return;
  list.querySelector(".plan-suggestion")?.remove();
  if (!agentOn()) return;
  const t = ui("en");
  const btn = el("button", "plan-suggestion", t.planSuggestion);
  btn.type = "button";
  btn.addEventListener("click", () => ask({ question: t.planSuggestion }));
  list.append(btn);
}

async function init() {
  renderSuggestions();
  await loadMe();
  questionEl.focus({ preventScroll: true });
  try {
    const response = await api("/api/config");
    const config = await response.json();
    for (const state of config.states || []) {
      names.set(state.code, state.name);
    }
    appConfig = config;
    showMode(config);
    if (config.knowledge) addKnowledgeSuggestion();
    showPlanSuggestion();
  } catch (err) {
    modeEl.hidden = false;
    modeEl.textContent = "The page could not load its settings. Reload the page, or sign in again.";
  }
}

init();
