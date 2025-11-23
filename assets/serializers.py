from rest_framework import serializers

from .models import Album, Asset, StorageBackend, StorageBucket, UploadBatch


class StorageBackendSerializer(serializers.ModelSerializer):
    """Serializer for StorageBackend model"""

    class Meta:
        model = StorageBackend
        fields = [
            "id",
            "name",
            "backend_type",
            "endpoint_url",
            "region",
            "is_active",
            "is_default",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class StorageBucketSerializer(serializers.ModelSerializer):
    """Serializer for StorageBucket model"""

    backend_name = serializers.CharField(source="backend.name", read_only=True)
    backend_type = serializers.CharField(source="backend.backend_type", read_only=True)

    class Meta:
        model = StorageBucket
        fields = [
            "id",
            "backend",
            "backend_name",
            "backend_type",
            "name",
            "display_name",
            "purpose",
            "is_public",
            "path_prefix",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class AssetSerializer(serializers.ModelSerializer):
    """Serializer for photo Asset model"""

    storage_bucket_name = serializers.CharField(source="storage_bucket.display_name", read_only=True)
    storage_backend_name = serializers.CharField(source="storage_bucket.backend.name", read_only=True)
    storage_url = serializers.CharField(read_only=True)
    storage_path = serializers.CharField(read_only=True)
    keyword_names = serializers.ListField(child=serializers.CharField(), read_only=True)

    # Thumbnail URLs for different sizes
    thumbnail_url = serializers.SerializerMethodField()
    thumbnail_urls = serializers.SerializerMethodField()
    original_url = serializers.CharField(read_only=True)

    # Include related metadata
    metadata = serializers.SerializerMethodField()
    faces = serializers.SerializerMethodField()

    class Meta:
        model = Asset
        fields = [
            "id",
            "sha256",
            "storage_bucket",
            "storage_bucket_name",
            "storage_backend_name",
            "storage_key",
            "storage_url",
            "storage_path",
            "mime_type",
            "width",
            "height",
            "taken_at",
            "description",
            "keyword_names",
            "phash",
            "thumbnail_url",
            "thumbnail_urls",
            "original_url",
            "metadata",
            "faces",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "sha256",
            "storage_url",
            "storage_path",
            "keyword_names",
            "thumbnail_url",
            "thumbnail_urls",
            "original_url",
            "metadata",
            "faces",
            "created_at",
            "updated_at",
        ]

    def get_thumbnail_url(self, obj):
        """Get the best available thumbnail URL (prefer medium size)"""
        # Try to get medium thumbnail first, then fall back to others
        thumbnail = obj.thumbnails.filter(is_ready=True, size="md").first()
        if not thumbnail:
            thumbnail = obj.thumbnails.filter(is_ready=True, size="sm").first()
        if not thumbnail:
            thumbnail = obj.thumbnails.filter(is_ready=True, size="lg").first()
        if not thumbnail:
            thumbnail = obj.thumbnails.filter(is_ready=True, size="xs").first()

        if thumbnail:
            return thumbnail.storage_url

        # Fallback to original image if no thumbnails available
        return obj.storage_url

    def get_thumbnail_urls(self, obj):
        """Get all available thumbnail URLs by size"""
        thumbnails = obj.thumbnails.filter(is_ready=True)
        return {thumbnail.size: thumbnail.storage_url for thumbnail in thumbnails}

    def get_original_url(self, obj):
        """Get the original full-size image URL"""
        return obj.storage_url

    def get_metadata(self, obj):
        """Get detailed metadata if available"""
        try:
            from metadata.serializers import AssetMetadataSerializer

            if hasattr(obj, "metadata"):
                return AssetMetadataSerializer(obj.metadata).data
        except ImportError:
            pass
        return None

    def get_faces(self, obj):
        """Get face detection data if available"""
        try:
            from people.serializers import FaceSerializer

            if hasattr(obj, "faces"):
                return FaceSerializer(obj.faces.all(), many=True).data
        except ImportError:
            pass
        return []


class AssetGallerySerializer(serializers.ModelSerializer):
    """Lightweight serializer for gallery listing.
    Includes only fields needed to render the grid and viewer.
    """

    thumbnail_url = serializers.SerializerMethodField()
    original_url = serializers.SerializerMethodField()

    class Meta:
        model = Asset
        fields = [
            "id",
            "width",
            "height",
            "taken_at",
            "created_at",
            "thumbnail_url",
            "original_url",
            "storage_url",
            "mime_type",
            "description",
        ]
        read_only_fields = fields

    def get_thumbnail_url(self, obj):
        thumbnail = obj.thumbnails.filter(is_ready=True, size="md").first()
        if not thumbnail:
            thumbnail = obj.thumbnails.filter(is_ready=True, size="sm").first()
        if not thumbnail:
            thumbnail = obj.thumbnails.filter(is_ready=True, size="lg").first()
        if not thumbnail:
            thumbnail = obj.thumbnails.filter(is_ready=True, size="xs").first()
        return thumbnail.storage_url if thumbnail else obj.storage_url

    def get_original_url(self, obj):
        return obj.storage_url


class AlbumSerializer(serializers.ModelSerializer):
    """Serializer for photo Album model"""

    photo_count = serializers.SerializerMethodField()

    class Meta:
        model = Album
        fields = ["id", "title", "description", "cover_asset", "photo_count", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]

    def get_photo_count(self, obj):
        return obj.assets.count()


class UploadBatchSerializer(serializers.ModelSerializer):
    """Serializer for UploadBatch model"""

    progress_percentage = serializers.SerializerMethodField()

    class Meta:
        model = UploadBatch
        fields = [
            "id",
            "name",
            "total_files",
            "processed_files",
            "failed_files",
            "progress_percentage",
            "default_keywords",
            "default_album",
            "created_by",
            "created_at",
            "updated_at",
            "completed_at",
        ]
        read_only_fields = [
            "id",
            "processed_files",
            "failed_files",
            "created_by",
            "created_at",
            "updated_at",
            "completed_at",
        ]

    def get_progress_percentage(self, obj):
        if obj.total_files == 0:
            return 0.0
        return (obj.processed_files / obj.total_files) * 100
