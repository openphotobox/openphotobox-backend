from django.urls import path

from . import views

app_name = "config"

urlpatterns = [
    path("features/", views.get_server_features, name="server-features"),
    path("config/", views.get_server_config, name="server-config"),
    path("media-types/", views.get_supported_media_types, name="server-media-types"),
    path("about/", views.get_server_about, name="server-about"),
]
