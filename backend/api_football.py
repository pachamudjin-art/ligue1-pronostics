"""
Intégration football-data.org — Gratuit, multi-compétitions.
Clé API : variable d'environnement FOOTBALL_DATA_KEY

Codes compétitions :
  FL1 = Ligue 1
  WC  = Coupe du Monde FIFA
  CL  = Champions League
  PL  = Premier League
"""

import urllib.request
import urllib.error
import urllib.parse
import json
import os
from datetime import datetime

BASE_URL = "https://api.football-data.org/v4"


def _request(endpoint: str, params: dict = None) -> dict | None:
    api_key = os.environ.get("FOOTBALL_DATA_KEY", "")
    if not api_key:
        print("FOOTBALL_DATA_KEY non définie.")
        return None
    query = ("?" + "&".join(f"{k}={v}" for k, v in params.items())) if params else ""
    url = f"{BASE_URL}/{endpoint}{query}"
    req = urllib.request.Request(url)
    req.add_header("X-Auth-Token", api_key)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"Erreur HTTP {e.code} ({url}): {body[:300]}")
        return None
    except Exception as e:
        print(f"Erreur API: {e}")
        return None


def fetch_fixtures(season_year: int, matchday: int = None, competition_code: str = "FL1") -> list:
    params = {"season": season_year}
    if matchday is not None:
        params["matchday"] = matchday

    data = _request(f"competitions/{competition_code}/matches", params)
    if not data or "matches" not in data:
        return []

    result = []
    for match in data["matches"]:
        utc_date = match.get("utcDate", "")
        try:
            dt = datetime.fromisoformat(utc_date.replace("Z", "+00:00"))
            kickoff_str = dt.strftime("%Y-%m-%d %H:%M:%S")
        except:
            kickoff_str = utc_date[:19].replace("T", " ")

        status_raw = match.get("status", "SCHEDULED")
        if status_raw == "FINISHED": norm_status = "finished"
        elif status_raw in ("IN_PLAY", "PAUSED"): norm_status = "live"
        elif status_raw in ("POSTPONED", "CANCELLED", "SUSPENDED"): norm_status = "postponed"
        else: norm_status = "scheduled"

        score = match.get("score", {})
        full_time = score.get("fullTime", {})
        home_score = full_time.get("home")
        away_score = full_time.get("away")

        # Nom des équipes : shortName ou name
        home = match["homeTeam"]
        away = match["awayTeam"]
        home_name = home.get("shortName") or home.get("name", "?")
        away_name = away.get("shortName") or away.get("name", "?")

        result.append({
            "external_id": match.get("id"),
            "home_team": home_name,
            "away_team": away_name,
            "kickoff_time": kickoff_str,
            "home_score": home_score,
            "away_score": away_score,
            "status": norm_status,
            "matchday_number": match.get("matchday"),
            "stage": match.get("stage", ""),
        })
    return result


def fetch_teams(competition_code: str, season_year: int) -> list:
    """Retourne la liste des équipes d'une compétition (pour le pronostic podium)."""
    data = _request(f"competitions/{competition_code}/teams", {"season": season_year})
    if not data or "teams" not in data:
        return []
    return [t.get("shortName") or t.get("name") for t in data["teams"]]


def import_matchday_to_db(season_year: int, matchday_number: int,
                           season_id: int, matchday_id: int, conn,
                           competition_code: str = "FL1") -> tuple[int, list]:
    fixtures = fetch_fixtures(season_year, matchday_number, competition_code)
    if not fixtures:
        return 0, ["Aucun match récupéré depuis l'API."]

    from database import q, qone
    imported = 0
    errors = []

    for f in fixtures:
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
                  (matchday_id, f["home_team"], f["away_team"], f["kickoff_time"],
                   f["home_score"], f["away_score"], f["status"], f["external_id"]))
                imported += 1
        except Exception as e:
            errors.append(str(e))

    conn.commit()
    return imported, errors


def update_live_scores(season_year: int, conn, competition_code: str = "FL1") -> int:
    fixtures = fetch_fixtures(season_year, competition_code=competition_code)
    from database import q, qone
    updated = 0
    for f in fixtures:
        if f["external_id"] and f["status"] in ("finished", "live"):
            result = q(conn, "UPDATE matches SET home_score=%s, away_score=%s, status=%s WHERE external_id=%s",
                (f["home_score"], f["away_score"], f["status"], f["external_id"]))
            if result.rowcount > 0:
                updated += 1
    conn.commit()
    return updated
