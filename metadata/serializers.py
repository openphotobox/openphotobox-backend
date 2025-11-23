from rest_framework import serializers

from .models import AssetKeyword, AssetMetadata, ClipEmbedding, KeywordTag, XmpSidecar


class AssetMetadataSerializer(serializers.ModelSerializer):
    """Serializer for detailed asset metadata including EXIF data"""

    class Meta:
        model = AssetMetadata
        fields = [
            "id",
            "exif_data",
            "camera_make",
            "camera_model",
            "lens_model",
            "iso",
            "aperture",
            "shutter_speed",
            "focal_length",
            "gps_latitude",
            "gps_longitude",
            "gps_altitude",
            "location_name",
            "color_space",
            "white_balance",
            "flash_fired",
            "software",
            "processing_notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class KeywordTagSerializer(serializers.ModelSerializer):
    """Serializer for keyword tags"""

    class Meta:
        model = KeywordTag
        fields = ["id", "name", "slug", "description", "parent", "usage_count", "color", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]


class AssetKeywordSerializer(serializers.ModelSerializer):
    """Serializer for asset-keyword relationships"""

    keyword = KeywordTagSerializer(read_only=True)

    class Meta:
        model = AssetKeyword
        fields = ["id", "keyword", "source", "confidence", "created_at"]
        read_only_fields = ["id", "created_at"]


class ClipEmbeddingSerializer(serializers.ModelSerializer):
    """Serializer for CLIP embeddings"""

    class Meta:
        model = ClipEmbedding
        fields = [
            "id",
            "model_name",
            "model_version",
            "processing_time_ms",
            "image_size_used",
            "embedding_norm",
            "confidence_score",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class XmpSidecarSerializer(serializers.ModelSerializer):
    """Serializer for XMP sidecar information"""

    class Meta:
        model = XmpSidecar
        fields = [
            "id",
            "sidecar_path",
            "sidecar_size",
            "sidecar_checksum",
            "status",
            "last_write_attempt",
            "write_error",
            "includes_faces",
            "includes_keywords",
            "includes_caption",
            "includes_gps",
            "version",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]
