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

PRIOR = 2.0  # smoothing: count / (opportunities + PRIOR), so 1-of-1 can't outrank 6-of-8

# Fearless series phases. Game 1 is the full-pool baseline; Game 3+ is where lockouts bite.
# Game 2 is a transition game and belongs to neither bucket.
EARLY, LATE = "early", "late"
LATE_GAME = 3
MIN_SHIFT_COUNT = 2  # a shift needs at least this many picks/bans to be reported


def smoothed(count: int, opportunities: int) -> float:
    return count / (opportunities + PRIOR) if opportunities > 0 else 0.0


def series_phase(game_number: int) -> str | None:
    if game_number == 1:
        return EARLY
    if game_number >= LATE_GAME:
        return LATE
    return None


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
class PhaseStats:
    """Counters for one phase (early/late) of a team's Fearless series."""

    games: int = 0
    wins: int = 0
    picks: Counter = field(default_factory=Counter)
    pick_unavailable: Counter = field(default_factory=Counter)
    bans: Counter = field(default_factory=Counter)
    ban_unavailable: Counter = field(default_factory=Counter)
    first_phase_roles: Counter = field(default_factory=Counter)  # roles of their phase-1 picks

    def pick_opportunities(self, champ_id):
        return self.games - self.pick_unavailable[champ_id]

    def ban_opportunities(self, champ_id):
        return self.games - self.ban_unavailable[champ_id]


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
    # Fearless series only: EARLY (Game 1) and LATE (Game 3+)
    phases: dict = field(default_factory=lambda: {EARLY: PhaseStats(), LATE: PhaseStats()})

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

    phase = series_phase(game.number) if mode != rules.FEARLESS_OFF else None
    bucket = stats.phases[phase] if phase else None
    if mode != rules.FEARLESS_OFF:
        stats.by_game_number[game.number]["games"] += 1
    if bucket:
        bucket.games += 1
        bucket.wins += game.winner_id == team_id
        for champ in locked_for_us | all_bans | opp_picks:
            bucket.pick_unavailable[champ] += 1
        for champ in locked_for_opp:
            bucket.ban_unavailable[champ] += 1

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
            if bucket:
                bucket.picks[a.champion_id] += 1
                if s.phase == 1 and a.role:
                    bucket.first_phase_roles[a.role] += 1
        else:
            stats.bans[a.champion_id] += 1
            stats.ban_phase[a.champion_id][s.phase] += 1
            if bucket:
                bucket.bans[a.champion_id] += 1


# --- Presentation --------------------------------------------------------


def champ_summary(c: Champion):
    return {"id": c.id, "key": c.key, "name": c.name, "image_url": c.image_url, "roles": c.roles}


def _raw_direction(row) -> int:
    """+1 if the raw late rate is higher than the raw early rate, -1 if lower, 0 if equal."""
    def rate(side):
        return side["count"] / side["opportunities"] if side["opportunities"] else 0.0
    early, late = rate(row["early"]), rate(row["late"])
    return (late > early) - (late < early)


def _ranked_shifts(rows, rising: bool, top: int):
    sign = 1 if rising else -1
    kept = [r for r in rows if sign * r["shift"] > 0 and sign * _raw_direction(r) > 0]
    return sorted(kept, key=lambda r: -sign * r["shift"])[:top]


def _shift_row(champ, early_n, early_opp, late_n, late_opp):
    return {
        "champion": champ_summary(champ),
        "early": {"count": early_n, "opportunities": early_opp},
        "late": {"count": late_n, "opportunities": late_opp},
        "shift": round(smoothed(late_n, late_opp) - smoothed(early_n, early_opp), 3),
    }


def late_series_shift(s: TeamStats, champs: dict, top: int = 6) -> dict:
    """Compare Game 1 with Game 3+ of Fearless series, measured against availability.

    - risers: picked more often late, when available (pocket picks, late-series comfort)
    - fallers: picked often in Game 1 but passed over late *even when still available*
      (champions that are simply Fearless-locked late don't count as falling)
    - ban_risers: banned more often late, when the opponent could still pick them

    Rows are ranked by the smoothed shift, but a row is only reported when the raw rates
    agree on the direction; otherwise smoothing alone could make a 2-of-2 late sample
    look like a drop from 13-of-21.
    """
    early, late = s.phases[EARLY], s.phases[LATE]

    def pick_row(cid):
        return _shift_row(champs[cid], early.picks[cid], early.pick_opportunities(cid),
                          late.picks[cid], late.pick_opportunities(cid))

    risers = [pick_row(cid) for cid in late.picks if cid in champs and late.picks[cid] >= MIN_SHIFT_COUNT]
    fallers = [
        pick_row(cid) for cid in early.picks
        if cid in champs and early.picks[cid] >= MIN_SHIFT_COUNT and late.pick_opportunities(cid) >= MIN_SHIFT_COUNT
    ]
    ban_risers = [
        _shift_row(champs[cid], early.bans[cid], early.ban_opportunities(cid), late.bans[cid], late.ban_opportunities(cid))
        for cid in late.bans if cid in champs and late.bans[cid] >= MIN_SHIFT_COUNT
    ]

    def role_shares(b: PhaseStats):
        total = sum(b.first_phase_roles.values())
        return [{"role": r, "share": round(n / total, 3)} for r, n in b.first_phase_roles.most_common()] if total else []

    def summary(b: PhaseStats):
        return {"games": b.games, "wins": b.wins, "losses": b.games - b.wins, "first_phase_roles": role_shares(b)}

    return {
        "late_game": LATE_GAME,
        "min_count": MIN_SHIFT_COUNT,
        "early": summary(early),
        "late": summary(late),
        "risers": _ranked_shifts(risers, rising=True, top=top),
        "fallers": _ranked_shifts(fallers, rising=False, top=top),
        "ban_risers": _ranked_shifts(ban_risers, rising=True, top=top),
        "low_sample": min(early.games, late.games) < 5,
    }


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
        "late_series": late_series_shift(s, champs),
    }
