"""
Tendency-based pick/ban suggestions. Every suggestion carries human-readable reasons.

Scoring uses a *smoothed* rate, count / (opportunities + PRIOR), so a champion
picked 1 of 1 times doesn't outrank one picked 6 of 8 times. Reasons always show
the raw numbers so the coach can judge the sample size themselves.

Turn types:
  our ban       -> target what the opponent picks most, in roles they still need
  our pick      -> our comfort picks for open roles, plus denial value vs. the opponent
  their ban     -> predicted: what they usually ban + what we usually pick
  their pick    -> predicted: their comfort picks for roles they still need
"""

from collections import Counter
from dataclasses import dataclass, field

from esports import draft_rules as rules
from esports.tendencies import Filters, TeamStats, compute_team_stats

PRIOR = 2.0
DENY_WEIGHT = 0.6
DENY_REASON_MIN_RATE = 0.25  # only call out denial when the opponent actually relies on the champ
LATE_GAME = 3  # "late series" = Game 3+


def smoothed(count, opportunities):
    return count / (opportunities + PRIOR) if opportunities > 0 else 0.0


@dataclass
class Suggestion:
    champion: object
    score: float
    reasons: list = field(default_factory=list)

    def as_dict(self):
        c = self.champion
        return {
            "champion": {"id": c.id, "key": c.key, "name": c.name, "image_url": c.image_url, "roles": c.roles},
            "score": round(self.score, 3),
            "reasons": self.reasons,
        }


def _late_picks(stats: TeamStats):
    games = sum(b["games"] for n, b in stats.by_game_number.items() if n >= LATE_GAME)
    picks = Counter()
    for n, b in stats.by_game_number.items():
        if n >= LATE_GAME:
            picks.update(b["picks"])
    return games, picks


def _role_fit(champ, open_roles):
    if not champ.roles:
        return 0.5
    return 1.0 if any(r in open_roles for r in champ.roles) else 0.15


def _pick_reason(stats, cid, who):
    n, opp = stats.picks[cid], stats.pick_opportunities(cid)
    return f"{who} picked it in {n} of {opp} games when available ({n / opp:.0%})" if opp and n else None


class SuggestionEngine:
    def __init__(self, state, filters: Filters | None = None, stats_cache: dict | None = None):
        self.state = state
        self.plan = state.plan
        filters = filters or Filters(patch=self.plan.scout_patch or None)
        cache = stats_cache if stats_cache is not None else {}
        key_us, key_opp = (self.plan.our_team_id, filters.patch), (self.plan.opponent_id, filters.patch)
        if key_us not in cache:
            cache[key_us] = compute_team_stats(self.plan.our_team, filters)
        if key_opp not in cache:
            cache[key_opp] = compute_team_stats(self.plan.opponent, filters)
        self.us, self.opp = cache[key_us], cache[key_opp]
        self.opp_name = self.plan.opponent.short_code
        self.us_name = self.plan.our_team.short_code

    @property
    def mode(self):
        s = self.state.next_step
        if s is None:
            return None
        return ("our" if self.state.is_our_turn() else "their") + f"_{s.action}"

    def suggest(self, limit=8) -> dict:
        s = self.state.next_step
        if s is None:
            return {"step": None, "mode": None, "predicted": False, "suggestions": [], "notes": ["Draft complete."]}
        handler = {
            "our_ban": self._our_ban,
            "our_pick": self._our_pick,
            "their_ban": self._their_ban,
            "their_pick": self._their_pick,
        }[self.mode]
        suggestions = sorted(handler(), key=lambda x: -x.score)[:limit]
        notes = []
        for team_stats, name in ((self.us, self.us_name), (self.opp, self.opp_name)):
            if team_stats.games < 5:
                notes.append(f"Only {team_stats.games} games of data for {name}: treat suggestions as low confidence.")
        return {
            "step": {"order": s.order, "side": s.side, "action": s.action, "label": s.label},
            "mode": self.mode,
            "predicted": not self.state.is_our_turn(),
            "suggestions": [x.as_dict() for x in suggestions],
            "notes": notes,
        }

    # --- handlers ---

    def _our_ban(self):
        opp_id = self.plan.opponent_id
        open_roles = self.state.open_roles(opp_id)
        blocked = self.state.unavailable(opp_id)  # no point banning what they can't pick
        late_games, late_picks = _late_picks(self.opp)
        is_late = self.state.game.number >= LATE_GAME
        opp_blue_first = self.state.game.team_id_on(rules.BLUE) == opp_id and self.state.next_step.order < 7

        out = []
        for c in self.state.champions:
            if c.id in blocked or self.opp.picks[c.id] == 0:
                continue
            score = smoothed(self.opp.picks[c.id], self.opp.pick_opportunities(c.id))
            reasons = [_pick_reason(self.opp, c.id, self.opp_name)]
            if opp_blue_first and self.opp.blue_first_pick[c.id]:
                score *= 1.3
                reasons.append(
                    f"Their blue-side first pick in {self.opp.blue_first_pick[c.id]} of {self.opp.blue_games} blue games"
                )
            if is_late and late_picks[c.id]:
                score *= 1.25
                reasons.append(f"Picked {late_picks[c.id]}x across {late_games} Fearless Game {LATE_GAME}+ games")
            fit = _role_fit(c, open_roles)
            score *= fit
            if fit == 1.0 and len(open_roles) < 5:
                needed = [r for r in c.roles if r in open_roles]
                reasons.append(f"They still need {needed[0]}")
            elif fit < 1.0 and c.roles:
                reasons.append("Role already filled for them, lower value")
            out.append(Suggestion(c, score, [r for r in reasons if r]))
        return out

    def _our_pick(self):
        us_id, opp_id = self.plan.our_team_id, self.plan.opponent_id
        open_roles = self.state.open_roles(us_id)
        blocked = self.state.unavailable(us_id)
        opp_blocked = self.state.unavailable(opp_id)
        hard = self.plan.fearless_mode == rules.FEARLESS_HARD
        games_left = rules.series_length(self.plan.format) - self.state.game.number

        out = []
        for c in self.state.champions:
            if c.id in blocked or (c.roles and not any(r in open_roles for r in c.roles)):
                continue
            comfort = smoothed(self.us.picks[c.id], self.us.pick_opportunities(c.id))
            deny = smoothed(self.opp.picks[c.id], self.opp.pick_opportunities(c.id)) if c.id not in opp_blocked else 0
            score = comfort + DENY_WEIGHT * deny
            if score <= 0:
                continue
            reasons = [_pick_reason(self.us, c.id, self.us_name)]
            if deny and self.opp.picks[c.id] >= 2 and self.opp.pick_rate(c.id) >= DENY_REASON_MIN_RATE:
                reasons.append(f"Denies {self.opp_name}: " + _pick_reason(self.opp, c.id, "they").split("they ", 1)[1])
                if hard and games_left > 0:
                    reasons.append("Hard Fearless: also locks it away from them for the rest of the series")
            roles = [r for r in c.roles if r in open_roles]
            if roles:
                reasons.append(f"Fills {roles[0]}")
            out.append(Suggestion(c, score, [r for r in reasons if r]))
        return out

    def _their_ban(self):
        us_id = self.plan.our_team_id
        blocked = self.state.unavailable(us_id)
        open_roles = self.state.open_roles(us_id)
        out = []
        for c in self.state.champions:
            if c.id in self.state.taken:
                continue
            habit = smoothed(self.opp.bans[c.id], self.opp.ban_opportunities(c.id))
            target = (
                smoothed(self.us.picks[c.id], self.us.pick_opportunities(c.id)) * _role_fit(c, open_roles)
                if c.id not in blocked
                else 0
            )
            score = habit + 0.8 * target
            if score <= 0:
                continue
            reasons = []
            if self.opp.bans[c.id]:
                n, o = self.opp.bans[c.id], self.opp.ban_opportunities(c.id)
                reasons.append(f"{self.opp_name} banned it in {n} of {o} games ({n / o:.0%})" if o else None)
            if target:
                reasons.append(f"Targets you: " + _pick_reason(self.us, c.id, self.us_name))
            out.append(Suggestion(c, score, [r for r in reasons if r]))
        return out

    def _their_pick(self):
        opp_id = self.plan.opponent_id
        open_roles = self.state.open_roles(opp_id)
        blocked = self.state.unavailable(opp_id)
        late_games, late_picks = _late_picks(self.opp)
        is_late = self.state.game.number >= LATE_GAME
        out = []
        for c in self.state.champions:
            if c.id in blocked or self.opp.picks[c.id] == 0:
                continue
            if c.roles and not any(r in open_roles for r in c.roles):
                continue
            score = smoothed(self.opp.picks[c.id], self.opp.pick_opportunities(c.id))
            reasons = [_pick_reason(self.opp, c.id, self.opp_name)]
            if is_late and late_picks[c.id]:
                score *= 1.25
                reasons.append(f"Picked {late_picks[c.id]}x across {late_games} Fearless Game {LATE_GAME}+ games")
            roles = [r for r in c.roles if r in open_roles]
            if roles:
                reasons.append(f"Fills {roles[0]} for them")
            out.append(Suggestion(c, score, [r for r in reasons if r]))
        return out
