import uuid

from django.db import models


class StorageBackend(models.Model):
    """
    Storage backend configuration for local filesystem storage.

    Users can configure multiple storage backends with different base paths,
    allowing for organizing photos across different drives or locations.
    """

    BACKEND_TYPES = [
        ("local", "Local File System"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, unique=True)
    backend_type = models.CharField(max_length=10, choices=BACKEND_TYPES, default="local")

    # Configuration (must contain 'base_path' for local storage)
    config = models.JSONField(
        default=dict,
        blank=True,
        help_text="Configuration dict. For local storage, must contain 'base_path' key with filesystem path.",
    )

    # Status
    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "storage_backends"
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.get_backend_type_display()})"

    def get_base_path(self):
        """
        Get the base filesystem path for this storage backend.

        Returns:
            Path: Base path for file storage

        Raises:
            ValidationError: If base_path is not configured
        """
        from pathlib import Path

        from django.core.exceptions import ValidationError

        base_path = self.config.get("base_path")
        if not base_path:
            raise ValidationError(f"Storage backend '{self.name}' has no base_path configured")

        return Path(base_path)


class StorageBucket(models.Model):
    """
    Storage bucket/container within a backend.
    Multiple buckets can exist per backend (e.g., originals, thumbnails).
    """

    BUCKET_PURPOSES = [
        ("originals", "Original Photos"),
        ("thumbnails", "Thumbnails"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    backend = models.ForeignKey(StorageBackend, on_delete=models.CASCADE, related_name="buckets")

    # Bucket identification
    name = models.CharField(max_length=255)  # Actual bucket name in storage backend
    display_name = models.CharField(max_length=100)  # Human-readable name
    purpose = models.CharField(max_length=20, choices=BUCKET_PURPOSES, default="originals")

    # Bucket settings
    is_public = models.BooleanField(default=False)
    path_prefix = models.CharField(max_length=500, blank=True)  # Optional path prefix within bucket

    # Status
    is_active = models.BooleanField(default=True)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "storage_buckets"
        unique_together = ["backend", "name"]
        ordering = ["backend", "purpose", "display_name"]

    def __str__(self):
        return f"{self.display_name} ({self.backend.name})"

    @property
    def full_path_prefix(self):
        """Get the full path including any backend-specific prefixes."""
        return self.path_prefix.rstrip("/") if self.path_prefix else ""


class Asset(models.Model):
    """
    Core asset model representing photos in the system.
    Based on the project plan's assets table schema.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sha256 = models.CharField(max_length=64, unique=True, db_index=True)

    # Owner of the asset
    owner = models.ForeignKey("auth.User", on_delete=models.CASCADE, related_name="owned_assets")

    # Storage information
    storage_bucket = models.ForeignKey(StorageBucket, on_delete=models.PROTECT, related_name="assets")
    storage_key = models.CharField(max_length=1024)
    mime_type = models.CharField(max_length=100)

    # Image metadata
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    taken_at = models.DateTimeField(null=True, blank=True)
    description = models.TextField(blank=True, help_text="User-provided description or caption for this photo")

    # Perceptual hash for duplicate detection
    phash = models.CharField(max_length=16, blank=True, db_index=True)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["-taken_at", "-created_at"]),
            models.Index(fields=["owner"]),
            models.Index(fields=["storage_bucket"]),
        ]
        ordering = ["-taken_at", "-created_at"]

    def __str__(self):
        return f"Photo {self.id} ({self.mime_type})"

    @property
    def storage_url(self):
        """Get the Django proxy URL for this asset."""
        # Always use Django proxy for secure access control
        # This proxies requests to the actual storage backend (MinIO, S3, etc.)
        from django.conf import settings

        # Use absolute URL to Django backend
        backend_url = getattr(settings, "BACKEND_URL", "http://localhost:8000")
        return f"{backend_url}/images/{self.storage_bucket.id}/{self.storage_key}"

    @property
    def storage_path(self):
        """Get the full storage path including bucket prefix."""
        prefix = self.storage_bucket.full_path_prefix
        if prefix:
            return f"{prefix}/{self.storage_key}"
        return self.storage_key


class AssetThumbnail(models.Model):
    """
    Thumbnails for assets at different sizes.
    Generated asynchronously by workers for efficient loading.
    """

    THUMBNAIL_SIZES = [
        ("sm", "Small (300px)"),
        ("md", "Medium (600px)"),
        ("preview", "Preview (2048px)"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name="thumbnails")
    size = models.CharField(max_length=10, choices=THUMBNAIL_SIZES)

    # Storage information
    storage_bucket = models.ForeignKey(StorageBucket, on_delete=models.CASCADE, related_name="thumbnails")
    storage_key = models.CharField(max_length=1024)

    # Image metadata
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    file_size = models.PositiveIntegerField()  # Size in bytes

    # Processing status
    is_ready = models.BooleanField(default=False)
    generated_at = models.DateTimeField(null=True, blank=True)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ["asset", "size"]
        indexes = [
            models.Index(fields=["asset", "size"]),
            models.Index(fields=["is_ready"]),
        ]
        ordering = ["size"]

    def __str__(self):
        return f"Thumbnail {self.get_size_display()} for {self.asset.id}"

    @property
    def storage_url(self):
        """Get the Django proxy URL for this thumbnail."""
        from django.conf import settings

        backend_url = getattr(settings, "BACKEND_URL", "http://localhost:8000")
        return f"{backend_url}/thumbnails/{self.storage_bucket.id}/{self.storage_key}"

    @classmethod
    def get_size_pixels(cls, size):
        """Get the pixel size for a thumbnail size."""
        size_map = {
            "sm": 300,
            "md": 600,
            "preview": 2048,
        }
        return size_map.get(size, 300)


class Like(models.Model):
    """
    Likes for assets.
    Users can like photos they have access to.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name="likes")
    user = models.ForeignKey("auth.User", on_delete=models.CASCADE, related_name="liked_assets")

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "asset_likes"
        unique_together = ["asset", "user"]
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["asset"]),
            models.Index(fields=["user"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"{self.user.username} likes {self.asset.id}"


class Comment(models.Model):
    """
    Comments on assets.
    Users can comment on photos they have access to.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name="comments")
    user = models.ForeignKey("auth.User", on_delete=models.CASCADE, related_name="asset_comments")
    content = models.TextField()

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "asset_comments"
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["asset"]),
            models.Index(fields=["user"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"Comment by {self.user.username} on {self.asset.id}"
