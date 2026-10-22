#!/usr/bin/env python3
"""Follow ESPN boxscores for the 6-way combo and push subtle ntfy alerts.

Alerts:
  * each PTS / REB / AST (and 3PM for the three-point legs) a watched player adds
  * ✓ when a player's leg hits, ✗ when it fails; alerts for that leg then stop
  * every lead change while a team-moneyline leg is still open
  * a final WON/LOST alert once the whole combo is decided; exits when all legs settle

Usage:
  NTFY_URL=https://ntfy.sh/<your-secret-topic> python3 tracker.py
  python3 tracker.py --dry-run          # print instead of sending
"""
import argparse
import json
import os
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass, field

API = "https://site.api.espn.com/apis/site/v2/sports/basketball/{league}/{endpoint}"
STATS = ("PTS", "REB", "AST", "3PM")
POLL_LIVE, POLL_IDLE = 5, 60

PRIORITY_STAT, PRIORITY_HIT, PRIORITY_LEAD, PRIORITY_RESULT = 2, 3, 3, 4  # ntfy: 1 min .. 5 max


@dataclass
class Leg:
    label: str
    player: str | None = None  # player leg: needs stat >= thr
    stat: str = ""
    thr: int = 0
    team: str | None = None  # team leg: abbreviation that must win
    status: str | None = None  # None (open) | "won" | "lost"


@dataclass
class Game:
    key: str
    league: str
    teams: tuple[str, str]  # substrings of the team location/name
    legs: list[Leg]
    event_id: str | None = None

    @property
    def team_leg(self) -> Leg | None:
        return next((l for l in self.legs if l.team), None)


GAMES = [
    Game("CHI@TOR", "nba", ("Chicago", "Toronto"),
         [Leg("Poeltl REB 4+", "Jakob Poeltl", "REB", 4), Leg("TOR win", team="TOR")]),
    Game("MIL@WSH", "nba", ("Milwaukee", "Washington"),
         [Leg("Young 3PM 1+", "Trae Young", "3PM", 1)]),
    Game("ATL@ORL", "nba", ("Atlanta", "Orlando"),
         [Leg("J. Johnson AST 4+", "Jalen Johnson", "AST", 4),
          Leg("NAW 3PM 1+", "Nickeil Alexander-Walker", "3PM", 1)]),
    Game("GS@ATL", "wnba", ("Golden State", "Atlanta"), [Leg("ATL win", team="ATL")]),
]


@dataclass
class Alert:
    title: str
    body: str
    priority: int


@dataclass
class GameState:
    stats: dict[str, dict[str, int]] | None = None  # None until baselined
    leader: str | None = None  # abbreviation of last team to lead
    done: bool = False
    live: bool = False


def norm(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower().strip()


def to_int(x) -> int:
    try:
        return int(x)
    except (TypeError, ValueError):
        return 0


def get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "combo-tracker/1.0"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def find_event(game: Game, date: str | None) -> str | None:
    url = API.format(league=game.league, endpoint="scoreboard")
    if date:
        url += f"?dates={date}"
    for ev in get_json(url).get("events", []):
        comps = ev["competitions"][0]["competitors"]
        names = [norm(f'{c["team"].get("location", "")} {c["team"].get("displayName", "")}') for c in comps]
        if all(any(norm(t) in n for n in names) for t in game.teams):
            return ev["id"]
    return None


def box_stats(summary: dict) -> dict[str, dict[str, int]]:
    """Normalized player name -> {PTS, REB, AST, 3PM}."""
    out = {}
    for team in summary.get("boxscore", {}).get("players", []):
        for grp in team.get("statistics", []):
            labels = grp.get("labels", [])
            for a in grp.get("athletes", []):
                row = dict(zip(labels, a.get("stats", [])))
                made = str(row.get("3PT", "0-0")).split("-")[0]
                out[norm(a["athlete"]["displayName"])] = {
                    "PTS": to_int(row.get("PTS")), "REB": to_int(row.get("REB")),
                    "AST": to_int(row.get("AST")), "3PM": to_int(made),
                }
    return out


def score_line(summary: dict) -> tuple[str, dict[str, int], str]:
    """(state, {abbr: score}, 'Q2 4:12')"""
    comp = summary["header"]["competitions"][0]
    scores = {c["team"]["abbreviation"]: to_int(c.get("score")) for c in comp["competitors"]}
    st = comp["status"]
    period = to_int(st.get("period"))
    label = f"Q{period}" if period <= 4 else f"OT{period - 4}"
    return st["type"]["state"], scores, f'{label} {st.get("displayClock", "")}'.strip()


def process(game: Game, st: GameState, summary: dict) -> list[Alert]:
    """Diff one poll against the previous one; the first poll only sets the baseline."""
    state, scores, when = score_line(summary)
    final = state == "post"
    st.live = state == "in"
    alerts: list[Alert] = []
    first = st.stats is None
    new_stats = box_stats(summary)

    for leg in game.legs:
        if leg.status or not leg.player:
            continue
        key = norm(leg.player)
        new = new_stats.get(key)
        old = None if first else st.stats.get(key, dict.fromkeys(STATS, 0))
        parts = []
        if new and old:
            parts = [f"+{new[s] - old[s]} {s} ({new[s]})" for s in STATS if new[s] > old[s]]
        have = new[leg.stat] if new else 0
        if have >= leg.thr:
            leg.status = "won"
            parts.append(f"✓ {leg.label} WON")
        elif final:
            leg.status = "lost"
            parts.append(f"✗ {leg.label} LOST ({have})")
        if parts:
            alerts.append(Alert(f'{leg.player.split()[-1]} · {game.key}', " · ".join(parts),
                                PRIORITY_HIT if leg.status else PRIORITY_STAT))
    if new_stats:
        st.stats = new_stats
    elif first:
        st.stats = {}

    tleg = game.team_leg
    if tleg and not tleg.status and len(scores) == 2 and any(scores.values()):
        mine, other = scores[tleg.team], sum(scores.values()) - scores[tleg.team]
        if final:
            tleg.status = "won" if mine > other else "lost"
            mark = "✓" if tleg.status == "won" else "✗"
            alerts.append(Alert(f"{game.key} final", f"{mark} {tleg.label} {tleg.status.upper()} · {mine}-{other}",
                                PRIORITY_HIT))
        else:
            leader = tleg.team if mine > other else next(t for t in scores if t != tleg.team) if other > mine else None
            if leader and st.leader and leader != st.leader and not first:
                mark = "🟢" if leader == tleg.team else "🔴"
                alerts.append(Alert(f"{game.key} lead change",
                                    f"{mark} {leader} leads {max(mine, other)}-{min(mine, other)} · {when}",
                                    PRIORITY_LEAD))
            if leader:
                st.leader = leader

    st.done = final or all(l.status for l in game.legs)
    return alerts


def combo_result(games: list[Game]) -> Alert | None:
    legs = [l for g in games for l in g.legs]
    lost = next((l for l in legs if l.status == "lost"), None)
    if lost:
        return Alert("6-way combo", f"LOST ✗ {lost.label}", PRIORITY_RESULT)
    if all(l.status == "won" for l in legs):
        return Alert("6-way combo", "WON ✓ all 6 legs", PRIORITY_RESULT)
    return None


def send(alert: Alert, url: str | None) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {alert.title}: {alert.body}", flush=True)
    if not url:
        return
    title = unicodedata.normalize("NFKD", alert.title).encode("ascii", "ignore").decode()
    req = urllib.request.Request(url, data=alert.body.encode(), method="POST",
                                 headers={"Title": title, "Priority": str(alert.priority)})
    try:
        urllib.request.urlopen(req, timeout=10).close()
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"ntfy send failed: {e}", file=sys.stderr)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print alerts, don't send")
    ap.add_argument("--date", help="scoreboard date YYYYMMDD (default: ESPN's current day)")
    ap.add_argument("--event", action="append", default=[], metavar="KEY=ID",
                    help="pin an ESPN event id, e.g. --event CHI@TOR=401810000")
    args = ap.parse_args()

    url = None if args.dry_run else os.environ.get("NTFY_URL")
    if not url and not args.dry_run:
        sys.exit("Set NTFY_URL (e.g. https://ntfy.sh/<topic>) or pass --dry-run")
    pinned = dict(e.split("=", 1) for e in args.event)
    for g in GAMES:
        g.event_id = pinned.get(g.key)
    states = {g.key: GameState() for g in GAMES}
    result_sent = False

    while not all(s.done for s in states.values()):
        for g in GAMES:
            st = states[g.key]
            if st.done:
                continue
            try:
                if g.event_id is None:
                    g.event_id = find_event(g, args.date)
                    if g.event_id is None:
                        print(f"{g.key}: not on scoreboard yet", file=sys.stderr)
                        continue
                    print(f"{g.key}: event {g.event_id}", flush=True)
                summary = get_json(API.format(league=g.league, endpoint="summary") + f"?event={g.event_id}")
                for alert in process(g, st, summary):
                    send(alert, url)
                result = None if result_sent else combo_result(GAMES)
                if result:
                    send(result, url)
                    result_sent = True
            except (urllib.error.URLError, TimeoutError, ValueError, KeyError) as e:
                print(f"{g.key}: poll failed: {e!r}", file=sys.stderr)
        time.sleep(POLL_LIVE if any(s.live for s in states.values()) else POLL_IDLE)


if __name__ == "__main__":
    main()
