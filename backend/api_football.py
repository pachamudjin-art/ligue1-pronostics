"""
Intégration API-Football (api-football.com) pour récupérer le calendrier Ligue 1.
Plan gratuit : 100 requêtes/jour, suffisant pour un usage entre amis.

Ligue 1 = league_id 61 sur API-Football.
"""

import urllib.request
import urllib.error
import json
import os
from datetime import datetime

API_KEY = os.environ.get("API_FOOTBALL_KEY", "")
BASE_URL = "https://v3.football.api-sports.io"
LEAGUE_ID = 61  # Ligue 1


def _request(endpoint: str, params: dict) -> dict | None:
    # Relit la clé à chaque appel pour prendre en compte les changements de variable d'env
    api_key = os.environ.get("API_FOOTBALL_KEY", "")
    if not api_key:
        print("API_FOOTBALL_KEY non définie.")
        return None

    query = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{BASE_URL}/{endpoint}?{query}"
    req = urllib.request.Request(url)
    req.add_header("x-apisports-key", api_key)

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.URLError as e:
        print(f"Erreur API-Football ({url}): {e}")
        return None
    except Exception as e:
        print(f"Erreur inattendue API-Football: {e}")
        return None


def fetch_fixtures(season_year: int, matchday: int | None = None) -> list:
    """
    Récupère les matchs d'une saison (et optionnellement d'une journée).
    Retourne une liste de dicts normalisés.
    """
    params = {"league": LEAGUE_ID, "season": season_year}
    if matchday is not None:
        params["round"] = f"Regular Season - {matchday}"

    data = _request("fixtures", params)
    if not data or "response" not in data:
        return []

    result = []
    for fixture in data["response"]:
        f = fixture["fixture"]
        teams = fixture["teams"]
        goals = fixture["goals"]

        kickoff_raw = f.get("date", "")
        try:
            kickoff_dt = datetime.fromisoformat(kickoff_raw.replace("Z", "+00:00"))
            kickoff_str = kickoff_dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            kickoff_str = kickoff_raw

        # Extraction du numéro de journée
        round_str = fixture.get("league", {}).get("round", "")
        matchday_number = None
        if "Regular Season - " in round_str:
            try:
                matchday_number = int(round_str.split("Regular Season - ")[1])
            except Exception:
                pass

        status = f.get("status", {}).get("short", "NS")
        # Normalisation du statut
        if status in ("FT", "AET", "PEN"):
            norm_status = "finished"
        elif status in ("1H", "2H", "HT", "ET", "P", "LIVE"):
            norm_status = "live"
        elif status in ("PST", "CANC", "ABD"):
            norm_status = "postponed"
        else:
            norm_status = "scheduled"

        result.append({
            "external_id": f["id"],
            "home_team": teams["home"]["name"],
            "away_team": teams["away"]["name"],
            "kickoff_time": kickoff_str,
            "home_score": goals.get("home"),
            "away_score": goals.get("away"),
            "status": norm_status,
            "matchday_number": matchday_number,
        })

    return result


def import_matchday_to_db(season_year: int, matchday_number: int,
                           season_id: int, matchday_id: int,
                           conn) -> tuple[int, list]:
    """
    Importe les matchs d'une journée depuis l'API vers la DB.
    Retourne (nb_importés, erreurs).
    """
    fixtures = fetch_fixtures(season_year, matchday_number)
    if not fixtures:
        return 0, ["Aucun match récupéré depuis l'API (vérifiez la clé API ou la connexion)."]

    c = conn.cursor()
    imported = 0
    errors = []

    for f in fixtures:
        try:
            existing = c.execute(
                "SELECT id FROM matches WHERE external_id=?", (f["external_id"],)
            ).fetchone()

            if existing:
                c.execute("""
                    UPDATE matches SET
                        home_team=?, away_team=?, kickoff_time=?,
                        home_score=?, away_score=?, status=?
                    WHERE id=?
                """, (
                    f["home_team"], f["away_team"], f["kickoff_time"],
                    f["home_score"], f["away_score"], f["status"],
                    existing["id"]
                ))
            else:
                c.execute("""
                    INSERT INTO matches
                        (matchday_id, home_team, away_team, kickoff_time,
                         home_score, away_score, status, external_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    matchday_id,
                    f["home_team"], f["away_team"], f["kickoff_time"],
                    f["home_score"], f["away_score"], f["status"],
                    f["external_id"]
                ))
                imported += 1
        except Exception as e:
            errors.append(str(e))

    conn.commit()
    return imported, errors


def update_live_scores(season_year: int, conn) -> int:
    """
    Met à jour les scores des matchs en cours ou récents.
    Retourne le nombre de matchs mis à jour.
    """
    fixtures = fetch_fixtures(season_year)
    c = conn.cursor()
    updated = 0

    for f in fixtures:
        if f["external_id"] and f["status"] in ("finished", "live"):
            result = c.execute(
                "UPDATE matches SET home_score=?, away_score=?, status=? WHERE external_id=?",
                (f["home_score"], f["away_score"], f["status"], f["external_id"])
            )
            if result.rowcount > 0:
                updated += 1

    conn.commit()
    return updated
