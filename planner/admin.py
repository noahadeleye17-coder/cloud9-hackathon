from django.contrib import admin

from .models import DraftPlan, PlannedAction, PlannedGame


class PlannedGameInline(admin.TabularInline):
    model = PlannedGame
    extra = 0


@admin.register(DraftPlan)
class DraftPlanAdmin(admin.ModelAdmin):
    list_display = ["name", "our_team", "opponent", "format", "fearless_mode", "updated_at"]
    inlines = [PlannedGameInline]


admin.site.register(PlannedAction)
