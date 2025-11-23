import uuid
from django.db import models
from django.contrib.postgres.fields import ArrayField
from pgvector.django import VectorField


class AssetMetadata(models.Model):
    """
    Extended metadata for assets including EXIF, IPTC, and XMP data.
    Separated from core Asset model for cleaner organization.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    # One-to-one relationship with Asset
    asset = models.OneToOneField(
        'assets.Asset',
        on_delete=models.CASCADE,
        related_name='metadata'
    )
    
    # EXIF data (raw JSON storage)
    exif_data = models.JSONField(default=dict, blank=True)
    
    # Camera and shooting information
    camera_make = models.CharField(max_length=100, blank=True)
    camera_model = models.CharField(max_length=100, blank=True)
    lens_model = models.CharField(max_length=100, blank=True)
    
    # Shooting parameters
    iso = models.PositiveIntegerField(null=True, blank=True)
    aperture = models.CharField(max_length=10, null=True, blank=True)  # e.g., "f/2.8"
    shutter_speed = models.CharField(max_length=20, null=True, blank=True)  # e.g., "1/60"
    focal_length = models.CharField(max_length=20, null=True, blank=True)  # e.g., "35mm"
    
    # GPS data
    gps_latitude = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    gps_longitude = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    gps_altitude = models.FloatField(null=True, blank=True)
    location_name = models.CharField(max_length=255, blank=True)
    
    # Color and technical data
    color_space = models.CharField(max_length=50, blank=True)
    white_balance = models.CharField(max_length=50, blank=True)
    flash_fired = models.BooleanField(null=True, blank=True)
    
    # Processing information
    software = models.CharField(max_length=100, blank=True)
    processing_notes = models.TextField(blank=True)
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'asset_metadata'
        indexes = [
            models.Index(fields=['camera_make', 'camera_model']),
            models.Index(fields=['iso']),
            models.Index(fields=['gps_latitude', 'gps_longitude']),
        ]
    
    def __str__(self):
        return f"Metadata for {self.asset_id}"


class KeywordTag(models.Model):
    """
    Hierarchical keyword/tag system for better organization.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    
    # Hierarchical structure
    parent = models.ForeignKey(
        'self',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='children'
    )
    
    # Usage statistics
    usage_count = models.PositiveIntegerField(default=0)
    
    # Color coding for UI
    color = models.CharField(max_length=7, blank=True)  # Hex color code
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'keyword_tags'
        ordering = ['name']
        indexes = [
            models.Index(fields=['parent']),
            models.Index(fields=['-usage_count']),
        ]
    
    def __str__(self):
        if self.parent:
            return f"{self.parent.name} > {self.name}"
        return self.name


class AssetKeyword(models.Model):
    """
    Many-to-many relationship between assets and keywords with additional metadata.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    asset = models.ForeignKey('assets.Asset', on_delete=models.CASCADE)
    keyword = models.ForeignKey(KeywordTag, on_delete=models.CASCADE)
    
    # How was this keyword assigned?
    ASSIGNMENT_SOURCES = [
        ('manual', 'Manual'),
        ('exif', 'EXIF Data'),
        ('ai', 'AI Generated'),
        ('batch', 'Batch Import'),
    ]
    source = models.CharField(max_length=10, choices=ASSIGNMENT_SOURCES, default='manual')
    confidence = models.FloatField(default=1.0)  # For AI-generated keywords
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'asset_keywords'
        unique_together = ['asset', 'keyword']
        indexes = [
            models.Index(fields=['source']),
            models.Index(fields=['-confidence']),
        ]


class ClipEmbedding(models.Model):
    """
    CLIP embeddings for semantic search, separated for better organization.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    # One-to-one relationship with Asset
    asset = models.OneToOneField(
        'assets.Asset',
        on_delete=models.CASCADE,
        related_name='clip_embedding'
    )
    
    # The actual embedding vector (512 dimensions for ViT-B/32)
    embedding = VectorField(dimensions=512)
    
    # Model information
    model_name = models.CharField(max_length=100, default='ViT-B/32')
    model_version = models.CharField(max_length=50, blank=True)
    
    # Processing metadata
    processing_time_ms = models.PositiveIntegerField(null=True, blank=True)
    image_size_used = models.CharField(max_length=20, blank=True)  # e.g., "224x224"
    
    # Quality metrics
    embedding_norm = models.FloatField(null=True, blank=True)
    confidence_score = models.FloatField(null=True, blank=True)
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'clip_embeddings'
        indexes = [
            models.Index(fields=['model_name']),
            models.Index(fields=['-created_at']),
        ]
    
    def __str__(self):
        return f"CLIP embedding for {self.asset_id} ({self.model_name})"


class XmpSidecar(models.Model):
    """
    Track XMP sidecar file status and metadata.
    """
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('writing', 'Writing'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    asset = models.OneToOneField(
        'assets.Asset',
        on_delete=models.CASCADE,
        related_name='xmp_sidecar'
    )
    
    # Sidecar file information
    sidecar_path = models.CharField(max_length=1024, blank=True)
    sidecar_size = models.PositiveIntegerField(null=True, blank=True)
    sidecar_checksum = models.CharField(max_length=64, blank=True)
    
    # Status tracking
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    last_write_attempt = models.DateTimeField(null=True, blank=True)
    write_error = models.TextField(blank=True)
    
    # What data is included in the sidecar
    includes_faces = models.BooleanField(default=False)
    includes_keywords = models.BooleanField(default=False)
    includes_caption = models.BooleanField(default=False)
    includes_gps = models.BooleanField(default=False)
    
    # Version tracking
    version = models.PositiveIntegerField(default=1)
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'xmp_sidecars'
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['-last_write_attempt']),
        ]
    
    def __str__(self):
        return f"XMP sidecar for {self.asset_id} ({self.status})"
