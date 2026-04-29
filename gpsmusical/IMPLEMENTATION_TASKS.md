# GPS Musical - Integração Spotify: Separação de Tarefas

**Data:** 26 de abril de 2026  
**Status:** Em andamento  

---

## 📋 TAREFAS CODEX (Manual + CLI)

**O QUE:** Configurar credenciais no Spotify Developer Dashboard  
**QUEM:** Você (manual no navegador)  
**TEMPO:** ~10 minutos  

### CODEX 1: Criar App Spotify Developer
```bash
# Passo 1: Abrir Spotify Developer Dashboard
https://developer.spotify.com/dashboard

# Passo 2: Login ou criar conta Spotify
# (você faz isso manualmente no navegador)

# Passo 3: Criar App "GPS Musical"
# - Clique "Create an App"
# - Nome: GPS Musical
# - Descrição: App para sincronização de repertório com marcação temporal
# - Aceite termos
# - Clique "Create"

# Passo 4: Copiar credenciais
# Na página da app, você verá:
# - Client ID: xxxxxxxxxxxxxxxxxx
# - Client Secret: yyyyyyyyyyyyyyyy
```

**Resultado esperado:**
- [ ] Client ID copiado
- [ ] Client Secret copiado
- [ ] Guardados em local seguro

---

### CODEX 2: Configurar Redirect URI
```bash
# Passo 1: Na dashboard, clique "Edit Settings"

# Passo 2: Na seção "Redirect URIs", adicione:
# Para desenvolvimento local:
http://localhost:8000/gpsmusical/

# Para produção (substitua seudominio.com):
https://seudominio.com/gpsmusical/

# Passo 3: Salvar

# Resultado: Duas URIs configuradas no dashboard
```

**Resultado esperado:**
- [ ] Redirect URI configurada (local ou remota)
- [ ] Salvo no dashboard

---

### CODEX 3: Testar Conectividade (Post-implementação)
```bash
# Será feito APÓS aplicar mudanças de código

# Passo 1: Abrir o app local
http://localhost:8000/gpsmusical/

# Passo 2: Ir para aba "Config"

# Passo 3: Seção Spotify
# - Cole Client ID: xxxxxx
# - Cole Redirect URI: http://localhost:8000/gpsmusical/
# - Clique "Salvar configuração"

# Passo 4: Clique "Conectar Spotify"
# - Browser redireciona para Spotify
# - Você autoriza o app
# - Volta para o app com token
# - Deve aparecer "Conectado" com seu nome

# Se funcionar: ✅ Sucesso!
# Se erro: Ver troubleshooting em CODEX_SPOTIFY_INTEGRATION.md
```

---

## 🤖 TAREFAS COPILOT (Via Agente)

**O QUE:** Aplicar todas as mudanças de código  
**QUEM:** Agente Copilot  
**TEMPO:** ~15 minutos  

### COPILOT 1: Modificar app.js
- [ ] Adicionar constantes Spotify API
- [ ] Estender state com propriedades Spotify
- [ ] Implementar `window.onSpotifyWebPlaybackSDKReady()`
- [ ] Implementar `SPOTIFY_updatePlaybackState()`
- [ ] Implementar `PLAYER_updateTimelineForSpotify()`
- [ ] Implementar `SPOTIFY_search()` com debounce
- [ ] Implementar `SPOTIFY_searchDebounced()`
- [ ] Implementar `UI_renderSpotifySearchResults()`
- [ ] Implementar `SPOTIFY_selectSearchResult()`
- [ ] Implementar `SPOTIFY_getTrackInfo()`
- [ ] Estender `SOURCE_play()` → adicionar `SPOTIFY_play()`
- [ ] Estender `SOURCE_pause()` → adicionar `SPOTIFY_pause()`
- [ ] Estender `SOURCE_seek()` → adicionar `SPOTIFY_seek()`
- [ ] Estender `SOURCE_getCurrentTime()` para Spotify
- [ ] Estender `SOURCE_getDuration()` para Spotify
- [ ] Estender `SOURCE_supportsTimeline()` incluir Spotify
- [ ] Atualizar `UI_updatePlaybackButtons()` com hint melhorado
- [ ] Atualizar `SPOTIFY_exchangeCode()` para reinit player
- [ ] Adicionar listeners de evento search input
- [ ] Atualizar `UI_renderAuthStatus()` para editor auth badge

### COPILOT 2: Modificar index.html
- [ ] Adicionar Web Playback SDK script no `<head>`
- [ ] Substituir `<div id="editorSourceSpotify">` por UI com busca
- [ ] Adicionar `<div id="spotifySearchResults">`
- [ ] Adicionar auth status badge

### COPILOT 3: Modificar styles.css
- [ ] Adicionar estilos `.search-results`
- [ ] Adicionar estilos `.spotify-search-item`
- [ ] Adicionar estilos `.spotify-result-image`
- [ ] Adicionar estilos `.spotify-result-info`

---

## 🔄 SEQUÊNCIA DE EXECUÇÃO

### Fase 1: CODEX (Você faz agora)
1. ✅ Acesse https://developer.spotify.com/dashboard
2. ✅ Crie app "GPS Musical"
3. ✅ Copie Client ID e Client Secret
4. ✅ Configure Redirect URI
5. ⏳ **Guarde as credenciais** (você usará depois)

### Fase 2: COPILOT (Agente vai fazer)
1. ⏳ Aplicar mudanças em app.js (~500 linhas)
2. ⏳ Aplicar mudanças em index.html (~20 linhas)
3. ⏳ Aplicar mudanças em styles.css (~50 linhas)

### Fase 3: CODEX (Você faz depois)
1. ⏳ Colar Client ID em Config → Spotify
2. ⏳ Colar Redirect URI em Config → Spotify
3. ⏳ Clique "Conectar Spotify"
4. ⏳ Autorize no navegador Spotify
5. ⏳ Verifique status "Conectado"

### Fase 4: Testes
1. ⏳ Testar busca de faixa
2. ⏳ Testar play/pause/seek
3. ⏳ Testar marcação de blocos com timeline

---

## 📁 Arquivos a Modificar

```
app.js
├─ Após linha 11: Adicionar constantes
├─ Linha ~47: Estender state
├─ Linha ~55: Adicionar onSpotifyWebPlaybackSDKReady
├─ Linha ~1000-1055: Estender SOURCE_*
├─ Linha ~1680: Adicionar SPOTIFY_search e helpers
├─ Linha ~1598: Atualizar SPOTIFY_exchangeCode
├─ Linha ~1560: Atualizar UI_renderAuthStatus
└─ Listeners: Adicionar event listeners search input

index.html
├─ Linha ~10: Adicionar Web Playback SDK script
├─ Linha ~147: Substituir editorSourceSpotify
└─ Listeners: Adicionar ao fim do código

styles.css
└─ Final: Adicionar estilos search + results
```

---

## ✅ Checklist Final

### Pre-Codex
- [ ] Documento lido e entendido
- [ ] Acesso ao Spotify Developer Dashboard

### Codex - App Spotify
- [ ] App criado
- [ ] Client ID copiado
- [ ] Client Secret guardado
- [ ] Redirect URI configurada

### Copilot - Código
- [ ] app.js modificado (20+ mudanças)
- [ ] index.html modificado (2 mudanças)
- [ ] styles.css modificado (1 mudança)

### Post-Codex - Teste
- [ ] Credentials coladas em Config
- [ ] OAuth flow testado
- [ ] Status "Conectado" verificado
- [ ] Busca de faixa testada
- [ ] Play/pause testado
- [ ] Timeline testada

---

## 🚨 Troubleshooting Rápido

| Erro | Causa | Solução |
|------|-------|---------|
| redirect_uri_mismatch | URL diferente no config | Verifique barra final |
| invalid_client | Client ID errado | Regenere no dashboard |
| Token não aparece | PKCE expirou | Limpe sessionStorage |
| Busca não funciona | Sem autenticação | Conecte Spotify em Config |

---

## 📞 Próximos Passos

1. **Agora:** Vá ao Spotify Dashboard (CODEX 1+2)
2. **Depois:** Espere agente aplicar mudanças (COPILOT 1+2+3)
3. **Depois:** Coloque credenciais em Config (CODEX 3)
4. **Depois:** Teste a integração completa

---

**Tempo total estimado:** ~30-40 minutos  
**Complexidade:** Média (setup OAuth + 20+ mudanças de código)  
**Risco:** Baixo (mudanças isoladas, sem quebrar funcionalidade existente)
