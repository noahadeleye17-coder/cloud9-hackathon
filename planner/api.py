from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_date
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from esports import draft_rules as rules
from esports.models import Champion, Team
from esports.tendencies import Filters, champ_summary, team_tendencies

from . import state as draft_state
from .export import plan_to_dict, plan_to_markdown
from .models import DraftPlan
from .serializers import (
    ActionInputSerializer,
    ChampionSerializer,
    DraftPlanSerializer,
    PlannedActionSerializer,
    PlannedGameSerializer,
    TeamSerializer,
)
from .suggestions import SuggestionEngine


def filters_from_request(request) -> Filters:
    q = request.query_params
    f = Filters(patch=q.get("patch") or None)
    for name, attr in (("from", "date_from"), ("to", "date_to")):
        if q.get(name):
            parsed = parse_date(q[name])
            if parsed is None:
                raise ValidationError({name: "Use YYYY-MM-DD."})
            setattr(f, attr, parsed)
    return f


class TeamViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Team.objects.all()
    serializer_class = TeamSerializer

    @action(detail=True)
    def tendencies(self, request, pk=None):
        return Response(team_tendencies(self.get_object(), filters_from_request(request)))


class ChampionViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Champion.objects.all()
    serializer_class = ChampionSerializer


class DraftPlanViewSet(viewsets.ModelViewSet):
    queryset = DraftPlan.objects.select_related("our_team", "opponent").prefetch_related("games__actions__champion")
    serializer_class = DraftPlanSerializer

    def _game_number(self, request, plan):
        raw = request.query_params.get("game")
        if raw is None and isinstance(request.data, dict):
            raw = request.data.get("game")
        try:
            number = int(raw or 1)
        except (TypeError, ValueError):
            raise ValidationError({"game": "Must be a game number."})
        if not plan.games.filter(number=number).exists():
            raise ValidationError({"game": f"Game {number} doesn't exist in this plan."})
        return number

    @action(detail=True)
    def pool(self, request, pk=None):
        plan = self.get_object()
        st = draft_state.game_state(plan, self._game_number(request, plan))

        def team_pool(team_id):
            return {
                "fearless_locked": [champ_summary(c) for c in st.champions if c.id in st.fearless_locked(team_id)],
                "open_roles": st.open_roles(team_id),
                "available": {role: [c.id for c in champs] for role, champs in st.pool(team_id).items()},
            }

        s = st.next_step
        return Response(
            {
                "game": st.game.number,
                "next_step": {"order": s.order, "side": s.side, "action": s.action, "label": s.label} if s else None,
                "acting_team_id": st.acting_team_id(),
                "taken": sorted(st.taken),
                "us": team_pool(plan.our_team_id),
                "opponent": team_pool(plan.opponent_id),
            }
        )

    @action(detail=True, methods=["post", "delete"])
    def actions(self, request, pk=None):
        plan = self.get_object()
        if request.method == "DELETE":
            number = self._game_number(request, plan)
            undone = draft_state.undo_last(plan, number)
            return Response({"undone": undone.order if undone else None})

        inp = ActionInputSerializer(data=request.data)
        inp.is_valid(raise_exception=True)
        try:
            act = draft_state.apply_action(
                plan, self._game_number(request, plan), inp.validated_data["champion"], inp.validated_data["role"]
            )
        except rules.DraftError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response(PlannedActionSerializer(act).data, status=status.HTTP_201_CREATED)

    @action(detail=True)
    def suggestions(self, request, pk=None):
        plan = self.get_object()
        st = draft_state.game_state(plan, self._game_number(request, plan))
        limit = min(int(request.query_params.get("limit", 8)), 30)
        return Response(SuggestionEngine(st).suggest(limit=limit))

    @action(detail=True)
    def export(self, request, pk=None):
        plan = self.get_object()
        if request.query_params.get("as") == "markdown":
            resp = HttpResponse(plan_to_markdown(plan), content_type="text/markdown; charset=utf-8")
            resp["Content-Disposition"] = f'attachment; filename="draft-plan-{plan.id}.md"'
            return resp
        return Response(plan_to_dict(plan))

    @action(detail=True, methods=["patch"], url_path=r"games/(?P<number>\d+)")
    def game(self, request, pk=None, number=None):
        plan = self.get_object()
        game = get_object_or_404(plan.games, number=number)
        ser = PlannedGameSerializer(game, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        if "our_side" in ser.validated_data and ser.validated_data["our_side"] != game.our_side and game.actions.exists():
            raise ValidationError({"our_side": "Clear this game's draft before switching sides."})
        ser.save()
        return Response(ser.data)
