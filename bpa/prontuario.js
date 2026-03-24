const state = {
  table: null,
  pk: null,
  columns: [],
  rows: [],
  selectedPkValue: null,
  selectedRowKey: null,
  availableTables: [],
};

const preferredTables = ["APLICAC", "CADCNS", "CADMED", "CADMUN", "S_PA", "S_PASRV"];
const storageKey = "portal-bpa-db-path";
const tableKey = "portal-bpa-selected-table";

const $ = (id) => document.getElementById(id);

function toast(message, ok = true) {
  const element = $("toast");
  element.textContent = message;
  element.className = `toast ${ok ? "ok" : "bad"}`;
  element.style.display = "block";
  window.clearTimeout(toast.timer);
  toast.timer = window.setTimeout(() => {
    element.style.display = "none";
  }, ok ? 3200 : 5200);
}

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function currentDbPath() {
  return ($("dbPath")?.value || "").trim();
}

function rememberDbPath() {
  const value = currentDbPath();
  localStorage.setItem(storageKey, value);
  $("dbLabel").textContent = value || "Padrao do servidor";
}

function withDbPath(url) {
  const dbPath = currentDbPath();
  if (!dbPath) return url;
  const separator = url.includes("?") ? "&" : "?";
  return `${url}${separator}dbPath=${encodeURIComponent(dbPath)}`;
}

async function api(url, options = {}) {
  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort(), 20000);

  try {
    const response = await fetch(withDbPath(url), {
      ...options,
      headers: {
        Accept: "application/json",
        ...(options.headers || {}),
      },
      signal: controller.signal,
    });

    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.error || data.detail || `HTTP ${response.status}`);
    }
    return data;
  } finally {
    window.clearTimeout(timeoutId);
  }
}

function updateHealth(status, detail, ok = false) {
  $("healthLabel").textContent = status;
  $("healthDetail").textContent = detail;
  $("healthLabel").style.color = ok ? "var(--accent-3)" : "";
}

function renderQuickTables() {
  const wrap = $("quickTables");
  const choices = preferredTables.filter((table) => state.availableTables.includes(table));
  if (!choices.length) {
    wrap.innerHTML = "";
    return;
  }

  wrap.innerHTML = choices
    .map((table) => {
      const isActive = state.table === table;
      return `<button class="quick-btn ${isActive ? "active" : ""}" type="button" data-table="${table}">${table}</button>`;
    })
    .join("");

  wrap.querySelectorAll("[data-table]").forEach((button) => {
    button.addEventListener("click", async () => {
      $("tables").value = button.dataset.table;
      localStorage.setItem(tableKey, button.dataset.table);
      await reloadCurrentTable();
    });
  });
}

function renderMeta(items) {
  $("meta").innerHTML = items.map((item) => `<span class="meta-pill">${item}</span>`).join("");
}

function buildField(column, pk) {
  const name = column.col_name;
  const id = `f_${name.toLowerCase()}`;
  const required = column.not_null_flag ? " *" : "";
  const helper = `Tipo ${column.field_type}${column.field_length ? `, tam. ${column.field_length}` : ""}`;

  return `
    <label>
      <span>${esc(name)}${required}</span>
      <input id="${esc(id)}" placeholder="${esc(name)}" ${pk && pk.toLowerCase() === name.toLowerCase() ? 'data-pk="true"' : ""} />
      <small>${helper}</small>
    </label>
  `;
}

function buildForm(columnsMeta, pk) {
  const wrap = $("formFields");
  wrap.innerHTML = columnsMeta.map((column) => buildField(column, pk)).join("");
  state.selectedPkValue = null;
  state.selectedRowKey = null;
  $("btnDelete").disabled = true;
  $("formHint").textContent = pk
    ? `PK detectada: ${pk}. Clique em uma linha para editar ou excluir.`
    : "Sem PK simples. Insercao funciona, mas editar e excluir podem nao estar disponiveis.";
}

function clearForm() {
  state.columns.forEach((column) => {
    const input = $(`f_${column.toLowerCase()}`);
    if (input) input.value = "";
  });
  state.selectedPkValue = null;
  state.selectedRowKey = null;
  $("btnDelete").disabled = true;
  document.querySelectorAll("#tbody tr").forEach((row) => row.classList.remove("is-selected"));
}

function fillFormFromRow(row, rowIndex) {
  state.columns.forEach((column) => {
    const key = column.toLowerCase();
    const input = $(`f_${key}`);
    if (input) input.value = row[key] ?? "";
  });

  state.selectedRowKey = rowIndex;
  document.querySelectorAll("#tbody tr").forEach((element) => element.classList.remove("is-selected"));
  const selected = document.querySelector(`#tbody tr[data-row-index="${rowIndex}"]`);
  if (selected) selected.classList.add("is-selected");

  if (state.pk) {
    state.selectedPkValue = row[state.pk.toLowerCase()];
    $("btnDelete").disabled = state.selectedPkValue === null || state.selectedPkValue === undefined || state.selectedPkValue === "";
  }
}

function getFormPayload() {
  const payload = {};
  state.columns.forEach((column) => {
    const input = $(`f_${column.toLowerCase()}`);
    const value = input ? input.value : "";
    if (value !== "" && value !== null && value !== undefined) {
      payload[column.toLowerCase()] = value;
    }
  });
  return payload;
}

async function loadTables(showToast = false) {
  const data = await api("/api/bpa/tables");
  const select = $("tables");
  const previous = localStorage.getItem(tableKey);

  state.availableTables = Array.isArray(data.tables) ? data.tables : [];
  select.innerHTML = state.availableTables
    .map((table) => `<option value="${esc(table)}">${esc(table)}</option>`)
    .join("");

  $("tableCount").textContent = String(state.availableTables.length);
  renderQuickTables();

  if (!state.availableTables.length) {
    state.table = null;
    renderMeta(["Nenhuma tabela retornada pelo BPA"]);
    return;
  }

  const nextTable = state.availableTables.includes(previous)
    ? previous
    : state.availableTables.includes(state.table)
      ? state.table
      : state.availableTables[0];

  select.value = nextTable;
  state.table = nextTable;
  localStorage.setItem(tableKey, nextTable);

  if (showToast) {
    toast("Tabelas BPA recarregadas.");
  }
}

async function loadColumns() {
  const table = $("tables").value;
  if (!table) return;

  const meta = await api(`/api/bpa/table/${encodeURIComponent(table)}/columns`);
  state.table = meta.table;
  state.pk = meta.pk;
  state.columns = meta.columns.map((column) => column.col_name);

  renderMeta([
    `Tabela: ${esc(state.table)}`,
    `PK: ${esc(state.pk || "sem PK simples")}`,
    `Colunas: ${state.columns.length}`,
    `Banco: ${esc(meta.db_path || currentDbPath() || "padrao do servidor")}`,
  ]);

  buildForm(meta.columns, state.pk);
  renderQuickTables();
}

function renderTable(columns, rows, pk) {
  $("thead").innerHTML = `
    <tr>
      ${columns.map((column) => `<th>${esc(column.toUpperCase())}</th>`).join("")}
      <th>Acoes</th>
    </tr>
  `;

  $("tbody").innerHTML = rows
    .map((row, index) => {
      const cells = columns
        .map((column) => {
          const value = row[column];
          return `<td>${esc(value === null || value === undefined ? "" : value)}</td>`;
        })
        .join("");

      return `
        <tr data-row-index="${index}">
          ${cells}
          <td><span class="table-action">${pk ? "Editar" : "Inserir/consultar"}</span></td>
        </tr>
      `;
    })
    .join("");

  document.querySelectorAll("#tbody tr").forEach((row) => {
    row.addEventListener("click", () => {
      const rowIndex = Number(row.dataset.rowIndex);
      fillFormFromRow(state.rows[rowIndex], rowIndex);
    });
  });
}

async function loadRows(showToast = false) {
  const table = $("tables").value;
  if (!table) return;

  const limit = Number($("limit").value || 50);
  const offset = Number($("offset").value || 0);
  const data = await api(`/api/bpa/table/${encodeURIComponent(table)}/rows?limit=${limit}&offset=${offset}`);

  state.table = data.table;
  state.pk = data.pk;
  state.rows = data.rows || [];
  state.columns = (data.columns || []).map((column) => column.toUpperCase());
  localStorage.setItem(tableKey, table);

  renderTable(data.columns || [], data.rows || [], data.pk);

  if (showToast) {
    toast("Dados do BPA carregados.");
  }
}

async function reloadCurrentTable(showToast = false) {
  clearForm();
  await loadColumns();
  await loadRows(showToast);
}

async function save(event) {
  event.preventDefault();

  if (!state.table) {
    toast("Selecione uma tabela primeiro.", false);
    return;
  }

  const payload = getFormPayload();
  if (!Object.keys(payload).length) {
    toast("Preencha pelo menos um campo.", false);
    return;
  }

  try {
    if (state.pk && state.selectedPkValue !== null && state.selectedPkValue !== undefined && state.selectedPkValue !== "") {
      await api(`/api/bpa/table/${encodeURIComponent(state.table)}/row/${encodeURIComponent(state.selectedPkValue)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      toast("Registro atualizado com sucesso.");
    } else {
      await api(`/api/bpa/table/${encodeURIComponent(state.table)}/row`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      toast("Registro inserido com sucesso.");
    }

    clearForm();
    await loadRows();
  } catch (error) {
    toast(`Erro ao salvar: ${error.message}`, false);
  }
}

async function removeSelected() {
  if (!state.pk) {
    toast("Sem PK simples. Exclusao automatica nao disponivel.", false);
    return;
  }

  if (state.selectedPkValue === null || state.selectedPkValue === undefined || state.selectedPkValue === "") {
    toast("Selecione uma linha para excluir.", false);
    return;
  }

  if (!window.confirm(`Excluir registro onde ${state.pk} = ${state.selectedPkValue}?`)) {
    return;
  }

  try {
    await api(`/api/bpa/table/${encodeURIComponent(state.table)}/row/${encodeURIComponent(state.selectedPkValue)}`, {
      method: "DELETE",
    });
    toast("Registro excluido com sucesso.");
    clearForm();
    await loadRows();
  } catch (error) {
    toast(`Erro ao excluir: ${error.message}`, false);
  }
}

async function health() {
  rememberDbPath();
  try {
    const data = await api("/api/bpa/health");
    updateHealth("Conexao ativa", `Banco confirmado em ${data.db_path || currentDbPath() || "padrao do servidor"}.`, Boolean(data.ok));
    toast("Conexao com o BPA confirmada.");
  } catch (error) {
    updateHealth("Servico indisponivel", error.message, false);
    toast(`Falha na conexao: ${error.message}`, false);
  }
}

function bootstrapStoredState() {
  const savedDbPath = localStorage.getItem(storageKey);
  if (savedDbPath) {
    $("dbPath").value = savedDbPath;
  }
  rememberDbPath();
}

async function boot() {
  bootstrapStoredState();
  $("form").addEventListener("submit", save);
  $("btnReload").addEventListener("click", async () => {
    await loadTables(true);
    await reloadCurrentTable();
  });
  $("btnLoadRows").addEventListener("click", () => reloadCurrentTable(true));
  $("btnClear").addEventListener("click", clearForm);
  $("btnDelete").addEventListener("click", removeSelected);
  $("btnHealth").addEventListener("click", health);
  $("tables").addEventListener("change", reloadCurrentTable);
  $("dbPath").addEventListener("change", async () => {
    rememberDbPath();
    await health();
    await loadTables();
    await reloadCurrentTable();
  });

  try {
    await health();
    await loadTables();
    await reloadCurrentTable();
  } catch (error) {
    toast(`Erro inicial: ${error.message}`, false);
  }
}

boot();
