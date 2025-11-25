from rest_framework import serializers

from .models import Asset, Comment, Like, StorageBackend, StorageBucket


class StorageBackendSerializer(serializers.ModelSerializer):
    """Serializer for StorageBackend model"""

    class Meta:
        model = StorageBackend
        fields = [
            "id",
            "name",
            "backend_type",
            "config",
            "is_active",
            "is_default",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class StorageBucketSerializer(serializers.ModelSerializer):
    """Serializer for StorageBucket model"""

    backend_name = serializers.CharField(source="backend.name", read_only=True)

    class Meta:
        model = StorageBucket
        fields = [
            "id",
            "backend",
            "backend_name",
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

    # Owner information
    owner_username = serializers.CharField(source="owner.username", read_only=True)
    can_edit = serializers.SerializerMethodField()

    # Thumbnail URLs for different sizes
    thumbnail_url = serializers.SerializerMethodField()
    thumbnail_urls = serializers.SerializerMethodField()
    preview_url = serializers.SerializerMethodField()
    original_url = serializers.CharField(read_only=True)

    # Include related metadata
    metadata = serializers.SerializerMethodField()
    faces = serializers.SerializerMethodField()

    # Likes and comments
    likes_count = serializers.SerializerMethodField()
    comments_count = serializers.SerializerMethodField()
    liked_by_user = serializers.SerializerMethodField()
    likes = serializers.SerializerMethodField()
    comments = serializers.SerializerMethodField()

    class Meta:
        model = Asset
        fields = [
            "id",
            "sha256",
            "owner",
            "owner_username",
            "can_edit",
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
            "preview_url",
            "original_url",
            "metadata",
            "faces",
            "likes_count",
            "comments_count",
            "liked_by_user",
            "likes",
            "comments",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "sha256",
            "owner",
            "owner_username",
            "can_edit",
            "storage_url",
            "storage_path",
            "keyword_names",
            "thumbnail_url",
            "thumbnail_urls",
            "preview_url",
            "original_url",
            "metadata",
            "faces",
            "likes_count",
            "comments_count",
            "liked_by_user",
            "likes",
            "comments",
            "created_at",
            "updated_at",
        ]

    def get_can_edit(self, obj):
        """Check if current user can edit this asset"""
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return False
        return obj.owner == request.user

    def get_thumbnail_url(self, obj):
        """Get the best available thumbnail URL (prefer medium size)"""
        # Try to get medium thumbnail first, then fall back to small
        thumbnail = obj.thumbnails.filter(is_ready=True, size="md").first()
        if not thumbnail:
            thumbnail = obj.thumbnails.filter(is_ready=True, size="sm").first()

        if thumbnail:
            return thumbnail.storage_url

        # Fallback to original image if no thumbnails available
        return obj.storage_url

    def get_thumbnail_urls(self, obj):
        """Get all available thumbnail URLs by size"""
        thumbnails = obj.thumbnails.filter(is_ready=True)
        return {thumbnail.size: thumbnail.storage_url for thumbnail in thumbnails}

    def get_preview_url(self, obj):
        """Get the preview image URL for fullscreen viewing (2048px)"""
        preview = obj.thumbnails.filter(is_ready=True, size="preview").first()
        if preview:
            return preview.storage_url
        # Fallback to original if preview not available
        return obj.storage_url

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

    def get_likes_count(self, obj):
        """Get the number of likes for this asset"""
        return obj.likes.count()

    def get_comments_count(self, obj):
        """Get the number of comments for this asset"""
        return obj.comments.count()

    def get_liked_by_user(self, obj):
        """Check if the current user has liked this asset"""
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return False
        return obj.likes.filter(user=request.user).exists()

    def get_likes(self, obj):
        """Get all likes for this asset as a list of full names"""
        likes = obj.likes.select_related("user").all()
        names = []
        for like in likes:
            user = like.user
            # Combine first_name and last_name, fallback to username if names not set
            full_name = f"{user.first_name} {user.last_name}".strip()
            if not full_name:
                full_name = user.username
            names.append(full_name)
        return names

    def get_comments(self, obj):
        """Get all comments for this asset"""
        comments = obj.comments.select_related("user").all()
        return CommentSerializer(comments, many=True, context=self.context).data


class AssetGallerySerializer(serializers.ModelSerializer):
    """Lightweight serializer for gallery listing.
    Includes only fields needed to render the grid and viewer.
    """

    thumbnail_url = serializers.SerializerMethodField()
    preview_url = serializers.SerializerMethodField()
    original_url = serializers.SerializerMethodField()
    likes_count = serializers.SerializerMethodField()
    comments_count = serializers.SerializerMethodField()
    liked_by_user = serializers.SerializerMethodField()

    class Meta:
        model = Asset
        fields = [
            "id",
            "width",
            "height",
            "taken_at",
            "created_at",
            "thumbnail_url",
            "preview_url",
            "original_url",
            "storage_url",
            "mime_type",
            "description",
            "likes_count",
            "comments_count",
            "liked_by_user",
        ]
        read_only_fields = fields

    def get_thumbnail_url(self, obj):
        thumbnail = obj.thumbnails.filter(is_ready=True, size="sm").first()
        if not thumbnail:
            thumbnail = obj.thumbnails.filter(is_ready=True, size="md").first()
        return thumbnail.storage_url if thumbnail else obj.storage_url

    def get_preview_url(self, obj):
        preview = obj.thumbnails.filter(is_ready=True, size="preview").first()
        return preview.storage_url if preview else obj.storage_url

    def get_original_url(self, obj):
        return obj.storage_url

    def get_likes_count(self, obj):
        """Get the number of likes for this asset"""
        return obj.likes.count()

    def get_comments_count(self, obj):
        """Get the number of comments for this asset"""
        return obj.comments.count()

    def get_liked_by_user(self, obj):
        """Check if the current user has liked this asset"""
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return False
        return obj.likes.filter(user=request.user).exists()


class LikeSerializer(serializers.ModelSerializer):
    """Serializer for Like model"""

    username = serializers.CharField(source="user.username", read_only=True)

    class Meta:
        model = Like
        fields = ["id", "asset", "user", "username", "created_at"]
        read_only_fields = ["id", "user", "username", "created_at"]


class CommentSerializer(serializers.ModelSerializer):
    """Serializer for Comment model"""

    username = serializers.CharField(source="user.username", read_only=True)
    can_edit = serializers.SerializerMethodField()

    class Meta:
        model = Comment
        fields = ["id", "asset", "user", "username", "content", "created_at", "updated_at", "can_edit"]
        read_only_fields = ["id", "user", "username", "created_at", "updated_at", "can_edit"]

    def get_can_edit(self, obj):
        """Check if current user can edit this comment"""
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return False
        return obj.user == request.user
