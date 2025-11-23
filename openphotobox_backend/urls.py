"""
URL configuration for openphotobox_backend project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView

from assets.views import serve_face_thumbnail, serve_image, serve_thumbnail, stream_events

urlpatterns = [
    # API Documentation
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("api/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
    # Image serving (proxies from MinIO)
    path("images/<uuid:bucket_id>/<path:path>", serve_image, name="serve_image"),
    path("thumbnails/<uuid:bucket_id>/<path:path>", serve_thumbnail, name="serve_thumbnail"),
    path("face-thumbnails/<uuid:bucket_id>/<path:path>", serve_face_thumbnail, name="serve_face_thumbnail"),
    # API endpoints by app domain
    path("api/", include("users.urls")),  # Authentication and setup (at root level)
    path("api/assets/", include("assets.urls")),  # Asset management, albums, uploads
    path("api/people/", include("people.urls")),  # People, faces, recognition
    path("api/metadata/", include("metadata.urls")),  # EXIF, keywords, CLIP embeddings
    path("api/events/stream/", stream_events, name="sse-events"),
    # Authentication endpoints can be added here later
    # path('api/auth/', include('rest_framework.urls')),
]
