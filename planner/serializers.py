from rest_framework import serializers

from esports.models import Champion, Team

from .models import DraftPlan, PlannedAction, PlannedGame


class TeamSerializer(serializers.ModelSerializer):
    class Meta:
        model = Team
        fields = ["id", "name", "short_code", "region"]


class ChampionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Champion
        fields = ["id", "key", "name", "roles", "image_url"]


class PlannedActionSerializer(serializers.ModelSerializer):
    champion = ChampionSerializer(read_only=True)
    side = serializers.SerializerMethodField()
    type = serializers.SerializerMethodField()
    label = serializers.SerializerMethodField()
    team_id = serializers.IntegerField(read_only=True)

    class Meta:
        model = PlannedAction
        fields = ["order", "label", "side", "type", "team_id", "champion", "role"]

    def get_side(self, obj):
        return obj.step.side

    def get_type(self, obj):
        return obj.step.action

    def get_label(self, obj):
        return obj.step.label


class PlannedGameSerializer(serializers.ModelSerializer):
    actions = PlannedActionSerializer(many=True, read_only=True)

    class Meta:
        model = PlannedGame
        fields = ["number", "our_side", "result", "notes", "actions"]
        read_only_fields = ["number"]


class DraftPlanSerializer(serializers.ModelSerializer):
    games = PlannedGameSerializer(many=True, read_only=True)
    our_team = serializers.PrimaryKeyRelatedField(queryset=Team.objects.all())
    opponent = serializers.PrimaryKeyRelatedField(queryset=Team.objects.all())

    class Meta:
        model = DraftPlan
        fields = [
            "id", "name", "our_team", "opponent", "format", "fearless_mode",
            "scout_patch", "notes", "created_at", "updated_at", "games",
        ]
        read_only_fields = ["created_at", "updated_at"]

    def validate(self, attrs):
        us = attrs.get("our_team", getattr(self.instance, "our_team", None))
        them = attrs.get("opponent", getattr(self.instance, "opponent", None))
        if us and them and us == them:
            raise serializers.ValidationError("Our team and opponent must be different.")
        if self.instance and "format" in attrs and attrs["format"] != self.instance.format:
            raise serializers.ValidationError({"format": "Format can't be changed after creation."})
        return attrs


class ActionInputSerializer(serializers.Serializer):
    game = serializers.IntegerField(min_value=1)
    champion = serializers.PrimaryKeyRelatedField(queryset=Champion.objects.all())
    role = serializers.CharField(required=False, allow_blank=True, default="")
