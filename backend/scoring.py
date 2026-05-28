"""
Logique de calcul des points selon la formule LibreOffice.

Légende :
  BB = Bonus But     = 6 pts  (score exact, 4 buts ou plus)
  PP = Parfait       = 4 pts  (score exact, moins de 4 buts)
  PA = Approchant    = 3 pts  (bonne issue + écart de buts proche + total de buts proche)
  PJ = Juste         = 2 pts  (bonne issue uniquement)
       Mauvais       = 0 pts

Formule LibreOffice traduite :
=SI(NB.VIDE(V26:W26)=2; 0;
  SI(ET(D33=V26; E33=W26; (D33+E33)>=4); 6;
  SI(ET(D33=V26; E33=W26; D33+E33<4); 4;
  SI(ET(
       ET(ABS(D33-E33)-ABS(V26-W26)<=1; (ABS(D33-E33)-ABS(V26-W26))>=-1);
       ABS((D33+E33)-(V26+W26))<=2;
       OU(ET(D33>E33;V26>W26); ET(D33<E33;V26<W26); ET(D33=E33;V26=W26;D33+V26=E33+W26))
     ); 3;
  SI(OU(ET(D33>E33;V26>W26); ET(D33<E33;V26<W26); ET(D33=E33;V26=W26;D33+V26=E33+W26));
     2; 0)
  ))))

D33/E33 = score réel domicile/extérieur
V26/W26 = pronostic domicile/extérieur
"""

def compute_points(real_home: int, real_away: int,
                   pred_home: int, pred_away: int) -> dict:
    """
    Retourne un dict {points, label} pour un pronostic.
    label : 'BB', 'PP', 'PA', 'PJ', ''
    """
    # Score exact ?
    exact = (real_home == pred_home and real_away == pred_away)
    if exact:
        total_goals = real_home + real_away
        if total_goals >= 4:
            return {"points": 6, "label": "BB"}
        else:
            return {"points": 4, "label": "PP"}

    # Bonne issue ?
    def outcome(h, a):
        if h > a:
            return "H"
        elif h < a:
            return "A"
        else:
            return "D"

    real_outcome = outcome(real_home, real_away)
    pred_outcome = outcome(pred_home, pred_away)
    good_outcome = (real_outcome == pred_outcome)

    if good_outcome:
        # PA = bonne issue + différence de buts proche (±1) + total de buts proche (±2)
        diff_real = abs(real_home - real_away)
        diff_pred = abs(pred_home - pred_away)
        goal_diff_close = abs(diff_real - diff_pred) <= 1
        total_close = abs((real_home + real_away) - (pred_home + pred_away)) <= 2

        if goal_diff_close and total_close:
            return {"points": 3, "label": "PA"}
        else:
            return {"points": 2, "label": "PJ"}
    else:
        return {"points": 0, "label": ""}


def compute_estimate_points(real_score: int, estimated_score: int) -> int:
    """
    +2 points si l'estimation est exacte, 0 sinon.
    """
    return 2 if real_score == estimated_score else 0


def compute_matchday_stats(pronostics_with_results: list) -> dict:
    """
    Calcule les stats agrégées pour un joueur sur une journée.

    pronostics_with_results : liste de dicts avec
      real_home, real_away, pred_home, pred_away
      (ne passer que les matchs terminés)

    Retourne : {points, pj, pp, pa, bb}
    """
    total_points = 0
    pj = pp = pa = bb = 0

    for p in pronostics_with_results:
        result = compute_points(
            p["real_home"], p["real_away"],
            p["pred_home"], p["pred_away"]
        )
        pts = result["points"]
        label = result["label"]
        total_points += pts
        if label == "PJ":
            pj += 1
        elif label == "PP":
            pp += 1
        elif label == "PA":
            pa += 1
        elif label == "BB":
            bb += 1

    return {
        "points": total_points,
        "pj": pj,
        "pp": pp,
        "pa": pa,
        "bb": bb
    }


def compute_general_ranking(players_data: list) -> list:
    """
    Tri général : points DESC → PJ DESC → PP DESC → PA DESC.

    players_data : liste de dicts {user_id, username, points, pj, pp, pa, bb, estimates_ok}
    Retourne la liste triée avec rang.
    """
    sorted_players = sorted(
        players_data,
        key=lambda x: (-x["points"], -x["pj"], -x["pp"], -x["pa"])
    )
    rank = 1
    for i, player in enumerate(sorted_players):
        if i > 0:
            prev = sorted_players[i - 1]
            if (player["points"] == prev["points"] and
                    player["pj"] == prev["pj"] and
                    player["pp"] == prev["pp"] and
                    player["pa"] == prev["pa"]):
                player["rank"] = prev["rank"]
            else:
                rank = i + 1
                player["rank"] = rank
        else:
            player["rank"] = 1
    return sorted_players
