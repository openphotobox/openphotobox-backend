from rest_framework import serializers
from .models import Notification


class NotificationSerializer(serializers.ModelSerializer):
    """Serializer for notifications"""
    
    is_read = serializers.ReadOnlyField()
    
    class Meta:
        model = Notification
        fields = [
            'id',
            'title', 
            'message',
            'type',
            'read_at',
            'created_at',
            'updated_at',
            'is_read'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'is_read']
