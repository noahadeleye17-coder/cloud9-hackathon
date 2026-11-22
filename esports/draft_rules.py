"""
Pro draft rules: the 20-step pick/ban order and Fearless lockouts.

Pure Python, no database access, so the simulator, the suggestion engine and
the tendency analysis all share one source of truth.
"""

from dataclasses import dataclass
from typing import Iterable

BLUE = "blue"
RED = "red"
SIDES = (BLUE, RED)

PICK = "pick"
BAN = "ban"

ROLES = ("top", "jungle", "mid", "bot", "support")

# Fearless variants.
#   hard: a champion picked by EITHER team in an earlier game is locked for BOTH
#         teams for the rest of the series (LCK/LPL/LEC/LTA style since 2025).
#   soft: a team can't re-pick a champion IT played earlier; the other team still can.
#   off:  normal draft, nothing carries between games.
# Bans never carry over between games in any variant.
FEARLESS_HARD = "hard"
FEARLESS_SOFT = "soft"
FEARLESS_OFF = "off"
FEARLESS_MODES = (FEARLESS_HARD, FEARLESS_SOFT, FEARLESS_OFF)


@dataclass(frozen=True)
class Step:
    order: int  # 1..20
    side: str  # BLUE / RED
    action: str  # PICK / BAN
    phase: int  # 1 = first ban+pick phase, 2 = second

    @property
    def label(self) -> str:
        """Human label like 'Blue ban 1' or 'Red pick 3' (per-side counter)."""
        n = sum(1 for s in DRAFT_ORDER[: self.order] if s.side == self.side and s.action == self.action)
        return f"{self.side.title()} {self.action} {n}"


def _build_order():
    B, R = BLUE, RED
    seq = [
        # Phase 1 bans: alternate, blue first.
        (B, BAN, 1), (R, BAN, 1), (B, BAN, 1), (R, BAN, 1), (B, BAN, 1), (R, BAN, 1),
        # Phase 1 picks: B1, R1 R2, B2 B3, R3.
        (B, PICK, 1), (R, PICK, 1), (R, PICK, 1), (B, PICK, 1), (B, PICK, 1), (R, PICK, 1),
        # Phase 2 bans: alternate, red first.
        (R, BAN, 2), (B, BAN, 2), (R, BAN, 2), (B, BAN, 2),
        # Phase 2 picks: R4, B4 B5, R5.
        (R, PICK, 2), (B, PICK, 2), (B, PICK, 2), (R, PICK, 2),
    ]
    return tuple(Step(i + 1, side, action, phase) for i, (side, action, phase) in enumerate(seq))


DRAFT_ORDER: tuple[Step, ...] = _build_order()
TOTAL_STEPS = len(DRAFT_ORDER)  # 20


def step(order: int) -> Step:
    if not 1 <= order <= TOTAL_STEPS:
        raise ValueError(f"Draft step must be 1..{TOTAL_STEPS}, got {order}")
    return DRAFT_ORDER[order - 1]


def series_length(fmt: str) -> int:
    return {"bo1": 1, "bo3": 3, "bo5": 5}[fmt]


def wins_needed(fmt: str) -> int:
    return series_length(fmt) // 2 + 1


# --- Fearless lockouts ---------------------------------------------------

# A prior pick is (team_id, champion_id).
PriorPick = tuple[int, int]


def fearless_locked(prior_picks: Iterable[PriorPick], team_id: int, mode: str) -> set[int]:
    """Champions `team_id` cannot pick because of earlier games in the series."""
    if mode == FEARLESS_OFF:
        return set()
    if mode == FEARLESS_HARD:
        return {champ for _team, champ in prior_picks}
    if mode == FEARLESS_SOFT:
        return {champ for team, champ in prior_picks if team == team_id}
    raise ValueError(f"Unknown fearless mode {mode!r}")


def unavailable_for(
    team_id: int,
    prior_picks: Iterable[PriorPick],
    current_game_champions: Iterable[int],
    mode: str,
) -> set[int]:
    """
    Champions `team_id` can't pick right now: Fearless-locked from earlier
    games, plus anything already picked or banned in the current game.
    """
    return fearless_locked(prior_picks, team_id, mode) | set(current_game_champions)


def unbannable(current_game_champions: Iterable[int]) -> set[int]:
    """
    Champions that can't be banned right now: anything already picked or banned
    in this game. Banning a Fearless-locked champion is allowed (but wasted),
    so the simulator warns about it rather than blocking it.
    """
    return set(current_game_champions)


class DraftError(ValueError):
    pass


def validate_action(
    order: int,
    acting_team_id: int,
    champion_id: int,
    prior_picks: Iterable[PriorPick],
    current_game_champions: Iterable[int],
    mode: str,
) -> Step:
    """Raise DraftError if the action is illegal; return the Step otherwise."""
    s = step(order)
    current = set(current_game_champions)
    if champion_id in current:
        raise DraftError("Champion already picked or banned in this game.")
    if s.action == PICK and champion_id in fearless_locked(prior_picks, acting_team_id, mode):
        raise DraftError("Champion is locked out by Fearless (played earlier in this series).")
    return s
