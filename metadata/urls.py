"""
URL patterns for the metadata app.
"""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import ClipNeighborsView, ClipSearchView

# Placeholder router - views will be added later
router = DefaultRouter()

urlpatterns = [
    path("", include(router.urls)),
    path("search/clip/", ClipSearchView.as_view(), name="clip-search"),
    path("search/clip-neighbors/", ClipNeighborsView.as_view(), name="clip-neighbors"),
]
