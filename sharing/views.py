"""
Views for the sharing system.
- Admin API for managing recipients, grants, and links
- Portal API for read-only recipient access via tokens
"""
import logging
from django.http import Http404
from django.utils import timezone
from rest_framework import viewsets, status, permissions
from rest_framework.decorators import api_view, permission_classes, action
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from .models import Recipient, AccessGrant, RecipientLink, RecipientAssetRebuildLog
from .services import RecipientAssetBuilder, SharingQueryService
from .serializers import (
    RecipientSerializer, AccessGrantSerializer, RecipientLinkSerializer,
    RecipientAssetRebuildLogSerializer, PortalAssetSerializer, PortalPersonSerializer
)

logger = logging.getLogger(__name__)


# =============================================================================
# Admin API - For managing sharing (authenticated users only)
# =============================================================================

class RecipientViewSet(viewsets.ModelViewSet):
    """
    Admin API for managing recipients.
    """
    queryset = Recipient.objects.all()
    serializer_class = RecipientSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)
    
    @action(detail=True, methods=['post'])
    def rebuild_assets(self, request, pk=None):
        """Manually trigger a rebuild of this recipient's asset list."""
        recipient = self.get_object()
        log = RecipientAssetBuilder.rebuild_for_recipient(
            recipient, 
            trigger_type='manual',
            trigger_details={'triggered_by': request.user.username}
        )
        return Response({
            'message': f'Asset rebuild triggered for {recipient.display_name}',
            'log': RecipientAssetRebuildLogSerializer(log).data
        })


class AccessGrantViewSet(viewsets.ModelViewSet):
    """
    Admin API for managing access grants.
    """
    queryset = AccessGrant.objects.select_related('recipient', 'album', 'person').all()
    serializer_class = AccessGrantSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    def perform_create(self, serializer):
        grant = serializer.save(granted_by=self.request.user)
        # Trigger recipient asset rebuild
        RecipientAssetBuilder.handle_grant_created(grant)
    
    def perform_destroy(self, instance):
        recipient = instance.recipient
        grant_details = {
            'grant_id': str(instance.id),
            'grant_type': instance.grant_type,
            'target_id': str(instance.album_id or instance.person_id)
        }
        super().perform_destroy(instance)
        # Trigger recipient asset rebuild after deletion
        RecipientAssetBuilder.handle_grant_deleted(recipient, grant_details)


class RecipientLinkViewSet(viewsets.ModelViewSet):
    """
    Admin API for managing recipient links.
    """
    queryset = RecipientLink.objects.select_related('recipient').all()
    serializer_class = RecipientLinkSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    def create(self, request, *args, **kwargs):
        """Create a new recipient link and return the plaintext token once."""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        # Extract password from request data (not stored in serializer)
        password = request.data.get('password')
        
        # Create the link with secure token generation
        link, plaintext_token = RecipientLink.create_link(
            recipient=serializer.validated_data['recipient'],
            created_by=request.user,
            password=password,
            **{k: v for k, v in serializer.validated_data.items() if k != 'recipient'}
        )
        
        # Return the link data with the plaintext token (only shown once)
        response_data = RecipientLinkSerializer(link).data
        response_data['token'] = plaintext_token  # Only returned on creation
        
        return Response(response_data, status=status.HTTP_201_CREATED)


class RecipientAssetRebuildLogViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Admin API for viewing rebuild logs.
    """
    queryset = RecipientAssetRebuildLog.objects.select_related('recipient').all()
    serializer_class = RecipientAssetRebuildLogSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    @action(detail=False, methods=['post'])
    def rebuild_all(self, request):
        """Trigger a full rebuild for all recipients."""
        logs = RecipientAssetBuilder.rebuild_all(trigger_type='manual_full_rebuild')
        return Response({
            'message': f'Triggered rebuild for {len(logs)} recipients',
            'logs': RecipientAssetRebuildLogSerializer(logs, many=True).data
        })


# =============================================================================
# Portal API - Read-only access via tokens (no authentication required)
# =============================================================================

def get_recipient_from_token(token: str) -> tuple[RecipientLink, dict]:
    """
    Helper to get recipient and flags from token.
    Raises Http404 if token is invalid or expired.
    """
    link = RecipientLink.get_by_token(token)
    if not link:
        raise Http404("Invalid or expired token")
    
    # Record access
    link.record_access()
    
    # Get effective feature flags
    flags = link.get_effective_flags()
    
    return link, flags


@api_view(['GET'])
@permission_classes([AllowAny])
def portal_info(request, token):
    """
    GET /r/:token
    Returns basic info about the recipient and their portal.
    """
    link, flags = get_recipient_from_token(token)
    recipient = link.recipient
    
    # Count assets and people visible to this recipient
    from .models import RecipientAsset
    from people.models import Person
    
    asset_count = RecipientAsset.objects.filter(recipient=recipient).count()
    
    people_count = Person.objects.filter(
        faces__asset__recipient_assets__recipient=recipient
    ).distinct().count()
    
    return Response({
        'recipient': {
            'id': str(recipient.id),
            'name': recipient.display_name
        },
        'counts': {
            'assets': asset_count,
            'people': people_count
        },
        'flags': flags
    })


@api_view(['GET'])
@permission_classes([AllowAny])
def portal_assets(request, token):
    """
    GET /r/:token/assets?cursor=<taken_at>&limit=200
    Returns timeline of assets visible to this recipient.
    """
    link, flags = get_recipient_from_token(token)
    recipient = link.recipient
    
    # Parse query parameters
    cursor = request.GET.get('cursor')
    limit = min(int(request.GET.get('limit', 200)), 500)  # Cap at 500
    
    # Get assets
    assets = SharingQueryService.get_recipient_assets(
        recipient=recipient,
        cursor_taken_at=cursor,
        limit=limit
    )
    
    # Serialize with minimal data for timeline
    serializer = PortalAssetSerializer(assets, many=True, context={'flags': flags})
    
    return Response({
        'assets': serializer.data,
        'has_more': len(serializer.data) == limit
    })


@api_view(['GET'])
@permission_classes([AllowAny])
def portal_asset_detail(request, token, asset_id):
    """
    GET /r/:token/assets/:assetId
    Returns asset detail with faces (if recipient has access).
    """
    link, flags = get_recipient_from_token(token)
    recipient = link.recipient
    
    # Verify access to this specific asset
    if not SharingQueryService.verify_recipient_asset_access(recipient, asset_id):
        raise Http404("Asset not found or access denied")
    
    # Get asset
    from assets.models import Asset
    try:
        asset = Asset.objects.get(id=asset_id)
    except Asset.DoesNotExist:
        raise Http404("Asset not found")
    
    # Get faces if enabled
    faces_data = []
    if flags['show_faces']:
        faces = SharingQueryService.get_asset_faces_for_recipient(
            recipient=recipient,
            asset_id=asset_id,
            show_names=flags['show_names']
        )
        faces_data = [
            {
                'id': str(face.id),
                'x': face.x,
                'y': face.y,
                'w': face.w,
                'h': face.h,
                'person_name': getattr(face, 'person_name', None),
                'person_id': str(face.person_id) if face.person_id else None
            }
            for face in faces
        ]
    
    return Response({
        'asset': PortalAssetSerializer(asset, context={'flags': flags}).data,
        'faces': faces_data
    })


@api_view(['GET'])
@permission_classes([AllowAny])
def portal_people(request, token):
    """
    GET /r/:token/people
    Returns people visible to this recipient with photo counts.
    """
    link, flags = get_recipient_from_token(token)
    recipient = link.recipient
    
    # Parse query parameters
    limit = min(int(request.GET.get('limit', 100)), 500)
    offset = int(request.GET.get('offset', 0))
    
    # Get people
    people = SharingQueryService.get_recipient_people(
        recipient=recipient,
        limit=limit,
        offset=offset
    )
    
    # Serialize
    serializer = PortalPersonSerializer(
        people, 
        many=True, 
        context={'flags': flags, 'recipient': recipient}
    )
    
    return Response({
        'people': serializer.data,
        'has_more': len(serializer.data) == limit
    })


@api_view(['GET'])
@permission_classes([AllowAny])
def portal_person_assets(request, token, person_id):
    """
    GET /r/:token/people/:personId/assets?cursor=&limit=
    Returns assets where this person appears, scoped to recipient access.
    """
    link, flags = get_recipient_from_token(token)
    recipient = link.recipient
    
    # Parse query parameters
    cursor = request.GET.get('cursor')
    limit = min(int(request.GET.get('limit', 200)), 500)
    
    # Get assets for this person
    assets = SharingQueryService.get_recipient_person_assets(
        recipient=recipient,
        person_id=person_id,
        cursor_taken_at=cursor,
        limit=limit
    )
    
    # Serialize
    serializer = PortalAssetSerializer(assets, many=True, context={'flags': flags})
    
    return Response({
        'assets': serializer.data,
        'has_more': len(serializer.data) == limit,
        'person_id': person_id
    })


@api_view(['POST'])
@permission_classes([AllowAny])
def portal_authenticate(request, token):
    """
    POST /r/:token/auth
    Authenticate with password for password-protected links.
    """
    link, flags = get_recipient_from_token(token)
    
    password = request.data.get('password', '')
    
    if not link.verify_password(password):
        return Response(
            {'error': 'Invalid password'}, 
            status=status.HTTP_401_UNAUTHORIZED
        )
    
    # Password is correct - return success
    # In a full implementation, you might set a session cookie here
    return Response({
        'message': 'Authentication successful',
        'flags': flags
    })
