"""
Application principale FastAPI — Ligue 1 Pronostics (PostgreSQL)
"""

from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
import hashlib
import os
from datetime import datetime, timezone

from database import (get_db, release_db, init_db, seed_users, seed_active_season,
                      ensure_season_exists, get_current_season_years,
                      q, qone, qall)
from scoring import (compute_points, compute_estimate_points,
                     compute_matchday_stats, compute_general_ranking)
from api_football import import_matchday_to_db, update_live_scores, fetch_fixtures, fetch_teams
from scoring import compute_podium_points

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.join(BASE_DIR, "..", "frontend")

app = FastAPI(title="Ligue 1 Pronostics")

@app.get("/health")
async def health():
    import os
    db_url = os.environ.get("DATABASE_URL", "")
    db_test = "non testé"
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT 1 as ok")
        row = c.fetchone()
        release_db(conn)
        db_test = "OK" if row else "pas de réponse"
    except Exception as e:
        db_test = f"ERREUR: {str(e)[:200]}"
    return {"status": "ok", "db_url_set": bool(db_url), "db_test": db_test}

app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("SECRET_KEY", "changeme-in-production-please")
)
app.mount("/static", StaticFiles(directory=os.path.join(FRONTEND_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(FRONTEND_DIR, "templates"))


# ─── Helpers ────────────────────────────────────────────────────────────────

def hash_password(pwd): return hashlib.sha256(pwd.encode()).hexdigest()
def get_current_user(request): return request.session.get("user")

def require_admin(request):
    user = get_current_user(request)
    if not user or not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin requis")
    return user

def utcnow_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

def parse_kickoff(s):
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s[:19], fmt).replace(tzinfo=timezone.utc)
        except: continue
    return datetime.min.replace(tzinfo=timezone.utc)

def match_is_locked(m):
    return datetime.now(timezone.utc) >= parse_kickoff(m["kickoff_time"])

def matchday_first_kickoff(matches):
    if not matches: return None
    return min(parse_kickoff(m["kickoff_time"]) for m in matches)

def get_active_season():
    conn = get_db()
    s = qone(conn, "SELECT * FROM seasons WHERE is_active=1 ORDER BY year_start DESC LIMIT 1")
    release_db(conn)
    return dict(s) if s else None

def get_season_by_id(sid):
    conn = get_db()
    s = qone(conn, "SELECT * FROM seasons WHERE id=%s", (sid,))
    release_db(conn)
    return dict(s) if s else None

def get_all_seasons():
    conn = get_db()
    rows = qall(conn, "SELECT * FROM seasons ORDER BY year_start DESC")
    release_db(conn)
    return [dict(r) for r in rows]

def compute_ranking_for_season(season_id, conn):
    users = qall(conn, "SELECT * FROM users WHERE is_admin=0")
    matchdays = qall(conn, "SELECT id FROM matchdays WHERE season_id=%s", (season_id,))
    # Résultat podium réel (si existe)
    podium_result = qone(conn, "SELECT * FROM podium_results WHERE season_id=%s", (season_id,))
    players_data = []
    for u in users:
        rows = qall(conn, """
            SELECT p.home_score as pred_home, p.away_score as pred_away,
                   m.home_score as real_home, m.away_score as real_away
            FROM pronostics p
            JOIN matches m ON m.id=p.match_id
            JOIN matchdays md ON md.id=m.matchday_id
            WHERE p.user_id=%s AND md.season_id=%s
              AND m.home_score IS NOT NULL AND m.away_score IS NOT NULL
        """, (u["id"], season_id))
        stats = compute_matchday_stats([dict(r) for r in rows])
        # Points podium
        podium_pts = 0
        if podium_result:
            my_podium = qone(conn, "SELECT * FROM podium_pronostics WHERE user_id=%s AND season_id=%s",
                            (u["id"], season_id))
            if my_podium:
                pp = compute_podium_points(
                    podium_result["rank1"], podium_result["rank2"], podium_result["rank3"],
                    my_podium["rank1"], my_podium["rank2"], my_podium["rank3"]
                )
                podium_pts = pp["points"]
        estimates_ok = 0
        for md in matchdays:
            est = qone(conn, "SELECT estimated_score FROM score_estimates WHERE user_id=%s AND matchday_id=%s",
                       (u["id"], md["id"]))
            if est:
                # Vérifier que TOUS les matchs de la journée sont terminés
                total_matches = qone(conn, "SELECT COUNT(*) as cnt FROM matches WHERE matchday_id=%s", (md["id"],))
                finished_matches = qone(conn, "SELECT COUNT(*) as cnt FROM matches WHERE matchday_id=%s AND home_score IS NOT NULL", (md["id"],))
                if not total_matches or not finished_matches: continue
                if total_matches["cnt"] == 0 or finished_matches["cnt"] < total_matches["cnt"]: continue
                md_rows = qall(conn, """
                    SELECT p.home_score as pred_home, p.away_score as pred_away,
                           m.home_score as real_home, m.away_score as real_away
                    FROM pronostics p JOIN matches m ON m.id=p.match_id
                    WHERE p.user_id=%s AND m.matchday_id=%s
                      AND m.home_score IS NOT NULL AND m.away_score IS NOT NULL
                """, (u["id"], md["id"]))
                md_stats = compute_matchday_stats([dict(r) for r in md_rows])
                if compute_estimate_points(md_stats["points"], est["estimated_score"]) == 2:
                    estimates_ok += 1
        players_data.append({
            "user_id": u["id"], "username": u["username"],
            "points": stats["points"] + (estimates_ok * 2) + podium_pts,
            "pj": stats["pj"], "pp": stats["pp"], "pa": stats["pa"],
            "bb": stats["bb"], "estimates_ok": estimates_ok,
            "podium_pts": podium_pts,
        })
    return compute_general_ranking(players_data)


# ─── Auth ────────────────────────────────────────────────────────────────────

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    conn = get_db()
    user = qone(conn, "SELECT * FROM users WHERE username=%s", (username,))
    release_db(conn)
    if user and user["password_hash"] == hash_password(password):
        request.session["user"] = {
            "id": user["id"], "username": user["username"],
            "is_admin": bool(user["is_admin"]),
            "theme": user.get("theme") or "ligue1"
        }
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "error": "Identifiants incorrects"})

@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


# ─── Changement de mot de passe ──────────────────────────────────────────────

@app.get("/profil", response_class=HTMLResponse)
async def profil_page(request: Request):
    user = get_current_user(request)
    if not user: return RedirectResponse("/login", status_code=303)
    season = get_active_season()
    return templates.TemplateResponse("profil.html", {"request": request, "user": user, "season": season})

@app.post("/profil/change-theme")
async def change_theme(request: Request, theme: str = Form(...)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    valid_themes = ["ligue1", "nuit-bleue", "rouge-passion", "neon", "minimaliste", "glassmorphism", "terrain", "ardoise", "brume", "l1classic"]
    if theme not in valid_themes:
        theme = "ligue1"
    conn = get_db()
    q(conn, "UPDATE users SET theme=%s WHERE id=%s", (theme, user["id"]))
    conn.commit()
    release_db(conn)
    # Mettre à jour la session
    request.session["user"]["theme"] = theme
    return RedirectResponse("/profil", status_code=303)


@app.post("/profil/change-password")
async def change_password(request: Request, current_password: str = Form(...),
                          new_password: str = Form(...), confirm_password: str = Form(...)):
    user = get_current_user(request)
    if not user: return RedirectResponse("/login", status_code=303)
    season = get_active_season()
    if new_password != confirm_password:
        return templates.TemplateResponse("profil.html", {
            "request": request, "user": user, "season": season,
            "error": "Les nouveaux mots de passe ne correspondent pas."
        })
    if len(new_password) < 4:
        return templates.TemplateResponse("profil.html", {
            "request": request, "user": user, "season": season,
            "error": "Le mot de passe doit faire au moins 4 caractères."
        })
    conn = get_db()
    db_user = qone(conn, "SELECT * FROM users WHERE id=%s", (user["id"],))
    if db_user["password_hash"] != hash_password(current_password):
        release_db(conn)
        return templates.TemplateResponse("profil.html", {
            "request": request, "user": user, "season": season,
            "error": "Mot de passe actuel incorrect."
        })
    q(conn, "UPDATE users SET password_hash=%s WHERE id=%s", (hash_password(new_password), user["id"]))
    conn.commit()
    release_db(conn)
    return templates.TemplateResponse("profil.html", {
        "request": request, "user": user, "season": season,
        "success": "Mot de passe modifié avec succès !"
    })

# Admin : changer le mdp d'un joueur
@app.post("/admin/reset-password")
async def admin_reset_password(request: Request, user_id: int = Form(...), new_password: str = Form(...)):
    require_admin(request)
    conn = get_db()
    q(conn, "UPDATE users SET password_hash=%s WHERE id=%s", (hash_password(new_password), user_id))
    conn.commit()
    release_db(conn)
    return RedirectResponse("/admin", status_code=303)


# ─── Home ────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    user = get_current_user(request)
    if not user: return RedirectResponse("/login", status_code=303)
    season = get_active_season()
    if not season:
        return templates.TemplateResponse("error.html", {"request": request, "message": "Aucune saison active."})
    conn = get_db()
    matchday = qone(conn, """
        SELECT md.* FROM matchdays md
        JOIN matches m ON m.matchday_id=md.id
        WHERE md.season_id=%s ORDER BY md.number DESC LIMIT 1
    """, (season["id"],))
    if not matchday:
        matchday = qone(conn, "SELECT * FROM matchdays WHERE season_id=%s ORDER BY number LIMIT 1", (season["id"],))
    release_db(conn)
    if matchday:
        return RedirectResponse(f"/saison/{season['id']}/journee/{matchday['number']}", status_code=303)
    return templates.TemplateResponse("error.html", {"request": request, "message": "Aucune journée disponible."})


# ─── Journée ─────────────────────────────────────────────────────────────────

@app.get("/saison/{season_id}/journee/{number}", response_class=HTMLResponse)
async def journee(request: Request, season_id: int, number: int):
    user = get_current_user(request)
    if not user: return RedirectResponse("/login", status_code=303)

    season = get_season_by_id(season_id)
    if not season:
        return templates.TemplateResponse("error.html", {"request": request, "message": "Saison introuvable."})

    active_season = get_active_season()
    is_active_season = (active_season and active_season["id"] == season_id)
    read_only = not is_active_season

    conn = get_db()
    matchday = qone(conn, "SELECT * FROM matchdays WHERE season_id=%s AND number=%s", (season_id, number))
    if not matchday:
        release_db(conn)
        return templates.TemplateResponse("error.html", {"request": request, "message": f"Journée {number} introuvable."})

    matches = qall(conn, "SELECT * FROM matches WHERE matchday_id=%s ORDER BY kickoff_time", (matchday["id"],))
    matches = [dict(m) for m in matches]

    my_pronostics = {}
    if matches:
        match_ids = [m["id"] for m in matches]
        placeholders = ",".join(["%s"] * len(match_ids))
        rows = qall(conn, f"SELECT * FROM pronostics WHERE user_id=%s AND match_id IN ({placeholders})",
                    [user["id"]] + match_ids)
        my_pronostics = {r["match_id"]: dict(r) for r in rows}

    my_estimate = qone(conn, "SELECT * FROM score_estimates WHERE user_id=%s AND matchday_id=%s",
                       (user["id"], matchday["id"]))

    first_ko = matchday_first_kickoff(matches)
    now = datetime.now(timezone.utc)
    estimate_locked = read_only or (first_ko is not None and now >= first_ko)

    md_rows = qall(conn, "SELECT number, label FROM matchdays WHERE season_id=%s ORDER BY number", (season_id,))
    all_matchdays = [r["number"] for r in md_rows]
    all_matchday_labels = {r["number"]: r["label"] or f"Journée {r['number']}" for r in md_rows}

    # Pronostics visibles si match commencé ou saison archivée
    all_pronostics = {}
    for m in matches:
        if match_is_locked(m) or read_only:
            rows = qall(conn, "SELECT p.*, u.username FROM pronostics p JOIN users u ON u.id=p.user_id WHERE p.match_id=%s", (m["id"],))
            enriched = []
            for r in rows:
                d = dict(r)
                # Calculer le label du pronostic si résultat connu
                if m["home_score"] is not None and m["away_score"] is not None:
                    result = compute_points(m["home_score"], m["away_score"], d["home_score"], d["away_score"])
                    d["label"] = result["label"]
                else:
                    d["label"] = ""
                enriched.append(d)
            all_pronostics[m["id"]] = enriched

    points_by_match = {}
    for m in matches:
        if m["home_score"] is not None and m["away_score"] is not None:
            if m["id"] in my_pronostics:
                p = my_pronostics[m["id"]]
                points_by_match[m["id"]] = compute_points(m["home_score"], m["away_score"], p["home_score"], p["away_score"])

    # Joueurs sans pronostic sur les matchs non verrouillés
    all_users = qall(conn, "SELECT id, username FROM users WHERE is_admin=0 ORDER BY username")
    missing_pronostics = []
    open_matches = [m for m in matches if not match_is_locked(m)]
    if open_matches and not read_only:
        open_match_ids = [m["id"] for m in open_matches]
        placeholders2 = ",".join(["%s"] * len(open_match_ids))
        for u in all_users:
            rows2 = qall(conn,
                f"SELECT match_id FROM pronostics WHERE user_id=%s AND match_id IN ({placeholders2})",
                [u["id"]] + open_match_ids)
            if len(rows2) < len(open_match_ids):
                missing_pronostics.append(u["username"])

    # ── Classement de la journée ──
    finished_matches = [m for m in matches if m["home_score"] is not None and m["away_score"] is not None]
    matchday_ranking = []
    if finished_matches:
        finished_ids = [m["id"] for m in finished_matches]
        ph = ",".join(["%s"] * len(finished_ids))
        for u in all_users:
            # Pronostics sur les matchs terminés
            u_pronos = qall(conn,
                f"SELECT p.*, m.home_score as real_h, m.away_score as real_a FROM pronostics p "
                f"JOIN matches m ON m.id=p.match_id "
                f"WHERE p.user_id=%s AND p.match_id IN ({ph})",
                [u["id"]] + finished_ids)
            prono_data = [{"pred_home": r["home_score"], "pred_away": r["away_score"],
                           "real_home": r["real_h"], "real_away": r["real_a"]} for r in u_pronos]
            stats = compute_matchday_stats(prono_data)

            # Estimation
            est = qone(conn, "SELECT estimated_score FROM score_estimates WHERE user_id=%s AND matchday_id=%s",
                      (u["id"], matchday["id"]))
            est_pts = 0
            if est and len(finished_matches) == len(matches):
                est_pts = compute_estimate_points(stats["points"], est["estimated_score"])

            matchday_ranking.append({
                "username": u["username"],
                "points": stats["points"] + est_pts,
                "pts_bruts": stats["points"],
                "est_pts": est_pts,
                "pj": stats["pj"], "pp": stats["pp"],
                "pa": stats["pa"], "bb": stats["bb"],
                "estimation": est["estimated_score"] if est else None,
                "nb_pronos": len(u_pronos),
                "nb_matchs": len(finished_matches),
            })
        matchday_ranking.sort(key=lambda x: (-x["points"], -x["pj"], -x["pp"], -x["pa"]))
        # Ajouter le rang
        for i, p in enumerate(matchday_ranking):
            if i > 0:
                prev = matchday_ranking[i-1]
                if p["points"] == prev["points"] and p["pj"] == prev["pj"] and p["pp"] == prev["pp"] and p["pa"] == prev["pa"]:
                    p["rank"] = prev["rank"]
                else:
                    p["rank"] = i + 1
            else:
                p["rank"] = 1

    # Podium (pour compétitions cup, uniquement 1ère journée)
    season_data = get_season_by_id(season_id)
    show_podium = (
        season_data and season_data.get("competition_type") == "cup"
        and number == min(all_matchdays) if all_matchdays else False
    )
    my_podium = None
    podium_result = None
    podium_locked = False
    podium_teams = []
    if show_podium:
        my_podium = qone(conn, "SELECT * FROM podium_pronostics WHERE user_id=%s AND season_id=%s",
                         (user["id"], season_id))
        podium_result = qone(conn, "SELECT * FROM podium_results WHERE season_id=%s", (season_id,))
        if first_ko:
            podium_locked = datetime.now(timezone.utc) >= first_ko
        if not podium_locked:
            try:
                podium_teams = sorted(fetch_teams(season_data.get("api_code", "FL1"), season_data["year_start"]))
            except:
                pass

    all_seasons = get_all_seasons()
    release_db(conn)

    return templates.TemplateResponse("journee.html", {
        "request": request, "user": user,
        "season": season, "all_seasons": all_seasons,
        "active_season_id": active_season["id"] if active_season else None,
        "matchday": dict(matchday), "matches": matches,
        "my_pronostics": my_pronostics,
        "my_estimate": dict(my_estimate) if my_estimate else None,
        "estimate_locked": estimate_locked,
        "all_matchdays": all_matchdays,
        "all_matchday_labels": all_matchday_labels,
        "all_pronostics": all_pronostics,
        "points_by_match": points_by_match,
        "now_utc": utcnow_str(),
        "read_only": read_only,
        "missing_pronostics": missing_pronostics,
        "matchday_ranking": matchday_ranking,
        "nb_finished": len(finished_matches),
        "show_podium": show_podium,
        "my_podium": dict(my_podium) if my_podium else None,
        "podium_result": dict(podium_result) if podium_result else None,
        "podium_locked": podium_locked,
        "podium_teams": podium_teams,
    })

@app.get("/journee/{number}", response_class=HTMLResponse)
async def journee_compat(request: Request, number: int):
    user = get_current_user(request)
    if not user: return RedirectResponse("/login", status_code=303)
    season = get_active_season()
    if season: return RedirectResponse(f"/saison/{season['id']}/journee/{number}", status_code=303)
    return RedirectResponse("/", status_code=303)


# ─── Pronostic ────────────────────────────────────────────────────────────────

@app.post("/pronostic/submit")
async def submit_pronostic(request: Request, match_id: int = Form(...),
                           home_score: int = Form(...), away_score: int = Form(...)):
    user = get_current_user(request)
    if not user: return JSONResponse({"ok": False, "error": "Non connecté"}, status_code=401)
    conn = get_db()
    match = qone(conn, "SELECT * FROM matches WHERE id=%s", (match_id,))
    if not match:
        release_db(conn)
        return JSONResponse({"ok": False, "error": "Match introuvable"}, status_code=404)
    if match_is_locked(match):
        release_db(conn)
        return JSONResponse({"ok": False, "error": "Match verrouillé"}, status_code=403)
    md = qone(conn, "SELECT md.season_id FROM matchdays md JOIN matches m ON m.matchday_id=md.id WHERE m.id=%s", (match_id,))
    active = get_active_season()
    if not active or md["season_id"] != active["id"]:
        release_db(conn)
        return JSONResponse({"ok": False, "error": "Saison archivée"}, status_code=403)
    now = utcnow_str()
    q(conn, """
        INSERT INTO pronostics (user_id, match_id, home_score, away_score, updated_at)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT(user_id, match_id) DO UPDATE SET
            home_score=EXCLUDED.home_score, away_score=EXCLUDED.away_score, updated_at=EXCLUDED.updated_at
    """, (user["id"], match_id, home_score, away_score, now))
    conn.commit()
    release_db(conn)
    return JSONResponse({"ok": True})


# ─── Estimation ───────────────────────────────────────────────────────────────

@app.post("/estimation/submit")
async def submit_estimation(request: Request, matchday_id: int = Form(...),
                            estimated_score: int = Form(...)):
    user = get_current_user(request)
    if not user: return JSONResponse({"ok": False, "error": "Non connecté"}, status_code=401)
    conn = get_db()
    md = qone(conn, "SELECT * FROM matchdays WHERE id=%s", (matchday_id,))
    active = get_active_season()
    if not active or md["season_id"] != active["id"]:
        release_db(conn)
        return JSONResponse({"ok": False, "error": "Saison archivée"}, status_code=403)
    matches = qall(conn, "SELECT * FROM matches WHERE matchday_id=%s ORDER BY kickoff_time LIMIT 1", (matchday_id,))
    first_ko = matchday_first_kickoff(matches)
    if first_ko and datetime.now(timezone.utc) >= first_ko:
        release_db(conn)
        return JSONResponse({"ok": False, "error": "Estimation verrouillée"}, status_code=403)
    now = utcnow_str()
    q(conn, """
        INSERT INTO score_estimates (user_id, matchday_id, estimated_score, updated_at)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT(user_id, matchday_id) DO UPDATE SET
            estimated_score=EXCLUDED.estimated_score, updated_at=EXCLUDED.updated_at
    """, (user["id"], matchday_id, estimated_score, now))
    conn.commit()
    release_db(conn)
    return JSONResponse({"ok": True})


# ─── Pronostic Podium ────────────────────────────────────────────────────────

@app.post("/podium/submit")
async def submit_podium(request: Request, season_id: int = Form(...),
                        rank1: str = Form(...), rank2: str = Form(...), rank3: str = Form(...)):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"ok": False, "error": "Non connecté"}, status_code=401)
    conn = get_db()
    season = qone(conn, "SELECT * FROM seasons WHERE id=%s", (season_id,))
    if not season or not season.get("is_active"):
        release_db(conn)
        return JSONResponse({"ok": False, "error": "Compétition non active"}, status_code=403)
    # Vérifier que le 1er match n'a pas encore commencé
    first_match = qone(conn, """
        SELECT m.kickoff_time FROM matches m
        JOIN matchdays md ON md.id=m.matchday_id
        WHERE md.season_id=%s ORDER BY m.kickoff_time LIMIT 1
    """, (season_id,))
    if first_match:
        first_ko = parse_kickoff(first_match["kickoff_time"])
        if datetime.now(timezone.utc) >= first_ko:
            release_db(conn)
            return JSONResponse({"ok": False, "error": "Pronostic podium verrouillé"}, status_code=403)
    now = utcnow_str()
    q(conn, """
        INSERT INTO podium_pronostics (user_id, season_id, rank1, rank2, rank3)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT(user_id, season_id) DO UPDATE SET
            rank1=EXCLUDED.rank1, rank2=EXCLUDED.rank2, rank3=EXCLUDED.rank3, updated_at=%s
    """, (user["id"], season_id, rank1.strip(), rank2.strip(), rank3.strip(), now))
    conn.commit()
    release_db(conn)
    return JSONResponse({"ok": True})


@app.get("/saison/{season_id}/podium", response_class=HTMLResponse)
async def podium_page(request: Request, season_id: int):
    user = get_current_user(request)
    if not user: return RedirectResponse("/login", status_code=303)
    season = get_season_by_id(season_id)
    if not season:
        return templates.TemplateResponse("error.html", {"request": request, "message": "Compétition introuvable."})

    conn = get_db()
    # Pronostics podium de tous les joueurs
    all_pronos = qall(conn, """
        SELECT pp.*, u.username FROM podium_pronostics pp
        JOIN users u ON u.id=pp.user_id WHERE pp.season_id=%s
    """, (season_id,))

    # Résultat podium réel
    podium_result = qone(conn, "SELECT * FROM podium_results WHERE season_id=%s", (season_id,))

    # Mon pronostic
    my_prono = qone(conn, "SELECT * FROM podium_pronostics WHERE user_id=%s AND season_id=%s",
                    (user["id"], season_id))

    # Premier match de la compétition (pour verrouillage)
    first_match = qone(conn, """
        SELECT m.kickoff_time FROM matches m
        JOIN matchdays md ON md.id=m.matchday_id
        WHERE md.season_id=%s ORDER BY m.kickoff_time LIMIT 1
    """, (season_id,))
    locked = False
    if first_match:
        locked = datetime.now(timezone.utc) >= parse_kickoff(first_match["kickoff_time"])

    # Équipes disponibles depuis l'API (ou liste vide si pas dispo)
    teams = []
    if not locked:
        try:
            teams = fetch_teams(season.get("api_code", "FL1"), season["year_start"])
        except:
            pass

    # Calcul des points podium si résultat connu
    podium_scores = []
    if podium_result:
        for p in all_pronos:
            pts = compute_podium_points(
                podium_result["rank1"], podium_result["rank2"], podium_result["rank3"],
                p["rank1"], p["rank2"], p["rank3"]
            )
            podium_scores.append({"username": p["username"], **pts})
        podium_scores.sort(key=lambda x: -x["points"])

    active_season = get_active_season()
    all_seasons = get_all_seasons()
    release_db(conn)

    return templates.TemplateResponse("podium.html", {
        "request": request, "user": user,
        "season": season, "all_seasons": all_seasons,
        "active_season_id": active_season["id"] if active_season else None,
        "all_pronos": [dict(p) for p in all_pronos],
        "my_prono": dict(my_prono) if my_prono else None,
        "podium_result": dict(podium_result) if podium_result else None,
        "podium_scores": podium_scores,
        "locked": locked,
        "teams": sorted(teams),
    })


@app.post("/admin/podium/set-result")
async def admin_set_podium_result(request: Request, season_id: int = Form(...),
                                   rank1: str = Form(...), rank2: str = Form(...), rank3: str = Form(...)):
    require_admin(request)
    conn = get_db()
    q(conn, """
        INSERT INTO podium_results (season_id, rank1, rank2, rank3)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT(season_id) DO UPDATE SET rank1=EXCLUDED.rank1, rank2=EXCLUDED.rank2, rank3=EXCLUDED.rank3
    """, (season_id, rank1.strip(), rank2.strip(), rank3.strip()))
    conn.commit()
    release_db(conn)
    return RedirectResponse(f"/saison/{season_id}/podium", status_code=303)


# ─── Classement ──────────────────────────────────────────────────────────────

@app.get("/classement", response_class=HTMLResponse)
async def classement_redirect(request: Request):
    user = get_current_user(request)
    if not user: return RedirectResponse("/login", status_code=303)
    season = get_active_season()
    if season: return RedirectResponse(f"/saison/{season['id']}/classement", status_code=303)
    return templates.TemplateResponse("error.html", {"request": request, "message": "Aucune saison active."})

@app.get("/saison/{season_id}/classement", response_class=HTMLResponse)
async def classement(request: Request, season_id: int):
    user = get_current_user(request)
    if not user: return RedirectResponse("/login", status_code=303)
    season = get_season_by_id(season_id)
    if not season:
        return templates.TemplateResponse("error.html", {"request": request, "message": "Saison introuvable."})
    conn = get_db()
    ranked = compute_ranking_for_season(season_id, conn)
    all_matchdays = [r["number"] for r in qall(conn, "SELECT number FROM matchdays WHERE season_id=%s ORDER BY number", (season_id,))]
    release_db(conn)
    active_season = get_active_season()
    return templates.TemplateResponse("classement.html", {
        "request": request, "user": user,
        "season": season, "all_seasons": get_all_seasons(),
        "active_season_id": active_season["id"] if active_season else None,
        "ranking": ranked, "all_matchdays": all_matchdays,
    })


# ─── Admin ────────────────────────────────────────────────────────────────────

@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    admin = require_admin(request)
    active_season = get_active_season()
    conn = get_db()
    matchdays = []
    if active_season:
        matchdays = qall(conn, """
            SELECT md.*, COUNT(m.id) as nb_matches FROM matchdays md
            LEFT JOIN matches m ON m.matchday_id=md.id
            WHERE md.season_id=%s GROUP BY md.id ORDER BY md.number
        """, (active_season["id"],))
        matchdays = [dict(m) for m in matchdays]
    # Tous les joueurs pour reset mdp
    all_users = qall(conn, "SELECT id, username FROM users WHERE is_admin=0 ORDER BY username")
    release_db(conn)
    return templates.TemplateResponse("admin.html", {
        "request": request, "user": admin,
        "season": active_season,
        "all_seasons": get_all_seasons(),
        "matchdays": matchdays,
        "all_users": [dict(u) for u in all_users],
    })

@app.post("/admin/season/set-active")
async def admin_set_active_season(request: Request, season_id: int = Form(...)):
    require_admin(request)
    conn = get_db()
    q(conn, "UPDATE seasons SET is_active=0")
    q(conn, "UPDATE seasons SET is_active=1 WHERE id=%s", (season_id,))
    conn.commit()
    release_db(conn)
    return RedirectResponse("/admin", status_code=303)

@app.post("/admin/season/create")
async def admin_create_season(
    request: Request,
    year_start: int = Form(...),
    competition_name: str = Form(""),
    competition_type: str = Form("league"),
    api_code: str = Form("FL1"),
    nb_journees: int = Form(34),
):
    require_admin(request)
    name = competition_name.strip() if competition_name.strip() else None
    ensure_season_exists(year_start, year_start + 1, name=name,
                         competition_type=competition_type,
                         api_code=api_code,
                         nb_journees=nb_journees)
    return RedirectResponse("/admin", status_code=303)

@app.get("/admin/journee/{number}", response_class=HTMLResponse)
async def admin_journee(request: Request, number: int):
    admin = require_admin(request)
    season = get_active_season()
    conn = get_db()
    matchday = qone(conn, "SELECT * FROM matchdays WHERE season_id=%s AND number=%s", (season["id"], number))
    matches = qall(conn, "SELECT * FROM matches WHERE matchday_id=%s ORDER BY kickoff_time", (matchday["id"],)) if matchday else []
    release_db(conn)
    return templates.TemplateResponse("admin_journee.html", {
        "request": request, "user": admin,
        "season": dict(season), "matchday": dict(matchday) if matchday else None,
        "matches": [dict(m) for m in matches],
    })

@app.post("/admin/matchday/rename")
async def admin_rename_matchday(request: Request, matchday_id: int = Form(...),
                                label: str = Form(...)):
    require_admin(request)
    conn = get_db()
    q(conn, "UPDATE matchdays SET label=%s WHERE id=%s", (label.strip(), matchday_id))
    conn.commit()
    md = qone(conn, "SELECT number FROM matchdays WHERE id=%s", (matchday_id,))
    release_db(conn)
    return RedirectResponse(f"/admin/journee/{md['number']}", status_code=303)


@app.post("/admin/match/add")
async def admin_add_match(request: Request, matchday_id: int = Form(...),
                          home_team: str = Form(...), away_team: str = Form(...),
                          kickoff_date: str = Form(...), kickoff_time: str = Form(...)):
    require_admin(request)
    conn = get_db()
    q(conn, "INSERT INTO matches (matchday_id, home_team, away_team, kickoff_time) VALUES (%s, %s, %s, %s)",
      (matchday_id, home_team, away_team, f"{kickoff_date} {kickoff_time}:00"))
    conn.commit()
    md = qone(conn, "SELECT number FROM matchdays WHERE id=%s", (matchday_id,))
    release_db(conn)
    return RedirectResponse(f"/admin/journee/{md['number']}", status_code=303)

@app.post("/admin/match/update-score")
async def admin_update_score(request: Request, match_id: int = Form(...),
                             home_score: int = Form(...), away_score: int = Form(...)):
    require_admin(request)
    conn = get_db()
    q(conn, "UPDATE matches SET home_score=%s, away_score=%s, status='finished' WHERE id=%s",
      (home_score, away_score, match_id))
    conn.commit()
    md = qone(conn, "SELECT md.number FROM matchdays md JOIN matches m ON m.matchday_id=md.id WHERE m.id=%s", (match_id,))
    release_db(conn)
    return RedirectResponse(f"/admin/journee/{md['number']}", status_code=303)

@app.post("/admin/match/update-kickoff")
async def admin_update_kickoff(request: Request, match_id: int = Form(...),
                               kickoff_date: str = Form(...), kickoff_time: str = Form(...)):
    require_admin(request)
    conn = get_db()
    q(conn, "UPDATE matches SET kickoff_time=%s WHERE id=%s",
      (f"{kickoff_date} {kickoff_time}:00", match_id))
    conn.commit()
    md = qone(conn, "SELECT md.number FROM matchdays md JOIN matches m ON m.matchday_id=md.id WHERE m.id=%s", (match_id,))
    release_db(conn)
    return RedirectResponse(f"/admin/journee/{md['number']}", status_code=303)

@app.post("/admin/match/delete")
async def admin_delete_match(request: Request, match_id: int = Form(...)):
    require_admin(request)
    conn = get_db()
    md = qone(conn, "SELECT md.number FROM matchdays md JOIN matches m ON m.matchday_id=md.id WHERE m.id=%s", (match_id,))
    q(conn, "DELETE FROM pronostics WHERE match_id=%s", (match_id,))
    q(conn, "DELETE FROM matches WHERE id=%s", (match_id,))
    conn.commit()
    release_db(conn)
    return RedirectResponse(f"/admin/journee/{md['number']}", status_code=303)

@app.post("/admin/import-api")
async def admin_import_api(request: Request, matchday_number: int = Form(...),
                           api_year: int = Form(None)):
    require_admin(request)
    season = get_active_season()
    conn = get_db()
    year_to_use = api_year if api_year else season["year_start"]
    matchday = qone(conn, "SELECT * FROM matchdays WHERE season_id=%s AND number=%s", (season["id"], matchday_number))
    if not matchday:
        release_db(conn)
        return JSONResponse({"ok": False, "error": f"Journée {matchday_number} introuvable"})
    api_code = season.get("api_code", "FL1")
    nb, errors = import_matchday_to_db(year_to_use, matchday_number, season["id"], matchday["id"], conn, competition_code=api_code)
    release_db(conn)
    return JSONResponse({"ok": True, "imported": nb, "errors": errors})

@app.post("/admin/import-saison-complete")
async def admin_import_saison_complete(request: Request):
    require_admin(request)
    season = get_active_season()
    conn = get_db()
    year_to_use = season["year_start"]
    api_code = season.get("api_code", "FL1")
    all_fixtures = fetch_fixtures(year_to_use, competition_code=api_code)
    if not all_fixtures:
        release_db(conn)
        return JSONResponse({"ok": False, "error": "Aucun match récupéré depuis l'API."})

    matchdays = qall(conn, "SELECT * FROM matchdays WHERE season_id=%s ORDER BY number", (season["id"],))
    matchdays_by_number = {md["number"]: md for md in matchdays}
    total_imported = 0
    journees_ok = []
    total_errors = []

    for f in all_fixtures:
        jn = f.get("matchday_number")
        if not jn or jn not in matchdays_by_number: continue
        md = matchdays_by_number[jn]
        try:
            existing = qone(conn, "SELECT id FROM matches WHERE external_id=%s", (f["external_id"],))
            if existing:
                q(conn, """UPDATE matches SET home_team=%s, away_team=%s, kickoff_time=%s,
                    home_score=%s, away_score=%s, status=%s WHERE id=%s""",
                  (f["home_team"], f["away_team"], f["kickoff_time"],
                   f["home_score"], f["away_score"], f["status"], existing["id"]))
            else:
                q(conn, """INSERT INTO matches (matchday_id, home_team, away_team, kickoff_time,
                    home_score, away_score, status, external_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                  (md["id"], f["home_team"], f["away_team"], f["kickoff_time"],
                   f["home_score"], f["away_score"], f["status"], f["external_id"]))
                total_imported += 1
                if jn not in journees_ok: journees_ok.append(jn)
        except Exception as e:
            total_errors.append(f"J{jn}: {str(e)}")

    journees_vides = [md["number"] for md in matchdays if md["number"] not in journees_ok]
    conn.commit()
    release_db(conn)
    return JSONResponse({"ok": True, "total_imported": total_imported,
                         "journees_importees": sorted(journees_ok),
                         "journees_vides": sorted(journees_vides),
                         "errors": total_errors[:10]})

@app.post("/admin/update-scores-api")
async def admin_update_scores_api(request: Request):
    require_admin(request)
    season = get_active_season()
    conn = get_db()
    nb = update_live_scores(season["year_start"], conn)
    conn.commit()
    release_db(conn)
    return JSONResponse({"ok": True, "updated": nb})


# ─── Chat ─────────────────────────────────────────────────────────────────────

@app.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request):
    user = get_current_user(request)
    if not user: return RedirectResponse("/login", status_code=303)
    conn = get_db()
    messages = qall(conn, """
        SELECT cm.id, cm.message, cm.created_at, u.username
        FROM chat_messages cm JOIN users u ON u.id=cm.user_id
        ORDER BY cm.created_at DESC LIMIT 100
    """)
    release_db(conn)
    season = get_active_season()
    return templates.TemplateResponse("chat.html", {
        "request": request, "user": user, "season": season,
        "messages": [dict(m) for m in reversed(messages)],
    })

@app.get("/chat/messages")
async def chat_poll(request: Request, after_id: int = 0):
    user = get_current_user(request)
    if not user: return JSONResponse({"ok": False}, status_code=401)
    conn = get_db()
    rows = qall(conn, """
        SELECT cm.id, cm.message, cm.created_at, u.username
        FROM chat_messages cm JOIN users u ON u.id=cm.user_id
        WHERE cm.id > %s ORDER BY cm.created_at ASC LIMIT 50
    """, (after_id,))
    release_db(conn)
    return JSONResponse({"ok": True, "messages": [dict(r) for r in rows]})

@app.post("/chat/send")
async def chat_send(request: Request, message: str = Form(...)):
    user = get_current_user(request)
    if not user: return JSONResponse({"ok": False, "error": "Non connecté"}, status_code=401)
    message = message.strip()
    if not message: return JSONResponse({"ok": False, "error": "Message vide"})
    if len(message) > 500: return JSONResponse({"ok": False, "error": "Message trop long"})
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT INTO chat_messages (user_id, message) VALUES (%s, %s) RETURNING id", (user["id"], message))
    new_id = c.fetchone()["id"]
    conn.commit()
    row = qone(conn, """
        SELECT cm.id, cm.message, cm.created_at, u.username
        FROM chat_messages cm JOIN users u ON u.id=cm.user_id WHERE cm.id=%s
    """, (new_id,))
    release_db(conn)
    return JSONResponse({"ok": True, "message": dict(row)})

@app.post("/chat/delete")
async def chat_delete(request: Request, message_id: int = Form(...)):
    user = get_current_user(request)
    if not user: return JSONResponse({"ok": False}, status_code=401)
    conn = get_db()
    msg = qone(conn, "SELECT user_id FROM chat_messages WHERE id=%s", (message_id,))
    if not msg:
        release_db(conn)
        return JSONResponse({"ok": False, "error": "Message introuvable"})
    if msg["user_id"] != user["id"] and not user.get("is_admin"):
        release_db(conn)
        return JSONResponse({"ok": False, "error": "Non autorisé"}, status_code=403)
    q(conn, "DELETE FROM chat_messages WHERE id=%s", (message_id,))
    conn.commit()
    release_db(conn)
    return JSONResponse({"ok": True})


# ─── GIPHY proxy ──────────────────────────────────────────────────────────────

@app.get("/giphy/trending")
async def giphy_trending(request: Request):
    user = get_current_user(request)
    if not user: return JSONResponse({"data": []}, status_code=401)
    import urllib.request as _ur, json as _j
    api_key = os.environ.get("GIPHY_KEY", "")
    if not api_key: return JSONResponse({"data": [], "error": "GIPHY_KEY non configurée"})
    try:
        with _ur.urlopen(f"https://api.giphy.com/v1/gifs/trending?api_key={api_key}&limit=12&rating=g", timeout=10) as r:
            return JSONResponse(_j.loads(r.read()))
    except Exception as e:
        return JSONResponse({"data": [], "error": str(e)})

@app.get("/giphy/search")
async def giphy_search(request: Request, q_param: str = ""):
    user = get_current_user(request)
    if not user: return JSONResponse({"data": []}, status_code=401)
    import urllib.request as _ur, json as _j, urllib.parse as _up
    api_key = os.environ.get("GIPHY_KEY", "")
    if not api_key: return JSONResponse({"data": [], "error": "GIPHY_KEY non configurée"})
    try:
        url = f"https://api.giphy.com/v1/gifs/search?api_key={api_key}&q={_up.quote(q_param)}&limit=12&rating=g&lang=fr"
        with _ur.urlopen(url, timeout=10) as r:
            return JSONResponse(_j.loads(r.read()))
    except Exception as e:
        return JSONResponse({"data": [], "error": str(e)})


# ─── Hall of Fame ────────────────────────────────────────────────────────────

HOF_SEED = [
    # (type, num, saison, joueur, points)
    ("champions", 1,  "2010-2011", "Ricardo",     412),
    ("champions", 2,  "2011-2012", "Ricardo",     578),
    ("champions", 3,  "2012-2013", "Seb",         562),
    ("champions", 4,  "2013-2014", "Coach",       582),
    ("champions", 5,  "2014-2015", "Seb",         632),
    ("champions", 6,  "2015-2016", "Mathieu",     554),
    ("champions", 7,  "2016-2017", "Mathieu",     645),
    ("champions", 8,  "2017-2018", "Mathieu",     587),
    ("champions", 9,  "2018-2019", "Ricardo",     529),
    ("champions", 10, "2019-2020", "Mathieu",     447),
    ("champions", 11, "2020-2021", "Ricardo",     546),
    ("champions", 12, "2021-2022", "Ricardo",     595),
    ("champions", 13, "2022-2023", "Seb",         602),
    ("champions", 14, "2023-2024", "Ben",         450),
    ("champions", 15, "2024-2025", "Seb",         None),
    ("champions", 16, "2025-2026", "Seb",         None),
    ("cuilleres", 1,  "2010-2011", "Seb",         388),
    ("cuilleres", 2,  "2011-2012", "Dreux",       473),
    ("cuilleres", 3,  "2012-2013", "Dreux",       498),
    ("cuilleres", 4,  "2013-2014", "Dreux",       492),
    ("cuilleres", 5,  "2014-2015", "Dreux",       492),
    ("cuilleres", 6,  "2015-2016", "Coach",       478),
    ("cuilleres", 7,  "2016-2017", "Dreux",       458),
    ("cuilleres", 8,  "2017-2018", "Le Doubs",    478),
    ("cuilleres", 9,  "2018-2019", "Greg",        469),
    ("cuilleres", 10, "2019-2020", "Le Doubs",    329),
    ("cuilleres", 11, "2020-2021", "Le Doubs",    479),
    ("cuilleres", 12, "2021-2022", "Dreux",       483),
    ("cuilleres", 13, "2022-2023", "Le Doubs",    515),
    ("cuilleres", 14, "2023-2024", "Le Doubs",    341),
    ("cuilleres", 15, "2024-2025", "Le Doubs",    None),
    ("cuilleres", 16, "2025-2026", "Coach",       None),
]

def seed_hall_of_fame():
    """Insère les données historiques si la table est vide."""
    conn = get_db()
    count = qone(conn, "SELECT COUNT(*) as cnt FROM hall_of_fame")
    if count and count["cnt"] > 0:
        release_db(conn)
        return
    for t, num, saison, joueur, points in HOF_SEED:
        q(conn, "INSERT INTO hall_of_fame (type, num, saison, joueur, points) VALUES (%s,%s,%s,%s,%s)",
          (t, num, saison, joueur, points))
    conn.commit()
    release_db(conn)
    print("Hall of Fame initialisé.")

@app.get("/hall-of-fame", response_class=HTMLResponse)
async def hall_of_fame(request: Request):
    user = get_current_user(request)
    if not user: return RedirectResponse("/login", status_code=303)
    season = get_active_season()
    conn = get_db()

    champions = [dict(r) for r in qall(conn,
        "SELECT * FROM hall_of_fame WHERE type='champions' ORDER BY num")]
    cuilleres = [dict(r) for r in qall(conn,
        "SELECT * FROM hall_of_fame WHERE type='cuilleres' ORDER BY num")]
    release_db(conn)

    from collections import Counter
    champ_count = dict(sorted(Counter(c["joueur"] for c in champions).items(), key=lambda x: -x[1]))
    cuil_count  = dict(sorted(Counter(c["joueur"] for c in cuilleres).items(),  key=lambda x: -x[1]))

    return templates.TemplateResponse("hall_of_fame.html", {
        "request": request, "user": user, "season": season,
        "champions": list(reversed(champions)),
        "cuilleres": list(reversed(cuilleres)),
        "champ_count": champ_count,
        "cuil_count": cuil_count,
    })

@app.post("/admin/hall-of-fame/add")
async def admin_add_hof(
    request: Request,
    type: str = Form(...),
    saison: str = Form(...),
    joueur: str = Form(...),
    points: str = Form(""),
):
    require_admin(request)
    conn = get_db()
    count = qone(conn, "SELECT COUNT(*) as cnt FROM hall_of_fame WHERE type=%s", (type,))
    num = (count["cnt"] or 0) + 1
    q(conn, "INSERT INTO hall_of_fame (type, num, saison, joueur, points) VALUES (%s,%s,%s,%s,%s)",
      (type, num, saison, joueur, int(points) if points.strip() else None))
    conn.commit()
    release_db(conn)
    return RedirectResponse("/hall-of-fame", status_code=303)


# ─── Import ODS ──────────────────────────────────────────────────────────────

@app.post("/admin/import-ods")
async def admin_import_ods(request: Request):
    require_admin(request)
    try:
        import import_ods
        import importlib
        importlib.reload(import_ods)

        conn = get_db()
        season = qone(conn, "SELECT * FROM seasons WHERE is_active=1")
        users = {u['username']: u['id'] for u in qall(conn, "SELECT id, username FROM users WHERE is_admin=0")}
        release_db(conn)

        total_pronos = 0
        total_estimations = 0
        errors = []

        for jn_str, data in import_ods.ODS_DATA.items():
            jn = int(jn_str)
            conn2 = get_db()
            matchday = qone(conn2, "SELECT * FROM matchdays WHERE season_id=%s AND number=%s",
                           (season['id'], jn))
            if not matchday:
                release_db(conn2)
                errors.append(f"J{jn}: journée introuvable")
                continue

            matches = qall(conn2, "SELECT * FROM matches WHERE matchday_id=%s ORDER BY kickoff_time",
                          (matchday['id'],))
            if not matches:
                release_db(conn2)
                errors.append(f"J{jn}: aucun match")
                continue

            # Index des matchs par paire d'équipes normalisée
            def norm(s):
                return str(s).strip().lower() if s else ''
            match_index = {}
            for m in matches:
                key = (norm(m['home_team']), norm(m['away_team']))
                match_index[key] = m

            for player, pronos in data['pronostics'].items():
                user_id = users.get(player)
                if not user_id:
                    continue
                for prono in pronos:
                    if prono is None:
                        continue
                    key = (norm(prono.get('home_team','')), norm(prono.get('away_team','')))
                    match = match_index.get(key)
                    if not match:
                        errors.append(f"J{jn} {player}: match {prono.get('home_team')} vs {prono.get('away_team')} introuvable")
                        continue
                    try:
                        q(conn2, """
                            INSERT INTO pronostics (user_id, match_id, home_score, away_score)
                            VALUES (%s, %s, %s, %s)
                            ON CONFLICT(user_id, match_id) DO UPDATE SET
                                home_score=EXCLUDED.home_score, away_score=EXCLUDED.away_score
                        """, (user_id, match['id'], prono['home_score'], prono['away_score']))
                        total_pronos += 1
                    except Exception as e:
                        errors.append(f"J{jn} {player}: {str(e)[:50]}")

            for player, estimation in data['estimations'].items():
                if estimation is None:
                    continue
                user_id = users.get(player)
                if not user_id:
                    continue
                try:
                    q(conn2, """
                        INSERT INTO score_estimates (user_id, matchday_id, estimated_score)
                        VALUES (%s, %s, %s)
                        ON CONFLICT(user_id, matchday_id) DO UPDATE SET
                            estimated_score=EXCLUDED.estimated_score
                    """, (user_id, matchday['id'], estimation))
                    total_estimations += 1
                except Exception as e:
                    errors.append(f"J{jn} {player} est: {str(e)[:50]}")

            conn2.commit()
            release_db(conn2)

        return JSONResponse({
            "ok": True,
            "pronos": total_pronos,
            "estimations": total_estimations,
            "errors": errors[:10]
        })
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


# ─── Debug ────────────────────────────────────────────────────────────────────

@app.get("/admin/debug", response_class=HTMLResponse)
async def admin_debug(request: Request):
    require_admin(request)
    conn = get_db()
    matches = qall(conn, """
        SELECT m.id, m.home_team, m.away_team, m.kickoff_time,
               m.home_score, m.away_score, m.status, md.number as journee
        FROM matches m JOIN matchdays md ON md.id=m.matchday_id
        ORDER BY md.number, m.kickoff_time
    """)
    pronostics = qall(conn, """
        SELECT u.username, m.home_team, m.away_team,
               p.home_score as pred_h, p.away_score as pred_a,
               m.home_score as real_h, m.away_score as real_a, m.kickoff_time
        FROM pronostics p JOIN users u ON u.id=p.user_id JOIN matches m ON m.id=p.match_id
        ORDER BY u.username, m.kickoff_time
    """)
    release_db(conn)
    rows_m = "".join(
        f"<tr><td>J{m['journee']}</td><td>{m['home_team']} – {m['away_team']}</td>"
        f"<td>{m['kickoff_time']}</td>"
        f"<td style='color:{'#2ea043' if m['home_score'] is not None else '#e74c3c'}'>"
        f"{'%d–%d' % (m['home_score'], m['away_score']) if m['home_score'] is not None else 'PAS DE SCORE'}</td>"
        f"<td>{m['status']}</td></tr>" for m in matches)
    rows_p = "".join(
        f"<tr><td>{p['username']}</td><td>{p['home_team']} – {p['away_team']}</td>"
        f"<td>{p['pred_h']}–{p['pred_a']}</td>"
        f"<td>{'%d–%d' % (p['real_h'], p['real_a']) if p['real_h'] is not None else '—'}</td>"
        f"<td>{p['kickoff_time']}</td></tr>" for p in pronostics)
    html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8">
    <style>body{{font-family:sans-serif;background:#0d1117;color:#e6edf3;padding:2rem}}
    table{{border-collapse:collapse;width:100%;margin-bottom:2rem;font-size:.85rem}}
    th{{background:#21262d;padding:.5rem .75rem;text-align:left;color:#8b949e}}
    td{{padding:.45rem .75rem;border-bottom:1px solid #30363d}}
    h2{{color:#e8c45a;margin:1.5rem 0 .75rem}}</style></head><body>
    <h2>Matchs ({len(matches)})</h2>
    <table><thead><tr><th>J</th><th>Match</th><th>Kickoff UTC</th><th>Score</th><th>Statut</th></tr></thead>
    <tbody>{rows_m}</tbody></table>
    <h2>Pronostics ({len(pronostics)})</h2>
    <table><thead><tr><th>Joueur</th><th>Match</th><th>Prono</th><th>Score réel</th><th>Kickoff UTC</th></tr></thead>
    <tbody>{rows_p}</tbody></table>
    <p style="color:#8b949e;font-size:.8rem">UTC : {utcnow_str()}</p>
    </body></html>"""
    return HTMLResponse(html)

@app.get("/admin/fix-team-names")
async def admin_fix_team_names(request: Request):
    require_admin(request)
    conn = get_db()
    fixes = [
        ("Bonsia H.", "Bosnie H."),
        ("Bosnia H.", "Bosnie H."),
        ("Jordan", "Jordanie"),
        ("Congo RD", "RD Congo"),
    ]
    total = 0
    for old_name, new_name in fixes:
        r = q(conn, "UPDATE matches SET home_team=%s WHERE home_team=%s", (new_name, old_name))
        total += r.rowcount
        r = q(conn, "UPDATE matches SET away_team=%s WHERE away_team=%s", (new_name, old_name))
        total += r.rowcount
    conn.commit()
    release_db(conn)
    return JSONResponse({"ok": True, "updated": total, "fixes": [f[1] for f in fixes]})


@app.get("/admin/test-api")
async def admin_test_api(request: Request):
    require_admin(request)
    import urllib.request, json as _json
    api_key = os.environ.get("FOOTBALL_DATA_KEY", "")
    results = {"cle_presente": bool(api_key), "cle_debut": api_key[:8] + "..." if api_key else "ABSENTE"}
    try:
        urllib.request.urlopen("https://www.google.com", timeout=5)
        results["reseau_general"] = "OK"
    except Exception as e:
        results["reseau_general"] = f"ECHEC: {str(e)}"
    try:
        req = urllib.request.Request("https://api.football-data.org/v4/competitions/FL1")
        req.add_header("X-Auth-Token", api_key)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = _json.loads(resp.read())
            results["api_football_data"] = "OK"
            results["competition"] = data.get("name", "?")
    except Exception as e:
        results["api_football_data"] = f"ECHEC: {str(e)}"
    return JSONResponse(results)


# ─── Démarrage ────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    import time
    print("Démarrage de l'application...")
    for attempt in range(5):
        try:
            init_db()
            seed_users()
            seed_active_season()
            print("Application démarrée.")
            return
        except Exception as e:
            print(f"Tentative {attempt+1}/5 échouée: {e}")
            time.sleep(3)
    print("ATTENTION: Base de données non disponible, démarrage sans DB.")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
