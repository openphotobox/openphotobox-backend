"""
URL patterns for the sharing app.
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter

# Placeholder router - views will be added later
router = DefaultRouter()

urlpatterns = [
    path('', include(router.urls)),
]