from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import AlbumViewSet

router = DefaultRouter()
router.register(r"albums", AlbumViewSet, basename="album")

urlpatterns = [
    path("", include(router.urls)),
]

