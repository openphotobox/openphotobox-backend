import time
from datetime import datetime, timezone
from pathlib import Path

from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import FileResponse, Http404, StreamingHttpResponse
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from openphotobox_backend.pagination import AssetCursorPagination

from .models import Asset, Comment, Like, StorageBackend, StorageBucket
from .serializers import (
    AssetGallerySerializer,
    AssetSerializer,
    CommentSerializer,
    LikeSerializer,
    StorageBackendSerializer,
    StorageBucketSerializer,
)
from .services import UploadService, get_default_upload_bucket


class AssetViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing photo assets.
    Supports filtering by date, keywords, visibility, etc.
    """

    queryset = Asset.objects.all()
    serializer_class = AssetSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = AssetCursorPagination

    def get_queryset(self):
        # Filter to only accessible assets based on album ownership/sharing
        from albums.permissions import get_accessible_assets

        queryset = get_accessible_assets(self.request.user)
        queryset = (
            queryset.prefetch_related("thumbnails")
            .select_related("storage_bucket", "storage_bucket__backend", "owner")
            .order_by("-taken_at", "-created_at")
        )
        # Only show assets that have at least one ready thumbnail to avoid heavy original loads
        # This prevents freshly uploaded, unprocessed photos from appearing in albums/timelines until ready
        queryset = queryset.filter(thumbnails__is_ready=True).distinct()

        # Filter by visibility
        visibility = self.request.query_params.get("visibility")
        if visibility:
            queryset = queryset.filter(visibility=visibility)

        # Filter by person or people combinations (via faces relationship in people app)
        person_id = self.request.query_params.get("person") or self.request.query_params.get("person_id")
        people_param = self.request.query_params.get("people") or self.request.query_params.get("person_ids")
        people_mode = (self.request.query_params.get("people_mode") or "all").lower()
        try:
            from people.models import Face

            if people_param:
                people_ids = [p.strip() for p in people_param.split(",") if p and p.strip()]
                if people_ids:
                    if people_mode not in ("all", "any"):
                        people_mode = "all"
                    if people_mode == "all":
                        # Intersect assets that contain each specified person
                        intersect_ids = None
                        for pid in people_ids:
                            ids_for_pid = set(Face.objects.filter(person_id=pid).values_list("asset_id", flat=True))
                            intersect_ids = ids_for_pid if intersect_ids is None else (intersect_ids & ids_for_pid)
                            if not intersect_ids:
                                break
                        queryset = queryset.filter(id__in=list(intersect_ids or []))
                    else:
                        queryset = queryset.filter(
                            id__in=Face.objects.filter(person_id__in=people_ids)
                            .values_list("asset_id", flat=True)
                            .distinct()
                        )
            elif person_id:
                queryset = queryset.filter(
                    id__in=Face.objects.filter(person_id=person_id).values_list("asset_id", flat=True)
                )
        except Exception:
            # If people app is unavailable for any reason, return no results for safety
            queryset = queryset.none()

        # Filter by album
        album_id = self.request.query_params.get("album") or self.request.query_params.get("album_id")
        albums_param = self.request.query_params.get("albums") or self.request.query_params.get("album_ids")
        if albums_param or album_id:
            try:
                from albums.models import AlbumAsset

                album_ids = []
                if albums_param:
                    album_ids = [a.strip() for a in albums_param.split(",") if a and a.strip()]
                if album_id:
                    album_ids.append(album_id)
                if album_ids:
                    queryset = queryset.filter(
                        id__in=AlbumAsset.objects.filter(album_id__in=album_ids)
                        .values_list("asset_id", flat=True)
                        .distinct()
                    )
            except Exception:
                queryset = queryset.none()

        # Filter by date range
        start_date = self.request.query_params.get("start_date")
        end_date = self.request.query_params.get("end_date")
        if start_date:
            queryset = queryset.filter(taken_at__gte=start_date)
        if end_date:
            queryset = queryset.filter(taken_at__lte=end_date)

        # Filter by keywords (via metadata app relationship)
        keywords = self.request.query_params.get("keywords")
        if keywords:
            keyword_list = [k.strip() for k in keywords.split(",")]
            # Import here to avoid circular imports
            try:
                from metadata.models import AssetKeyword

                # Filter assets that have any of the specified keywords
                queryset = queryset.filter(
                    id__in=AssetKeyword.objects.filter(keyword__name__in=keyword_list).values_list(
                        "asset_id", flat=True
                    )
                )
            except ImportError:
                pass  # Metadata app not available

        # Search in descriptions
        search = self.request.query_params.get("search")
        if search:
            queryset = queryset.filter(description__icontains=search)

        return queryset.order_by("-taken_at", "-created_at")

    @action(detail=False, methods=["get"])
    def gallery(self, request):
        """Lightweight gallery listing for the main grid.
        Returns minimal fields with cursor pagination.
        Accepts same filters as list().
        """
        queryset = self.get_queryset()
        page = self.paginate_queryset(queryset)
        serializer = AssetGallerySerializer(page, many=True)
        return self.get_paginated_response(serializer.data)

    @action(detail=False, methods=["get"])
    def timeline(self, request):
        """Return available photo dates and counts for the timeline sidebar.
        Groups by capture day (YYYY-MM-DD) using taken_at if present, else created_at.
        """
        from django.db.models import Count
        from django.db.models.functions import Coalesce, TruncDate

        qs = (
            self.get_queryset()
            .annotate(capture_date=Coalesce(TruncDate("taken_at"), TruncDate("created_at")))
            .values("capture_date")
            .annotate(count=Count("id"))
            .order_by("-capture_date")
        )

        items = [
            {"date": row["capture_date"].isoformat(), "count": row["count"]}
            for row in qs
            if row["capture_date"] is not None
        ]
        return Response({"results": items})

    @action(detail=False, methods=["get"], url_path="ready-since")
    def ready_since(self, request):
        """Return assets that became ready (have a ready thumbnail) since a given ISO timestamp.
        Query params: since=ISO8601, limit (default 100)
        """
        since = request.query_params.get("since")
        try:
            limit = int(request.query_params.get("limit", 100))
        except Exception:
            limit = 100
        qs = self.get_queryset()
        if since:
            try:
                from datetime import datetime

                # Support naive or timezone-aware strings
                dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
                qs = qs.filter(updated_at__gte=dt)
            except Exception:
                pass
        qs = qs.order_by("-updated_at")[: max(1, min(limit, 500))]
        serializer = AssetGallerySerializer(qs, many=True)
        return Response({"results": serializer.data})

    @action(detail=False, methods=["get"])
    def by_date(self, request):
        """Return all assets for a specific date (YYYY-MM-DD) for a section.
        Minimal fields for the gallery.
        Query param: date=YYYY-MM-DD
        """
        date_str = request.query_params.get("date")
        if not date_str:
            return Response({"error": "date is required (YYYY-MM-DD)"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            from datetime import datetime

            day = datetime.fromisoformat(date_str).date()
        except Exception:
            return Response({"error": "Invalid date format, expected YYYY-MM-DD"}, status=status.HTTP_400_BAD_REQUEST)

        qs = self.get_queryset().filter(Q(taken_at__date=day) | (Q(taken_at__isnull=True) & Q(created_at__date=day)))
        serializer = AssetGallerySerializer(qs, many=True)
        return Response({"results": serializer.data})

    @action(detail=False, methods=["post"])
    def upload_file(self, request):
        """
        Direct file upload to local filesystem storage.

        POST /api/assets/upload_file/
        Content-Type: multipart/form-data

        Body:
            file: binary file data
            bucket_id (optional): target bucket ID (defaults to originals bucket)
            metadata (optional): JSON string with additional metadata

        Returns:
            Asset details including asset_id
        """
        try:
            # Get uploaded file
            if "file" not in request.FILES:
                return Response({"error": "No file provided"}, status=status.HTTP_400_BAD_REQUEST)

            uploaded_file = request.FILES["file"]

            # Get storage bucket
            bucket_id = request.data.get("bucket_id")
            if bucket_id:
                try:
                    bucket = StorageBucket.objects.get(id=bucket_id)
                except StorageBucket.DoesNotExist:
                    return Response({"error": "Invalid bucket_id"}, status=status.HTTP_400_BAD_REQUEST)
            else:
                # Default to originals bucket
                bucket = get_default_upload_bucket("originals")

            # Initialize upload service
            upload_service = UploadService(bucket.backend)

            # Parse metadata if provided
            metadata = {}
            if "metadata" in request.data:
                import json

                try:
                    metadata = json.loads(request.data["metadata"])
                except json.JSONDecodeError:
                    return Response({"error": "Invalid metadata JSON"}, status=status.HTTP_400_BAD_REQUEST)

            # Save file to local storage and create asset
            asset = upload_service.save_uploaded_file(
                file=uploaded_file, bucket=bucket, metadata=metadata, owner=request.user
            )

            # Serialize and return asset details
            serializer = AssetSerializer(asset)
            return Response(serializer.data, status=status.HTTP_201_CREATED)

        except ValidationError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response({"error": f"Failed to upload file: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=["post"])
    def bulk_update(self, request):
        """Bulk update assets with new metadata"""
        asset_ids = request.data.get("asset_ids", [])
        update_data = request.data.get("update_data", {})

        if not asset_ids:
            return Response({"error": "asset_ids is required"}, status=status.HTTP_400_BAD_REQUEST)

        assets = Asset.objects.filter(id__in=asset_ids)
        updated_count = assets.update(**update_data)

        return Response({"message": f"Updated {updated_count} assets", "updated_count": updated_count})

    @action(detail=False, methods=["post"], url_path="upload/config")
    def get_upload_config(self, request):
        """
        Get upload configuration for local storage.

        POST /api/assets/upload/config/
        {
            "filename": "photo.jpg",
            "content_type": "image/jpeg"
        }
        """
        try:
            filename = request.data.get("filename")

            if not filename:
                return Response({"error": "filename is required"}, status=status.HTTP_400_BAD_REQUEST)

            # Check if storage backend is configured
            try:
                bucket = get_default_upload_bucket("originals")
            except ValidationError as e:
                return Response(
                    {
                        "error": "Storage not configured",
                        "details": str(e),
                    },
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )

            # Generate unique file key
            upload_service = UploadService(bucket.backend)
            file_key = upload_service.generate_upload_key(filename)

            return Response(
                {
                    "upload_method": "direct",
                    "upload_endpoint": "/api/assets/upload_file/",
                    "suggested_key": file_key,
                    "bucket_id": str(bucket.id),
                }
            )

        except ValidationError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response(
                {"error": f"Upload configuration failed: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=False, methods=["get"])
    def liked(self, request):
        """Return all assets liked by the current user.
        Uses the same pagination as the gallery endpoint.
        """
        # Get assets that the user has liked
        liked_asset_ids = Like.objects.filter(user=request.user).values_list("asset_id", flat=True)

        # Filter to accessible assets (respecting album permissions)
        queryset = self.get_queryset().filter(id__in=liked_asset_ids)

        # Paginate and return
        page = self.paginate_queryset(queryset)
        serializer = AssetGallerySerializer(page, many=True, context={"request": request})
        return self.get_paginated_response(serializer.data)


def stream_events(request):
    """Server-Sent Events stream for lightweight push updates.
    Auth: either session auth (request.user.is_authenticated) or token via ?token=...
    Emits asset_ready events when new assets become ready (thumbnails present) since connect time.
    """
    # Authenticate via token param if provided
    user = getattr(request, "user", None)
    token = request.GET.get("token")
    if (not getattr(user, "is_authenticated", False)) and token:
        try:
            # Try DRF TokenAuth if installed
            try:
                from rest_framework.authtoken.models import Token as DRFToken

                t = DRFToken.objects.select_related("user").get(key=token)
                user = t.user
            except Exception:
                user = None
        except Exception:
            user = None
    if not user or not getattr(user, "is_authenticated", False):
        return Response({"detail": "Authentication required"}, status=status.HTTP_401_UNAUTHORIZED)

    def event_stream():
        # Start baseline timestamp
        last = datetime.now(timezone.utc)
        # Initial comment to open the stream
        yield ": connected\n\n"
        while True:
            try:
                # Find assets updated since last that have ready thumbnails
                qs = (
                    Asset.objects.filter(updated_at__gte=last)
                    .filter(thumbnails__is_ready=True)
                    .order_by("updated_at")
                    .distinct()[:200]
                )
                items = list(qs.values("id", "updated_at"))
                if items:
                    # Advance last to the newest update time
                    newest = max(i["updated_at"] for i in items if i.get("updated_at"))
                    if newest:
                        last = newest
                    data = {"type": "asset_ready", "ids": [str(i["id"]) for i in items]}
                    yield "event: asset_ready\n" + f"data: {data}\n\n"
                else:
                    # Heartbeat to keep connection alive
                    yield ": keep-alive\n\n"
            except Exception:
                # On error, emit heartbeat
                yield ": error\n\n"
            time.sleep(2)

    resp = StreamingHttpResponse(event_stream(), content_type="text/event-stream")
    resp["Cache-Control"] = "no-cache"
    resp["X-Accel-Buffering"] = "no"  # for nginx
    return resp


class StorageViewSet(viewsets.ViewSet):
    """
    Simplified storage configuration API.
    Hides the complexity of backends and buckets from the frontend.
    """

    permission_classes = [permissions.IsAdminUser]

    @action(detail=False, methods=["post"], url_path="setup")
    def setup(self, request):
        """
        Simple storage setup - provide a path and we'll handle the rest.

        POST /api/storage/setup/
        {
            "path": "/home/user/photos"
        }

        This will:
        - Create a StorageBackend with the path
        - Create originals and thumbnails buckets
        - Set everything as default
        - Create the directory structure
        """
        import os

        path = request.data.get("path")
        if not path:
            return Response({"error": "path is required"}, status=status.HTTP_400_BAD_REQUEST)

        # Validate path
        path = str(Path(path).resolve())

        # Check if storage is already configured with a different path
        existing_backend = StorageBackend.objects.filter(name="Default Storage").first()
        if existing_backend:
            existing_path = str(existing_backend.get_base_path())
            if existing_path != path:
                # Check if any assets exist
                asset_count = Asset.objects.filter(storage_bucket__backend=existing_backend).count()
                if asset_count > 0:
                    return Response(
                        {
                            "error": "Cannot change storage path",
                            "details": f"Storage is already configured at '{existing_path}' with {asset_count} photos. "
                            "Changing the path would make existing photos inaccessible.",
                            "current_path": existing_path,
                            "requested_path": path,
                            "asset_count": asset_count,
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )

        try:
            # Create directory if it doesn't exist
            os.makedirs(path, exist_ok=True)
            os.makedirs(os.path.join(path, "originals"), exist_ok=True)
            os.makedirs(os.path.join(path, "thumbnails"), exist_ok=True)
        except OSError as e:
            return Response({"error": f"Failed to create directory: {str(e)}"}, status=status.HTTP_400_BAD_REQUEST)

        # Check if path is writable
        if not os.access(path, os.W_OK):
            return Response({"error": f"Directory is not writable: {path}"}, status=status.HTTP_400_BAD_REQUEST)

        # Unset other defaults
        StorageBackend.objects.filter(is_default=True).update(is_default=False)

        # Create or update backend
        backend, created = StorageBackend.objects.update_or_create(
            name="Default Storage",
            defaults={"backend_type": "local", "config": {"base_path": path}, "is_default": True, "is_active": True},
        )

        # Create buckets
        originals_bucket, _ = StorageBucket.objects.update_or_create(
            backend=backend,
            purpose="originals",
            defaults={"name": "originals", "display_name": "Original Photos", "is_active": True},
        )

        thumbnails_bucket, _ = StorageBucket.objects.update_or_create(
            backend=backend,
            purpose="thumbnails",
            defaults={"name": "thumbnails", "display_name": "Thumbnails", "is_active": True},
        )

        return Response(
            {"success": True, "path": path, "message": "Storage configured successfully"}, status=status.HTTP_200_OK
        )

    @action(detail=False, methods=["get"], url_path="status")
    def status(self, request):
        """
        Simple storage status check.

        GET /api/storage/status/

        Returns:
        {
            "configured": true/false,
            "path": "/path/to/storage" or null
        }
        """
        try:
            # Check if we have a default backend with buckets
            backend = StorageBackend.objects.filter(is_default=True, is_active=True).first()

            if not backend:
                return Response({"configured": False, "path": None})

            # Check if buckets exist
            has_originals = StorageBucket.objects.filter(backend=backend, purpose="originals", is_active=True).exists()
            has_thumbnails = StorageBucket.objects.filter(
                backend=backend, purpose="thumbnails", is_active=True
            ).exists()

            if not (has_originals and has_thumbnails):
                return Response({"configured": False, "path": None})

            # Get the path
            path = backend.get_base_path()

            return Response({"configured": True, "path": str(path)})

        except Exception:
            return Response({"configured": False, "path": None})


class StorageBackendViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing storage backends (advanced usage).
    Most users should use the simplified /api/storage/ endpoints instead.
    """

    queryset = StorageBackend.objects.all()
    serializer_class = StorageBackendSerializer
    permission_classes = [permissions.IsAdminUser]  # Admin only for storage config

    def get_queryset(self):
        queryset = StorageBackend.objects.all()

        # Filter by backend type
        backend_type = self.request.query_params.get("backend_type")
        if backend_type:
            queryset = queryset.filter(backend_type=backend_type)

        # Filter by active status
        is_active = self.request.query_params.get("is_active")
        if is_active is not None:
            queryset = queryset.filter(is_active=is_active.lower() == "true")

        return queryset.order_by("-is_default", "-is_active", "name")


class StorageBucketViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing storage buckets.
    """

    queryset = StorageBucket.objects.select_related("backend").all()
    serializer_class = StorageBucketSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        queryset = StorageBucket.objects.select_related("backend").all()

        # Filter by backend
        backend_id = self.request.query_params.get("backend")
        if backend_id:
            queryset = queryset.filter(backend_id=backend_id)

        # Filter by purpose
        purpose = self.request.query_params.get("purpose")
        if purpose:
            queryset = queryset.filter(purpose=purpose)

        # Filter by active status
        is_active = self.request.query_params.get("is_active")
        if is_active is not None:
            queryset = queryset.filter(is_active=is_active.lower() == "true")

        return queryset.order_by("backend__name", "purpose", "display_name")


# Image serving views for local storage
def serve_image(request, bucket_id, path):
    """
    Serve images from local filesystem storage with access control.

    GET /images/<bucket_id>/<path>
    """
    try:
        # Get the storage bucket
        bucket = StorageBucket.objects.get(id=bucket_id)
        backend = bucket.backend

        # Get file path from local storage
        base_path = backend.get_base_path()
        file_path = base_path / bucket.purpose / path

        if not file_path.exists():
            raise Http404("Image not found")

        # Serve file with FileResponse
        response = FileResponse(open(file_path, "rb"))
        response["Cache-Control"] = "public, max-age=3600"

        # Try to guess content type
        import mimetypes

        content_type, _ = mimetypes.guess_type(str(file_path))
        if content_type:
            response["Content-Type"] = content_type

        return response

    except StorageBucket.DoesNotExist:
        raise Http404("Bucket not found")
    except Exception:
        raise Http404("Error serving image")


def serve_thumbnail(request, bucket_id, path):
    """
    Serve thumbnails from local filesystem storage with access control.

    GET /thumbnails/<bucket_id>/<path>
    """
    try:
        # Get the storage bucket
        bucket = StorageBucket.objects.get(id=bucket_id)
        backend = bucket.backend

        # Get file path from local storage
        base_path = backend.get_base_path()
        file_path = base_path / bucket.purpose / path

        if not file_path.exists():
            raise Http404("Thumbnail not found")

        # Serve file with FileResponse
        response = FileResponse(open(file_path, "rb"))
        response["Cache-Control"] = "public, max-age=86400"  # 24 hours
        response["Content-Type"] = "image/jpeg"

        return response

    except StorageBucket.DoesNotExist:
        raise Http404("Bucket not found")
    except Exception:
        raise Http404("Error serving thumbnail")


def serve_face_thumbnail(request, bucket_id, path):
    """
    Serve face thumbnails from local filesystem storage with access control.

    GET /face-thumbnails/<bucket_id>/<path>
    """
    try:
        # Get the storage bucket
        bucket = StorageBucket.objects.get(id=bucket_id)
        backend = bucket.backend

        # Get file path from local storage
        base_path = backend.get_base_path()

        # Face thumbnails might be in originals bucket with face-thumbnails prefix
        if bucket.purpose == "originals":
            file_path = base_path / "face-thumbnails" / path
        else:
            file_path = base_path / bucket.purpose / path

        if not file_path.exists():
            raise Http404("Face thumbnail not found")

        # Serve file with FileResponse
        response = FileResponse(open(file_path, "rb"))
        response["Cache-Control"] = "public, max-age=86400"  # 24 hours
        response["Content-Type"] = "image/jpeg"

        return response

    except StorageBucket.DoesNotExist:
        raise Http404("Bucket not found")
    except Exception:
        raise Http404("Error serving face thumbnail")


class LikeViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing likes on assets.
    """

    queryset = Like.objects.all()
    serializer_class = LikeSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        """Filter likes to only those on accessible assets"""
        from albums.permissions import get_accessible_assets

        accessible_assets = get_accessible_assets(self.request.user)
        return Like.objects.filter(asset__in=accessible_assets).select_related("user", "asset")

    def perform_create(self, serializer):
        """Check that user can view the asset before allowing like"""
        from albums.permissions import can_view_asset

        asset = serializer.validated_data.get("asset")
        if not can_view_asset(self.request.user, asset):
            raise ValidationError("You do not have permission to like this asset")

        # Set the user to the current user
        serializer.save(user=self.request.user)

    def destroy(self, request, *args, **kwargs):
        """Only allow users to delete their own likes"""
        like = self.get_object()
        if like.user != request.user:
            return Response({"error": "You can only delete your own likes"}, status=status.HTTP_403_FORBIDDEN)
        return super().destroy(request, *args, **kwargs)


class CommentViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing comments on assets.
    """

    queryset = Comment.objects.all()
    serializer_class = CommentSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        """Filter comments to only those on accessible assets"""
        from albums.permissions import get_accessible_assets

        accessible_assets = get_accessible_assets(self.request.user)
        return Comment.objects.filter(asset__in=accessible_assets).select_related("user", "asset")

    def perform_create(self, serializer):
        """Check that user can view the asset before allowing comment"""
        from albums.permissions import can_view_asset

        asset = serializer.validated_data.get("asset")
        if not can_view_asset(self.request.user, asset):
            raise ValidationError("You do not have permission to comment on this asset")

        # Set the user to the current user
        serializer.save(user=self.request.user)

    def update(self, request, *args, **kwargs):
        """Only allow users to update their own comments"""
        comment = self.get_object()
        if comment.user != request.user:
            return Response({"error": "You can only edit your own comments"}, status=status.HTTP_403_FORBIDDEN)
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        """Only allow users to update their own comments"""
        comment = self.get_object()
        if comment.user != request.user:
            return Response({"error": "You can only edit your own comments"}, status=status.HTTP_403_FORBIDDEN)
        return super().partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        """Only allow users to delete their own comments"""
        comment = self.get_object()
        if comment.user != request.user:
            return Response({"error": "You can only delete your own comments"}, status=status.HTTP_403_FORBIDDEN)
        return super().destroy(request, *args, **kwargs)
