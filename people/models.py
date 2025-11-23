import uuid

from django.db import models
from pgvector.django import VectorField

from assets.models import StorageBucket


class Person(models.Model):
    """
    Person model for face recognition and tagging.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    display_name = models.CharField(max_length=255)
    aka = models.JSONField(default=list, blank=True)  # Alternative names
    notes = models.TextField(blank=True)

    # Reference to the best face for this person (headshot)
    headshot_face = models.ForeignKey(
        "Face", on_delete=models.SET_NULL, null=True, blank=True, related_name="headshot_for_person"
    )

    # Centroid embedding for this person (average of all their face embeddings)
    embedding_centroid = VectorField(dimensions=512, null=True, blank=True)
    embedding_count = models.PositiveIntegerField(default=0)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "people"
        ordering = ["display_name"]

    def __str__(self):
        return self.display_name


class Face(models.Model):
    """
    Face detection results with bounding boxes and embeddings.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Foreign key to Asset (cross-app relationship)
    asset = models.ForeignKey(
        "assets.Asset",  # Cross-app reference
        on_delete=models.CASCADE,
        related_name="faces",
    )

    person = models.ForeignKey(Person, on_delete=models.SET_NULL, null=True, blank=True, related_name="faces")

    # Normalized bounding box coordinates (0.0 to 1.0)
    x = models.FloatField()  # Left
    y = models.FloatField()  # Top
    w = models.FloatField()  # Width
    h = models.FloatField()  # Height

    # Face embedding (512-dimensional float32 array stored as bytes)
    embedding = models.BinaryField()

    # Quality score from face detection
    quality = models.FloatField()

    # Detection metadata
    detection_model = models.CharField(max_length=100, default="InsightFace")
    detection_confidence = models.FloatField(default=0.0)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "faces"
        indexes = [
            models.Index(fields=["asset"]),
            models.Index(fields=["person"]),
            models.Index(fields=["quality"]),
            models.Index(fields=["-detection_confidence"]),
        ]
        ordering = ["-quality", "-detection_confidence"]

    def __str__(self):
        person_name = self.person.display_name if self.person else "Unknown"
        return f"Face in {self.asset_id} - {person_name} (quality: {self.quality:.2f})"


class FaceSearch(models.Model):
    """
    Vector search representation for a face. Stores an L2-normalized 512-d embedding
    for efficient cosine similarity search using pgvector.
    """

    face = models.OneToOneField(Face, on_delete=models.CASCADE, primary_key=True, related_name="face_search")
    embedding = VectorField(dimensions=512)

    class Meta:
        db_table = "face_search"
        indexes = [
            models.Index(fields=["face"]),
        ]


class PersonMergeSuggestion(models.Model):
    """
    Suggestions for merging duplicate people based on embedding similarity.
    """

    STATUS_CHOICES = [
        ("open", "Open"),
        ("accepted", "Accepted"),
        ("rejected", "Rejected"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    person_a = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="merge_suggestions_a")
    person_b = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="merge_suggestions_b")

    # Similarity metrics
    cosine_similarity = models.FloatField()
    confidence_score = models.FloatField()

    # Status tracking
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="open")
    reviewed_by = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "person_merge_suggestions"
        unique_together = ["person_a", "person_b"]
        ordering = ["-cosine_similarity", "-created_at"]

    def __str__(self):
        return f"Merge {self.person_a.display_name} + {self.person_b.display_name} ({self.cosine_similarity:.3f})"


class FaceThumbnail(models.Model):
    """
    Cropped thumbnails of detected faces for quick display in UI.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    face = models.OneToOneField("people.Face", on_delete=models.CASCADE, related_name="thumbnail")

    # Storage information
    storage_bucket = models.ForeignKey(StorageBucket, on_delete=models.CASCADE, related_name="face_thumbnails")
    storage_key = models.CharField(max_length=1024)  # Object key/path within bucket

    # Image metadata
    size = models.PositiveIntegerField(default=128)  # Square thumbnail size
    file_size = models.PositiveIntegerField()  # Size in bytes

    # Processing status
    is_ready = models.BooleanField(default=False)
    generated_at = models.DateTimeField(null=True, blank=True)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "face_thumbnails"
        indexes = [
            models.Index(fields=["face"]),
            models.Index(fields=["is_ready"]),
        ]

    def __str__(self):
        return f"Face thumbnail for {self.face.id}"

    @property
    def storage_url(self):
        """Get the Django proxy URL for this face thumbnail."""
        from django.conf import settings

        backend_url = getattr(settings, "BACKEND_URL", "http://localhost:8000")
        return f"{backend_url}/face-thumbnails/{self.storage_bucket.id}/{self.storage_key}"
