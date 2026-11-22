"""
Opponent tendency analysis.

Key idea: pick/ban *rates* are measured against *opportunities*, not raw games.
A champion only counts as an opportunity for a team in a game if they could
actually have picked it, i.e. it wasn't:
  - banned in that game (by either team),
  - picked by the opponent in that game,
  - Fearless-locked for that team from an earlier game of the series.
This keeps late Fearless games from making a team's comfort picks look
"abandoned" when they simply weren't available.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date

from django.db.models import Q

from . import draft_rules as rules
from .models import Champion, DraftAction, Game, Team


@dataclass
class Filters:
    patch: str | None = None
    date_from: date | None = None
    date_to: date | None = None

    def apply(self, qs):
        if self.patch:
            qs = qs.filter(patch=self.patch)
        if self.date_from:
            qs = qs.filter(series__date__gte=self.date_from)
        if self.date_to:
            qs = qs.filter(series__date__lte=self.date_to)
        return qs

    def as_dict(self):
        return {
            "patch": self.patch,
            "from": self.date_from.isoformat() if self.date_from else None,
            "to": self.date_to.isoformat() if self.date_to else None,
        }


@dataclass
class TeamStats:
    """Raw counters for one team. Rates are derived on demand."""

    team: Team
    games: int = 0
    series_ids: set = field(default_factory=set)
    record: dict = field(default_factory=lambda: {s: {"games": 0, "wins": 0} for s in rules.SIDES})

    picks: Counter = field(default_factory=Counter)
    pick_unavailable: Counter = field(default_factory=Counter)  # games where champ wasn't pickable
    pick_phase1: Counter = field(default_factory=Counter)
    pick_roles: dict = field(default_factory=lambda: defaultdict(Counter))  # champ -> role -> n

    bans: Counter = field(default_factory=Counter)
    ban_unavailable: Counter = field(default_factory=Counter)
    ban_phase: dict = field(default_factory=lambda: defaultdict(Counter))  # champ -> phase -> n

    blue_games: int = 0
    blue_first_pick: Counter = field(default_factory=Counter)
    red_games: int = 0
    red_first_rotation: Counter = field(default_factory=Counter)  # red's picks at steps 8 and 9

    # Fearless series only: game number -> stats
    by_game_number: dict = field(default_factory=lambda: defaultdict(lambda: {"games": 0, "picks": Counter()}))

    def pick_opportunities(self, champ_id):
        return self.games - self.pick_unavailable[champ_id]

    def ban_opportunities(self, champ_id):
        return self.games - self.ban_unavailable[champ_id]

    def pick_rate(self, champ_id):
        opp = self.pick_opportunities(champ_id)
        return (self.picks[champ_id] / opp) if opp else 0.0

    def ban_rate(self, champ_id):
        opp = self.ban_opportunities(champ_id)
        return (self.bans[champ_id] / opp) if opp else 0.0


def compute_team_stats(team: Team, filters: Filters | None = None) -> TeamStats:
    filters = filters or Filters()
    games = list(
        filters.apply(Game.objects.filter(Q(blue_team=team) | Q(red_team=team)))
        .select_related("series")
        .order_by("series_id", "number")
    )
    stats = TeamStats(team=team)
    if not games:
        return stats

    # Fetch actions for every game in the touched series (not just filtered games),
    # because Fearless lockouts depend on earlier games of the same series.
    series_ids = {g.series_id for g in games}
    actions_by_game = defaultdict(list)
    for a in DraftAction.objects.filter(game__series_id__in=series_ids).order_by("game_id", "order"):
        actions_by_game[a.game_id].append(a)
    all_games_by_series = defaultdict(list)
    for g in Game.objects.filter(series_id__in=series_ids).order_by("number"):
        all_games_by_series[g.series_id].append(g)

    target_game_ids = {g.id for g in games}
    series_mode = {g.series_id: g.series.fearless_mode for g in games}

    for series_id, series_games in all_games_by_series.items():
        prior_picks: list[rules.PriorPick] = []
        mode = series_mode[series_id]
        for g in series_games:
            if g.id in target_game_ids:
                _accumulate_game(stats, g, actions_by_game[g.id], prior_picks, mode)
            prior_picks.extend((a.team_id, a.champion_id) for a in actions_by_game[g.id] if a.action_type == rules.PICK)

    return stats


def _accumulate_game(stats: TeamStats, game: Game, actions, prior_picks, mode):
    team_id = stats.team.id
    side = game.side_of(team_id)
    opp_id = game.red_team_id if side == rules.BLUE else game.blue_team_id

    stats.games += 1
    stats.series_ids.add(game.series_id)
    stats.record[side]["games"] += 1
    if game.winner_id == team_id:
        stats.record[side]["wins"] += 1

    all_bans = {a.champion_id for a in actions if a.action_type == rules.BAN}
    opp_picks = {a.champion_id for a in actions if a.action_type == rules.PICK and a.team_id == opp_id}
    locked_for_us = rules.fearless_locked(prior_picks, team_id, mode)
    locked_for_opp = rules.fearless_locked(prior_picks, opp_id, mode)

    for champ in locked_for_us | all_bans | opp_picks:
        stats.pick_unavailable[champ] += 1
    # A ban is "wasted" on a champion the opponent can't pick anyway.
    for champ in locked_for_opp:
        stats.ban_unavailable[champ] += 1

    if side == rules.BLUE:
        stats.blue_games += 1
    else:
        stats.red_games += 1

    if mode != rules.FEARLESS_OFF:
        stats.by_game_number[game.number]["games"] += 1

    for a in actions:
        if a.team_id != team_id:
            continue
        s = rules.step(a.order)
        if a.action_type == rules.PICK:
            stats.picks[a.champion_id] += 1
            if s.phase == 1:
                stats.pick_phase1[a.champion_id] += 1
            if a.role:
                stats.pick_roles[a.champion_id][a.role] += 1
            if a.order == 7:
                stats.blue_first_pick[a.champion_id] += 1
            elif a.order in (8, 9):
                stats.red_first_rotation[a.champion_id] += 1
            if mode != rules.FEARLESS_OFF:
                stats.by_game_number[game.number]["picks"][a.champion_id] += 1
        else:
            stats.bans[a.champion_id] += 1
            stats.ban_phase[a.champion_id][s.phase] += 1


# --- Presentation --------------------------------------------------------


def champ_summary(c: Champion):
    return {"id": c.id, "key": c.key, "name": c.name, "image_url": c.image_url, "roles": c.roles}


def team_tendencies(team: Team, filters: Filters | None = None, top: int = 15) -> dict:
    """JSON-ready tendency report for the scouting page and the API."""
    filters = filters or Filters()
    s = compute_team_stats(team, filters)
    ids = set(s.picks) | set(s.bans) | set(s.blue_first_pick) | set(s.red_first_rotation)
    champs = {c.id: c for c in Champion.objects.filter(id__in=ids)}

    def ranked(counter, limit=top):
        return [(cid, n) for cid, n in counter.most_common(limit) if cid in champs]

    picks = [
        {
            "champion": champ_summary(champs[cid]),
            "count": n,
            "opportunities": s.pick_opportunities(cid),
            "rate": round(s.pick_rate(cid), 3),
            "phase1": s.pick_phase1[cid],
            "roles": dict(s.pick_roles[cid].most_common()),
        }
        for cid, n in ranked(s.picks)
    ]
    bans = [
        {
            "champion": champ_summary(champs[cid]),
            "count": n,
            "opportunities": s.ban_opportunities(cid),
            "rate": round(s.ban_rate(cid), 3),
            "phase1": s.ban_phase[cid][1],
            "phase2": s.ban_phase[cid][2],
        }
        for cid, n in ranked(s.bans)
    ]

    by_role = {}
    for role in rules.ROLES:
        role_counter = Counter({cid: roles[role] for cid, roles in s.pick_roles.items() if roles.get(role)})
        by_role[role] = [{"champion": champ_summary(champs[cid]), "count": n} for cid, n in ranked(role_counter, 5)]

    by_game = {}
    for number in sorted(s.by_game_number):
        bucket = s.by_game_number[number]
        by_game[number] = {
            "games": bucket["games"],
            "picks": [{"champion": champ_summary(champs[cid]), "count": n} for cid, n in ranked(bucket["picks"], 8)],
        }

    return {
        "team": {"id": team.id, "name": team.name, "short_code": team.short_code},
        "filters": filters.as_dict(),
        "games": s.games,
        "series": len(s.series_ids),
        "record": s.record,
        "picks": picks,
        "bans": bans,
        "blue_first_pick": {
            "games": s.blue_games,
            "champions": [{"champion": champ_summary(champs[cid]), "count": n} for cid, n in ranked(s.blue_first_pick, 8)],
        },
        "red_first_rotation": {
            "games": s.red_games,
            "champions": [
                {"champion": champ_summary(champs[cid]), "count": n} for cid, n in ranked(s.red_first_rotation, 8)
            ],
        },
        "by_role": by_role,
        "by_game_number": by_game,
    }
