# Fearless Draft Lab

A League of Legends scouting and draft-planning tool for Fearless series.
It was built for the Cloud9 x JetBrains "Sky's The Limit" hackathon, Category 2 (Macro Scouting & Draft Management).

Coaches use it to **scout** an opponent's pick and ban habits. They then **plan a full Fearless Bo3/Bo5**: the tool runs every pick and ban in pro draft order, locks out champions used earlier in the series, shows what's left, and suggests picks and bans. Each suggestion says why it was made.

## Features

- **Opponent scouting**
  - Most-picked and most-banned champions, rated against the games where each champion was actually available (see Methodology).
  - First-pick habits on blue and red side, picks by role, and blue/red record.
  - How the opponent's picks change from Game 1 to Game 5 of a Fearless series.
  - **Game 1 vs Game 3+ comparison** for Fearless series: late-series pocket picks, comfort picks they drop even while still available, late-series bans, first-phase role priority, and record early vs late.
  - Filters for patch and date range.
- **Fearless series simulator**
  - The real 20-step pro draft order.
  - Hard, Soft or Off Fearless modes.
  - Automatic lockouts, open roles per team, and a champion grid you can search and filter by role.
  - A warning when you ban a champion the opponent already can't pick.
  - If you change an earlier game, any later planned picks that are now illegal get flagged.
- **Tendency-based suggestions** at every step:
  - On your turn: what to ban or pick.
  - On the opponent's turn: a prediction of what they'll do, so you can rehearse their side.
  - Every suggestion shows its numbers, e.g. "TL picked it in 7 of 7 games when available (100%)".
- **Save and export**: plans with notes and results per game, exported as a Markdown match-day sheet or as JSON.
- **REST API** for everything above, under `/api/`.

## Quick start

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
python manage.py migrate
python manage.py load_champions --ddragon   # champion list and portraits from Riot Data Dragon (or omit --ddragon to work offline)
python manage.py seed_demo                  # SYNTHETIC demo data, until GRID data is imported
python manage.py runserver
```

Then open http://127.0.0.1:8000. To run the tests: `python manage.py test`.

## Data

| Source | Status |
|---|---|
| GRID esports data | Importer to be written once access is granted |
| Synthetic demo data | `seed_demo`. Every series is tagged `source=synthetic` and the UI shows a banner while it's loaded |
| Champions and portraits | Riot Data Dragon (`load_champions --ddragon`), plus a bundled role file for pro lanes |

The synthetic generator gives each team built-in habits, so the analysis has real patterns to find:

- comfort pools per role
- signature first picks
- pocket picks saved for Game 3+
- bans that target the opponent

It exists to build and demo the product. It is **not** real match data.

## Methodology

**Rates are measured against opportunities.** Counting picks per game is misleading in Fearless. A champion locked out in Games 2–5 would look "unpopular" when it simply couldn't be picked. A game counts as an *opportunity* for a team to pick a champion only if that champion:

- wasn't banned in that game,
- wasn't picked by the opponent in that game, and
- wasn't Fearless-locked for that team by an earlier game of the series.

The ban rate is measured over games where the opponent could still pick the champion.

**Suggestions** rank champions by a smoothed rate, `count / (opportunities + 2)`, so a 1-of-1 sample can't outrank 6-of-8. The reasons always show the raw counts, so the coach can judge the sample size. What gets scored depends on whose turn it is:

| Turn | What it scores |
|---|---|
| Your ban | What the opponent picks most, among champions still available to them. Boosted for their blue-side first pick (phase 1) and their Game 3+ picks (late series). Scored lower if they've already filled that champion's role |
| Your pick | Your own comfort picks for roles you still need to fill, plus how much it denies the opponent. In Hard Fearless, taking a champion also locks it away from them for the rest of the series |
| Their ban (predicted) | What they usually ban, plus your most-picked champions |
| Their pick (predicted) | Their comfort picks for roles they still need to fill |

**Game 1 vs Game 3+.** Game 1 is the full-pool baseline; Game 3+ is where Fearless lockouts bite (Game 2 is left out as a transition game). Both sides use the same opportunity rule, so a comfort pick that is simply locked out late is not reported as "dropped". Shifts are ranked by the smoothed rate, but a shift is only shown when the raw rates point the same way, so a 2-of-2 late sample can't look like a drop. Suggestions in Game 3+ boost a champion only when the opponent's late-series rate is higher than their overall rate.

**Fearless variants:**

- **Hard:** a champion picked by either team is locked for both teams for the rest of the series.
- **Soft:** each team can't re-pick its own champions.
- **Off:** normal draft.

Bans never carry over between games.

## Architecture

- `esports/`: historical match data (Team, Champion, Series, Game, DraftAction).
  - `draft_rules.py`: draft order and Fearless logic. Pure Python, shared by everything else.
  - `tendencies.py`: the scouting analysis.
  - `synthetic.py`: the demo data generator.
- `planner/`: the coach's series plans (DraftPlan, PlannedGame, PlannedAction).
  - `state.py`: the simulator.
  - `suggestions.py`, `export.py`, `api.py`: suggestions, export and the REST API.
  - The web UI uses Django templates and HTMX.

Stack: Django 6.1, Django REST Framework and HTMX. SQLite is used locally and PostgreSQL in production (via `DATABASE_URL`). Deploys to Render via `render.yaml`.

## API

| Method | Endpoint | |
|---|---|---|
| GET | `/api/teams/` · `/api/champions/` | |
| GET | `/api/teams/{id}/tendencies/?patch=&from=&to=` | scouting report |
| POST | `/api/draft-plans/` | create a series plan (`our_team`, `opponent`, `format`, `fearless_mode`) |
| GET | `/api/draft-plans/{id}/pool/?game=N` | next step, Fearless locks, available pool per role |
| POST / DELETE | `/api/draft-plans/{id}/actions/` | record the next pick/ban (409 if illegal) / undo the last one |
| GET | `/api/draft-plans/{id}/suggestions/?game=N` | suggestions with reasons |
| PATCH | `/api/draft-plans/{id}/games/N/` | side, result, notes |
| GET | `/api/draft-plans/{id}/export/?as=markdown` | export (JSON by default) |

## JetBrains tooling

This section will be filled in during the build: what PyCharm, Junie and DataGrip were each used for.

## License

MIT. League of Legends and Riot Games are trademarks of Riot Games, Inc. This project isn't endorsed by Riot Games.
