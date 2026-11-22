# Fearless Draft Lab — Project Plan (Draft v1)

**Hackathon:** Cloud9 x JetBrains: Sky's The Limit (Devpost)
**Category:** 2 — League of Legends Macro Scouting & Draft Management System
**Blueprint:** Modular Draft Organizer with Fearless Draft support (+ tendency analysis)
**Builder:** Noah Adeleye (solo)
**Deadline:** Jan 12, 2027, 11:00 am PT (8:00 pm WAT)
**Working name:** Fearless Draft Lab (change it if you want)

---

## 1. The problem

Cloud9 coaches still do pre-match prep in spreadsheets. They track what champions the opponent picks and bans, then plan their own drafts.
Fearless Draft makes that harder. In a series, a champion that's been played in an earlier game can't be picked again, so the champion pool gets smaller each game.
By Game 3 or Game 5, coaches have to know which champions are still available to both teams and how the opponent usually adjusts.

The judges want the tool to answer real coaching questions like:
- Which pick/ban tendencies does this opponent repeat most?
- How does the opponent's drafting change in Fearless Game 3, 4 or 5?

## 2. What it does (one line)

A web app where a coach scouts an opponent's drafting habits, then runs a full Fearless series draft with the tool keeping track of locked-out champions and suggesting picks and bans based on the data.

## 3. Features

### Must-have (MVP — this is what gets submitted no matter what)
1. **Opponent scouting page**
   - Pick a team and see their most-picked champions, most-banned champions, first-pick habits, and pick order by role.
   - Filter by patch and date range.
2. **Fearless series simulator**
   - Set up a Bo3 or Bo5 between Cloud9 and the opponent.
   - Run each game's pick/ban phase in the standard order (ban, pick, ban, pick).
   - The tool automatically locks out champions already used in earlier games of the series.
   - Shows the "remaining pool" for each team, per role, at every step.
3. **Tendency-based suggestions**
   - At each step in the draft, suggest bans and picks using the opponent's history and what's still available.
   - Every suggestion shows the reason (e.g. "picked in 6 of their last 8 games when available").
4. **Save and export a draft plan**
   - Save a planned series (with notes per game) and export it so the coach can use it on match day.

### Nice-to-have (only after MVP works)
5. **How the opponent drafts late in a series** — compare their Game 1 drafts to their Game 3+ drafts in Fearless series.
6. **Macro context** — jungle pathing or early objective patterns, if the GRID data supports it.
7. **Live draft win-rate estimate** — a predicted win rate for the current draft that updates with each pick (listed as a stretch goal by the organisers).

## 4. Data

- **Main source:** GRID esports data (offered for this category). The Devpost pages don't say how to get access or what fields are included. **First task: ask in the Cloud9 Discord** and find the GRID starter repo.
- **Fallback if GRID access is slow:** use public pro match data so the build isn't blocked. Swap in GRID once access arrives.
- **Champion list / images:** Riot's public Data Dragon. Must follow Riot's developer policies.
- Data needed at minimum: match, series, teams, game number in series, patch, picks and bans in order, side (blue/red), winner.

## 5. Tech stack

| Part | Choice | Why |
|---|---|---|
| Backend | Python, Django + Django REST Framework | What you already use at Markelo |
| Database | PostgreSQL | Relational data (series → games → picks/bans) |
| Frontend | React (Vite) or Django templates + HTMX | Decide in review. HTMX is faster to build solo |
| Data import | Django management commands | Pull and clean GRID data into the DB |
| Hosting | Render (you've used it for Off-Campus Hub) | Judges need a working demo link |
| **JetBrains tools** | **PyCharm (main IDE) + Junie (AI agent)**, DataGrip for the DB | Required, and judges score how deeply they're used |

**Note on VS Code:** other editors are allowed alongside JetBrains tools, but the judges score how much you used JetBrains. Write most of the code in PyCharm, use Junie for real tasks, and commit the `.idea` / `.junie` folders. Keep notes as you go. They become your tooling write-up and the bonus-prize case study.

## 6. Data model (rough)

- **Team** — name, region, short code
- **Champion** — name, roles, image
- **Series** — two teams, format (Bo3/Bo5), Fearless or not, date, patch
- **Game** — series, game number, blue team, red team, first-pick team, who had selection priority, winner
- **DraftAction** — game, order number, team, type (pick/ban), champion
- **DraftPlan** (saved by a coach) — opponent, format, notes, planned games
- **PlannedAction** — plan, game number, step, team, type, champion

The Fearless lockout comes from these tables: the champions that can't be picked are the ones picked in earlier games of the same series.

## 6b. Draft rules the build must follow (added Nov 19)

**Draft order: a fixed 20-step table.** Each step has an order number, a slot (first-pick team or second-pick team) and a type (pick or ban). That one table drives the simulator, the suggestions and the validation.
- Bans: 1st 2nd 1st 2nd 1st 2nd
- Picks: 1st, then 2nd 2nd, then 1st 1st, then 2nd
- Bans: 2nd 1st 2nd 1st
- Picks: 2nd, then 1st 1st, then 2nd

**Key the table by first-pick team, not blue/red.** Since the 2026 season, Riot's "First Selection" rule lets the team with selection priority choose either the map side *or* the draft order. So side and pick order are no longer the same thing. Each game stores both values separately: `blue_team` and `first_pick_team`.

**Hard Fearless.** A champion picked in any game is locked for **both** teams for the rest of the series. Bans don't carry over. Lockout query = every champion picked in earlier games of this series, by either team. The "remaining pool" is shared, though it can still be shown per role for each team's roster. Confirm with Cloud9 Discord that the hackathon uses the pro rules.

**Side / first selection per game.** The higher seed chooses for Game 1. After that, the loser of the previous game chooses. The simulator lets the user set this for every game.

**Small samples.** Few teams play many Game 4s and 5s, so late-series numbers often rest on 2–3 games. Every suggestion shows its sample ("3 of 4 games"). "When available" leaves out games where the champion was banned or already locked.

## 7. API (rough)

- `GET /teams/` and `GET /teams/{id}/tendencies?patch=&from=&to=`
- `GET /champions/`
- `POST /draft-plans/` — start a simulated series
- `GET /draft-plans/{id}/pool?game=` — remaining champions per team
- `POST /draft-plans/{id}/actions/` — record a pick or ban (rejected if the champion is locked out)
- `GET /draft-plans/{id}/suggestions?game=&step=` — suggested picks/bans with reasons
- `GET /draft-plans/{id}/export`

## 8. Timeline (Nov 18 → Jan 12, about 8 weeks)

| Week | Dates | Goal |
|---|---|---|
| 1 | Nov 18–24 | Register, get JetBrains code, join Discord, get GRID access, confirm Fearless rules, finalise this plan |
| 2 | Nov 25–Dec 1 | Django project, data model, import script, real data in the DB |
| 3 | Dec 2–8 | Tendency analysis endpoints + scouting page |
| 4 | Dec 9–15 | Fearless simulator: draft order, lockouts, remaining pool |
| 5 | Dec 16–22 | Suggestions with reasons, save/export plans |
| 6 | Dec 23–29 | UI polish, deploy to Render, test with real series (holiday buffer) |
| 7 | Dec 30–Jan 5 | Pick one nice-to-have (late-series comparison is the safest) |
| 8 | Jan 6–12 | Record demo video, methodology write-up, tooling write-up, bonus write-up. **Submit by Jan 11**, a day early |

## 9. Submission checklist

- [ ] Public GitHub repo with run instructions
- [ ] LICENSE file at repo root (MIT)
- [ ] `.idea` / `.junie` folders committed
- [ ] Working demo link (Render)
- [ ] Description of features + which JetBrains tool did what
- [ ] Methodology write-up
- [ ] Demo video, 3 minutes max, public on YouTube
- [ ] Category 2 selected
- [ ] Optional: AI workflow case study link for the $1,000 bonus

## 10. How this scores against the judging criteria

- **Technological implementation:** real data pipeline, correct Fearless logic, full use of PyCharm + Junie + DataGrip.
- **Design:** one clear flow: scout → plan series → export.
- **Potential impact:** directly replaces the spreadsheet workflow Cloud9 described.
- **Quality of idea:** suggestions that explain their reasons, and late-series Fearless analysis.
- **Presentation:** demo video walks through a real opponent and a full Bo5.

## 11. Open questions (settle before building)

1. How do we get GRID data access, and what's in it?
2. ~~Exact Fearless rules~~ → assuming Hard Fearless + First Selection (section 6b). Still confirm in Discord.
3. React or HTMX for the frontend? Leaning HTMX + Alpine.js for the draft board.
4. Which teams/leagues to focus on for the demo (e.g. LTA + a few top opponents)?

## 12. Risks

- **GRID access is slow or limited** → start with the fallback data, swap in GRID later.
- **Scope creep** → MVP features 1–4 first. Nothing else until they're deployed.
- **Weak JetBrains usage** → build in PyCharm from day one and log how Junie was used.
- **Final-week crunch** → aim to submit Jan 11.
