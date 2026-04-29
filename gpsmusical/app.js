window.onerror = function(msg, src, line, col){
  alert(`GPS Musical: erro no JavaScript.\n\n${msg}\n${src || ""}\nlinha: ${line}, col: ${col}`);
  return false;
};

const STORAGE_KEY = "gps_musical_db_v6";
const AUTH_KEY = "gps_musical_auth_v1";
const IDB_NAME = "gps_musical_media";
const IDB_VER = 1;
const IDB_STORE = "audio";
const API_CONFIG_URL = "/api/gps/config";
const API_CONFIG_TEST_URL = "/api/gps/config/test-database";
const API_BACKUPS_URL = "/api/gps/backups";
const SPOTIFY_API_BASE = "https://api.spotify.com/v1";
const SPOTIFY_SEARCH_LIMIT = 10;
const SPOTIFY_SEARCH_DEBOUNCE_MS = 300;

const REMOTE_STORE = window.RemoteStoreClient
  ? window.RemoteStoreClient.create({
      storeId: "gps-musical",
      normalize: normalizeSongs,
      onError: (error, source) => {
        console.warn("GPS Musical sync error:", source, error);
        UI_setSyncStatus(`Falha na sync (${source}): ${error.message || error}`, "danger");
      },
    })
  : null;

const state = {
  songs: [],
  editingId: null,
  viewingId: null,
  currentBlockIndex: 0,
  currentTab: "biblioteca",
  config: defaultConfig(),
  auth: loadAuthState(),
  remoteBackups: [],
  timelineRAF: null,
  slideTimer: null,
  playingMode: null,
  markActive: false,
  markNextIndex: 0,
  editorAudioMeta: null,
  editorAudioBlob: null,
  youtubePlayer: null,
  youtubeReady: false,
  youtubePlayerReady: false,
  activeSourceType: "none",
  activeYouTubeId: "",
  activeSpotifyEmbedUrl: "",
  spotifyPlayer: null,
  spotifyPlaybackState: null,
  spotifyDeviceId: "",
  spotifySearchResults: [],
  spotifySearchQuery: "",
  spotifySearchDebounce: null,
  spotifyCurrentTrack: null,
  spotifyIsPlaying: false,
  syncStatusKind: "warn",
};

let youtubeApiResolve = null;
const youtubeApiReady = new Promise((resolve) => {
  youtubeApiResolve = resolve;
});

window.onYouTubeIframeAPIReady = function(){
  state.youtubeReady = true;
  if(youtubeApiResolve) youtubeApiResolve();
};

window.onSpotifyWebPlaybackSDKReady = function(){
  const accessToken = state.auth.spotify.accessToken;
  if(!accessToken) {
    console.log("Spotify Web Playback SDK ready, waiting for authentication");
    return;
  }

  const player = new Spotify.Player({
    name: "GPS Musical Player",
    getOAuthToken: async (cb) => {
      const token = await SPOTIFY_refreshIfNeeded();
      cb(token);
    },
    volume: 0.5,
  });

  state.spotifyPlayer = player;

  player.addListener("player_state_changed", (playbackState) => {
    state.spotifyPlaybackState = playbackState;
    SPOTIFY_updatePlaybackState(playbackState);
  });

  player.addListener("ready", ({ device_id }) => {
    state.spotifyDeviceId = device_id;
    console.log("Spotify device ready:", device_id);
  });

  player.addListener("not_ready", ({ device_id }) => {
    console.log("Spotify device not ready:", device_id);
  });

  player.connect();
};

function SPOTIFY_updatePlaybackState(playbackState){
  if(!playbackState) return;
  
  state.spotifyCurrentTrack = playbackState.track_window?.current_track;
  state.spotifyIsPlaying = !playbackState.paused;
  
  if(state.playingMode === "timeline" && state.activeSourceType === "spotify") {
    PLAYER_updateTimelineForSpotify(playbackState);
  }
}

function PLAYER_updateTimelineForSpotify(playbackState){
  if(!playbackState) return;
  
  const song = PLAYER_song();
  if(!song) return;

  const currentMs = playbackState.position;
  const currentSec = currentMs / 1000;

  const timedBlocks = song.blocks
    .map((block, index) => ({ index, time: Number(block.timeSec) }))
    .filter(item => Number.isFinite(item.time))
    .sort((a, b) => a.time - b.time);

  let active = timedBlocks[0];
  for(const item of timedBlocks) {
    if(item.time <= currentSec) active = item;
    else break;
  }

  if(active && active.index !== state.currentBlockIndex) {
    PLAYER_renderStage(active.index);
  }

  const durationMs = playbackState.track_window?.current_track?.duration_ms || 1;
  $("stageProgress").style.width = `${(Math.max(0, Math.min(1, currentSec / (durationMs / 1000))) * 100).toFixed(2)}%`;
  $("stageTime").textContent = `${fmtTime(currentSec)} / ${fmtTime(durationMs / 1000)}`;
}


function $(id){ return document.getElementById(id); }
function nowISO(){ return new Date().toISOString(); }
function uid(){ return crypto?.randomUUID?.() || `id-${Math.random().toString(16).slice(2)}-${Date.now()}`; }
function safeParseJSON(text){ try{ return JSON.parse(text); }catch{ return null; } }
function normalizeText(value){ return (value || "").toString().toLowerCase().trim(); }
function clamp(value, min, max){ return Math.max(min, Math.min(max, value)); }

function esc(value){
  return (value ?? "").toString()
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function fmtTime(seconds){
  const sec = Math.max(0, Number(seconds) || 0);
  const min = Math.floor(sec / 60);
  const rest = Math.floor(sec % 60);
  return `${String(min).padStart(2, "0")}:${String(rest).padStart(2, "0")}`;
}

function defaultConfig(){
  const redirectUri = `${location.origin}${location.pathname}`;
  return {
    database: {
      provider: "default",
      url: "",
      host: "",
      port: "3306",
      name: "",
      user: "",
      password: "",
      ssl: false,
    },
    youtube: {
      apiKey: "",
      clientId: "",
      redirectUri,
    },
    spotify: {
      clientId: "",
      clientSecret: "",
      redirectUri,
    },
    updatedAt: "",
  };
}

function loadAuthState(){
  const payload = safeParseJSON(localStorage.getItem(AUTH_KEY) || "");
  return {
    youtube: {
      accessToken: payload?.youtube?.accessToken || "",
      expiresAt: Number(payload?.youtube?.expiresAt || 0),
      profileName: payload?.youtube?.profileName || "",
    },
    spotify: {
      accessToken: payload?.spotify?.accessToken || "",
      refreshToken: payload?.spotify?.refreshToken || "",
      expiresAt: Number(payload?.spotify?.expiresAt || 0),
      profileName: payload?.spotify?.profileName || "",
    },
  };
}

function saveAuthState(){
  localStorage.setItem(AUTH_KEY, JSON.stringify(state.auth));
  UI_renderAuthStatus();
}

function authTokenValid(provider){
  const entry = state.auth[provider];
  return Boolean(entry?.accessToken && Number(entry.expiresAt || 0) > Date.now() + 60_000);
}

function normalizeAudioSource(rawSource, rawAudioMeta){
  const source = rawSource && typeof rawSource === "object" ? rawSource : null;
  if(source){
    const type = ["none", "local", "youtube", "spotify"].includes(source.type) ? source.type : "none";
    if(type === "youtube"){
      const parsed = parseYouTubeSource(source.url || source.videoId || "");
      return parsed
        ? { type: "youtube", label: String(source.label || parsed.label || "YouTube"), url: parsed.url, videoId: parsed.videoId }
        : { type: "none", label: "" };
    }
    if(type === "spotify"){
      const parsed = parseSpotifySource(source.url || source.uri || source.id || "");
      return parsed
        ? {
            type: "spotify",
            label: String(source.label || parsed.label || "Spotify"),
            url: parsed.url,
            uri: parsed.uri,
            resourceType: parsed.resourceType,
            resourceId: parsed.resourceId,
            embedUrl: parsed.embedUrl,
          }
        : { type: "none", label: "" };
    }
    if(type === "local"){
      return { type: "local", label: String(source.label || rawAudioMeta?.name || "MP3 local") };
    }
    return { type: "none", label: "" };
  }

  if(rawAudioMeta && typeof rawAudioMeta === "object"){
    return { type: "local", label: String(rawAudioMeta.name || "MP3 local") };
  }

  return { type: "none", label: "" };
}

function normalizeSongs(songs){
  if(!Array.isArray(songs)) return [];
  return songs.map((song) => {
    const audioMeta = song?.audioMeta && typeof song.audioMeta === "object"
      ? {
          name: String(song.audioMeta.name || ""),
          mime: String(song.audioMeta.mime || "audio/mpeg"),
        }
      : null;

    return {
      id: song?.id || uid(),
      title: String(song?.title || ""),
      artist: String(song?.artist || ""),
      key: String(song?.key || ""),
      tags: Array.isArray(song?.tags) ? song.tags.map((tag) => String(tag)).filter(Boolean) : [],
      notes: String(song?.notes || ""),
      audioMeta,
      audioSource: normalizeAudioSource(song?.audioSource, audioMeta),
      blocks: Array.isArray(song?.blocks)
        ? song.blocks
            .filter((block) => block && typeof block === "object")
            .map((block) => ({
              type: String(block.type || "Bloco"),
              title: String(block.title || ""),
              chords: String(block.chords || ""),
              lyrics: String(block.lyrics || ""),
              timeSec: block.timeSec === null || block.timeSec === "" || block.timeSec === undefined
                ? null
                : (Number.isFinite(Number(block.timeSec)) ? Number(block.timeSec) : null),
            }))
        : [],
      createdAt: String(song?.createdAt || nowISO()),
      updatedAt: String(song?.updatedAt || nowISO()),
    };
  });
}

function DB_load(){
  const raw = localStorage.getItem(STORAGE_KEY);
  if(!raw) return [];
  return normalizeSongs(safeParseJSON(raw) || []);
}

function DB_saveLocal(){
  localStorage.setItem(STORAGE_KEY, JSON.stringify(state.songs, null, 2));
}

function DB_replaceSongs(songs){
  state.songs = normalizeSongs(songs);
  DB_saveLocal();
}

function DB_save(){
  state.songs = normalizeSongs(state.songs);
  DB_saveLocal();
  if(REMOTE_STORE) REMOTE_STORE.queueSave(state.songs);
}

function UI_showTab(name){
  state.currentTab = name;
  ["biblioteca", "editor", "view", "docs", "config", "backup"].forEach((tabName) => {
    $(`tabBtn-${tabName}`)?.classList.toggle("active", tabName === name);
    $(`tab-${tabName}`)?.classList.toggle("active", tabName === name);
  });
}

function UI_setSyncStatus(message, kind = "warn"){
  state.syncStatusKind = kind;
  $("syncStatus").textContent = message;
  const badge = $("syncBackendBadge");
  badge.className = `pill ${kind === "ok" ? "ok" : kind === "danger" ? "danger" : "warn"}`;
}

function UI_updateBackendBadge(status){
  const badge = $("dbStatusBadge");
  if(!status?.ok){
    badge.textContent = "Sem conexão";
    badge.className = "pill danger";
    UI_setSyncStatus(status?.error || "Banco remoto indisponível.", "danger");
    return;
  }

  const label = status.usingExternal ? "Alwaysdata / externo" : "Banco padrão";
  badge.textContent = `${label} • ${status.provider || "db"}`;
  badge.className = "pill ok";
  $("syncBackendBadge").textContent = label;
  $("syncBackendBadge").className = "pill ok";
  UI_setSyncStatus(`Sincronização pronta em ${status.database || "banco configurado"}.`, "ok");
}

async function apiRequest(url, options = {}){
  const response = await fetch(url, {
    cache: "no-store",
    headers: { Accept: "application/json", ...(options.headers || {}) },
    ...options,
  });

  let payload = null;
  if(response.status !== 204){
    const text = await response.text();
    if(text){
      payload = safeParseJSON(text) || { error: text };
    }
  }

  if(!response.ok){
    throw new Error(payload?.error || `Falha na requisição (${response.status}).`);
  }

  return payload;
}

async function CFG_load(){
  try{
    const payload = await apiRequest(API_CONFIG_URL);
    state.config = payload?.config ? mergeConfig(payload.config) : defaultConfig();
    CFG_fillForm(state.config);
    UI_renderAuthStatus();
    UI_renderDatabaseStatus(payload?.databaseStatus || null);
  }catch(error){
    UI_renderDatabaseStatus({ ok: false, error: error.message || String(error) });
  }
}

function mergeConfig(raw){
  const base = defaultConfig();
  return {
    ...base,
    ...raw,
    database: { ...base.database, ...(raw?.database || {}) },
    youtube: { ...base.youtube, ...(raw?.youtube || {}) },
    spotify: { ...base.spotify, ...(raw?.spotify || {}) },
  };
}

function CFG_fillForm(config){
  $("cfgDbProvider").value = config.database.provider || "default";
  $("cfgDbUrl").value = config.database.url || "";
  $("cfgDbHost").value = config.database.host || "";
  $("cfgDbPort").value = config.database.port || "";
  $("cfgDbName").value = config.database.name || "";
  $("cfgDbUser").value = config.database.user || "";
  $("cfgDbPassword").value = config.database.password || "";
  $("cfgDbSsl").value = String(Boolean(config.database.ssl));
  $("cfgYoutubeApiKey").value = config.youtube.apiKey || "";
  $("cfgYoutubeClientId").value = config.youtube.clientId || "";
  $("cfgYoutubeRedirectUri").value = config.youtube.redirectUri || `${location.origin}${location.pathname}`;
  $("cfgSpotifyClientId").value = config.spotify.clientId || "";
  $("cfgSpotifyClientSecret").value = config.spotify.clientSecret || "";
  $("cfgSpotifyRedirectUri").value = config.spotify.redirectUri || `${location.origin}${location.pathname}`;
}

function CFG_collectForm(){
  return {
    database: {
      provider: $("cfgDbProvider").value,
      url: $("cfgDbUrl").value.trim(),
      host: $("cfgDbHost").value.trim(),
      port: $("cfgDbPort").value.trim(),
      name: $("cfgDbName").value.trim(),
      user: $("cfgDbUser").value.trim(),
      password: $("cfgDbPassword").value,
      ssl: $("cfgDbSsl").value === "true",
    },
    youtube: {
      apiKey: $("cfgYoutubeApiKey").value.trim(),
      clientId: $("cfgYoutubeClientId").value.trim(),
      redirectUri: $("cfgYoutubeRedirectUri").value.trim() || `${location.origin}${location.pathname}`,
    },
    spotify: {
      clientId: $("cfgSpotifyClientId").value.trim(),
      clientSecret: $("cfgSpotifyClientSecret").value,
      redirectUri: $("cfgSpotifyRedirectUri").value.trim() || `${location.origin}${location.pathname}`,
    },
  };
}

function UI_renderDatabaseStatus(status){
  const box = $("dbStatusBox");
  const text = $("dbStatusText");
  box.className = "status-box";

  if(!status){
    text.textContent = "Sem status do banco.";
    return;
  }

  if(status.ok){
    box.classList.add("ok");
    text.textContent = `Conectado em ${status.database || "banco configurado"} (${status.provider || "db"}).`;
  }else{
    box.classList.add("danger");
    text.textContent = status.error || "Falha ao conectar no banco.";
  }

  UI_updateBackendBadge(status);
}

async function CFG_save(){
  const config = CFG_collectForm();
  try{
    const payload = await apiRequest(API_CONFIG_URL, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(config),
    });
    state.config = mergeConfig(payload.config || config);
    CFG_fillForm(state.config);
    UI_renderDatabaseStatus(payload.databaseStatus);
    BK_set("Configuração salva.");
    await SYNC_pullOrPushAfterConfigChange();
  }catch(error){
    UI_renderDatabaseStatus({ ok: false, error: error.message || String(error) });
    BK_set(`Erro ao salvar configuração: ${error.message || error}`);
  }
}

async function CFG_test(){
  const config = CFG_collectForm();
  try{
    const payload = await apiRequest(API_CONFIG_TEST_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(config),
    });
    UI_renderDatabaseStatus(payload);
  }catch(error){
    UI_renderDatabaseStatus({ ok: false, error: error.message || String(error) });
  }
}

async function SYNC_pullOrPushAfterConfigChange(){
  try{
    const payload = await apiRequest("/api/stores/gps-musical");
    const remoteSongs = normalizeSongs(payload?.value || []);
    if(remoteSongs.length){
      DB_replaceSongs(remoteSongs);
      UI_renderSongList();
      if(state.viewingId && state.songs.some((song) => song.id === state.viewingId)) UI_openSongView(state.viewingId);
    }else if(state.songs.length){
      DB_save();
    }
  }catch{
    if(state.songs.length) DB_save();
  }
}

function UI_filteredSongs(){
  const query = normalizeText($("searchInput").value);
  const sort = $("sortSelect").value;
  let list = [...state.songs];

  if(query){
    list = list.filter((song) => {
      const haystack = [
        song.title,
        song.artist,
        song.key,
        (song.tags || []).join(","),
        song.notes,
        song.audioMeta?.name || "",
        song.audioSource?.label || "",
        song.audioSource?.url || "",
        ...(song.blocks || []).map((block) => [block.type, block.title, block.chords, block.lyrics, block.timeSec].join(" ")),
      ].join(" ");
      return normalizeText(haystack).includes(query);
    });
  }

  if(sort === "updated_desc") list.sort((a, b) => (b.updatedAt || "").localeCompare(a.updatedAt || ""));
  if(sort === "title_asc") list.sort((a, b) => normalizeText(a.title).localeCompare(normalizeText(b.title)));
  if(sort === "artist_asc") list.sort((a, b) => normalizeText(a.artist).localeCompare(normalizeText(b.artist)));
  return list;
}

function sourcePill(song){
  const source = song.audioSource || { type: "none" };
  if(source.type === "local") return `<span class="pill">MP3 local</span>`;
  if(source.type === "youtube") return `<span class="pill">YouTube</span>`;
  if(source.type === "spotify") return `<span class="pill">Spotify</span>`;
  return `<span class="pill">Sem áudio</span>`;
}

function UI_renderSongList(){
  const root = $("songList");
  root.innerHTML = "";
  const songs = UI_filteredSongs();

  if(!songs.length){
    root.innerHTML = '<div class="hint">Nenhuma música encontrada.</div>';
    return;
  }

  songs.forEach((song) => {
    const card = document.createElement("div");
    card.className = "song-card";
    card.innerHTML = `
      <div class="song-title">${esc(song.title || "Sem título")}</div>
      <div class="song-meta">
        <span class="pill">${esc(song.artist || "Sem artista")}</span>
        ${song.key ? `<span class="pill">Tom: ${esc(song.key)}</span>` : ""}
        <span class="pill">${song.blocks.length} blocos</span>
        ${sourcePill(song)}
      </div>
    `;
    card.addEventListener("click", () => UI_openSongView(song.id));
    root.appendChild(card);
  });
}

function UI_blocksFromEditor(){
  return Array.from(document.querySelectorAll("#blocksEditor .block-edit")).map((node) => {
    const rawTime = node.querySelector(".blockTime").value.trim();
    const timeValue = rawTime === "" ? null : Number(rawTime);
    return {
      type: node.querySelector(".blockType").value,
      title: node.querySelector(".blockTitle").value.trim(),
      timeSec: Number.isFinite(timeValue) ? timeValue : null,
      chords: node.querySelector(".blockChords").value,
      lyrics: node.querySelector(".blockLyrics").value,
    };
  });
}

function UI_bindBlockNode(node){
  node.querySelector(".delBtn").addEventListener("click", () => node.remove());
  node.querySelector(".upBtn").addEventListener("click", () => UI_moveBlock(node, -1));
  node.querySelector(".downBtn").addEventListener("click", () => UI_moveBlock(node, 1));
}

function UI_addBlock(block = null){
  const node = $("blockEditorTpl").content.firstElementChild.cloneNode(true);
  if(block){
    node.querySelector(".blockType").value = block.type || "Verso";
    node.querySelector(".blockTitle").value = block.title || "";
    node.querySelector(".blockTime").value = block.timeSec ?? "";
    node.querySelector(".blockChords").value = block.chords || "";
    node.querySelector(".blockLyrics").value = block.lyrics || "";
  }
  UI_bindBlockNode(node);
  $("blocksEditor").appendChild(node);
}

function UI_moveBlock(node, direction){
  const parent = node.parentElement;
  const siblings = Array.from(parent.children);
  const index = siblings.indexOf(node);
  const nextIndex = index + direction;
  if(nextIndex < 0 || nextIndex >= siblings.length) return;
  if(direction < 0) parent.insertBefore(node, siblings[nextIndex]);
  else parent.insertBefore(node, siblings[nextIndex].nextSibling);
}

function UI_newSong(){
  UI_resetEditor();
  UI_addBlock({ type: "Introdução", title: "", timeSec: 0, chords: "", lyrics: "" });
  UI_showTab("editor");
}

function UI_setEditorSourceType(type){
  $("audioSourceType").value = type;
  $("editorSourceLocal").classList.toggle("hidden", type !== "local");
  $("editorSourceYouTube").classList.toggle("hidden", type !== "youtube");
  $("editorSourceSpotify").classList.toggle("hidden", type !== "spotify");
}

function UI_resetEditor(){
  PLAYER_finishMark();
  state.editingId = null;
  state.editorAudioMeta = null;
  state.editorAudioBlob = null;
  $("titleInput").value = "";
  $("artistInput").value = "";
  $("keyInput").value = "";
  $("tagsInput").value = "";
  $("notesInput").value = "";
  $("youtubeSourceInput").value = "";
  $("youtubeSourceLabel").value = "";
  $("spotifySourceInput").value = "";
  $("spotifySourceLabel").value = "";
  $("audioFileEditor").value = "";
  $("audioEditorStatus").textContent = "Nenhum áudio vinculado.";
  $("blocksEditor").innerHTML = "";
  $("deleteSongBtn").disabled = true;
  UI_setEditorSourceType("none");
}

function parseYouTubeSource(input){
  const raw = (input || "").trim();
  if(!raw) return null;

  const directMatch = raw.match(/^[a-zA-Z0-9_-]{11}$/);
  if(directMatch){
    return {
      videoId: raw,
      url: `https://www.youtube.com/watch?v=${raw}`,
      label: "YouTube",
    };
  }

  try{
    const url = new URL(raw);
    let videoId = "";
    if(url.hostname.includes("youtu.be")) videoId = url.pathname.replaceAll("/", "");
    else if(url.pathname.startsWith("/shorts/")) videoId = url.pathname.split("/")[2] || "";
    else videoId = url.searchParams.get("v") || "";
    if(/^[a-zA-Z0-9_-]{11}$/.test(videoId)){
      return {
        videoId,
        url: `https://www.youtube.com/watch?v=${videoId}`,
        label: "YouTube",
      };
    }
  }catch{
    return null;
  }

  return null;
}

function parseSpotifySource(input){
  const raw = (input || "").trim();
  if(!raw) return null;

  const uriMatch = raw.match(/^spotify:(track|album|playlist|episode):([a-zA-Z0-9]+)$/i);
  if(uriMatch){
    const resourceType = uriMatch[1].toLowerCase();
    const resourceId = uriMatch[2];
    return {
      resourceType,
      resourceId,
      uri: `spotify:${resourceType}:${resourceId}`,
      url: `https://open.spotify.com/${resourceType}/${resourceId}`,
      embedUrl: `https://open.spotify.com/embed/${resourceType}/${resourceId}?utm_source=gpsmusical`,
      label: "Spotify",
    };
  }

  try{
    const url = new URL(raw);
    const segments = url.pathname.split("/").filter(Boolean);
    const allowed = new Set(["track", "album", "playlist", "episode"]);
    if(segments.length >= 2 && allowed.has(segments[0])){
      const resourceType = segments[0];
      const resourceId = segments[1];
      return {
        resourceType,
        resourceId,
        uri: `spotify:${resourceType}:${resourceId}`,
        url: `https://open.spotify.com/${resourceType}/${resourceId}`,
        embedUrl: `https://open.spotify.com/embed/${resourceType}/${resourceId}?utm_source=gpsmusical`,
        label: "Spotify",
      };
    }
  }catch{
    return null;
  }

  return null;
}

function UI_collectAudioSource(existingSong){
  const type = $("audioSourceType").value;
  if(type === "none") return { audioSource: { type: "none", label: "" }, audioMeta: existingSong?.audioMeta || null };

  if(type === "local"){
    const currentMeta = state.editorAudioMeta || existingSong?.audioMeta || null;
    if(!currentMeta && !state.editorAudioBlob) throw new Error("Selecione um MP3 local.");
    return {
      audioSource: { type: "local", label: currentMeta?.name || "MP3 local" },
      audioMeta: currentMeta,
    };
  }

  if(type === "youtube"){
    const parsed = parseYouTubeSource($("youtubeSourceInput").value);
    if(!parsed) throw new Error("Informe um link ou ID válido do YouTube.");
    return {
      audioSource: {
        type: "youtube",
        label: $("youtubeSourceLabel").value.trim() || parsed.label,
        url: parsed.url,
        videoId: parsed.videoId,
      },
      audioMeta: existingSong?.audioMeta || null,
    };
  }

  if(type === "spotify"){
    const parsed = parseSpotifySource($("spotifySourceInput").value);
    if(!parsed) throw new Error("Informe um link ou URI válido do Spotify.");
    return {
      audioSource: {
        type: "spotify",
        label: $("spotifySourceLabel").value.trim() || parsed.label,
        url: parsed.url,
        uri: parsed.uri,
        resourceType: parsed.resourceType,
        resourceId: parsed.resourceId,
        embedUrl: parsed.embedUrl,
      },
      audioMeta: existingSong?.audioMeta || null,
    };
  }

  return { audioSource: { type: "none", label: "" }, audioMeta: existingSong?.audioMeta || null };
}

async function UI_saveSong(){
  const title = $("titleInput").value.trim();
  if(!title) return alert("Informe o título da música.");

  const existingSong = state.editingId ? state.songs.find((song) => song.id === state.editingId) : null;
  let mediaData;
  try{
    mediaData = UI_collectAudioSource(existingSong);
  }catch(error){
    alert(error.message || String(error));
    return;
  }

  const payload = {
    title,
    artist: $("artistInput").value.trim(),
    key: $("keyInput").value.trim(),
    tags: $("tagsInput").value.split(",").map((tag) => tag.trim()).filter(Boolean),
    notes: $("notesInput").value.trim(),
    blocks: UI_blocksFromEditor(),
    audioMeta: mediaData.audioMeta,
    audioSource: mediaData.audioSource,
    updatedAt: nowISO(),
  };

  let songId = state.editingId;
  if(songId){
    const index = state.songs.findIndex((song) => song.id === songId);
    if(index >= 0){
      state.songs[index] = { ...state.songs[index], ...payload };
    }
  }else{
    songId = uid();
    state.songs.unshift({
      id: songId,
      createdAt: nowISO(),
      ...payload,
    });
  }

  if($("audioSourceType").value === "local" && state.editorAudioBlob && state.editorAudioMeta){
    try{
      await idbPutAudio(songId, { ...state.editorAudioMeta, blob: state.editorAudioBlob });
      const song = state.songs.find((item) => item.id === songId);
      if(song) song.audioMeta = { ...state.editorAudioMeta };
      state.editorAudioBlob = null;
      state.editorAudioMeta = null;
    }catch(error){
      alert(`Não foi possível salvar o MP3 local.\n\n${error.message || error}`);
    }
  }

  DB_save();
  UI_renderSongList();
  UI_resetEditor();
  UI_openSongView(songId);
}

async function UI_editCurrent(){
  const song = state.songs.find((item) => item.id === state.viewingId);
  if(!song) return;
  state.editingId = song.id;
  state.editorAudioMeta = null;
  state.editorAudioBlob = null;
  $("titleInput").value = song.title || "";
  $("artistInput").value = song.artist || "";
  $("keyInput").value = song.key || "";
  $("tagsInput").value = (song.tags || []).join(", ");
  $("notesInput").value = song.notes || "";
  $("blocksEditor").innerHTML = "";
  song.blocks.forEach((block) => UI_addBlock(block));
  $("deleteSongBtn").disabled = false;
  $("audioFileEditor").value = "";
  $("audioEditorStatus").textContent = song.audioMeta?.name ? `Vinculado: ${song.audioMeta.name}` : "Nenhum áudio vinculado.";

  if(song.audioSource?.type === "youtube"){
    UI_setEditorSourceType("youtube");
    $("youtubeSourceInput").value = song.audioSource.url || "";
    $("youtubeSourceLabel").value = song.audioSource.label || "";
  }else if(song.audioSource?.type === "spotify"){
    UI_setEditorSourceType("spotify");
    $("spotifySourceInput").value = song.audioSource.url || song.audioSource.uri || "";
    $("spotifySourceLabel").value = song.audioSource.label || "";
  }else if(song.audioSource?.type === "local"){
    UI_setEditorSourceType("local");
  }else{
    UI_setEditorSourceType("none");
  }

  UI_showTab("editor");
}

async function UI_deleteSong(){
  if(!state.editingId) return;
  const song = state.songs.find((item) => item.id === state.editingId);
  if(!song) return;
  if(!confirm(`Excluir "${song.title}"?`)) return;

  await idbDeleteAudio(song.id).catch(() => {});
  state.songs = state.songs.filter((item) => item.id !== song.id);
  DB_save();
  UI_resetEditor();
  UI_renderSongList();
  UI_showTab("biblioteca");
}

async function UI_openSongView(songId){
  PLAYER_stopAll();
  const song = state.songs.find((item) => item.id === songId);
  if(!song) return;

  state.viewingId = songId;
  state.currentBlockIndex = 0;
  $("viewTitle").textContent = song.title || "Sem título";

  const meta = [];
  if(song.artist) meta.push(song.artist);
  if(song.key) meta.push(`Tom: ${song.key}`);
  if(song.tags.length) meta.push(`Tags: ${song.tags.join(", ")}`);
  if(song.notes) meta.push(`Obs: ${song.notes}`);
  $("viewMeta").textContent = meta.join(" • ") || "—";

  const root = $("blocksView");
  root.innerHTML = "";
  song.blocks.forEach((block, index) => {
    const card = document.createElement("div");
    const timeValue = Number.isFinite(block.timeSec) ? `${block.timeSec.toFixed(1)}s` : "sem tempo";
    card.className = "block-card";
    card.innerHTML = `
      <div class="row space-between" style="align-items:center">
        <div>
          <div style="font-weight:950">${esc(block.type || "Bloco")}</div>
          <div class="hint small" style="margin-top:4px">${esc(block.title || `#${index + 1}`)}</div>
        </div>
        <span class="pill">${timeValue}</span>
      </div>
      <div class="split" style="margin-top:10px">
        ${block.chords.trim() ? `<pre class="pre">${esc(block.chords)}</pre>` : '<div class="hint small">Sem cifra.</div>'}
        ${block.lyrics.trim() ? `<pre class="pre">${esc(block.lyrics)}</pre>` : '<div class="hint small">Sem letra.</div>'}
      </div>
    `;
    root.appendChild(card);
  });

  await MEDIA_loadSongSource(song);
  UI_showTab("view");
}

function getAudio(){
  return $("audioMain");
}

function idbOpen(){
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(IDB_NAME, IDB_VER);
    request.onupgradeneeded = () => {
      const db = request.result;
      if(!db.objectStoreNames.contains(IDB_STORE)) db.createObjectStore(IDB_STORE);
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || new Error("Falha ao abrir IndexedDB"));
  });
}

async function idbPutAudio(songId, payload){
  const db = await idbOpen();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(IDB_STORE, "readwrite");
    tx.objectStore(IDB_STORE).put(payload, songId);
    tx.oncomplete = () => { db.close(); resolve(true); };
    tx.onerror = () => { db.close(); reject(tx.error || new Error("Falha ao salvar áudio")); };
  });
}

async function idbGetAudio(songId){
  const db = await idbOpen();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(IDB_STORE, "readonly");
    const request = tx.objectStore(IDB_STORE).get(songId);
    request.onsuccess = () => { db.close(); resolve(request.result || null); };
    request.onerror = () => { db.close(); reject(request.error || new Error("Falha ao ler áudio")); };
  });
}

async function idbDeleteAudio(songId){
  const db = await idbOpen();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(IDB_STORE, "readwrite");
    tx.objectStore(IDB_STORE).delete(songId);
    tx.oncomplete = () => { db.close(); resolve(true); };
    tx.onerror = () => { db.close(); reject(tx.error || new Error("Falha ao apagar áudio")); };
  });
}

function AUDIO_dockTo(where){
  const audio = getAudio();
  const dockView = $("audioDockView");
  const dockStage = $("audioDockStage");
  if(!audio || !dockView || !dockStage) return;
  if(where === "stage") dockStage.appendChild(audio);
  else dockView.appendChild(audio);
}

function MEDIA_clearPanels(){
  if(window.__gpsAudioObjectUrl){
    URL.revokeObjectURL(window.__gpsAudioObjectUrl);
    window.__gpsAudioObjectUrl = null;
  }

  if(state.youtubePlayerReady && state.youtubePlayer){
    try{ state.youtubePlayer.pauseVideo(); }catch{
    }
  }

  const audio = getAudio();
  audio.pause();
  audio.removeAttribute("src");
  audio.load();
  AUDIO_dockTo("view");

  $("youtubePlayerPanel").classList.add("hidden");
  $("spotifyPlayerPanel").classList.add("hidden");
  $("localAudioActions").classList.add("hidden");
  $("spotifyEmbedFrame").src = "";
  state.activeSourceType = "none";
  state.activeYouTubeId = "";
  state.activeSpotifyEmbedUrl = "";
}

async function MEDIA_prepareYouTube(videoId){
  await youtubeApiReady;
  return new Promise((resolve, reject) => {
    const onReady = () => {
      state.youtubePlayerReady = true;
      state.activeYouTubeId = videoId;
      try{
        state.youtubePlayer.cueVideoById(videoId);
      }catch{
      }
      resolve(true);
    };

    if(state.youtubePlayer){
      try{
        state.youtubePlayer.cueVideoById(videoId);
        state.activeYouTubeId = videoId;
        state.youtubePlayerReady = true;
        resolve(true);
      }catch(error){
        reject(error);
      }
      return;
    }

    state.youtubePlayer = new YT.Player("youtubePlayerHost", {
      videoId,
      playerVars: { playsinline: 1, rel: 0 },
      events: {
        onReady,
        onError: () => reject(new Error("Não foi possível carregar o vídeo do YouTube.")),
      },
    });
  });
}

async function MEDIA_loadSongSource(song){
  MEDIA_clearPanels();
  const source = song.audioSource || { type: "none" };
  $("viewSourceBadge").textContent = source.label || source.type || "Sem áudio";

  if(source.type === "local"){
    $("localAudioActions").classList.remove("hidden");
    state.activeSourceType = "local";
    if(song.audioMeta){
      const payload = await idbGetAudio(song.id);
      if(payload?.blob){
        window.__gpsAudioObjectUrl = URL.createObjectURL(payload.blob);
        getAudio().src = window.__gpsAudioObjectUrl;
        $("audioViewStatus").textContent = `MP3 local: ${song.audioMeta.name || "arquivo local"}`;
      }else{
        $("audioViewStatus").textContent = "O MP3 local não foi encontrado neste navegador.";
      }
    }else{
      $("audioViewStatus").textContent = "Nenhum MP3 local vinculado.";
    }
  }else if(source.type === "youtube"){
    state.activeSourceType = "youtube";
    $("youtubePlayerPanel").classList.remove("hidden");
    try{
      await MEDIA_prepareYouTube(source.videoId);
      $("audioViewStatus").textContent = `YouTube: ${source.label || source.url}`;
    }catch(error){
      $("audioViewStatus").textContent = error.message || "Falha ao carregar YouTube.";
    }
  }else if(source.type === "spotify"){
    state.activeSourceType = "spotify";
    state.activeSpotifyEmbedUrl = source.embedUrl || "";
    $("spotifyPlayerPanel").classList.remove("hidden");
    $("spotifyEmbedFrame").src = source.embedUrl || "";
    $("audioViewStatus").textContent = `Spotify: ${source.label || source.url || "fonte configurada"}`;
  }else{
    $("audioViewStatus").textContent = "Sem fonte de áudio.";
  }

  UI_updatePlaybackButtons();
}

function SOURCE_supportsTimeline(){
  return state.activeSourceType === "local" || 
         state.activeSourceType === "youtube" || 
         (state.activeSourceType === "spotify" && state.spotifyPlayer);
}

function SOURCE_hasPlayableSource(){
  if(state.activeSourceType === "local") return Boolean(getAudio().src);
  if(state.activeSourceType === "youtube") return Boolean(state.activeYouTubeId);
  if(state.activeSourceType === "spotify") return Boolean(state.activeSpotifyEmbedUrl);
  return false;
}

function SOURCE_play(){
  if(state.activeSourceType === "local") return getAudio().play();
  if(state.activeSourceType === "youtube" && state.youtubePlayerReady) return state.youtubePlayer.playVideo();
  if(state.activeSourceType === "spotify" && state.spotifyPlayer && state.spotifyDeviceId) {
    return SPOTIFY_play();
  }
  return null;
}

async function SPOTIFY_play(){
  if(!state.spotifyCurrentTrack) return;
  try {
    await fetch(`https://api.spotify.com/v1/me/player/play?device_id=${state.spotifyDeviceId}`, {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${state.auth.spotify.accessToken}`,
      },
      body: JSON.stringify({
        uris: [state.spotifyCurrentTrack.uri],
      }),
    });
  } catch(error) {
    console.error("Spotify play error:", error);
  }
}


function SOURCE_pause(){
  if(state.activeSourceType === "local") getAudio().pause();
  if(state.activeSourceType === "youtube" && state.youtubePlayerReady) state.youtubePlayer.pauseVideo();
  if(state.activeSourceType === "spotify" && state.spotifyPlayer) {
    SPOTIFY_pause();
  }
}

async function SPOTIFY_pause(){
  try {
    await fetch(`https://api.spotify.com/v1/me/player/pause?device_id=${state.spotifyDeviceId}`, {
      method: "PUT",
      headers: {
        Authorization: `Bearer ${state.auth.spotify.accessToken}`,
      },
    });
  } catch(error) {
    console.error("Spotify pause error:", error);
  }
}


function SOURCE_seek(seconds){
  const target = Math.max(0, Number(seconds) || 0);
  if(state.activeSourceType === "local"){
    getAudio().currentTime = target;
  }else if(state.activeSourceType === "youtube" && state.youtubePlayerReady){
    state.youtubePlayer.seekTo(target, true);
  }else if(state.activeSourceType === "spotify") {
    SPOTIFY_seek(target * 1000);
  }
}

async function SPOTIFY_seek(positionMs){
  try {
    await fetch(`https://api.spotify.com/v1/me/player/seek?position_ms=${Math.floor(positionMs)}&device_id=${state.spotifyDeviceId}`, {
      method: "PUT",
      headers: {
        Authorization: `Bearer ${state.auth.spotify.accessToken}`,
      },
    });
  } catch(error) {
    console.error("Spotify seek error:", error);
  }
}


function SOURCE_getCurrentTime(){
  if(state.activeSourceType === "local") return Number(getAudio().currentTime || 0);
  if(state.activeSourceType === "youtube" && state.youtubePlayerReady){
    try{ return Number(state.youtubePlayer.getCurrentTime() || 0); }catch{ return 0; }
  }
  if(state.activeSourceType === "spotify" && state.spotifyPlaybackState) {
    return state.spotifyPlaybackState.position / 1000;
  }
  return 0;
}


function SOURCE_getDuration(){
  if(state.activeSourceType === "local") return Number(getAudio().duration || 0);
  if(state.activeSourceType === "youtube" && state.youtubePlayerReady){
    try{ return Number(state.youtubePlayer.getDuration() || 0); }catch{ return 0; }
  }
  if(state.activeSourceType === "spotify" && state.spotifyCurrentTrack) {
    return state.spotifyCurrentTrack.duration_ms / 1000;
  }
  return 0;
}


function UI_updatePlaybackButtons(){
  const canTimeline = SOURCE_supportsTimeline();
  $("markBtn").disabled = !canTimeline;
  $("playTimelineBtn").disabled = !canTimeline;
  
  let hintText = "Atalhos no palco: <kbd>→</kbd>, <kbd>←</kbd> e <kbd>Esc</kbd>. ";
  if(state.activeSourceType === "spotify" && state.spotifyPlayer) {
    hintText += "Timeline Spotify usa duração da API. Marque tempos nos blocos.";
  } else if(state.activeSourceType === "youtube") {
    hintText += "Timeline usa tempo do vídeo. Marque tempos automaticamente no palco.";
  } else if(state.activeSourceType === "local") {
    hintText += "Timeline usa tempo do MP3. Marque tempos automaticamente no palco.";
  }
  
  $("playerHint").innerHTML = canTimeline ? hintText : "Modo slide continua disponível, mas marcação automática de tempo requer MP3 local, YouTube ou Spotify autenticado.";
}

function PLAYER_song(){
  return state.songs.find((song) => song.id === state.viewingId) || null;
}

function PLAYER_openStage(){
  const song = PLAYER_song();
  if(!song) return;
  $("stage").classList.add("active");
  if(state.activeSourceType === "local") AUDIO_dockTo("stage");
  $("stageSongTitle").textContent = song.title || "GPS Musical";
  $("stageSongSub").textContent = [song.artist, song.key ? `Tom: ${song.key}` : ""].filter(Boolean).join(" • ") || "—";
  PLAYER_renderStage(state.currentBlockIndex);
}

function PLAYER_closeStage(){
  $("stage").classList.remove("active");
  AUDIO_dockTo("view");
}

function PLAYER_renderStage(index){
  const song = PLAYER_song();
  if(!song || !song.blocks.length) return;
  const position = clamp(index, 0, song.blocks.length - 1);
  state.currentBlockIndex = position;
  const block = song.blocks[position];
  $("stageKind").textContent = block.type || "Bloco";
  $("stageBlockTitle").textContent = block.title || `#${position + 1}`;
  $("stageChords").textContent = block.chords.trim() || "—";
  $("stageLyrics").textContent = block.lyrics.trim() || "—";
  $("stageFooter").textContent = `Bloco ${position + 1}/${song.blocks.length} • ${Number.isFinite(block.timeSec) ? `${block.timeSec.toFixed(1)}s` : "sem tempo"}`;
  $("stageTime").textContent = `${fmtTime(SOURCE_getCurrentTime())} / ${fmtTime(SOURCE_getDuration())}`;
}

function PLAYER_prev(){ PLAYER_renderStage(state.currentBlockIndex - 1); }
function PLAYER_next(){ PLAYER_renderStage(state.currentBlockIndex + 1); }

function PLAYER_stopTimeline(pauseSource){
  if(state.timelineRAF){
    cancelAnimationFrame(state.timelineRAF);
    state.timelineRAF = null;
  }
  state.playingMode = null;
  $("stopTimelineBtn").disabled = true;
  if(pauseSource) SOURCE_pause();
}

function PLAYER_stopSlide(pauseSource){
  if(state.slideTimer){
    clearInterval(state.slideTimer);
    state.slideTimer = null;
  }
  state.playingMode = null;
  $("stopSlideBtn").disabled = true;
  if(pauseSource) SOURCE_pause();
}

function PLAYER_stopAll(){
  PLAYER_stopTimeline(false);
  PLAYER_stopSlide(false);
  $("stageProgress").style.width = "0%";
}

function PLAYER_playTimeline(){
  const song = PLAYER_song();
  if(!song) return;
  if(!SOURCE_supportsTimeline()) return alert("A timeline automática está disponível para MP3 local e YouTube.");

  const timedBlocks = song.blocks
    .map((block, index) => ({ index, time: Number(block.timeSec) }))
    .filter((item) => Number.isFinite(item.time))
    .sort((a, b) => a.time - b.time);

  if(!timedBlocks.length) return alert("Nenhum bloco tem tempo preenchido.");
  if(!SOURCE_hasPlayableSource()) return alert("Configure uma fonte de áudio válida.");

  PLAYER_stopAll();
  state.playingMode = "timeline";
  $("stopTimelineBtn").disabled = false;
  state.currentBlockIndex = timedBlocks[0].index;
  PLAYER_openStage();
  SOURCE_seek(0);
  SOURCE_play();

  const maxTime = timedBlocks[timedBlocks.length - 1].time || 1;
  const tick = () => {
    if(state.playingMode !== "timeline") return;
    const now = SOURCE_getCurrentTime();
    let active = timedBlocks[0];
    for(const item of timedBlocks){
      if(item.time <= now) active = item;
      else break;
    }
    if(active.index !== state.currentBlockIndex) PLAYER_renderStage(active.index);
    $("stageProgress").style.width = `${(Math.max(0, Math.min(1, now / Math.max(1, maxTime))) * 100).toFixed(2)}%`;
    $("stageTime").textContent = `${fmtTime(now)} / ${fmtTime(SOURCE_getDuration())}`;
    state.timelineRAF = requestAnimationFrame(tick);
  };
  state.timelineRAF = requestAnimationFrame(tick);
}

function PLAYER_playSlide(){
  const song = PLAYER_song();
  if(!song) return;
  if(!song.blocks.length) return alert("Essa música não tem blocos.");
  PLAYER_stopAll();
  state.playingMode = "slide";
  $("stopSlideBtn").disabled = false;
  state.currentBlockIndex = 0;
  PLAYER_openStage();

  const seconds = Math.max(1, Number($("slideSecondsInput").value) || 10);
  let startedAt = performance.now();

  const progress = () => {
    if(state.playingMode !== "slide") return;
    const elapsed = (performance.now() - startedAt) / 1000;
    $("stageProgress").style.width = `${(Math.max(0, Math.min(1, elapsed / seconds)) * 100).toFixed(2)}%`;
    $("stageTime").textContent = `${fmtTime(SOURCE_getCurrentTime())} / ${fmtTime(SOURCE_getDuration())}`;
    requestAnimationFrame(progress);
  };
  requestAnimationFrame(progress);

  state.slideTimer = setInterval(() => {
    startedAt = performance.now();
    state.currentBlockIndex = (state.currentBlockIndex + 1) % song.blocks.length;
    PLAYER_renderStage(state.currentBlockIndex);
  }, seconds * 1000);
}

function PLAYER_toggleMark(){
  const song = PLAYER_song();
  if(!song) return;
  if(!song.blocks.length) return alert("Adicione blocos antes de marcar.");
  if(!SOURCE_supportsTimeline() || !SOURCE_hasPlayableSource()){
    return alert("A marcação automática precisa de MP3 local ou vídeo do YouTube.");
  }

  if(!state.markActive){
    state.markActive = true;
    state.markNextIndex = 0;
    $("markBtn").textContent = "Marcar próximo bloco";
    $("markBtn").classList.add("ok");
    $("finishMarkBtn").classList.remove("hidden");
    SOURCE_seek(0);
    SOURCE_play();
    PLAYER_openStage();
    PLAYER_renderStage(0);
    return;
  }

  const blockIndex = state.markNextIndex;
  if(blockIndex >= song.blocks.length){
    PLAYER_finishMark();
    alert("Marcação concluída.");
    return;
  }

  song.blocks[blockIndex].timeSec = Number(SOURCE_getCurrentTime().toFixed(1));
  state.markNextIndex = blockIndex + 1;
  song.updatedAt = nowISO();
  DB_save();
  UI_openSongView(song.id);
  PLAYER_openStage();
  PLAYER_renderStage(Math.min(state.markNextIndex, song.blocks.length - 1));
}

function PLAYER_finishMark(){
  state.markActive = false;
  state.markNextIndex = 0;
  $("markBtn").textContent = "Marcar tempos";
  $("markBtn").classList.remove("ok");
  $("finishMarkBtn").classList.add("hidden");
}

function BK_set(message){
  $("backupStatus").textContent = message || "";
}

function BK_exportJSON(){
  const payload = {
    app: "gps_musical",
    version: 6,
    exportedAt: nowISO(),
    songs: state.songs,
  };
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `gps_musical_backup_${new Date().toISOString().slice(0, 10)}.json`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
  BK_set("Backup JSON exportado.");
}

async function BK_importJSON(file){
  const text = await file.text();
  const payload = safeParseJSON(text);
  const songs = Array.isArray(payload) ? payload : (Array.isArray(payload?.songs) ? payload.songs : null);
  if(!songs) return alert("Arquivo JSON inválido.");
  state.songs = normalizeSongs(songs).map((song) => {
    if(song.audioSource.type === "local") song.audioMeta = null;
    return song;
  });
  DB_save();
  UI_renderSongList();
  BK_set(`Importado: ${state.songs.length} músicas. MP3 local deve ser reanexado neste navegador.`);
  UI_showTab("biblioteca");
}

async function BK_clearLocalCache(){
  if(!confirm("Limpar cache local e recarregar do banco remoto quando disponível?")) return;
  const ids = state.songs.map((song) => song.id);
  localStorage.removeItem(STORAGE_KEY);
  for(const id of ids){
    await idbDeleteAudio(id).catch(() => {});
  }
  state.songs = [];
  UI_renderSongList();
  if(REMOTE_STORE) await REMOTE_STORE.syncNow();
  state.songs = DB_load();
  UI_renderSongList();
  BK_set("Cache local limpo.");
}

async function BK_loadRemoteBackups(){
  try{
    const payload = await apiRequest(API_BACKUPS_URL);
    state.remoteBackups = Array.isArray(payload?.items) ? payload.items : [];
    BK_renderRemoteBackups();
  }catch(error){
    $("remoteBackupStatus").textContent = `Falha ao listar backups: ${error.message || error}`;
  }
}

function BK_renderRemoteBackups(){
  const root = $("remoteBackupList");
  root.innerHTML = "";
  if(!state.remoteBackups.length){
    root.innerHTML = '<div class="hint small">Nenhum backup remoto encontrado.</div>';
    return;
  }

  state.remoteBackups.forEach((backup) => {
    const item = document.createElement("div");
    item.className = "backup-item";
    item.innerHTML = `
      <div>
        <div style="font-weight:900">${esc(backup.fileName || backup.id)}</div>
        <div class="hint small">${esc(backup.createdAt || "")} • ${backup.songs || 0} músicas • ${backup.sizeBytes || 0} bytes</div>
      </div>
      <div class="row">
        <button class="btn restore-btn" type="button">Restaurar</button>
        <button class="btn ghost download-btn" type="button">Baixar</button>
      </div>
    `;
    item.querySelector(".restore-btn").addEventListener("click", () => BK_restoreRemoteBackup(backup.id));
    item.querySelector(".download-btn").addEventListener("click", () => {
      location.href = `${API_BACKUPS_URL}/${encodeURIComponent(backup.id)}/download`;
    });
    root.appendChild(item);
  });
}

async function BK_createRemoteBackup(){
  try{
    const payload = await apiRequest(API_BACKUPS_URL, { method: "POST" });
    $("remoteBackupStatus").textContent = `Backup criado: ${payload?.backup?.fileName || "ok"}`;
    await BK_loadRemoteBackups();
  }catch(error){
    $("remoteBackupStatus").textContent = `Falha ao criar backup: ${error.message || error}`;
  }
}

async function BK_restoreRemoteBackup(backupId){
  if(!confirm("Restaurar esse backup no banco remoto atual?")) return;
  try{
    await apiRequest(`${API_BACKUPS_URL}/${encodeURIComponent(backupId)}/restore`, { method: "POST" });
    $("remoteBackupStatus").textContent = "Backup restaurado com sucesso.";
    if(REMOTE_STORE) await REMOTE_STORE.syncNow();
    state.songs = DB_load();
    UI_renderSongList();
    if(state.viewingId && state.songs.some((song) => song.id === state.viewingId)) UI_openSongView(state.viewingId);
  }catch(error){
    $("remoteBackupStatus").textContent = `Falha ao restaurar backup: ${error.message || error}`;
  }
}

function UI_renderAuthStatus(){
  const youtubeConnected = authTokenValid("youtube");
  const spotifyConnected = authTokenValid("spotify");

  $("youtubeAuthBadge").textContent = youtubeConnected ? "Conectado" : "Desconectado";
  $("youtubeAuthBadge").className = `pill ${youtubeConnected ? "ok" : "warn"}`;
  $("youtubeAuthStatus").textContent = youtubeConnected
    ? `Conta ativa: ${state.auth.youtube.profileName || "YouTube conectado"}`
    : "Nenhuma conta conectada.";

  $("spotifyAuthBadge").textContent = spotifyConnected ? "Conectado" : "Desconectado";
  $("spotifyAuthBadge").className = `pill ${spotifyConnected ? "ok" : "warn"}`;
  $("spotifyAuthStatus").textContent = spotifyConnected
    ? `Conta ativa: ${state.auth.spotify.profileName || "Spotify conectado"}`
    : "Nenhuma conta conectada.";
  
  // Update editor Spotify auth status
  if($("spotifyEditorAuthBadge")) {
    $("spotifyEditorAuthBadge").textContent = spotifyConnected ? "Conectado" : "Desconectado";
    $("spotifyEditorAuthBadge").className = `pill ${spotifyConnected ? "ok" : "warn"}`;
  }
}

async function YOUTUBE_connect(){
  const clientId = $("cfgYoutubeClientId").value.trim();
  if(!clientId) return alert("Preencha o Client ID do YouTube/Google na aba Config.");
  if(!window.google?.accounts?.oauth2) return alert("Biblioteca do Google ainda não carregou.");

  const tokenClient = window.google.accounts.oauth2.initTokenClient({
    client_id: clientId,
    scope: "https://www.googleapis.com/auth/youtube.readonly",
    callback: async (tokenResponse) => {
      state.auth.youtube.accessToken = tokenResponse.access_token || "";
      state.auth.youtube.expiresAt = Date.now() + Math.max(0, Number(tokenResponse.expires_in || 0)) * 1000;
      try{
        const response = await fetch("https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true", {
          headers: { Authorization: `Bearer ${state.auth.youtube.accessToken}` },
        });
        const payload = await response.json();
        state.auth.youtube.profileName = payload?.items?.[0]?.snippet?.title || "Conta YouTube";
      }catch{
        state.auth.youtube.profileName = "Conta YouTube";
      }
      saveAuthState();
    },
  });

  tokenClient.requestAccessToken({ prompt: "consent" });
}

function YOUTUBE_disconnect(){
  state.auth.youtube = { accessToken: "", expiresAt: 0, profileName: "" };
  saveAuthState();
}

async function spotifyBase64Url(buffer){
  return btoa(String.fromCharCode(...new Uint8Array(buffer)))
    .replaceAll("+", "-")
    .replaceAll("/", "_")
    .replaceAll("=", "");
}

async function SPOTIFY_connect(){
  const clientId = $("cfgSpotifyClientId").value.trim();
  const redirectUri = $("cfgSpotifyRedirectUri").value.trim() || `${location.origin}${location.pathname}`;
  if(!clientId) return alert("Preencha o Client ID do Spotify na aba Config.");

  const verifier = [...crypto.getRandomValues(new Uint8Array(64))]
    .map((item) => item.toString(16).padStart(2, "0"))
    .join("");
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  const challenge = await spotifyBase64Url(digest);
  const oauthState = uid();

  sessionStorage.setItem("gps_spotify_verifier", verifier);
  sessionStorage.setItem("gps_spotify_state", oauthState);

  const params = new URLSearchParams({
    client_id: clientId,
    response_type: "code",
    redirect_uri: redirectUri,
    code_challenge_method: "S256",
    code_challenge: challenge,
    state: oauthState,
    scope: "user-read-email user-read-private playlist-read-private user-library-read",
  });
  location.href = `https://accounts.spotify.com/authorize?${params.toString()}`;
}

async function SPOTIFY_exchangeCode(code){
  const clientId = $("cfgSpotifyClientId").value.trim() || state.config.spotify.clientId;
  const redirectUri = $("cfgSpotifyRedirectUri").value.trim() || state.config.spotify.redirectUri || `${location.origin}${location.pathname}`;
  const verifier = sessionStorage.getItem("gps_spotify_verifier") || "";
  if(!clientId || !verifier) throw new Error("Fluxo Spotify incompleto.");

  const body = new URLSearchParams({
    client_id: clientId,
    grant_type: "authorization_code",
    code,
    redirect_uri: redirectUri,
    code_verifier: verifier,
  });

  const response = await fetch("https://accounts.spotify.com/api/token", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body,
  });
  const payload = await response.json();
  if(!response.ok) throw new Error(payload?.error_description || payload?.error || "Falha ao autenticar no Spotify.");

  state.auth.spotify.accessToken = payload.access_token || "";
  state.auth.spotify.refreshToken = payload.refresh_token || "";
  state.auth.spotify.expiresAt = Date.now() + Math.max(0, Number(payload.expires_in || 0)) * 1000;
  const me = await fetch("https://api.spotify.com/v1/me", {
    headers: { Authorization: `Bearer ${state.auth.spotify.accessToken}` },
  });
  const mePayload = await me.json();
  state.auth.spotify.profileName = mePayload?.display_name || mePayload?.id || "Conta Spotify";
  saveAuthState();
  
  // Re-initialize Web Playback Player if SDK is ready
  if(window.Spotify?.Player && window.onSpotifyWebPlaybackSDKReady) {
    window.onSpotifyWebPlaybackSDKReady();
  }
}

async function SPOTIFY_refreshIfNeeded(){
  if(authTokenValid("spotify")) return state.auth.spotify.accessToken;
  if(!state.auth.spotify.refreshToken) return "";
  const clientId = $("cfgSpotifyClientId").value.trim() || state.config.spotify.clientId;
  if(!clientId) return "";

  const body = new URLSearchParams({
    client_id: clientId,
    grant_type: "refresh_token",
    refresh_token: state.auth.spotify.refreshToken,
  });

  const response = await fetch("https://accounts.spotify.com/api/token", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body,
  });
  const payload = await response.json();
  if(!response.ok) return "";
  state.auth.spotify.accessToken = payload.access_token || "";
  state.auth.spotify.expiresAt = Date.now() + Math.max(0, Number(payload.expires_in || 0)) * 1000;
  if(payload.refresh_token) state.auth.spotify.refreshToken = payload.refresh_token;
  saveAuthState();
  return state.auth.spotify.accessToken;
}

function SPOTIFY_disconnect(){
  state.auth.spotify = { accessToken: "", refreshToken: "", expiresAt: 0, profileName: "" };
  saveAuthState();
}

async function SPOTIFY_search(query, type = "track"){
  if(!query.trim()) {
    state.spotifySearchResults = [];
    return;
  }

  const accessToken = await SPOTIFY_refreshIfNeeded();
  if(!accessToken) {
    console.warn("Spotify access token não disponível para busca");
    return;
  }

  try {
    const params = new URLSearchParams({
      q: query.trim(),
      type: type,
      limit: SPOTIFY_SEARCH_LIMIT,
    });

    const response = await fetch(`${SPOTIFY_API_BASE}/search?${params}`, {
      headers: { Authorization: `Bearer ${accessToken}` },
    });

    if(!response.ok) {
      console.warn("Spotify search failed:", response.status);
      state.spotifySearchResults = [];
      return;
    }

    const payload = await response.json();
    
    if(type === "track" && payload.tracks) {
      state.spotifySearchResults = (payload.tracks.items || []).map(item => ({
        id: item.id,
        type: "track",
        name: item.name,
        artist: (item.artists || []).map(a => a.name).join(", "),
        duration_ms: item.duration_ms,
        uri: item.uri,
        url: item.external_urls.spotify,
        embedUrl: `https://open.spotify.com/embed/track/${item.id}?utm_source=gpsmusical`,
        image: item.album?.images?.[0]?.url || "",
      }));
    }

    UI_renderSpotifySearchResults();
  } catch(error) {
    console.error("Spotify search error:", error);
    state.spotifySearchResults = [];
  }
}

function SPOTIFY_searchDebounced(query){
  if(state.spotifySearchDebounce) clearTimeout(state.spotifySearchDebounce);
  state.spotifySearchDebounce = setTimeout(() => {
    SPOTIFY_search(query, "track");
  }, SPOTIFY_SEARCH_DEBOUNCE_MS);
}

function UI_renderSpotifySearchResults(){
  const container = $("spotifySearchResults");
  if(!container) return;
  
  container.innerHTML = "";
  if(!state.spotifySearchResults.length) {
    container.innerHTML = '<div class="hint small">Nenhum resultado.</div>';
    return;
  }

  state.spotifySearchResults.forEach(item => {
    const div = document.createElement("div");
    div.className = "spotify-search-item";
    div.innerHTML = `
      <div class="spotify-result-image" style="background-image: url('${esc(item.image)}')"></div>
      <div class="spotify-result-info">
        <div class="spotify-result-name">${esc(item.name)}</div>
        <div class="hint small">${esc(item.artist || "")}</div>
      </div>
    `;
    div.addEventListener("click", () => SPOTIFY_selectSearchResult(item));
    container.appendChild(div);
  });
}

function SPOTIFY_selectSearchResult(item){
  $("spotifySourceInput").value = item.url;
  $("spotifySourceLabel").value = item.name;
  state.spotifySearchResults = [];
  UI_renderSpotifySearchResults();
}

async function SPOTIFY_getTrackInfo(trackId){
  const accessToken = await SPOTIFY_refreshIfNeeded();
  if(!accessToken) return null;

  try {
    const response = await fetch(`${SPOTIFY_API_BASE}/tracks/${trackId}`, {
      headers: { Authorization: `Bearer ${accessToken}` },
    });

    if(!response.ok) return null;

    const item = await response.json();
    return {
      id: item.id,
      name: item.name,
      artist: (item.artists || []).map(a => a.name).join(", "),
      duration_ms: item.duration_ms,
      uri: item.uri,
      url: item.external_urls.spotify,
    };
  } catch(error) {
    console.error("Error fetching Spotify track info:", error);
    return null;
  }
}


async function AUTH_handleSpotifyCallback(){
  const params = new URLSearchParams(location.search);
  const code = params.get("code");
  const incomingState = params.get("state");
  const expectedState = sessionStorage.getItem("gps_spotify_state");
  if(!code) return;

  try{
    if(!incomingState || incomingState !== expectedState) throw new Error("Estado OAuth do Spotify inválido.");
    await SPOTIFY_exchangeCode(code);
  }catch(error){
    alert(error.message || String(error));
  }finally{
    sessionStorage.removeItem("gps_spotify_verifier");
    sessionStorage.removeItem("gps_spotify_state");
    const cleanUrl = `${location.origin}${location.pathname}${location.hash || ""}`;
    history.replaceState({}, document.title, cleanUrl);
  }
}

async function AUTH_refreshProfiles(){
  UI_renderAuthStatus();
  try{
    if(authTokenValid("spotify") || state.auth.spotify.refreshToken){
      const token = await SPOTIFY_refreshIfNeeded();
      if(token){
        const response = await fetch("https://api.spotify.com/v1/me", {
          headers: { Authorization: `Bearer ${token}` },
        });
        if(response.ok){
          const payload = await response.json();
          state.auth.spotify.profileName = payload?.display_name || payload?.id || state.auth.spotify.profileName;
          saveAuthState();
        }
      }
    }
  }catch{
  }
}

function seedIfEmpty(){
  if(state.songs.length) return;
  state.songs = [{
    id: uid(),
    title: "Exemplo - Minha Música",
    artist: "Artista",
    key: "G",
    tags: ["exemplo", "gps"],
    notes: "Você pode usar MP3 local, YouTube ou Spotify como fonte de áudio.",
    audioMeta: null,
    audioSource: { type: "none", label: "" },
    blocks: [
      { type: "Introdução", title: "", timeSec: 0, chords: "G  D  Em  C", lyrics: "" },
      { type: "Verso", title: "1", timeSec: 10, chords: "G              D\nMinha letra aqui...\nEm             C\nContinua aqui...", lyrics: "Minha letra aqui...\nContinua aqui..." },
      { type: "Refrão", title: "2x", timeSec: 25, chords: "C   D   G", lyrics: "Refrão da música..." },
    ],
    createdAt: nowISO(),
    updatedAt: nowISO(),
  }];
  DB_saveLocal();
}

async function SYNC_now(){
  if(!REMOTE_STORE) return;
  try{
    await REMOTE_STORE.syncNow();
    state.songs = DB_load();
    UI_renderSongList();
    UI_setSyncStatus("Sincronização concluída.", "ok");
  }catch(error){
    UI_setSyncStatus(`Falha ao sincronizar: ${error.message || error}`, "danger");
  }
}

function bindEvents(){
  document.querySelectorAll("[data-tab]").forEach((button) => {
    button.addEventListener("click", () => UI_showTab(button.dataset.tab));
  });

  $("searchInput").addEventListener("input", UI_renderSongList);
  $("sortSelect").addEventListener("change", UI_renderSongList);
  $("newSongBtn").addEventListener("click", UI_newSong);
  $("refreshRemoteBtn").addEventListener("click", SYNC_now);

  $("audioSourceType").addEventListener("change", (event) => UI_setEditorSourceType(event.target.value));
  
  // Spotify search in editor
  if($("spotifySearchInput")) {
    $("spotifySearchInput").addEventListener("input", (e) => {
      const container = $("spotifySearchResults");
      if(e.target.value.trim()) {
        container?.classList.remove("hidden");
      } else {
        container?.classList.add("hidden");
      }
      SPOTIFY_searchDebounced(e.target.value);
    });
    
    $("spotifySearchInput").addEventListener("blur", () => {
      setTimeout(() => {
        const container = $("spotifySearchResults");
        container?.classList.add("hidden");
      }, 200);
    });
    
    $("spotifySearchInput").addEventListener("focus", () => {
      if(state.spotifySearchResults.length) {
        $("spotifySearchResults")?.classList.remove("hidden");
      }
    });
  }
  
  $("audioFileEditor").addEventListener("change", (event) => {
    const file = event.target.files?.[0];
    if(!file) return;
    state.editorAudioMeta = { name: file.name, mime: file.type || "audio/mpeg" };
    state.editorAudioBlob = file;
    $("audioEditorStatus").textContent = `Selecionado: ${file.name}`;
  });
  $("removeEditorAudioBtn").addEventListener("click", () => {
    state.editorAudioMeta = null;
    state.editorAudioBlob = null;
    $("audioFileEditor").value = "";
    $("audioEditorStatus").textContent = "Nenhum áudio vinculado.";
  });

  $("addBlockBtn").addEventListener("click", () => UI_addBlock());
  $("clearEditorBtn").addEventListener("click", UI_resetEditor);
  $("saveSongBtn").addEventListener("click", UI_saveSong);
  $("deleteSongBtn").addEventListener("click", UI_deleteSong);

  $("backToLibraryBtn").addEventListener("click", () => UI_showTab("biblioteca"));
  $("editCurrentBtn").addEventListener("click", UI_editCurrent);
  $("audioFileView").addEventListener("change", async (event) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if(!file) return;
    const song = PLAYER_song();
    if(!song) return;
    await idbPutAudio(song.id, { name: file.name, mime: file.type || "audio/mpeg", blob: file });
    song.audioMeta = { name: file.name, mime: file.type || "audio/mpeg" };
    song.audioSource = { type: "local", label: file.name };
    song.updatedAt = nowISO();
    DB_save();
    UI_renderSongList();
    await MEDIA_loadSongSource(song);
  });
  $("removeCurrentAudioBtn").addEventListener("click", async () => {
    const song = PLAYER_song();
    if(!song) return;
    await idbDeleteAudio(song.id).catch(() => {});
    song.audioMeta = null;
    if(song.audioSource?.type === "local") song.audioSource = { type: "none", label: "" };
    song.updatedAt = nowISO();
    DB_save();
    UI_renderSongList();
    await MEDIA_loadSongSource(song);
  });

  $("openStageBtn").addEventListener("click", PLAYER_openStage);
  $("markBtn").addEventListener("click", PLAYER_toggleMark);
  $("finishMarkBtn").addEventListener("click", PLAYER_finishMark);
  $("playTimelineBtn").addEventListener("click", PLAYER_playTimeline);
  $("stopTimelineBtn").addEventListener("click", () => PLAYER_stopTimeline(true));
  $("playSlideBtn").addEventListener("click", PLAYER_playSlide);
  $("stopSlideBtn").addEventListener("click", () => PLAYER_stopSlide(true));

  $("stagePrevBtn").addEventListener("click", PLAYER_prev);
  $("stageNextBtn").addEventListener("click", PLAYER_next);
  $("stageCloseBtn").addEventListener("click", PLAYER_closeStage);

  $("cfgSaveBtn").addEventListener("click", CFG_save);
  $("cfgTestBtn").addEventListener("click", CFG_test);
  $("cfgReloadBtn").addEventListener("click", CFG_load);
  $("youtubeConnectBtn").addEventListener("click", YOUTUBE_connect);
  $("youtubeDisconnectBtn").addEventListener("click", YOUTUBE_disconnect);
  $("spotifyConnectBtn").addEventListener("click", SPOTIFY_connect);
  $("spotifyDisconnectBtn").addEventListener("click", SPOTIFY_disconnect);

  $("exportJsonBtn").addEventListener("click", BK_exportJSON);
  $("importFile").addEventListener("change", async (event) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if(file) await BK_importJSON(file);
  });
  $("clearLocalCacheBtn").addEventListener("click", BK_clearLocalCache);
  $("refreshRemoteBackupsBtn").addEventListener("click", BK_loadRemoteBackups);
  $("createRemoteBackupBtn").addEventListener("click", BK_createRemoteBackup);

  document.addEventListener("keydown", (event) => {
    if(!$("stage").classList.contains("active")) return;
    if(event.key === "Escape") PLAYER_closeStage();
    if(event.key === "ArrowRight") PLAYER_next();
    if(event.key === "ArrowLeft") PLAYER_prev();
  });

  window.addEventListener("focus", () => { if(REMOTE_STORE) REMOTE_STORE.syncNow(); });
  document.addEventListener("visibilitychange", () => {
    if(!document.hidden && REMOTE_STORE) REMOTE_STORE.syncNow();
  });

  window.addEventListener("storage", (event) => {
    if(event.key !== STORAGE_KEY || !event.newValue) return;
    state.songs = DB_load();
    UI_renderSongList();
  });

  getAudio().addEventListener("timeupdate", () => {
    $("stageTime").textContent = `${fmtTime(SOURCE_getCurrentTime())} / ${fmtTime(SOURCE_getDuration())}`;
  });
}

(async function boot(){
  bindEvents();
  await AUTH_handleSpotifyCallback();
  state.songs = DB_load();
  seedIfEmpty();
  UI_renderSongList();
  UI_resetEditor();
  UI_showTab("biblioteca");
  await CFG_load();
  await AUTH_refreshProfiles();
  await BK_loadRemoteBackups();

  if(REMOTE_STORE){
    await REMOTE_STORE.bootstrap({
      getLocalSnapshot: () => state.songs,
      applySnapshot: (snapshot) => {
        if(state.editingId) return false;
        DB_replaceSongs(snapshot);
        UI_renderSongList();
        if(state.viewingId && state.songs.some((song) => song.id === state.viewingId)) UI_openSongView(state.viewingId);
        return true;
      },
    });
    UI_setSyncStatus("Sincronização remota pronta.", "ok");
  }else{
    UI_setSyncStatus("Sync remota indisponível neste ambiente.", "warn");
  }
})();
