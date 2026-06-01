/* ══════════════════════════════════════════════════════════
   Ligue 1 Pronostics — JS principal
   ══════════════════════════════════════════════════════════ */

// ── Toast ────────────────────────────────────────────────────
function toast(msg, type = "ok", duration = 3000) {
  let container = document.getElementById("toast");
  if (!container) {
    container = document.createElement("div");
    container.id = "toast";
    document.body.appendChild(container);
  }
  const el = document.createElement("div");
  el.className = `toast-item ${type}`;
  el.textContent = msg;
  container.appendChild(el);
  setTimeout(() => el.remove(), duration);
}

// ── Soumettre un pronostic ───────────────────────────────────
async function submitProno(matchId) {
  const h = document.getElementById(`h_${matchId}`)?.value;
  const a = document.getElementById(`a_${matchId}`)?.value;

  if (h === "" || a === "" || h === null || a === null) {
    toast("Saisissez les deux scores", "err"); return;
  }
  if (parseInt(h) < 0 || parseInt(a) < 0) {
    toast("Score invalide", "err"); return;
  }

  const btn = document.getElementById(`btn_${matchId}`);
  if (btn) btn.disabled = true;

  const fd = new FormData();
  fd.append("match_id", matchId);
  fd.append("home_score", h);
  fd.append("away_score", a);

  try {
    const res = await fetch("/pronostic/submit", { method: "POST", body: fd });
    const data = await res.json();
    if (data.ok) {
      toast("✓ Pronostic enregistré !", "ok");
      // Met à jour l'affichage inline
      const display = document.getElementById(`prono_display_${matchId}`);
      if (display) {
        display.innerHTML = `<span class="score-display">${h} – ${a}</span>`;
      }
      const form = document.getElementById(`prono_form_${matchId}`);
      if (form) form.style.display = "none";
      const editBtn = document.getElementById(`edit_btn_${matchId}`);
      if (editBtn) editBtn.style.display = "inline-flex";
    } else {
      toast(data.error || "Erreur", "err");
    }
  } catch (e) {
    toast("Erreur réseau", "err");
  }
  if (btn) btn.disabled = false;
}

// ── Afficher le formulaire d'édition ────────────────────────
function editProno(matchId) {
  const form = document.getElementById(`prono_form_${matchId}`);
  const editBtn = document.getElementById(`edit_btn_${matchId}`);
  if (form) form.style.display = "flex";
  if (editBtn) editBtn.style.display = "none";
}

// ── Soumettre l'estimation ───────────────────────────────────
async function submitEstimation(matchdayId) {
  const val = document.getElementById("estimation_input")?.value;
  if (val === "" || val === null) {
    toast("Entrez votre estimation", "err"); return;
  }

  const fd = new FormData();
  fd.append("matchday_id", matchdayId);
  fd.append("estimated_score", val);

  try {
    const res = await fetch("/estimation/submit", { method: "POST", body: fd });
    const data = await res.json();
    if (data.ok) {
      toast("✓ Estimation enregistrée !", "ok");
      const display = document.getElementById("estimation_display");
      if (display) {
        display.innerHTML = `<span class="score-display">${val} pts estimés</span>`;
      }
      const form = document.getElementById("estimation_form");
      if (form) form.style.display = "none";
      const editBtn = document.getElementById("estimation_edit_btn");
      if (editBtn) editBtn.style.display = "inline-flex";
    } else {
      toast(data.error || "Erreur", "err");
    }
  } catch (e) {
    toast("Erreur réseau", "err");
  }
}

// ── Navigation journée ───────────────────────────────────────
function goToJournee(select, seasonId) {
  const val = select.value;
  if (val) window.location.href = `/saison/${seasonId}/journee/${val}`;
}

// ── Admin : importer depuis l'API ─────────────────────────────
async function importFromApi(matchdayNumber) {
  const btn = document.getElementById("btn_import_api");
  const origLabel = btn ? btn.textContent : "";
  if (btn) { btn.disabled = true; btn.textContent = "⏳ Import en cours…"; }
  toast("Import en cours, patientez 10-15 secondes…", "ok", 15000);

  const fd = new FormData();
  fd.append("matchday_number", matchdayNumber);

  // Timeout explicite de 60 secondes
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 60000);

  try {
    const res = await fetch("/admin/import-api", {
      method: "POST", body: fd, signal: controller.signal
    });
    clearTimeout(timeout);
    const data = await res.json();
    if (data.ok) {
      toast(`✓ ${data.imported} match(s) importé(s)`, "ok");
      if (data.errors && data.errors.length) {
        toast("Erreurs : " + data.errors.join(", "), "err");
      }
      setTimeout(() => window.location.reload(), 1500);
    } else {
      toast(data.error || "Erreur API", "err");
    }
  } catch (e) {
    clearTimeout(timeout);
    if (e.name === "AbortError") {
      toast("Délai dépassé (60s) — vérifiez votre clé API", "err");
    } else {
      toast("Erreur réseau : " + e.message, "err");
    }
  }
  if (btn) { btn.disabled = false; btn.textContent = origLabel; }
}

// ── Admin : mettre à jour les scores via API ──────────────────
async function updateScoresApi() {
  const btn = document.getElementById("btn_update_scores");
  const origLabel = btn ? btn.textContent : "";
  if (btn) { btn.disabled = true; btn.textContent = "⏳ Mise à jour…"; }
  toast("Récupération des scores en cours…", "ok", 15000);

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 60000);

  try {
    const res = await fetch("/admin/update-scores-api", {
      method: "POST", signal: controller.signal
    });
    clearTimeout(timeout);
    const data = await res.json();
    if (data.ok) {
      toast(`✓ ${data.updated} score(s) mis à jour`, "ok");
      setTimeout(() => window.location.reload(), 1500);
    } else {
      toast("Erreur API", "err");
    }
  } catch (e) {
    clearTimeout(timeout);
    if (e.name === "AbortError") {
      toast("Délai dépassé (60s)", "err");
    } else {
      toast("Erreur réseau : " + e.message, "err");
    }
  }
  if (btn) { btn.disabled = false; btn.textContent = origLabel; }
}

// ── Countdown ────────────────────────────────────────────────
function initCountdowns() {
  document.querySelectorAll("[data-kickoff]").forEach(el => {
    const kickoff = new Date(el.dataset.kickoff + "Z"); // UTC
    function tick() {
      const diff = kickoff - Date.now();
      if (diff <= 0) {
        el.textContent = "Coup d'envoi !";
        return;
      }
      const h = Math.floor(diff / 3600000);
      const m = Math.floor((diff % 3600000) / 60000);
      const s = Math.floor((diff % 60000) / 1000);
      el.textContent = h > 0
        ? `dans ${h}h${String(m).padStart(2,"0")}`
        : `dans ${m}m${String(s).padStart(2,"0")}s`;
      setTimeout(tick, 1000);
    }
    tick();
  });
}

document.addEventListener("DOMContentLoaded", initCountdowns);


// ══════════════════════════════════════════════════════════════
// Chat flottant
// ══════════════════════════════════════════════════════════════

let chatLastId = 0;
let chatOpen = false;
let chatUnread = 0;
let chatPollTimer = null;
let gifDebounce = null;

function initFloatingChat() {
  loadChatHistory();
  startChatPolling();
}

// ── Ouvrir / fermer le panneau ───────────────────────────────
function toggleChat() {
  chatOpen = !chatOpen;
  const panel = document.getElementById("chat-panel");
  const fab   = document.getElementById("chat-fab");
  const overlay = document.getElementById("chat-overlay");

  panel.classList.toggle("open", chatOpen);
  fab.classList.toggle("open", chatOpen);
  overlay.classList.toggle("open", chatOpen);

  if (chatOpen) {
    chatUnread = 0;
    updateNotifBadge();
    setTimeout(() => {
      const box = document.getElementById("chat-box");
      if (box) box.scrollTop = box.scrollHeight;
      // Ne pas ouvrir le clavier automatiquement sur mobile
      if (window.innerWidth > 480) {
        document.getElementById("chat-input")?.focus();
      }
    }, 320);
    closeEmojiPicker();
    closeGifPicker();
  }
}

// ── Badge notifications ──────────────────────────────────────
function updateNotifBadge() {
  const badge = document.getElementById("chat-notif");
  if (!badge) return;
  if (chatUnread > 0 && !chatOpen) {
    badge.textContent = chatUnread > 9 ? "9+" : chatUnread;
    badge.style.display = "flex";
  } else {
    badge.style.display = "none";
  }
}

// ── Charger l'historique ─────────────────────────────────────
async function loadChatHistory() {
  try {
    const res = await fetch("/chat/messages?after_id=0");
    const data = await res.json();
    if (data.ok && data.messages.length > 0) {
      const box = document.getElementById("chat-box");
      if (!box) return;
      box.innerHTML = "";
      data.messages.forEach(msg => box.appendChild(renderChatMsg(msg)));
      chatLastId = data.messages[data.messages.length - 1].id;
      box.scrollTop = box.scrollHeight;
    }
  } catch(e) {}
}

// ── Polling toutes les 15s ───────────────────────────────────
function startChatPolling() {
  chatPollTimer = setInterval(async () => {
    try {
      const res = await fetch(`/chat/messages?after_id=${chatLastId}`);
      const data = await res.json();
      if (data.ok && data.messages.length > 0) {
        const box = document.getElementById("chat-box");
        if (!box) return;
        const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
        data.messages.forEach(msg => {
          if (!box.querySelector(`[data-id="${msg.id}"]`)) {
            box.appendChild(renderChatMsg(msg));
            chatLastId = msg.id;
            if (!chatOpen && msg.username !== window.CHAT_USER) {
              chatUnread++;
              updateNotifBadge();
            }
          }
        });
        if (atBottom || chatOpen) box.scrollTop = box.scrollHeight;
      }
    } catch(e) {}
  }, 15000);
}

// ── Rendre un message ────────────────────────────────────────
const REACTION_EMOJIS = ["👍","❤️","😂","😮","😢","😡","🖕"];

function renderReactionsHtml(msg) {
  const reactions = msg.reactions || [];
  const hasMyReaction = reactions.some(r => r.mine);
  const chips = reactions.map(r =>
    `<button class="react-chip${r.mine ? ' mine' : ''}"
      onclick="sendReaction(${msg.id},'${r.emoji}')"
      title="${r.users || ''}">${r.emoji} <span>${r.count}</span></button>`
  ).join("");
  // Bouton ＋ seulement si pas encore réagi
  const addBtn = hasMyReaction ? "" :
    `<button class="react-add-btn" onclick="toggleReactPicker(${msg.id})">＋</button>`;
  return `<div class="reactions-row" id="reactions-${msg.id}">${chips}${addBtn}</div>
    <div class="react-picker" id="react-picker-${msg.id}" style="display:none">${
      REACTION_EMOJIS.map(e => `<button onclick="sendReaction(${msg.id},'${e}')">${e}</button>`).join("")
    }</div>`;
}

function renderChatMsg(msg) {
  const isMine = msg.username === window.CHAT_USER;
  const canDel = isMine || window.CHAT_IS_ADMIN;

  const d = new Date(msg.created_at.replace(" ", "T") + "Z");
  const timeStr = d.toLocaleTimeString("fr-FR", {hour:"2-digit", minute:"2-digit"});

  const gifMatch = msg.message.match(/^\[GIF:(https?:\/\/[^\]]+)\]$/);

  const div = document.createElement("div");
  div.className = "chat-msg" + (isMine ? " mine" : "");
  div.dataset.id = msg.id;

  const contentHtml = gifMatch
    ? `<img class="chat-gif" src="${escHtml(gifMatch[1])}" alt="GIF" loading="lazy">`
    : escHtml(msg.message);

  div.innerHTML = `
    <div class="chat-bubble">
      <div class="chat-meta">
        <span class="chat-author">${escHtml(msg.username)}</span>
        <span class="chat-time">${timeStr}</span>
        ${canDel ? `<button class="chat-del-btn" onclick="deleteChatMsg(${msg.id},this)">✕</button>` : ""}
      </div>
      <div class="chat-text">${contentHtml}</div>
    </div>
    ${renderReactionsHtml(msg)}`;
  return div;
}

function toggleReactPicker(msgId) {
  document.querySelectorAll(".react-picker").forEach(p => {
    if (p.id !== `react-picker-${msgId}`) p.style.display = "none";
  });
  const picker = document.getElementById(`react-picker-${msgId}`);
  if (picker) picker.style.display = picker.style.display === "none" ? "flex" : "none";
}

async function sendReaction(msgId, emoji) {
  const picker = document.getElementById(`react-picker-${msgId}`);
  if (picker) picker.style.display = "none";
  const fd = new FormData();
  fd.append("message_id", msgId);
  fd.append("emoji", emoji);
  try {
    const res = await fetch("/chat/react", {method:"POST", body:fd});
    const data = await res.json();
    if (data.ok) {
      const row = document.getElementById(`reactions-${msgId}`);
      if (row) {
        const hasMyReaction = data.reactions.some(r => r.mine);
        const chips = data.reactions.map(r =>
          `<button class="react-chip${r.mine ? ' mine' : ''}"
            onclick="sendReaction(${msgId},'${r.emoji}')"
            title="${r.users || ''}">${r.emoji} <span>${r.count}</span></button>`
        ).join("");
        const addBtn = hasMyReaction ? "" :
          `<button class="react-add-btn" onclick="toggleReactPicker(${msgId})">＋</button>`;
        row.innerHTML = chips + addBtn;
      }
    }
  } catch(e) { console.error(e); }
}

document.addEventListener("click", e => {
  if (!e.target.closest(".react-add-btn") && !e.target.closest(".react-picker")) {
    document.querySelectorAll(".react-picker").forEach(p => p.style.display = "none");
  }
});

// ── Appui long mobile pour ouvrir le sélecteur de réaction ───
(function() {
  let pressTimer = null;
  let pressTarget = null;

  function startPress(e) {
    const bubble = e.target.closest(".chat-bubble");
    if (!bubble) return;
    const msgDiv = bubble.closest(".chat-msg");
    if (!msgDiv) return;
    const msgId = msgDiv.dataset.id;
    pressTarget = msgId;
    pressTimer = setTimeout(() => {
      if (pressTarget === msgId) {
        // Vibration tactile si disponible
        if (navigator.vibrate) navigator.vibrate(40);
        toggleReactPicker(msgId);
        // Scroller pour que le picker soit visible
        const picker = document.getElementById(`react-picker-${msgId}`);
        if (picker) picker.scrollIntoView({block: "nearest", behavior: "smooth"});
      }
    }, 500);
  }

  function cancelPress() {
    if (pressTimer) { clearTimeout(pressTimer); pressTimer = null; }
    pressTarget = null;
  }

  document.addEventListener("touchstart", startPress, {passive: true});
  document.addEventListener("touchend", cancelPress, {passive: true});
  document.addEventListener("touchmove", cancelPress, {passive: true});
})();

function escHtml(s) {
  return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")
    .replace(/"/g,"&quot;").replace(/'/g,"&#039;");
}

// ── Envoyer un message ───────────────────────────────────────
async function sendChatMsg() {
  const input = document.getElementById("chat-input");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  closeEmojiPicker();
  // Fermer le clavier sur mobile
  input.blur();

  const fd = new FormData();
  fd.append("message", text);
  try {
    const res = await fetch("/chat/send", {method:"POST", body:fd});
    const data = await res.json();
    if (data.ok) {
      const box = document.getElementById("chat-box");
      box.appendChild(renderChatMsg(data.message));
      chatLastId = data.message.id;
      box.scrollTop = box.scrollHeight;
    } else {
      toast(data.error || "Erreur envoi", "err");
    }
  } catch(e) {
    toast("Erreur réseau", "err");
  }
}

// ── Supprimer un message ─────────────────────────────────────
async function deleteChatMsg(msgId, btn) {
  if (!confirm("Supprimer ce message ?")) return;
  const fd = new FormData();
  fd.append("message_id", msgId);
  const res = await fetch("/chat/delete", {method:"POST", body:fd});
  const data = await res.json();
  if (data.ok) {
    document.getElementById("chat-box")?.querySelector(`[data-id="${msgId}"]`)?.remove();
  }
}

// ── Émojis ───────────────────────────────────────────────────
function toggleEmojiPicker() {
  const picker = document.getElementById("emoji-picker");
  const gifPicker = document.getElementById("gif-picker");
  const btn = document.querySelector('.chat-tool-btn[onclick="toggleEmojiPicker()"]');
  const isOpen = picker.style.display !== "none";
  gifPicker.style.display = "none";
  document.querySelector('.chat-tool-btn[onclick="toggleGifPicker()"]')?.classList.remove("active");
  picker.style.display = isOpen ? "none" : "block";
  btn?.classList.toggle("active", !isOpen);
}

function closeEmojiPicker() {
  document.getElementById("emoji-picker").style.display = "none";
  document.querySelector('.chat-tool-btn[onclick="toggleEmojiPicker()"]')?.classList.remove("active");
}

function insertEmoji(emoji) {
  const input = document.getElementById("chat-input");
  const pos = input.selectionStart;
  input.value = input.value.slice(0, pos) + emoji + input.value.slice(pos);
  input.selectionStart = input.selectionEnd = pos + emoji.length;
  input.focus();
}

// ── GIFs ─────────────────────────────────────────────────────
function toggleGifPicker() {
  const picker = document.getElementById("gif-picker");
  const emojiPicker = document.getElementById("emoji-picker");
  const btn = document.querySelector('.chat-tool-btn[onclick="toggleGifPicker()"]');
  const isOpen = picker.style.display !== "none";
  emojiPicker.style.display = "none";
  document.querySelector('.chat-tool-btn[onclick="toggleEmojiPicker()"]')?.classList.remove("active");
  picker.style.display = isOpen ? "none" : "flex";
  btn?.classList.toggle("active", !isOpen);
  if (!isOpen) {
    document.getElementById("gif-search")?.focus();
    loadTrendingGifs();
  }
}

function closeGifPicker() {
  document.getElementById("gif-picker").style.display = "none";
  document.querySelector('.chat-tool-btn[onclick="toggleGifPicker()"]')?.classList.remove("active");
}

async function loadTrendingGifs() {
  const results = document.getElementById("gif-results");
  results.innerHTML = '<span style="color:var(--muted);font-size:.8rem;padding:.5rem">Chargement…</span>';
  try {
    const res = await fetch("/giphy/trending");
    const data = await res.json();
    displayGifs(data.data);
  } catch(e) {
    results.innerHTML = '<span style="color:var(--muted);font-size:.8rem;padding:.5rem">Erreur GIPHY — vérifiez la clé API</span>';
  }
}

function searchGifs(query) {
  clearTimeout(gifDebounce);
  if (!query.trim()) { loadTrendingGifs(); return; }
  gifDebounce = setTimeout(async () => {
    const results = document.getElementById("gif-results");
    results.innerHTML = '<span style="color:var(--muted);font-size:.8rem;padding:.5rem">Recherche…</span>';
    try {
      const res = await fetch(`/giphy/search?q=${encodeURIComponent(query)}`);
      const data = await res.json();
      displayGifs(data.data);
    } catch(e) {}
  }, 400);
}

function displayGifs(gifs) {
  const results = document.getElementById("gif-results");
  if (!gifs || gifs.length === 0) {
    results.innerHTML = '<span style="color:var(--muted);font-size:.8rem;padding:.5rem">Aucun GIF trouvé</span>';
    return;
  }
  results.innerHTML = "";
  gifs.forEach(gif => {
    const img = document.createElement("img");
    img.className = "gif-thumb";
    img.src = gif.images.fixed_width_small.url;
    img.alt = gif.title;
    img.loading = "lazy";
    img.onclick = () => sendGif(gif.images.original.url);
    results.appendChild(img);
  });
}

async function sendGif(url) {
  closeGifPicker();
  const fd = new FormData();
  fd.append("message", `[GIF:${url}]`);
  try {
    const res = await fetch("/chat/send", {method:"POST", body:fd});
    const data = await res.json();
    if (data.ok) {
      const box = document.getElementById("chat-box");
      box.appendChild(renderChatMsg(data.message));
      chatLastId = data.message.id;
      box.scrollTop = box.scrollHeight;
    }
  } catch(e) {
    toast("Erreur envoi GIF", "err");
  }
}


// ── Admin : importer la saison complète ──────────────────────
async function importSaisonComplete() {
  if (!confirm("Importer toute la saison (34 journées) depuis l'API ?\nCela peut prendre 30 à 60 secondes.")) return;

  const btn = document.getElementById("btn_import_saison");
  const progress = document.getElementById("import_saison_progress");
  if (btn) { btn.disabled = true; btn.textContent = "⏳ Import en cours…"; }
  if (progress) { progress.style.display = "block"; progress.textContent = "Connexion à l'API…"; }

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 120000); // 2 min max

  try {
    const res = await fetch("/admin/import-saison-complete", {
      method: "POST", signal: controller.signal
    });
    clearTimeout(timeout);
    const data = await res.json();

    if (data.ok) {
      const msg = `✓ ${data.total_imported} match(s) importé(s) sur ${data.journees_importees.length} journée(s)`;
      toast(msg, "ok", 5000);
      if (progress) progress.textContent = msg;

      if (data.journees_vides && data.journees_vides.length > 0) {
        toast(`${data.journees_vides.length} journée(s) sans match (calendrier incomplet ?)`, "ok", 4000);
      }
      if (data.errors && data.errors.length > 0) {
        toast("Quelques erreurs : " + data.errors[0], "err", 5000);
      }
      setTimeout(() => window.location.reload(), 2000);
    } else {
      toast(data.error || "Erreur import", "err");
      if (progress) progress.style.display = "none";
    }
  } catch (e) {
    clearTimeout(timeout);
    if (e.name === "AbortError") {
      toast("Délai dépassé (2 min) — l'import est peut-être en cours, rechargez la page", "err", 6000);
    } else {
      toast("Erreur réseau : " + e.message, "err");
    }
    if (progress) progress.style.display = "none";
  }

  if (btn) { btn.disabled = false; btn.textContent = "📥 Importer toute la saison (API)"; }
}


// ── Import pronostics LibreOffice ─────────────────────────────
async function importOds() {
  if (!confirm("Importer tous les pronostics du tableau LibreOffice 2025/2026 ?\nCela peut prendre 30 secondes.")) return;
  const btn = document.getElementById("btn_import_ods");
  if (btn) { btn.disabled = true; btn.textContent = "⏳ Import en cours…"; }
  toast("Import des pronostics LibreOffice…", "ok", 30000);
  try {
    const res = await fetch("/admin/import-ods", { method: "POST" });
    const data = await res.json();
    if (data.ok) {
      toast(`✓ ${data.pronos} pronostics + ${data.estimations} estimations importés`, "ok", 6000);
      if (data.errors && data.errors.length) toast("Erreurs: " + data.errors[0], "err", 5000);
    } else {
      toast("Erreur: " + (data.error || "inconnue"), "err");
    }
  } catch(e) {
    toast("Erreur réseau: " + e.message, "err");
  }
  if (btn) { btn.disabled = false; btn.textContent = "📋 Importer pronostics LibreOffice"; }
}


// ── Pronostic Podium ─────────────────────────────────────────
async function submitPodium(e, seasonId) {
  e.preventDefault();
  const formId = e.target.id;
  const form = document.getElementById(formId);
  const fd = new FormData(form);
  fd.set("season_id", seasonId);
  const vals = [fd.get("rank1"), fd.get("rank2"), fd.get("rank3")];
  if (vals.some(v => !v)) {
    toast("Choisissez les 3 équipes du podium", "err"); return;
  }
  if (new Set(vals).size < 3) {
    toast("Vous ne pouvez pas pronostiquer la même équipe deux fois !", "err"); return;
  }
  try {
    const res = await fetch("/podium/submit", {method:"POST", body:fd});
    const data = await res.json();
    if (data.ok) {
      toast("✓ Pronostic podium enregistré !", "ok");
      setTimeout(() => window.location.reload(), 1200);
    } else {
      toast(data.error || "Erreur", "err");
    }
  } catch(e) {
    toast("Erreur réseau", "err");
  }
}
