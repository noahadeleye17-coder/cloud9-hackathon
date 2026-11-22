from django.contrib import admin

from .models import Champion, DraftAction, Game, Series, Team


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ["name", "short_code", "region"]
    search_fields = ["name", "short_code"]


@admin.register(Champion)
class ChampionAdmin(admin.ModelAdmin):
    list_display = ["name", "key", "roles"]
    search_fields = ["name", "key"]


class GameInline(admin.TabularInline):
    model = Game
    extra = 0


@admin.register(Series)
class SeriesAdmin(admin.ModelAdmin):
    list_display = ["__str__", "league", "patch", "fearless_mode", "source"]
    list_filter = ["source", "fearless_mode", "patch", "format"]
    inlines = [GameInline]


class DraftActionInline(admin.TabularInline):
    model = DraftAction
    extra = 0


@admin.register(Game)
class GameAdmin(admin.ModelAdmin):
    list_display = ["__str__", "blue_team", "red_team", "winner"]
    inlines = [DraftActionInline]
