from django.contrib import admin
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from planner.api import ChampionViewSet, DraftPlanViewSet, TeamViewSet

router = DefaultRouter()
router.register("teams", TeamViewSet)
router.register("champions", ChampionViewSet)
router.register("draft-plans", DraftPlanViewSet)

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include(router.urls)),
    path("", include("planner.urls")),
]
