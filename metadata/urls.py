"""
URL patterns for the metadata app.
"""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import ClipNeighborsView, SearchView

# Placeholder router - views will be added later
router = DefaultRouter()

urlpatterns = [
    path("", include(router.urls)),
    path("search/", SearchView.as_view(), name="search"),
    path("search/clip-neighbors/", ClipNeighborsView.as_view(), name="clip-neighbors"),
]
