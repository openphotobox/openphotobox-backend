import uuid
from django.db import models
from django.contrib.auth.models import User
from django.contrib.postgres.fields import ArrayField


class StorageBackend(models.Model):
    """
    Storage backend configuration for different storage providers.
    
    S3/MinIO are the first-class citizens and recommended for production use.
    They provide efficient presigned URL uploads, scalability, and reliability.
    
    LocalFS is provided but not recommended for production.
    """
    BACKEND_TYPES = [
        ('s3', 'Amazon S3'),
        ('minio', 'MinIO'),
        ('local', 'Local File System'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, unique=True)
    backend_type = models.CharField(max_length=10, choices=BACKEND_TYPES)
    

    endpoint_url = models.URLField(blank=True)
    region = models.CharField(max_length=50, blank=True)
    
    # Configuration (can be extended for different backends)
    config = models.JSONField(default=dict, blank=True)
    
    # Status
    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False)
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'storage_backends'
        ordering = ['name']
    
    def __str__(self):
        return f"{self.name} ({self.get_backend_type_display()})"


class StorageBucket(models.Model):
    """
    Storage bucket/container within a backend.
    Multiple buckets can exist per backend (e.g., originals, thumbnails).
    """
    BUCKET_PURPOSES = [
        ('originals', 'Original Photos'),
        ('thumbnails', 'Thumbnails'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    backend = models.ForeignKey(StorageBackend, on_delete=models.CASCADE, related_name='buckets')
    
    # Bucket identification
    name = models.CharField(max_length=255)  # Actual bucket name in storage backend
    display_name = models.CharField(max_length=100)  # Human-readable name
    purpose = models.CharField(max_length=20, choices=BUCKET_PURPOSES, default='originals')
    
    # Bucket settings
    is_public = models.BooleanField(default=False)
    path_prefix = models.CharField(max_length=500, blank=True)  # Optional path prefix within bucket
    
    # Status
    is_active = models.BooleanField(default=True)
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'storage_buckets'
        unique_together = ['backend', 'name']
        ordering = ['backend', 'purpose', 'display_name']
    
    def __str__(self):
        return f"{self.display_name} ({self.backend.name})"
    
    @property
    def full_path_prefix(self):
        """Get the full path including any backend-specific prefixes."""
        return self.path_prefix.rstrip('/') if self.path_prefix else ''


class Asset(models.Model):
    """
    Core asset model representing photos in the system.
    Based on the project plan's assets table schema.
    """
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sha256 = models.CharField(max_length=64, unique=True, db_index=True)
    
    # Storage information
    storage_bucket = models.ForeignKey(StorageBucket, on_delete=models.PROTECT, related_name='assets')
    storage_key = models.CharField(max_length=1024)
    mime_type = models.CharField(max_length=100)
    
    # Image metadata
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    taken_at = models.DateTimeField(null=True, blank=True)
    description = models.TextField(
        blank=True, 
        help_text="User-provided description or caption for this photo"
    )

    # Perceptual hash for duplicate detection
    phash = models.CharField(max_length=16, blank=True, db_index=True)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Photo {self.id} ({self.mime_type})"
    
    @property
    def storage_url(self):
        """Get the Django proxy URL for this asset."""
        # Always use Django proxy for secure access control
        # This proxies requests to the actual storage backend (MinIO, S3, etc.)
        from django.conf import settings
        
        # Use absolute URL to Django backend
        backend_url = getattr(settings, 'BACKEND_URL', 'http://localhost:8000')
        return f"{backend_url}/images/{self.storage_bucket.id}/{self.storage_key}"
    
    @property
    def storage_path(self):
        """Get the full storage path including bucket prefix."""
        prefix = self.storage_bucket.full_path_prefix
        if prefix:
            return f"{prefix}/{self.storage_key}"
        return self.storage_key


class Album(models.Model):
    """
    Albums for organizing photos.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    
    # Cover photo for the album
    cover_asset = models.ForeignKey(
        Asset, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='cover_for_albums'
    )
    
    # Many-to-many relationship with photos
    assets = models.ManyToManyField(Asset, through='AlbumAsset', related_name='albums')
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'albums'
        ordering = ['title']
    
    def __str__(self):
        return self.title


class AlbumAsset(models.Model):
    """
    Through model for Album-Photo many-to-many relationship with ordering.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    album = models.ForeignKey(Album, on_delete=models.CASCADE)
    asset = models.ForeignKey(Asset, on_delete=models.CASCADE)
    order = models.PositiveIntegerField(default=0)
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'album_assets'
        unique_together = ['album', 'asset']
        ordering = ['order', 'created_at']


class UploadBatch(models.Model):
    """
    Tracks batches of uploads for processing.
    """
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, blank=True)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    
    # Upload metadata
    total_files = models.PositiveIntegerField(default=0)
    processed_files = models.PositiveIntegerField(default=0)
    failed_files = models.PositiveIntegerField(default=0)
    
    # Default metadata for this batch
    default_keywords = ArrayField(models.CharField(max_length=100), default=list, blank=True)
    default_album = models.ForeignKey(
        Album, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True
    )
    
    # Created by user
    created_by = models.ForeignKey(User, on_delete=models.CASCADE)
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        db_table = 'upload_batches'
        ordering = ['-created_at']
    
    def __str__(self):
        return f"Upload batch {self.name or self.id}"


class AssetThumbnail(models.Model):
    """
    Thumbnails for assets at different sizes.
    Generated asynchronously by workers for efficient loading.
    """
    THUMBNAIL_SIZES = [
        ('xs', 'Extra Small (150px)'),
        ('sm', 'Small (300px)'),
        ('md', 'Medium (600px)'),
        ('lg', 'Large (1200px)'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name='thumbnails')
    size = models.CharField(max_length=2, choices=THUMBNAIL_SIZES)
    
    # Storage information
    storage_bucket = models.ForeignKey(StorageBucket, on_delete=models.CASCADE, related_name='thumbnails')
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
        unique_together = ['asset', 'size']
        indexes = [
            models.Index(fields=['asset', 'size']),
            models.Index(fields=['is_ready']),
        ]
        ordering = ['size']
    
    def __str__(self):
        return f"Thumbnail {self.get_size_display()} for {self.asset.id}"
    
    @property
    def storage_url(self):
        """Get the Django proxy URL for this thumbnail."""
        from django.conf import settings
        backend_url = getattr(settings, 'BACKEND_URL', 'http://localhost:8000')
        return f"{backend_url}/thumbnails/{self.storage_bucket.id}/{self.storage_key}"
    
    @classmethod
    def get_size_pixels(cls, size):
        """Get the pixel size for a thumbnail size."""
        size_map = {
            'xs': 150,
            'sm': 300,
            'md': 600,
            'lg': 1200,
        }
        return size_map.get(size, 300)
