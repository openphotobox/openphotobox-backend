"""
URL patterns for the users app.
"""

from django.urls import path

from . import views

urlpatterns = [
    # Setup endpoints
    path("setup/check/", views.setup_check, name="setup-check"),
    path("setup/create-admin/", views.setup_create_admin, name="setup-create-admin"),
    # Authentication endpoints
    path("auth/login/", views.login_view, name="login"),
    path("auth/logout/", views.logout_view, name="logout"),
    # Profile endpoints
    path("auth/me/", views.profile_view, name="profile"),
    path("auth/me/update/", views.update_profile_view, name="update-profile"),
    path("auth/change-password/", views.change_password_view, name="change-password"),
    # Admin user management endpoints
    path("admin/users/", views.users_list_create_view, name="users-list-create"),
    path("admin/users/<int:user_id>/", views.update_user_view, name="update-user"),
    path("admin/users/<int:user_id>/delete/", views.delete_user_view, name="delete-user"),
]
