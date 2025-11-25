"""
Views for the albums app.
"""

from django.contrib.auth.models import User
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import AlbumAsset, AlbumShare
from .permissions import can_contribute_to_album, can_edit_album, get_accessible_albums
from .serializers import (
    AlbumCreateSerializer,
    AlbumSerializer,
    AlbumShareCreateSerializer,
    AlbumShareSerializer,
    AlbumShareUpdateSerializer,
)


class AlbumViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing photo albums.

    Albums are collections of photos that can be organized and shared.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        """Filter albums to only those the user can access"""
        return get_accessible_albums(self.request.user).prefetch_related("shares", "shares__shared_with")

    def get_serializer_class(self):
        """Use different serializers for different actions"""
        if self.action == "create":
            return AlbumCreateSerializer
        return AlbumSerializer

    def perform_create(self, serializer):
        """Set the owner when creating an album"""
        serializer.save(owner=self.request.user)

    def update(self, request, *args, **kwargs):
        """Only allow owner to update album"""
        album = self.get_object()
        if not can_edit_album(request.user, album):
            return Response({"error": "Only the album owner can edit album details"}, status=status.HTTP_403_FORBIDDEN)
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        """Only allow owner to partially update album"""
        album = self.get_object()
        if not can_edit_album(request.user, album):
            return Response({"error": "Only the album owner can edit album details"}, status=status.HTTP_403_FORBIDDEN)
        return super().partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        """Only allow owner to delete album"""
        album = self.get_object()
        if not can_edit_album(request.user, album):
            return Response({"error": "Only the album owner can delete the album"}, status=status.HTTP_403_FORBIDDEN)
        return super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=["post"])
    def add_photos(self, request, pk=None):
        """
        Add photos to this album.

        POST /api/albums/{id}/add_photos/
        Body: {"asset_ids": ["uuid1", "uuid2", ...]}

        Requires contribute permission (owner or shared with contribute permission)
        """
        album = self.get_object()

        if not can_contribute_to_album(request.user, album):
            return Response(
                {"error": "You don't have permission to add photos to this album"}, status=status.HTTP_403_FORBIDDEN
            )

        asset_ids = request.data.get("asset_ids", [])
        if not asset_ids:
            return Response({"error": "asset_ids is required"}, status=status.HTTP_400_BAD_REQUEST)

        # Filter to only accessible assets (user must own or have access to the assets)
        from albums.permissions import filter_accessible_asset_ids

        accessible_ids = filter_accessible_asset_ids(request.user, asset_ids)

        # Create album-photo relationships
        album_assets = []
        for i, asset_id in enumerate(accessible_ids):
            album_assets.append(AlbumAsset(album=album, asset_id=asset_id, order=i))

        AlbumAsset.objects.bulk_create(album_assets, ignore_conflicts=True)

        return Response(
            {
                "message": f"Added {len(accessible_ids)} photos to album",
                "album": AlbumSerializer(album, context={"request": request}).data,
            }
        )

    @action(detail=True, methods=["post"])
    def remove_photos(self, request, pk=None):
        """
        Remove photos from this album.

        POST /api/albums/{id}/remove_photos/
        Body: {"asset_ids": ["uuid1", "uuid2", ...]}

        Requires contribute permission (owner or shared with contribute permission)
        """
        album = self.get_object()

        if not can_contribute_to_album(request.user, album):
            return Response(
                {"error": "You don't have permission to remove photos from this album"},
                status=status.HTTP_403_FORBIDDEN,
            )

        asset_ids = request.data.get("asset_ids", [])
        if not asset_ids:
            return Response({"error": "asset_ids is required"}, status=status.HTTP_400_BAD_REQUEST)

        # Remove the album-photo relationships
        deleted_count, _ = AlbumAsset.objects.filter(album=album, asset_id__in=asset_ids).delete()

        return Response(
            {
                "message": f"Removed {deleted_count} photos from album",
                "album": AlbumSerializer(album, context={"request": request}).data,
            }
        )

    @action(detail=True, methods=["post"])
    def share(self, request, pk=None):
        """
        Share this album with another user.

        POST /api/albums/{id}/share/
        Body: {
            "user_id": "uuid",
            "permission_level": "view" or "contribute"
        }

        Only the album owner can share the album.
        """
        album = self.get_object()

        if not can_edit_album(request.user, album):
            return Response({"error": "Only the album owner can share the album"}, status=status.HTTP_403_FORBIDDEN)

        serializer = AlbumShareCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user_id = serializer.validated_data["user_id"]
        permission_level = serializer.validated_data["permission_level"]

        # Get the user to share with
        try:
            shared_with_user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return Response({"error": "User not found"}, status=status.HTTP_404_NOT_FOUND)

        # Don't allow sharing with self
        if shared_with_user == request.user:
            return Response({"error": "Cannot share album with yourself"}, status=status.HTTP_400_BAD_REQUEST)

        # Create or update the share
        share, created = AlbumShare.objects.update_or_create(
            album=album,
            shared_with=shared_with_user,
            defaults={"permission_level": permission_level, "shared_by": request.user},
        )

        action_word = "shared" if created else "updated"
        return Response(
            {
                "message": f"Album {action_word} successfully",
                "share": AlbumShareSerializer(share).data,
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"])
    def unshare(self, request, pk=None):
        """
        Unshare this album with a user (remove sharing).

        POST /api/albums/{id}/unshare/
        Body: {"user_id": "uuid"}

        Only the album owner can unshare the album.
        """
        album = self.get_object()

        if not can_edit_album(request.user, album):
            return Response({"error": "Only the album owner can unshare the album"}, status=status.HTTP_403_FORBIDDEN)

        user_id = request.data.get("user_id")
        if not user_id:
            return Response({"error": "user_id is required"}, status=status.HTTP_400_BAD_REQUEST)

        # Delete the share
        deleted_count, _ = AlbumShare.objects.filter(album=album, shared_with_id=user_id).delete()

        if deleted_count == 0:
            return Response({"error": "Album was not shared with this user"}, status=status.HTTP_404_NOT_FOUND)

        return Response({"message": "Album unshared successfully"})

    @action(detail=True, methods=["get"])
    def shares(self, request, pk=None):
        """
        List all shares for this album.

        GET /api/albums/{id}/shares/

        Only the album owner can see the shares list.
        """
        album = self.get_object()

        if not can_edit_album(request.user, album):
            return Response({"error": "Only the album owner can view shares"}, status=status.HTTP_403_FORBIDDEN)

        shares = album.shares.select_related("shared_with", "shared_by").all()
        serializer = AlbumShareSerializer(shares, many=True)

        return Response({"shares": serializer.data})

    @action(detail=True, methods=["patch"])
    def update_share(self, request, pk=None):
        """
        Update permission level for an existing share.

        PATCH /api/albums/{id}/update_share/
        Body: {
            "user_id": "uuid",
            "permission_level": "view" or "contribute"
        }

        Only the album owner can update share permissions.
        """
        album = self.get_object()

        if not can_edit_album(request.user, album):
            return Response(
                {"error": "Only the album owner can update share permissions"}, status=status.HTTP_403_FORBIDDEN
            )

        user_id = request.data.get("user_id")
        if not user_id:
            return Response({"error": "user_id is required"}, status=status.HTTP_400_BAD_REQUEST)

        serializer = AlbumShareUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        permission_level = serializer.validated_data["permission_level"]

        try:
            share = AlbumShare.objects.get(album=album, shared_with_id=user_id)
            share.permission_level = permission_level
            share.save()

            return Response(
                {
                    "message": "Share permission updated successfully",
                    "share": AlbumShareSerializer(share).data,
                }
            )
        except AlbumShare.DoesNotExist:
            return Response({"error": "Album is not shared with this user"}, status=status.HTTP_404_NOT_FOUND)
