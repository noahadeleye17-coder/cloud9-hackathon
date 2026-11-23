"""
Synthetic pro-draft generator, for building and demoing before GRID data arrives.

Every series is tagged source=synthetic and the UI shows a banner while any
synthetic data is loaded. Swap in real data via an importer; nothing else changes.

Each team gets a "personality" so tendency analysis has real signal to find:
  - a weighted comfort pool per role,
  - signature first picks it grabs early,
  - pocket picks it saves for Fearless Game 3+,
  - ban habits (meta threats + the opponent's comfort picks).
"""

import random
from dataclasses import dataclass, field
from datetime import date, timedelta

from django.db import transaction

from . import draft_rules as rules
from .models import Champion, DataSource, DraftAction, Game, Series, Team

DEMO_TEAMS = [
    ("Cloud9", "C9", 1.10),
    ("Team Liquid", "TL", 1.05),
    ("FlyQuest", "FLY", 1.08),
    ("100 Thieves", "100", 0.95),
    ("Shopify Rebellion", "SR", 0.92),
    ("Dignitas", "DIG", 0.90),
    ("LYON", "LYON", 0.97),
    ("Disguised", "DSG", 0.85),
]
PATCHES = ["26.19", "26.20", "26.21", "26.22"]

# Role order a team fills its picks in (rough pro heuristics: flex/power roles early).
PICK_ROLE_PRIORITY = ["jungle", "mid", "bot", "support", "top"]


@dataclass
class Personality:
    strength: float
    comfort: dict = field(default_factory=dict)  # role -> {champ_id: weight}
    signature: list = field(default_factory=list)  # champ ids they love to first-pick
    pocket: list = field(default_factory=list)  # champ ids boosted in G3+
    ban_bias: dict = field(default_factory=dict)  # champ_id -> weight


def _weighted_choice(rng, weights: dict):
    items = [(k, w) for k, w in weights.items() if w > 0]
    if not items:
        return None
    total = sum(w for _, w in items)
    r = rng.uniform(0, total)
    upto = 0.0
    for k, w in items:
        upto += w
        if upto >= r:
            return k
    return items[-1][0]


class Generator:
    def __init__(self, seed=9):
        self.rng = random.Random(seed)
        self.champs = list(Champion.objects.exclude(roles=[]))
        if len(self.champs) < 40:
            raise RuntimeError("Load champions first: manage.py load_champions")
        self.by_role = {r: [c for c in self.champs if r in c.roles] for r in rules.ROLES}
        self.role_of = {c.id: c.roles for c in self.champs}
        # Global "meta strength" per champion.
        self.meta = {c.id: self.rng.paretovariate(1.6) for c in self.champs}

    def personality(self, strength) -> Personality:
        p = Personality(strength=strength)
        for role, pool in self.by_role.items():
            picks = self.rng.sample(pool, k=min(len(pool), self.rng.randint(5, 7)))
            # Steep weights: a team's top couple of champs per role dominate.
            p.comfort[role] = {c.id: self.meta[c.id] * (3.0 / (i + 1)) for i, c in enumerate(picks)}
        top_comfort = sorted(
            ((cid, w) for pool in p.comfort.values() for cid, w in pool.items()), key=lambda x: -x[1]
        )
        p.signature = [cid for cid, _ in top_comfort[:3]]
        rest = [cid for pool in p.comfort.values() for cid in pool if cid not in p.signature]
        p.pocket = self.rng.sample(rest, k=min(2, len(rest)))
        p.ban_bias = {c.id: self.meta[c.id] for c in self.champs}
        return p

    # --- one draft ---

    def draft(self, blue, red, pers, game_number, prior_picks, mode):
        """Return list of (order, side, team, action, champ_id, role)."""
        taken: set[int] = set()
        filled = {blue.id: set(), red.id: set()}
        out = []
        for s in rules.DRAFT_ORDER:
            team = blue if s.side == rules.BLUE else red
            opp = red if team is blue else blue
            p, op = pers[team.id], pers[opp.id]
            if s.action == rules.BAN:
                locked_for_opp = rules.fearless_locked(prior_picks, opp.id, mode)
                weights = {}
                for cid, w in p.ban_bias.items():
                    if cid in taken or cid in locked_for_opp:
                        continue
                    # Target what the opponent likes, in roles they haven't filled.
                    opp_like = sum(pool.get(cid, 0) for r, pool in op.comfort.items() if r not in filled[opp.id])
                    if cid in op.signature and s.phase == 1:
                        opp_like *= 2.5
                    weights[cid] = (w * 0.6 + opp_like * 1.4) ** 2
                champ = _weighted_choice(self.rng, weights)
                if champ is None:  # ban list exhausted -> ban any champion the opponent could still pick
                    champ = _weighted_choice(
                        self.rng, {c.id: 1 for c in self.champs if c.id not in taken and c.id not in locked_for_opp}
                    )
                if champ is None:
                    raise RuntimeError("No champions left to ban; load more champions before seeding.")
                role = ""
            else:
                unavailable = rules.unavailable_for(team.id, prior_picks, taken, mode)
                open_roles = [r for r in PICK_ROLE_PRIORITY if r not in filled[team.id]]
                # Mostly follow the priority order, sometimes flex.
                role = open_roles[0] if self.rng.random() < 0.55 else self.rng.choice(open_roles)
                weights = {cid: w for cid, w in p.comfort[role].items() if cid not in unavailable}
                for cid in weights:
                    if s.order == 7 and cid in p.signature:
                        weights[cid] *= 4
                    if game_number >= 3 and cid in p.pocket:
                        weights[cid] *= 6
                    weights[cid] = weights[cid] ** 1.5
                champ = _weighted_choice(self.rng, weights)
                if champ is None:  # comfort pool exhausted (late Fearless) -> fall back to meta
                    champ = _weighted_choice(
                        self.rng, {c.id: self.meta[c.id] for c in self.by_role[role] if c.id not in unavailable}
                    )
                if champ is None:
                    champ = _weighted_choice(
                        self.rng, {c.id: 1 for c in self.champs if c.id not in unavailable}
                    )
                if champ is None:
                    raise RuntimeError("No champions left to pick; load more champions before seeding.")
                filled[team.id].add(role)
            taken.add(champ)
            out.append((s.order, s.side, team, s.action, champ, role))
        return out

    # --- whole dataset ---

    @transaction.atomic
    def generate(self, n_series=70, days=120, today=None):
        today = today or date.today()
        teams = [
            Team.objects.get_or_create(short_code=code, defaults={"name": name, "region": "LTA North"})[0]
            for name, code, _ in DEMO_TEAMS
        ]
        strength = {code: s for _, code, s in DEMO_TEAMS}
        pers = {t.id: self.personality(strength[t.short_code]) for t in teams}

        actions = []
        for i in range(n_series):
            a, b = self.rng.sample(teams, 2)
            day = today - timedelta(days=int(days * (1 - i / n_series)))
            patch = PATCHES[min(len(PATCHES) - 1, int(len(PATCHES) * i / n_series))]
            fmt = "bo5" if self.rng.random() < 0.3 else "bo3"
            series = Series.objects.create(
                team_a=a, team_b=b, format=fmt, fearless_mode=rules.FEARLESS_HARD,
                date=day, patch=patch, league="LTA North (synthetic)", source=DataSource.SYNTHETIC,
            )
            wins = {a.id: 0, b.id: 0}
            prior_picks = []
            blue, red = (a, b) if self.rng.random() < 0.5 else (b, a)
            number = 0
            while max(wins.values()) < rules.wins_needed(fmt):
                number += 1
                pa, pb = pers[blue.id].strength, pers[red.id].strength * 0.96  # small blue-side edge
                winner = blue if self.rng.random() < pa / (pa + pb) else red
                wins[winner.id] += 1
                game = Game.objects.create(
                    series=series, number=number, blue_team=blue, red_team=red, winner=winner, patch=patch
                )
                for order, side, team, action, champ, role in self.draft(
                    blue, red, pers, number, prior_picks, series.fearless_mode
                ):
                    actions.append(
                        DraftAction(game=game, order=order, side=side, team=team, action_type=action,
                                    champion_id=champ, role=role)
                    )
                    if action == rules.PICK:
                        prior_picks.append((team.id, champ))
                # Loser usually takes blue side next game.
                loser = red if winner is blue else blue
                other = winner
                blue, red = (loser, other) if self.rng.random() < 0.8 else (other, loser)
        DraftAction.objects.bulk_create(actions)
        return {"teams": len(teams), "series": n_series, "actions": len(actions)}
