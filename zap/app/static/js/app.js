const boardEl = document.getElementById("board");
const alertSound = document.getElementById("alert-sound");
const user = JSON.parse(document.body.dataset.user || "{}");
const apiBase = document.body.dataset.apiBase || "";
const feedbackEl = document.getElementById("client-feedback");
const whatsappSendLog = document.getElementById("whatsapp-send-log");
let currentTicketId = null;
let lastMessagePollAt = new Date().toISOString();
let notificationAudioContext = null;
let notificationAudioUnlocked = false;
let availableLabels = [];
let availableDepartments = [];
let availableStates = [];
let quickReplyShortcutMap = new Map();
let quickReplyEntries = [];
let quickReplyUsageOrder = [];
const quickReplySuggestionsEl = document.getElementById("quick-reply-suggestions");
const quickReplyUsageStorageKey = "zap.quick_reply_usage";
const sidebarToggle = document.querySelector("[data-sidebar-toggle]");
const sidebarStorageKey = "zap.sidebar.collapsed";

function setSidebarCollapsed(collapsed) {
  document.body.classList.toggle("sidebar-collapsed", collapsed);
  if (sidebarToggle) {
    sidebarToggle.setAttribute("aria-expanded", String(!collapsed));
    sidebarToggle.title = collapsed ? "Expandir menu" : "Minimizar menu";
  }
  try {
    localStorage.setItem(sidebarStorageKey, collapsed ? "1" : "0");
  } catch {
    // Ignore storage failures.
  }
}

try {
  setSidebarCollapsed(localStorage.getItem(sidebarStorageKey) === "1");
} catch {
  setSidebarCollapsed(false);
}

sidebarToggle?.addEventListener("click", () => {
  setSidebarCollapsed(!document.body.classList.contains("sidebar-collapsed"));
});

function apiUrl(path) {
  if (/^https?:\/\//i.test(path)) return path;
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  if (apiBase && normalizedPath.startsWith(`${apiBase}/`)) return normalizedPath;
  if (apiBase && normalizedPath === apiBase) return normalizedPath;
  return `${apiBase}${normalizedPath}`;
}

let feedbackTimer = null;
function showFeedback(message, type = "error") {
  if (!feedbackEl) {
    if (type === "error") console.error(message);
    return;
  }
  feedbackEl.innerHTML = `<div class="flash ${type}">${escapeHtml(message)}</div>`;
  window.clearTimeout(feedbackTimer);
  feedbackTimer = window.setTimeout(() => {
    feedbackEl.innerHTML = "";
  }, type === "error" ? 7000 : 4000);
}

function sleep(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function escapeRegExp(value) {
  return String(value).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function loadQuickReplyUsageOrder() {
  try {
    const parsed = JSON.parse(localStorage.getItem(quickReplyUsageStorageKey) || "[]");
    if (Array.isArray(parsed)) {
      return parsed.map((item) => String(item || "").trim().toLowerCase()).filter(Boolean);
    }
  } catch {
    // Ignore storage failures.
  }
  return [];
}

function saveQuickReplyUsageOrder() {
  try {
    localStorage.setItem(quickReplyUsageStorageKey, JSON.stringify(quickReplyUsageOrder));
  } catch {
    // Ignore storage failures.
  }
}

function recordQuickReplyUsage(shortcut) {
  const normalized = String(shortcut || "").trim().toLowerCase();
  if (!normalized) return;
  quickReplyUsageOrder = [normalized, ...quickReplyUsageOrder.filter((item) => item !== normalized)].slice(0, 20);
  saveQuickReplyUsageOrder();
}

function sortQuickReplyEntries(entries) {
  const usageRank = new Map(quickReplyUsageOrder.map((shortcut, index) => [shortcut, index]));
  return [...entries].sort((a, b) => {
    const rankA = usageRank.has(a.shortcut) ? usageRank.get(a.shortcut) : Number.POSITIVE_INFINITY;
    const rankB = usageRank.has(b.shortcut) ? usageRank.get(b.shortcut) : Number.POSITIVE_INFINITY;
    if (rankA !== rankB) return rankA - rankB;
    return a.shortcut.localeCompare(b.shortcut);
  });
}

function previewQuickReplyBody(body, maxLength = 48) {
  const text = String(body || "").replace(/\s+/g, " ").trim();
  if (!text) return "";
  if (text.length <= maxLength) return text;
  return `${text.slice(0, maxLength - 3)}...`;
}

function refreshQuickReplyShortcuts() {
  quickReplyEntries = sortQuickReplyEntries(Array.from(document.querySelectorAll(".quick-reply")).flatMap((button) => {
    const shortcut = String(button.dataset.shortcut || "").trim().toLowerCase();
    const body = String(button.dataset.body || "").trim();
    const title = String(button.dataset.title || button.textContent || "").trim();
    return shortcut && body ? [{ shortcut, body, title }] : [];
  }));
  quickReplyShortcutMap = new Map(quickReplyEntries.map((item) => [item.shortcut, item.body]));
  updateQuickReplySuggestions();
}

function getMessageTextarea() {
  return messageForm?.querySelector('textarea[name="content"]') || null;
}

function insertTextAtCursor(textarea, text) {
  if (!textarea) return;
  const currentValue = textarea.value;
  const start = textarea.selectionStart ?? currentValue.length;
  const end = textarea.selectionEnd ?? currentValue.length;
  const nextValue = `${currentValue.slice(0, start)}${text}${currentValue.slice(end)}`;
  textarea.value = nextValue;
  const cursor = start + text.length;
  textarea.setSelectionRange(cursor, cursor);
  textarea.focus();
}

function replaceShortcutToken(textarea, shortcut, replacement, trailing = " ") {
  if (!textarea || !shortcut || !replacement) return false;
  const value = textarea.value;
  const caret = textarea.selectionStart ?? value.length;
  const before = value.slice(0, caret);
  const after = value.slice(caret);
  const pattern = new RegExp(`(^|\\s)(${escapeRegExp(shortcut)})$`, "i");
  const match = before.match(pattern);
  if (!match) return false;
  const prefix = match[1] || "";
  const start = before.length - match[2].length;
  const nextBefore = `${before.slice(0, start)}${replacement}${trailing}`;
  textarea.value = `${nextBefore}${after}`;
  const cursor = nextBefore.length;
  textarea.setSelectionRange(cursor, cursor);
  return true;
}

function expandQuickReplyDraft(text) {
  let draft = String(text || "");
  quickReplyShortcutMap.forEach((body, shortcut) => {
    const escapedShortcut = escapeRegExp(shortcut);
    const hasMatch = new RegExp(`(^|\\s)(${escapedShortcut})(?=\\s|$)`, "i").test(draft);
    const pattern = new RegExp(`(^|\\s)(${escapedShortcut})(?=\\s|$)`, "gi");
    if (hasMatch) {
      recordQuickReplyUsage(shortcut);
    }
    draft = draft.replace(pattern, (match, prefix, token) => `${prefix}${body}`);
  });
  return draft;
}

function insertQuickReply(body, shortcut = "") {
  const textarea = getMessageTextarea();
  if (!textarea) return;
  recordQuickReplyUsage(shortcut);
  insertTextAtCursor(textarea, body);
  textarea.dispatchEvent(new Event("input", { bubbles: true }));
}

function getActiveQuickReplyQuery(textarea) {
  if (!textarea) return "";
  const value = textarea.value;
  const caret = textarea.selectionStart ?? value.length;
  const before = value.slice(0, caret);
  const match = before.match(/(?:^|\s)(\/[^\s]*)$/i);
  return match ? match[1].toLowerCase() : "";
}

function getQuickReplyMatches(query) {
  const normalizedQuery = String(query || "").trim().toLowerCase();
  if (!normalizedQuery) return [];
  const matches = quickReplyEntries.filter((item) => {
    if (item.shortcut.startsWith(normalizedQuery)) return true;
    const searchHaystack = `${item.shortcut} ${item.title} ${item.body}`.toLowerCase();
    return searchHaystack.includes(normalizedQuery.slice(1));
  });
  return sortQuickReplyEntries(matches);
}

function renderQuickReplySuggestions(matches) {
  if (!quickReplySuggestionsEl) return;
  quickReplySuggestionsEl.innerHTML = "";
  if (!matches.length) {
    quickReplySuggestionsEl.classList.remove("open");
    return;
  }
  matches.slice(0, 5).forEach((reply) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "quick-suggestion";
    button.dataset.shortcut = reply.shortcut;
    button.dataset.body = reply.body;
    button.dataset.title = reply.title;
    button.innerHTML = `
      <strong>${escapeHtml(reply.shortcut)}</strong>
      <span>${escapeHtml(previewQuickReplyBody(reply.body || reply.title || ""))}</span>
    `;
    quickReplySuggestionsEl.appendChild(button);
  });
  quickReplySuggestionsEl.classList.add("open");
}

function updateQuickReplySuggestions() {
  const textarea = getMessageTextarea();
  const query = getActiveQuickReplyQuery(textarea);
  renderQuickReplySuggestions(query ? getQuickReplyMatches(query) : []);
}

function expandActiveQuickReply(triggerKey = "") {
  const textarea = getMessageTextarea();
  if (!textarea) return false;
  const token = getActiveQuickReplyQuery(textarea);
  if (!token) return false;
  const body = quickReplyShortcutMap.get(token);
  if (!body) return false;
  const trailing = triggerKey === "Tab" || triggerKey === "Enter" || triggerKey === ";" ? " " : "";
  recordQuickReplyUsage(token);
  return replaceShortcutToken(textarea, token, body, trailing);
}

function insertSuggestion(reply) {
  const textarea = getMessageTextarea();
  if (!textarea || !reply) return;
  const query = getActiveQuickReplyQuery(textarea);
  if (query) {
    replaceShortcutToken(textarea, query, reply.body, " ");
  } else {
    insertTextAtCursor(textarea, reply.body);
  }
  textarea.dispatchEvent(new Event("input", { bubbles: true }));
}

function appendWhatsappLog(message, level = "ok") {
  if (!whatsappSendLog) {
    console[level === "error" ? "error" : "info"](message);
    return;
  }
  whatsappSendLog.classList.remove("empty");
  const entry = document.createElement("div");
  entry.className = `message-log-entry ${level}`;
  entry.innerHTML = `
    <strong>${escapeHtml(message)}</strong>
    <small>${escapeHtml(new Date().toLocaleString())}</small>
  `;
  whatsappSendLog.prepend(entry);
}

function unlockNotificationSound() {
  if (notificationAudioUnlocked) return;
  const AudioContextClass = window.AudioContext || window.webkitAudioContext;
  if (!AudioContextClass) {
    notificationAudioUnlocked = true;
    return;
  }
  if (!notificationAudioContext) {
    notificationAudioContext = new AudioContextClass();
  }
  notificationAudioContext.resume?.().catch(() => {});
  notificationAudioUnlocked = true;
}

function playNotificationSound() {
  const AudioContextClass = window.AudioContext || window.webkitAudioContext;
  if (!notificationAudioContext && AudioContextClass) {
    notificationAudioContext = new AudioContextClass();
  }
  if (notificationAudioContext) {
    if (notificationAudioContext.state === "suspended") {
      notificationAudioContext.resume().catch(() => {});
    }
    const ctx = notificationAudioContext;
    const oscillator = ctx.createOscillator();
    const gain = ctx.createGain();
    oscillator.type = "sine";
    oscillator.frequency.value = 880;
    gain.gain.value = 0.0001;
    oscillator.connect(gain);
    gain.connect(ctx.destination);
    const now = ctx.currentTime;
    gain.gain.setValueAtTime(0.0001, now);
    gain.gain.exponentialRampToValueAtTime(0.18, now + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.18);
    oscillator.start(now);
    oscillator.stop(now + 0.2);
    oscillator.onended = () => {
      oscillator.disconnect();
      gain.disconnect();
    };
    return;
  }
  if (alertSound) {
    alertSound.currentTime = 0;
    alertSound.play().catch(() => {});
  }
}

document.addEventListener("pointerdown", unlockNotificationSound, { capture: true, once: false });
document.addEventListener("keydown", unlockNotificationSound, { capture: true, once: false });

function csrfJson(url, method, payload) {
  return fetchJson(url, {
    method,
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify(payload),
  }, 0);
}

async function fetchJson(url, options = {}, retries = 0) {
  const response = await fetch(apiUrl(url), options);
  const raw = await response.text();
  const contentType = response.headers.get("content-type") || "";
  let data = {};
  if (contentType.includes("application/json") || /^[\s\r\n]*[\[{]/.test(raw)) {
    try {
      data = JSON.parse(raw || "{}");
    } catch {
      data = {};
    }
  }
  if (response.redirected && /\/login(?:[/?#]|$)/i.test(response.url || "")) {
    throw new Error("Sua sessao expirou. Entre novamente para continuar.");
  }
  if (!response.ok) {
    if (retries > 0 && [502, 503, 504].includes(response.status)) {
      await sleep(350);
      return fetchJson(url, options, retries - 1);
    }
    const message = data.description || data.error || data.message || raw.trim().replace(/<[^>]+>/g, "").trim();
    if (message) {
      throw new Error(message);
    }
    throw new Error(`Falha na requisicao HTTP ${response.status}`);
  }
  return data;
}

function openModal(id) {
  const el = document.getElementById(id);
  if (el) el.classList.add("open");
}

function closeModal(modal) {
  modal.classList.remove("open");
}

document.addEventListener("click", (event) => {
  const openBtn = event.target.closest("[data-open-modal]");
  if (openBtn) {
    if (openBtn.dataset.openModal === "ticket-modal") {
      renderLabelPicker(document.getElementById("ticket-label-picker"), availableLabels, []);
      populateDepartmentSelects(availableDepartments);
    }
    openModal(openBtn.dataset.openModal);
  }

  const closeBtn = event.target.closest("[data-close-modal]");
  if (closeBtn) {
    closeModal(closeBtn.closest(".modal"));
  }

  const quickReply = event.target.closest(".quick-reply");
  if (quickReply) {
    insertQuickReply(quickReply.dataset.body || "", quickReply.dataset.shortcut || "");
    const modal = quickReply.closest(".modal");
    if (modal) closeModal(modal);
    showFeedback("Atalho inserido na mensagem.", "success");
  }

  const quickSuggestion = event.target.closest(".quick-suggestion");
  if (quickSuggestion) {
    recordQuickReplyUsage(quickSuggestion.dataset.shortcut || "");
    insertSuggestion({
      shortcut: String(quickSuggestion.dataset.shortcut || "").trim(),
      body: String(quickSuggestion.dataset.body || "").trim(),
      title: String(quickSuggestion.dataset.title || "").trim(),
    });
    updateQuickReplySuggestions();
    showFeedback("Atalho expandido na mensagem.", "success");
  }
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") document.querySelectorAll(".modal.open").forEach(closeModal);
  if (document.activeElement === getMessageTextarea()) {
    if ((event.key === "Tab" || event.key === "Enter" || event.key === ";" || (event.ctrlKey && event.key === " ")) && expandActiveQuickReply(event.key)) {
      event.preventDefault();
      showFeedback("Atalho expandido na mensagem.", "success");
    }
  }
});

async function loadReferenceData() {
  const data = await fetchJson("/api/dashboard", { credentials: "same-origin" }, 2);
  availableStates = data.states || [];
  availableLabels = data.labels || [];
  availableDepartments = data.departments || [];
  quickReplyUsageOrder = loadQuickReplyUsageOrder();
  refreshQuickReplyShortcuts();
  renderLabelPicker(document.getElementById("ticket-label-picker"), availableLabels, []);
  populateDepartmentSelects(availableDepartments);
  if (boardEl) renderBoard(data.states || [], data.tickets || []);
}

function populateDepartmentSelects(departments, selectedValue = "") {
  const selects = document.querySelectorAll("select[data-department-select]");
  selects.forEach((select) => {
    const current = select.dataset.selectedDepartmentId || selectedValue || select.value || user.department_id || "";
    select.innerHTML = '<option value="">Departamento</option>';
    departments.forEach((department) => {
      const option = document.createElement("option");
      option.value = String(department.id);
      option.textContent = department.name;
      if (String(current) === String(department.id)) option.selected = true;
      select.appendChild(option);
    });
  });
}

function formatDateTimeLocal(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const offset = date.getTimezoneOffset() * 60000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

function getTicketStateIndex(stateId) {
  return availableStates.findIndex((state) => String(state.id) === String(stateId));
}

function getAdjacentStateId(stateId, delta) {
  const index = getTicketStateIndex(stateId);
  if (index < 0) return null;
  const target = availableStates[index + delta];
  return target ? target.id : null;
}

async function updateTicketState(ticketId, statusId) {
  if (!statusId) return;
  await csrfJson(`/api/tickets/${ticketId}`, "PATCH", { status_id: statusId });
  await loadReferenceData();
}

function renderBoard(states, tickets) {
  if (!boardEl) return;
  boardEl.innerHTML = "";
  if (!states.length) {
    boardEl.innerHTML = `
      <div class="empty-state">
        <h2>Kanban sem estados</h2>
        <p>Crie pelo menos um estado em Configuracoes para montar as colunas do quadro.</p>
      </div>
    `;
    return;
  }
  const ticketsByState = new Map(states.map((state) => [state.id, []]));
  tickets.forEach((ticket) => {
    if (!ticketsByState.has(ticket.status_id)) ticketsByState.set(ticket.status_id, []);
    ticketsByState.get(ticket.status_id).push(ticket);
  });

  states.forEach((state) => {
    const column = document.createElement("section");
    column.className = "column";
    column.innerHTML = `
      <div class="column-head">
        <div class="column-title">
          <span class="swatch" style="background:${state.color}"></span>
          <strong>${state.name}</strong>
        </div>
        <span class="pill">${ticketsByState.get(state.id)?.length || 0}</span>
      </div>
      <div class="ticket-list" data-state-id="${state.id}"></div>
    `;
    const list = column.querySelector(".ticket-list");
    ticketsByState.get(state.id)?.forEach((ticket) => list.appendChild(renderTicket(ticket)));
    list.addEventListener("dragover", (event) => event.preventDefault());
    list.addEventListener("drop", async (event) => {
      event.preventDefault();
      const ticketId = Number(event.dataTransfer.getData("text/plain"));
      if (!ticketId) return;
      await csrfJson(`/api/tickets/${ticketId}`, "PATCH", { status_id: state.id });
      await loadReferenceData();
    });
    boardEl.appendChild(column);
  });
}

function renderTicket(ticket) {
  const template = document.getElementById("ticket-card-template");
  const card = template.content.firstElementChild.cloneNode(true);
  card.dataset.ticketId = ticket.id;
  card.querySelector('[data-field="title"]').textContent = ticket.title;
  card.querySelector(".ticket-id").textContent = `#${ticket.id}`;
  card.querySelector('[data-field="client_name"]').textContent = `${ticket.client_name} - ${ticket.client_phone}`;
  card.querySelector('[data-field="service"]').textContent = ticket.service || "Sem servico definido";
  card.querySelector('[data-field="department"]').textContent = ticket.department_name || "Sem departamento";
  card.querySelector('[data-field="status"]').textContent = ticket.status_name || "Sem status";
  const unreadCount = Number(ticket.unread_count || 0);
  const unreadBadge = card.querySelector('[data-field="unread"]');
  if (unreadBadge) {
    unreadBadge.hidden = unreadCount <= 0;
    unreadBadge.textContent = unreadCount > 99 ? "99+" : String(unreadCount);
  }
  card.classList.toggle("has-unread", unreadCount > 0);
  const actions = document.createElement("div");
  actions.className = "ticket-card-actions";
  const prevStateId = getAdjacentStateId(ticket.status_id, -1);
  const nextStateId = getAdjacentStateId(ticket.status_id, 1);
  actions.innerHTML = `
    <button type="button" class="ticket-nav-btn" data-move-card="prev" ${prevStateId ? "" : "disabled"} aria-label="Mover para a coluna anterior">&larr;</button>
    <button type="button" class="ticket-nav-btn" data-move-card="next" ${nextStateId ? "" : "disabled"} aria-label="Mover para a coluna seguinte">&rarr;</button>
  `;
  if (ticket.due_at) {
    const due = document.createElement("p");
    due.className = "muted";
    due.textContent = `Agenda: ${new Date(ticket.due_at).toLocaleString()}`;
    card.appendChild(due);
  }
  card.appendChild(actions);
  const labelRow = card.querySelector(".label-row");
  ticket.labels.forEach((label) => {
    const chip = document.createElement("span");
    chip.className = "label-chip";
    chip.innerHTML = `<span class="swatch" style="background:${label.color}"></span>${label.name}`;
    labelRow.appendChild(chip);
  });
  card.addEventListener("dragstart", (event) => {
    event.dataTransfer.setData("text/plain", String(ticket.id));
    playNotificationSound();
  });
  actions.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-move-card]");
    if (!button || button.disabled) return;
    event.stopPropagation();
    const nextStatusId = button.dataset.moveCard === "prev" ? prevStateId : nextStateId;
    if (!nextStatusId) return;
    try {
      await updateTicketState(ticket.id, nextStatusId);
      showFeedback("Card movido com sucesso.", "success");
    } catch (error) {
      showFeedback(error.message);
    }
  });
  card.addEventListener("click", () => openTicket(ticket.id));
  return card;
}

const ticketForm = document.getElementById("ticket-form");
ticketForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const payload = serializeForm(ticketForm);
    const result = await csrfJson("/api/tickets", "POST", payload);
    ticketForm.reset();
    document.getElementById("ticket-modal").classList.remove("open");
    showFeedback(result.merged ? "Card existente reutilizado para esse telefone." : "Card criado com sucesso.", "success");
    await loadReferenceData();
  } catch (error) {
    showFeedback(error.message);
  }
});

function bindSimpleForm(formId, endpoint, transform = (payload) => payload) {
  const form = document.getElementById(formId);
  form?.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const data = Object.fromEntries(new FormData(form).entries());
      const payload = transform(data, form);
      await csrfJson(endpoint, "POST", payload);
      form.reset();
      showFeedback("Item criado com sucesso.", "success");
      location.reload();
    } catch (error) {
      showFeedback(error.message);
    }
  });
}

bindSimpleForm("state-form", "/api/config/states", (data) => ({
  name: data.name,
  order_index: Number(data.order_index || 0),
  color: data.color,
  is_closed: Boolean(data.is_closed),
  is_default: Boolean(data.is_default),
}));
bindSimpleForm("department-form", "/api/config/departments");
bindSimpleForm("label-form", "/api/config/labels");
bindSimpleForm("quick-reply-form", "/api/config/quick-replies");
bindSettingsForm("whatsapp-settings-form");
bindSettingsForm("agenda-settings-form");
bindSettingsForm("reminder-settings-form");
bindSettingsForm("integration-settings-form");
bindUserCreateForm();
bindUserEditForm();
bindReminderRefresh();
bindIntegrationStatusRefresh();

if (boardEl || document.querySelector("select[data-department-select]")) {
  loadReferenceData().catch((error) => showFeedback(error.message));
}
if (document.getElementById("integration-status-list")) {
  loadIntegrationStatus().catch((error) => showFeedback(error.message));
}
if (document.getElementById("agenda-preview")) {
  loadAgendaPreview().catch((error) => showFeedback(error.message));
}

async function openTicket(ticketId) {
  currentTicketId = ticketId;
  const data = await fetchJson(`/api/tickets/${ticketId}`, { credentials: "same-origin" }, 2);
  if (!data.ok) {
    showFeedback(data.description || data.error || "Falha ao abrir o card");
    return;
  }
  const modal = document.getElementById("details-modal");
  const form = document.getElementById("details-form");
  const messageList = document.getElementById("message-list");
  const conversationMeta = document.getElementById("conversation-meta");
  const conversationContact = document.getElementById("conversation-contact");
  const messageStatus = document.getElementById("message-status");
  const selectedLabelIds = (data.ticket.labels || []).map((label) => String(label.id));
  form.ticket_id.value = data.ticket.id;
  form.title.value = data.ticket.title;
  form.client_name.value = data.ticket.client_name;
  form.client_phone.value = data.ticket.client_phone;
  form.company.value = data.ticket.company;
  form.service.value = data.ticket.service;
  form.due_at.value = formatDateTimeLocal(data.ticket.due_at);
  form.description.value = data.ticket.description;
  form.department_id.dataset.selectedDepartmentId = data.ticket.department_id || "";
  populateDepartmentSelects(data.departments || availableDepartments, data.ticket.department_id || "");
  renderLabelPicker(document.getElementById("details-label-picker"), availableLabels, selectedLabelIds);
  conversationContact.textContent = data.conversation.contact_name ? `Contato: ${data.conversation.contact_name}` : "Sem contato identificado";
  conversationMeta.textContent = data.conversation.last_message_at ? `Atualizado em ${new Date(data.conversation.last_message_at).toLocaleString()}` : "Sem mensagens";
  messageList.innerHTML = "";
  if (messageStatus) messageStatus.textContent = "";
  if (whatsappSendLog) {
    whatsappSendLog.innerHTML = "";
    whatsappSendLog.classList.add("empty");
  }
  data.conversation.messages.forEach((message) => {
    messageList.appendChild(renderMessage(message));
  });
  modal.classList.add("open");
  markTicketAsRead(ticketId);
}

async function markTicketAsRead(ticketId) {
  try {
    await csrfJson(`/api/tickets/${ticketId}/read`, "POST", {});
    await loadReferenceData();
  } catch (error) {
    console.warn("Nao foi possivel marcar mensagens como lidas.", error);
  }
}

function renderMessage(message) {
  const bubble = document.createElement("article");
  bubble.className = `message-bubble ${message.direction}`;
  const meta = document.createElement("span");
  meta.className = "meta";
  const senderLabel = formatSenderLabel(message);
  meta.textContent = `${senderLabel} - ${new Date(message.created_at).toLocaleString()}`;
  bubble.appendChild(meta);

  if (message.content) {
    const text = document.createElement("div");
    text.textContent = message.content;
    bubble.appendChild(text);
  }

  if (message.media_url) {
    bubble.appendChild(renderAttachment(message.media_url));
  }
  return bubble;
}

function renderAttachment(mediaUrl) {
  const wrapper = document.createElement("div");
  wrapper.className = "message-attachment";
  const resolvedUrl = apiUrl(mediaUrl);
  const kind = guessAttachmentKind(resolvedUrl);
  const filename = getAttachmentFilename(resolvedUrl);

  if (kind === "image") {
    const link = document.createElement("a");
    link.href = resolvedUrl;
    link.target = "_blank";
    link.rel = "noreferrer";
    const img = document.createElement("img");
    img.src = resolvedUrl;
    img.alt = filename || "Anexo de imagem";
    img.loading = "lazy";
    link.appendChild(img);
    wrapper.appendChild(link);
    return wrapper;
  }

  if (kind === "video" || kind === "audio") {
    const media = document.createElement(kind);
    media.src = resolvedUrl;
    media.controls = true;
    media.preload = "metadata";
    wrapper.appendChild(media);
  }

  const link = document.createElement("a");
  link.href = resolvedUrl;
  link.target = "_blank";
  link.rel = "noreferrer";
  link.textContent = filename || "Abrir anexo";
  wrapper.appendChild(link);
  return wrapper;
}

function guessAttachmentKind(url) {
  const path = (() => {
    try {
      return new URL(url, window.location.href).pathname;
    } catch {
      return String(url || "");
    }
  })().toLowerCase();
  if (/\.(png|jpe?g|gif|webp|bmp|svg)$/.test(path)) return "image";
  if (/\.(mp4|webm|mov|mkv)$/.test(path)) return "video";
  if (/\.(mp3|wav|ogg|m4a)$/.test(path)) return "audio";
  return "document";
}

function getAttachmentFilename(url) {
  try {
    const pathname = new URL(url, window.location.href).pathname;
    const filename = pathname.split("/").pop() || "";
    return decodeURIComponent(filename);
  } catch {
    return String(url || "");
  }
}

function parseLegacySenderLabel(value) {
  const raw = String(value || "").trim();
  if (!raw) return { name: "", department: "" };
  const match = raw.match(/^\[(.+?)\](?:\s+\[(.+?)\])?$/);
  if (!match) return { name: raw, department: "" };
  return {
    name: (match[1] || "").trim(),
    department: (match[2] || "").trim(),
  };
}

function formatSenderLabel(message) {
  const senderName = String(message?.sender_name || "").trim();
  const senderDepartment = String(message?.sender_department || "").trim();
  if (senderDepartment) {
    return `${senderName} [${senderDepartment}]`;
  }
  const legacy = parseLegacySenderLabel(senderName);
  if (legacy.department) {
    return `${legacy.name} [${legacy.department}]`;
  }
  return legacy.name || senderName;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

const detailsForm = document.getElementById("details-form");
detailsForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!currentTicketId) return;
  try {
    const payload = serializeForm(detailsForm);
    await csrfJson(`/api/tickets/${currentTicketId}`, "PATCH", payload);
    showFeedback("Card atualizado com sucesso.", "success");
    await loadReferenceData();
  } catch (error) {
    showFeedback(error.message);
  }
});

const deleteTicketBtn = document.getElementById("delete-ticket-btn");
deleteTicketBtn?.addEventListener("click", async () => {
  if (!currentTicketId) return;
  if (!window.confirm("Excluir este card? Essa ação nao pode ser desfeita.")) return;
  try {
    await csrfJson(`/api/tickets/${currentTicketId}`, "DELETE", {});
    showFeedback("Card excluido com sucesso.", "success");
    currentTicketId = null;
    document.getElementById("details-modal").classList.remove("open");
    await loadReferenceData();
  } catch (error) {
    showFeedback(error.message);
  }
});

const messageForm = document.getElementById("message-form");
messageForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!currentTicketId) return;
  const submitBtn = messageForm.querySelector('button[type="submit"]');
  const messageStatus = document.getElementById("message-status");
  const recipient = messageForm.querySelector('input[name="client_phone"]')?.value || "";
  const contentField = messageForm.querySelector('textarea[name="content"]');
  if (contentField) {
    contentField.value = expandQuickReplyDraft(contentField.value);
  }
  try {
    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.textContent = "Enviando...";
    }
    if (messageStatus) messageStatus.textContent = "Enviando mensagem para o WhatsApp...";
    appendWhatsappLog(`Enviando para ${recipient || "telefone do card"}`, "warn");
    const payload = Object.fromEntries(new FormData(messageForm).entries());
    const result = await csrfJson(`/api/tickets/${currentTicketId}/messages`, "POST", payload);
    if (result.ok) {
      messageForm.reset();
      updateQuickReplySuggestions();
      openTicket(currentTicketId).catch((error) => console.warn("Nao foi possivel recarregar o card apos o envio.", error));
      const messageId = result.whatsapp?.data?.messages?.[0]?.id || result.message_id || "sem id";
      if (messageStatus) messageStatus.textContent = `Aceita pela Meta. ID: ${messageId}`;
      appendWhatsappLog(
        `Aceita pela Meta com status ${result.whatsapp?.status_code || "200"} e id ${messageId}`,
        "ok"
      );
      playNotificationSound();
    }
  } catch (error) {
    if (messageStatus) messageStatus.textContent = `Erro no envio: ${error.message}`;
    appendWhatsappLog(`Erro no envio: ${error.message}`, "error");
    showFeedback(error.message);
  } finally {
    if (submitBtn) {
      submitBtn.disabled = false;
      submitBtn.textContent = "Enviar WhatsApp";
    }
  }
});

const messageContentField = messageForm?.querySelector('textarea[name="content"]');
messageContentField?.addEventListener("input", updateQuickReplySuggestions);
messageContentField?.addEventListener("click", updateQuickReplySuggestions);
messageContentField?.addEventListener("keyup", updateQuickReplySuggestions);
messageForm?.addEventListener("reset", () => {
  window.setTimeout(updateQuickReplySuggestions, 0);
});
messageContentField?.addEventListener("blur", () => {
  window.setTimeout(() => {
    if (!document.activeElement || document.activeElement !== quickReplySuggestionsEl) {
      quickReplySuggestionsEl?.classList.remove("open");
    }
  }, 120);
});
quickReplySuggestionsEl?.addEventListener("mouseleave", () => {
  if (document.activeElement !== messageContentField) {
    quickReplySuggestionsEl.classList.remove("open");
  }
});

const pickFileBtn = document.getElementById("pick-file-btn");
const fileUpload = document.getElementById("file-upload");
pickFileBtn?.addEventListener("click", () => fileUpload?.click());
fileUpload?.addEventListener("change", async () => {
  if (!fileUpload.files?.length) return;
  const formData = new FormData();
  formData.append("file", fileUpload.files[0]);
  try {
    const response = await fetch(apiUrl("/api/uploads"), {
      method: "POST",
      credentials: "same-origin",
      body: formData,
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.description || data.error || "Falha no upload");
    if (messageForm) {
      messageForm.media_url.value = data.public_url || data.url;
      showFeedback("Arquivo anexado com sucesso.", "success");
    }
  } catch (error) {
    showFeedback(error.message);
  }
});

const syncAgendaBtn = document.getElementById("sync-agenda-btn");
syncAgendaBtn?.addEventListener("click", async () => {
  try {
    await csrfJson("/api/agenda/sync", "POST", {});
    showFeedback("Agenda sincronizada com sucesso.", "success");
    await loadAgendaPreview();
  } catch (error) {
    showFeedback(error.message);
  }
});

function bindReminderRefresh() {
  const runRemindersBtn = document.getElementById("run-reminders-btn");
  const runRemindersShortcut = document.getElementById("run-reminders-shortcut");
  const runReminders = async () => {
    try {
      await csrfJson("/api/reminders/run", "POST", {});
      showFeedback("Lembretes executados com sucesso.", "success");
    } catch (error) {
      showFeedback(error.message);
    }
  };
  runRemindersBtn?.addEventListener("click", runReminders);
  runRemindersShortcut?.addEventListener("click", runReminders);
}

setInterval(async () => {
  if (!boardEl) return;
  try {
    const data = await fetchJson(`/api/messages/poll?since=${encodeURIComponent(lastMessagePollAt)}`, { credentials: "same-origin" }, 1);
    lastMessagePollAt = data.server_time || new Date().toISOString();
    const incomingMessages = (data.messages || []).filter((message) => message.direction === "incoming");
    if (incomingMessages.length) {
      playNotificationSound();
      loadReferenceData().catch((error) => console.warn(error));
    }
  } catch (error) {
    console.warn(error);
  }
}, 30000);

async function loadAgendaPreview() {
  const previewEl = document.getElementById("agenda-preview");
  if (!previewEl) return;
  const data = await fetchJson("/api/agenda/preview", { credentials: "same-origin" }, 2);
  previewEl.innerHTML = "";
  if (!data.rows.length) {
    previewEl.innerHTML = '<p class="muted">Nenhuma linha encontrada na planilha.</p>';
    return;
  }
  data.rows.slice(0, 8).forEach((row) => {
    const item = document.createElement("div");
    item.className = "list-item";
    item.innerHTML = `
      <strong>${escapeHtml(row.Cliente || row["Ticket ID"] || "Linha")}</strong>
      <span>${escapeHtml(row.Servico || "")}</span>
      <span class="muted">${escapeHtml(row.Vencimento || "")}</span>
    `;
    previewEl.appendChild(item);
  });
}

async function loadIntegrationStatus() {
  const list = document.getElementById("integration-status-list");
  if (!list) return;
  const data = await fetchJson("/api/integrations/status", { credentials: "same-origin" }, 2);
  renderIntegrationStatus(list, data.items || []);
}

function bindIntegrationStatusRefresh() {
  const button = document.getElementById("refresh-integration-status-btn");
  button?.addEventListener("click", loadIntegrationStatus);
}

function renderIntegrationStatus(container, items) {
  container.innerHTML = "";
  items.forEach((item) => {
    const row = document.createElement("div");
    row.className = `status-row status-${item.status}`;
    row.innerHTML = `<strong>${escapeHtml(item.name)}</strong><span>${escapeHtml(item.detail)}</span>`;
    container.appendChild(row);
  });
}

function serializeForm(form) {
  const payload = {};
  const formData = new FormData(form);
  for (const [key, value] of formData.entries()) {
    if (key in payload) {
      if (!Array.isArray(payload[key])) {
        payload[key] = [payload[key]];
      }
      payload[key].push(value);
    } else {
      payload[key] = value;
    }
  }
  return payload;
}

function renderLabelPicker(container, labels, selectedIds) {
  if (!container) return;
  container.innerHTML = "";
  if (!labels.length) {
    container.innerHTML = '<span class="muted">Nenhuma etiqueta criada ainda.</span>';
    return;
  }

  labels.forEach((label) => {
    const id = String(label.id);
    const wrapper = document.createElement("label");
    wrapper.className = "label-check";
    wrapper.innerHTML = `
      <input type="checkbox" name="label_ids" value="${id}" ${selectedIds.includes(id) ? "checked" : ""}>
      <span class="swatch" style="background:${label.color}"></span>
      <span>${label.name}</span>
    `;
    container.appendChild(wrapper);
  });
}

function bindSettingsForm(formId) {
  const form = document.getElementById(formId);
  form?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = {};
    for (const element of form.elements) {
      if (!element.name) continue;
      if (element.type === "checkbox") {
        payload[element.name] = element.checked;
      } else if (element.type !== "button" && element.type !== "submit") {
        payload[element.name] = element.value;
      }
    }
    try {
      await csrfJson("/api/settings/bulk", "POST", { settings: payload });
      showFeedback("Configuracao salva com sucesso.", "success");
      location.reload();
    } catch (error) {
      showFeedback(error.message);
    }
  });
}

function bindUserCreateForm() {
  const form = document.getElementById("user-create-form");
  form?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = Object.fromEntries(new FormData(form).entries());
    try {
      await csrfJson("/api/users", "POST", payload);
      form.reset();
      showFeedback("Usuario criado com sucesso.", "success");
      location.reload();
    } catch (error) {
      showFeedback(error.message);
    }
  });
}

function bindUserEditForm() {
  const form = document.getElementById("user-edit-form");
  const modal = document.getElementById("user-modal");

  document.addEventListener("click", (event) => {
    const button = event.target.closest(".edit-user-btn");
    if (!button) return;
    const data = JSON.parse(button.dataset.user || "{}");
    form.id.value = data.id || "";
    form.name.value = data.name || "";
    form.email.value = data.email || "";
    form.role.value = data.role || "operator";
    form.password.value = "";
    form.department_id.dataset.selectedDepartmentId = data.department_id || "";
    populateDepartmentSelects(availableDepartments, data.department_id || "");
    modal.classList.add("open");
  });

  form?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = Object.fromEntries(new FormData(form).entries());
    const userId = payload.id;
    delete payload.id;
    try {
      await csrfJson(`/api/users/${userId}`, "PATCH", payload);
      modal.classList.remove("open");
      showFeedback("Usuario atualizado com sucesso.", "success");
      location.reload();
    } catch (error) {
      showFeedback(error.message);
    }
  });
}
