const STORAGE_KEY = "tatooStudioDB";

function loadDB() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY)) || {};
  } catch (error) {
    console.error(error);
    return {};
  }
}

function saveDB(db) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(db));
}

function formatDate(value) {
  if (!value) return "-";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "-" : date.toLocaleString("pt-BR");
}

function formatDateOnly(value) {
  if (!value) return "-";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleDateString("pt-BR");
}

function currencyBRL(value) {
  return Number(value || 0).toLocaleString("pt-BR", {
    style: "currency",
    currency: "BRL",
  });
}

function escapeHTML(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function safeLine(value, fallback = "Nao informado") {
  const clean = String(value || "").trim();
  return clean ? escapeHTML(clean) : `<span class="document-blank">${escapeHTML(fallback)}</span>`;
}

function signatureModeLabel(mode) {
  return mode === "external" ? "Assinatura digital externa" : "Assinatura na tela";
}

const contractorProfile = {
  studioName: "Grenckelvin Tattoo Artist",
  cnpj: "30.853.085/0001-11",
  address: "Rua Sao Paulo, no 379, sala 5",
  postalCode: "86730-000",
  city: "Astorga",
  state: "PR",
  representative: "Grenckelvin Fernandes Almeida",
  representativeCpf: "092.091.029-75",
};

const db = loadDB();
const params = new URLSearchParams(window.location.search);
const consentId = params.get("consentId");

const consent = Array.isArray(db.consents)
  ? db.consents.find((item) => item.id === consentId)
  : null;

const client = consent && Array.isArray(db.clients)
  ? db.clients.find((item) => item.id === consent.clientId)
  : null;

const session = consent && Array.isArray(db.sessions)
  ? db.sessions.find((item) => item.id === consent.sessionId)
  : null;

const consentBox = document.querySelector("#signature-consent");
const statusBox = document.querySelector("#signature-status");
const form = document.querySelector("#signature-form");
const description = document.querySelector("#signature-description");
const badge = document.querySelector("#signature-badge");
const signerNameInput = document.querySelector("#signer-name");
const signerDocumentInput = document.querySelector("#signer-document");
const signatureModeInput = document.querySelector("#signature-mode");
const imageConsentInput = document.querySelector("#image-consent");
const annexConfirmationInput = document.querySelector("#annex-confirmation");
const signerConfirmationInput = document.querySelector("#signer-confirmation");
const modeNote = document.querySelector("#signature-mode-note");
const canvasSection = document.querySelector("#signature-canvas-section");
const canvas = document.querySelector("#signature-canvas");
const clearButton = document.querySelector("#clear-signature");
const submitButton = document.querySelector("#save-signature");
const downloadLink = document.querySelector("#download-contract");
const ctx = canvas.getContext("2d");

function setStatus(message, type = "") {
  statusBox.textContent = message;
  statusBox.className = `status-box ${type}`.trim();
}

function resizeCanvas() {
  const ratio = Math.max(window.devicePixelRatio || 1, 1);
  const rect = canvas.getBoundingClientRect();
  canvas.width = rect.width * ratio;
  canvas.height = rect.height * ratio;
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.strokeStyle = "#2f2016";
  ctx.lineWidth = 2.5;
}

function renderChecklistItem(label, active) {
  return `
    <li class="document-check-item">
      <span class="check-mark">${active ? "X" : ""}</span>
      <span>${escapeHTML(label)}</span>
    </li>
  `;
}

function renderSummaryGrid(items) {
  return items.map((item) => `
    <div class="summary-row">
      <span>${escapeHTML(item.label)}</span>
      <strong>${safeLine(item.value)}</strong>
    </div>
  `).join("");
}

function clientSummaryLines() {
  return [
    { label: "Nome", value: client?.name },
    { label: "Nascimento", value: formatDateOnly(client?.birthDate) },
    { label: "RG", value: client?.rg },
    { label: "CPF", value: client?.document },
    { label: "Endereco", value: client?.address },
    { label: "Cidade / UF", value: client ? `${client.city || "Nao informado"} / ${client.state || "Nao informado"}` : "" },
    { label: "Telefone", value: client?.phone },
    { label: "Redes sociais", value: client?.social },
  ];
}

function contractLocation() {
  const city = client?.city || "________________";
  const state = client?.state || "__";
  return `${escapeHTML(city)} / ${escapeHTML(state)}`;
}

function setDownloadLink(contractFile) {
  if (contractFile?.url) {
    downloadLink.href = contractFile.url;
    downloadLink.download = contractFile.fileName || "contrato.pdf";
    downloadLink.classList.remove("hidden");
    return;
  }

  downloadLink.href = "#";
  downloadLink.removeAttribute("download");
  downloadLink.classList.add("hidden");
}

function updateModeUI() {
  const external = signatureModeInput.value === "external";
  canvasSection.classList.toggle("is-hidden", external);
  clearButton.disabled = external;
  submitButton.textContent = external ? "Gerar PDF para assinatura digital" : "Salvar assinatura e gerar PDF";
  modeNote.textContent = external
    ? "Esse modo gera o PDF, salva uma copia em contratos/ no servidor e baixa o arquivo para assinatura externa em Gov.br ou certificado digital."
    : "A assinatura na tela salva o desenho, gera o PDF e guarda o arquivo em contratos/ no servidor.";
  modeNote.className = `mode-note ${external ? "external" : "draw"}`;
}

function restoreSignatureImage(dataUrl) {
  if (!dataUrl) return;
  const image = new Image();
  image.onload = () => {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    const ratio = Math.min(canvas.width / image.width, canvas.height / image.height);
    const drawWidth = image.width * ratio;
    const drawHeight = image.height * ratio;
    const offsetX = (canvas.width - drawWidth) / 2;
    const offsetY = (canvas.height - drawHeight) / 2;
    ctx.drawImage(image, offsetX, offsetY, drawWidth, drawHeight);
    hasStroke = true;
  };
  image.src = dataUrl;
}

function buildSignatureDraft() {
  const signatureMode = signatureModeInput.value === "external" ? "external" : "draw";
  return {
    signerName: signerNameInput.value.trim(),
    signerDocument: signerDocumentInput.value.trim(),
    signatureMode,
    imageConsent: imageConsentInput.value,
    annexConfirmation: annexConfirmationInput.value.trim(),
    confirmation: signerConfirmationInput.value.trim(),
    signedAt: new Date().toISOString(),
    imageDataUrl: signatureMode === "draw" ? canvas.toDataURL("image/png") : "",
    userAgent: navigator.userAgent,
  };
}

function buildContractPayload(signatureDraft) {
  return {
    consentId: consent.id,
    termType: consent.termType,
    createdAt: consent.createdAt,
    notes: consent.notes || "",
    healthNotes: client?.healthNotes || "",
    client: {
      name: client?.name || "",
      birthDate: client?.birthDate || "",
      rg: client?.rg || "",
      document: client?.document || "",
      address: client?.address || "",
      city: client?.city || "",
      state: client?.state || "",
      phone: client?.phone || "",
      social: client?.social || "",
    },
    contractor: { ...contractorProfile },
    session: {
      description: session?.description || "",
      bodyArea: session?.bodyArea || "",
      artist: session?.artist || "",
      appointmentAt: session?.appointmentAt || "",
      budget: Number(session?.budget || 0),
    },
    signature: signatureDraft,
  };
}

async function requestContractPdf(signatureDraft) {
  const response = await fetch("/api/tatoo/contracts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(buildContractPayload(signatureDraft)),
  });

  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || "Nao foi possivel gerar o PDF do contrato.");
  }

  return payload.contract;
}

function renderContract() {
  const createdAt = formatDate(consent?.createdAt);
  const sessionDate = formatDate(session?.appointmentAt);
  const budget = session?.budget ? currencyBRL(session.budget) : "Nao informado";
  const healthText = client?.healthNotes || "Sem observacoes adicionais registradas na ficha do cliente.";
  const signatureData = consent?.signature || null;
  const customClauses = consent?.notes
    ? `
      <section class="document-section">
        <h2>Clausulas adicionais do atendimento</h2>
        <p>${escapeHTML(consent.notes)}</p>
      </section>
    `
    : "";
  const signatureInfo = signatureData
    ? `
      <div class="document-note success-note">
        ${signatureModeLabel(signatureData.signatureMode)} registrada em ${escapeHTML(formatDate(signatureData.signedAt))}.
        ${signatureData.contractFile?.fileName ? `PDF salvo em contratos/: ${escapeHTML(signatureData.contractFile.fileName)}.` : ""}
      </div>
    `
    : "";
  const imageConsent = signatureData?.imageConsent || "A definir na assinatura";
  const annexConfirmation = signatureData?.annexConfirmation || "Pendente de confirmacao na assinatura";
  const contractFile = signatureData?.contractFile;
  const contractFileNote = contractFile?.url
    ? `
      <div class="document-note">
        PDF salvo no servidor: <a href="${contractFile.url}" target="_blank" rel="noreferrer">${escapeHTML(contractFile.fileName || "Baixar contrato")}</a>
      </div>
    `
    : "";

  return `
    <header class="document-hero">
      <div>
        <p class="document-kicker">${escapeHTML(consent?.termType || "Contrato de tatuagem")}</p>
        <h2>Contrato de servico de pigmentacao artificial permanente</h2>
        <p>
          Instrumento particular baseado no documento disponibilizado pelo estudio, com adaptacao para leitura digital,
          preenchimento automatico e assinatura touch ou externa.
        </p>
      </div>
      <div class="document-stamp">
        <span>TERMO</span>
        <strong>${escapeHTML(contractorProfile.city)} ${escapeHTML(contractorProfile.state)}</strong>
        <small>Criado em ${escapeHTML(createdAt)}</small>
      </div>
    </header>

    ${signatureInfo}
    ${contractFileNote}

    <section class="document-section">
      <h2>Partes</h2>
      <div class="document-grid two">
        <article class="document-card">
          <p class="document-label">Contratante</p>
          ${renderSummaryGrid(clientSummaryLines())}
        </article>
        <article class="document-card">
          <p class="document-label">Contratado</p>
          <div class="summary-row">
            <span>Estudio</span>
            <strong>${escapeHTML(contractorProfile.studioName)}</strong>
          </div>
          <div class="summary-row">
            <span>CNPJ</span>
            <strong>${escapeHTML(contractorProfile.cnpj)}</strong>
          </div>
          <div class="summary-row">
            <span>Endereco</span>
            <strong>${escapeHTML(contractorProfile.address)}, CEP ${escapeHTML(contractorProfile.postalCode)}</strong>
          </div>
          <div class="summary-row">
            <span>Cidade / UF</span>
            <strong>${escapeHTML(contractorProfile.city)} / ${escapeHTML(contractorProfile.state)}</strong>
          </div>
          <div class="summary-row">
            <span>Responsavel</span>
            <strong>${escapeHTML(contractorProfile.representative)}</strong>
          </div>
          <div class="summary-row">
            <span>CPF</span>
            <strong>${escapeHTML(contractorProfile.representativeCpf)}</strong>
          </div>
        </article>
      </div>
    </section>

    <section class="document-section">
      <h2>Resumo do atendimento</h2>
      <div class="document-grid three">
        <article class="document-card compact">
          <p class="document-label">Sessao</p>
          <strong>${safeLine(session?.description)}</strong>
          <small>Area: ${safeLine(session?.bodyArea)}</small>
        </article>
        <article class="document-card compact">
          <p class="document-label">Agenda</p>
          <strong>${escapeHTML(sessionDate)}</strong>
          <small>Profissional: ${safeLine(session?.artist)}</small>
        </article>
        <article class="document-card compact">
          <p class="document-label">Valor previsto</p>
          <strong>${escapeHTML(budget)}</strong>
          <small>Forma de pagamento combinada no dia da sessao.</small>
        </article>
      </div>
    </section>

    <section class="document-section">
      <h2>Clausula primeira - do objeto</h2>
      <p>
        O presente instrumento tem como objeto a prestacao de servicos de pigmentacao artificial permanente da pele
        (tatuagem), consistente na pigmentacao exogena introduzida fisicamente na camada dermica ou subepidermica da pele,
        com resultado permanente, para fins de embelezamento ou correcao estetica.
      </p>
      <p>
        Para a realizacao do procedimento, o contratante declara que forneceu ou fornecera historico de saude suficiente
        para a avaliacao do atendimento, reconhecendo que a ficha de anamnese integra este contrato.
      </p>
      <p>
        O contratante tambem declara ter recebido previamente, por meios digitais, as informacoes de agendamento e
        funcionamento do atendimento, equivalentes ao Anexo II.
      </p>
    </section>

    <section class="document-section">
      <h2>Clausula segunda - saude e higiene do estudio</h2>
      <p>
        Os produtos utilizados no procedimento e na higienizacao do ambiente seguem as normas sanitarias aplicaveis,
        incluindo materiais registrados e acondicionados de forma adequada. Tintas sao fracionadas por cliente, sobras sao
        descartadas como residuo infectante e liquidos sao tratados para reduzir risco de contaminacao.
      </p>
      <p>
        Materiais nao descartaveis passam por limpeza, desinfeccao e ou esterilizacao, enquanto luvas, agulhas, laminas e
        itens equivalentes sao de uso unico e descartavel.
      </p>
    </section>

    <section class="document-section">
      <h2>Clausula terceira - valor e vigencia</h2>
      <p>
        O servico sera executado por sessoes. Para este atendimento, o valor previsto registrado no sistema e
        <strong>${escapeHTML(budget)}</strong>, podendo haver ajuste apenas se houver mudanca relevante de escopo, desenho
        ou cronograma previamente combinada entre as partes.
      </p>
      <p>
        O pagamento deve ocorrer no dia do procedimento, em especie, cartao ou PIX, observada eventual taxa de agendamento
        ja combinada com o estudio.
      </p>
    </section>

    <section class="document-section">
      <h2>Obrigacoes do contratante</h2>
      <ul class="document-list">
        <li>Zelar pela propria pele e seguir as orientacoes de preparo e cicatrizacao fornecidas pelo profissional.</li>
        <li>Informar alergias, medicamentos, condicoes cutaneas e qualquer dado de saude que possa impactar o procedimento.</li>
        <li>Justificar ausencia com antecedencia minima de 72 horas, quando aplicavel.</li>
        <li>Comparecer alimentado e com documento com foto no dia agendado.</li>
      </ul>
    </section>

    <section class="document-section">
      <h2>Obrigacoes do contratado</h2>
      <ul class="document-list">
        <li>Executar o objeto do contrato buscando excelencia tecnica, seguranca e higiene.</li>
        <li>Apresentar materiais novos ou esterilizados antes da sessao e descartar corretamente os residuos ao final.</li>
        <li>Orientar o cliente sobre preparo, atendimento e cuidados posteriores ao procedimento.</li>
      </ul>
    </section>

    <section class="document-section">
      <h2>Clausula sexta - responsabilidade</h2>
      <p>
        O contratante declara que teve conhecimento dos cuidados anteriores, durante e posteriores ao procedimento, sendo de
        sua responsabilidade seguir essas orientacoes para alcancar o resultado esperado. O estudio nao podera ser
        responsabilizado por informacoes omitidas pelo cliente sobre alergias, problemas cutaneos, uso de medicacao ou
        outras condicoes que interfiram no resultado final.
      </p>
    </section>

    <section class="document-section">
      <h2>Clausula setima - uso de imagem</h2>
      <p>
        O documento-base preve a possibilidade de uso de fotos e reproducoes graficas do trabalho para portfolio, redes
        sociais, website e demais materiais publicitarios do estudio, desde que de forma respeitosa e vinculada ao servico.
      </p>
      <p>
        Situacao atual deste termo: <strong>${escapeHTML(imageConsent)}</strong>.
      </p>
    </section>

    <section class="document-section">
      <h2>Clausula oitava - rescisao e reagendamento</h2>
      <p>
        O contrato pode ser rescindido por qualquer das partes, respeitadas as clausulas aqui descritas e as regras de
        agendamento. O estudio tambem podera cancelar ou interromper a sessao diante de ausencia injustificada, atraso
        excessivo, inercia prolongada, uso de alcool ou drogas, ou condicoes da pele que inviabilizem a execucao segura do trabalho.
      </p>
    </section>

    <section class="document-section">
      <h2>Boa-fe, alteracoes e foro</h2>
      <p>
        As partes afirmam que celebram este instrumento de boa-fe, com conhecimento previo de suas clausulas, que somente
        poderao ser alteradas por ajuste expresso entre as partes. Fica eleito o foro da comarca de
        ${escapeHTML(contractorProfile.city)} - ${escapeHTML(contractorProfile.state)} para dirimir eventuais controversias.
      </p>
    </section>

    ${customClauses}

    <section class="document-section">
      <h2>Anexo I - ficha de anamnese</h2>
      <div class="document-grid two">
        <article class="document-card">
          <ul class="document-checklist">
            ${renderChecklistItem("Esta em tratamento medico", false)}
            ${renderChecklistItem("Doenca infectocontagiosa", false)}
            ${renderChecklistItem("Cirurgia recente no local", false)}
            ${renderChecklistItem("Diabete", false)}
            ${renderChecklistItem("Possui alguma alergia", /alerg/i.test(healthText))}
            ${renderChecklistItem("Historico de convulsoes", false)}
            ${renderChecklistItem("Dormiu bem na ultima noite", false)}
          </ul>
        </article>
        <article class="document-card">
          <ul class="document-checklist">
            ${renderChecklistItem("Gestante", false)}
            ${renderChecklistItem("Amamentando", false)}
            ${renderChecklistItem("Medicacao continua", /medic/i.test(healthText))}
            ${renderChecklistItem("Problema de pele ou cicatrizacao", /pele|cicatriz/i.test(healthText))}
            ${renderChecklistItem("Historico de queloide", /queloid/i.test(healthText))}
            ${renderChecklistItem("Alimentou-se nas ultimas horas", false)}
            ${renderChecklistItem("Fumante", false)}
          </ul>
        </article>
      </div>
      <div class="document-note">
        Observacoes de saude registradas: ${escapeHTML(healthText)}
      </div>
      <p class="document-footnote">
        Declaracao: as informacoes acima devem ser verdadeiras, nao cabendo ao profissional responsabilidade por dados
        omitidos nesta avaliacao.
      </p>
    </section>

    <section class="document-section">
      <h2>Anexo II - termo de agendamento e atendimento</h2>
      <ol class="document-numbered">
        <li>O estudio realiza procedimentos com rigor de higiene, usando materiais descartaveis e ou esterilizados.</li>
        <li>O orcamento pode variar conforme ideia, tamanho, cores, local da tatuagem e grau de complexidade da arte.</li>
        <li>O agendamento so e confirmado apos pagamento do sinal; reagendamentos dependem de antecedencia e disponibilidade.</li>
        <li>Alteracoes no desenho ou no local tatuado devem ser comunicadas antes da data marcada.</li>
        <li>No dia do procedimento, o cliente deve comparecer alimentado, com documento com foto, sem uso de alcool ou drogas.</li>
        <li>Os cuidados de cicatrizacao serao repassados pelo profissional, e intercorrencias por descuido podem gerar novo custo.</li>
      </ol>
      <div class="document-note">
        Declaracao de recebimento: ${safeLine(annexConfirmation)}
      </div>
    </section>

    <section class="document-section final-section">
      <h2>Fechamento</h2>
      <p>
        Por ser expressao da verdade e da livre manifestacao de vontade, o presente instrumento e assinado digitalmente.
      </p>
      <div class="document-signatures">
        <div class="signature-line-block">
          <span>${safeLine(signatureData?.signerName || client?.name)}</span>
          <small>Contratante</small>
        </div>
        <div class="signature-line-block">
          <span>${escapeHTML(contractorProfile.representative)}</span>
          <small>Tatuador / representante do estudio</small>
        </div>
      </div>
      <small class="document-footnote">
        Local do contratante: ${contractLocation()}.
      </small>
    </section>
  `;
}

let drawing = false;
let hasStroke = false;
let lastX = 0;
let lastY = 0;

function pointFromEvent(event) {
  const rect = canvas.getBoundingClientRect();
  return {
    x: event.clientX - rect.left,
    y: event.clientY - rect.top,
  };
}

function startDrawing(event) {
  if (signatureModeInput.value === "external") return;
  drawing = true;
  const point = pointFromEvent(event);
  lastX = point.x;
  lastY = point.y;
}

function draw(event) {
  if (!drawing) return;
  const point = pointFromEvent(event);
  ctx.beginPath();
  ctx.moveTo(lastX, lastY);
  ctx.lineTo(point.x, point.y);
  ctx.stroke();
  lastX = point.x;
  lastY = point.y;
  hasStroke = true;
}

function stopDrawing() {
  drawing = false;
}

canvas.addEventListener("pointerdown", startDrawing);
canvas.addEventListener("pointermove", draw);
canvas.addEventListener("pointerup", stopDrawing);
canvas.addEventListener("pointerleave", stopDrawing);
canvas.addEventListener("pointercancel", stopDrawing);

clearButton.addEventListener("click", () => {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  hasStroke = false;
  setStatus("Assinatura limpa.");
});

signatureModeInput.addEventListener("change", updateModeUI);

downloadLink.addEventListener("click", (event) => {
  if (downloadLink.classList.contains("hidden")) {
    event.preventDefault();
  }
});

if (!consent) {
  description.textContent = "Nenhum consentimento foi encontrado. Gere o termo na pagina principal e abra este link novamente.";
  badge.textContent = "Link invalido";
  consentBox.innerHTML = `
    <div class="document-empty">
      <strong>Consentimento nao localizado</strong>
      <p>Use um link no formato <code>signature.html?consentId=ID_DO_TERMO</code>.</p>
    </div>
  `;
  form.style.display = "none";
} else {
  const savedSignature = consent.signature || null;
  signerNameInput.value = savedSignature?.signerName || client?.name || "";
  signerDocumentInput.value = savedSignature?.signerDocument || client?.document || "";
  signatureModeInput.value = savedSignature?.signatureMode || "draw";
  imageConsentInput.value = savedSignature?.imageConsent || "Autorizo";
  annexConfirmationInput.value = savedSignature?.annexConfirmation || annexConfirmationInput.value;
  signerConfirmationInput.value = savedSignature?.confirmation || signerConfirmationInput.value;
  description.textContent = "Leia o contrato completo abaixo, valide os dados do cliente, escolha o modo de assinatura e gere o PDF final.";
  badge.textContent = consent.termType || "Contrato";
  updateModeUI();
  setDownloadLink(savedSignature?.contractFile || null);
  consentBox.innerHTML = renderContract();
  if (savedSignature?.signatureMode === "draw" && savedSignature?.imageDataUrl) {
    setTimeout(() => restoreSignatureImage(savedSignature.imageDataUrl), 0);
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();

  if (!consent) {
    setStatus("Consentimento nao encontrado.", "error");
    return;
  }

  const signatureMode = signatureModeInput.value === "external" ? "external" : "draw";
  if (signatureMode === "draw" && !hasStroke) {
    setStatus("A assinatura ainda nao foi desenhada.", "error");
    return;
  }

  const currentDB = loadDB();
  const targetConsent = currentDB.consents?.find((item) => item.id === consent.id);

  if (!targetConsent) {
    setStatus("O consentimento nao existe mais na base local.", "error");
    return;
  }

  submitButton.disabled = true;
  setStatus(signatureMode === "external" ? "Gerando PDF para assinatura digital externa..." : "Salvando assinatura e gerando PDF...", "");

  try {
    const signatureDraft = buildSignatureDraft();
    const contractFile = await requestContractPdf(signatureDraft);

    targetConsent.signature = {
      ...signatureDraft,
      contractFile,
    };

    saveDB(currentDB);
    consent.signature = targetConsent.signature;
    setDownloadLink(contractFile);
    consentBox.innerHTML = renderContract();

    if (signatureMode === "external" && contractFile?.url) {
      downloadLink.click();
      setStatus("PDF gerado, salvo em contratos/ e baixado para assinatura externa.", "success");
    } else {
      setStatus("Assinatura salva e PDF arquivado em contratos/ com sucesso.", "success");
    }
  } catch (error) {
    console.error(error);
    setStatus(error.message || "Nao foi possivel gerar o PDF do contrato.", "error");
  } finally {
    submitButton.disabled = false;
  }
});

window.addEventListener("resize", resizeCanvas);
resizeCanvas();
