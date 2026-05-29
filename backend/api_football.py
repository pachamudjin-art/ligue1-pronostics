"""
Intégration football-data.org — Gratuit, Ligue 1 couverte pour toujours.
Ligue 1 = competition code "FL1"
Clé API : créer un compte gratuit sur https://www.football-data.org/client/register
Variable d'environnement : FOOTBALL_DATA_KEY
"""

import urllib.request
import urllib.error
import json
import os
from datetime import datetime

BASE_URL = "https://api.football-data.org/v4"
COMPETITION_CODE = "FL1"  # Ligue 1


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
        print(f"Erreur HTTP football-data.org {e.code} ({url}): {body[:300]}")
        return None
    except urllib.error.URLError as e:
        print(f"Erreur réseau football-data.org ({url}): {e}")
        return None
    except Exception as e:
        print(f"Erreur inattendue football-data.org: {e}")
        return None


def fetch_fixtures(season_year: int, matchday: int | None = None) -> list:
    """
    Récupère les matchs d'une saison (et optionnellement d'une journée).
    Retourne une liste de dicts normalisés.
    """
    params = {"season": season_year}
    if matchday is not None:
        params["matchday"] = matchday

    data = _request(f"competitions/{COMPETITION_CODE}/matches", params)
    if not data or "matches" not in data:
        return []

    result = []
    for match in data["matches"]:
        utc_date = match.get("utcDate", "")
        try:
            dt = datetime.fromisoformat(utc_date.replace("Z", "+00:00"))
            kickoff_str = dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            kickoff_str = utc_date[:19].replace("T", " ")

        status_raw = match.get("status", "SCHEDULED")
        if status_raw in ("FINISHED",):
            norm_status = "finished"
        elif status_raw in ("IN_PLAY", "PAUSED"):
            norm_status = "live"
        elif status_raw in ("POSTPONED", "CANCELLED", "SUSPENDED"):
            norm_status = "postponed"
        else:
            norm_status = "scheduled"

        score = match.get("score", {})
        full_time = score.get("fullTime", {})
        home_score = full_time.get("home")
        away_score = full_time.get("away")

        result.append({
            "external_id": match.get("id"),
            "home_team": match["homeTeam"]["shortName"] or match["homeTeam"]["name"],
            "away_team": match["awayTeam"]["shortName"] or match["awayTeam"]["name"],
            "kickoff_time": kickoff_str,
            "home_score": home_score,
            "away_score": away_score,
            "status": norm_status,
            "matchday_number": match.get("matchday"),
        })

    return result


def import_matchday_to_db(season_year: int, matchday_number: int,
                           season_id: int, matchday_id: int, conn) -> tuple[int, list]:
    """
    Importe les matchs d'une journée depuis l'API vers la DB.
    """
    fixtures = fetch_fixtures(season_year, matchday_number)
    if not fixtures:
        return 0, ["Aucun match récupéré depuis l'API (vérifiez FOOTBALL_DATA_KEY ou la connexion)."]

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
