const boardEl = document.getElementById("board");
const alertSound = document.getElementById("alert-sound");
const user = JSON.parse(document.body.dataset.user || "{}");
let currentTicketId = null;
let availableLabels = [];
let availableDepartments = [];

function csrfJson(url, method, payload) {
  return fetch(url, {
    method,
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify(payload),
  }).then(async (response) => {
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.description || data.error || "Falha na requisicao");
    return data;
  });
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
    navigator.clipboard?.writeText(quickReply.dataset.body || "");
    const original = quickReply.textContent;
    quickReply.textContent = "Copiado";
    setTimeout(() => {
      quickReply.textContent = original;
    }, 900);
  }
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") document.querySelectorAll(".modal.open").forEach(closeModal);
});

async function loadReferenceData() {
  const response = await fetch("/api/dashboard", { credentials: "same-origin" });
  const data = await response.json();
  if (!response.ok) return;
  availableLabels = data.labels || [];
  availableDepartments = data.departments || [];
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

function renderBoard(states, tickets) {
  if (!boardEl) return;
  boardEl.innerHTML = "";
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
  if (ticket.due_at) {
    const due = document.createElement("p");
    due.className = "muted";
    due.textContent = `Agenda: ${new Date(ticket.due_at).toLocaleString()}`;
    card.appendChild(due);
  }
  const labelRow = card.querySelector(".label-row");
  ticket.labels.forEach((label) => {
    const chip = document.createElement("span");
    chip.className = "label-chip";
    chip.innerHTML = `<span class="swatch" style="background:${label.color}"></span>${label.name}`;
    labelRow.appendChild(chip);
  });
  card.addEventListener("dragstart", (event) => {
    event.dataTransfer.setData("text/plain", String(ticket.id));
    if (alertSound) {
      alertSound.volume = 0.15;
      alertSound.play().catch(() => {});
    }
  });
  card.addEventListener("click", () => openTicket(ticket.id));
  return card;
}

const ticketForm = document.getElementById("ticket-form");
ticketForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = serializeForm(ticketForm);
  await csrfJson("/api/tickets", "POST", payload);
  ticketForm.reset();
  document.getElementById("ticket-modal").classList.remove("open");
  await loadReferenceData();
});

function bindSimpleForm(formId, endpoint, transform = (payload) => payload) {
  const form = document.getElementById(formId);
  form?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(form).entries());
    const payload = transform(data, form);
    await csrfJson(endpoint, "POST", payload);
    form.reset();
    location.reload();
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

if (boardEl || document.querySelector("select[data-department-select]")) loadReferenceData();
if (document.getElementById("integration-status-list")) loadIntegrationStatus();
if (document.getElementById("agenda-preview")) loadAgendaPreview();

async function openTicket(ticketId) {
  currentTicketId = ticketId;
  const response = await fetch(`/api/tickets/${ticketId}`, { credentials: "same-origin" });
  const data = await response.json();
  if (!response.ok) return;
  const modal = document.getElementById("details-modal");
  const form = document.getElementById("details-form");
  const messageList = document.getElementById("message-list");
  const conversationMeta = document.getElementById("conversation-meta");
  const conversationContact = document.getElementById("conversation-contact");
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
  data.conversation.messages.forEach((message) => {
    messageList.appendChild(renderMessage(message));
  });
  modal.classList.add("open");
}

function renderMessage(message) {
  const bubble = document.createElement("article");
  bubble.className = `message-bubble ${message.direction}`;
  bubble.innerHTML = `
    <span class="meta">${message.sender_name} - ${new Date(message.created_at).toLocaleString()}</span>
    <div>${escapeHtml(message.content || message.media_url || "")}</div>
  `;
  return bubble;
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
  const payload = serializeForm(detailsForm);
  await csrfJson(`/api/tickets/${currentTicketId}`, "PATCH", payload);
  await loadReferenceData();
});

const messageForm = document.getElementById("message-form");
messageForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!currentTicketId) return;
  const payload = Object.fromEntries(new FormData(messageForm).entries());
  const result = await csrfJson(`/api/tickets/${currentTicketId}/messages`, "POST", payload);
  if (result.ok) {
    messageForm.reset();
    await openTicket(currentTicketId);
    if (alertSound) alertSound.play().catch(() => {});
  }
});

const pickFileBtn = document.getElementById("pick-file-btn");
const fileUpload = document.getElementById("file-upload");
pickFileBtn?.addEventListener("click", () => fileUpload?.click());
fileUpload?.addEventListener("change", async () => {
  if (!fileUpload.files?.length) return;
  const formData = new FormData();
  formData.append("file", fileUpload.files[0]);
  const response = await fetch("/api/uploads", {
    method: "POST",
    credentials: "same-origin",
    body: formData,
  });
  const data = await response.json();
  if (response.ok && messageForm) {
    messageForm.media_url.value = data.url;
  }
});

const syncAgendaBtn = document.getElementById("sync-agenda-btn");
syncAgendaBtn?.addEventListener("click", async () => {
  await csrfJson("/api/agenda/sync", "POST", {});
  await loadAgendaPreview();
});

function bindReminderRefresh() {
  const runRemindersBtn = document.getElementById("run-reminders-btn");
  const runRemindersShortcut = document.getElementById("run-reminders-shortcut");
  const runReminders = async () => {
    await csrfJson("/api/reminders/run", "POST", {});
  };
  runRemindersBtn?.addEventListener("click", runReminders);
  runRemindersShortcut?.addEventListener("click", runReminders);
}

setInterval(async () => {
  if (!boardEl) return;
  const response = await fetch("/api/messages/poll", { credentials: "same-origin" });
  if (!response.ok) return;
  const data = await response.json();
  if (data.messages && data.messages.length) {
    if (alertSound) alertSound.play().catch(() => {});
  }
}, 30000);

async function loadAgendaPreview() {
  const previewEl = document.getElementById("agenda-preview");
  if (!previewEl) return;
  const response = await fetch("/api/agenda/preview", { credentials: "same-origin" });
  const data = await response.json();
  if (!response.ok) return;
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
  const response = await fetch("/api/integrations/status", { credentials: "same-origin" });
  const data = await response.json();
  if (!response.ok) return;
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
    await csrfJson("/api/settings/bulk", "POST", { settings: payload });
    location.reload();
  });
}

function bindUserCreateForm() {
  const form = document.getElementById("user-create-form");
  form?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = Object.fromEntries(new FormData(form).entries());
    await csrfJson("/api/users", "POST", payload);
    form.reset();
    location.reload();
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
    await csrfJson(`/api/users/${userId}`, "PATCH", payload);
    modal.classList.remove("open");
    location.reload();
  });
}
