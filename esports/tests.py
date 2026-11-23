from datetime import date

from django.test import SimpleTestCase, TestCase

from . import draft_rules as rules
from .models import Champion, DraftAction, Game, Series, Team
from .tendencies import Filters, _ranked_shifts, _shift_row, compute_team_stats, team_tendencies


class DraftOrderTests(SimpleTestCase):
    def test_twenty_steps_ten_each(self):
        self.assertEqual(rules.TOTAL_STEPS, 20)
        for side in rules.SIDES:
            mine = [s for s in rules.DRAFT_ORDER if s.side == side]
            self.assertEqual(sum(s.action == rules.BAN for s in mine), 5)
            self.assertEqual(sum(s.action == rules.PICK for s in mine), 5)

    def test_pro_sequence(self):
        compact = "".join(f"{s.side[0]}{s.action[0]}" for s in rules.DRAFT_ORDER)
        self.assertEqual(
            compact,
            "bbrbbbrbbbrb"  # phase 1 bans: B R B R B R
            "bprprpbpbprp"  # phase 1 picks: B R R B B R
            "rbbbrbbb"  # phase 2 bans: R B R B
            "rpbpbprp",  # phase 2 picks: R B B R
        )

    def test_labels(self):
        self.assertEqual(rules.step(7).label, "Blue pick 1")
        self.assertEqual(rules.step(9).label, "Red pick 2")
        self.assertEqual(rules.step(13).label, "Red ban 4")
        self.assertEqual(rules.step(20).label, "Red pick 5")

    def test_step_bounds(self):
        with self.assertRaises(ValueError):
            rules.step(0)
        with self.assertRaises(ValueError):
            rules.step(21)


class FearlessRuleTests(SimpleTestCase):
    prior = [(1, 100), (2, 200)]  # team 1 played champ 100, team 2 played champ 200

    def test_hard_locks_both_teams(self):
        self.assertEqual(rules.fearless_locked(self.prior, 1, rules.FEARLESS_HARD), {100, 200})
        self.assertEqual(rules.fearless_locked(self.prior, 2, rules.FEARLESS_HARD), {100, 200})

    def test_soft_locks_own_picks_only(self):
        self.assertEqual(rules.fearless_locked(self.prior, 1, rules.FEARLESS_SOFT), {100})
        self.assertEqual(rules.fearless_locked(self.prior, 2, rules.FEARLESS_SOFT), {200})

    def test_off(self):
        self.assertEqual(rules.fearless_locked(self.prior, 1, rules.FEARLESS_OFF), set())

    def test_validate_rejects_locked_pick_but_allows_ban(self):
        with self.assertRaises(rules.DraftError):
            rules.validate_action(7, 1, 200, self.prior, set(), rules.FEARLESS_HARD)
        rules.validate_action(1, 1, 200, self.prior, set(), rules.FEARLESS_HARD)  # ban: allowed (wasted)

    def test_validate_rejects_duplicate_in_game(self):
        with self.assertRaises(rules.DraftError):
            rules.validate_action(2, 2, 5, [], {5}, rules.FEARLESS_HARD)

    def test_wins_needed(self):
        self.assertEqual(rules.wins_needed("bo3"), 2)
        self.assertEqual(rules.wins_needed("bo5"), 3)


def make_game(series, number, blue, red, winner, actions):
    """actions: list of champion objects in step order (len <= 20)."""
    g = Game.objects.create(series=series, number=number, blue_team=blue, red_team=red, winner=winner, patch=series.patch)
    for s, champ in zip(rules.DRAFT_ORDER, actions):
        team = blue if s.side == rules.BLUE else red
        DraftAction.objects.create(
            game=g, order=s.order, side=s.side, team=team, action_type=s.action, champion=champ,
            role=(champ.roles[0] if s.action == rules.PICK and champ.roles else ""),
        )
    return g


class TendencyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.a = Team.objects.create(name="Alpha", short_code="A")
        cls.b = Team.objects.create(name="Bravo", short_code="B")
        cls.champs = [Champion.objects.create(key=f"C{i}", name=f"Champ{i:02d}", roles=["mid"]) for i in range(50)]

    def series(self, **kw):
        defaults = dict(team_a=self.a, team_b=self.b, format="bo3", fearless_mode=rules.FEARLESS_HARD,
                        date=date(2026, 10, 1), patch="26.20")
        defaults.update(kw)
        return Series.objects.create(**defaults)

    def test_pick_opportunities_exclude_fearless_locked(self):
        c = self.champs
        s = self.series()
        # G1: A (blue) picks c[10] at step 7.
        g1 = c[0:6] + [c[10]] + c[11:24]
        make_game(s, 1, self.a, self.b, self.a, g1)
        # G2: c[10] is hard-locked for A. Different champs entirely.
        g2 = c[24:44]
        make_game(s, 2, self.a, self.b, self.a, g2)

        stats = compute_team_stats(self.a)
        self.assertEqual(stats.games, 2)
        self.assertEqual(stats.picks[c[10].id], 1)
        # Available only in G1 (locked in G2) -> 1 of 1, i.e. 100%, not 50%.
        self.assertEqual(stats.pick_opportunities(c[10].id), 1)
        self.assertEqual(stats.pick_rate(c[10].id), 1.0)
        # Blue first pick tracked.
        self.assertEqual(stats.blue_first_pick[c[10].id], 1)
        # Game-number buckets for Fearless analysis.
        self.assertEqual(stats.by_game_number[1]["games"], 1)
        self.assertEqual(stats.by_game_number[2]["games"], 1)

    def test_banned_champion_not_an_opportunity(self):
        c = self.champs
        s = self.series(fearless_mode=rules.FEARLESS_OFF)
        make_game(s, 1, self.a, self.b, self.b, c[0:20])  # c[0] banned by A at step 1
        stats = compute_team_stats(self.a)
        self.assertEqual(stats.pick_opportunities(c[0].id), 0)
        self.assertEqual(stats.bans[c[0].id], 1)
        self.assertEqual(stats.record[rules.BLUE], {"games": 1, "wins": 0})

    def test_filters_and_report_shape(self):
        c = self.champs
        make_game(self.series(patch="26.20"), 1, self.a, self.b, self.a, c[0:20])
        make_game(self.series(patch="26.21"), 1, self.b, self.a, self.a, c[20:40])
        self.assertEqual(compute_team_stats(self.a, Filters(patch="26.21")).games, 1)
        report = team_tendencies(self.a)
        self.assertEqual(report["games"], 2)
        self.assertTrue(report["picks"])
        self.assertIn("rate", report["picks"][0])
        self.assertEqual(set(report["by_role"]), set(rules.ROLES))

    def test_late_series_shift_measures_against_availability(self):
        """Game 1 vs Game 3+: risers, fallers and ban shifts use availability, not raw counts.

        In every game Alpha is blue, so Alpha's first pick (step 7) is list index 6 and
        Alpha's first ban (step 1) is index 0.
        """
        c = self.champs
        for _ in range(2):  # series 1-2: Alpha first-picks c[6] in G1, c[26] in G3
            s = self.series(format="bo5")
            make_game(s, 1, self.a, self.b, self.b, c[0:20])
            make_game(s, 3, self.a, self.b, self.a, c[20:40])
        for _ in range(2):  # series 3-4: c[6] never shows up, so it's available and passed over
            s = self.series(format="bo5")
            make_game(s, 1, self.a, self.b, self.b, c[40:50] + c[10:20])
            make_game(s, 3, self.a, self.b, self.a, c[20:40])

        stats = compute_team_stats(self.a)
        early, late = stats.phases["early"], stats.phases["late"]
        self.assertEqual((early.games, late.games), (4, 4))
        # c[6] is hard-locked in G3 of series 1-2, so only series 3-4 count as late opportunities.
        self.assertEqual(early.picks[c[6].id], 2)
        self.assertEqual(late.pick_opportunities(c[6].id), 2)

        ls = team_tendencies(self.a)["late_series"]
        name = lambda rows: [r["champion"]["name"] for r in rows]
        self.assertEqual((ls["early"]["wins"], ls["early"]["losses"]), (0, 4))
        self.assertEqual((ls["late"]["wins"], ls["late"]["losses"]), (4, 0))
        self.assertIn(c[26].name, name(ls["risers"]))
        riser = next(r for r in ls["risers"] if r["champion"]["name"] == c[26].name)
        self.assertEqual(riser["early"], {"count": 0, "opportunities": 4})
        self.assertEqual(riser["late"], {"count": 4, "opportunities": 4})
        self.assertIn(c[6].name, name(ls["fallers"]))
        self.assertIn(c[20].name, name(ls["ban_risers"]))  # Alpha's first ban in every G3
        self.assertTrue(ls["low_sample"])

    def test_non_fearless_series_excluded_from_late_series(self):
        c = self.champs
        s = self.series(format="bo5", fearless_mode=rules.FEARLESS_OFF)
        make_game(s, 1, self.a, self.b, self.a, c[0:20])
        make_game(s, 3, self.a, self.b, self.a, c[0:20])
        stats = compute_team_stats(self.a)
        self.assertEqual(stats.phases["early"].games + stats.phases["late"].games, 0)


class ShiftRankingTests(SimpleTestCase):
    def test_small_late_sample_at_full_rate_is_not_a_drop(self):
        champ = Champion(id=1, key="Lux", name="Lux", roles=["mid"])
        # 13 of 21 early (62%) vs 2 of 2 late (100%): smoothing alone says "down", raw rates say "up".
        row = _shift_row(champ, 13, 21, 2, 2)
        self.assertLess(row["shift"], 0)
        self.assertEqual(_ranked_shifts([row], rising=False, top=5), [])
        self.assertEqual(_ranked_shifts([row], rising=True, top=5), [])
