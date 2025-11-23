from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
# Register specific prefixes BEFORE the catch-all AssetViewSet to avoid collisions
router.register(r"albums", views.AlbumViewSet)
router.register(r"upload-batches", views.UploadBatchViewSet)
router.register(r"storage-backends", views.StorageBackendViewSet)
router.register(r"storage-buckets", views.StorageBucketViewSet)
router.register(r"", views.AssetViewSet, basename="asset")

urlpatterns = [
    path("", include(router.urls)),
]

# Note:
# - People and faces endpoints moved to people app
# - Sharing endpoints moved to sharing app
