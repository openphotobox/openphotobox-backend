"""
Views for user authentication and management.
"""

from django.contrib.auth import login, logout
from django.contrib.auth.models import User
from django.views.decorators.csrf import csrf_exempt
from rest_framework import permissions, status
from rest_framework.authtoken.models import Token
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.response import Response

from .serializers import ChangePasswordSerializer, LoginSerializer, RegisterSerializer, SetupSerializer, UserSerializer


@api_view(["GET"])
@permission_classes([permissions.AllowAny])
def setup_check(request):
    """Check if initial setup is needed"""
    user_count = User.objects.count()
    needs_setup = user_count == 0

    return Response(
        {
            "needs_setup": needs_setup,
            "user_count": user_count,
            "message": "Setup required" if needs_setup else "System is set up",
        }
    )


@api_view(["POST"])
@permission_classes([permissions.AllowAny])
def setup_create_admin(request):
    """Create the first admin user - only works when no users exist"""
    # Check if setup is still needed
    user_count = User.objects.count()
    if user_count > 0:
        return Response(
            {
                "error": "Setup has already been completed. Initial admin user already exists.",
                "message": "To create additional users, an admin must use the user management features.",
                "user_count": user_count,
            },
            status=status.HTTP_403_FORBIDDEN,
        )

    serializer = SetupSerializer(data=request.data)
    if serializer.is_valid():
        user = serializer.save()
        token, created = Token.objects.get_or_create(user=user)

        return Response(
            {
                "token": token.key,
                "user": UserSerializer(user).data,
                "message": "Initial admin user created successfully. Setup is now complete.",
            },
            status=status.HTTP_201_CREATED,
        )

    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@csrf_exempt
@api_view(["POST"])
@permission_classes([permissions.AllowAny])
@authentication_classes([])
def login_view(request):
    """User login"""
    serializer = LoginSerializer(data=request.data)
    if serializer.is_valid():
        user = serializer.validated_data["user"]
        token, created = Token.objects.get_or_create(user=user)

        # Update login session
        login(request, user)

        return Response({"token": token.key, "user": UserSerializer(user).data, "message": "Login successful"})

    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@csrf_exempt
@api_view(["POST"])
@permission_classes([permissions.IsAuthenticated])
@authentication_classes([])
def logout_view(request):
    """User logout"""
    try:
        # Delete the user's token
        request.user.auth_token.delete()
    except Token.DoesNotExist:
        pass

    logout(request)

    return Response({"message": "Logout successful"})


@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated])
def profile_view(request):
    """Get user profile"""
    return Response({"user": UserSerializer(request.user).data})


@api_view(["PUT", "PATCH"])
@permission_classes([permissions.IsAuthenticated])
def update_profile_view(request):
    """Update user profile"""
    serializer = UserSerializer(request.user, data=request.data, partial=request.method == "PATCH")

    if serializer.is_valid():
        serializer.save()
        return Response({"user": serializer.data, "message": "Profile updated successfully"})

    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(["POST"])
@permission_classes([permissions.IsAuthenticated])
def change_password_view(request):
    """Change user password"""
    serializer = ChangePasswordSerializer(data=request.data, context={"request": request})

    if serializer.is_valid():
        user = request.user
        user.set_password(serializer.validated_data["new_password"])
        user.save()

        # Invalidate current token and create new one
        try:
            user.auth_token.delete()
        except Token.DoesNotExist:
            pass

        token = Token.objects.create(user=user)

        return Response(
            {
                "message": "Password changed successfully",
                "token": token.key,  # Return new token
            }
        )

    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(["GET", "POST"])
@permission_classes([permissions.IsAdminUser])
def users_list_create_view(request):
    """List users (GET) or create new user (POST) - admin only"""
    if request.method == "GET":
        users = User.objects.all().order_by("username")
        return Response({"users": UserSerializer(users, many=True).data, "count": users.count()})

    elif request.method == "POST":
        serializer = RegisterSerializer(data=request.data)
        if serializer.is_valid():
            user = serializer.save()
            return Response(
                {
                    "user": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer"},
                            "username": {"type": "string"},
                            "email": {"type": "string"},
                            "is_admin": {"type": "boolean"},
                        },
                    }(user).data,
                    "message": f'User "{user.username}" created successfully',
                },
                status=status.HTTP_201_CREATED,
            )

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    return None


@api_view(["DELETE"])
@permission_classes([permissions.IsAdminUser])
def delete_user_view(request, user_id):
    """Delete user (admin only)"""
    try:
        user_to_delete = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return Response({"error": "User not found"}, status=status.HTTP_404_NOT_FOUND)

    # Prevent admin from deleting themselves
    if user_to_delete.id == request.user.id:
        return Response({"error": "You cannot delete your own account"}, status=status.HTTP_400_BAD_REQUEST)

    username = user_to_delete.username
    user_to_delete.delete()

    return Response({"message": f'User "{username}" has been deleted successfully'})


@api_view(["PUT", "PATCH"])
@permission_classes([permissions.IsAdminUser])
def update_user_view(request, user_id):
    """Update user (admin only)"""
    try:
        user_to_update = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return Response({"error": "User not found"}, status=status.HTTP_404_NOT_FOUND)

    serializer = UserSerializer(user_to_update, data=request.data, partial=request.method == "PATCH")

    if serializer.is_valid():
        serializer.save()
        return Response({"user": serializer.data, "message": f'User "{user_to_update.username}" updated successfully'})

    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
