from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .models import ServerConfiguration
from .serializers import ServerConfigSerializer, ServerFeaturesSerializer, ServerMediaTypesSerializer


@api_view(["GET"])
@permission_classes([AllowAny])
def get_server_features(request):
    """
    Get server feature flags
    GET /api/server/features/
    """
    try:
        config = ServerConfiguration.get_or_create_default()
        serializer = ServerFeaturesSerializer(config)
        return Response(serializer.data, status=status.HTTP_200_OK)
    except Exception as e:
        return Response(
            {"error": f"Failed to get server features: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(["GET"])
@permission_classes([AllowAny])
def get_server_config(request):
    """
    Get server configuration
    GET /api/server/config/
    """
    try:
        config = ServerConfiguration.get_or_create_default()
        serializer = ServerConfigSerializer(config)
        return Response(serializer.data, status=status.HTTP_200_OK)
    except Exception as e:
        return Response(
            {"error": f"Failed to get server config: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(["GET"])
@permission_classes([AllowAny])
def get_supported_media_types(request):
    """
    Get supported media types
    GET /api/server/media-types/
    """
    try:
        config = ServerConfiguration.get_or_create_default()
        serializer = ServerMediaTypesSerializer(config)
        return Response(serializer.data, status=status.HTTP_200_OK)
    except Exception as e:
        return Response(
            {"error": f"Failed to get supported media types: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(["GET"])
@permission_classes([AllowAny])
def get_server_about(request):
    """
    Get server about information
    GET /api/server/about/
    """
    try:
        # Return basic server information
        about_info = {
            "build": "1.0.0",
            "buildImage": "openphotobox:latest",
            "buildImageUrl": "https://github.com/your-org/openphotobox",
            "buildUrl": "https://github.com/your-org/openphotobox",
            "exiftool": "12.0.0",
            "ffmpeg": "6.0.0",
            "imagemagick": "7.1.0",
            "libvips": "8.14.0",
            "licensed": False,
            "nodejs": "20.0.0",
            "repository": "openphotobox",
            "repositoryUrl": "https://github.com/your-org/openphotobox",
            "sourceCommit": "main",
            "sourceRef": "main",
            "sourceUrl": "https://github.com/your-org/openphotobox",
            "thirdPartyBugFeatureUrl": "https://github.com/your-org/openphotobox/issues",
            "thirdPartyDocumentationUrl": "https://docs.openphotobox.com",
            "thirdPartySourceUrl": "https://github.com/your-org/openphotobox",
            "thirdPartySupportUrl": "https://github.com/your-org/openphotobox/discussions",
            "version": "1.0.0",
            "versionUrl": "https://github.com/your-org/openphotobox/releases",
        }
        return Response(about_info, status=status.HTTP_200_OK)
    except Exception as e:
        return Response(
            {"error": f"Failed to get server about info: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
