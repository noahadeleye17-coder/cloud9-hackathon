# Junie guidelines: Fearless Draft Lab

## Project
Django 6.1 + DRF + HTMX app for LoL draft scouting and Fearless series planning. Two apps:
- `esports`: historical match data, `draft_rules.py` (draft order + Fearless logic), `tendencies.py` (analysis).
- `planner`: coach plans, `state.py` (simulator), `suggestions.py`, `export.py`, `api.py`, `views.py` (HTMX UI).

## Rules
- All draft-order and Fearless logic goes through `esports/draft_rules.py`. Never hard-code step numbers or lockout rules elsewhere.
- Pick and ban rates are always measured against *opportunities* (see README Methodology), never raw game counts.
- Every suggestion must carry human-readable reasons that show the raw counts.
- Synthetic data is tagged `source=synthetic`. Never mix it into real-data analysis or role derivation silently.
- Run `python manage.py test` before finishing any task; add tests for new logic.
- Keep views thin; put logic in `state.py` / `suggestions.py` / `tendencies.py`.
- Code style: PEP 8, 120-char lines, type hints on public functions.
