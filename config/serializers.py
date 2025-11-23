from rest_framework import serializers

from .models import ServerConfiguration


class ServerFeaturesSerializer(serializers.ModelSerializer):
    """Serializer for server features (feature flags)"""

    class Meta:
        model = ServerConfiguration
        fields = [
            "config_file",
            "duplicate_detection",
            "email",
            "facial_recognition",
            "import_faces",
            "map",
            "oauth",
            "oauth_auto_launch",
            "password_login",
            "reverse_geocoding",
            "search",
            "sidecar",
            "smart_search",
            "trash",
        ]


class ServerConfigSerializer(serializers.ModelSerializer):
    """Serializer for server configuration"""

    class Meta:
        model = ServerConfiguration
        fields = [
            "external_domain",
            "is_initialized",
            "is_onboarded",
            "login_page_message",
            "map_dark_style_url",
            "map_light_style_url",
            "oauth_button_text",
            "public_users",
            "trash_days",
            "user_delete_delay",
        ]


class ServerMediaTypesSerializer(serializers.ModelSerializer):
    """Serializer for supported media types"""

    image = serializers.ListField(source="supported_image_types")
    sidecar = serializers.ListField(source="supported_sidecar_types")
    video = serializers.ListField(source="supported_video_types")

    class Meta:
        model = ServerConfiguration
        fields = ["image", "sidecar", "video"]
