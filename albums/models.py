import uuid

from django.db import models


class Album(models.Model):
    """
    Albums for organizing photos.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Owner of the album
    owner = models.ForeignKey("auth.User", on_delete=models.CASCADE, related_name="owned_albums")

    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)

    # Cover photo for the album
    cover_asset = models.ForeignKey(
        "assets.Asset", on_delete=models.SET_NULL, null=True, blank=True, related_name="cover_for_albums"
    )

    # Many-to-many relationship with photos
    assets = models.ManyToManyField("assets.Asset", through="AlbumAsset", related_name="albums")

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "albums"
        ordering = ["title"]
        indexes = [
            models.Index(fields=["owner"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return self.title


class AlbumAsset(models.Model):
    """
    Through model for Album-Photo many-to-many relationship with ordering.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    album = models.ForeignKey(Album, on_delete=models.CASCADE)
    asset = models.ForeignKey("assets.Asset", on_delete=models.CASCADE)
    order = models.PositiveIntegerField(default=0)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "album_assets"
        unique_together = ["album", "asset"]
        ordering = ["order", "created_at"]


class AlbumShare(models.Model):
    """
    Sharing permissions for albums.
    Allows album owners to share their albums with other users.
    """

    PERMISSION_CHOICES = [
        ("view", "View"),
        ("contribute", "Contribute"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # The album being shared
    album = models.ForeignKey(Album, on_delete=models.CASCADE, related_name="shares")

    # User the album is shared with
    shared_with = models.ForeignKey("auth.User", on_delete=models.CASCADE, related_name="shared_albums")

    # Permission level: view (read-only) or contribute (can add photos)
    permission_level = models.CharField(max_length=20, choices=PERMISSION_CHOICES, default="view")

    # User who created the share (typically the album owner)
    shared_by = models.ForeignKey("auth.User", on_delete=models.CASCADE, related_name="created_shares")

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "album_shares"
        unique_together = ["album", "shared_with"]
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["album"]),
            models.Index(fields=["shared_with"]),
            models.Index(fields=["permission_level"]),
        ]

    def __str__(self):
        return f"{self.album.title} shared with {self.shared_with.username} ({self.permission_level})"
