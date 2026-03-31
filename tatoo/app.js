const STORAGE_KEY = "tatooStudioDB";

const emptyDB = () => ({
  clients: [],
  sessions: [],
  consents: [],
  payments: [],
  meta: {
    studioName: "Tatoo Studio",
    version: 2,
    updatedAt: new Date().toISOString(),
  },
});

function generateId(prefix) {
  return `${prefix}_${Date.now()}_${Math.random().toString(16).slice(2, 8)}`;
}

function loadDB() {
  const raw = localStorage.getItem(STORAGE_KEY);
  if (!raw) {
    const base = emptyDB();
    localStorage.setItem(STORAGE_KEY, JSON.stringify(base));
    return base;
  }

  try {
    const parsed = JSON.parse(raw);
    return {
      ...emptyDB(),
      ...parsed,
      clients: Array.isArray(parsed.clients) ? parsed.clients : [],
      sessions: Array.isArray(parsed.sessions) ? parsed.sessions : [],
      consents: Array.isArray(parsed.consents) ? parsed.consents : [],
      payments: Array.isArray(parsed.payments) ? parsed.payments : [],
      meta: {
        ...emptyDB().meta,
        ...(parsed.meta || {}),
      },
    };
  } catch (error) {
    console.error("Erro ao ler base local:", error);
    return emptyDB();
  }
}

function saveDB(db) {
  db.meta.updatedAt = new Date().toISOString();
  localStorage.setItem(STORAGE_KEY, JSON.stringify(db));
}

let db = loadDB();

const els = {
  clientForm: document.querySelector("#client-form"),
  sessionForm: document.querySelector("#session-form"),
  consentForm: document.querySelector("#consent-form"),
  paymentForm: document.querySelector("#payment-form"),
  clientsTable: document.querySelector("#clients-table"),
  sessionsList: document.querySelector("#sessions-list"),
  consentsList: document.querySelector("#consents-list"),
  paymentsList: document.querySelector("#payments-list"),
  clientSearch: document.querySelector("#client-search"),
  exportButton: document.querySelector("#export-json"),
  importInput: document.querySelector("#import-json"),
  metricClients: document.querySelector("#metric-clients"),
  metricSessions: document.querySelector("#metric-sessions"),
  metricConsents: document.querySelector("#metric-consents"),
  metricRevenue: document.querySelector("#metric-revenue"),
  sessionClient: document.querySelector("#session-client"),
  consentClient: document.querySelector("#consent-client"),
  consentSession: document.querySelector("#consent-session"),
  paymentClient: document.querySelector("#payment-client"),
  paymentSession: document.querySelector("#payment-session"),
};

function currencyBRL(value) {
  return Number(value || 0).toLocaleString("pt-BR", {
    style: "currency",
    currency: "BRL",
  });
}

function formatDate(value) {
  if (!value) return "-";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "-" : date.toLocaleString("pt-BR");
}

function getClientById(id) {
  return db.clients.find((client) => client.id === id);
}

function getSessionById(id) {
  return db.sessions.find((session) => session.id === id);
}

function fillSelect(select, items, placeholder, formatter) {
  if (!select) return;
  const current = select.value;
  select.innerHTML = "";

  const baseOption = document.createElement("option");
  baseOption.value = "";
  baseOption.textContent = placeholder;
  select.appendChild(baseOption);

  items.forEach((item) => {
    const option = document.createElement("option");
    option.value = item.id;
    option.textContent = formatter(item);
    select.appendChild(option);
  });

  if (items.some((item) => item.id === current)) {
    select.value = current;
  }
}

function cloneEmptyState() {
  return document.querySelector("#empty-state-template").content.cloneNode(true);
}

function consentDisplayStatus(consent) {
  if (!consent.signature) {
    return { className: "pending", label: "Pendente" };
  }

  if (consent.signature.signatureMode === "external") {
    return { className: "external", label: "PDF gerado" };
  }

  return { className: "ok", label: "Assinado" };
}

function renderMetrics() {
  const revenue = db.payments.reduce((sum, payment) => sum + Number(payment.amount || 0), 0);
  els.metricClients.textContent = String(db.clients.length);
  els.metricSessions.textContent = String(db.sessions.length);
  els.metricConsents.textContent = String(db.consents.length);
  els.metricRevenue.textContent = currencyBRL(revenue);
}

function renderClients() {
  const query = (els.clientSearch.value || "").toLowerCase().trim();
  const filtered = db.clients.filter((client) => {
    return [client.name, client.phone, client.document, client.email, client.rg, client.city, client.state, client.social]
      .filter(Boolean)
      .some((field) => field.toLowerCase().includes(query));
  });

  els.clientsTable.innerHTML = "";
  if (!filtered.length) {
    const row = document.createElement("tr");
    row.innerHTML = `<td colspan="3">Nenhum cliente encontrado.</td>`;
    els.clientsTable.appendChild(row);
    return;
  }

  filtered.forEach((client) => {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td>
        <strong>${client.name}</strong><br>
        <small>CPF: ${client.document || "-"}</small><br>
        <small>RG: ${client.rg || "-"}</small>
      </td>
      <td>
        ${client.phone || "-"}<br>
        <small>${client.email || "Sem e-mail"}</small><br>
        <small>${[client.city, client.state].filter(Boolean).join(" / ") || "Cidade nao informada"}</small>
      </td>
      <td>${client.healthNotes || "Sem observacoes"}</td>
    `;
    els.clientsTable.appendChild(row);
  });
}

function renderSessions() {
  els.sessionsList.innerHTML = "";
  const sessions = [...db.sessions].sort((a, b) => new Date(a.appointmentAt) - new Date(b.appointmentAt));

  if (!sessions.length) {
    els.sessionsList.appendChild(cloneEmptyState());
    return;
  }

  sessions.forEach((session) => {
    const client = getClientById(session.clientId);
    const item = document.createElement("article");
    item.className = "stack-item";
    item.innerHTML = `
      <div class="stack-top">
        <h5>${client ? client.name : "Cliente removido"}</h5>
        <span class="pill">${session.status}</span>
      </div>
      <p>${session.description}</p>
      <small>${session.bodyArea} - ${session.artist} - ${formatDate(session.appointmentAt)}</small><br>
      <small>Orcamento: ${currencyBRL(session.budget)}</small>
    `;
    els.sessionsList.appendChild(item);
  });
}

function consentLink(consent) {
  const url = new URL("./signature.html", window.location.href);
  url.searchParams.set("consentId", consent.id);
  return url.toString();
}

function renderConsents() {
  els.consentsList.innerHTML = "";

  if (!db.consents.length) {
    els.consentsList.appendChild(cloneEmptyState());
    return;
  }

  const consents = [...db.consents].sort((a, b) => new Date(b.createdAt) - new Date(a.createdAt));
  consents.forEach((consent) => {
    const client = getClientById(consent.clientId);
    const session = getSessionById(consent.sessionId);
    const item = document.createElement("article");
    item.className = "stack-item";
    const status = consentDisplayStatus(consent);
    const link = consentLink(consent);
    const contractFile = consent.signature?.contractFile;
    const signatureModeLabel = consent.signature?.signatureMode === "external" ? "Assinatura externa" : consent.signature ? "Assinatura na tela" : "Sem assinatura";
    item.innerHTML = `
      <div class="stack-top">
        <h5>${consent.termType}</h5>
        <span class="pill ${status.className}">${status.label}</span>
      </div>
      <p>${client ? client.name : "Cliente removido"} - ${session ? session.bodyArea : "Sessao nao encontrada"}</p>
      <small>Criado em ${formatDate(consent.createdAt)}</small>
      <small>Link da assinatura: <a href="${link}" target="_blank" rel="noreferrer">${link}</a></small>
      <small>Modo: ${signatureModeLabel}</small>
      ${contractFile?.url ? `<small>PDF do contrato: <a href="${contractFile.url}" target="_blank" rel="noreferrer">${contractFile.fileName || "Baixar PDF"}</a></small>` : ""}
      ${consent.notes ? `<small>Observacoes: ${consent.notes}</small>` : ""}
    `;
    els.consentsList.appendChild(item);
  });
}

function renderPayments() {
  els.paymentsList.innerHTML = "";

  if (!db.payments.length) {
    els.paymentsList.appendChild(cloneEmptyState());
    return;
  }

  const payments = [...db.payments].sort((a, b) => new Date(b.createdAt) - new Date(a.createdAt));
  payments.forEach((payment) => {
    const client = getClientById(payment.clientId);
    const session = getSessionById(payment.sessionId);
    const item = document.createElement("article");
    item.className = "stack-item";
    item.innerHTML = `
      <div class="stack-top">
        <h5>${currencyBRL(payment.amount)}</h5>
        <span class="pill">${payment.method}</span>
      </div>
      <p>${client ? client.name : "Cliente removido"}</p>
      <small>${session ? session.description : "Sessao nao encontrada"}</small><br>
      <small>${formatDate(payment.createdAt)}</small>
      ${payment.notes ? `<small>Obs.: ${payment.notes}</small>` : ""}
    `;
    els.paymentsList.appendChild(item);
  });
}

function renderSelects() {
  fillSelect(els.sessionClient, db.clients, "Selecione um cliente", (client) => client.name);
  fillSelect(els.consentClient, db.clients, "Selecione um cliente", (client) => client.name);
  fillSelect(els.paymentClient, db.clients, "Selecione um cliente", (client) => client.name);

  fillSelect(
    els.consentSession,
    db.sessions,
    "Selecione uma sessao",
    (session) => {
      const client = getClientById(session.clientId);
      return `${client ? client.name : "Cliente"} - ${session.bodyArea} - ${formatDate(session.appointmentAt)}`;
    }
  );

  fillSelect(
    els.paymentSession,
    db.sessions,
    "Selecione uma sessao",
    (session) => {
      const client = getClientById(session.clientId);
      return `${client ? client.name : "Cliente"} - ${session.description.slice(0, 40)}`;
    }
  );
}

function renderAll() {
  renderMetrics();
  renderClients();
  renderSessions();
  renderConsents();
  renderPayments();
  renderSelects();
}

function syncSessionSelectsByClient(clientId, targetSelect) {
  const sessions = db.sessions.filter((session) => !clientId || session.clientId === clientId);
  fillSelect(
    targetSelect,
    sessions,
    "Selecione uma sessao",
    (session) => `${session.bodyArea} - ${formatDate(session.appointmentAt)}`
  );
}

els.clientForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const formData = new FormData(event.currentTarget);
  db.clients.unshift({
    id: generateId("client"),
    name: formData.get("name")?.toString().trim(),
    document: formData.get("document")?.toString().trim(),
    rg: formData.get("rg")?.toString().trim(),
    phone: formData.get("phone")?.toString().trim(),
    email: formData.get("email")?.toString().trim(),
    birthDate: formData.get("birthDate")?.toString(),
    address: formData.get("address")?.toString().trim(),
    city: formData.get("city")?.toString().trim(),
    state: formData.get("state")?.toString().trim().toUpperCase(),
    social: formData.get("social")?.toString().trim(),
    healthNotes: formData.get("healthNotes")?.toString().trim(),
    createdAt: new Date().toISOString(),
  });
  saveDB(db);
  event.currentTarget.reset();
  renderAll();
});

els.sessionForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const formData = new FormData(event.currentTarget);
  db.sessions.unshift({
    id: generateId("session"),
    clientId: formData.get("clientId")?.toString(),
    artist: formData.get("artist")?.toString().trim(),
    appointmentAt: formData.get("appointmentAt")?.toString(),
    bodyArea: formData.get("bodyArea")?.toString().trim(),
    description: formData.get("description")?.toString().trim(),
    budget: Number(formData.get("budget") || 0),
    status: formData.get("status")?.toString() || "Agendada",
    createdAt: new Date().toISOString(),
  });
  saveDB(db);
  event.currentTarget.reset();
  renderAll();
});

els.consentForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const formData = new FormData(event.currentTarget);
  db.consents.unshift({
    id: generateId("consent"),
    clientId: formData.get("clientId")?.toString(),
    sessionId: formData.get("sessionId")?.toString(),
    termType: formData.get("termType")?.toString(),
    notes: formData.get("notes")?.toString().trim(),
    createdAt: new Date().toISOString(),
    signature: null,
  });
  saveDB(db);
  event.currentTarget.reset();
  renderAll();
});

els.paymentForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const formData = new FormData(event.currentTarget);
  db.payments.unshift({
    id: generateId("payment"),
    clientId: formData.get("clientId")?.toString(),
    sessionId: formData.get("sessionId")?.toString(),
    amount: Number(formData.get("amount") || 0),
    method: formData.get("method")?.toString(),
    notes: formData.get("notes")?.toString().trim(),
    createdAt: new Date().toISOString(),
  });
  saveDB(db);
  event.currentTarget.reset();
  renderAll();
});

els.clientSearch.addEventListener("input", renderClients);

els.consentClient.addEventListener("change", (event) => {
  syncSessionSelectsByClient(event.target.value, els.consentSession);
});

els.paymentClient.addEventListener("change", (event) => {
  syncSessionSelectsByClient(event.target.value, els.paymentSession);
});

els.exportButton.addEventListener("click", () => {
  const blob = new Blob([JSON.stringify(db, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `tatoo-studio-backup-${new Date().toISOString().slice(0, 10)}.json`;
  link.click();
  URL.revokeObjectURL(url);
});

els.importInput.addEventListener("change", async (event) => {
  const [file] = event.target.files || [];
  if (!file) return;

  try {
    const text = await file.text();
    const parsed = JSON.parse(text);
    db = {
      ...emptyDB(),
      ...parsed,
      clients: Array.isArray(parsed.clients) ? parsed.clients : [],
      sessions: Array.isArray(parsed.sessions) ? parsed.sessions : [],
      consents: Array.isArray(parsed.consents) ? parsed.consents : [],
      payments: Array.isArray(parsed.payments) ? parsed.payments : [],
    };
    saveDB(db);
    renderAll();
    alert("Base importada com sucesso.");
  } catch (error) {
    console.error(error);
    alert("Nao foi possivel importar o JSON.");
  } finally {
    event.target.value = "";
  }
});

document.querySelectorAll("[data-scroll]").forEach((button) => {
  button.addEventListener("click", () => {
    const target = document.querySelector(button.dataset.scroll);
    if (target) {
      target.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  });
});

renderAll();
