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
  if (btn) btn.disabled = true;

  const fd = new FormData();
  fd.append("matchday_number", matchdayNumber);

  try {
    const res = await fetch("/admin/import-api", { method: "POST", body: fd });
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
    toast("Erreur réseau", "err");
  }
  if (btn) btn.disabled = false;
}

// ── Admin : mettre à jour les scores via API ──────────────────
async function updateScoresApi() {
  const btn = document.getElementById("btn_update_scores");
  if (btn) btn.disabled = true;

  try {
    const res = await fetch("/admin/update-scores-api", { method: "POST" });
    const data = await res.json();
    if (data.ok) {
      toast(`✓ ${data.updated} score(s) mis à jour`, "ok");
      setTimeout(() => window.location.reload(), 1500);
    } else {
      toast("Erreur API", "err");
    }
  } catch (e) {
    toast("Erreur réseau", "err");
  }
  if (btn) btn.disabled = false;
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
