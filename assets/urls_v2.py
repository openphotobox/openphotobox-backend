from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()

router.register(r"", views.AssetViewSetV2, basename="assets-v2")


urlpatterns = [
    path("", include(router.urls)),
]


# Note:
# - People and faces endpoints moved to people app
# - Sharing endpoints moved to sharing app
