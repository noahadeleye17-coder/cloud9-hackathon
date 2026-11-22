"""Draft state for a planned game: next step, locks, remaining pools, role coverage."""

from dataclasses import dataclass, field

from django.db import transaction

from esports import draft_rules as rules
from esports.models import Champion

from .models import DraftPlan, PlannedAction, PlannedGame


def assign_roles(picks: list[PlannedAction]) -> dict[int, str | None]:
    """
    Map pick action id -> role. Uses the coach's explicit role when set, otherwise
    the champion's first listed role that the team hasn't filled yet.
    """
    filled: set[str] = set()
    out = {}
    for a in sorted(picks, key=lambda x: (x.role == "", x.order)):  # explicit roles claim first
        if a.role:
            role = a.role
        else:
            role = next((r for r in a.champion.roles if r not in filled), None)
        if role:
            filled.add(role)
        out[a.id] = role
    return out


@dataclass
class GameState:
    plan: DraftPlan
    game: PlannedGame
    actions: list  # PlannedAction for this game, ordered
    prior_picks: list  # (team_id, champion_id) from earlier games of the plan
    champions: list = field(default_factory=list)

    @property
    def next_step(self) -> rules.Step | None:
        n = len(self.actions) + 1
        return rules.step(n) if n <= rules.TOTAL_STEPS else None

    @property
    def complete(self):
        return self.next_step is None

    @property
    def taken(self) -> set[int]:
        return {a.champion_id for a in self.actions}

    def acting_team_id(self):
        s = self.next_step
        return self.game.team_id_on(s.side) if s else None

    def is_our_turn(self):
        return self.acting_team_id() == self.plan.our_team_id

    def picks_of(self, team_id):
        return [a for a in self.actions if a.step.action == rules.PICK and self.game.team_id_on(a.step.side) == team_id]

    def bans_of(self, team_id):
        return [a for a in self.actions if a.step.action == rules.BAN and self.game.team_id_on(a.step.side) == team_id]

    def filled_roles(self, team_id) -> dict[str, PlannedAction]:
        picks = self.picks_of(team_id)
        roles = assign_roles(picks)
        return {roles[a.id]: a for a in picks if roles[a.id]}

    def open_roles(self, team_id) -> list[str]:
        filled = self.filled_roles(team_id)
        return [r for r in rules.ROLES if r not in filled]

    def fearless_locked(self, team_id) -> set[int]:
        return rules.fearless_locked(self.prior_picks, team_id, self.plan.fearless_mode)

    def unavailable(self, team_id) -> set[int]:
        return rules.unavailable_for(team_id, self.prior_picks, self.taken, self.plan.fearless_mode)

    def pool(self, team_id) -> dict[str, list[Champion]]:
        """Champions team_id can still pick, grouped by role (a flex champ appears under each role)."""
        blocked = self.unavailable(team_id)
        out = {r: [] for r in rules.ROLES}
        out["other"] = []
        for c in self.champions:
            if c.id in blocked:
                continue
            if not c.roles:
                out["other"].append(c)
            for r in c.roles:
                if r in out:
                    out[r].append(c)
        return out


def game_state(plan: DraftPlan, number: int) -> GameState:
    game = plan.games.get(number=number)
    actions = list(game.actions.select_related("champion").order_by("order"))
    prior_picks = []
    for g in plan.games.filter(number__lt=number).prefetch_related("actions"):
        for a in g.actions.all():
            if a.step.action == rules.PICK:
                prior_picks.append((g.team_id_on(a.step.side), a.champion_id))
    return GameState(plan=plan, game=game, actions=actions, prior_picks=prior_picks, champions=list(Champion.objects.all()))


@transaction.atomic
def apply_action(plan: DraftPlan, number: int, champion: Champion, role: str = "") -> PlannedAction:
    state = game_state(plan, number)
    s = state.next_step
    if s is None:
        raise rules.DraftError("This game's draft is already complete.")
    if role and role not in rules.ROLES:
        raise rules.DraftError(f"Unknown role {role!r}.")
    if s.action == rules.BAN:
        role = ""
    rules.validate_action(
        s.order, state.acting_team_id(), champion.id, state.prior_picks, state.taken, plan.fearless_mode
    )
    action = PlannedAction.objects.create(game=state.game, order=s.order, champion=champion, role=role)
    plan.save(update_fields=["updated_at"])
    return action


@transaction.atomic
def undo_last(plan: DraftPlan, number: int) -> PlannedAction | None:
    game = plan.games.get(number=number)
    last = game.actions.order_by("-order").first()
    if last:
        last.delete()
        plan.save(update_fields=["updated_at"])
    return last


def series_conflicts(plan: DraftPlan) -> list[dict]:
    """
    Picks in later games that are illegal given earlier games, e.g. the coach went
    back and changed Game 1 after planning Game 2. Reported, not auto-deleted.
    """
    conflicts = []
    prior: list[rules.PriorPick] = []
    for g in plan.games.prefetch_related("actions__champion").order_by("number"):
        for a in g.actions.all():
            if a.step.action != rules.PICK:
                continue
            team_id = g.team_id_on(a.step.side)
            if a.champion_id in rules.fearless_locked(prior, team_id, plan.fearless_mode):
                conflicts.append({"game": g.number, "order": a.order, "champion": a.champion.name})
        prior.extend(
            (g.team_id_on(a.step.side), a.champion_id) for a in g.actions.all() if a.step.action == rules.PICK
        )
    return conflicts
