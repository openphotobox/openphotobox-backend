from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import Notification
from .serializers import NotificationSerializer


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_notifications(request):
    """
    Get notifications for the current user
    GET /api/notifications/?unread=true
    """
    try:
        user = request.user
        unread_only = request.GET.get("unread", "false").lower() == "true"

        queryset = Notification.objects.filter(user=user)

        if unread_only:
            queryset = queryset.filter(read_at__isnull=True)

        notifications = queryset.order_by("-created_at")
        serializer = NotificationSerializer(notifications, many=True)

        return Response(serializer.data, status=status.HTTP_200_OK)

    except Exception as e:
        return Response(
            {"error": f"Failed to get notifications: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(["PUT"])
@permission_classes([IsAuthenticated])
def update_notification(request, notification_id):
    """
    Update a notification (mark as read)
    PUT /api/notifications/{id}/
    """
    try:
        user = request.user

        try:
            notification = Notification.objects.get(id=notification_id, user=user)
        except Notification.DoesNotExist:
            return Response({"error": "Notification not found"}, status=status.HTTP_404_NOT_FOUND)

        # Mark as read if readAt is provided
        read_at = request.data.get("readAt")
        if read_at:
            notification.read_at = timezone.now()
            notification.save()

        serializer = NotificationSerializer(notification)
        return Response(serializer.data, status=status.HTTP_200_OK)

    except Exception as e:
        return Response(
            {"error": f"Failed to update notification: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(["PUT"])
@permission_classes([IsAuthenticated])
def update_notifications_bulk(request):
    """
    Update multiple notifications (mark all as read)
    PUT /api/notifications/
    """
    try:
        user = request.user
        notification_ids = request.data.get("ids", [])

        if not notification_ids:
            return Response({"error": "No notification IDs provided"}, status=status.HTTP_400_BAD_REQUEST)

        # Update notifications for the current user
        updated_count = Notification.objects.filter(id__in=notification_ids, user=user).update(read_at=timezone.now())

        return Response({"message": f"Updated {updated_count} notifications"}, status=status.HTTP_200_OK)

    except Exception as e:
        return Response(
            {"error": f"Failed to update notifications: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
