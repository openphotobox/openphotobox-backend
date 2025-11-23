"""
Serializers for the sharing system.
"""
from rest_framework import serializers
from .models import Recipient, AccessGrant, RecipientLink, RecipientAssetRebuildLog


class RecipientSerializer(serializers.ModelSerializer):
    """Serializer for Recipient model."""
    
    access_grant_count = serializers.SerializerMethodField()
    asset_count = serializers.SerializerMethodField()
    
    class Meta:
        model = Recipient
        fields = [
            'id', 'display_name', 'email', 'notes',
            'default_show_faces', 'default_show_names', 'default_allow_downloads',
            'access_grant_count', 'asset_count',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
    
    def get_access_grant_count(self, obj):
        return obj.access_grants.count()
    
    def get_asset_count(self, obj):
        return obj.recipient_assets.count()


class AccessGrantSerializer(serializers.ModelSerializer):
    """Serializer for AccessGrant model."""
    
    target_name = serializers.SerializerMethodField()
    
    class Meta:
        model = AccessGrant
        fields = [
            'id', 'recipient', 'album', 'person', 'grant_type',
            'target_name', 'created_at'
        ]
        read_only_fields = ['id', 'grant_type', 'created_at']
    
    def get_target_name(self, obj):
        if obj.album:
            return obj.album.title
        elif obj.person:
            return obj.person.display_name
        return None
    
    def validate(self, data):
        """Ensure exactly one of album or person is provided."""
        album = data.get('album')
        person = data.get('person')
        
        if not album and not person:
            raise serializers.ValidationError(
                "Either album or person must be specified."
            )
        
        if album and person:
            raise serializers.ValidationError(
                "Cannot specify both album and person in the same grant."
            )
        
        return data


class RecipientLinkSerializer(serializers.ModelSerializer):
    """Serializer for RecipientLink model."""
    
    recipient_name = serializers.CharField(source='recipient.display_name', read_only=True)
    effective_flags = serializers.SerializerMethodField()
    is_expired = serializers.SerializerMethodField()
    
    class Meta:
        model = RecipientLink
        fields = [
            'id', 'recipient', 'recipient_name', 'name',
            'show_faces', 'show_names', 'allow_downloads', 'effective_flags',
            'expires_at', 'is_expired', 'access_count', 'last_accessed_at',
            'created_at', 'updated_at'
        ]
        read_only_fields = [
            'id', 'access_count', 'last_accessed_at', 'created_at', 'updated_at'
        ]
        extra_kwargs = {
            'token_hash': {'write_only': True},
            'password_hash': {'write_only': True},
        }
    
    def get_effective_flags(self, obj):
        return obj.get_effective_flags()
    
    def get_is_expired(self, obj):
        from django.utils import timezone
        return obj.expires_at and obj.expires_at < timezone.now()


class RecipientAssetRebuildLogSerializer(serializers.ModelSerializer):
    """Serializer for RecipientAssetRebuildLog model."""
    
    recipient_name = serializers.CharField(source='recipient.display_name', read_only=True)
    duration_seconds = serializers.SerializerMethodField()
    
    class Meta:
        model = RecipientAssetRebuildLog
        fields = [
            'id', 'recipient', 'recipient_name', 'trigger_type', 'trigger_details',
            'status', 'assets_added', 'assets_removed', 'error_message',
            'duration_seconds', 'started_at', 'completed_at'
        ]
        read_only_fields = '__all__'
    
    def get_duration_seconds(self, obj):
        if obj.completed_at and obj.started_at:
            return (obj.completed_at - obj.started_at).total_seconds()
        return None


# =============================================================================
# Portal API Serializers (for read-only recipient access)
# =============================================================================

class PortalAssetSerializer(serializers.Serializer):
    """
    Serializer for assets in the portal API.
    Minimal data for timeline views.
    """
    id = serializers.UUIDField()
    taken_at = serializers.DateTimeField()
    width = serializers.IntegerField()
    height = serializers.IntegerField()
    mime_type = serializers.CharField()
    caption = serializers.CharField()
    is_video = serializers.BooleanField()
    
    # Conditional fields based on flags
    download_url = serializers.SerializerMethodField()
    thumbnail_url = serializers.SerializerMethodField()
    
    def get_download_url(self, obj):
        flags = self.context.get('flags', {})
        if flags.get('allow_downloads', True):
            # In a real implementation, this would be a presigned S3 URL
            return f"/api/assets/{obj.id}/download"
        return None
    
    def get_thumbnail_url(self, obj):
        # Always show thumbnails regardless of download permissions
        return f"/api/assets/{obj.id}/thumbnail"


class PortalPersonSerializer(serializers.Serializer):
    """
    Serializer for people in the portal API.
    """
    id = serializers.UUIDField()
    display_name = serializers.SerializerMethodField()
    photo_count = serializers.IntegerField()
    headshot_url = serializers.SerializerMethodField()
    
    def get_display_name(self, obj):
        flags = self.context.get('flags', {})
        if flags.get('show_names', True):
            return obj.display_name
        return f"Person {str(obj.id)[:8]}"  # Anonymous identifier
    
    def get_headshot_url(self, obj):
        if obj.headshot_face_id:
            return f"/api/faces/{obj.headshot_face_id}/thumbnail"
        return None
