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
@app.get("/sw.js", include_in_schema=False)
async def service_worker():
    from fastapi.responses import Response
    sw = "self.addEventListener('push',e=>{let d={title:'ENTE Pronos',body:'Notification'};try{d=JSON.parse(e.data.text())}catch(x){}e.waitUntil(self.registration.showNotification(d.title,{body:d.body,vibrate:[200,100,200],tag:'pronos'}))});self.addEventListener('notificationclick',e=>{e.notification.close();e.waitUntil(clients.openWindow('/'))});"
    return Response(content=sw, media_type="application/javascript", headers={"Service-Worker-Allowed": "/"})

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
    podium_result = qone(conn, "SELECT * FROM podium_results WHERE season_id=%s", (season_id,))

    # Une seule requête pour tous les pronostics
    from collections import defaultdict
    all_rows = qall(conn, """
        SELECT p.user_id,
               p.home_score as pred_home, p.away_score as pred_away,
               m.home_score as real_home, m.away_score as real_away,
               m.matchday_id
        FROM pronostics p
        JOIN matches m ON m.id=p.match_id
        JOIN matchdays md ON md.id=m.matchday_id
        WHERE md.season_id=%s AND m.home_score IS NOT NULL
    """, (season_id,))
    rows_by_user = defaultdict(list)
    for r in all_rows:
        rows_by_user[r["user_id"]].append(dict(r))

    # Une seule requête pour toutes les estimations
    all_ests = qall(conn, """
        SELECT se.user_id, se.matchday_id, se.estimated_score
        FROM score_estimates se
        JOIN matchdays md ON md.id=se.matchday_id
        WHERE md.season_id=%s
    """, (season_id,))
    ests_by_user_md = {}
    for e in all_ests:
        ests_by_user_md[(e["user_id"], e["matchday_id"])] = e["estimated_score"]

    # Journées complètes
    md_completion = {}
    for md in matchdays:
        r = qone(conn, """
            SELECT COUNT(*) FILTER (WHERE home_score IS NOT NULL) as fin,
                   COUNT(*) as tot FROM matches WHERE matchday_id=%s
        """, (md["id"],))
        md_completion[md["id"]] = r and r["tot"] > 0 and r["fin"] == r["tot"]

    # Podiums
    all_podiums = {}
    if podium_result:
        pods = qall(conn, "SELECT * FROM podium_pronostics WHERE season_id=%s", (season_id,))
        for p in pods:
            all_podiums[p["user_id"]] = p

    players_data = []
    for u in users:
        rows = rows_by_user.get(u["id"], [])
        stats = compute_matchday_stats(rows)
        # Points podium
        podium_pts = 0
        if podium_result and u["id"] in all_podiums:
            my_podium = all_podiums[u["id"]]
            pp = compute_podium_points(
                podium_result["rank1"], podium_result["rank2"], podium_result["rank3"],
                my_podium["rank1"], my_podium["rank2"], my_podium["rank3"]
            )
            podium_pts = pp["points"]
        estimates_ok = 0
        # Utiliser les données déjà chargées
        for md in matchdays:
            est_val = ests_by_user_md.get((u["id"], md["id"]))
            if est_val and md_completion.get(md["id"]):
                md_rows = [r for r in rows if r["matchday_id"] == md["id"]]
                md_stats = compute_matchday_stats(md_rows)
                if compute_estimate_points(md_stats["points"], est_val) == 2:
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
    conn = get_db()
    notif_prefs = qone(conn, "SELECT * FROM user_notifications WHERE user_id=%s", (user["id"],))
    release_db(conn)
    return templates.TemplateResponse("profil.html", {
        "request": request, "user": user, "season": season,
        "error": None, "success": None,
        "notif_prefs": dict(notif_prefs) if notif_prefs else None,
    })

@app.post("/profil/change-theme")
async def change_theme(request: Request, theme: str = Form(...)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    valid_themes = ["ligue1", "nuit-bleue", "rouge-passion", "neon", "minimaliste", "glassmorphism", "terrain", "ardoise", "brume", "l1classic", "pasunappli", "applidemerde", "alternative"]
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
    msg_list = [dict(r) for r in rows]
    # Charger les réactions
    if msg_list:
        msg_ids = [m["id"] for m in msg_list]
        ph = ",".join(["%s"] * len(msg_ids))
        reactions = qall(conn, f"""
            SELECT cr.message_id, cr.emoji, COUNT(*) as count,
                   bool_or(cr.user_id=%s) as mine,
                   string_agg(u.username, ', ' ORDER BY u.username) as users
            FROM chat_reactions cr JOIN users u ON u.id=cr.user_id
            WHERE cr.message_id IN ({ph})
            GROUP BY cr.message_id, cr.emoji ORDER BY count DESC
        """, [user["id"]] + msg_ids)
        react_map = {}
        for r in reactions:
            mid = r["message_id"]
            if mid not in react_map:
                react_map[mid] = []
            react_map[mid].append({"emoji": r["emoji"], "count": int(r["count"]), "mine": bool(r["mine"])})
        for m in msg_list:
            m["reactions"] = react_map.get(m["id"], [])
    release_db(conn)
    return JSONResponse({"ok": True, "messages": msg_list})

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


# ─── Stats ───────────────────────────────────────────────────────────────────

@app.get("/saison/{season_id}/stats", response_class=HTMLResponse)
async def stats_page(request: Request, season_id: int):
    user = get_current_user(request)
    if not user: return RedirectResponse("/login", status_code=303)

    season = get_season_by_id(season_id)
    if not season or season.get("competition_type") != "league":
        return RedirectResponse("/", status_code=303)

    conn = get_db()
    users_list = qall(conn, "SELECT id, username FROM users WHERE is_admin=0 ORDER BY username")
    matchdays = qall(conn, "SELECT id, number, label FROM matchdays WHERE season_id=%s ORDER BY number", (season_id,))

    # ── Graphique : points cumulés — UNE seule requête massive ──
    # Récupérer TOUS les pronostics de la saison en une requête
    all_pronos = qall(conn, """
        SELECT p.user_id, m.matchday_id,
               p.home_score as pred_home, p.away_score as pred_away,
               m.home_score as real_home, m.away_score as real_away
        FROM pronostics p
        JOIN matches m ON m.id=p.match_id
        JOIN matchdays md ON md.id=m.matchday_id
        WHERE md.season_id=%s AND m.home_score IS NOT NULL
    """, (season_id,))

    # Récupérer TOUTES les estimations de la saison
    all_ests = qall(conn, """
        SELECT se.user_id, se.matchday_id, se.estimated_score
        FROM score_estimates se
        JOIN matchdays md ON md.id=se.matchday_id
        WHERE md.season_id=%s
    """, (season_id,))

    # Récupérer journées complètes
    md_completion = {}
    for md in matchdays:
        r = qone(conn, """
            SELECT COUNT(*) FILTER (WHERE home_score IS NOT NULL) as fin,
                   COUNT(*) as tot FROM matches WHERE matchday_id=%s
        """, (md["id"],))
        md_completion[md["id"]] = r and r["tot"] > 0 and r["fin"] == r["tot"]

    # Indexer pronostics par (user_id, matchday_id)
    from collections import defaultdict
    pronos_by_user_md = defaultdict(list)
    for p in all_pronos:
        pronos_by_user_md[(p["user_id"], p["matchday_id"])].append(dict(p))

    ests_by_user_md = {}
    for e in all_ests:
        ests_by_user_md[(e["user_id"], e["matchday_id"])] = e["estimated_score"]

    cumul_data = {}
    for u in users_list:
        cumul = []
        total = 0
        for md in matchdays:
            rows = pronos_by_user_md.get((u["id"], md["id"]), [])
            stats = compute_matchday_stats(rows)
            est_val = ests_by_user_md.get((u["id"], md["id"]))
            est_pts = 0
            if est_val and md_completion.get(md["id"]):
                est_pts = compute_estimate_points(stats["points"], est_val)
            total += stats["points"] + est_pts
            cumul.append(total)
        cumul_data[u["username"]] = cumul

    journee_labels = [md["label"] or f"J{md['number']}" for md in matchdays]
    # Filtrer journées sans données (tous à 0)
    last_active = 0
    for i in range(len(matchdays) - 1, -1, -1):
        if any(cumul_data[u["username"]][i] > 0 for u in users_list):
            last_active = i + 1
            break

    journee_labels = journee_labels[:last_active]
    for uname in cumul_data:
        cumul_data[uname] = cumul_data[uname][:last_active]

    # ── Némésis — une seule requête pour tous les joueurs ───────
    all_pronos_full = qall(conn, """
        SELECT p.user_id, m.home_team, m.away_team,
               p.home_score as pred_home, p.away_score as pred_away,
               m.home_score as real_home, m.away_score as real_away
        FROM pronostics p JOIN matches m ON m.id=p.match_id
        JOIN matchdays md ON md.id=m.matchday_id
        WHERE md.season_id=%s AND m.home_score IS NOT NULL
    """, (season_id,))

    nemesis_data = {u["username"]: {"team": "—", "count": 0} for u in users_list}
    user_team_zeros = defaultdict(lambda: defaultdict(int))
    user_id_to_name = {u["id"]: u["username"] for u in users_list}

    for r in all_pronos_full:
        pts = compute_points(r["real_home"], r["real_away"], r["pred_home"], r["pred_away"])
        if pts["points"] == 0:
            uname = user_id_to_name.get(r["user_id"])
            if uname:
                for team in [r["home_team"], r["away_team"]]:
                    user_team_zeros[uname][team] += 1

    for uname, team_zeros in user_team_zeros.items():
        if team_zeros:
            worst = max(team_zeros, key=lambda t: team_zeros[t])
            nemesis_data[uname] = {"team": worst, "count": team_zeros[worst]}

    # ── Stats insolites ──────────────────────────────────────────
    # 1. Dernière victoire de journée (classé 1er seul)
    # Récupérer toutes les saisons L1 pour chercher en arrière si besoin
    all_league_seasons = qall(conn, """
        SELECT id, year_start FROM seasons
        WHERE competition_type='league' ORDER BY year_start DESC
    """)

    def find_last_win(user_id, seasons_to_check):
        """Retourne (saison_name, journee_num, nb_journees_depuis) ou None"""
        journees_since = 0
        for s in seasons_to_check:
            mds = qall(conn, "SELECT id, number FROM matchdays WHERE season_id=%s ORDER BY number DESC", (s["id"],))
            for md in mds:
                # Vérifier si journée terminée
                fin = qone(conn, "SELECT COUNT(*) as cnt FROM matches WHERE matchday_id=%s AND home_score IS NOT NULL", (md["id"],))
                total = qone(conn, "SELECT COUNT(*) as cnt FROM matches WHERE matchday_id=%s", (md["id"],))
                if not fin or not total or fin["cnt"] == 0 or fin["cnt"] < total["cnt"]:
                    continue
                journees_since += 1
                rows = qall(conn, """
                    SELECT p.home_score as pred_home, p.away_score as pred_away,
                           m.home_score as real_home, m.away_score as real_away
                    FROM pronostics p JOIN matches m ON m.id=p.match_id
                    WHERE p.user_id=%s AND m.matchday_id=%s AND m.home_score IS NOT NULL
                """, (user_id, md["id"]))
                if not rows: continue
                stats = compute_matchday_stats([dict(r) for r in rows])
                all_pts = []
                for u2 in users_list:
                    r2 = qall(conn, """
                        SELECT p.home_score as pred_home, p.away_score as pred_away,
                               m.home_score as real_home, m.away_score as real_away
                        FROM pronostics p JOIN matches m ON m.id=p.match_id
                        WHERE p.user_id=%s AND m.matchday_id=%s AND m.home_score IS NOT NULL
                    """, (u2["id"], md["id"]))
                    s2 = compute_matchday_stats([dict(r) for r in r2])
                    all_pts.append(s2["points"])
                if stats["points"] > 0 and stats["points"] == max(all_pts) and all_pts.count(max(all_pts)) == 1:
                    return {"journee": md["number"], "since": journees_since - 1, "season_year": s["year_start"]}
        return None

    last_win = {}
    for u in users_list:
        result = find_last_win(u["id"], all_league_seasons)
        last_win[u["username"]] = result

    # Calculer nb journées terminées dans saison actuelle
    finished_mds_count = 0
    for md in matchdays:
        r = qone(conn, """
            SELECT COUNT(*) FILTER (WHERE home_score IS NOT NULL) as fin,
                   COUNT(*) as tot
            FROM matches WHERE matchday_id=%s
        """, (md["id"],))
        if r and r["tot"] > 0 and r["fin"] == r["tot"]:
            finished_mds_count += 1

    # 2. La flipette : plus de pronostics identiques à la majorité
    # 3. Tout au pif : moins de pronostics identiques à la majorité
    flipette_scores = {u["username"]: 0 for u in users_list}
    pif_scores = {u["username"]: 0 for u in users_list}
    total_pronos = {u["username"]: 0 for u in users_list}

    # Une seule requête pour tous les pronostics
    all_match_pronos = qall(conn, """
        SELECT p.user_id, p.match_id, p.home_score, p.away_score, u.username
        FROM pronostics p
        JOIN users u ON u.id=p.user_id
        JOIN matches m ON m.id=p.match_id
        JOIN matchdays md ON md.id=m.matchday_id
        WHERE md.season_id=%s AND m.home_score IS NOT NULL
        ORDER BY p.match_id
    """, (season_id,))

    from collections import Counter
    # Grouper par match_id
    by_match = defaultdict(list)
    for p in all_match_pronos:
        by_match[p["match_id"]].append(p)

    for mid, match_pronos in by_match.items():
        if len(match_pronos) < 2: continue
        outcomes = []
        for mp in match_pronos:
            if mp["home_score"] > mp["away_score"]: o = "H"
            elif mp["home_score"] < mp["away_score"]: o = "A"
            else: o = "D"
            outcomes.append((mp["username"], o))
        outcome_counts = Counter(o for _, o in outcomes)
        majority_outcome = outcome_counts.most_common(1)[0][0]
        for uname, o in outcomes:
            total_pronos[uname] = total_pronos.get(uname, 0) + 1
            if o == majority_outcome:
                flipette_scores[uname] = flipette_scores.get(uname, 0) + 1

    # Calculer le % de conformité
    conformity = {}
    for uname in flipette_scores:
        if total_pronos.get(uname, 0) > 0:
            conformity[uname] = round(100 * flipette_scores[uname] / total_pronos[uname], 1)
        else:
            conformity[uname] = 0

    flipette = max(conformity, key=lambda u: conformity[u]) if conformity else "—"
    pif = min(conformity, key=lambda u: conformity[u]) if conformity else "—"

    # 4. Le boulard : moyenne d'estimation la plus haute
    # 5. Le Français : moyenne la plus basse
    est_avgs = {}
    for u in users_list:
        ests = qall(conn, """
            SELECT se.estimated_score FROM score_estimates se
            JOIN matchdays md ON md.id=se.matchday_id
            WHERE se.user_id=%s AND md.season_id=%s
        """, (u["id"], season_id))
        if ests:
            est_avgs[u["username"]] = round(sum(e["estimated_score"] for e in ests) / len(ests), 1)
        else:
            est_avgs[u["username"]] = 0

    boulard = max(est_avgs, key=lambda u: est_avgs[u]) if est_avgs else "—"
    francais = min((u for u in est_avgs if est_avgs[u] > 0), key=lambda u: est_avgs[u]) if est_avgs else "—"

    active_season = get_active_season()
    all_seasons = get_all_seasons()
    release_db(conn)

    # Calcul écart au leader
    ecart_data = {}
    for uname in cumul_data:
        ecart_data[uname] = []
    for i in range(len(journee_labels)):
        leader_pts = max(cumul_data[uname][i] for uname in cumul_data) if cumul_data else 0
        for uname in cumul_data:
            ecart_data[uname].append(cumul_data[uname][i] - leader_pts)

    return templates.TemplateResponse("stats.html", {
        "request": request, "user": user,
        "season": season, "all_seasons": all_seasons,
        "active_season_id": active_season["id"] if active_season else None,
        "journee_labels": journee_labels,
        "cumul_data": cumul_data,
        "ecart_data": ecart_data,
        "nemesis_data": nemesis_data,
        "last_win": last_win,
        "finished_mds_count": finished_mds_count,
        "conformity": conformity,
        "flipette": flipette, "pif": pif,
        "est_avgs": est_avgs,
        "boulard": boulard, "francais": francais,
        "last_active": last_active,
    })


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

# ─── Notifications (Email + Telegram) ───────────────────────────────────────

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8689221193:AAH_LpOUTWW8JCj1IzgGx-1wcnLGZkmzpws")
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

def send_telegram(chat_id: str, text: str) -> bool:
    """Envoie un message Telegram."""
    try:
        import urllib.request, urllib.parse, json
        data = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "HTML"}).encode()
        req = urllib.request.Request(f"{TELEGRAM_API}/sendMessage",
                                      data=data,
                                      headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        print(f"Telegram error: {e}")
        return False

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
RESEND_FROM = os.environ.get("RESEND_FROM", "onboarding@resend.dev")

def send_email_resend(to_email: str, subject: str, body: str):
    """Envoie un email via l'API HTTPS de Resend (le SMTP sortant est bloqué sur Railway hors plan Pro).
    Retourne (ok: bool, detail: str)."""
    if not RESEND_API_KEY or not to_email:
        return False, "RESEND_API_KEY non défini ou destinataire manquant."
    try:
        import urllib.request, urllib.error, json
        payload = json.dumps({
            "from": RESEND_FROM,
            "to": [to_email],
            "subject": subject,
            "text": body,
        }).encode()
        req = urllib.request.Request(
            "https://api.resend.com/emails",
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
        return True, "ok"
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        print(f"Resend HTTP error {e.code}: {detail}")
        return False, f"HTTP {e.code}: {detail[:300]}"
    except Exception as e:
        print(f"Resend error: {e}")
        return False, str(e)

def send_reminder_email(to_email: str, subject: str, body: str) -> bool:
    """Envoie un email de rappel (via Resend)."""
    ok, _ = send_email_resend(to_email, subject, body)
    return ok

@app.get("/admin/test-email")
async def test_email(request: Request, to: str = ""):
    """Teste l'envoi d'un email de rappel via Resend, avec détail de l'erreur le cas échéant."""
    require_admin(request)
    if not to:
        return JSONResponse({"ok": False, "error": "Paramètre 'to' manquant. Utilisez /admin/test-email?to=adresse@exemple.com"})
    if not RESEND_API_KEY:
        return JSONResponse({"ok": False, "error": "RESEND_API_KEY n'est pas défini dans les variables d'environnement."})
    ok, detail = send_email_resend(to, "Test email - Ligue 1 Pronostics",
        "Ceci est un email de test envoyé depuis /admin/test-email. Si vous le recevez, la config Resend fonctionne.")
    return JSONResponse({"ok": ok, "to": to, "resend_from": RESEND_FROM, "detail": detail})

@app.get("/telegram/webhook-info")
async def telegram_info(request: Request):
    """Vérifie le statut du bot Telegram."""
    require_admin(request)
    import urllib.request, json
    try:
        with urllib.request.urlopen(f"{TELEGRAM_API}/getMe", timeout=10) as resp:
            data = json.loads(resp.read())
        return JSONResponse(data)
    except Exception as e:
        return JSONResponse({"error": str(e)})

@app.get("/telegram/updates")
async def telegram_updates(request: Request):
    """Récupère les nouveaux messages du bot pour lier les comptes."""
    require_admin(request)
    import urllib.request, json
    try:
        with urllib.request.urlopen(f"{TELEGRAM_API}/getUpdates?limit=20", timeout=10) as resp:
            data = json.loads(resp.read())
        return JSONResponse(data)
    except Exception as e:
        return JSONResponse({"error": str(e)})

@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    """Webhook Telegram — répond au /start avec le chat_id."""
    import json, urllib.request
    try:
        body = await request.json()
        message = body.get("message", {})
        text = message.get("text", "")
        chat = message.get("chat", {})
        chat_id = chat.get("id")
        if not chat_id:
            return JSONResponse({"ok": True})
        if text.strip() == "/start":
            reply = (
                f"👋 Bienvenue sur ENTE Pronos !\n\n"
                f"Votre Chat ID est :\n"
                f"<code>{chat_id}</code>\n\n"
                f"Copiez ce numéro et collez-le dans votre profil sur l'appli "
                f"(section Notifications) pour activer les rappels."
            )
            data = json.dumps({
                "chat_id": chat_id,
                "text": reply,
                "parse_mode": "HTML"
            }).encode()
            req = urllib.request.Request(
                f"{TELEGRAM_API}/sendMessage",
                data=data,
                headers={"Content-Type": "application/json"}
            )
            urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"Webhook error: {e}")
    return JSONResponse({"ok": True})

@app.get("/admin/users-notif", response_class=HTMLResponse)
async def admin_users_notif(request: Request):
    """Affiche la config notifications de tous les joueurs."""
    require_admin(request)
    conn = get_db()
    rows = qall(conn, """
        SELECT u.username, u.id,
               un.telegram_chat_id, un.email,
               un.notify_24h, un.notify_2h
        FROM users u
        LEFT JOIN user_notifications un ON un.user_id=u.id
        WHERE u.is_admin=0
        ORDER BY u.username
    """)
    release_db(conn)
    season = get_active_season()
    user = get_current_user(request)
    html = """
    <style>
      table { width:100%; border-collapse:collapse; font-size:.88rem; }
      th { background:var(--surface2); padding:.6rem .8rem; text-align:left; color:var(--muted); font-size:.75rem; text-transform:uppercase; border-bottom:2px solid var(--border); }
      td { padding:.6rem .8rem; border-bottom:1px solid var(--border); }
      .ok { color:var(--green); } .ko { color:#e74c3c; } .na { color:var(--muted); }
    </style>
    <div class="container">
      <h1>🔔 Config Notifications</h1>
      <div class="card" style="padding:0;overflow:hidden;">
        <table>
          <thead><tr>
            <th>Joueur</th>
            <th>Telegram Chat ID</th>
            <th>Email</th>
            <th>24h</th>
            <th>2h</th>
          </tr></thead>
          <tbody>
    """
    for r in rows:
        tg = f'<span class="ok">✓ {r["telegram_chat_id"]}</span>' if r["telegram_chat_id"] else '<span class="ko">✗ Non configuré</span>'
        em = f'<span class="ok">✓ {r["email"]}</span>' if r["email"] else '<span class="na">—</span>'
        n24 = '<span class="ok">✓</span>' if r["notify_24h"] else '<span class="ko">✗</span>'
        n2  = '<span class="ok">✓</span>' if r["notify_2h"] else '<span class="ko">✗</span>'
        html += f"<tr><td><strong>{r['username']}</strong></td><td>{tg}</td><td>{em}</td><td>{n24}</td><td>{n2}</td></tr>"
    html += "</tbody></table></div></div>"
    from fastapi.responses import HTMLResponse as HR
    return HR(f"""<!DOCTYPE html><html><head><meta charset="UTF-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <link rel="stylesheet" href="/static/css/style.css">
    <title>Config Notifications</title></head>
    <body data-theme="{user['theme'] if user and user.get('theme') else 'ligue1'}">
    <nav><a class="nav-brand" href="/">🏆 PRONOS ENTE</a>
    <div class="nav-links"><a href="/admin">⚙ Admin</a></div></nav>
    {html}</body></html>""")


@app.get("/admin/telegram-set-webhook")
async def telegram_set_webhook(request: Request):
    """Configure le webhook Telegram vers ce serveur."""
    require_admin(request)
    import urllib.request, json
    webhook_url = str(request.base_url) + "telegram/webhook"
    data = json.dumps({"url": webhook_url}).encode()
    req = urllib.request.Request(
        f"{TELEGRAM_API}/setWebhook",
        data=data,
        headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
        return JSONResponse({"ok": True, "webhook_url": webhook_url, "result": result})
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        return JSONResponse({"error": f"HTTP {e.code}", "detail": body, "webhook_url": webhook_url})
    except Exception as e:
        return JSONResponse({"error": str(e), "webhook_url": webhook_url})

@app.post("/profil/save-notifications")
async def save_notifications(request: Request,
                              email: str = Form(""),
                              telegram_chat_id: str = Form(""),
                              notify_24h: str = Form("0"),
                              notify_2h: str = Form("0")):
    user = get_current_user(request)
    if not user: return RedirectResponse("/login", status_code=303)
    conn = get_db()
    q(conn, """
        INSERT INTO user_notifications (user_id, email, telegram_chat_id, notify_24h, notify_2h)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT(user_id) DO UPDATE SET
            email=EXCLUDED.email,
            telegram_chat_id=EXCLUDED.telegram_chat_id,
            notify_24h=EXCLUDED.notify_24h,
            notify_2h=EXCLUDED.notify_2h
    """, (user["id"],
          email.strip() or None,
          telegram_chat_id.strip() or None,
          1 if notify_24h == "1" else 0,
          1 if notify_2h == "1" else 0))
    conn.commit()
    release_db(conn)
    return RedirectResponse("/profil?notif_saved=1", status_code=303)


# ─── Notifications Push ──────────────────────────────────────────────────────

VAPID_PUBLIC_KEY  = os.environ.get("VAPID_PUBLIC_KEY",  "BINHFCzaBJaUu7IC6X5TP2hVJZAwExk0CSgnoHMmwY2kdqd_3eLl9_Ug96ww656cWrxW3uOVTYHjDmSMCnwRAE0")
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "g4lZjEZoksbqJhEQ81uN2TJuajDHPNPZcLtkZuNchWA")
def send_push_notification(endpoint: str, p256dh: str, auth: str, title: str, body: str) -> bool:
    from webpush import send_web_push
    return send_web_push(
        endpoint=endpoint, p256dh=p256dh, auth=auth,
        title=title, body=body,
        vapid_private_key=VAPID_PRIVATE_KEY,
        vapid_public_key=VAPID_PUBLIC_KEY,
    )

@app.get("/push/vapid-public-key")
async def get_vapid_public_key():
    return JSONResponse({"publicKey": VAPID_PUBLIC_KEY})

@app.post("/push/subscribe")
async def push_subscribe(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"ok": False}, status_code=401)
    body = await request.json()
    endpoint = body.get("endpoint")
    p256dh   = body.get("keys", {}).get("p256dh")
    auth     = body.get("keys", {}).get("auth")
    if not endpoint or not p256dh or not auth:
        return JSONResponse({"ok": False, "error": "Données manquantes"}, status_code=400)
    conn = get_db()
    q(conn, """
        INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT(endpoint) DO UPDATE SET user_id=%s, p256dh=%s, auth=%s
    """, (user["id"], endpoint, p256dh, auth, user["id"], p256dh, auth))
    conn.commit()
    release_db(conn)
    return JSONResponse({"ok": True})

@app.post("/push/unsubscribe")
async def push_unsubscribe(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"ok": False}, status_code=401)
    body = await request.json()
    endpoint = body.get("endpoint")
    conn = get_db()
    q(conn, "DELETE FROM push_subscriptions WHERE endpoint=%s", (endpoint,))
    conn.commit()
    release_db(conn)
    return JSONResponse({"ok": True})

@app.get("/admin/run-cron-notify")
async def run_cron_manually(request: Request):
    """Déclenche le cron manuellement depuis le navigateur."""
    require_admin(request)
    conn = get_db()
    try:
        now = datetime.now(timezone.utc)
        sent = []
        upcoming = qall(conn, """
            SELECT md.id as matchday_id, md.number, md.label, md.season_id,
                   MIN(m.kickoff_time) as first_kickoff
            FROM matchdays md
            JOIN matches m ON m.matchday_id=md.id
            JOIN seasons s ON s.id=md.season_id
            WHERE s.is_active=1 AND m.kickoff_time > to_char(NOW() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS')
            GROUP BY md.id, md.number, md.label, md.season_id
            ORDER BY first_kickoff LIMIT 3
        """)
        notif_users = qall(conn, """
            SELECT un.*, u.username FROM user_notifications un
            JOIN users u ON u.id=un.user_id
        """)
        debug = []
        for md in upcoming:
            try:
                ko = datetime.fromisoformat(md["first_kickoff"].replace(" ", "T") + "+00:00")
                diff_hours = (ko - now).total_seconds() / 3600
            except:
                continue
            label = md["label"] or f"J{md['number']}"
            debug.append({"matchday": label, "diff_hours": round(diff_hours, 2)})
            for notif_type, hours_before, window in [("24h", 24, 2), ("2h", 2, 1.5)]:
                if hours_before - window < diff_hours <= hours_before:
                    already = qone(conn, "SELECT id FROM notification_log WHERE matchday_id=%s AND type=%s",
                                   (md["matchday_id"], notif_type))
                    if already:
                        sent.append({"matchday": label, "type": notif_type, "status": "deja envoye"})
                        continue
                    nb_matches_r = qone(conn, "SELECT COUNT(*) as cnt FROM matches WHERE matchday_id=%s", (md["matchday_id"],))
                    nb_matches = nb_matches_r["cnt"] if nb_matches_r else 0
                    title = f"⚽ {label} dans {hours_before}h !"
                    body_txt = f"Debut de la {label} dans {hours_before} heure{'s' if hours_before > 1 else ''}, fais tes pronos ! https://l1pronos.up.railway.app"
                    ok_count = 0
                    for nu in notif_users:
                        notify_flag = "notify_24h" if notif_type == "24h" else "notify_2h"
                        if not nu.get(notify_flag):
                            continue
                        pc = qone(conn, """
                            SELECT COUNT(*) as cnt FROM pronostics p
                            JOIN matches m ON m.id=p.match_id
                            WHERE p.user_id=%s AND m.matchday_id=%s
                        """, (nu["user_id"], md["matchday_id"]))
                        if pc and pc["cnt"] >= nb_matches:
                            continue
                        if nu.get("telegram_chat_id"):
                            tg_msg = "<b>" + title + "</b>\n" + body_txt
                            if send_telegram(nu["telegram_chat_id"], tg_msg):
                                ok_count += 1
                        elif nu.get("email"):
                            if send_reminder_email(nu["email"], title, body_txt):
                                ok_count += 1
                    q(conn, "INSERT INTO notification_log (matchday_id, type) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                      (md["matchday_id"], notif_type))
                    conn.commit()
                    sent.append({"matchday": label, "type": notif_type, "sent": ok_count})
    finally:
        release_db(conn)
    return JSONResponse({"ok": True, "now_utc": now.isoformat(), "debug": debug, "sent": sent})

@app.get("/admin/reset-notif-log")
async def reset_notif_log(request: Request):
    require_admin(request)
    conn = get_db()
    q(conn, "DELETE FROM notification_log")
    conn.commit()
    release_db(conn)
    return JSONResponse({"ok": True, "message": "Log notifications réinitialisé"})

@app.get("/admin/cron-notify-debug")
async def cron_notify_debug(request: Request):
    """Debug : voir ce que le cron verrait."""
    require_admin(request)
    conn = get_db()
    now = datetime.now(timezone.utc)
    upcoming = qall(conn, """
        SELECT md.id as matchday_id, md.number, md.label,
               MIN(m.kickoff_time) as first_kickoff
        FROM matchdays md
        JOIN matches m ON m.matchday_id=md.id
        JOIN seasons s ON s.id=md.season_id
        WHERE s.is_active=1 AND m.kickoff_time > to_char(NOW() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS')
        GROUP BY md.id, md.number, md.label
        ORDER BY first_kickoff LIMIT 3
    """)
    subs = qall(conn, "SELECT id, user_id FROM push_subscriptions")
    result = []
    for md in upcoming:
        try:
            ko = datetime.fromisoformat(md["first_kickoff"].replace(" ", "T") + "+00:00")
            diff_hours = (ko - now).total_seconds() / 3600
        except Exception as e:
            diff_hours = -1
        result.append({
            "matchday": md["label"] or f"J{md['number']}",
            "first_kickoff": md["first_kickoff"],
            "diff_hours": round(diff_hours, 2),
            "in_24h_window": 23 < diff_hours <= 24,
            "in_2h_window": 1 < diff_hours <= 2,
        })
    release_db(conn)
    return JSONResponse({"now_utc": now.isoformat(), "upcoming": result, "subscriptions": len(subs)})

@app.post("/admin/cron-notify")
async def cron_notify(request: Request):
    """Appelé par cron-job.org toutes les heures — envoie les notifications si nécessaire."""
    # Vérifier le secret pour éviter les appels non autorisés
    secret = request.headers.get("X-Cron-Secret", "")
    cron_secret = os.environ.get("CRON_SECRET", "")
    if cron_secret and secret != cron_secret:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    conn = get_db()
    now = datetime.now(timezone.utc)
    sent = []

    # Récupérer tous les premiers matchs de chaque journée active
    upcoming = qall(conn, """
        SELECT md.id as matchday_id, md.number, md.label, md.season_id,
               MIN(m.kickoff_time) as first_kickoff
        FROM matchdays md
        JOIN matches m ON m.matchday_id=md.id
        JOIN seasons s ON s.id=md.season_id
        WHERE s.is_active=1 AND m.kickoff_time > to_char(NOW() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS')
        GROUP BY md.id, md.number, md.label, md.season_id
        ORDER BY first_kickoff
        LIMIT 3
    """)

    subscriptions = qall(conn, "SELECT * FROM push_subscriptions")

    for md in upcoming:
        try:
            ko = datetime.fromisoformat(md["first_kickoff"].replace(" ", "T") + "+00:00")
        except:
            continue
        diff_hours = (ko - now).total_seconds() / 3600
        label = md["label"] or f"J{md['number']}"

        for notif_type, hours_before in [("24h", 24), ("2h", 2)]:
            if hours_before - 1 < diff_hours <= hours_before:
                # Vérifier si déjà envoyé
                already = qone(conn, "SELECT id FROM notification_log WHERE matchday_id=%s AND type=%s",
                               (md["matchday_id"], notif_type))
                if already:
                    continue
                # Envoyer uniquement aux abonnés qui n'ont pas complété leurs pronos
                # Compter les matchs de la journée
                matches_count = qone(conn, """
                    SELECT COUNT(*) as cnt FROM matches WHERE matchday_id=%s
                """, (md["matchday_id"],))
                nb_matches = matches_count["cnt"] if matches_count else 0

                title = f"⚽ {label} dans {hours_before}h !"
                body_text = f"Début de la {label} dans {hours_before} heure{'s' if hours_before > 1 else ''}, fais tes pronos ! 👉 https://l1pronos.up.railway.app"
                ok_count = 0
                # Récupérer tous les abonnés aux notifications
                notif_users = qall(conn, """
                    SELECT un.*, u.username FROM user_notifications un
                    JOIN users u ON u.id=un.user_id
                    WHERE (notify_24h=1 AND %s='24h') OR (notify_2h=1 AND %s='2h')
                """, (notif_type, notif_type))
                for nu in notif_users:
                    # Vérifier si pronos complets
                    pc = qone(conn, """
                        SELECT COUNT(*) as cnt FROM pronostics p
                        JOIN matches m ON m.id=p.match_id
                        WHERE p.user_id=%s AND m.matchday_id=%s
                    """, (nu["user_id"], md["matchday_id"]))
                    if pc and pc["cnt"] >= nb_matches:
                        continue
                    # Envoyer Telegram
                    if nu["telegram_chat_id"]:
                        tg_msg = f"<b>{title}</b>\n{body_text}"
                        if send_telegram(nu["telegram_chat_id"], tg_msg):
                            ok_count += 1
                    # Envoyer Email
                    elif nu["email"]:
                        if send_reminder_email(nu["email"], title, body_text):
                            ok_count += 1
                # Logger l'envoi
                q(conn, "INSERT INTO notification_log (matchday_id, type) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                  (md["matchday_id"], notif_type))
                conn.commit()
                sent.append({"matchday": label, "type": notif_type, "sent": ok_count})

    release_db(conn)
    return JSONResponse({"ok": True, "sent": sent, "checked": len(upcoming)})


# ─── Export CSV & Mail ───────────────────────────────────────────────────────

import csv
import io
import base64

RESEND_TO = os.environ.get("RESEND_TO", "pronos.ente.va@gmail.com")

def send_csv_backup(season_name: str, matchday_label: str, csv_content: str):
    """Envoie le CSV par mail via l'API Resend, avec le CSV en pièce jointe."""
    if not RESEND_API_KEY:
        print("RESEND_API_KEY non défini, mail non envoyé.")
        return False
    try:
        import urllib.request, urllib.error, json
        filename = f"pronos_{season_name.replace(' ','_')}_{matchday_label.replace(' ','_')}.csv"
        body = f"Backup automatique des pronostics.\n\nSaison : {season_name}\nJournée : {matchday_label}"
        payload = json.dumps({
            "from": RESEND_FROM,
            "to": [RESEND_TO],
            "subject": f"[ENTE Pronos] Backup {season_name} — {matchday_label}",
            "text": body,
            "attachments": [{
                "filename": filename,
                "content": base64.b64encode(csv_content.encode("utf-8")).decode("ascii"),
            }],
        }).encode()
        req = urllib.request.Request(
            "https://api.resend.com/emails",
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
        print(f"Mail backup envoyé : {filename}")
        return True
    except Exception as e:
        print(f"Erreur envoi mail: {e}")
        return False

def generate_pronos_csv(season_id: int, matchday_id: int, conn) -> str:
    """Génère un CSV des pronostics d'une journée."""
    rows = qall(conn, """
        SELECT u.username, m.home_team, m.away_team, m.kickoff_time,
               p.home_score as pred_home, p.away_score as pred_away,
               m.home_score as real_home, m.away_score as real_away
        FROM pronostics p
        JOIN users u ON u.id=p.user_id
        JOIN matches m ON m.id=p.match_id
        WHERE m.matchday_id=%s
        ORDER BY u.username, m.kickoff_time
    """, (matchday_id,))
    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow(["Joueur", "Domicile", "Extérieur", "Coup d'envoi",
                     "Prono Dom", "Prono Ext", "Score Dom", "Score Ext"])
    for r in rows:
        writer.writerow([
            r["username"], r["home_team"], r["away_team"], r["kickoff_time"],
            r["pred_home"], r["pred_away"],
            r["real_home"] if r["real_home"] is not None else "",
            r["real_away"] if r["real_away"] is not None else "",
        ])
    return output.getvalue()

@app.get("/admin/export-csv/{matchday_id}")
async def admin_export_csv(request: Request, matchday_id: int):
    """Télécharger le CSV d'une journée."""
    require_admin(request)
    conn = get_db()
    md = qone(conn, "SELECT * FROM matchdays WHERE id=%s", (matchday_id,))
    season = qone(conn, "SELECT * FROM seasons WHERE id=%s", (md["season_id"],)) if md else None
    if not md or not season:
        release_db(conn)
        return JSONResponse({"error": "Journée introuvable"}, status_code=404)
    csv_content = generate_pronos_csv(season["id"], matchday_id, conn)
    release_db(conn)
    from fastapi.responses import Response
    filename = f"pronos_{season['name'].replace(' ','_')}_{md['label'] or 'J'+str(md['number'])}.csv"
    return Response(
        content=csv_content.encode("utf-8"),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

@app.post("/admin/send-backup/{matchday_id}")
async def admin_send_backup(request: Request, matchday_id: int):
    """Envoie le backup CSV par mail dans un thread séparé."""
    require_admin(request)
    conn = get_db()
    md = qone(conn, "SELECT * FROM matchdays WHERE id=%s", (matchday_id,))
    season = qone(conn, "SELECT * FROM seasons WHERE id=%s", (md["season_id"],)) if md else None
    if not md or not season:
        release_db(conn)
        return JSONResponse({"error": "Journée introuvable"}, status_code=404)
    csv_content = generate_pronos_csv(season["id"], matchday_id, conn)
    release_db(conn)
    label = md["label"] or f"J{md['number']}"
    import asyncio
    loop = asyncio.get_event_loop()
    ok = await loop.run_in_executor(None, send_csv_backup, season["name"], label, csv_content)
    return JSONResponse({"ok": ok})


# ─── Réactions chat ──────────────────────────────────────────────────────────

VALID_REACTIONS = ["👍", "❤️", "😂", "😮", "😢", "😡", "🖕"]

@app.post("/chat/react")
async def chat_react(request: Request, message_id: int = Form(...), emoji: str = Form(...)):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"ok": False}, status_code=401)
    if emoji not in VALID_REACTIONS:
        return JSONResponse({"ok": False, "error": "Emoji invalide"}, status_code=400)
    conn = get_db()
    # Toggle : si la réaction existe déjà, la supprimer ; sinon l'ajouter
    existing = qone(conn, "SELECT id FROM chat_reactions WHERE message_id=%s AND user_id=%s AND emoji=%s",
                    (message_id, user["id"], emoji))
    # Supprimer toute réaction existante de cet user sur ce message
    existing_any = qone(conn, "SELECT id, emoji FROM chat_reactions WHERE message_id=%s AND user_id=%s",
                        (message_id, user["id"]))
    if existing_any:
        q(conn, "DELETE FROM chat_reactions WHERE message_id=%s AND user_id=%s",
          (message_id, user["id"]))
        # Si même emoji → toggle off, sinon → remplace
        if existing_any["emoji"] == emoji:
            action = "removed"
            conn.commit()
            reactions = qall(conn, """
                SELECT cr.emoji, COUNT(*) as count,
                       bool_or(cr.user_id=%s) as mine,
                       string_agg(u.username, ', ' ORDER BY u.username) as users
                FROM chat_reactions cr JOIN users u ON u.id=cr.user_id
                WHERE cr.message_id=%s GROUP BY cr.emoji ORDER BY count DESC
            """, (user["id"], message_id))
            release_db(conn)
            return JSONResponse({"ok": True, "action": action,
                                 "reactions": [dict(r) for r in reactions]})
    # Ajouter la nouvelle réaction
    q(conn, "INSERT INTO chat_reactions (message_id, user_id, emoji) VALUES (%s,%s,%s)",
      (message_id, user["id"], emoji))
    action = "added"
    conn.commit()
    reactions = qall(conn, """
        SELECT cr.emoji, COUNT(*) as count,
               bool_or(cr.user_id=%s) as mine,
               string_agg(u.username, ', ' ORDER BY u.username) as users
        FROM chat_reactions cr JOIN users u ON u.id=cr.user_id
        WHERE cr.message_id=%s GROUP BY cr.emoji ORDER BY count DESC
    """, (user["id"], message_id))
    release_db(conn)
    return JSONResponse({"ok": True, "action": action,
                         "reactions": [dict(r) for r in reactions]})


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
