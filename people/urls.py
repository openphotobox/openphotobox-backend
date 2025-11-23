"""
URL patterns for the people app.
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register(r'', views.PersonViewSet, basename='person')
router.register(r'faces', views.FaceViewSet)
router.register(r'merge-suggestions', views.PersonMergeSuggestionViewSet)

urlpatterns = [
    path('', include(router.urls)),
]
