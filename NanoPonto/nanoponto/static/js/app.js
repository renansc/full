const state = {
  payload: null,
  currentUser: null,
  hoursReport: null,
  activeAdminPanel: "punch-card",
};

function qs(selector) {
  return document.querySelector(selector);
}

function createItem(title, subtitle, actions = []) {
  const wrapper = document.createElement("div");
  wrapper.className = "list-item";
  const titleElement = document.createElement("strong");
  titleElement.textContent = title;
  const subtitleElement = document.createElement("small");
  subtitleElement.textContent = subtitle;
  wrapper.append(titleElement, subtitleElement);
  if (actions.length) {
    const actionRow = document.createElement("div");
    actionRow.className = "list-actions";
    actions.forEach((action) => actionRow.appendChild(action));
    wrapper.appendChild(actionRow);
  }
  return wrapper;
}

function button(label, onClick) {
  const element = document.createElement("button");
  element.type = "button";
  element.textContent = label;
  element.addEventListener("click", onClick);
  return element;
}

function link(label, href) {
  const element = document.createElement("a");
  element.href = href;
  element.textContent = label;
  element.target = "_blank";
  return element;
}

function localDateIso(date = new Date()) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || payload.message || "Erro inesperado");
  }
  return payload;
}

async function requestForm(url, formData) {
  const response = await fetch(url, {
    method: "POST",
    body: formData,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || payload.message || "Erro inesperado");
  }
  return payload;
}

function isAdmin() {
  return state.currentUser?.role === "admin";
}

function isEmployee() {
  return state.currentUser?.role === "employee";
}

function updateAdminPanels() {
  const menu = qs("#admin-menu");
  const groups = document.querySelectorAll("[data-admin-panel-group]");
  const panels = document.querySelectorAll("[data-admin-panel]");

  if (!menu || !panels.length) return;

  if (!isAdmin()) {
    menu.classList.add("hidden");
    panels.forEach((panel) => panel.classList.remove("panel-hidden-by-menu"));
    groups.forEach((group) => {
      group.classList.toggle("hidden", group.hasAttribute("data-admin-only"));
    });
    return;
  }

  menu.classList.remove("hidden");
  const fallbackPanel = document.querySelector("[data-admin-menu-item]")?.dataset.adminMenuItem || "punch-card";
  state.activeAdminPanel = state.activeAdminPanel || fallbackPanel;

  panels.forEach((panel) => {
    panel.classList.toggle("panel-hidden-by-menu", panel.dataset.adminPanel !== state.activeAdminPanel);
  });

  document.querySelectorAll("[data-admin-menu-item]").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.adminMenuItem === state.activeAdminPanel);
  });

  groups.forEach((group) => {
    const groupPanels = group.matches("[data-admin-panel]")
      ? [group]
      : [...group.querySelectorAll("[data-admin-panel]")];
    const hasVisiblePanel = groupPanels.some(
      (panel) => !panel.classList.contains("panel-hidden-by-menu")
    );
    group.classList.toggle("hidden", !hasVisiblePanel);
  });
}

function toggleAuthView() {
  const authGrid = qs("#auth-grid");
  const appShell = qs("#app-shell");
  if (state.currentUser) {
    authGrid.classList.add("hidden");
    appShell.classList.remove("hidden");
    qs("#session-user").textContent = isAdmin()
      ? "Admin"
      : `${state.currentUser.employee_name} | CPF ${state.currentUser.employee_cpf}`;
    qs("#recent-punches-title").textContent = isAdmin() ? "Ultimas Batidas" : "Minhas Batidas";
  } else {
    authGrid.classList.remove("hidden");
    appShell.classList.add("hidden");
  }

  document.querySelectorAll("[data-admin-only]").forEach((element) => {
    element.classList.toggle("hidden", !isAdmin());
  });
  updateAdminPanels();
}

function updateLoginLabels() {
  const role = qs("#login-role")?.value || "employee";
  const usernameLabel = qs("#login-username-label");
  const passwordLabel = qs("#login-password-label");
  const usernameInput = qs("#login-username");
  const passwordInput = qs("#login-password");
  if (role === "admin") {
    usernameLabel.firstChild.textContent = "Usuario admin";
    passwordLabel.firstChild.textContent = "Senha admin";
    usernameInput.placeholder = "Digite admin";
    passwordInput.placeholder = "Use o CNPJ da empresa";
    usernameInput.value = "admin";
  } else {
    usernameLabel.firstChild.textContent = "CPF do usuario";
    passwordLabel.firstChild.textContent = "Senha do usuario";
    usernameInput.placeholder = "Somente numeros";
    passwordInput.placeholder = "Senha cadastrada ou CPF inicial";
    usernameInput.value = "";
  }
  passwordInput.value = "";
}

function fillEmployeeSelect(selector, preferredValue = "", options = {}) {
  const target = qs(selector);
  if (!target) return;
  const autoSelectFirst = options.autoSelectFirst !== false;
  const currentValue = preferredValue || target.value;
  target.innerHTML = "";
  const empty = document.createElement("option");
  empty.value = "";
  empty.textContent = "Selecione";
  target.appendChild(empty);
  (state.payload?.employees || []).forEach((employee) => {
    const option = document.createElement("option");
    option.value = employee.id;
    option.textContent = `${employee.full_name} (${employee.employee_code})`;
    target.appendChild(option);
  });

  if (currentValue && [...target.options].some((option) => option.value === String(currentValue))) {
    target.value = String(currentValue);
  } else if (autoSelectFirst && state.payload?.employees?.length) {
    target.value = String(state.payload.employees[0].id);
  }
}

function setFormValues(form, values) {
  if (!form || !values || typeof values !== "object") return;
  Object.entries(values).forEach(([key, value]) => {
    const field = form.elements.namedItem(key);
    if (field) field.value = value ?? "";
  });
}

function renderRecentPunches() {
  const target = qs("#recent-punches");
  target.innerHTML = "";
  (state.payload?.recent_punches || []).forEach((punch) => {
    const emailAction = button("Enviar e-mail", async () => {
      const recipient = window.prompt("Enviar comprovante para qual e-mail?");
      if (!recipient) return;
      try {
        const result = await requestJson(`/api/receipts/${punch.id}/email`, {
          method: "POST",
          body: JSON.stringify({ recipient }),
        });
        window.alert(result.message || result.status);
      } catch (error) {
        window.alert(error.message);
      }
    });
    target.appendChild(
      createItem(
        `${punch.employee_name} | NSR ${String(punch.nsr).padStart(9, "0")}`,
        `${new Date(punch.punch_at).toLocaleString("pt-BR")} | ${punch.collector_label} | hash ${punch.hash_code.slice(0, 18)}...`,
        [link("PDF", `/api/receipts/${punch.id}.pdf`), emailAction]
      )
    );
  });
}

function renderBankSummaries() {
  const target = qs("#bank-summaries");
  if (!target) return;
  target.innerHTML = "";
  (state.payload?.bank_summaries || []).forEach((summary) => {
    const sign = summary.balance_minutes >= 0 ? "+" : "";
    target.appendChild(
      createItem(
        summary.employee_name,
        `Trabalhado: ${summary.worked_minutes} min | Previsto: ${summary.expected_minutes} min | Ajustes: ${summary.adjustment_minutes} min | Saldo: ${sign}${summary.balance_minutes} min`
      )
    );
  });
}

function renderCompliance() {
  const target = qs("#compliance-list");
  if (!target) return;
  target.innerHTML = "";
  (state.payload?.compliance || []).forEach((item) => {
    target.appendChild(createItem(item.item, `Status: ${item.status}`));
  });
}

function renderEmailIntegration() {
  const statusTarget = qs("#email-integration-status");
  const lastTarget = qs("#email-integration-last");
  const dispatchesTarget = qs("#email-dispatches");
  const emailIntegration = state.payload?.email_integration;
  if (!statusTarget || !lastTarget || !dispatchesTarget || !emailIntegration) return;

  const labelMap = {
    operational: "Operacional",
    ready: "Pronta para teste",
    pending_config: "Pendente de configuracao",
    error: "Erro no ultimo envio",
  };

  statusTarget.textContent = labelMap[emailIntegration.status] || emailIntegration.status || "desconhecido";
  lastTarget.textContent = emailIntegration.last_dispatch
    ? `${emailIntegration.last_dispatch.recipient} | ${emailIntegration.last_dispatch.status} | ${emailIntegration.last_dispatch.message}`
    : "Sem registros";

  dispatchesTarget.innerHTML = "";
  (emailIntegration.recent_dispatches || []).forEach((item) => {
    dispatchesTarget.appendChild(
      createItem(
        `${item.recipient} | ${item.status}`,
        `${item.created_at ? new Date(item.created_at).toLocaleString("pt-BR") : ""} | ${item.message || "sem mensagem"}`
      )
    );
  });
}

function renderCollections() {
  const holidaysTarget = qs("#holidays-list");
  const leavesTarget = qs("#leaves-list");
  const adjustmentsTarget = qs("#adjustments-list");
  const justificationsTarget = qs("#justifications-list");
  if (!holidaysTarget || !leavesTarget || !adjustmentsTarget || !justificationsTarget) return;

  holidaysTarget.innerHTML = "";
  leavesTarget.innerHTML = "";
  adjustmentsTarget.innerHTML = "";
  justificationsTarget.innerHTML = "";

  (state.payload?.holidays || []).forEach((item) => {
    holidaysTarget.appendChild(createItem(item.name, `${item.holiday_date} | ${item.scope}`));
  });
  (state.payload?.leaves || []).forEach((item) => {
    leavesTarget.appendChild(createItem(item.employee_name, `${item.leave_type} | ${item.start_date} ate ${item.end_date}`));
  });
  (state.payload?.adjustments || []).forEach((item) => {
    adjustmentsTarget.appendChild(createItem(item.employee_name, `${item.reference_date} | ${item.minutes_delta} min | ${item.reason}`));
  });
  (state.payload?.justifications || []).forEach((item) => {
    const actions = [link("PDF", `/api/justifications/${item.id}.pdf`)];
    if (item.attachment_original_name) {
      actions.push(link("Atestado", `/api/justifications/${item.id}/attachment`));
    }
    justificationsTarget.appendChild(
      createItem(
        `${item.employee_name} | ${item.occurrence_label}`,
        `${item.reference_date}${item.informed_time ? ` | ${item.informed_time}` : ""} | ${item.reason} | ${item.attachment_original_name || "sem atestado"} | ${item.signature_status}`,
        actions
      )
    );
  });
}

function applyJustificationPreset(type) {
  const typeField = qs("#justification-occurrence-type");
  const reasonField = qs("#justification-reason");
  const detailsField = qs("#justification-details");
  if (!typeField || !reasonField || !detailsField) return;

  if (type === "esquecimento") {
    typeField.value = "esquecimento";
    reasonField.value = "Esquecimento de batida";
    detailsField.value = "Funcionario informa que esqueceu de registrar uma ou mais batidas e solicita o lancamento da jornada efetivamente realizada.";
  } else if (type === "fora_horario") {
    typeField.value = "fora_horario";
    reasonField.value = "Ponto realizado fora do horario habitual";
    detailsField.value = "Funcionario informa que trabalhou fora do horario habitual e registra a justificativa da marcacao.";
  }
}

function renderEmployeesList() {
  const target = qs("#employees-list");
  if (!target) return;
  target.innerHTML = "";
  (state.payload?.employees || []).forEach((employee) => {
    const editAction = button("Editar", () => {
      setFormValues(qs("#employee-form"), employee);
      state.activeAdminPanel = "employee-card";
      updateAdminPanels();
    });
    target.appendChild(
      createItem(
        `${employee.full_name} (${employee.employee_code})`,
        `${employee.cpf} | ${employee.email || "sem e-mail"} | ${employee.department || "sem setor"}`,
        [editAction]
      )
    );
  });
}

async function saveMissingPunches(referenceDate, informedTime) {
  if (!state.hoursReport) return;
  const payload = {
    employee_id: state.hoursReport.employee.id,
    reference_date: referenceDate,
    informed_time: informedTime,
    period_start: state.hoursReport.period_start,
    period_end: state.hoursReport.period_end,
  };
  const result = await requestJson("/api/reports/hours/missing-punches", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  state.hoursReport = result.report;
  renderHoursReport();
}

function renderHoursReport() {
  const summary = qs("#rh-report-summary");
  const tableBody = qs("#rh-report-table-body");
  const title = qs("#hours-report-title");
  if (!summary || !tableBody) return;
  if (title) {
    title.textContent = isAdmin() ? "Espelho de Ponto" : "Minhas Batidas e Justificativas";
  }

  if (!state.hoursReport) {
    summary.textContent = isAdmin()
      ? "Selecione um funcionario para visualizar as batidas consolidadas do periodo."
      : "Selecione o periodo para visualizar suas batidas e justificativas.";
    tableBody.innerHTML = `
      <tr>
        <td colspan="10">Nenhum relatorio carregado.</td>
      </tr>
    `;
    return;
  }

  const { employee, period_start: periodStart, period_end: periodEnd, totals, rows } = state.hoursReport;
  summary.textContent = `${employee.full_name} | Periodo ${periodStart} ate ${periodEnd} | Trabalhado ${totals.worked_label} | Previsto ${totals.expected_label} | Ajustes ${totals.adjustment_label} | Saldo ${totals.balance_label}`;
  tableBody.innerHTML = "";
  rows.forEach((row) => {
    const tr = document.createElement("tr");
    [
      row.date,
      row.weekday_label,
      row.punches_label,
    ].forEach((value) => {
      const td = document.createElement("td");
      td.textContent = value;
      tr.appendChild(td);
    });

    const editableCell = document.createElement("td");
    const editableInput = document.createElement("input");
    editableInput.type = "text";
    editableInput.value = row.editable_times || "";
    editableInput.placeholder = "08:00 / 18:00";
    editableInput.dataset.referenceDate = row.date;
    editableCell.appendChild(editableInput);
    tr.appendChild(editableCell);

    [
      row.expected_label,
      row.worked_label,
      row.adjustment_label,
      row.balance_label,
    ].forEach((value) => {
      const td = document.createElement("td");
      td.textContent = value;
      tr.appendChild(td);
    });

    const notesCell = document.createElement("td");
    notesCell.textContent = row.notes;
    const certificateJustifications = (row.justifications || []).filter((item) => item.occurrence_type === "atestado_medico" || item.attachment_original_name);
    if (certificateJustifications.length) {
      const badge = document.createElement("strong");
      badge.className = "report-status";
      badge.textContent = "Atestado medico";
      notesCell.appendChild(badge);
      const links = document.createElement("div");
      links.className = "report-links";
      certificateJustifications.forEach((item) => {
        if (item.attachment_original_name) {
          links.appendChild(link(`Ver atestado: ${item.attachment_original_name}`, `/api/justifications/${item.id}/attachment`));
        }
      });
      if (links.childElementCount) {
        notesCell.appendChild(links);
      }
    }
    tr.appendChild(notesCell);

    const actionCell = document.createElement("td");
    const saveButton = button("Salvar", async () => {
      try {
        await saveMissingPunches(row.date, editableInput.value);
      } catch (error) {
        window.alert(error.message);
      }
    });
    actionCell.appendChild(saveButton);
    tr.appendChild(actionCell);
    tableBody.appendChild(tr);
  });
}

function ensureRhReportDefaults() {
  const startField = qs("#rh-report-start");
  const endField = qs("#rh-report-end");
  const employeeField = qs("#rh-report-employee");
  if (!startField || !endField || !employeeField) return;

  const today = new Date();
  if (!startField.value) {
    startField.value = localDateIso(new Date(today.getFullYear(), today.getMonth(), 1));
  }
  if (!endField.value) {
    endField.value = localDateIso(today);
  }
  if (isEmployee() && state.payload?.employees?.length) {
    employeeField.value = String(state.payload.employees[0].id);
  }
}

async function loadHoursReport(showErrors = true) {
  const form = qs("#rh-report-form");
  if (!form) return;
  const payload = asPayload(form);
  if (!payload.employee_id && state.payload?.employees?.length && (isEmployee() || isAdmin())) {
    payload.employee_id = String(state.payload.employees[0].id);
    const employeeField = qs("#rh-report-employee");
    if (employeeField) employeeField.value = payload.employee_id;
  }
  if (!payload.employee_id) {
    state.hoursReport = null;
    renderHoursReport();
    if (showErrors) {
      window.alert("Selecione um funcionario para consultar as batidas.");
    }
    return;
  }

  try {
    const params = new URLSearchParams(payload);
    state.hoursReport = await requestJson(`/api/reports/hours?${params.toString()}`);
    renderHoursReport();
  } catch (error) {
    state.hoursReport = null;
    renderHoursReport();
    if (showErrors) {
      window.alert(error.message);
    }
  }
}

async function refresh() {
  state.payload = await requestJson("/api/bootstrap");
  state.currentUser = state.payload.current_user || null;
  toggleAuthView();
  qs("#service-status").textContent = state.payload.config.service_status || "desconhecido";
  qs("#app-name").textContent = state.payload.config.app_name;
  fillEmployeeSelect("#punch-employee");
  fillEmployeeSelect("#leave-employee");
  fillEmployeeSelect("#adjustment-employee");
  fillEmployeeSelect("#justification-employee");
  fillEmployeeSelect("#medical-certificate-employee");
  fillEmployeeSelect("#rh-report-employee", "", { autoSelectFirst: true });
  setFormValues(qs("#settings-form"), state.payload.config);
  if (isEmployee()) {
    const employeeSelect = qs("#punch-employee");
    if (employeeSelect && state.payload.employees?.length) {
      employeeSelect.value = String(state.payload.employees[0].id);
    }
  }
  renderRecentPunches();
  renderBankSummaries();
  renderEmailIntegration();
  renderCompliance();
  renderCollections();
  renderEmployeesList();
  if (isAdmin() || isEmployee()) {
    ensureRhReportDefaults();
    await loadHoursReport(false);
  } else {
    state.hoursReport = null;
    renderHoursReport();
  }
}

function asPayload(form) {
  const data = Object.fromEntries(new FormData(form).entries());
  Object.keys(data).forEach((key) => {
    if (
      data[key] === "" &&
      !["cno_caepf", "developer_inpi", "legal_responsible_name", "legal_responsible_cpf", "technical_responsible_registry"].includes(key)
    ) {
      delete data[key];
    }
  });
  return data;
}

function togglePasswordRecovery(visible) {
  const panel = qs("#password-recovery-panel");
  if (!panel) return;
  panel.classList.toggle("hidden", !visible);
}

async function bindForms() {
  const loginRole = qs("#login-role");
  if (loginRole) {
    loginRole.addEventListener("change", updateLoginLabels);
    updateLoginLabels();
  }

  const loginForm = qs("#login-form");
  if (loginForm) {
    loginForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      try {
        const result = await requestJson("/api/auth/login", {
          method: "POST",
          body: JSON.stringify(asPayload(form)),
        });
        state.currentUser = result.current_user;
        togglePasswordRecovery(false);
        qs("#login-result").textContent = "Login realizado com sucesso.";
        await refresh();
      } catch (error) {
        qs("#login-result").textContent = error.message;
      }
    });
  }

  const forgotPasswordToggle = qs("#forgot-password-toggle");
  if (forgotPasswordToggle) {
    forgotPasswordToggle.addEventListener("click", () => {
      togglePasswordRecovery(true);
    });
  }

  const closePasswordRecovery = qs("#close-password-recovery");
  if (closePasswordRecovery) {
    closePasswordRecovery.addEventListener("click", () => {
      togglePasswordRecovery(false);
    });
  }

  const recoveryRequestForm = qs("#password-recovery-request-form");
  if (recoveryRequestForm) {
    recoveryRequestForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      const resultTarget = qs("#password-recovery-result");
      try {
        const result = await requestJson("/api/auth/password-recovery/request", {
          method: "POST",
          body: JSON.stringify(asPayload(form)),
        });
        togglePasswordRecovery(true);
        qs("#password-recovery-confirm-cpf").value = qs("#password-recovery-cpf").value;
        let message = `${result.message} Destino: ${result.recipient_hint}`;
        if (result.debug_reset_code) {
          message += ` | Codigo de teste: ${result.debug_reset_code}`;
        }
        resultTarget.textContent = message;
      } catch (error) {
        resultTarget.textContent = error.message;
      }
    });
  }

  const recoveryConfirmForm = qs("#password-recovery-confirm-form");
  if (recoveryConfirmForm) {
    recoveryConfirmForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      const resultTarget = qs("#password-recovery-result");
      try {
        await requestJson("/api/auth/password-recovery/confirm", {
          method: "POST",
          body: JSON.stringify(asPayload(form)),
        });
        resultTarget.textContent = "Senha redefinida com sucesso. Agora o funcionario pode entrar com a nova senha.";
        qs("#login-role").value = "employee";
        updateLoginLabels();
        qs("#login-username").value = qs("#password-recovery-confirm-cpf").value;
        form.reset();
        togglePasswordRecovery(false);
      } catch (error) {
        resultTarget.textContent = error.message;
      }
    });
  }

  document.querySelectorAll("[data-admin-menu-item]").forEach((button) => {
    button.addEventListener("click", () => {
      state.activeAdminPanel = button.dataset.adminMenuItem;
      updateAdminPanels();
    });
  });

  const logoutButton = qs("#logout-button");
  if (logoutButton) {
    logoutButton.addEventListener("click", async () => {
      await requestJson("/api/auth/logout", { method: "POST" });
      state.currentUser = null;
      state.payload = null;
      state.hoursReport = null;
      toggleAuthView();
      togglePasswordRecovery(false);
      renderHoursReport();
      qs("#login-result").textContent = "Sessao encerrada.";
    });
  }

  const punchForm = qs("#punch-form");
  if (punchForm) {
    punchForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      try {
        const result = await requestJson("/api/punches", {
          method: "POST",
          body: JSON.stringify(asPayload(form)),
        });
        const autoEmailSuffix = result.auto_email?.status
          ? ` | E-mail automatico: ${result.auto_email.status}`
          : "";
        qs("#punch-result").textContent = `${result.message} Hash ${result.punch.hash_code}${autoEmailSuffix}`;
        form.reset();
        await refresh();
      } catch (error) {
        qs("#punch-result").textContent = error.message;
      }
    });
  }

  [
    ["#settings-form", "/api/settings"],
    ["#employee-form", "/api/employees"],
    ["#holiday-form", "/api/holidays"],
    ["#leave-form", "/api/leaves"],
    ["#adjustment-form", "/api/bank-adjustments"],
    ["#justification-form", "/api/justifications"],
    ["#medical-certificate-form", "/api/medical-certificates"],
  ].forEach(([selector, url]) => {
    const form = qs(selector);
    if (!form) return;
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const currentForm = event.currentTarget;
      try {
        if (selector === "#justification-form" || selector === "#medical-certificate-form") {
          await requestForm(url, new FormData(currentForm));
        } else {
          await requestJson(url, {
            method: "POST",
            body: JSON.stringify(asPayload(currentForm)),
          });
        }
        if (selector === "#justification-form") {
          qs("#justification-result").textContent = "Justificativa registrada com sucesso.";
        }
        if (selector === "#medical-certificate-form") {
          qs("#medical-certificate-result").textContent = "Atestado registrado com sucesso.";
        }
        currentForm.reset();
        await refresh();
      } catch (error) {
        if (selector === "#justification-form") {
          qs("#justification-result").textContent = error.message;
        } else if (selector === "#medical-certificate-form") {
          qs("#medical-certificate-result").textContent = error.message;
        } else {
          window.alert(error.message);
        }
      }
    });
  });

  const presetEsquecimento = qs("#preset-justification-esquecimento");
  if (presetEsquecimento) {
    presetEsquecimento.addEventListener("click", () => {
      applyJustificationPreset("esquecimento");
    });
  }

  const presetForaHorario = qs("#preset-justification-fora-horario");
  if (presetForaHorario) {
    presetForaHorario.addEventListener("click", () => {
      applyJustificationPreset("fora_horario");
    });
  }

  const employeeFormReset = qs("#employee-form-reset");
  if (employeeFormReset) {
    employeeFormReset.addEventListener("click", () => {
      qs("#employee-form")?.reset();
      const idField = qs("#employee-form input[name='id']");
      if (idField) idField.value = "";
    });
  }

  const emailTestForm = qs("#email-test-form");
  if (emailTestForm) {
    emailTestForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      try {
        const result = await requestJson("/api/integrations/email/test", {
          method: "POST",
          body: JSON.stringify(asPayload(form)),
        });
        window.alert(`Teste enviado: ${result.status} - ${result.message}`);
        await refresh();
      } catch (error) {
        window.alert(error.message);
      }
    });
  }

  const rhReportForm = qs("#rh-report-form");
  if (rhReportForm) {
    rhReportForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      await loadHoursReport(true);
    });
  }

  const rhReportEmployee = qs("#rh-report-employee");
  if (rhReportEmployee) {
    rhReportEmployee.addEventListener("change", async () => {
      if (isAdmin() && rhReportEmployee.value) {
        await loadHoursReport(true);
      } else if (isAdmin()) {
        state.hoursReport = null;
        renderHoursReport();
      }
    });
  }

  const downloadHoursReportButton = qs("#download-hours-report");
  if (downloadHoursReportButton) {
    downloadHoursReportButton.addEventListener("click", () => {
      const form = qs("#rh-report-form");
      const payload = asPayload(form);
      if (!payload.employee_id) {
        window.alert("Selecione um funcionario para gerar o documento.");
        return;
      }
      const params = new URLSearchParams(payload);
      window.open(`/api/reports/hours.pdf?${params.toString()}`, "_blank");
    });
  }

  const downloadAfdButton = qs("#download-afd");
  if (downloadAfdButton) {
    downloadAfdButton.addEventListener("click", () => {
      const params = new URLSearchParams(asPayload(qs("#afd-form")));
      window.open(`/api/afd.txt?${params.toString()}`, "_blank");
    });
  }

  const downloadFiscalButton = qs("#download-fiscal");
  if (downloadFiscalButton) {
    downloadFiscalButton.addEventListener("click", () => {
      const params = new URLSearchParams(asPayload(qs("#afd-form")));
      window.open(`/api/fiscalizacao.zip?${params.toString()}`, "_blank");
    });
  }
}

document.addEventListener("DOMContentLoaded", async () => {
  await bindForms();
  try {
    const auth = await requestJson("/api/auth/me");
    state.currentUser = auth.current_user;
    toggleAuthView();
    if (auth.authenticated) {
      await refresh();
    }
  } catch (error) {
    state.currentUser = null;
    toggleAuthView();
  }
});
