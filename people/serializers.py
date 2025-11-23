"""
Serializers for the people app models.
"""

from rest_framework import serializers

from .models import Face, Person, PersonMergeSuggestion


class PersonSerializer(serializers.ModelSerializer):
    """Serializer for Person model"""

    face_count = serializers.SerializerMethodField()
    asset_count = serializers.SerializerMethodField()
    headshot_url = serializers.SerializerMethodField()

    class Meta:
        model = Person
        fields = [
            "id",
            "display_name",
            "aka",
            "notes",
            "face_count",
            "asset_count",
            "headshot_url",
            "embedding_centroid",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "face_count", "asset_count", "headshot_url", "created_at", "updated_at"]

    def get_face_count(self, obj):
        """Get the number of faces for this person."""
        return obj.faces.count()

    def get_asset_count(self, obj):
        """Get the number of unique assets this person appears in."""
        return obj.faces.values("asset").distinct().count()

    def get_headshot_url(self, obj):
        """Get URL for the person's headshot face thumbnail if available."""
        try:
            face = obj.headshot_face
            if face and hasattr(face, "thumbnail") and face.thumbnail and face.thumbnail.is_ready:
                return face.thumbnail.storage_url
            # Fallback: use any available face thumbnail for this person (best quality first)
            from .models import Face

            candidate = (
                Face.objects.select_related("thumbnail")
                .filter(person=obj, thumbnail__is_ready=True)
                .order_by("-quality", "-detection_confidence", "-created_at")
                .first()
            )
            if candidate and candidate.thumbnail:
                return candidate.thumbnail.storage_url
        except Exception:
            pass
        return None


class FaceSerializer(serializers.ModelSerializer):
    """Serializer for Face model"""

    person_name = serializers.CharField(source="person.display_name", read_only=True)
    asset_id = serializers.CharField(source="asset.id", read_only=True)
    thumbnail_url = serializers.SerializerMethodField()
    person_headshot_url = serializers.SerializerMethodField()

    class Meta:
        model = Face
        fields = [
            "id",
            "asset",
            "asset_id",
            "person",
            "person_name",
            "x",
            "y",
            "w",
            "h",
            "embedding",
            "quality",
            "detection_confidence",
            "thumbnail_url",
            "person_headshot_url",
            "detection_model",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "asset_id", "person_name", "created_at", "updated_at"]

    def get_thumbnail_url(self, obj):
        try:
            # Use the face thumbnail if ready
            if hasattr(obj, "thumbnail") and obj.thumbnail and obj.thumbnail.is_ready:
                return obj.thumbnail.storage_url
        except Exception:
            pass
        return None

    def get_person_headshot_url(self, obj):
        try:
            person = obj.person
            if not person:
                return None
            headshot = person.headshot_face
            # Prefer the headshot face's thumbnail
            if headshot and hasattr(headshot, "thumbnail") and headshot.thumbnail and headshot.thumbnail.is_ready:
                return headshot.thumbnail.storage_url
        except Exception:
            pass
        return None


class PersonMergeSuggestionSerializer(serializers.ModelSerializer):
    """Serializer for PersonMergeSuggestion model"""

    person_a_name = serializers.CharField(source="person_a.display_name", read_only=True)
    person_b_name = serializers.CharField(source="person_b.display_name", read_only=True)

    class Meta:
        model = PersonMergeSuggestion
        fields = [
            "id",
            "person_a",
            "person_a_name",
            "person_b",
            "person_b_name",
            "cosine_similarity",
            "confidence_score",
            "status",
            "reviewed_by",
            "reviewed_at",
            "created_at",
        ]
        read_only_fields = ["id", "person_a_name", "person_b_name", "created_at"]


class PersonMergeRequestSerializer(serializers.Serializer):
    """Serializer for person merge requests"""

    target_person_id = serializers.UUIDField()
    source_person_ids = serializers.ListField(
        child=serializers.UUIDField(), min_length=1, help_text="List of person IDs to merge into the target person"
    )
    delete_source_persons = serializers.BooleanField(
        default=True, help_text="Whether to delete source persons after merging"
    )


class FaceAssignmentSerializer(serializers.Serializer):
    """Serializer for face assignment requests"""

    face_ids = serializers.ListField(
        child=serializers.UUIDField(), min_length=1, help_text="List of face IDs to assign"
    )
    person_id = serializers.UUIDField(help_text="Person ID to assign faces to")


class FaceUnassignmentSerializer(serializers.Serializer):
    """Serializer for face unassignment requests"""

    face_ids = serializers.ListField(
        child=serializers.UUIDField(), min_length=1, help_text="List of face IDs to unassign (set person to null)"
    )


class ManualFaceCreateSerializer(serializers.Serializer):
    """Serializer for creating a manual face selection on an asset."""

    asset_id = serializers.UUIDField()
    # Normalized coordinates (0..1) in image coordinates
    x = serializers.FloatField(min_value=0.0, max_value=1.0)
    y = serializers.FloatField(min_value=0.0, max_value=1.0)
    w = serializers.FloatField(min_value=0.0, max_value=1.0)
    h = serializers.FloatField(min_value=0.0, max_value=1.0)
    # Optional person assignment
    person_id = serializers.UUIDField(required=False, allow_null=True)
