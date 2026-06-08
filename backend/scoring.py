"""
Logique de calcul des points.

BB=6, PP=4, PA=3, PJ=2 pts
Compteurs cumulatifs : BB→BB+PP+PJ, PP→PP+PJ, PA→PA+PJ

Podium (Coupe du Monde etc.) :
  Vainqueur exact          → 10 pts
  Équipe à la bonne place  → 5 pts (2e ou 3e)
  Équipe sur le podium     → 3 pts (mauvaise place)
  Absent du podium         → 0 pt
"""

def compute_points(real_home, real_away, pred_home, pred_away):
    exact = (real_home == pred_home and real_away == pred_away)
    if exact:
        total = real_home + real_away
        if total >= 4:
            return {"points": 6, "label": "BB", "pj": 1, "pp": 1, "pa": 0, "bb": 1}
        else:
            return {"points": 4, "label": "PP", "pj": 1, "pp": 1, "pa": 0, "bb": 0}

    def outcome(h, a):
        if h > a: return "H"
        if h < a: return "A"
        return "D"

    good = outcome(real_home, real_away) == outcome(pred_home, pred_away)
    if good:
        diff_real = abs(real_home - real_away)
        diff_pred = abs(pred_home - pred_away)
        if abs(diff_real - diff_pred) <= 1 and abs((real_home+real_away)-(pred_home+pred_away)) <= 2:
            return {"points": 3, "label": "PA", "pj": 1, "pp": 0, "pa": 1, "bb": 0}
        else:
            return {"points": 2, "label": "PJ", "pj": 1, "pp": 0, "pa": 0, "bb": 0}
    return {"points": 0, "label": "", "pj": 0, "pp": 0, "pa": 0, "bb": 0}


def compute_podium_points(real_rank1, real_rank2, real_rank3, pred_rank1, pred_rank2, pred_rank3):
    """
    Calcule les points du pronostic podium.
    Retourne {points, detail} avec détail par équipe.
    """
    real_podium = {real_rank1: 1, real_rank2: 2, real_rank3: 3}
    predictions = [(pred_rank1, 1), (pred_rank2, 2), (pred_rank3, 3)]
    total = 0
    detail = []

    for team, pred_place in predictions:
        real_place = real_podium.get(team)
        if real_place is None:
            detail.append({"team": team, "pred_place": pred_place, "real_place": None, "points": 0})
        elif pred_place == 1 and real_place == 1:
            detail.append({"team": team, "pred_place": 1, "real_place": 1, "points": 20})
            total += 20
        elif pred_place == real_place:
            detail.append({"team": team, "pred_place": pred_place, "real_place": real_place, "points": 10})
            total += 10
        else:
            detail.append({"team": team, "pred_place": pred_place, "real_place": real_place, "points": 6})
            total += 6

    return {"points": total, "detail": detail}


def compute_estimate_points(real_score, estimated_score):
    return 2 if real_score == estimated_score else 0


def compute_matchday_stats(pronostics_with_results):
    total_pts = pj = pp = pa = bb = 0
    for p in pronostics_with_results:
        r = compute_points(p["real_home"], p["real_away"], p["pred_home"], p["pred_away"])
        total_pts += r["points"]
        pj += r["pj"]; pp += r["pp"]; pa += r["pa"]; bb += r["bb"]
    return {"points": total_pts, "pj": pj, "pp": pp, "pa": pa, "bb": bb}


def compute_general_ranking(players_data):
    sorted_p = sorted(players_data, key=lambda x: (-x["points"], -x["pj"], -x["pp"], -x["pa"]))
    for i, p in enumerate(sorted_p):
        if i > 0:
            prev = sorted_p[i-1]
            same = (p["points"]==prev["points"] and p["pj"]==prev["pj"]
                    and p["pp"]==prev["pp"] and p["pa"]==prev["pa"])
            p["rank"] = prev["rank"] if same else i + 1
        else:
            p["rank"] = 1
    return sorted_p
