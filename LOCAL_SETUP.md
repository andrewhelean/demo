# Task: verify and run the ESPN combo tracker locally

Repo: https://github.com/andrewhelean/demo, branch `espn-combo-tracker`. Files: `tracker.py` (stdlib-only Python), `test_tracker.py`.

## What it does
Polls ESPN's `site.api.espn.com/apis/site/v2/sports/basketball/{nba|wnba}/summary` and sends ntfy pushes (mirrored to Apple Watch):
- Each PTS/REB/AST (plus 3PM) a watched player adds; `✓ leg` when his leg threshold is crossed.
- Every lead change in games with a team-moneyline leg (CHI@TOR, GS@ATL).

Watched legs (6-way combo): Jakob Poeltl REB 4+, Toronto win, Trae Young 3PT 1+, Jalen Johnson AST 4+, Nickeil Alexander-Walker 3PT 1+, Atlanta win. Config is the `GAMES` list in `tracker.py`.

## Status
Written in a sandbox that could not reach ESPN. Offline tests pass (`python3 -m unittest`), but it has **never run against the live API**. Do not invent or mock results; verify against real data.

## Steps
1. `git checkout espn-combo-tracker`, run `python3 -m unittest`.
2. While a game is live, run `python3 tracker.py --dry-run` and confirm:
   - each game prints `event <id>` (otherwise try `--date YYYYMMDD` or `--event KEY=ID`);
   - ESPN's actual JSON matches the parsing (`boxscore.players[].statistics[].labels/athletes`, `header.competitions[0]` score/status). Fix `box_stats`/`score_line` if not;
   - player names match ESPN's `displayName` (e.g. "Jakob Poeltl") and stat deltas appear within ~a minute of the play;
   - lead-change scores agree with ESPN's site.
3. Assumption to confirm: the "Atlanta" leg in "Game 3: Golden State vs Atlanta" is WNBA (Valkyries vs Dream). If it is another league, change `league` in `GAMES`.
4. Send a test push: `curl -d "test" ntfy.sh/<topic>`, confirm it reaches the Apple Watch.
5. Run for real and keep the machine awake (e.g. `caffeinate -i` on macOS):
   `NTFY_URL=https://ntfy.sh/<topic> python3 tracker.py`
   The script exits when all games are final.

Ask the user for their ntfy topic; do not commit it. Keep fixes minimal, and commit/push to this branch.
