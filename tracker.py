#!/usr/bin/env python3
"""Follow ESPN boxscores for the 6-way combo and push subtle ntfy alerts.

Alerts:
  * each PTS / REB / AST (and 3PM for the three-point legs) a watched player adds
  * a ✓ when a player crosses the threshold of his leg
  * every lead change in games that have a team-moneyline leg

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

PRIORITY_STAT, PRIORITY_HIT, PRIORITY_LEAD = 2, 3, 3  # ntfy: 1 min .. 5 max


@dataclass
class Game:
    key: str
    league: str
    teams: tuple[str, str]  # substrings of the team location/name
    players: dict[str, dict[str, int]] = field(default_factory=dict)  # name -> {stat: leg threshold}
    lead_alerts: bool = False
    my_side: str | None = None  # team abbreviation you need to win
    event_id: str | None = None


GAMES = [
    Game("CHI@TOR", "nba", ("Chicago", "Toronto"), {"Jakob Poeltl": {"REB": 4}},
         lead_alerts=True, my_side="TOR"),
    Game("MIL@WSH", "nba", ("Milwaukee", "Washington"), {"Trae Young": {"3PM": 1}}),
    Game("ATL@ORL", "nba", ("Atlanta", "Orlando"),
         {"Jalen Johnson": {"AST": 4}, "Nickeil Alexander-Walker": {"3PM": 1}}),
    Game("GS@ATL", "wnba", ("Golden State", "Atlanta"), lead_alerts=True, my_side="ATL"),
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
    st.live = state == "in"
    st.done = state == "post"
    alerts: list[Alert] = []
    first = st.stats is None

    new_stats = box_stats(summary)
    if not first:
        for name, legs in game.players.items():
            old, new = st.stats.get(norm(name)), new_stats.get(norm(name))
            if old is None or new is None:
                continue
            parts, hit = [], False
            for stat in STATS:
                delta = new[stat] - old[stat]
                if delta <= 0:
                    continue
                parts.append(f"+{delta} {stat} ({new[stat]})")
                thr = legs.get(stat)
                if thr and old[stat] < thr <= new[stat]:
                    hit = True
            if parts:
                body = " · ".join(parts) + (" ✓ leg" if hit else "")
                alerts.append(Alert(f'{name.split()[-1]} · {game.key}', body,
                                    PRIORITY_HIT if hit else PRIORITY_STAT))
    if new_stats:
        st.stats = new_stats
    elif first:
        st.stats = {}

    if game.lead_alerts and len(scores) == 2 and any(scores.values()):
        (a, sa), (b, sb) = scores.items()
        leader = a if sa > sb else b if sb > sa else None
        if leader and st.leader and leader != st.leader and not first:
            mark = "" if game.my_side is None else ("🟢 " if leader == game.my_side else "🔴 ")
            hi, lo = max(sa, sb), min(sa, sb)
            alerts.append(Alert(f"{game.key} lead change", f"{mark}{leader} leads {hi}-{lo} · {when}",
                                PRIORITY_LEAD))
        if leader:
            st.leader = leader
    return alerts


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
            except (urllib.error.URLError, TimeoutError, ValueError, KeyError) as e:
                print(f"{g.key}: poll failed: {e!r}", file=sys.stderr)
        time.sleep(POLL_LIVE if any(s.live for s in states.values()) else POLL_IDLE)


if __name__ == "__main__":
    main()
