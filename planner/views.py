from django import forms
from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from esports import draft_rules as rules
from esports.models import Champion, DataSource, Game, Series, Team
from esports.tendencies import Filters, team_tendencies

from . import state as draft_state
from .export import plan_to_markdown
from .models import DraftPlan
from .suggestions import SuggestionEngine


def _common():
    return {"has_synthetic": Series.objects.filter(source=DataSource.SYNTHETIC).exists()}


def home(request):
    return render(
        request,
        "planner/home.html",
        {
            **_common(),
            "teams": Team.objects.count(),
            "games": Game.objects.count(),
            "plans": DraftPlan.objects.select_related("our_team", "opponent")[:5],
        },
    )


# --- Scouting ---------------------------------------------------------------


def scouting(request):
    teams = Team.objects.all()
    patches = Game.objects.order_by("-patch").values_list("patch", flat=True).distinct()
    team = None
    report = None
    if team_id := request.GET.get("team"):
        team = get_object_or_404(Team, pk=team_id)
        f = Filters(
            patch=request.GET.get("patch") or None,
            date_from=parse_date(request.GET.get("from") or "") if request.GET.get("from") else None,
            date_to=parse_date(request.GET.get("to") or "") if request.GET.get("to") else None,
        )
        report = team_tendencies(team, f)
    ctx = {**_common(), "teams": teams, "patches": patches, "team": team, "report": report, "q": request.GET}
    template = "planner/_scouting_report.html" if request.headers.get("HX-Request") else "planner/scouting.html"
    return render(request, template, ctx)


# --- Plans ------------------------------------------------------------------


class PlanForm(forms.ModelForm):
    class Meta:
        model = DraftPlan
        fields = ["name", "our_team", "opponent", "format", "fearless_mode", "scout_patch", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 3})}

    def clean(self):
        data = super().clean()
        if data.get("our_team") and data.get("our_team") == data.get("opponent"):
            raise forms.ValidationError("Pick two different teams.")
        return data


def plan_list(request):
    if request.method == "POST":
        form = PlanForm(request.POST)
        if form.is_valid():
            plan = form.save()
            return redirect("planner:board", plan_id=plan.id, number=1)
    else:
        initial = {}
        if c9 := Team.objects.filter(short_code="C9").first():
            initial["our_team"] = c9
        form = PlanForm(initial=initial)
    plans = DraftPlan.objects.select_related("our_team", "opponent")
    return render(request, "planner/plan_list.html", {**_common(), "form": form, "plans": plans})


@require_POST
def plan_delete(request, plan_id):
    get_object_or_404(DraftPlan, pk=plan_id).delete()
    messages.info(request, "Plan deleted.")
    return redirect("planner:plans")


def plan_export(request, plan_id):
    plan = get_object_or_404(DraftPlan, pk=plan_id)
    resp = HttpResponse(plan_to_markdown(plan), content_type="text/markdown; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="draft-plan-{plan.id}.md"'
    return resp


# --- Draft board ------------------------------------------------------------


def _board_context(plan, number, error=None):
    st = draft_state.game_state(plan, number)
    us, them = plan.our_team_id, plan.opponent_id
    blue_id, red_id = st.game.team_id_on(rules.BLUE), st.game.team_id_on(rules.RED)
    by_order = {a.order: a for a in st.actions}
    acting = st.acting_team_id()

    def side_slots(side, action):
        return [
            {"step": s, "action": by_order.get(s.order), "current": st.next_step and st.next_step.order == s.order}
            for s in rules.DRAFT_ORDER
            if s.side == side and s.action == action
        ]

    if st.next_step:
        unavailable = (
            st.unavailable(acting) if st.next_step.action == rules.PICK else rules.unbannable(st.taken)
        )
        wasted_ban = st.fearless_locked(us if acting == them else them) if st.next_step.action == rules.BAN else set()
    else:
        unavailable, wasted_ban = set(st.taken), set()

    locked_us, locked_them = st.fearless_locked(us), st.fearless_locked(them)
    grid = []
    for c in st.champions:
        status = "ok"
        if c.id in st.taken:
            status = "taken"
        elif c.id in unavailable:
            status = "locked"
        elif c.id in wasted_ban:
            status = "wasted"
        grid.append({"c": c, "status": status, "locked_us": c.id in locked_us, "locked_them": c.id in locked_them})

    return {
        **_common(),
        "plan": plan,
        "st": st,
        "games": plan.games.order_by("number"),
        "blue": st.game.blue_team,
        "red": st.game.red_team,
        "blue_is_us": blue_id == us,
        "slots": {
            "blue_bans": side_slots(rules.BLUE, rules.BAN),
            "red_bans": side_slots(rules.RED, rules.BAN),
            "blue_picks": side_slots(rules.BLUE, rules.PICK),
            "red_picks": side_slots(rules.RED, rules.PICK),
        },
        "grid": grid,
        "roles": rules.ROLES,
        "suggest": SuggestionEngine(st).suggest(limit=6),
        "open_roles_us": st.open_roles(us),
        "open_roles_them": st.open_roles(them),
        "locked_count": len(locked_us | locked_them),
        "conflicts": draft_state.series_conflicts(plan),
        "error": error,
        "acting_is_us": acting == us,
        "red_id": red_id,
    }


def board(request, plan_id, number):
    plan = get_object_or_404(DraftPlan.objects.select_related("our_team", "opponent"), pk=plan_id)
    get_object_or_404(plan.games, number=number)
    return render(request, "planner/board.html", _board_context(plan, number))


@require_POST
def board_act(request, plan_id, number):
    plan = get_object_or_404(DraftPlan, pk=plan_id)
    champ = get_object_or_404(Champion, pk=request.POST.get("champion"))
    error = None
    try:
        draft_state.apply_action(plan, number, champ, request.POST.get("role", ""))
    except rules.DraftError as exc:
        error = str(exc)
    return render(request, "planner/_board.html", _board_context(plan, number, error))


@require_POST
def board_undo(request, plan_id, number):
    plan = get_object_or_404(DraftPlan, pk=plan_id)
    draft_state.undo_last(plan, number)
    return render(request, "planner/_board.html", _board_context(plan, number))


@require_POST
def board_reset(request, plan_id, number):
    plan = get_object_or_404(DraftPlan, pk=plan_id)
    plan.games.get(number=number).actions.all().delete()
    return render(request, "planner/_board.html", _board_context(plan, number))


@require_POST
def game_settings(request, plan_id, number):
    plan = get_object_or_404(DraftPlan, pk=plan_id)
    game = get_object_or_404(plan.games, number=number)
    error = None
    side = request.POST.get("our_side")
    if side in rules.SIDES and side != game.our_side:
        if game.actions.exists():
            error = "Clear this game's draft before switching sides."
        else:
            game.our_side = side
    if "result" in request.POST and request.POST["result"] in {"", "win", "loss"}:
        game.result = request.POST["result"]
    if "notes" in request.POST:
        game.notes = request.POST["notes"]
    game.save()
    return render(request, "planner/_board.html", _board_context(plan, number, error))
