"""
Logique de calcul des points.

BB = 6 pts  : score exact, 4 buts ou plus
PP = 4 pts  : score exact, moins de 4 buts
PA = 3 pts  : bonne issue + écart de buts proche (±1) + total de buts proche (±2)
PJ = 2 pts  : bonne issue uniquement
   = 0 pts  : mauvaise issue

Compteurs (cumulatifs pour les égalités) :
  BB → BB + PP + PJ
  PP → PP + PJ
  PA → PA + PJ
  PJ → PJ
"""

def compute_points(real_home, real_away, pred_home, pred_away):
    """Retourne {points, label, pj, pp, pa, bb}."""
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
        goal_diff_close = abs(diff_real - diff_pred) <= 1
        total_close = abs((real_home + real_away) - (pred_home + pred_away)) <= 2
        if goal_diff_close and total_close:
            return {"points": 3, "label": "PA", "pj": 1, "pp": 0, "pa": 1, "bb": 0}
        else:
            return {"points": 2, "label": "PJ", "pj": 1, "pp": 0, "pa": 0, "bb": 0}

    return {"points": 0, "label": "", "pj": 0, "pp": 0, "pa": 0, "bb": 0}


def compute_estimate_points(real_score, estimated_score):
    return 2 if real_score == estimated_score else 0


def compute_matchday_stats(pronostics_with_results):
    """
    pronostics_with_results : liste de dicts avec real_home, real_away, pred_home, pred_away
    Retourne : {points, pj, pp, pa, bb}
    """
    total_pts = pj = pp = pa = bb = 0
    for p in pronostics_with_results:
        r = compute_points(p["real_home"], p["real_away"], p["pred_home"], p["pred_away"])
        total_pts += r["points"]
        pj += r["pj"]
        pp += r["pp"]
        pa += r["pa"]
        bb += r["bb"]
    return {"points": total_pts, "pj": pj, "pp": pp, "pa": pa, "bb": bb}


def compute_general_ranking(players_data):
    """Tri : points DESC → PJ DESC → PP DESC → PA DESC."""
    sorted_p = sorted(
        players_data,
        key=lambda x: (-x["points"], -x["pj"], -x["pp"], -x["pa"])
    )
    for i, p in enumerate(sorted_p):
        if i > 0:
            prev = sorted_p[i-1]
            same = (p["points"] == prev["points"] and p["pj"] == prev["pj"]
                    and p["pp"] == prev["pp"] and p["pa"] == prev["pa"])
            p["rank"] = prev["rank"] if same else i + 1
        else:
            p["rank"] = 1
    return sorted_p
