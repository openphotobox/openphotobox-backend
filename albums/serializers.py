from rest_framework import serializers

from .models import Album, AlbumAsset, AlbumShare


class AlbumSerializer(serializers.ModelSerializer):
    """Serializer for photo Album model"""

    photo_count = serializers.SerializerMethodField()
    owner_username = serializers.CharField(source="owner.username", read_only=True)
    shared_with = serializers.SerializerMethodField()
    can_contribute = serializers.SerializerMethodField()
    is_owner = serializers.SerializerMethodField()

    class Meta:
        model = Album
        fields = [
            "id",
            "owner",
            "owner_username",
            "title",
            "description",
            "cover_asset",
            "photo_count",
            "shared_with",
            "can_contribute",
            "is_owner",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "owner",
            "owner_username",
            "photo_count",
            "shared_with",
            "can_contribute",
            "is_owner",
            "created_at",
            "updated_at",
        ]

    def get_photo_count(self, obj):
        return obj.assets.count()

    def get_shared_with(self, obj):
        """Get list of users this album is shared with"""
        shares = obj.shares.select_related("shared_with").all()
        return [
            {
                "user_id": str(share.shared_with.id),
                "username": share.shared_with.username,
                "permission_level": share.permission_level,
                "shared_at": share.created_at,
            }
            for share in shares
        ]

    def get_can_contribute(self, obj):
        """Check if current user can contribute to this album"""
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return False

        # Owner can always contribute
        if obj.owner == request.user:
            return True

        # Check if user has contribute permission via share
        share = obj.shares.filter(shared_with=request.user).first()
        if share and share.permission_level == "contribute":
            return True

        return False

    def get_is_owner(self, obj):
        """Check if current user is the owner of this album"""
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return False
        return obj.owner == request.user


class AlbumCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating albums"""

    class Meta:
        model = Album
        fields = ["title", "description", "cover_asset"]

    def create(self, validated_data):
        # Set the owner from the request context
        request = self.context.get("request")
        if request and request.user.is_authenticated:
            validated_data["owner"] = request.user
        return super().create(validated_data)


class AlbumAssetSerializer(serializers.ModelSerializer):
    """Serializer for AlbumAsset through model"""

    class Meta:
        model = AlbumAsset
        fields = ["id", "album", "asset", "order", "created_at"]
        read_only_fields = ["id", "created_at"]


class AlbumShareSerializer(serializers.ModelSerializer):
    """Serializer for viewing album shares"""

    album_title = serializers.CharField(source="album.title", read_only=True)
    shared_with_username = serializers.CharField(source="shared_with.username", read_only=True)
    shared_by_username = serializers.CharField(source="shared_by.username", read_only=True)

    class Meta:
        model = AlbumShare
        fields = [
            "id",
            "album",
            "album_title",
            "shared_with",
            "shared_with_username",
            "permission_level",
            "shared_by",
            "shared_by_username",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "album_title",
            "shared_with_username",
            "shared_by_username",
            "created_at",
            "updated_at",
        ]


class AlbumShareCreateSerializer(serializers.Serializer):
    """Serializer for creating album shares"""

    user_id = serializers.UUIDField(required=True)
    permission_level = serializers.ChoiceField(choices=["view", "contribute"], default="view")

    def validate_user_id(self, value):
        """Validate that the user exists"""
        from django.contrib.auth.models import User

        try:
            User.objects.get(id=value)
        except User.DoesNotExist:
            raise serializers.ValidationError("User not found")
        return value


class AlbumShareUpdateSerializer(serializers.Serializer):
    """Serializer for updating album share permissions"""

    permission_level = serializers.ChoiceField(choices=["view", "contribute"], required=True)
