"""Offline tests using payloads shaped like ESPN's /summary response."""
import unittest

from tracker import Game, GameState, Leg, combo_result, process


def summary(scores, stats, state="in", period=2, clock="4:12"):
    labels = ["MIN", "PTS", "FG", "3PT", "REB", "AST"]
    athletes = [
        {"athlete": {"displayName": n},
         "stats": ["20", str(p), "3-7", f"{t}-3", str(r), str(a)]}
        for n, (p, t, r, a) in stats.items()
    ]
    return {
        "header": {"competitions": [{
            "competitors": [{"team": {"abbreviation": k}, "score": str(v)} for k, v in scores.items()],
            "status": {"type": {"state": state}, "period": period, "displayClock": clock},
        }]},
        "boxscore": {"players": [{"statistics": [{"labels": labels, "athletes": athletes}]}]},
    }


class ProcessTest(unittest.TestCase):
    def setUp(self):
        self.game = Game("CHI@TOR", "nba", ("Chicago", "Toronto"),
                         [Leg("Poeltl REB 4+", "Jakob Poeltl", "REB", 4),
                          Leg("Young 3PM 1+", "Trae Young", "3PM", 1),
                          Leg("TOR win", team="TOR")])
        self.st = GameState()

    def test_baseline_is_silent(self):
        s = summary({"CHI": 50, "TOR": 40}, {"Jakob Poeltl": (6, 0, 3, 1)})
        self.assertEqual(process(self.game, self.st, s), [])

    def test_stat_deltas_then_leg_won_then_silent(self):
        process(self.game, self.st, summary({"CHI": 50, "TOR": 40}, {"Jakob Poeltl": (6, 0, 3, 1)}))
        a, = process(self.game, self.st, summary({"CHI": 50, "TOR": 42}, {"Jakob Poeltl": (8, 0, 4, 1)}))
        self.assertEqual(a.body, "+2 PTS (8) · +1 REB (4) · ✓ Poeltl REB 4+ WON")
        self.assertEqual(self.game.legs[0].status, "won")
        self.assertEqual(process(self.game, self.st,
                                 summary({"CHI": 50, "TOR": 42}, {"Jakob Poeltl": (10, 0, 5, 2)})), [])

    def test_three_pointer_gives_one_alert(self):
        process(self.game, self.st, summary({"CHI": 50, "TOR": 40}, {"Trae Young": (5, 0, 1, 1)}))
        a, = process(self.game, self.st, summary({"CHI": 50, "TOR": 43}, {"Trae Young": (8, 1, 1, 1)}))
        self.assertEqual(a.body, "+3 PTS (8) · +1 3PM (1) · ✓ Young 3PM 1+ WON")

    def test_lead_change_not_tie_or_same_team_retake(self):
        process(self.game, self.st, summary({"CHI": 50, "TOR": 48}, {}))
        self.assertEqual(process(self.game, self.st, summary({"CHI": 50, "TOR": 50}, {})), [])
        self.assertEqual(process(self.game, self.st, summary({"CHI": 52, "TOR": 50}, {})), [])
        a, = process(self.game, self.st, summary({"CHI": 52, "TOR": 53}, {}))
        self.assertEqual(a.body, "🟢 TOR leads 53-52 · Q2 4:12")

    def test_final_settles_open_legs_and_stops(self):
        process(self.game, self.st, summary({"CHI": 50, "TOR": 40}, {"Jakob Poeltl": (6, 0, 3, 1)}))
        alerts = process(self.game, self.st,
                         summary({"CHI": 100, "TOR": 90}, {"Jakob Poeltl": (6, 0, 3, 1)}, state="post"))
        self.assertEqual([l.status for l in self.game.legs], ["lost", "lost", "lost"])
        self.assertEqual(len(alerts), 3)
        self.assertTrue(self.st.done)

    def test_team_leg_won_and_no_lead_alert_after_settling(self):
        process(self.game, self.st, summary({"CHI": 50, "TOR": 40}, {}))
        a, = [x for x in process(self.game, self.st, summary({"CHI": 90, "TOR": 95}, {}, state="post"))
              if "final" in x.title]
        self.assertEqual(a.body, "✓ TOR win WON · 95-90")

    def test_game_done_early_when_all_legs_settled(self):
        game = Game("MIL@WSH", "nba", ("Milwaukee", "Washington"),
                    [Leg("Young 3PM 1+", "Trae Young", "3PM", 1)])
        st = GameState()
        process(game, st, summary({"MIL": 10, "WSH": 8}, {"Trae Young": (2, 0, 0, 0)}))
        process(game, st, summary({"MIL": 10, "WSH": 11}, {"Trae Young": (5, 1, 0, 0)}))
        self.assertTrue(st.done)

    def test_combo_result(self):
        games = [Game("A", "nba", ("a", "b"), [Leg("x", team="A", status="won")]),
                 Game("B", "nba", ("c", "d"), [Leg("y", team="B")])]
        self.assertIsNone(combo_result(games))
        games[1].legs[0].status = "won"
        self.assertEqual(combo_result(games).body, "WON ✓ all 6 legs")
        games[1].legs[0].status = "lost"
        self.assertEqual(combo_result(games).body, "LOST ✗ y")


if __name__ == "__main__":
    unittest.main()
