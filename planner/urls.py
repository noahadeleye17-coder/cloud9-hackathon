from django.urls import path

from . import views

app_name = "planner"

urlpatterns = [
    path("", views.home, name="home"),
    path("scouting/", views.scouting, name="scouting"),
    path("plans/", views.plan_list, name="plans"),
    path("plans/<int:plan_id>/delete/", views.plan_delete, name="plan_delete"),
    path("plans/<int:plan_id>/export.md", views.plan_export, name="plan_export"),
    path("plans/<int:plan_id>/game/<int:number>/", views.board, name="board"),
    path("plans/<int:plan_id>/game/<int:number>/act/", views.board_act, name="board_act"),
    path("plans/<int:plan_id>/game/<int:number>/undo/", views.board_undo, name="board_undo"),
    path("plans/<int:plan_id>/game/<int:number>/reset/", views.board_reset, name="board_reset"),
    path("plans/<int:plan_id>/game/<int:number>/settings/", views.game_settings, name="game_settings"),
]
