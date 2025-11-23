from django.urls import path

from . import views

app_name = "notifications"

urlpatterns = [
    path("", views.get_notifications, name="notifications-list"),
    path("<int:notification_id>/", views.update_notification, name="notification-update"),
    path("bulk/", views.update_notifications_bulk, name="notifications-bulk-update"),
]
