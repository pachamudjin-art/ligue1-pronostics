"""
Application principale FastAPI — Ligue 1 Pronostics
"""

from fastapi import FastAPI, Request, Form, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
import hashlib
import os
from datetime import datetime, timezone

from database import get_db, init_db, seed_users, seed_season_2025_2026
from scoring import (
    compute_points, compute_estimate_points,
    compute_matchday_stats, compute_general_ranking
)
from api_football import import_matchday_to_db, update_live_scores

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.join(BASE_DIR, "..", "frontend")

app = FastAPI(title="Ligue 1 Pronostics")

app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("SECRET_KEY", "changeme-in-production-please")
)

app.mount(
    "/static",
    StaticFiles(directory=os.path.join(FRONTEND_DIR, "static")),
    name="static"
)

templates = Jinja2Templates(directory=os.path.join(FRONTEND_DIR, "templates"))


# ─── Helpers ────────────────────────────────────────────────────────────────

def hash_password(pwd: str) -> str:
    return hashlib.sha256(pwd.encode()).hexdigest()


def get_current_user(request: Request):
    return request.session.get("user")


def require_login(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=303, detail="Not authenticated",
                            headers={"Location": "/login"})
    return user


def require_admin(request: Request):
    user = get_current_user(request)
    if not user or not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin requis")
    return user


def utcnow_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def parse_kickoff(kickoff_str: str) -> datetime:
    """Parse kickoff from DB (stored as UTC string)."""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            dt = datetime.strptime(kickoff_str[:19], fmt[:len(kickoff_str[:19])])
            return dt.replace(tzinfo=timezone.utc)
        except Exception:
            continue
    return datetime.min.replace(tzinfo=timezone.utc)


def match_is_locked(match_row) -> bool:
    """Un match est verrouillé si le coup d'envoi est passé."""
    kickoff = parse_kickoff(match_row["kickoff_time"])
    now = datetime.now(timezone.utc)
    return now >= kickoff


def matchday_first_kickoff(matches) -> datetime | None:
    if not matches:
        return None
    times = [parse_kickoff(m["kickoff_time"]) for m in matches]
    return min(times)


def get_active_season():
    conn = get_db()
    season = conn.execute(
        "SELECT * FROM seasons WHERE is_active=1 ORDER BY year_start DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return season


# ─── Auth ────────────────────────────────────────────────────────────────────

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


@app.post("/login")
async def login(request: Request,
                username: str = Form(...),
                password: str = Form(...)):
    conn = get_db()
    user = conn.execute(
        "SELECT * FROM users WHERE username=?", (username,)
    ).fetchone()
    conn.close()

    if user and user["password_hash"] == hash_password(password):
        request.session["user"] = {
            "id": user["id"],
            "username": user["username"],
            "is_admin": bool(user["is_admin"])
        }
        return RedirectResponse("/", status_code=303)

    return templates.TemplateResponse("login.html", {
        "request": request,
        "error": "Identifiants incorrects"
    })


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


# ─── Home : redirect vers journée courante ───────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)

    season = get_active_season()
    if not season:
        return templates.TemplateResponse("error.html", {
            "request": request,
            "message": "Aucune saison active. Contactez l'administrateur."
        })

    conn = get_db()
    # Trouver la journée la plus récente avec des matchs
    matchday = conn.execute("""
        SELECT md.* FROM matchdays md
        JOIN matches m ON m.matchday_id = md.id
        WHERE md.season_id = ?
        ORDER BY md.number
        LIMIT 1
    """, (season["id"],)).fetchone()

    if not matchday:
        matchday = conn.execute(
            "SELECT * FROM matchdays WHERE season_id=? ORDER BY number LIMIT 1",
            (season["id"],)
        ).fetchone()

    conn.close()

    if matchday:
        return RedirectResponse(f"/journee/{matchday['number']}", status_code=303)

    return templates.TemplateResponse("error.html", {
        "request": request,
        "message": "Aucune journée disponible."
    })


# ─── Journée ─────────────────────────────────────────────────────────────────

@app.get("/journee/{number}", response_class=HTMLResponse)
async def journee(request: Request, number: int):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)

    season = get_active_season()
    conn = get_db()

    matchday = conn.execute(
        "SELECT * FROM matchdays WHERE season_id=? AND number=?",
        (season["id"], number)
    ).fetchone()

    if not matchday:
        conn.close()
        return templates.TemplateResponse("error.html", {
            "request": request, "message": f"Journée {number} introuvable."
        })

    matches = conn.execute(
        "SELECT * FROM matches WHERE matchday_id=? ORDER BY kickoff_time",
        (matchday["id"],)
    ).fetchall()

    # Pronostics du joueur connecté
    my_pronostics = {}
    if matches:
        match_ids = [m["id"] for m in matches]
        placeholders = ",".join("?" * len(match_ids))
        rows = conn.execute(
            f"SELECT * FROM pronostics WHERE user_id=? AND match_id IN ({placeholders})",
            [user["id"]] + match_ids
        ).fetchall()
        my_pronostics = {r["match_id"]: r for r in rows}

    # Estimation du joueur
    my_estimate = conn.execute(
        "SELECT * FROM score_estimates WHERE user_id=? AND matchday_id=?",
        (user["id"], matchday["id"])
    ).fetchone()

    # Premier coup d'envoi de la journée
    first_ko = matchday_first_kickoff(matches)
    now = datetime.now(timezone.utc)
    estimate_locked = (first_ko is not None and now >= first_ko)

    # Liste des journées disponibles
    all_matchdays = conn.execute(
        "SELECT number FROM matchdays WHERE season_id=? ORDER BY number",
        (season["id"],)
    ).fetchall()

    # Pronostics de tous les joueurs (visibles uniquement si match commencé)
    all_pronostics = {}
    if user.get("is_admin") or True:  # on filtre côté template
        all_users = conn.execute("SELECT id, username FROM users WHERE is_admin=0").fetchall()
        for m in matches:
            if match_is_locked(m):
                rows = conn.execute(
                    "SELECT p.*, u.username FROM pronostics p JOIN users u ON u.id=p.user_id WHERE p.match_id=?",
                    (m["id"],)
                ).fetchall()
                all_pronostics[m["id"]] = [dict(r) for r in rows]

    # Calcul des points pour les matchs terminés
    points_by_match = {}
    for m in matches:
        if m["home_score"] is not None and m["away_score"] is not None:
            if m["id"] in my_pronostics:
                p = my_pronostics[m["id"]]
                result = compute_points(
                    m["home_score"], m["away_score"],
                    p["home_score"], p["away_score"]
                )
                points_by_match[m["id"]] = result

    conn.close()

    return templates.TemplateResponse("journee.html", {
        "request": request,
        "user": user,
        "season": dict(season),
        "matchday": dict(matchday),
        "matches": [dict(m) for m in matches],
        "my_pronostics": {k: dict(v) for k, v in my_pronostics.items()},
        "my_estimate": dict(my_estimate) if my_estimate else None,
        "estimate_locked": estimate_locked,
        "all_matchdays": [m["number"] for m in all_matchdays],
        "all_pronostics": all_pronostics,
        "points_by_match": points_by_match,
        "now_utc": utcnow_str(),
    })


# ─── Soumettre un pronostic ───────────────────────────────────────────────────

@app.post("/pronostic/submit")
async def submit_pronostic(
    request: Request,
    match_id: int = Form(...),
    home_score: int = Form(...),
    away_score: int = Form(...)
):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"ok": False, "error": "Non connecté"}, status_code=401)

    conn = get_db()
    match = conn.execute("SELECT * FROM matches WHERE id=?", (match_id,)).fetchone()
    if not match:
        conn.close()
        return JSONResponse({"ok": False, "error": "Match introuvable"}, status_code=404)

    if match_is_locked(match):
        conn.close()
        return JSONResponse({"ok": False, "error": "Match verrouillé"}, status_code=403)

    now = utcnow_str()
    conn.execute("""
        INSERT INTO pronostics (user_id, match_id, home_score, away_score, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id, match_id) DO UPDATE SET
            home_score=excluded.home_score,
            away_score=excluded.away_score,
            updated_at=excluded.updated_at
    """, (user["id"], match_id, home_score, away_score, now))
    conn.commit()
    conn.close()

    return JSONResponse({"ok": True})


# ─── Soumettre une estimation ─────────────────────────────────────────────────

@app.post("/estimation/submit")
async def submit_estimation(
    request: Request,
    matchday_id: int = Form(...),
    estimated_score: int = Form(...)
):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"ok": False, "error": "Non connecté"}, status_code=401)

    conn = get_db()
    matches = conn.execute(
        "SELECT * FROM matches WHERE matchday_id=? ORDER BY kickoff_time LIMIT 1",
        (matchday_id,)
    ).fetchall()

    first_ko = matchday_first_kickoff(matches)
    now_dt = datetime.now(timezone.utc)
    if first_ko and now_dt >= first_ko:
        conn.close()
        return JSONResponse({"ok": False, "error": "Estimation verrouillée"}, status_code=403)

    now = utcnow_str()
    conn.execute("""
        INSERT INTO score_estimates (user_id, matchday_id, estimated_score, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id, matchday_id) DO UPDATE SET
            estimated_score=excluded.estimated_score,
            updated_at=excluded.updated_at
    """, (user["id"], matchday_id, estimated_score, now))
    conn.commit()
    conn.close()

    return JSONResponse({"ok": True})


# ─── Classement ──────────────────────────────────────────────────────────────

@app.get("/classement", response_class=HTMLResponse)
async def classement(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)

    season = get_active_season()
    conn = get_db()

    users = conn.execute("SELECT * FROM users WHERE is_admin=0").fetchall()
    players_data = []

    for u in users:
        # Tous les pronostics avec résultats connus
        rows = conn.execute("""
            SELECT p.home_score as pred_home, p.away_score as pred_away,
                   m.home_score as real_home, m.away_score as real_away
            FROM pronostics p
            JOIN matches m ON m.id = p.match_id
            JOIN matchdays md ON md.id = m.matchday_id
            WHERE p.user_id = ? AND md.season_id = ?
              AND m.home_score IS NOT NULL AND m.away_score IS NOT NULL
        """, (u["id"], season["id"])).fetchall()

        stats = compute_matchday_stats([dict(r) for r in rows])

        # Estimations justes
        estimates_ok = 0
        matchdays = conn.execute(
            "SELECT id FROM matchdays WHERE season_id=?", (season["id"],)
        ).fetchall()

        for md in matchdays:
            est = conn.execute(
                "SELECT estimated_score FROM score_estimates WHERE user_id=? AND matchday_id=?",
                (u["id"], md["id"])
            ).fetchone()
            if est:
                # Calcul du score réel pour cette journée
                md_rows = conn.execute("""
                    SELECT p.home_score as pred_home, p.away_score as pred_away,
                           m.home_score as real_home, m.away_score as real_away
                    FROM pronostics p
                    JOIN matches m ON m.id = p.match_id
                    WHERE p.user_id = ? AND m.matchday_id = ?
                      AND m.home_score IS NOT NULL AND m.away_score IS NOT NULL
                """, (u["id"], md["id"])).fetchall()

                md_stats = compute_matchday_stats([dict(r) for r in md_rows])
                if compute_estimate_points(md_stats["points"], est["estimated_score"]) == 2:
                    estimates_ok += 1

        total_points = stats["points"] + (estimates_ok * 2)

        players_data.append({
            "user_id": u["id"],
            "username": u["username"],
            "points": total_points,
            "pj": stats["pj"],
            "pp": stats["pp"],
            "pa": stats["pa"],
            "bb": stats["bb"],
            "estimates_ok": estimates_ok,
        })

    conn.close()
    ranked = compute_general_ranking(players_data)

    all_matchdays = []
    conn2 = get_db()
    mds = conn2.execute(
        "SELECT number FROM matchdays WHERE season_id=? ORDER BY number",
        (season["id"],)
    ).fetchall()
    all_matchdays = [m["number"] for m in mds]
    conn2.close()

    return templates.TemplateResponse("classement.html", {
        "request": request,
        "user": user,
        "season": dict(season),
        "ranking": ranked,
        "all_matchdays": all_matchdays,
    })


# ─── Admin ────────────────────────────────────────────────────────────────────

@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    admin = require_admin(request)
    season = get_active_season()
    conn = get_db()

    matchdays = conn.execute(
        "SELECT md.*, COUNT(m.id) as nb_matches FROM matchdays md "
        "LEFT JOIN matches m ON m.matchday_id=md.id "
        "WHERE md.season_id=? GROUP BY md.id ORDER BY md.number",
        (season["id"],)
    ).fetchall()

    conn.close()

    return templates.TemplateResponse("admin.html", {
        "request": request,
        "user": admin,
        "season": dict(season),
        "matchdays": [dict(m) for m in matchdays],
    })


@app.get("/admin/journee/{number}", response_class=HTMLResponse)
async def admin_journee(request: Request, number: int):
    admin = require_admin(request)
    season = get_active_season()
    conn = get_db()

    matchday = conn.execute(
        "SELECT * FROM matchdays WHERE season_id=? AND number=?",
        (season["id"], number)
    ).fetchone()

    matches = conn.execute(
        "SELECT * FROM matches WHERE matchday_id=? ORDER BY kickoff_time",
        (matchday["id"],)
    ).fetchall() if matchday else []

    conn.close()

    return templates.TemplateResponse("admin_journee.html", {
        "request": request,
        "user": admin,
        "season": dict(season),
        "matchday": dict(matchday) if matchday else None,
        "matches": [dict(m) for m in matches],
    })


@app.post("/admin/match/add")
async def admin_add_match(
    request: Request,
    matchday_id: int = Form(...),
    home_team: str = Form(...),
    away_team: str = Form(...),
    kickoff_date: str = Form(...),
    kickoff_time: str = Form(...)
):
    require_admin(request)
    kickoff = f"{kickoff_date} {kickoff_time}:00"
    conn = get_db()
    conn.execute(
        "INSERT INTO matches (matchday_id, home_team, away_team, kickoff_time) VALUES (?, ?, ?, ?)",
        (matchday_id, home_team, away_team, kickoff)
    )
    conn.commit()
    conn.close()

    md = get_db().execute("SELECT number FROM matchdays WHERE id=?", (matchday_id,)).fetchone()
    return RedirectResponse(f"/admin/journee/{md['number']}", status_code=303)


@app.post("/admin/match/update-score")
async def admin_update_score(
    request: Request,
    match_id: int = Form(...),
    home_score: int = Form(...),
    away_score: int = Form(...)
):
    require_admin(request)
    conn = get_db()
    conn.execute(
        "UPDATE matches SET home_score=?, away_score=?, status='finished' WHERE id=?",
        (home_score, away_score, match_id)
    )
    conn.commit()
    md_row = conn.execute(
        "SELECT md.number FROM matchdays md JOIN matches m ON m.matchday_id=md.id WHERE m.id=?",
        (match_id,)
    ).fetchone()
    conn.close()
    return RedirectResponse(f"/admin/journee/{md_row['number']}", status_code=303)


@app.post("/admin/match/delete")
async def admin_delete_match(request: Request, match_id: int = Form(...)):
    require_admin(request)
    conn = get_db()
    md_row = conn.execute(
        "SELECT md.number FROM matchdays md JOIN matches m ON m.matchday_id=md.id WHERE m.id=?",
        (match_id,)
    ).fetchone()
    conn.execute("DELETE FROM pronostics WHERE match_id=?", (match_id,))
    conn.execute("DELETE FROM matches WHERE id=?", (match_id,))
    conn.commit()
    conn.close()
    return RedirectResponse(f"/admin/journee/{md_row['number']}", status_code=303)


@app.post("/admin/import-api")
async def admin_import_api(
    request: Request,
    matchday_number: int = Form(...)
):
    admin = require_admin(request)
    season = get_active_season()
    conn = get_db()

    matchday = conn.execute(
        "SELECT * FROM matchdays WHERE season_id=? AND number=?",
        (season["id"], matchday_number)
    ).fetchone()

    if not matchday:
        conn.close()
        return JSONResponse({"ok": False, "error": "Journée introuvable"})

    nb, errors = import_matchday_to_db(
        season["year_start"], matchday_number,
        season["id"], matchday["id"], conn
    )
    conn.close()

    return JSONResponse({"ok": True, "imported": nb, "errors": errors})


@app.post("/admin/update-scores-api")
async def admin_update_scores_api(request: Request):
    require_admin(request)
    season = get_active_season()
    conn = get_db()
    nb = update_live_scores(season["year_start"], conn)
    conn.close()
    return JSONResponse({"ok": True, "updated": nb})


@app.post("/admin/match/update-kickoff")
async def admin_update_kickoff(
    request: Request,
    match_id: int = Form(...),
    kickoff_date: str = Form(...),
    kickoff_time: str = Form(...)
):
    require_admin(request)
    kickoff = f"{kickoff_date} {kickoff_time}:00"
    conn = get_db()
    conn.execute("UPDATE matches SET kickoff_time=? WHERE id=?", (kickoff, match_id))
    conn.commit()
    md_row = conn.execute(
        "SELECT md.number FROM matchdays md JOIN matches m ON m.matchday_id=md.id WHERE m.id=?",
        (match_id,)
    ).fetchone()
    conn.close()
    return RedirectResponse(f"/admin/journee/{md_row['number']}", status_code=303)


# ─── Démarrage ────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    init_db()
    seed_users()
    seed_season_2025_2026()
    print("Application démarrée.")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)


# ─── Chat global ─────────────────────────────────────────────────────────────

@app.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)

    conn = get_db()
    messages = conn.execute("""
        SELECT cm.id, cm.message, cm.created_at, u.username
        FROM chat_messages cm
        JOIN users u ON u.id = cm.user_id
        ORDER BY cm.created_at DESC
        LIMIT 100
    """).fetchall()
    conn.close()

    return templates.TemplateResponse("chat.html", {
        "request": request,
        "user": user,
        "messages": [dict(m) for m in reversed(messages)],
    })


@app.get("/chat/messages")
async def chat_poll(request: Request, after_id: int = 0):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"ok": False}, status_code=401)
    conn = get_db()
    rows = conn.execute("""
        SELECT cm.id, cm.message, cm.created_at, u.username
        FROM chat_messages cm
        JOIN users u ON u.id = cm.user_id
        WHERE cm.id > ?
        ORDER BY cm.created_at ASC
        LIMIT 50
    """, (after_id,)).fetchall()
    conn.close()
    return JSONResponse({"ok": True, "messages": [dict(r) for r in rows]})


@app.post("/chat/send")
async def chat_send(request: Request, message: str = Form(...)):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"ok": False, "error": "Non connecté"}, status_code=401)
    message = message.strip()
    if not message:
        return JSONResponse({"ok": False, "error": "Message vide"})
    if len(message) > 500:
        return JSONResponse({"ok": False, "error": "Message trop long (500 car. max)"})
    conn = get_db()
    cursor = conn.execute(
        "INSERT INTO chat_messages (user_id, message) VALUES (?, ?)",
        (user["id"], message)
    )
    new_id = cursor.lastrowid
    conn.commit()
    row = conn.execute("""
        SELECT cm.id, cm.message, cm.created_at, u.username
        FROM chat_messages cm JOIN users u ON u.id=cm.user_id
        WHERE cm.id=?
    """, (new_id,)).fetchone()
    conn.close()
    return JSONResponse({"ok": True, "message": dict(row)})


@app.post("/chat/delete")
async def chat_delete(request: Request, message_id: int = Form(...)):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"ok": False}, status_code=401)
    conn = get_db()
    msg = conn.execute("SELECT user_id FROM chat_messages WHERE id=?", (message_id,)).fetchone()
    if not msg:
        conn.close()
        return JSONResponse({"ok": False, "error": "Message introuvable"})
    if msg["user_id"] != user["id"] and not user.get("is_admin"):
        conn.close()
        return JSONResponse({"ok": False, "error": "Non autorisé"}, status_code=403)
    conn.execute("DELETE FROM chat_messages WHERE id=?", (message_id,))
    conn.commit()
    conn.close()
    return JSONResponse({"ok": True})


# ─── Debug admin ─────────────────────────────────────────────────────────────

@app.get("/admin/debug", response_class=HTMLResponse)
async def admin_debug(request: Request):
    require_admin(request)
    conn = get_db()

    matches = conn.execute("""
        SELECT m.id, m.home_team, m.away_team, m.kickoff_time,
               m.home_score, m.away_score, m.status, md.number as journee
        FROM matches m JOIN matchdays md ON md.id = m.matchday_id
        ORDER BY md.number, m.kickoff_time
    """).fetchall()

    pronostics = conn.execute("""
        SELECT p.id, u.username, m.home_team, m.away_team,
               p.home_score as pred_h, p.away_score as pred_a,
               m.home_score as real_h, m.away_score as real_a,
               m.kickoff_time
        FROM pronostics p
        JOIN users u ON u.id = p.user_id
        JOIN matches m ON m.id = p.match_id
        ORDER BY u.username, m.kickoff_time
    """).fetchall()

    conn.close()

    rows_m = "".join(
        f"<tr><td>J{m['journee']}</td><td>{m['home_team']} – {m['away_team']}</td>"
        f"<td>{m['kickoff_time']}</td>"
        f"<td style='color:{'#2ea043' if m['home_score'] is not None else '#e74c3c'}'>"
        f"{'%d–%d' % (m['home_score'], m['away_score']) if m['home_score'] is not None else 'PAS DE SCORE'}</td>"
        f"<td>{m['status']}</td></tr>"
        for m in matches
    )

    rows_p = "".join(
        f"<tr><td>{p['username']}</td><td>{p['home_team']} – {p['away_team']}</td>"
        f"<td>{p['pred_h']}–{p['pred_a']}</td>"
        f"<td>{'%d–%d' % (p['real_h'], p['real_a']) if p['real_h'] is not None else '—'}</td>"
        f"<td>{p['kickoff_time']}</td></tr>"
        for p in pronostics
    )

    html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8">
    <style>body{{font-family:sans-serif;background:#0d1117;color:#e6edf3;padding:2rem}}
    table{{border-collapse:collapse;width:100%;margin-bottom:2rem;font-size:.85rem}}
    th{{background:#21262d;padding:.5rem .75rem;text-align:left;color:#8b949e}}
    td{{padding:.45rem .75rem;border-bottom:1px solid #30363d}}
    h2{{color:#e8c45a;margin:1.5rem 0 .75rem}}</style></head><body>
    <h2>Matchs en base ({len(matches)})</h2>
    <table><thead><tr><th>J</th><th>Match</th><th>Kickoff UTC</th><th>Score</th><th>Statut</th></tr></thead>
    <tbody>{rows_m}</tbody></table>
    <h2>Pronostics en base ({len(pronostics)})</h2>
    <table><thead><tr><th>Joueur</th><th>Match</th><th>Prono</th><th>Score réel</th><th>Kickoff UTC</th></tr></thead>
    <tbody>{rows_p}</tbody></table>
    <p style="color:#8b949e;font-size:.8rem">Heure serveur UTC : {utcnow_str()}</p>
    </body></html>"""

    return HTMLResponse(html)
