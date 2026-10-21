"""Offline tests using payloads shaped like ESPN's /summary response."""
import unittest

from tracker import Game, GameState, process


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
                         {"Jakob Poeltl": {"REB": 4}, "Trae Young": {"3PM": 1}},
                         lead_alerts=True, my_side="TOR")
        self.st = GameState()

    def test_baseline_is_silent(self):
        s = summary({"CHI": 50, "TOR": 40}, {"Jakob Poeltl": (6, 0, 3, 1)})
        self.assertEqual(process(self.game, self.st, s), [])

    def test_stat_deltas_and_threshold(self):
        process(self.game, self.st, summary({"CHI": 50, "TOR": 40}, {"Jakob Poeltl": (6, 0, 3, 1)}))
        a, = process(self.game, self.st, summary({"CHI": 50, "TOR": 42}, {"Jakob Poeltl": (8, 0, 4, 1)}))
        self.assertEqual(a.body, "+2 PTS (8) · +1 REB (4) ✓ leg")
        self.assertEqual(a.priority, 3)
        self.assertEqual(process(self.game, self.st,
                                 summary({"CHI": 50, "TOR": 42}, {"Jakob Poeltl": (8, 0, 4, 1)})), [])

    def test_three_pointer_gives_one_alert(self):
        process(self.game, self.st, summary({"CHI": 50, "TOR": 40}, {"Trae Young": (5, 0, 1, 1)}))
        a, = process(self.game, self.st, summary({"CHI": 50, "TOR": 43}, {"Trae Young": (8, 1, 1, 1)}))
        self.assertEqual(a.body, "+3 PTS (8) · +1 3PM (1) ✓ leg")

    def test_lead_change_not_tie_or_same_team_retake(self):
        stats = {"Jakob Poeltl": (0, 0, 0, 0)}
        process(self.game, self.st, summary({"CHI": 50, "TOR": 48}, stats))
        self.assertEqual(process(self.game, self.st, summary({"CHI": 50, "TOR": 50}, stats)), [])
        self.assertEqual(process(self.game, self.st, summary({"CHI": 52, "TOR": 50}, stats)), [])
        a, = process(self.game, self.st, summary({"CHI": 52, "TOR": 53}, stats))
        self.assertEqual(a.body, "🟢 TOR leads 53-52 · Q2 4:12")

    def test_final_marks_done(self):
        process(self.game, self.st, summary({"CHI": 1, "TOR": 2}, {}, state="post"))
        self.assertTrue(self.st.done)


if __name__ == "__main__":
    unittest.main()
