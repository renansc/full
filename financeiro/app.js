/* =========================
   Gestão Financeira (JSON + OFX + AP/AR)
   - LocalStorage como banco (JSON)
   - Importação OFX
   - Conciliação: Banco↔Lançamento e Banco↔Título (AP/AR)
   - Contas a pagar/receber com centro de custo e anexos
========================= */

const LS_KEY = "gf_v1_data";
const remoteSync = window.RemoteStoreClient
  ? window.RemoteStoreClient.create({
      storeId: "financeiro-nanotech",
      normalize: migrate,
      onError: (error, source) => {
        console.warn("Financeiro sync error:", source, error);
      }
    })
  : null;

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

/* ---------- Estado ---------- */
let state = loadState();
let financeAiDiagState = {
  loading: false,
  loaded: false,
  error: "",
  data: null,
  lastLoadedAt: 0
};

/* ---------- Util ---------- */
function uid(prefix="id"){
  return prefix + "_" + Math.random().toString(16).slice(2) + "_" + Date.now().toString(16);
}
function brl(n){
  const v = Number(n || 0);
  return v.toLocaleString("pt-BR",{style:"currency",currency:"BRL"});
}
function toISODate(d){
  const dt = new Date(d);
  const y = dt.getFullYear();
  const m = String(dt.getMonth()+1).padStart(2,"0");
  const da = String(dt.getDate()).padStart(2,"0");
  return `${y}-${m}-${da}`;
}
function formatDateTime(value){
  if(!value) return "-";
  const dt = new Date(value);
  if(Number.isNaN(dt.getTime())) return String(value);
  return dt.toLocaleString("pt-BR");
}
function parseISODate(s){
  const [y,m,d] = s.split("-").map(Number);
  return new Date(y, m-1, d);
}
function clamp(v,min,max){ return Math.max(min, Math.min(max, v)); }
function addMonthsISO(baseISO, months){
  const base = parseISODate(baseISO);
  const day = base.getDate();
  const target = new Date(base.getFullYear(), base.getMonth() + months + 1, 0);
  const finalDay = Math.min(day, target.getDate());
  return toISODate(new Date(target.getFullYear(), target.getMonth(), finalDay));
}
function splitAmount(totalValue, parts){
  const totalCents = Math.round(Number(totalValue || 0) * 100);
  const qtd = clamp(Number(parts || 1), 1, 999);
  const base = Math.floor(totalCents / qtd);
  let remainder = totalCents - (base * qtd);
  return Array.from({ length: qtd }, () => {
    const cents = base + (remainder > 0 ? 1 : 0);
    remainder = Math.max(0, remainder - 1);
    return cents / 100;
  });
}
function filePreviewSrc(anexo){
  return anexo?.dataUrl || anexo?.url || "";
}
async function requestJson(url, options={}){
  const response = await fetch(url, {
    cache: "no-store",
    headers: { Accept: "application/json", ...(options.headers || {}) },
    ...options
  });

  let payload = null;
  if(response.status !== 204){
    const text = await response.text();
    if(text){
      try{
        payload = JSON.parse(text);
      }catch{
        payload = { error: text };
      }
    }
  }

  if(!response.ok){
    throw new Error(payload?.error || `Falha na requisição (${response.status}).`);
  }
  return payload;
}
function getContaNome(contaId){
  return state.contas.find(c => c.id === contaId)?.nome || "";
}
async function uploadTituloAttachment(file, titulo){
  const formData = new FormData();
  formData.set("file", file);
  formData.set("attachmentId", uid("anx"));
  formData.set("vencimento", titulo.vencimento || "");
  formData.set("contaNome", getContaNome(titulo.contaId));
  formData.set("pessoa", titulo.pessoa || "");
  formData.set("descricao", titulo.desc || "");

  const payload = await requestJson("/api/finance/attachments", {
    method: "POST",
    body: formData
  });
  return payload?.attachment || null;
}
async function removeTituloAttachmentFile(anexo){
  if(!anexo?.path) return;
  await fetch(`/api/finance/attachments?path=${encodeURIComponent(anexo.path)}`, {
    method: "DELETE",
    cache: "no-store"
  });
}
async function triggerFinanceReminders({silent=false}={}){
  const status = $("#statusAvisos");
  try{
    const payload = await requestJson("/api/finance/reminders/run", { method: "POST" });
    if(status) status.textContent = payload?.message || "Avisos processados.";
    if(!silent && payload?.message) alert(payload.message);
  }catch(err){
    if(status) status.textContent = err?.message || "Nao foi possivel processar os avisos.";
    if(!silent) alert(err?.message || "Nao foi possivel processar os avisos.");
  }
}

function saveState(){
  persistLocalState();
  return remoteSync ? remoteSync.queueSave(state) : Promise.resolve();
}
function persistLocalState(){
  localStorage.setItem(LS_KEY, JSON.stringify(state));
}
function loadState(){
  const raw = localStorage.getItem(LS_KEY);
  if(raw){
    try { return migrate(JSON.parse(raw)); } catch {}
  }
  return seed();
}
function replaceState(nextState){
  state = migrate(nextState);
  persistLocalState();
}
function migrate(d){
  if(!d.config) d.config = { tolDias: 3, tolValor: 0.5, scoreMin: 60 };
  if(!d.reconciliations) d.reconciliations = [];
  if(!d.imports) d.imports = [];
  if(!d.lancamentos) d.lancamentos = [];
  if(!d.categorias) d.categorias = [];
  if(!d.contas) d.contas = [];
  if(!d.titulos) d.titulos = [];
  if(!d.compras) d.compras = [];
  d.compras = Array.isArray(d.compras) ? d.compras.map(normalizeCompraRecord) : [];
  return d;
}
function seed(){
  const contaId = uid("conta");
  return {
    contas: [
      { id: contaId, nome: "Conta principal", moeda: "BRL", saldoInicial: 0 }
    ],
    categorias: [
      { id: uid("cat"), nome: "Alimentação", tipo: "DESPESA" },
      { id: uid("cat"), nome: "Transporte", tipo: "DESPESA" },
      { id: uid("cat"), nome: "Moradia", tipo: "DESPESA" },
      { id: uid("cat"), nome: "Salário", tipo: "RECEITA" },
      { id: uid("cat"), nome: "Outros", tipo: "DESPESA" },
    ],
    lancamentos: [],
    imports: [],
    reconciliations: [],
    titulos: [],
    compras: [],
    config: { tolDias: 3, tolValor: 0.5, scoreMin: 60 }
  };
}

function escapeHtml(str){
  return String(str ?? "")
    .replaceAll("&","&amp;")
    .replaceAll("<","&lt;")
    .replaceAll(">","&gt;")
    .replaceAll('"',"&quot;")
    .replaceAll("'","&#039;");
}

const TITLE_META_MARKER = "\n__GF_META__:";

function parseTitleObs(obs){
  const raw = String(obs || "");
  const markerIndex = raw.indexOf(TITLE_META_MARKER);
  if(markerIndex < 0){
    return { text: raw.trim(), meta: {} };
  }

  const text = raw.slice(0, markerIndex).trim();
  const metaRaw = raw.slice(markerIndex + TITLE_META_MARKER.length);
  try{
    const meta = JSON.parse(metaRaw);
    return { text, meta: (meta && typeof meta === "object") ? meta : {} };
  }catch{
    return { text: raw.trim(), meta: {} };
  }
}

function buildTitleObs(text, meta={}){
  const cleanText = String(text || "").trim();
  const cleanMeta = Object.fromEntries(
    Object.entries(meta).filter(([, value]) => value !== null && value !== undefined && String(value).trim() !== "")
  );
  if(!Object.keys(cleanMeta).length) return cleanText;
  return `${cleanText}${cleanText ? "\n" : ""}${TITLE_META_MARKER}${JSON.stringify(cleanMeta)}`;
}

function getTituloFormaPagamento(titulo){
  return parseTitleObs(titulo?.obs).meta.formaPagamento || "";
}

function getCompraByTituloId(tituloId){
  return state.compras.find(compra => compra.titleId === tituloId) || null;
}

/* ---------- Navegação ---------- */
function setView(name){
  $$(".tab").forEach(b => b.classList.toggle("active", b.dataset.view === name));
  $$(".view").forEach(v => v.classList.add("hidden"));
  $("#view-"+name).classList.remove("hidden");
  renderAll();
}
$("#tabs").addEventListener("click", (e)=>{
  const btn = e.target.closest(".tab");
  if(!btn) return;
  setView(btn.dataset.view);
});

/* ---------- Render geral ---------- */
function renderAll(){
  fillSelects();
  renderDashboard();
  renderLancamentos();
  renderContas();
  renderCategorias();
  renderImportPreview();
  renderConciliacao();
  renderCompras();
  renderAPAR();
  renderConfig();
}

/* ---------- Selects (preserva seleção) ---------- */
function safeRestoreSelect(selector, value){
  const el = $(selector);
  if(!el) return;
  const exists = Array.from(el.options).some(o => o.value === value);
  el.value = exists ? value : (el.options[0]?.value || "");
}

function fillSelects(){
  const contas = state.contas;
  const cats = state.categorias;

  const prev = {
    dashConta: $("#dashConta")?.value,
    fConta: $("#fConta")?.value,
    lConta: $("#lConta")?.value,
    ofxConta: $("#ofxConta")?.value,
    concConta: $("#concConta")?.value,
    concImport: $("#concImport")?.value,

    apConta: $("#apConta")?.value,
    arConta: $("#arConta")?.value,
    tConta: $("#tConta")?.value,
    pcConta: $("#pcConta")?.value,

    lTipo: $("#lTipo")?.value,
    lCategoria: $("#lCategoria")?.value,

    tTipo: $("#tTipo")?.value,
    tCategoria: $("#tCategoria")?.value,
    pcCategoria: $("#pcCategoria")?.value
  };

  const contaOptions = (includeAll=false) => {
    const opts = [];
    if(includeAll) opts.push(`<option value="ALL">Todas</option>`);
    for(const c of contas){
      opts.push(`<option value="${c.id}">${escapeHtml(c.nome)}</option>`);
    }
    return opts.join("");
  };

  // contas gerais
  $("#dashConta").innerHTML = contaOptions(true);
  $("#fConta").innerHTML    = contaOptions(true);
  $("#lConta").innerHTML    = contaOptions(false);
  $("#ofxConta").innerHTML  = contaOptions(false);
  $("#concConta").innerHTML = contaOptions(false);

  // AP/AR
  $("#apConta").innerHTML = contaOptions(true);
  $("#arConta").innerHTML = contaOptions(true);
  $("#tConta").innerHTML  = contaOptions(false);
  if($("#pcConta")) $("#pcConta").innerHTML = contaOptions(false);

  safeRestoreSelect("#dashConta", prev.dashConta ?? "ALL");
  safeRestoreSelect("#fConta", prev.fConta ?? "ALL");
  safeRestoreSelect("#lConta", prev.lConta ?? (contas[0]?.id || ""));
  safeRestoreSelect("#ofxConta", prev.ofxConta ?? (contas[0]?.id || ""));
  safeRestoreSelect("#concConta", prev.concConta ?? (contas[0]?.id || ""));

  safeRestoreSelect("#apConta", prev.apConta ?? "ALL");
  safeRestoreSelect("#arConta", prev.arConta ?? "ALL");
  safeRestoreSelect("#tConta", prev.tConta ?? (contas[0]?.id || ""));
  safeRestoreSelect("#pcConta", prev.pcConta ?? (contas[0]?.id || ""));

  // categorias do modal de lançamento dependem do tipo
  const tipoLanc = $("#lTipo").value || prev.lTipo || "DESPESA";
  $("#lTipo").value = tipoLanc;
  const catsLanc = cats.filter(c => c.tipo === tipoLanc);
  $("#lCategoria").innerHTML = catsLanc.map(c => `<option value="${c.id}">${escapeHtml(c.nome)}</option>`).join("");
  safeRestoreSelect("#lCategoria", prev.lCategoria ?? (catsLanc[0]?.id || ""));

  // categorias do modal de título dependem do tipo AP/AR
  const tipoTit = $("#tTipo")?.value || prev.tTipo || "AP";
  if($("#tTipo")) $("#tTipo").value = tipoTit;
  const tipoCatTit = (tipoTit === "AR") ? "RECEITA" : "DESPESA";
  const catsTit = cats.filter(c => c.tipo === tipoCatTit);
  $("#tCategoria").innerHTML = catsTit.map(c => `<option value="${c.id}">${escapeHtml(c.nome)}</option>`).join("");
  safeRestoreSelect("#tCategoria", prev.tCategoria ?? (catsTit[0]?.id || ""));

  const catsCompra = cats.filter(c => c.tipo === "DESPESA");
  if($("#pcCategoria")){
    $("#pcCategoria").innerHTML = catsCompra.map(c => `<option value="${c.id}">${escapeHtml(c.nome)}</option>`).join("");
    safeRestoreSelect("#pcCategoria", prev.pcCategoria ?? (catsCompra[0]?.id || ""));
  }
}

$("#lTipo").addEventListener("change", fillSelects);
$("#tTipo").addEventListener("change", fillSelects);

/* ---------- Dashboard ---------- */
function renderDashboard(){
  const selConta = $("#dashConta").value || "ALL";
  if(!$("#dashMes").value) $("#dashMes").value = toISODate(new Date()).slice(0,7);
  const mes = $("#dashMes").value;

  const [y,m] = mes.split("-").map(Number);

  const lancs = state.lancamentos.filter(l => {
    if(selConta !== "ALL" && l.contaId !== selConta) return false;
    const dt = parseISODate(l.data);
    return dt.getFullYear() === y && (dt.getMonth()+1) === m;
  });

  let receitas = 0, despesas = 0;
  for(const l of lancs){
    const v = Number(l.valor || 0);
    if(l.tipo === "RECEITA") receitas += v;
    else despesas += v;
  }
  $("#kpiReceitas").textContent = brl(receitas);
  $("#kpiDespesas").textContent = brl(despesas);
  $("#kpiSaldoMes").textContent = brl(receitas - despesas);

  const hoje = new Date();
  function saldoConta(contaId){
    const conta = state.contas.find(c => c.id === contaId);
    if(!conta) return 0;
    let s = Number(conta.saldoInicial || 0);
    for(const l of state.lancamentos){
      if(l.contaId !== contaId) continue;
      const dt = parseISODate(l.data);
      if(dt > hoje) continue;
      const v = Number(l.valor || 0);
      s += (l.tipo === "RECEITA") ? v : -v;
    }
    return s;
  }

  if(selConta === "ALL"){
    const total = state.contas.reduce((acc,c)=> acc + saldoConta(c.id), 0);
    $("#kpiSaldoConta").textContent = brl(total);
  } else {
    $("#kpiSaldoConta").textContent = brl(saldoConta(selConta));
  }

  const byCat = new Map();
  for(const l of lancs){
    if(l.tipo !== "DESPESA") continue;
    const v = Number(l.valor||0);
    byCat.set(l.categoriaId, (byCat.get(l.categoriaId)||0) + v);
  }
  const rows = Array.from(byCat.entries())
    .map(([catId,total]) => ({catId,total}))
    .sort((a,b)=> b.total - a.total);

  const cats = state.categorias;
  $("#boxCategoriasMes").innerHTML = rows.length ? rows.map(r=>{
    const cat = cats.find(c=>c.id===r.catId);
    return `
      <div class="item">
        <div class="left">
          <span class="badge">${escapeHtml(cat?.nome || "Sem categoria")}</span>
        </div>
        <div><b>${brl(r.total)}</b></div>
      </div>
    `;
  }).join("") : `<div class="muted">Sem despesas no mês selecionado.</div>`;
}

$("#btnHoje").addEventListener("click", ()=>{
  $("#dashMes").value = toISODate(new Date()).slice(0,7);
  renderDashboard();
});
$("#dashConta").addEventListener("change", renderDashboard);
$("#dashMes").addEventListener("change", renderDashboard);

/* ---------- Lançamentos ---------- */
let editLancId = null;

function renderLancamentos(){
  const conta = $("#fConta").value || "ALL";
  const ini = $("#fIni").value;
  const fim = $("#fFim").value;
  const busca = ($("#fBusca").value || "").trim().toLowerCase();

  let list = [...state.lancamentos];

  if(conta !== "ALL") list = list.filter(l => l.contaId === conta);
  if(ini) list = list.filter(l => l.data >= ini);
  if(fim) list = list.filter(l => l.data <= fim);
  if(busca) list = list.filter(l => (l.desc || "").toLowerCase().includes(busca));

  list.sort((a,b)=> b.data.localeCompare(a.data));

  const contaById = new Map(state.contas.map(c=>[c.id,c]));
  const catById = new Map(state.categorias.map(c=>[c.id,c]));

  $("#tbLanc").innerHTML = list.map(l=>{
    const c = contaById.get(l.contaId);
    const cat = catById.get(l.categoriaId);
    const conc = l.conciliado ? `<span class="badge ok">Sim</span>` : `<span class="badge warn">Não</span>`;
    const tipoBadge = l.tipo === "RECEITA" ? `<span class="badge ok">RECEITA</span>` : `<span class="badge bad">DESPESA</span>`;
    return `
      <tr>
        <td>${escapeHtml(l.data)}</td>
        <td>${escapeHtml(c?.nome || "-")}</td>
        <td>${tipoBadge}</td>
        <td>${escapeHtml(cat?.nome || "-")}</td>
        <td>${escapeHtml(l.desc || "")}</td>
        <td class="right"><b>${brl(l.valor)}</b></td>
        <td>${conc}</td>
        <td class="right">
          <button class="btn" data-act="edit" data-id="${l.id}">Editar</button>
          <button class="btn danger" data-act="del" data-id="${l.id}">Excluir</button>
        </td>
      </tr>
    `;
  }).join("") || `<tr><td colspan="8" class="muted">Nenhum lançamento.</td></tr>`;
}

$("#btnFiltrar").addEventListener("click", renderLancamentos);
$("#btnNovoLanc").addEventListener("click", ()=> openLancModal(null));

$("#tbLanc").addEventListener("click", (e)=>{
  const btn = e.target.closest("button");
  if(!btn) return;
  const id = btn.dataset.id;
  const act = btn.dataset.act;
  if(act === "edit"){
    openLancModal(id);
  } else if(act === "del"){
    if(confirm("Excluir este lançamento?")){
      state.reconciliations = state.reconciliations.filter(r => r.lancId !== id);
      // desmarca títulos que apontem para esse lançamento
      for(const t of state.titulos){
        if(t.lancId === id){
          t.lancId = null;
          if(t.status === "BAIXADO") t.status = "ABERTO";
        }
      }
      state.lancamentos = state.lancamentos.filter(l => l.id !== id);
      saveState();
      renderAll();
    }
  }
});

function openLancModal(id){
  editLancId = id;
  $("#modalLanc").classList.remove("hidden");
  $("#modalLancTitle").textContent = id ? "Editar lançamento" : "Novo lançamento";

  const l = id ? state.lancamentos.find(x=>x.id===id) : null;

  $("#lData").value = l?.data || toISODate(new Date());
  $("#lConta").value = l?.contaId || (state.contas[0]?.id || "");
  $("#lTipo").value = l?.tipo || "DESPESA";
  fillSelects();
  $("#lCategoria").value = l?.categoriaId || $("#lCategoria").value;
  $("#lDesc").value = l?.desc || "";
  $("#lValor").value = l ? Number(l.valor || 0) : "";
  $("#lConc").value = l?.conciliado ? "1" : "0";
}
function closeLancModal(){
  $("#modalLanc").classList.add("hidden");
  editLancId = null;
}

$("#btnFecharModalLanc").addEventListener("click", closeLancModal);
$("#btnCancelarLanc").addEventListener("click", closeLancModal);
$("#modalLanc").addEventListener("click",(e)=>{ if(e.target.id==="modalLanc") closeLancModal(); });

document.addEventListener("keydown",(e)=>{
  if(e.key !== "Escape") return;
  if(!$("#modalLanc").classList.contains("hidden")) closeLancModal();
  if(!$("#modalConta").classList.contains("hidden")) closeContaModal();
  if(!$("#modalTitulo").classList.contains("hidden")) closeTituloModal();
});

$("#btnSalvarLanc").addEventListener("click", ()=>{
  const data = $("#lData").value;
  const contaId = $("#lConta").value;
  const tipo = $("#lTipo").value;
  const categoriaId = $("#lCategoria").value;
  const desc = $("#lDesc").value.trim();
  const valor = Number($("#lValor").value);
  const conciliado = $("#lConc").value === "1";

  if(!data || !contaId || !tipo || !categoriaId || !desc || !Number.isFinite(valor) || valor<=0){
    alert("Preencha todos os campos corretamente.");
    return;
  }

  if(editLancId){
    const idx = state.lancamentos.findIndex(x=>x.id===editLancId);
    if(idx >= 0){
      const old = state.lancamentos[idx];
      state.lancamentos[idx] = { ...old, data, contaId, tipo, categoriaId, desc, valor, conciliado };
    }
  } else {
    state.lancamentos.unshift({ id: uid("lanc"), data, contaId, tipo, categoriaId, desc, valor, conciliado });
  }
  saveState();
  closeLancModal();
  renderAll();
});

/* ---------- Contas ---------- */
let editContaId = null;

function renderContas(){
  $("#listaContas").innerHTML = state.contas.map(c=>{
    return `
      <div class="item">
        <div class="left">
          <span class="badge">${escapeHtml(c.moeda || "BRL")}</span>
          <div>
            <div><b>${escapeHtml(c.nome)}</b></div>
            <div class="muted">Saldo inicial: ${brl(c.saldoInicial)}</div>
          </div>
        </div>
        <div class="row gap">
          <button class="btn" data-act="edit" data-id="${c.id}">Editar</button>
          <button class="btn danger" data-act="del" data-id="${c.id}">Excluir</button>
        </div>
      </div>
    `;
  }).join("");

  $("#listaImports").innerHTML = state.imports
    .slice()
    .sort((a,b)=> b.createdAt.localeCompare(a.createdAt))
    .map(imp=>{
      const conta = state.contas.find(c=>c.id===imp.contaId);
      return `
        <div class="item">
          <div class="left">
            <span class="badge">${escapeHtml(conta?.nome || "-")}</span>
            <div>
              <div><b>${escapeHtml(imp.fileName || "import.ofx")}</b></div>
              <div class="muted">${escapeHtml(imp.createdAt)} • ${imp.txs.length} transações</div>
            </div>
          </div>
          <div class="row gap">
            <button class="btn" data-act="useImport" data-id="${imp.id}">Usar</button>
            <button class="btn danger" data-act="delImport" data-id="${imp.id}">Excluir</button>
          </div>
        </div>
      `;
    }).join("") || `<div class="muted">Nenhuma importação ainda.</div>`;
}

$("#btnNovaConta").addEventListener("click", ()=> openContaModal(null));

$("#listaContas").addEventListener("click", (e)=>{
  const btn = e.target.closest("button");
  if(!btn) return;
  const id = btn.dataset.id;
  const act = btn.dataset.act;

  if(act === "edit") openContaModal(id);
  if(act === "del"){
    if(confirm("Excluir esta conta? (lançamentos, títulos e imports dessa conta também serão removidos)")){
      state.lancamentos = state.lancamentos.filter(l => l.contaId !== id);
      state.titulos = state.titulos.filter(t => t.contaId !== id);
      state.compras = state.compras.filter(c => c.contaId !== id);
      state.imports = state.imports.filter(i => i.contaId !== id);

      // remove reconciliations órfãos
      const lancIds = new Set(state.lancamentos.map(l=>l.id));
      const bankIds = new Set(state.imports.flatMap(i=>i.txs.map(t=>t.id)));
      state.reconciliations = state.reconciliations.filter(r => lancIds.has(r.lancId) && bankIds.has(r.bankTxId));

      state.contas = state.contas.filter(c => c.id !== id);
      if(state.contas.length === 0){
        state.contas.push({ id: uid("conta"), nome:"Conta principal", moeda:"BRL", saldoInicial:0 });
      }
      saveState();
      renderAll();
    }
  }
});

$("#listaImports").addEventListener("click", (e)=>{
  const btn = e.target.closest("button");
  if(!btn) return;
  const id = btn.dataset.id;
  const act = btn.dataset.act;

  if(act === "useImport"){
    setView("conciliacao");
    $("#concImport").value = id;
    renderConciliacao();
  }
  if(act === "delImport"){
    if(confirm("Excluir este OFX importado? (vínculos de conciliação serão removidos)")){
      const imp = state.imports.find(i=>i.id===id);
      const bankIds = new Set(imp?.txs.map(t=>t.id) || []);
      state.reconciliations = state.reconciliations.filter(r => !bankIds.has(r.bankTxId));

      for(const l of state.lancamentos){
        if(l.bankTxId && bankIds.has(l.bankTxId)){
          l.bankTxId = null;
          l.conciliado = false;
        }
      }
      for(const t of state.titulos){
        if(t.bankTxId && bankIds.has(t.bankTxId)){
          t.bankTxId = null;
          // se estava conciliado via lançamento, não muda status; apenas remove vínculo bancário
        }
      }

      state.imports = state.imports.filter(i=>i.id!==id);
      saveState();
      renderAll();
    }
  }
});

function openContaModal(id){
  editContaId = id;
  $("#modalConta").classList.remove("hidden");
  $("#modalContaTitle").textContent = id ? "Editar conta" : "Nova conta";
  const c = id ? state.contas.find(x=>x.id===id) : null;
  $("#cNome").value = c?.nome || "";
  $("#cMoeda").value = c?.moeda || "BRL";
  $("#cSaldo").value = c ? Number(c.saldoInicial || 0) : 0;
}
function closeContaModal(){
  $("#modalConta").classList.add("hidden");
  editContaId = null;
}
$("#btnFecharModalConta").addEventListener("click", closeContaModal);
$("#btnCancelarConta").addEventListener("click", closeContaModal);
$("#modalConta").addEventListener("click",(e)=>{ if(e.target.id==="modalConta") closeContaModal(); });

$("#btnSalvarConta").addEventListener("click", ()=>{
  const nome = $("#cNome").value.trim();
  const moeda = ($("#cMoeda").value || "BRL").trim().toUpperCase();
  const saldoInicial = Number($("#cSaldo").value);

  if(!nome || !Number.isFinite(saldoInicial)){
    alert("Informe nome e saldo inicial.");
    return;
  }

  if(editContaId){
    const idx = state.contas.findIndex(c=>c.id===editContaId);
    if(idx>=0) state.contas[idx] = { ...state.contas[idx], nome, moeda, saldoInicial };
  } else {
    state.contas.push({ id: uid("conta"), nome, moeda, saldoInicial });
  }
  saveState();
  closeContaModal();
  renderAll();
});

/* ---------- Categorias ---------- */
function renderCategorias(){
  const cats = state.categorias.slice().sort((a,b)=> a.tipo.localeCompare(b.tipo) || a.nome.localeCompare(b.nome));
  $("#listaCats").innerHTML = cats.map(c=>{
    const badge = c.tipo === "RECEITA" ? `<span class="badge ok">RECEITA</span>` : `<span class="badge bad">DESPESA</span>`;
    return `
      <div class="item">
        <div class="left">
          ${badge}
          <div><b>${escapeHtml(c.nome)}</b></div>
        </div>
        <div class="row gap">
          <button class="btn danger" data-id="${c.id}">Excluir</button>
        </div>
      </div>
    `;
  }).join("");
}

$("#btnAddCat").addEventListener("click", ()=>{
  const nome = $("#catNome").value.trim();
  const tipo = $("#catTipo").value;
  if(!nome) return alert("Informe o nome da categoria.");
  state.categorias.push({ id: uid("cat"), nome, tipo });
  $("#catNome").value = "";
  saveState();
  renderAll();
});

$("#listaCats").addEventListener("click", (e)=>{
  const btn = e.target.closest("button");
  if(!btn) return;
  const id = btn.dataset.id;
  if(confirm("Excluir categoria? (lançamentos existentes manterão o ID antigo)")){
    state.categorias = state.categorias.filter(c=>c.id!==id);
    saveState();
    renderAll();
  }
});

/* ---------- Importação OFX ---------- */
function renderImportPreview(){
  const last = state.imports.slice().sort((a,b)=> b.createdAt.localeCompare(a.createdAt))[0];
  if(!last){
    $("#tbOfxPreview").innerHTML = `<tr><td colspan="4" class="muted">Nenhuma importação.</td></tr>`;
    return;
  }
  $("#tbOfxPreview").innerHTML = last.txs.slice(0,50).map(t=>{
    return `
      <tr>
        <td>${escapeHtml(t.date)}</td>
        <td>${escapeHtml(t.memo || "")}</td>
        <td class="right"><b>${brl(Math.abs(t.amount))}</b></td>
        <td class="muted">${escapeHtml(t.fitid || "")}</td>
      </tr>
    `;
  }).join("");
}

$("#btnImportarOFX").addEventListener("click", async ()=>{
  const contaId = $("#ofxConta").value;
  const file = $("#ofxFile").files?.[0];
  if(!contaId) return alert("Selecione uma conta.");
  if(!file) return alert("Selecione um arquivo OFX.");

  const text = await file.text();
  const txs = parseOFX(text);

  if(!txs.length){
    alert("Não consegui ler transações desse OFX.");
    return;
  }

  const imp = {
    id: uid("imp"),
    contaId,
    createdAt: new Date().toISOString().slice(0,19).replace("T"," "),
    fileName: file.name,
    txs: txs.map(t => ({...t, id: uid("banktx")}))
  };

  state.imports.push(imp);
  saveState();
  renderAll();
  alert(`Importado: ${imp.txs.length} transações. Vá em "Conciliação".`);
});

/* Parser OFX (foco OFX 1.x SGML). */
function parseOFX(ofxText){
  if(!ofxText) return [];

  let s = ofxText.replace(/\r\n/g,"\n");
  s = s.replace(/<(\w+?)>([^<\n\r]*)/g, (m,tag,val)=>{
    if(val.includes(`</${tag}>`)) return m;
    if(val.trim()==="") return `<${tag}>`;
    return `<${tag}>${escapeXml(val.trim())}</${tag}>`;
  });

  const blocks = s.match(/<STMTTRN>[\s\S]*?<\/STMTTRN>/gi) || [];
  const txs = [];

  for(const b of blocks){
    const type = getTag(b,"TRNTYPE") || "";
    const dt = getTag(b,"DTPOSTED") || getTag(b,"DTUSER") || "";
    const amt = getTag(b,"TRNAMT") || "";
    const fitid = getTag(b,"FITID") || "";
    const name = getTag(b,"NAME") || "";
    const memo = getTag(b,"MEMO") || "";

    const date = ofxDateToISO(dt);
    const amount = Number(String(amt).replace(",", "."));

    if(!date || !Number.isFinite(amount)) continue;

    txs.push({
      date,
      amount,
      fitid: fitid || "",
      memo: (memo || name || "").trim() || "(sem descrição)",
      trntype: type
    });
  }

  return txs;
}
function getTag(xmlish, tag){
  const re = new RegExp(`<${tag}>([\\s\\S]*?)<\\/${tag}>`, "i");
  const m = xmlish.match(re);
  return m ? decodeXml(m[1].trim()) : "";
}
function ofxDateToISO(dt){
  const m = String(dt).match(/^(\d{4})(\d{2})(\d{2})/);
  if(!m) return "";
  return `${m[1]}-${m[2]}-${m[3]}`;
}
function escapeXml(str){
  return String(str).replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;");
}
function decodeXml(str){
  return String(str).replaceAll("&lt;","<").replaceAll("&gt;",">").replaceAll("&amp;","&");
}

/* ---------- Conciliação ---------- */
let selectedBankTxId = null;
let selectedLancId = null;
let selectedTituloId = null;

function renderConciliacao(){
  const contaId = $("#concConta").value || state.contas[0]?.id || "";
  if(contaId) $("#concConta").value = contaId;

  const imports = state.imports
    .filter(i => i.contaId === contaId)
    .slice()
    .sort((a,b)=> b.createdAt.localeCompare(a.createdAt));

  $("#concImport").innerHTML = imports.map(i=> `<option value="${i.id}">${escapeHtml(i.createdAt)} • ${escapeHtml(i.fileName||"import.ofx")}</option>`).join("")
    || `<option value="">(Sem importações)</option>`;

  const importId = $("#concImport").value || imports[0]?.id || "";
  if(importId) $("#concImport").value = importId;

  const imp = state.imports.find(i=>i.id===importId);
  const bankTxs = imp?.txs || [];
  const lancs = state.lancamentos.filter(l => l.contaId === contaId);

  const reconByBank = new Map(state.reconciliations.map(r=>[r.bankTxId, r.lancId]));
  const reconByLanc = new Map(state.reconciliations.map(r=>[r.lancId, r.bankTxId]));

  $("#bankList").innerHTML = bankTxs.map(t=>{
    const linkedLancId = reconByBank.get(t.id);
    const status = linkedLancId ? `<span class="badge ok">Conciliado</span>` : `<span class="badge warn">Pendente</span>`;
    const cls = (selectedBankTxId === t.id) ? "item selected" : "item";
    const signBadge = t.amount >= 0 ? `<span class="badge ok">CR</span>` : `<span class="badge bad">DB</span>`;
    return `
      <div class="${cls}" data-id="${t.id}" data-kind="bank">
        <div class="left">
          ${signBadge}
          <div>
            <div><b>${escapeHtml(t.memo || "")}</b></div>
            <div class="muted">${escapeHtml(t.date)} • ${escapeHtml(t.fitid || "")}</div>
          </div>
        </div>
        <div style="text-align:right">
          <div><b>${brl(Math.abs(t.amount))}</b></div>
          <div>${status}</div>
        </div>
      </div>
    `;
  }).join("") || `<div class="muted">Selecione uma importação OFX.</div>`;

  const catById = new Map(state.categorias.map(c=>[c.id,c]));
  $("#sysList").innerHTML = lancs
    .slice()
    .sort((a,b)=> b.data.localeCompare(a.data))
    .map(l=>{
      const linkedBankId = reconByLanc.get(l.id);
      const status = linkedBankId ? `<span class="badge ok">Conciliado</span>` : `<span class="badge warn">Pendente</span>`;
      const cls = (selectedLancId === l.id) ? "item selected" : "item";
      const tipoBadge = l.tipo === "RECEITA" ? `<span class="badge ok">RECEITA</span>` : `<span class="badge bad">DESPESA</span>`;
      const cat = catById.get(l.categoriaId);
      return `
        <div class="${cls}" data-id="${l.id}" data-kind="sys">
          <div class="left">
            ${tipoBadge}
            <div>
              <div><b>${escapeHtml(l.desc || "")}</b></div>
              <div class="muted">${escapeHtml(l.data)} • ${escapeHtml(cat?.nome || "-")}</div>
            </div>
          </div>
          <div style="text-align:right">
            <div><b>${brl(l.valor)}</b></div>
            <div>${status}</div>
          </div>
        </div>
      `;
    }).join("") || `<div class="muted">Sem lançamentos nesta conta.</div>`;

  // Títulos em aberto (AP/AR)
  const titulosAbertos = state.titulos
    .filter(t => t.contaId===contaId && t.status==="ABERTO")
    .slice()
    .sort((a,b)=> a.vencimento.localeCompare(b.vencimento));

  $("#titList").innerHTML = titulosAbertos.map(t=>{
    const cls = (selectedTituloId===t.id) ? "item selected" : "item";
    const badge = (t.tipo==="AR") ? `<span class="badge ok">AR</span>` : `<span class="badge bad">AP</span>`;
    return `
      <div class="${cls}" data-id="${t.id}">
        <div class="left">
          ${badge}
          <div>
            <div><b>${escapeHtml(t.desc || "")}</b></div>
            <div class="muted">${escapeHtml(t.vencimento)} • ${escapeHtml(t.pessoa||"-")} • ${escapeHtml(t.centroCusto||"-")}</div>
          </div>
        </div>
        <div style="text-align:right">
          <div><b>${brl(t.valor)}</b></div>
          <div class="muted">ABERTO</div>
        </div>
      </div>
    `;
  }).join("") || `<div class="muted">Nenhum título em aberto nesta conta.</div>`;

  $("#concStatus").textContent = buildConcStatus(contaId, imp);
}

function buildConcStatus(contaId, imp){
  if(!imp) return "Selecione um OFX importado.";
  const bankCount = imp.txs.length;
  const reconciled = imp.txs.filter(t => state.reconciliations.some(r => r.bankTxId === t.id)).length;
  return `Importação: ${imp.fileName || "OFX"} • ${bankCount} transações • ${reconciled} conciliadas • Conta: ${state.contas.find(c=>c.id===contaId)?.nome || "-"}`;
}

$("#concConta").addEventListener("change", ()=>{
  selectedBankTxId = null;
  selectedLancId = null;
  selectedTituloId = null;
  renderConciliacao();
});
$("#concImport").addEventListener("change", ()=>{
  selectedBankTxId = null;
  selectedLancId = null;
  selectedTituloId = null;
  renderConciliacao();
});

$("#bankList").addEventListener("click", (e)=>{
  const item = e.target.closest(".item");
  if(!item) return;
  selectedBankTxId = item.dataset.id;
  renderConciliacao();
});
$("#sysList").addEventListener("click", (e)=>{
  const item = e.target.closest(".item");
  if(!item) return;
  selectedLancId = item.dataset.id;
  renderConciliacao();
});
$("#titList").addEventListener("click",(e)=>{
  const item=e.target.closest(".item"); if(!item) return;
  selectedTituloId = item.dataset.id;
  renderConciliacao();
});

// Banco ↔ Lançamento (com criação se não selecionar lançamento)
$("#btnVincular").addEventListener("click", ()=>{
  const contaId = $("#concConta").value;
  const importId = $("#concImport").value;
  const imp = state.imports.find(i=>i.id===importId);

  if(!selectedBankTxId){
    alert("Selecione 1 item do banco.");
    return;
  }
  if(!imp){
    alert("Selecione um OFX.");
    return;
  }

  const bankTx = imp.txs.find(t=>t.id===selectedBankTxId);
  if(!bankTx){
    alert("Transação do banco não encontrada.");
    return;
  }

  // Se não selecionou lançamento, cria automático
  if(!selectedLancId){
    const isCredit = Number(bankTx.amount||0) >= 0;
    const tipo = isCredit ? "RECEITA" : "DESPESA";
    const catId = state.categorias.find(c=>c.tipo===tipo)?.id || state.categorias[0]?.id;

    const lanc = {
      id: uid("lanc"),
      data: bankTx.date,
      contaId,
      tipo,
      categoriaId: catId,
      desc: bankTx.memo || "(importado do banco)",
      valor: Math.abs(Number(bankTx.amount||0)),
      conciliado: true,
      bankTxId: bankTx.id
    };

    state.lancamentos.unshift(lanc);
    selectedLancId = lanc.id;
  }

  state.reconciliations = state.reconciliations.filter(r =>
    r.bankTxId !== selectedBankTxId && r.lancId !== selectedLancId
  );
  state.reconciliations.push({ bankTxId: selectedBankTxId, lancId: selectedLancId });

  const l = state.lancamentos.find(x=>x.id===selectedLancId);
  if(l){
    l.conciliado = true;
    l.bankTxId = selectedBankTxId;
  }

  saveState();
  renderAll();
});

$("#btnDesvincular").addEventListener("click", ()=>{
  if(!selectedBankTxId && !selectedLancId){
    alert("Selecione um item do banco OU um lançamento conciliado.");
    return;
  }
  const before = state.reconciliations.length;
  state.reconciliations = state.reconciliations.filter(r => {
    if(selectedBankTxId && r.bankTxId === selectedBankTxId) return false;
    if(selectedLancId && r.lancId === selectedLancId) return false;
    return true;
  });

  if(state.reconciliations.length !== before){
    for(const l of state.lancamentos){
      if(selectedLancId && l.id === selectedLancId){
        l.conciliado = false; l.bankTxId = null;
      }
      if(selectedBankTxId && l.bankTxId === selectedBankTxId){
        l.conciliado = false; l.bankTxId = null;
      }
    }
    // também remove bankTxId de títulos que apontem para esse bankTx
    if(selectedBankTxId){
      for(const t of state.titulos){
        if(t.bankTxId === selectedBankTxId){
          t.bankTxId = null;
        }
      }
    }
    saveState();
  }
  selectedBankTxId = null;
  selectedLancId = null;
  selectedTituloId = null;
  renderAll();
});

$("#btnSugerir").addEventListener("click", ()=>{
  const contaId = $("#concConta").value;
  const importId = $("#concImport").value;
  const imp = state.imports.find(i=>i.id===importId);
  if(!imp) return alert("Selecione um OFX.");
  const cfg = state.config;

  const reconBank = new Set(state.reconciliations.map(r=>r.bankTxId));
  const reconLanc = new Set(state.reconciliations.map(r=>r.lancId));

  const pendBank = imp.txs.filter(t => !reconBank.has(t.id));
  const pendLanc = state.lancamentos.filter(l => l.contaId===contaId && !reconLanc.has(l.id));

  let linked = 0;

  for(const bt of pendBank){
    let best = {score: -1, lanc: null};
    for(const l of pendLanc){
      const score = scoreMatch(bt, l, cfg);
      if(score > best.score){
        best = {score, lanc: l};
      }
    }
    if(best.lanc && best.score >= cfg.scoreMin){
      state.reconciliations.push({ bankTxId: bt.id, lancId: best.lanc.id });
      best.lanc.conciliado = true;
      best.lanc.bankTxId = bt.id;
      const idx = pendLanc.findIndex(x=>x.id===best.lanc.id);
      if(idx>=0) pendLanc.splice(idx,1);
      linked++;
    }
  }

  saveState();
  renderAll();
  alert(`Sugestões aplicadas: ${linked} vínculo(s).`);
});

function scoreMatch(bankTx, lanc, cfg){
  const bankAbs = Math.abs(Number(bankTx.amount||0));
  const sysAbs = Math.abs(Number(lanc.valor||0));

  const diffV = Math.abs(bankAbs - sysAbs);
  const okValor = diffV <= Number(cfg.tolValor||0);
  const scoreValor = okValor ? 55 : clamp(55 - (diffV*20), 0, 55);

  const d1 = parseISODate(bankTx.date);
  const d2 = parseISODate(lanc.data);
  const diffDias = Math.abs(Math.round((d1 - d2) / (1000*60*60*24)));
  const okDias = diffDias <= Number(cfg.tolDias||0);
  const scoreData = okDias ? 25 : clamp(25 - (diffDias*6), 0, 25);

  const bankIsCredit = Number(bankTx.amount||0) >= 0;
  const sysIsCredit = lanc.tipo === "RECEITA";
  const scoreTipo = (bankIsCredit === sysIsCredit) ? 15 : 0;

  const a = normalizeText(bankTx.memo || "");
  const b = normalizeText(lanc.desc || "");
  const inter = textOverlap(a,b);
  const scoreTxt = clamp(inter * 10, 0, 5);

  return Math.round(scoreValor + scoreData + scoreTipo + scoreTxt);
}
function normalizeText(s){
  return String(s).toLowerCase()
    .normalize("NFD").replace(/\p{Diacritic}/gu,"")
    .replace(/[^a-z0-9\s]/g," ")
    .replace(/\s+/g," ")
    .trim();
}
function textOverlap(a,b){
  if(!a || !b) return 0;
  const sa = new Set(a.split(" ").filter(w=>w.length>=4));
  const sb = new Set(b.split(" ").filter(w=>w.length>=4));
  let hit = 0;
  for(const w of sa) if(sb.has(w)) hit++;
  return hit;
}

$("#btnCriarLancDoBanco").addEventListener("click", ()=>{
  const contaId = $("#concConta").value;
  const importId = $("#concImport").value;
  const imp = state.imports.find(i=>i.id===importId);
  if(!imp) return alert("Selecione um OFX.");

  const reconBank = new Set(state.reconciliations.map(r=>r.bankTxId));
  const pendBank = imp.txs.filter(t => !reconBank.has(t.id));

  if(!pendBank.length) return alert("Não há transações pendentes.");

  const catDesp = state.categorias.find(c=>c.tipo==="DESPESA")?.id || state.categorias[0]?.id;
  const catRec = state.categorias.find(c=>c.tipo==="RECEITA")?.id || state.categorias[0]?.id;

  let created = 0;
  for(const bt of pendBank){
    const isCredit = Number(bt.amount||0) >= 0;
    const tipo = isCredit ? "RECEITA" : "DESPESA";
    const categoriaId = isCredit ? catRec : catDesp;

    const lanc = {
      id: uid("lanc"),
      data: bt.date,
      contaId,
      tipo,
      categoriaId,
      desc: bt.memo || "(importado do banco)",
      valor: Math.abs(Number(bt.amount||0)),
      conciliado: true,
      bankTxId: bt.id
    };
    state.lancamentos.unshift(lanc);
    state.reconciliations.push({ bankTxId: bt.id, lancId: lanc.id });
    created++;
  }

  saveState();
  renderAll();
  alert(`Criados ${created} lançamento(s) a partir do OFX e marcados como conciliados.`);
});

/* ---------- AP/AR (Títulos) + anexos ---------- */
function novoTitulo({tipo, pessoa, desc, categoriaId, contaId, valor, vencimento, centroCusto, obs}){
  return {
    id: uid("tit"),
    tipo,
    pessoa: (pessoa||"").trim(),
    desc: (desc||"").trim(),
    categoriaId,
    contaId,
    valor: Number(valor),
    vencimento,
    centroCusto: (centroCusto||"").trim(),
    obs: (obs||"").trim(),
    status: "ABERTO",
    baixadoEm: null,
    lancId: null,
    bankTxId: null,
    anexos: []
  };
}

function baixarTitulo(tituloId, dataBaixaISO=null){
  const t = state.titulos.find(x=>x.id===tituloId);
  if(!t) throw new Error("Título não encontrado.");
  if(t.status !== "ABERTO") throw new Error("Título não está em aberto.");

  const data = dataBaixaISO || toISODate(new Date());
  const isAR = t.tipo === "AR";

  const lanc = {
    id: uid("lanc"),
    data,
    contaId: t.contaId,
    tipo: isAR ? "RECEITA" : "DESPESA",
    categoriaId: t.categoriaId,
    desc: `${t.desc}${t.pessoa ? " - " + t.pessoa : ""}${t.centroCusto ? " ["+t.centroCusto+"]" : ""}`,
    valor: Math.abs(Number(t.valor||0)),
    conciliado: !!t.bankTxId,
    bankTxId: t.bankTxId || null
  };

  state.lancamentos.unshift(lanc);

  t.status = "BAIXADO";
  t.baixadoEm = data;
  t.lancId = lanc.id;

  return lanc;
}

function vincularBankTxAoTitulo({tituloId, bankTxId, bankDateISO}){
  const t = state.titulos.find(x=>x.id===tituloId);
  if(!t) throw new Error("Título não encontrado.");

  t.bankTxId = bankTxId;

  if(t.lancId){
    const l = state.lancamentos.find(x=>x.id===t.lancId);
    if(l){
      l.conciliado = true;
      l.bankTxId = bankTxId;
    }
  } else {
    baixarTitulo(tituloId, bankDateISO);
  }

  const lancId = t.lancId;
  if(lancId){
    state.reconciliations = state.reconciliations.filter(r => r.bankTxId !== bankTxId && r.lancId !== lancId);
    state.reconciliations.push({ bankTxId, lancId });
  }
}

async function detectCodesFromAttachment(anexo){
  const box = $("#anexoCodePreview");
  if(!box) return;

  if(!anexo){
    box.textContent = "Selecione uma imagem ou PDF para tentar ler QR Code, linha digitavel ou codigo de barras.";
    return;
  }
  const mime = anexo.mime || "";
  const isImage = mime.startsWith("image/");
  const isPdf = mime.includes("pdf");
  if(!isImage && !isPdf){
    box.textContent = "A leitura automatica esta disponivel para anexos de imagem e PDF.";
    return;
  }

  try{
    box.textContent = isPdf ? "Lendo codigo do PDF..." : "Lendo codigo da imagem...";
    const payload = await requestJson("/api/finance/attachments/decode", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        path: anexo.path || "",
        dataUrl: anexo.path ? "" : (anexo.dataUrl || ""),
        mime: anexo.mime || ""
      })
    });
    const results = payload?.codes || [];

    if(!results.length){
      box.textContent = isPdf
        ? "Nenhum QR Code, linha digitavel ou codigo de barras foi encontrado nas primeiras paginas do PDF."
        : "Nenhum QR Code ou codigo de barras foi encontrado na imagem.";
      return;
    }

    box.innerHTML = results.map((result, index)=>`
      <div class="item">
        <div class="left" style="flex:1">
          <span class="badge">${escapeHtml(result.format || `COD ${index + 1}`)}</span>
          <div style="min-width:0">
            <div><b>${escapeHtml(result.rawValue || "(sem valor)")}</b></div>
          </div>
        </div>
      </div>
    `).join("");
  }catch(err){
    console.warn("Falha ao ler codigo do anexo:", err);
    box.textContent = "Nao foi possivel ler QR Code, linha digitavel ou codigo de barras deste anexo.";
  }
}

function criarTitulosDoOFX({contaId, importId}){
  const imp = state.imports.find(i=>i.id===importId);
  if(!imp) throw new Error("Import OFX não encontrado.");

  const existentes = new Set(state.titulos.filter(t=>t.bankTxId).map(t=>t.bankTxId));

  const catDesp = state.categorias.find(c=>c.tipo==="DESPESA")?.id || state.categorias[0]?.id;
  const catRec  = state.categorias.find(c=>c.tipo==="RECEITA")?.id  || state.categorias[0]?.id;

  let created = 0;
  for(const bt of imp.txs){
    if(existentes.has(bt.id)) continue;

    const isCredit = Number(bt.amount||0) >= 0;
    const tipo = isCredit ? "AR" : "AP";

    const t = novoTitulo({
      tipo,
      pessoa: "",
      desc: bt.memo || "(importado do banco)",
      categoriaId: isCredit ? catRec : catDesp,
      contaId,
      valor: Math.abs(Number(bt.amount||0)),
      vencimento: bt.date,
      centroCusto: "",
      obs: ""
    });

    // já salva o bankTx no título (você pode baixar depois ou vincular direto)
    t.bankTxId = bt.id;

    state.titulos.unshift(t);
    created++;
  }
  return created;
}

// Conciliação: Banco ↔ Título
$("#btnVincularTitulo").addEventListener("click", ()=>{
  const contaId = $("#concConta").value;
  const importId = $("#concImport").value;
  const imp = state.imports.find(i=>i.id===importId);
  if(!imp) return alert("Selecione um OFX.");
  if(!selectedBankTxId) return alert("Selecione 1 transação do banco.");
  if(!selectedTituloId) return alert("Selecione 1 título (AP/AR) em aberto.");

  const bt = imp.txs.find(t=>t.id===selectedBankTxId);
  if(!bt) return alert("Transação do banco não encontrada.");

  try{
    vincularBankTxAoTitulo({ tituloId: selectedTituloId, bankTxId: bt.id, bankDateISO: bt.date });
    saveState();
    selectedTituloId = null;
    selectedLancId = null;
    renderAll();
    alert("Vinculado ao título, baixado e lançamento gerado/conciliado.");
  }catch(err){
    alert(err?.message || "Falha ao vincular ao título.");
  }
});

$("#btnCriarTitulosDoOFX").addEventListener("click", ()=>{
  const contaId = $("#concConta").value;
  const importId = $("#concImport").value;
  if(!importId) return alert("Selecione um OFX.");
  const qtd = criarTitulosDoOFX({contaId, importId});
  saveState();
  renderAll();
  alert(`Criados ${qtd} título(s) a partir do OFX.`);
});

/* ---------- Compras ---------- */
let editCompraId = null;
const COMPRA_AI_CATEGORY_META = {
  melhor_preco: { title: "Melhor preco", cardClass: "best" },
  custo_beneficio: { title: "Custo-beneficio", cardClass: "value" },
  alternativa: { title: "Alternativas", cardClass: "" }
};
let compraAiState = defaultCompraAiState();

function defaultCompraAiState(){
  return {
    loading: false,
    error: "",
    query: "",
    summary: "",
    offers: [],
    sources: [],
    generatedAt: "",
    model: "",
    selectedOfferIndex: -1,
    selectedOffer: null
  };
}

function resetCompraAiState(){
  compraAiState = defaultCompraAiState();
}

function cloneCompraAiPayload(item){
  if(!item || typeof item !== "object") return null;
  try{
    return JSON.parse(JSON.stringify(item));
  }catch{
    return null;
  }
}

function buildPersistedCompraAiState(aiState){
  const persisted = {
    query: String(aiState?.query || "").trim(),
    summary: String(aiState?.summary || "").trim(),
    offers: Array.isArray(aiState?.offers) ? aiState.offers.map(cloneCompraAiPayload).filter(Boolean) : [],
    sources: Array.isArray(aiState?.sources) ? aiState.sources.map(cloneCompraAiPayload).filter(Boolean) : [],
    generatedAt: String(aiState?.generatedAt || "").trim(),
    model: String(aiState?.model || "").trim(),
    selectedOfferIndex: Number.isInteger(aiState?.selectedOfferIndex) ? aiState.selectedOfferIndex : -1,
    selectedOffer: cloneCompraAiPayload(aiState?.selectedOffer)
  };

  const hasContent = !!(
    persisted.query ||
    persisted.summary ||
    persisted.offers.length ||
    persisted.sources.length ||
    persisted.generatedAt ||
    persisted.model ||
    persisted.selectedOffer
  );

  return hasContent ? persisted : null;
}

function hydrateCompraAiState(savedState){
  const persisted = buildPersistedCompraAiState(savedState);
  return persisted ? { ...defaultCompraAiState(), ...persisted } : defaultCompraAiState();
}

function normalizeCompraRecord(compra){
  if(!compra || typeof compra !== "object") return compra;
  return {
    ...compra,
    aiResearch: buildPersistedCompraAiState(compra.aiResearch)
  };
}

function isSelectedCompraAiOffer(offer, index){
  if(index === compraAiState.selectedOfferIndex) return true;

  const selectedOffer = compraAiState.selectedOffer;
  if(!selectedOffer || typeof selectedOffer !== "object") return false;

  if(selectedOffer.url && offer?.url){
    return selectedOffer.url === offer.url;
  }

  return selectedOffer.title === offer?.title && selectedOffer.store === offer?.store;
}

function novoPedidoCompra({requestedAt, desc, fornecedor, produtoUrl, fotoUrl, justificativa, categoriaId, contaId, centroCusto, valor, vencimento, formaPagamento, obs, aiResearch}){
  return {
    id: uid("compra"),
    requestedAt,
    status: "PENDENTE",
    desc: (desc || "").trim(),
    fornecedor: (fornecedor || "").trim(),
    produtoUrl: (produtoUrl || "").trim(),
    fotoUrl: (fotoUrl || "").trim(),
    justificativa: (justificativa || "").trim(),
    categoriaId,
    contaId,
    centroCusto: (centroCusto || "").trim(),
    valor: Number(valor),
    vencimento,
    formaPagamento: (formaPagamento || "").trim(),
    obs: (obs || "").trim(),
    aiResearch: buildPersistedCompraAiState(aiResearch),
    titleId: null,
    approvedAt: null,
    rejectedAt: null
  };
}

function compraStatusBadge(status){
  if(status === "APROVADO") return `<span class="badge ok">APROVADO</span>`;
  if(status === "REPROVADO") return `<span class="badge bad">REPROVADO</span>`;
  if(status === "CANCELADO") return `<span class="badge bad">CANCELADO</span>`;
  return `<span class="badge warn">PENDENTE</span>`;
}

function buildCompraTituloObs(compra){
  return buildTitleObs(compra.obs, {
    formaPagamento: compra.formaPagamento,
    compraId: compra.id,
    produtoUrl: compra.produtoUrl,
    justificativaCompra: compra.justificativa
  });
}

function gerarTituloDaCompra(compra){
  const titulo = novoTitulo({
    tipo: "AP",
    pessoa: compra.fornecedor,
    desc: compra.desc,
    categoriaId: compra.categoriaId,
    contaId: compra.contaId,
    valor: compra.valor,
    vencimento: compra.vencimento,
    centroCusto: compra.centroCusto,
    obs: buildCompraTituloObs(compra)
  });
  titulo.status = "ABERTO";
  state.titulos.unshift(titulo);
  return titulo;
}

function syncCompraToTitulo(compra){
  if(!compra.titleId) return;
  const titulo = state.titulos.find(t => t.id === compra.titleId);
  if(!titulo) return;

  titulo.contaId = compra.contaId;
  titulo.categoriaId = compra.categoriaId;
  titulo.pessoa = compra.fornecedor;
  titulo.desc = compra.desc;
  titulo.valor = compra.valor;
  titulo.vencimento = compra.vencimento;
  titulo.centroCusto = compra.centroCusto;
  titulo.obs = buildCompraTituloObs(compra);
}

function aprovarCompra(compraId){
  const compra = state.compras.find(c => c.id === compraId);
  if(!compra) throw new Error("Solicitacao de compra nao encontrada.");

  if(!compra.titleId){
    const titulo = gerarTituloDaCompra(compra);
    compra.titleId = titulo.id;
  } else {
    syncCompraToTitulo(compra);
  }

  compra.status = "APROVADO";
  compra.approvedAt = nowIsoLocal();
  compra.rejectedAt = null;
}

function reprovarCompra(compraId){
  const compra = state.compras.find(c => c.id === compraId);
  if(!compra) throw new Error("Solicitacao de compra nao encontrada.");
  if(compra.titleId) throw new Error("Nao e possivel reprovar uma solicitacao que ja gerou contas a pagar.");
  compra.status = "REPROVADO";
  compra.rejectedAt = nowIsoLocal();
  compra.approvedAt = null;
}

function cancelarCompra(compraId){
  const compra = state.compras.find(c => c.id === compraId);
  if(!compra) throw new Error("Solicitacao de compra nao encontrada.");
  if(compra.titleId) throw new Error("Nao e possivel cancelar uma solicitacao ja aprovada.");
  compra.status = "CANCELADO";
  compra.rejectedAt = null;
  compra.approvedAt = null;
}

function nowIsoLocal(){
  return new Date().toISOString();
}

function openTituloDaCompra(compraId){
  const compra = state.compras.find(c => c.id === compraId);
  if(!compra?.titleId) return;
  setView("pagar");
  openTituloModal(compra.titleId, "AP");
}

function renderCompraPreview(){
  const fotoBox = $("#compraFotoPreview");
  const linkBox = $("#compraLinkPreview");
  const statusBox = $("#compraStatusInfo");
  if(!fotoBox || !linkBox || !statusBox) return;

  const fotoUrl = ($("#pcFotoUrl").value || "").trim();
  const produtoUrl = ($("#pcProdutoUrl").value || "").trim();
  const compra = editCompraId ? state.compras.find(c => c.id === editCompraId) : null;

  fotoBox.innerHTML = fotoUrl
    ? `<img src="${escapeHtml(fotoUrl)}" alt="produto" onerror="this.replaceWith(document.createTextNode('Nao foi possivel carregar a foto informada.'))" />`
    : `Informe uma URL de imagem para visualizar a foto do produto.`;

  linkBox.innerHTML = produtoUrl
    ? `<a href="${escapeHtml(produtoUrl)}" target="_blank" rel="noopener noreferrer">${escapeHtml(produtoUrl)}</a>`
    : `Informe o link do produto para facilitar a aprovacao.`;

  statusBox.textContent = compra?.titleId
    ? "Esta solicitacao ja gerou um contas a pagar. Alteracoes salvas aqui atualizam o titulo vinculado."
    : "A solicitacao nasce como pendente. A aprovacao gera automaticamente um titulo em contas a pagar.";
}

function renderCompraAiResults(){
  const btn = $("#btnPesquisaIA");
  const statusBox = $("#compraAiStatus");
  const resultsBox = $("#compraAiResultados");
  if(!statusBox || !resultsBox) return;

  if(btn){
    btn.disabled = compraAiState.loading;
    btn.textContent = compraAiState.loading ? "Pesquisando..." : "Pesquisa I.A";
  }

  if(compraAiState.loading){
    statusBox.textContent = "Varrendo lojas online e organizando ofertas por faixa de preco e aderencia.";
    resultsBox.innerHTML = `<div class="muted">Aguarde um instante, o motor Python esta pesquisando na web.</div>`;
    return;
  }

  if(compraAiState.error){
    statusBox.textContent = compraAiState.error;
    resultsBox.innerHTML = `<div class="muted">${escapeHtml(compraAiState.error)}</div>`;
    return;
  }

  if(!compraAiState.offers.length){
    statusBox.textContent = "Descreva o item e clique em Pesquisa I.A para comparar opcoes.";
    resultsBox.innerHTML = `<div class="muted">Nenhuma pesquisa realizada ainda.</div>`;
    return;
  }

  statusBox.textContent = `${compraAiState.offers.length} oferta(s) encontrada(s). Escolha uma opcao para preencher a solicitacao.`;

  const groups = Object.keys(COMPRA_AI_CATEGORY_META)
    .map(category => ({
      category,
      meta: COMPRA_AI_CATEGORY_META[category],
      items: compraAiState.offers
        .map((offer, index) => ({ offer, index }))
        .filter(entry => entry.offer.category === category)
    }))
    .filter(group => group.items.length);

  const summaryHtml = compraAiState.summary
    ? `<div class="aiSummary">${escapeHtml(compraAiState.summary)}</div>`
    : "";

  const queryHtml = compraAiState.query
    ? `<div class="muted" style="margin-top:8px">Consulta: <b>${escapeHtml(compraAiState.query)}</b></div>`
    : "";

  const groupsHtml = groups.map(group => `
    <section class="aiGroup">
      <div class="aiGroupTitle">${escapeHtml(group.meta.title)}</div>
      <div class="aiOfferList">
        ${group.items.map(({offer, index}) => {
          const priceValue = Number(offer.priceValue || 0);
          const priceLabel = priceValue > 0 ? brl(priceValue) : (offer.priceText || "Preco nao informado");
          const cardClasses = ["aiOfferCard"];
          const isSelected = isSelectedCompraAiOffer(offer, index);
          if(group.meta.cardClass) cardClasses.push(group.meta.cardClass);
          if(isSelected) cardClasses.push("selected");
          return `
            <article class="${cardClasses.join(" ")}">
              <div class="aiOfferHead">
                <div>
                  <h4 class="aiOfferTitle">${escapeHtml(offer.title || "Oferta encontrada")}</h4>
                  <div class="aiOfferStore">${escapeHtml(offer.store || "Loja nao informada")}</div>
                </div>
                <div>
                  ${isSelected ? `<div class="badge ok" style="margin-bottom:6px">Oferta selecionada</div>` : ""}
                  <div class="aiOfferPrice">${escapeHtml(priceLabel)}</div>
                </div>
              </div>
              <div class="aiOfferReason">${escapeHtml(offer.reason || "Link sugerido pela Pesquisa I.A.")}</div>
              <div class="aiOfferActions">
                <a class="btn" href="${escapeHtml(offer.url || "#")}" target="_blank" rel="noopener noreferrer">Abrir link</a>
                <button class="btn primary" type="button" data-act="useAiOffer" data-idx="${index}">${isSelected ? "Oferta aplicada" : "Usar esta oferta"}</button>
              </div>
            </article>
          `;
        }).join("")}
      </div>
    </section>
  `).join("");

  const sourcesHtml = Array.isArray(compraAiState.sources) && compraAiState.sources.length
    ? `
      <div class="aiSources">
        <div class="muted"><b>Fontes consultadas</b></div>
        <ul>
          ${compraAiState.sources.map(source => `
            <li><a href="${escapeHtml(source.url || "#")}" target="_blank" rel="noopener noreferrer">${escapeHtml(source.title || source.url || "Fonte")}</a></li>
          `).join("")}
        </ul>
      </div>
    `
    : "";

  resultsBox.innerHTML = `${queryHtml}${summaryHtml}${groupsHtml}${sourcesHtml}`;
}

async function pesquisarCompraComIA(){
  const draft = currentCompraDraft();
  if((draft.desc || "").trim().length < 3){
    alert("Descreva o produto ou servico antes de usar a Pesquisa I.A.");
    return;
  }

  compraAiState = {
    ...defaultCompraAiState(),
    loading: true
  };
  renderCompraAiResults();

  try{
    const payload = await requestJson("/api/finance/purchase-research", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        desc: draft.desc,
        fornecedor: draft.fornecedor,
        justificativa: draft.justificativa,
        obs: draft.obs,
        produtoUrl: draft.produtoUrl
      })
    });

    compraAiState = {
      ...defaultCompraAiState(),
      query: payload?.query || draft.desc,
      summary: payload?.summary || "",
      offers: Array.isArray(payload?.offers) ? payload.offers : [],
      sources: Array.isArray(payload?.sources) ? payload.sources : [],
      generatedAt: payload?.generatedAt || "",
      model: payload?.model || ""
    };
  }catch(err){
    compraAiState = {
      ...defaultCompraAiState(),
      error: err?.message || "Nao foi possivel executar a Pesquisa I.A."
    };
  }

  renderCompraAiResults();
}

function aplicarOfertaPesquisaIA(index){
  const offer = compraAiState.offers[index];
  if(!offer) return;

  compraAiState.selectedOfferIndex = index;
  compraAiState.selectedOffer = cloneCompraAiPayload(offer);

  $("#pcFornecedor").value = offer.store || $("#pcFornecedor").value;
  $("#pcProdutoUrl").value = offer.url || $("#pcProdutoUrl").value;

  const priceValue = Number(offer.priceValue || 0);
  if(Number.isFinite(priceValue) && priceValue > 0){
    $("#pcValor").value = String(priceValue);
  }
  if(!$("#pcDesc").value.trim()){
    $("#pcDesc").value = offer.title || "";
  }

  renderCompraPreview();
  renderCompraAiResults();
}

function openCompraModal(id=null){
  editCompraId = id;
  resetCompraAiState();
  $("#modalCompra").classList.remove("hidden");
  $("#modalCompraTitle").textContent = id ? "Editar solicitacao de compra" : "Nova solicitacao de compra";

  const compra = id ? state.compras.find(c => c.id === id) : null;
  $("#pcData").value = compra?.requestedAt ? String(compra.requestedAt).slice(0, 10) : toISODate(new Date());
  $("#pcFornecedor").value = compra?.fornecedor || "";
  $("#pcDesc").value = compra?.desc || "";
  $("#pcProdutoUrl").value = compra?.produtoUrl || "";
  $("#pcFotoUrl").value = compra?.fotoUrl || "";
  $("#pcJustificativa").value = compra?.justificativa || "";
  $("#pcConta").value = compra?.contaId || $("#pcConta").value;
  $("#pcCategoria").value = compra?.categoriaId || $("#pcCategoria").value;
  $("#pcCentroCusto").value = compra?.centroCusto || "";
  $("#pcFormaPagamento").value = compra?.formaPagamento || "";
  $("#pcVenc").value = compra?.vencimento || toISODate(new Date());
  $("#pcValor").value = compra ? Number(compra.valor || 0) : "";
  $("#pcObs").value = compra?.obs || "";
  if(compra?.aiResearch){
    compraAiState = hydrateCompraAiState(compra.aiResearch);
  }
  renderCompraPreview();
  renderCompraAiResults();
}

function closeCompraModal(){
  $("#modalCompra").classList.add("hidden");
  resetCompraAiState();
  renderCompraAiResults();
  editCompraId = null;
}

function currentCompraDraft(){
  return {
    requestedAt: ($("#pcData").value || toISODate(new Date())) + "T00:00:00",
    fornecedor: $("#pcFornecedor").value.trim(),
    desc: $("#pcDesc").value.trim(),
    produtoUrl: $("#pcProdutoUrl").value.trim(),
    fotoUrl: $("#pcFotoUrl").value.trim(),
    justificativa: $("#pcJustificativa").value.trim(),
    contaId: $("#pcConta").value,
    categoriaId: $("#pcCategoria").value,
    centroCusto: $("#pcCentroCusto").value.trim(),
    formaPagamento: $("#pcFormaPagamento").value.trim(),
    vencimento: $("#pcVenc").value,
    valor: Number($("#pcValor").value),
    obs: $("#pcObs").value.trim(),
    aiResearch: buildPersistedCompraAiState(compraAiState)
  };
}

function renderCompras(){
  const tb = $("#tbCompras");
  if(!tb) return;

  const filtroStatus = $("#cpStatus").value || "ALL";
  const busca = ($("#cpBusca").value || "").trim().toLowerCase();
  const contaById = new Map(state.contas.map(c => [c.id, c]));

  let list = [...state.compras];
  if(filtroStatus !== "ALL") list = list.filter(c => c.status === filtroStatus);
  if(busca){
    list = list.filter(c => `${c.desc} ${c.fornecedor} ${c.justificativa}`.toLowerCase().includes(busca));
  }

  list.sort((a, b)=> String(b.requestedAt || "").localeCompare(String(a.requestedAt || "")));

  tb.innerHTML = list.map(compra=>{
    const conta = contaById.get(compra.contaId);
    const temTitulo = !!compra.titleId;
    const podeAprovar = !temTitulo && (compra.status === "PENDENTE" || compra.status === "REPROVADO");
    const podeReprovar = !temTitulo && compra.status === "PENDENTE";
    const podeCancelar = !temTitulo && compra.status !== "CANCELADO";
    return `
      <tr>
        <td>${escapeHtml(String(compra.requestedAt || "").slice(0, 10))}</td>
        <td>${escapeHtml(compra.desc || "")}</td>
        <td>${escapeHtml(compra.fornecedor || "-")}</td>
        <td>${escapeHtml(conta?.nome || "-")}</td>
        <td>${escapeHtml(compra.formaPagamento || "-")}</td>
        <td>${escapeHtml(compra.vencimento || "-")}</td>
        <td class="right"><b>${brl(compra.valor)}</b></td>
        <td>${compraStatusBadge(compra.status)}</td>
        <td>${temTitulo ? `<button class="btn" data-act="openTitle" data-id="${compra.id}">Abrir AP</button>` : `<span class="muted">Pendente</span>`}</td>
        <td class="right">
          <button class="btn" data-act="edit" data-id="${compra.id}">Editar</button>
          <button class="btn primary" data-act="approve" data-id="${compra.id}" ${podeAprovar ? "" : "disabled"}>Aprovar</button>
          <button class="btn" data-act="reject" data-id="${compra.id}" ${podeReprovar ? "" : "disabled"}>Reprovar</button>
          <button class="btn danger" data-act="cancel" data-id="${compra.id}" ${podeCancelar ? "" : "disabled"}>Cancelar</button>
        </td>
      </tr>
    `;
  }).join("") || `<tr><td colspan="10" class="muted">Nenhuma solicitacao de compra.</td></tr>`;
}

$("#btnFiltrarCompras")?.addEventListener("click", renderCompras);
$("#btnNovaCompra")?.addEventListener("click", ()=> openCompraModal());
$("#btnFecharModalCompra")?.addEventListener("click", closeCompraModal);
$("#btnCancelarCompra")?.addEventListener("click", closeCompraModal);
$("#modalCompra")?.addEventListener("click", (e)=>{ if(e.target.id === "modalCompra") closeCompraModal(); });
$("#pcFotoUrl")?.addEventListener("input", renderCompraPreview);
$("#pcProdutoUrl")?.addEventListener("input", renderCompraPreview);
$("#btnPesquisaIA")?.addEventListener("click", pesquisarCompraComIA);
$("#compraAiResultados")?.addEventListener("click", (e)=>{
  const btn = e.target.closest("button[data-act='useAiOffer']");
  if(!btn) return;
  aplicarOfertaPesquisaIA(Number(btn.dataset.idx));
});

$("#btnSalvarCompra")?.addEventListener("click", ()=>{
  const draft = currentCompraDraft();
  if(!draft.desc || !draft.justificativa || !draft.contaId || !draft.categoriaId || !draft.formaPagamento || !draft.vencimento || !Number.isFinite(draft.valor) || draft.valor <= 0){
    alert("Preencha produto, justificativa, conta, categoria, forma de pagamento, vencimento e valor.");
    return;
  }

  if(editCompraId){
    const compra = state.compras.find(c => c.id === editCompraId);
    if(!compra) return;
    Object.assign(compra, draft);
    if(compra.titleId) syncCompraToTitulo(compra);
  } else {
    state.compras.unshift(novoPedidoCompra(draft));
  }

  saveState();
  renderAll();
  closeCompraModal();
});

$("#tbCompras")?.addEventListener("click", (e)=>{
  const btn = e.target.closest("button");
  if(!btn) return;
  const compraId = btn.dataset.id;
  const act = btn.dataset.act;
  const compra = state.compras.find(c => c.id === compraId);
  if(!compra) return;

  try{
    if(act === "edit") openCompraModal(compraId);
    if(act === "openTitle") openTituloDaCompra(compraId);
    if(act === "approve"){
      aprovarCompra(compraId);
      saveState();
      renderAll();
      alert("Solicitacao aprovada e contas a pagar gerado.");
    }
    if(act === "reject"){
      reprovarCompra(compraId);
      saveState();
      renderAll();
      alert("Solicitacao reprovada.");
    }
    if(act === "cancel"){
      cancelarCompra(compraId);
      saveState();
      renderAll();
      alert("Solicitacao cancelada.");
    }
  }catch(err){
    alert(err?.message || "Nao foi possivel atualizar a solicitacao.");
  }
});

/* ---------- Views AP/AR ---------- */
function renderAPAR(){
  renderTabelaTitulos("AP");
  renderTabelaTitulos("AR");
}

function renderTabelaTitulos(tipo){
  const isAP = tipo==="AP";
  const tb = isAP ? $("#tbAP") : $("#tbAR");

  const selConta = (isAP ? $("#apConta").value : $("#arConta").value) || "ALL";
  const selStatus = (isAP ? $("#apStatus").value : $("#arStatus").value) || "ALL";
  const ini = (isAP ? $("#apIni").value : $("#arIni").value) || "";
  const fim = (isAP ? $("#apFim").value : $("#arFim").value) || "";
  const busca = ((isAP ? $("#apBusca").value : $("#arBusca").value) || "").trim().toLowerCase();

  const contaById = new Map(state.contas.map(c=>[c.id,c]));

  let list = state.titulos.filter(t=>t.tipo===tipo);

  if(selConta!=="ALL") list = list.filter(t=>t.contaId===selConta);
  if(selStatus!=="ALL") list = list.filter(t=>t.status===selStatus);
  if(ini) list = list.filter(t=>t.vencimento >= ini);
  if(fim) list = list.filter(t=>t.vencimento <= fim);
  if(busca){
    list = list.filter(t=>{
      const s = `${t.desc} ${t.pessoa} ${t.centroCusto}`.toLowerCase();
      return s.includes(busca);
    });
  }

  list.sort((a,b)=> a.vencimento.localeCompare(b.vencimento));

  tb.innerHTML = list.map(t=>{
    const conta = contaById.get(t.contaId);
    const pagamento = getTituloFormaPagamento(t);
    const st = t.status==="ABERTO" ? `<span class="badge warn">ABERTO</span>`
            : t.status==="BAIXADO" ? `<span class="badge ok">BAIXADO</span>`
            : `<span class="badge bad">CANCELADO</span>`;

    const anexos = (t.anexos?.length||0);
    const anexBadge = anexos ? `<span class="badge">${anexos}</span>` : `<span class="muted">0</span>`;
    const canBaixar = t.status==="ABERTO";

    return `
      <tr>
        <td>${escapeHtml(t.vencimento)}</td>
        <td>${escapeHtml(conta?.nome || "-")}</td>
        <td>${escapeHtml(t.centroCusto || "-")}</td>
        <td>${escapeHtml(t.pessoa || "-")}</td>
        <td>${escapeHtml(t.desc || "")}</td>
        <td>${escapeHtml(pagamento || "-")}</td>
        <td class="right"><b>${brl(t.valor)}</b></td>
        <td>${st}</td>
        <td>${anexBadge}</td>
        <td class="right">
          <button class="btn" data-act="edit" data-id="${t.id}">Editar</button>
          <button class="btn ${canBaixar?'primary':''}" data-act="baixar" data-id="${t.id}" ${canBaixar?'':'disabled'}>Baixar</button>
          <button class="btn danger" data-act="cancel" data-id="${t.id}">Cancelar</button>
        </td>
      </tr>
    `;
  }).join("") || `<tr><td colspan="10" class="muted">Nenhum título.</td></tr>`;
}

$("#btnFiltrarAP").addEventListener("click", ()=>renderTabelaTitulos("AP"));
$("#btnFiltrarAR").addEventListener("click", ()=>renderTabelaTitulos("AR"));
$("#btnNovoAP").addEventListener("click", ()=>openTituloModal(null,"AP"));
$("#btnNovoAR").addEventListener("click", ()=>openTituloModal(null,"AR"));

$("#tbAP").addEventListener("click",(e)=>{
  const btn=e.target.closest("button"); if(!btn) return;
  handleTituloAction(btn.dataset.act, btn.dataset.id);
});
$("#tbAR").addEventListener("click",(e)=>{
  const btn=e.target.closest("button"); if(!btn) return;
  handleTituloAction(btn.dataset.act, btn.dataset.id);
});

function handleTituloAction(act, id){
  const t = state.titulos.find(x=>x.id===id);
  if(!t) return;

  if(act==="edit") openTituloModal(id, t.tipo);

  if(act==="baixar"){
    try{
      baixarTitulo(id, toISODate(new Date()));
      saveState();
      renderAll();
      alert("Baixado e lançamento criado.");
    }catch(err){
      alert(err?.message || "Não foi possível baixar.");
    }
  }

  if(act==="cancel"){
    if(confirm("Cancelar este título?")){
      t.status="CANCELADO";
      saveState();
      renderAll();
    }
  }
}

/* ---------- Modal Título (AP/AR) ---------- */
let editTituloId = null;
let previewAnexoId = null;

function syncParcelamentoUi(){
  const isNewTitulo = !editTituloId;
  const enabled = isNewTitulo && $("#tGerarParcelas").checked;
  const qtdParcelas = clamp(Number($("#tParcelas").value || 2), 2, 60);
  const valorTotal = Number($("#tValor").value);

  $("#tGerarParcelas").disabled = !isNewTitulo;
  $("#parcelamentoCampos").classList.toggle("hidden", !enabled);
  $("#tValorLabel").textContent = enabled ? "Valor total (R$)" : "Valor (R$)";
  $("#parcelamentoHint").textContent = isNewTitulo
    ? "Disponivel para novos lancamentos. O valor sera tratado como total."
    : "Parcelamento fica disponivel apenas na criacao de um novo titulo.";

  if(!$("#tPrimeiraParcela").value){
    $("#tPrimeiraParcela").value = $("#tVenc").value || toISODate(new Date());
  }

  if(enabled && Number.isFinite(valorTotal) && valorTotal > 0){
    const parcelas = splitAmount(valorTotal, qtdParcelas).map(brl);
    const exemplo = parcelas.slice(0, 3).join(", ");
    $("#parcelamentoResumo").textContent = `Serao geradas ${qtdParcelas} parcelas mensais. Ex.: ${exemplo}${parcelas.length > 3 ? "..." : ""}`;
    return;
  }

  $("#parcelamentoResumo").textContent = "As parcelas serao geradas mensalmente a partir do primeiro vencimento.";
}

function criarTitulosParcelados(draft){
  const parcelas = clamp(Number(draft.parcelas || 1), 2, 60);
  const primeiraParcela = draft.primeiraParcela || draft.vencimento;
  const valores = splitAmount(draft.valor, parcelas);
  const createdIds = [];
  const baseMeta = { formaPagamento: draft.formaPagamento };

  for(let index = 0; index < parcelas; index++){
    const titulo = novoTitulo({
      ...draft,
      valor: valores[index],
      vencimento: addMonthsISO(primeiraParcela, index),
      desc: `${draft.desc} (${index + 1}/${parcelas})`,
      obs: buildTitleObs(
        [draft.obs, `Parcela ${index + 1}/${parcelas}`].filter(Boolean).join(" | "),
        baseMeta
      )
    });
    titulo.status = draft.status || "ABERTO";
    state.titulos.unshift(titulo);
    createdIds.push(titulo.id);
  }

  return createdIds;
}

function openTituloModal(id, tipoDefault="AP"){
  editTituloId = id;
  previewAnexoId = null;
  $("#modalTitulo").classList.remove("hidden");

  const t = id ? state.titulos.find(x=>x.id===id) : null;
  const parsedObs = parseTitleObs(t?.obs);
  const compraVinculada = id ? getCompraByTituloId(id) : null;
  $("#modalTituloTitle").textContent = id ? "Editar título" : "Novo título";

  $("#tTipo").value = t?.tipo || tipoDefault;
  $("#tTipo").disabled = !!compraVinculada;
  fillSelects();
  $("#tStatus").value = t?.status || "ABERTO";
  $("#tVenc").value = t?.vencimento || toISODate(new Date());
  $("#tConta").value = t?.contaId || (state.contas[0]?.id || "");
  $("#tCentroCusto").value = t?.centroCusto || "";
  $("#tPessoa").value = t?.pessoa || "";
  $("#tDesc").value = t?.desc || "";
  $("#tCategoria").value = t?.categoriaId || $("#tCategoria").value;
  $("#tValor").value = t ? Number(t.valor||0) : "";
  $("#tFormaPagamento").value = parsedObs.meta.formaPagamento || "";
  $("#tObs").value = parsedObs.text || "";
  $("#tGerarParcelas").checked = false;
  $("#tParcelas").value = 2;
  $("#tPrimeiraParcela").value = t?.vencimento || $("#tVenc").value;
  $("#tVenc").dataset.prevValue = $("#tVenc").value;

  renderAnexos();
  renderAnexoPreview(null);
  syncParcelamentoUi();
  updateBaixaButton();
}

function closeTituloModal(){
  $("#modalTitulo").classList.add("hidden");
  editTituloId = null;
  previewAnexoId = null;
  $("#tTipo").disabled = false;
  renderAnexoPreview(null);
}

function currentTituloDraft(){
  return {
    tipo: $("#tTipo").value,
    status: $("#tStatus").value,
    vencimento: $("#tVenc").value,
    contaId: $("#tConta").value,
    centroCusto: $("#tCentroCusto").value.trim(),
    pessoa: $("#tPessoa").value.trim(),
    desc: $("#tDesc").value.trim(),
    categoriaId: $("#tCategoria").value,
    valor: Number($("#tValor").value),
    formaPagamento: $("#tFormaPagamento").value.trim(),
    obs: $("#tObs").value.trim(),
    gerarParcelas: $("#tGerarParcelas").checked,
    parcelas: clamp(Number($("#tParcelas").value || 1), 1, 60),
    primeiraParcela: $("#tPrimeiraParcela").value || $("#tVenc").value
  };
}

function syncCompraFromTitulo(titulo, draft, meta){
  const compraId = meta?.compraId || "";
  if(!compraId) return;

  const compra = state.compras.find(item => item.id === compraId);
  if(!compra) return;

  compra.fornecedor = draft.pessoa;
  compra.desc = draft.desc;
  compra.categoriaId = draft.categoriaId;
  compra.contaId = draft.contaId;
  compra.valor = draft.valor;
  compra.vencimento = draft.vencimento;
  compra.centroCusto = draft.centroCusto;
  compra.formaPagamento = draft.formaPagamento;
  compra.obs = draft.obs;
}

function updateBaixaButton(){
  const t = editTituloId ? state.titulos.find(x=>x.id===editTituloId) : null;
  const can = (t && t.status === "ABERTO");
  $("#btnBaixarTitulo").disabled = !can;
}

function renderAnexos(){
  const t = editTituloId ? state.titulos.find(x=>x.id===editTituloId) : null;
  const anexos = t?.anexos || [];
  $("#listaAnexos").innerHTML = anexos.length ? anexos.map(a=>`
    <div class="item ${previewAnexoId===a.id?'selected':''}">
      <div class="left" style="flex:1">
        <span class="badge">${a.mime.includes("pdf") ? "PDF" : "IMG"}</span>
        <div style="min-width:0">
          <div><b>${escapeHtml(a.name)}</b></div>
          <div class="muted">${escapeHtml(a.mime)}${a.path ? ` - ${escapeHtml(a.path)}` : ""}</div>
        </div>
      </div>
      <div class="row gap">
        <button class="btn" data-act="view" data-id="${a.id}">Ver</button>
        <button class="btn danger" data-act="del" data-id="${a.id}">Remover</button>
      </div>
    </div>
  `).join("") : `<div class="muted">Nenhum anexo.</div>`;
}

function renderAnexoPreview(anexo){
  const box = $("#anexoPreview");
  if(!anexo){
    box.innerHTML = `Selecione um anexo para visualizar.`;
    detectCodesFromAttachment(null);
    return;
  }
  const src = filePreviewSrc(anexo);
  if(!src){
    box.textContent = "Arquivo sem URL de visualizacao.";
    detectCodesFromAttachment(null);
    return;
  }
  if(anexo.mime.includes("pdf")){
    box.innerHTML = `<iframe src="${src}" style="width:100%;height:360px;border:0;border-radius:12px"></iframe>`;
    detectCodesFromAttachment(anexo);
  } else if(anexo.mime.startsWith("image/")){
    box.innerHTML = `<img src="${src}" alt="anexo" style="max-width:100%;border-radius:12px" />`;
    detectCodesFromAttachment(anexo);
  } else {
    box.textContent = "Formato não suportado na prévia.";
    detectCodesFromAttachment(null);
  }
}

$("#btnFecharModalTitulo").addEventListener("click", closeTituloModal);
$("#btnCancelarTitulo").addEventListener("click", closeTituloModal);
$("#modalTitulo").addEventListener("click",(e)=>{ if(e.target.id==="modalTitulo") closeTituloModal(); });
$("#tGerarParcelas").addEventListener("change", syncParcelamentoUi);
$("#tParcelas").addEventListener("input", syncParcelamentoUi);
$("#tValor").addEventListener("input", syncParcelamentoUi);
$("#tVenc").addEventListener("change", ()=>{
  if(!$("#tPrimeiraParcela").value || $("#tPrimeiraParcela").value === $("#tVenc").dataset.prevValue){
    $("#tPrimeiraParcela").value = $("#tVenc").value;
  }
  $("#tVenc").dataset.prevValue = $("#tVenc").value;
  syncParcelamentoUi();
});

$("#btnSalvarTitulo").addEventListener("click", ()=>{
  const d = currentTituloDraft();
  if(!d.vencimento || !d.contaId || !d.categoriaId || !d.desc || !Number.isFinite(d.valor) || d.valor<=0){
    alert("Preencha vencimento, conta, categoria, descrição e valor.");
    return;
  }

  if(editTituloId){
    const t = state.titulos.find(x=>x.id===editTituloId);
    if(!t) return;
    const oldMeta = parseTitleObs(t.obs).meta;
    t.tipo = d.tipo;
    t.status = d.status;
    t.vencimento = d.vencimento;
    t.contaId = d.contaId;
    t.categoriaId = d.categoriaId;
    t.desc = d.desc;
    t.pessoa = d.pessoa;
    t.valor = d.valor;
    t.centroCusto = d.centroCusto;
    t.obs = buildTitleObs(d.obs, { ...oldMeta, formaPagamento: d.formaPagamento });
    syncCompraFromTitulo(t, d, oldMeta);
  } else {
    if(d.gerarParcelas && d.parcelas > 1){
      const createdIds = criarTitulosParcelados(d);
      editTituloId = createdIds[0] || null;
      saveState();
      renderAll();
      if(editTituloId) openTituloModal(editTituloId, d.tipo);
      alert(`${createdIds.length} parcelas criadas com sucesso.`);
      return;
    }

    const t = novoTitulo({ ...d, obs: buildTitleObs(d.obs, { formaPagamento: d.formaPagamento }) });
    t.status = d.status || "ABERTO";
    state.titulos.unshift(t);
    editTituloId = t.id;
  }

  saveState();
  renderAll();
  openTituloModal(editTituloId, d.tipo);
});

$("#btnBaixarTitulo").addEventListener("click", ()=>{
  if(!editTituloId) return;
  try{
    baixarTitulo(editTituloId, toISODate(new Date()));
    saveState();
    renderAll();
    openTituloModal(editTituloId, $("#tTipo").value);
  }catch(err){
    alert(err?.message || "Não foi possível baixar.");
  }
});

$("#btnAddAnexo").addEventListener("click", async ()=>{
  if(!editTituloId){
    alert("Salve o título primeiro para anexar arquivos.");
    return;
  }
  const file = $("#tAnexoFile").files?.[0];
  if(!file) return alert("Selecione um arquivo (PDF/Imagem).");

  if(file.size > 15 * 1024 * 1024){
    alert("Arquivo muito grande. Use até 15MB por anexo.");
    return;
  }

  const t = state.titulos.find(x=>x.id===editTituloId);
  if(!t) return;

  try{
    const uploaded = await uploadTituloAttachment(file, t);
    if(!uploaded) throw new Error("Upload nao retornou metadados do anexo.");
    t.anexos.push(uploaded);

    $("#tAnexoFile").value = "";
    await saveState();
    renderAll();
    openTituloModal(editTituloId, t.tipo);
    previewAnexoId = uploaded.id;
    renderAnexos();
    renderAnexoPreview(uploaded);
  }catch(err){
    alert(err?.message || "Nao foi possivel enviar o anexo.");
  }
});

$("#listaAnexos").addEventListener("click", async (e)=>{
  const btn = e.target.closest("button");
  if(!btn) return;
  const act = btn.dataset.act;
  const id = btn.dataset.id;
  const t = editTituloId ? state.titulos.find(x=>x.id===editTituloId) : null;
  if(!t) return;

  if(act==="view"){
    const a = t.anexos.find(x=>x.id===id);
    previewAnexoId = id;
    renderAnexos();
    renderAnexoPreview(a);
  }
  if(act==="del"){
    if(confirm("Remover anexo?")){
      const anexo = t.anexos.find(x=>x.id===id);
      t.anexos = t.anexos.filter(x=>x.id!==id);
      if(previewAnexoId===id){ previewAnexoId=null; renderAnexoPreview(null); }
      try{
        await removeTituloAttachmentFile(anexo);
      }catch(err){
        console.warn("Falha ao remover arquivo fisico do anexo:", err);
      }
      await saveState();
      renderAnexos();
    }
  }
});

/* ---------- Config ---------- */
function financeAiBadgeClass(level){
  if(level === "ok") return "ok";
  if(level === "bad") return "bad";
  return "warn";
}

function financeAiBadgeLabel(code, level){
  if(code === "scraper_ready") return "Pronto";
  if(code === "search_ok") return "Busca OK";
  if(code === "search_empty") return "Sem links";
  if(code === "search_unreachable") return "Busca";
  if(code === "network_error") return "Rede";
  if(level === "ok") return "Pronto";
  if(level === "bad") return "Falha";
  return "Atencao";
}

function renderFinanceAiStatus(){
  const badge = $("#aiStatusBadge");
  const checkedAt = $("#aiStatusCheckedAt");
  const messageBox = $("#aiStatusMessage");
  const detailsBox = $("#aiStatusDetails");
  const refreshBtn = $("#btnAtualizarAiStatus");
  if(!badge || !checkedAt || !messageBox || !detailsBox) return;

  if(refreshBtn){
    refreshBtn.disabled = financeAiDiagState.loading;
    refreshBtn.textContent = financeAiDiagState.loading ? "Atualizando..." : "Atualizar status";
  }

  if(financeAiDiagState.loading){
    badge.className = "badge warn";
    badge.textContent = "Verificando...";
    checkedAt.textContent = "Consultando o servidor e a busca web.";
    messageBox.className = "statusBox";
    messageBox.textContent = "Executando diagnostico da pesquisa inteligente. Aguarde um instante.";
    detailsBox.innerHTML = `
      <div class="diagItem"><div class="diagLabel">Servidor</div><div class="diagValue">Lendo configuracao...</div></div>
      <div class="diagItem"><div class="diagLabel">Busca web</div><div class="diagValue">Testando raspagem...</div></div>
    `;
    return;
  }

  if(financeAiDiagState.error){
    badge.className = "badge bad";
    badge.textContent = "Falha";
    checkedAt.textContent = "Nao foi possivel consultar o diagnostico.";
    messageBox.className = "statusBox bad";
    messageBox.textContent = financeAiDiagState.error;
    detailsBox.innerHTML = `
      <div class="diagItem">
        <div class="diagLabel">Sugestao</div>
        <div class="diagValue">Confira se o servidor foi redeployado corretamente e se a rota /api/finance/ai-status esta respondendo.</div>
      </div>
    `;
    return;
  }

  const payload = financeAiDiagState.data;
  if(!payload){
    badge.className = "badge warn";
    badge.textContent = "Pendente";
    checkedAt.textContent = "Nenhum diagnostico executado ainda.";
    messageBox.className = "statusBox";
    messageBox.textContent = "Abra esta aba ou clique em atualizar para verificar o mecanismo Python de scraping.";
    detailsBox.innerHTML = "";
    return;
  }

  const status = payload.status || {};
  const probe = payload.probe || {};
  const config = payload.config || {};
  const level = financeAiBadgeClass(status.level);

  badge.className = `badge ${level}`;
  badge.textContent = financeAiBadgeLabel(status.code, status.level);
  checkedAt.textContent = `Ultima verificacao: ${formatDateTime(payload.checkedAt)}`;
  messageBox.className = `statusBox ${level}`;
  messageBox.textContent = status.message || "Diagnostico carregado.";

  detailsBox.innerHTML = [
    {
      label: "Provedor",
      value: config.provider || "-"
    },
    {
      label: "Mecanismo",
      value: config.engine || "-"
    },
    {
      label: "Busca web",
      value: config.searchUrl || "-"
    },
    {
      label: "Lojas monitoradas",
      value: config.allowedDomainsCount || "-"
    },
    {
      label: "Dominios permitidos",
      value: Array.isArray(config.allowedDomains) && config.allowedDomains.length
        ? config.allowedDomains.join(", ")
        : "-"
    },
    {
      label: "Timeout",
      value: config.timeoutSeconds ? `${config.timeoutSeconds}s` : "-"
    },
    {
      label: "Max. ofertas",
      value: config.maxOffers || "-"
    },
    {
      label: "User-Agent",
      value: config.userAgentConfigured ? "Configurado" : "Padrao"
    },
    {
      label: "Teste da busca",
      value: probe.attempted ? (probe.success ? "Conexao validada" : "Falhou") : "Nao executado"
    },
    {
      label: "Resultados de teste",
      value: probe.resultCount || "-"
    },
    {
      label: "Codigo",
      value: probe.errorCode || status.code || "-"
    },
    {
      label: "Detalhe",
      value: probe.message || "Sem detalhe adicional."
    }
  ].map(item => `
    <div class="diagItem">
      <div class="diagLabel">${escapeHtml(item.label)}</div>
      <div class="diagValue">${escapeHtml(String(item.value || "-"))}</div>
    </div>
  `).join("");
}

async function loadFinanceAiStatus({force=false}={}){
  if(!$("#aiStatusBadge")) return;
  if(financeAiDiagState.loading) return;

  const now = Date.now();
  if(!force && financeAiDiagState.loaded && (now - financeAiDiagState.lastLoadedAt) < 30000){
    renderFinanceAiStatus();
    return;
  }

  financeAiDiagState.loading = true;
  financeAiDiagState.error = "";
  renderFinanceAiStatus();

  try{
    const payload = await requestJson("/api/finance/ai-status?probe=1");
    financeAiDiagState = {
      loading: false,
      loaded: true,
      error: "",
      data: payload,
      lastLoadedAt: Date.now()
    };
  }catch(err){
    financeAiDiagState = {
      loading: false,
      loaded: false,
      error: err?.message || "Nao foi possivel carregar o diagnostico da I.A.",
      data: null,
      lastLoadedAt: 0
    };
  }

  renderFinanceAiStatus();
}

function renderConfig(){
  $("#cfgTolDias").value = state.config.tolDias;
  $("#cfgTolValor").value = state.config.tolValor;
  $("#cfgScoreMin").value = state.config.scoreMin;
  renderFinanceAiStatus();
  if(!$("#view-config")?.classList.contains("hidden")){
    loadFinanceAiStatus();
  }
}
$("#btnSalvarCfg").addEventListener("click", ()=>{
  state.config.tolDias = clamp(Number($("#cfgTolDias").value), 0, 30);
  state.config.tolValor = clamp(Number($("#cfgTolValor").value), 0, 999999);
  state.config.scoreMin = clamp(Number($("#cfgScoreMin").value), 0, 100);
  saveState();
  alert("Config salva.");
});
$("#btnRodarAvisos")?.addEventListener("click", ()=> triggerFinanceReminders());
$("#btnAtualizarAiStatus")?.addEventListener("click", ()=> loadFinanceAiStatus({ force: true }));

/* ---------- Backup JSON ---------- */
$("#btnExportJSON").addEventListener("click", ()=>{
  const blob = new Blob([JSON.stringify(state,null,2)], {type:"application/json"});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `gestao-financeira-backup_${new Date().toISOString().slice(0,10)}.json`;
  a.click();
  URL.revokeObjectURL(a.href);
});

$("#btnImportJSON").addEventListener("click", async ()=>{
  const file = $("#jsonFile").files?.[0];
  if(!file) return alert("Selecione um .json de backup.");
  try{
    const text = await file.text();
    const data = migrate(JSON.parse(text));
    replaceState(data);
    saveState();
    renderAll();
    alert("Backup importado com sucesso.");
  }catch{
    alert("JSON inválido.");
  }
});

$("#btnReset").addEventListener("click", ()=>{
  if(confirm("Apagar tudo?")){
    localStorage.removeItem(LS_KEY);
    replaceState(seed());
    saveState();
    renderAll();
  }
});

/* ---------- Boot ---------- */
(function init(){
  $("#dashMes").value = toISODate(new Date()).slice(0,7);
  $("#dashConta").value = "ALL";
  $("#fConta").value = "ALL";
  renderAll();
  triggerFinanceReminders({ silent: true });

  window.addEventListener("storage", (e)=>{
    if(e.key !== LS_KEY || !e.newValue) return;
    try{
      replaceState(JSON.parse(e.newValue));
      renderAll();
    }catch{
      console.warn("Falha ao ler atualizacao local.");
    }
  });

  if(remoteSync){
    remoteSync.bootstrap({
      getLocalSnapshot: ()=>state,
      applySnapshot: (snapshot)=>{
        replaceState(snapshot);
        renderAll();
      }
    });

    window.addEventListener("focus", ()=> remoteSync.syncNow());
    document.addEventListener("visibilitychange", ()=>{
      if(!document.hidden) remoteSync.syncNow();
    });
  }
})();
