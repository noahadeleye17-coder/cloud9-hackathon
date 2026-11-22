from datetime import date

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from esports import draft_rules as rules
from esports.models import Champion, Series, Team
from esports.tests import make_game

from . import state as draft_state
from .export import plan_to_markdown
from .models import DraftPlan
from .suggestions import SuggestionEngine


class PlannerBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.us = Team.objects.create(name="Cloud9", short_code="C9")
        cls.them = Team.objects.create(name="Team Liquid", short_code="TL")
        roles = list(rules.ROLES)
        cls.champs = [
            Champion.objects.create(key=f"C{i}", name=f"Champ{i:02d}", roles=[roles[i % 5]]) for i in range(80)
        ]
        # Historical data: TL picks Champ40 a lot.
        for n in range(4):
            s = Series.objects.create(team_a=cls.us, team_b=cls.them, format="bo3",
                                      fearless_mode=rules.FEARLESS_OFF, date=date(2026, 10, 1 + n), patch="26.20")
            draft = cls.champs[0:6] + [cls.champs[45], cls.champs[40]] + cls.champs[46:58]
            make_game(s, 1, cls.us, cls.them, cls.us, draft)

    def make_plan(self, **kw):
        kw = {"name": "Test", "our_team": self.us, "opponent": self.them, "format": "bo3", **kw}
        return DraftPlan.objects.create(**kw)


class StateTests(PlannerBase):
    def test_plan_creates_games_with_alternating_sides(self):
        plan = self.make_plan(format="bo5")
        self.assertEqual(list(plan.games.values_list("our_side", flat=True)), ["blue", "red", "blue", "red", "blue"])

    def test_apply_follows_draft_order_and_blocks_duplicates(self):
        plan = self.make_plan()
        a = draft_state.apply_action(plan, 1, self.champs[0])
        self.assertEqual(a.order, 1)
        self.assertEqual(a.step.action, rules.BAN)
        with self.assertRaises(rules.DraftError):
            draft_state.apply_action(plan, 1, self.champs[0])

    def full_game(self, plan, number, champs):
        for c in champs:
            draft_state.apply_action(plan, number, c)

    def test_hard_fearless_locks_next_game(self):
        plan = self.make_plan()
        self.full_game(plan, 1, self.champs[0:20])
        picked_g1 = self.champs[6]  # step 7 = pick
        st = draft_state.game_state(plan, 2)
        self.assertIn(picked_g1.id, st.fearless_locked(self.us.id))
        self.assertIn(picked_g1.id, st.fearless_locked(self.them.id))
        self.assertNotIn(self.champs[0].id, st.fearless_locked(self.us.id))  # bans don't carry
        # Move G2 to the pick phase, then try a locked champ.
        for c in self.champs[20:26]:
            draft_state.apply_action(plan, 2, c)
        with self.assertRaises(rules.DraftError):
            draft_state.apply_action(plan, 2, picked_g1)

    def test_soft_fearless_only_locks_own_picks(self):
        plan = self.make_plan(fearless_mode=rules.FEARLESS_SOFT)
        self.full_game(plan, 1, self.champs[0:20])
        st = draft_state.game_state(plan, 2)
        us_pick = self.champs[6]  # G1 step 7: blue = us
        them_pick = self.champs[7]  # G1 step 8: red = them
        self.assertIn(us_pick.id, st.fearless_locked(self.us.id))
        self.assertNotIn(us_pick.id, st.fearless_locked(self.them.id))
        self.assertIn(them_pick.id, st.fearless_locked(self.them.id))

    def test_complete_game_rejects_more(self):
        plan = self.make_plan()
        self.full_game(plan, 1, self.champs[0:20])
        self.assertTrue(draft_state.game_state(plan, 1).complete)
        with self.assertRaises(rules.DraftError):
            draft_state.apply_action(plan, 1, self.champs[30])

    def test_undo_and_open_roles(self):
        plan = self.make_plan()
        self.full_game(plan, 1, self.champs[0:7])  # 6 bans + blue pick 1 (Champ06 -> role index 1 = jungle)
        st = draft_state.game_state(plan, 1)
        self.assertNotIn("jungle", st.open_roles(self.us.id))
        draft_state.undo_last(plan, 1)
        self.assertIn("jungle", draft_state.game_state(plan, 1).open_roles(self.us.id))

    def test_conflicts_after_editing_earlier_game(self):
        plan = self.make_plan()
        self.full_game(plan, 2, self.champs[0:7])  # plan G2 first: our pick Champ06
        self.full_game(plan, 1, self.champs[20:26] + [self.champs[6]])  # then use Champ06 in G1
        conflicts = draft_state.series_conflicts(plan)
        self.assertEqual(conflicts, [{"game": 2, "order": 7, "champion": "Champ06"}])


class SuggestionTests(PlannerBase):
    def test_our_ban_targets_opponent_favourite_with_reason(self):
        plan = self.make_plan()
        result = SuggestionEngine(draft_state.game_state(plan, 1)).suggest()
        self.assertEqual(result["mode"], "our_ban")
        top = result["suggestions"][0]
        self.assertEqual(top["champion"]["name"], "Champ40")
        self.assertTrue(any("4 of 4" in r for r in top["reasons"]))

    def test_suggestions_skip_locked_champions(self):
        plan = self.make_plan()
        # G1: get to red's first pick (step 8) and have TL pick Champ40.
        for c in self.champs[0:7]:
            draft_state.apply_action(plan, 1, c)
        draft_state.apply_action(plan, 1, self.champs[40])
        names = [s["champion"]["name"] for s in SuggestionEngine(draft_state.game_state(plan, 2)).suggest()["suggestions"]]
        self.assertNotIn("Champ40", names)

    def test_opponent_turn_is_predicted(self):
        plan = self.make_plan()
        draft_state.apply_action(plan, 1, self.champs[0])  # step 2 is red = TL ban
        result = SuggestionEngine(draft_state.game_state(plan, 1)).suggest()
        self.assertEqual(result["mode"], "their_ban")
        self.assertTrue(result["predicted"])


class ApiTests(PlannerBase):
    def setUp(self):
        self.client = APIClient()

    def test_full_api_flow(self):
        r = self.client.post("/api/draft-plans/", {"name": "API", "our_team": self.us.id, "opponent": self.them.id,
                                                   "format": "bo3", "fearless_mode": "hard"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        pid = r.json()["id"]
        self.assertEqual(len(r.json()["games"]), 3)

        r = self.client.post(f"/api/draft-plans/{pid}/actions/", {"game": 1, "champion": self.champs[0].id}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()["label"], "Blue ban 1")

        r = self.client.post(f"/api/draft-plans/{pid}/actions/", {"game": 1, "champion": self.champs[0].id}, format="json")
        self.assertEqual(r.status_code, 409)

        r = self.client.get(f"/api/draft-plans/{pid}/pool/?game=1")
        self.assertEqual(r.json()["next_step"]["label"], "Red ban 1")

        r = self.client.get(f"/api/draft-plans/{pid}/suggestions/?game=1")
        self.assertEqual(r.status_code, 200)
        self.assertIn("suggestions", r.json())

        r = self.client.delete(f"/api/draft-plans/{pid}/actions/?game=1")
        self.assertEqual(r.json()["undone"], 1)

        r = self.client.patch(f"/api/draft-plans/{pid}/games/2/", {"our_side": "blue", "notes": "x"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)

        r = self.client.get(f"/api/draft-plans/{pid}/export/?as=markdown")
        self.assertIn(b"# API", r.content)

    def test_same_team_rejected(self):
        r = self.client.post("/api/draft-plans/", {"name": "x", "our_team": self.us.id, "opponent": self.us.id,
                                                   "format": "bo3"}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_tendencies_endpoint(self):
        r = self.client.get(f"/api/teams/{self.them.id}/tendencies/?patch=26.20")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["games"], 4)
        r = self.client.get(f"/api/teams/{self.them.id}/tendencies/?from=bad")
        self.assertEqual(r.status_code, 400)


class PageTests(PlannerBase):
    def test_pages_render(self):
        plan = self.make_plan()
        for url in [
            reverse("planner:home"),
            reverse("planner:scouting"),
            reverse("planner:scouting") + f"?team={self.them.id}",
            reverse("planner:plans"),
            reverse("planner:board", args=[plan.id, 1]),
        ]:
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_board_actions_via_htmx(self):
        plan = self.make_plan()
        url = reverse("planner:board_act", args=[plan.id, 1])
        r = self.client.post(url, {"champion": self.champs[3].id})
        self.assertContains(r, "Red ban 1")
        r = self.client.post(url, {"champion": self.champs[3].id})
        self.assertContains(r, "already picked or banned")
        self.assertContains(self.client.post(reverse("planner:board_undo", args=[plan.id, 1])), "Blue ban 1")

    def test_markdown_export(self):
        plan = self.make_plan()
        draft_state.apply_action(plan, 1, self.champs[0])
        md = plan_to_markdown(plan)
        self.assertIn("## Game 1", md)
        self.assertIn("Champ00", md)
