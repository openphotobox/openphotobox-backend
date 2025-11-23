from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.db.models import Q
from django.core.exceptions import ValidationError
from django.http import Http404
from botocore.exceptions import ClientError
from .models import Asset, Album, AlbumAsset, UploadBatch, StorageBackend, StorageBucket
from .serializers import (
    AssetSerializer, AlbumSerializer, UploadBatchSerializer,
    StorageBackendSerializer, StorageBucketSerializer,
    AssetGallerySerializer
)
from .services import UploadService, get_default_upload_bucket
from openphotobox_backend.pagination import AssetCursorPagination
from django.http import StreamingHttpResponse
import time
from datetime import datetime, timezone


class AssetViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing photo assets.
    Supports filtering by date, keywords, visibility, etc.
    """
    queryset = Asset.objects.all()
    serializer_class = AssetSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = AssetCursorPagination
    
    def get_queryset(self):
        queryset = Asset.objects.prefetch_related('thumbnails').select_related('storage_bucket', 'storage_bucket__backend').order_by('-taken_at', '-created_at')
        # Only show assets that have at least one ready thumbnail to avoid heavy original loads
        # This prevents freshly uploaded, unprocessed photos from appearing in albums/timelines until ready
        queryset = queryset.filter(thumbnails__is_ready=True).distinct()
        
        # Filter by visibility
        visibility = self.request.query_params.get('visibility')
        if visibility:
            queryset = queryset.filter(visibility=visibility)
        
        # Filter by person or people combinations (via faces relationship in people app)
        person_id = self.request.query_params.get('person') or self.request.query_params.get('person_id')
        people_param = self.request.query_params.get('people') or self.request.query_params.get('person_ids')
        people_mode = (self.request.query_params.get('people_mode') or 'all').lower()
        try:
            from people.models import Face
            if people_param:
                people_ids = [p.strip() for p in people_param.split(',') if p and p.strip()]
                if people_ids:
                    if people_mode not in ('all', 'any'):
                        people_mode = 'all'
                    if people_mode == 'all':
                        # Intersect assets that contain each specified person
                        intersect_ids = None
                        for pid in people_ids:
                            ids_for_pid = set(
                                Face.objects.filter(person_id=pid).values_list('asset_id', flat=True)
                            )
                            intersect_ids = ids_for_pid if intersect_ids is None else (intersect_ids & ids_for_pid)
                            if not intersect_ids:
                                break
                        queryset = queryset.filter(id__in=list(intersect_ids or []))
                    else:
                        queryset = queryset.filter(
                            id__in=Face.objects.filter(person_id__in=people_ids)
                            .values_list('asset_id', flat=True)
                            .distinct()
                        )
            elif person_id:
                queryset = queryset.filter(
                    id__in=Face.objects.filter(person_id=person_id).values_list('asset_id', flat=True)
                )
        except Exception:
            # If people app is unavailable for any reason, return no results for safety
            queryset = queryset.none()
        
        # Filter by album
        album_id = self.request.query_params.get('album') or self.request.query_params.get('album_id')
        albums_param = self.request.query_params.get('albums') or self.request.query_params.get('album_ids')
        if albums_param or album_id:
            try:
                from .models import AlbumAsset
                album_ids = []
                if albums_param:
                    album_ids = [a.strip() for a in albums_param.split(',') if a and a.strip()]
                if album_id:
                    album_ids.append(album_id)
                if album_ids:
                    queryset = queryset.filter(
                        id__in=AlbumAsset.objects.filter(album_id__in=album_ids)
                        .values_list('asset_id', flat=True)
                        .distinct()
                    )
            except Exception:
                queryset = queryset.none()

        # Filter by date range
        start_date = self.request.query_params.get('start_date')
        end_date = self.request.query_params.get('end_date')
        if start_date:
            queryset = queryset.filter(taken_at__gte=start_date)
        if end_date:
            queryset = queryset.filter(taken_at__lte=end_date)
        
        # Filter by keywords (via metadata app relationship)
        keywords = self.request.query_params.get('keywords')
        if keywords:
            keyword_list = [k.strip() for k in keywords.split(',')]
            # Import here to avoid circular imports
            try:
                from metadata.models import AssetKeyword
                # Filter assets that have any of the specified keywords
                queryset = queryset.filter(
                    id__in=AssetKeyword.objects.filter(
                        keyword__name__in=keyword_list
                    ).values_list('asset_id', flat=True)
                )
            except ImportError:
                pass  # Metadata app not available
        
        # Search in descriptions
        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(description__icontains=search)
        
        return queryset.order_by('-taken_at', '-created_at')

    @action(detail=False, methods=['get'])
    def gallery(self, request):
        """Lightweight gallery listing for the main grid.
        Returns minimal fields with cursor pagination.
        Accepts same filters as list().
        """
        queryset = self.get_queryset()
        page = self.paginate_queryset(queryset)
        serializer = AssetGallerySerializer(page, many=True)
        return self.get_paginated_response(serializer.data)

    @action(detail=False, methods=['get'])
    def timeline(self, request):
        """Return available photo dates and counts for the timeline sidebar.
        Groups by capture day (YYYY-MM-DD) using taken_at if present, else created_at.
        """
        from django.db.models import Count
        from django.db.models.functions import TruncDate, Coalesce

        qs = (
            self.get_queryset()
            .annotate(capture_date=Coalesce(TruncDate('taken_at'), TruncDate('created_at')))
            .values('capture_date')
            .annotate(count=Count('id'))
            .order_by('-capture_date')
        )

        items = [
            { 'date': row['capture_date'].isoformat(), 'count': row['count'] }
            for row in qs if row['capture_date'] is not None
        ]
        return Response({ 'results': items })

    @action(detail=False, methods=['get'], url_path='ready-since')
    def ready_since(self, request):
        """Return assets that became ready (have a ready thumbnail) since a given ISO timestamp.
        Query params: since=ISO8601, limit (default 100)
        """
        since = request.query_params.get('since')
        try:
            limit = int(request.query_params.get('limit', 100))
        except Exception:
            limit = 100
        qs = self.get_queryset()
        if since:
            try:
                from datetime import datetime
                # Support naive or timezone-aware strings
                dt = datetime.fromisoformat(since.replace('Z', '+00:00'))
                qs = qs.filter(updated_at__gte=dt)
            except Exception:
                pass
        qs = qs.order_by('-updated_at')[:max(1, min(limit, 500))]
        serializer = AssetGallerySerializer(qs, many=True)
        return Response({'results': serializer.data})

    @action(detail=False, methods=['get'])
    def by_date(self, request):
        """Return all assets for a specific date (YYYY-MM-DD) for a section.
        Minimal fields for the gallery.
        Query param: date=YYYY-MM-DD
        """
        date_str = request.query_params.get('date')
        if not date_str:
            return Response({'error': 'date is required (YYYY-MM-DD)'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            from datetime import datetime
            day = datetime.fromisoformat(date_str).date()
        except Exception:
            return Response({'error': 'Invalid date format, expected YYYY-MM-DD'}, status=status.HTTP_400_BAD_REQUEST)

        qs = self.get_queryset().filter(
            Q(taken_at__date=day) | (Q(taken_at__isnull=True) & Q(created_at__date=day))
        )
        serializer = AssetGallerySerializer(qs, many=True)
        return Response({'results': serializer.data})

    @action(detail=False, methods=['post'])
    def upload_file(self, request):
        """
        Direct file upload for LocalFS storage backend.
        Requires X-File-Key, X-Bucket-ID headers from get_upload_config.
        
        POST /api/assets/upload_file/
        Content-Type: multipart/form-data
        X-File-Key: 2024/01/15/uuid.jpg
        X-Bucket-ID: bucket-uuid
        X-Upload-Batch-ID: batch-uuid-optional
        
        Body: file=<binary data>
        """
        try:
            # Get required headers
            file_key = request.headers.get('X-File-Key')
            bucket_id = request.headers.get('X-Bucket-ID')
            upload_batch_id = request.headers.get('X-Upload-Batch-ID') or None
            
            if not file_key or not bucket_id:
                return Response(
                    {'error': 'X-File-Key and X-Bucket-ID headers are required'}, 
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Get uploaded file
            if 'file' not in request.FILES:
                return Response(
                    {'error': 'No file provided'}, 
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            uploaded_file = request.FILES['file']
            
            # Get storage bucket
            try:
                bucket = StorageBucket.objects.get(id=bucket_id)
            except StorageBucket.DoesNotExist:
                return Response(
                    {'error': 'Invalid bucket_id'}, 
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Verify this is a LocalFS backend
            if bucket.backend.backend_type != 'local':
                return Response(
                    {'error': 'Direct upload only supported for LocalFS backends'}, 
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Initialize upload service for LocalFS
            upload_service = UploadService(bucket.backend)
            
            # Save file to local storage and create asset
            asset = upload_service.handle_direct_upload(
                file=uploaded_file,
                file_key=file_key,
                bucket_id=bucket_id,
                upload_batch_id=upload_batch_id
            )
            
            return Response({
                'file_key': file_key,
                'bucket_id': bucket_id,
                'asset_id': str(asset.id),
                'message': 'File uploaded successfully'
            }, status=status.HTTP_201_CREATED)
                
        except ValidationError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response(
                {'error': f'Failed to upload file: {str(e)}'}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=False, methods=['post'])
    def request_upload_url(self, request):
        """
        Generate a presigned URL for uploading a file directly to S3/MinIO.
        
        POST /api/assets/request_upload_url/
        {
            "filename": "photo.jpg",
            "content_type": "image/jpeg",
            "upload_batch_id": "uuid-optional"
        }
        """
        try:
            filename = request.data.get('filename')
            content_type = request.data.get('content_type')
            upload_batch_id = request.data.get('upload_batch_id')
            
            if not filename or not content_type:
                return Response(
                    {'error': 'filename and content_type are required'}, 
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Get default upload bucket
            bucket = get_default_upload_bucket('originals')
            
            # Initialize upload service
            upload_service = UploadService(bucket.backend)
            
            # Generate unique file key
            file_key = upload_service.generate_upload_key(filename, upload_batch_id)
            
            # Generate presigned URL
            upload_data = upload_service.generate_presigned_upload_url(
                bucket=bucket,
                file_key=file_key,
                content_type=content_type,
                expires_in=3600  # 1 hour
            )
            
            return Response(upload_data, status=status.HTTP_200_OK)
            
        except ValidationError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response(
                {'error': f'Failed to generate upload URL: {str(e)}'}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=['post'])
    def complete_upload(self, request):
        """
        Complete the upload process by creating an Asset record.
        Call this after successfully uploading the file to S3/MinIO.
        
        POST /api/assets/complete_upload/
        {
            "file_key": "2024/01/15/uuid.jpg",
            "bucket_id": "bucket-uuid",
            "upload_batch_id": "batch-uuid-optional",
            "metadata": {
                "width": 1920,
                "height": 1080,
                "taken_at": "2024-01-15T10:30:00Z",
                "description": "Family photo"
            }
        }
        """
        try:
            file_key = request.data.get('file_key')
            bucket_id = request.data.get('bucket_id')
            upload_batch_id = request.data.get('upload_batch_id')
            metadata = request.data.get('metadata', {})
            
            if not file_key or not bucket_id:
                return Response(
                    {'error': 'file_key and bucket_id are required'}, 
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Get storage bucket
            try:
                bucket = StorageBucket.objects.get(id=bucket_id)
            except StorageBucket.DoesNotExist:
                return Response(
                    {'error': 'Invalid bucket_id'}, 
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Initialize upload service
            upload_service = UploadService(bucket.backend)
            
            # Complete upload and create asset
            asset = upload_service.complete_upload(
                file_key=file_key,
                bucket_id=bucket_id,
                upload_batch_id=upload_batch_id,
                metadata=metadata
            )
            
            # Return created asset
            serializer = AssetSerializer(asset)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
            
        except ValidationError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response(
                {'error': f'Failed to complete upload: {str(e)}'}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    # Alias path that cannot collide with detail routes
    @action(detail=False, methods=['post'], url_path='upload/complete')
    def complete_upload_alias(self, request):
        return self.complete_upload(request)

    @action(detail=False, methods=['post'])
    def bulk_update(self, request):
        """Bulk update assets with new metadata"""
        asset_ids = request.data.get('asset_ids', [])
        update_data = request.data.get('update_data', {})
        
        if not asset_ids:
            return Response(
                {'error': 'asset_ids is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        assets = Asset.objects.filter(id__in=asset_ids)
        updated_count = assets.update(**update_data)
        
        return Response({
            'message': f'Updated {updated_count} assets',
            'updated_count': updated_count
        })

    @action(detail=False, methods=['get'])
    def by_date(self, request):
        """Return all assets for a specific date (YYYY-MM-DD) for a section.
        Minimal fields for the gallery.
        Query param: date=YYYY-MM-DD
        """
        date_str = request.query_params.get('date')
        if not date_str:
            return Response({'error': 'date is required (YYYY-MM-DD)'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            from datetime import datetime
            day = datetime.fromisoformat(date_str).date()
        except Exception:
            return Response({'error': 'Invalid date format, expected YYYY-MM-DD'}, status=status.HTTP_400_BAD_REQUEST)

        qs = self.get_queryset().filter(
            Q(taken_at__date=day) | (Q(taken_at__isnull=True) & Q(created_at__date=day))
        )
        serializer = AssetGallerySerializer(qs, many=True)
        return Response({'results': serializer.data})

    @action(detail=False, methods=['post'], url_path='upload/config')
    def get_upload_config(self, request):
        """
        Get upload configuration based on storage backend type.
        Returns presigned URL for S3/MinIO or direct upload endpoint for LocalFS.
        
        POST /api/assets/get_upload_config/
        {
            "filename": "photo.jpg",
            "content_type": "image/jpeg", 
            "file_size": 2048576,
            "upload_batch_id": "uuid-optional",
            "sha256": "optional sha"
        }
        """
        try:
            filename = request.data.get('filename')
            content_type = request.data.get('content_type')
            file_size = request.data.get('file_size')
            upload_batch_id = request.data.get('upload_batch_id')
            provided_sha256 = request.data.get('sha256')
            
            # Validate required fields
            missing_fields = []
            if not filename:
                missing_fields.append('filename')
            if not content_type:
                missing_fields.append('content_type')
            if not file_size:
                missing_fields.append('file_size')
                
            if missing_fields:
                return Response({
                    'error': 'Missing required fields',
                    'details': f"The following fields are required: {', '.join(missing_fields)}",
                    'required_fields': ['filename', 'content_type', 'file_size'],
                    'optional_fields': ['upload_batch_id']
                }, status=status.HTTP_400_BAD_REQUEST)
            
            # Validate file size
            if not isinstance(file_size, (int, float)) or file_size <= 0:
                return Response({
                    'error': 'Invalid file size',
                    'details': 'file_size must be a positive number representing bytes'
                }, status=status.HTTP_400_BAD_REQUEST)

            # Early duplicate check using provided sha256
            if provided_sha256:
                from .models import Asset
                existing = Asset.objects.filter(sha256=provided_sha256).first()
                if existing:
                    return Response({
                        'upload_method': 'duplicate',
                        'asset_id': str(existing.id)
                    })
            
            # Check if storage backend is configured
            try:
                bucket = get_default_upload_bucket('originals')
                backend = bucket.backend
            except ValidationError as e:
                return Response({
                    'error': 'Storage not configured',
                    'details': str(e),
                    'action_required': 'Please configure a storage backend before uploading files',
                    'admin_url': '/admin/settings'
                }, status=status.HTTP_503_SERVICE_UNAVAILABLE)
            
            # Generate unique file key
            upload_service = UploadService(backend)
            file_key = upload_service.generate_upload_key(filename, upload_batch_id)
            
            # Determine upload method based on backend type
            if backend.backend_type in ['s3', 'minio', 'gcs', 'azure']:
                upload_data = upload_service.generate_presigned_upload_url(
                    bucket=bucket,
                    file_key=file_key,
                    content_type=content_type,
                    expires_in=3600,
                    sha256=provided_sha256
                )
                return Response({
                    'upload_method': 'presigned_url',
                    'upload_url': upload_data['upload_url'],
                    'fields': upload_data.get('fields', {}),
                    'file_key': file_key,
                    'bucket_id': str(bucket.id),
                    'expires_at': upload_data.get('expires_at')
                })
            elif backend.backend_type == 'local':
                return Response({
                    'upload_method': 'direct_upload',
                    'upload_endpoint': f'/api/assets/upload_file/',
                    'file_key': file_key,
                    'bucket_id': str(bucket.id),
                    'upload_headers': {
                        'X-File-Key': file_key,
                        'X-Bucket-ID': str(bucket.id),
                        'X-Upload-Batch-ID': upload_batch_id or ''
                    }
                })
            else:
                return Response({'error': f'Unsupported backend type: {backend.backend_type}'}, status=status.HTTP_400_BAD_REQUEST)
        except ValidationError as e:
            return Response({'error': 'Configuration error', 'details': str(e), 'type': 'validation_error'}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f'Upload config failed: {str(e)}', exc_info=True)
            return Response({'error': 'Upload configuration failed', 'details': 'An unexpected error occurred while setting up upload configuration', 'type': 'internal_error'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


    # Back-compat: legacy path for old clients
    @action(detail=False, methods=['post'], url_path='get_upload_config')
    def get_upload_config_legacy(self, request):
        return self.get_upload_config(request)


def stream_events(request):
    """Server-Sent Events stream for lightweight push updates.
    Auth: either session auth (request.user.is_authenticated) or token via ?token=...
    Emits asset_ready events when new assets become ready (thumbnails present) since connect time.
    """
    # Authenticate via token param if provided
    user = getattr(request, 'user', None)
    token = request.GET.get('token')
    if (not getattr(user, 'is_authenticated', False)) and token:
        try:
            from django.contrib.auth import get_user_model
            # Try DRF TokenAuth if installed
            try:
                from rest_framework.authtoken.models import Token as DRFToken  # type: ignore
                t = DRFToken.objects.select_related('user').get(key=token)
                user = t.user
            except Exception:
                user = None
        except Exception:
            user = None
    if not user or not getattr(user, 'is_authenticated', False):
        return Response({'detail': 'Authentication required'}, status=status.HTTP_401_UNAUTHORIZED)

    def event_stream():
        # Start baseline timestamp
        last = datetime.now(timezone.utc)
        # Initial comment to open the stream
        yield ": connected\n\n"
        while True:
            try:
                # Find assets updated since last that have ready thumbnails
                qs = (
                    Asset.objects
                    .filter(updated_at__gte=last)
                    .filter(thumbnails__is_ready=True)
                    .order_by('updated_at')
                    .distinct()[:200]
                )
                items = list(qs.values('id', 'updated_at'))
                if items:
                    # Advance last to the newest update time
                    newest = max(i['updated_at'] for i in items if i.get('updated_at'))
                    if newest:
                        last = newest
                    data = {'type': 'asset_ready', 'ids': [str(i['id']) for i in items]}
                    yield f"event: asset_ready\n" + f"data: {data}\n\n"
                else:
                    # Heartbeat to keep connection alive
                    yield ": keep-alive\n\n"
            except Exception:
                # On error, emit heartbeat
                yield ": error\n\n"
            time.sleep(2)

    resp = StreamingHttpResponse(event_stream(), content_type='text/event-stream')
    resp['Cache-Control'] = 'no-cache'
    resp['X-Accel-Buffering'] = 'no'  # for nginx
    return resp

    @action(detail=False, methods=['get'])
    def by_date(self, request):
        """Return all assets for a specific date (YYYY-MM-DD) for a section.
        Minimal fields for the gallery.
        Query param: date=YYYY-MM-DD
        """
        date_str = request.query_params.get('date')
        if not date_str:
            return Response({'error': 'date is required (YYYY-MM-DD)'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            from datetime import datetime
            day = datetime.fromisoformat(date_str).date()
        except Exception:
            return Response({'error': 'Invalid date format, expected YYYY-MM-DD'}, status=status.HTTP_400_BAD_REQUEST)

        qs = self.get_queryset().filter(
            Q(taken_at__date=day) | (Q(taken_at__isnull=True) & Q(created_at__date=day))
        )
        serializer = AssetGallerySerializer(qs, many=True)
        return Response({'results': serializer.data})


    @action(detail=False, methods=['post'])
    def upload_file(self, request):
        """
        Direct file upload for LocalFS storage backend.
        Requires X-File-Key, X-Bucket-ID headers from get_upload_config.
        
        POST /api/assets/upload_file/
        Content-Type: multipart/form-data
        X-File-Key: 2024/01/15/uuid.jpg
        X-Bucket-ID: bucket-uuid
        X-Upload-Batch-ID: batch-uuid-optional
        
        Body: file=<binary data>
        """
        try:
            # Get required headers
            file_key = request.headers.get('X-File-Key')
            bucket_id = request.headers.get('X-Bucket-ID')
            upload_batch_id = request.headers.get('X-Upload-Batch-ID') or None
            
            if not file_key or not bucket_id:
                return Response(
                    {'error': 'X-File-Key and X-Bucket-ID headers are required'}, 
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Get uploaded file
            if 'file' not in request.FILES:
                return Response(
                    {'error': 'No file provided'}, 
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            uploaded_file = request.FILES['file']
            
            # Get storage bucket
            try:
                bucket = StorageBucket.objects.get(id=bucket_id)
            except StorageBucket.DoesNotExist:
                return Response(
                    {'error': 'Invalid bucket_id'}, 
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Verify this is a LocalFS backend
            if bucket.backend.backend_type != 'local':
                return Response(
                    {'error': 'Direct upload only supported for LocalFS backends'}, 
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Initialize upload service for LocalFS
            upload_service = UploadService(bucket.backend)
            
            # Save file to local storage and create asset
            asset = upload_service.handle_direct_upload(
                file=uploaded_file,
                file_key=file_key,
                bucket_id=bucket_id,
                upload_batch_id=upload_batch_id
            )
            
            return Response({
                'file_key': file_key,
                'bucket_id': bucket_id,
                'asset_id': str(asset.id),
                'message': 'File uploaded successfully'
            }, status=status.HTTP_201_CREATED)
                
        except ValidationError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response(
                {'error': f'Failed to upload file: {str(e)}'}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


    @action(detail=False, methods=['post'])
    def request_upload_url(self, request):
        """
        Generate a presigned URL for uploading a file directly to S3/MinIO.
        
        POST /api/assets/request_upload_url/
        {
            "filename": "photo.jpg",
            "content_type": "image/jpeg",
            "upload_batch_id": "uuid-optional"
        }
        """
        try:
            filename = request.data.get('filename')
            content_type = request.data.get('content_type')
            upload_batch_id = request.data.get('upload_batch_id')
            
            if not filename or not content_type:
                return Response(
                    {'error': 'filename and content_type are required'}, 
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Get default upload bucket
            bucket = get_default_upload_bucket('originals')
            
            # Initialize upload service
            upload_service = UploadService(bucket.backend)
            
            # Generate unique file key
            file_key = upload_service.generate_upload_key(filename, upload_batch_id)
            
            # Generate presigned URL
            upload_data = upload_service.generate_presigned_upload_url(
                bucket=bucket,
                file_key=file_key,
                content_type=content_type,
                expires_in=3600  # 1 hour
            )
            
            return Response(upload_data, status=status.HTTP_200_OK)
            
        except ValidationError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response(
                {'error': f'Failed to generate upload URL: {str(e)}'}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    

    @action(detail=False, methods=['post'])
    def complete_upload(self, request):
        """
        Complete the upload process by creating an Asset record.
        Call this after successfully uploading the file to S3/MinIO.
        
        POST /api/assets/complete_upload/
        {
            "file_key": "2024/01/15/uuid.jpg",
            "bucket_id": "bucket-uuid",
            "upload_batch_id": "batch-uuid-optional",
            "metadata": {
                "width": 1920,
                "height": 1080,
                "taken_at": "2024-01-15T10:30:00Z",
                "description": "Family photo"
            }
        }
        """
        try:
            file_key = request.data.get('file_key')
            bucket_id = request.data.get('bucket_id')
            upload_batch_id = request.data.get('upload_batch_id')
            metadata = request.data.get('metadata', {})
            
            if not file_key or not bucket_id:
                return Response(
                    {'error': 'file_key and bucket_id are required'}, 
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Get storage bucket
            try:
                bucket = StorageBucket.objects.get(id=bucket_id)
            except StorageBucket.DoesNotExist:
                return Response(
                    {'error': 'Invalid bucket_id'}, 
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Initialize upload service
            upload_service = UploadService(bucket.backend)
            
            # Complete upload and create asset
            asset = upload_service.complete_upload(
                file_key=file_key,
                bucket_id=bucket_id,
                upload_batch_id=upload_batch_id,
                metadata=metadata
            )
            
            # Return created asset
            serializer = AssetSerializer(asset)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
            
        except ValidationError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response(
                {'error': f'Failed to complete upload: {str(e)}'}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    # Alias path that cannot collide with detail routes
    @action(detail=False, methods=['post'], url_path='upload/complete')
    def complete_upload_alias(self, request):
        return self.complete_upload(request)

    @action(detail=False, methods=['post'])
    def bulk_update(self, request):
        """Bulk update assets with new metadata"""
        asset_ids = request.data.get('asset_ids', [])
        update_data = request.data.get('update_data', {})
        
        if not asset_ids:
            return Response(
                {'error': 'asset_ids is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        assets = Asset.objects.filter(id__in=asset_ids)
        updated_count = assets.update(**update_data)
        
        return Response({
            'message': f'Updated {updated_count} assets',
            'updated_count': updated_count
        })


class StorageBackendViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing storage backends.
    """
    queryset = StorageBackend.objects.all()
    serializer_class = StorageBackendSerializer
    permission_classes = [permissions.IsAdminUser]  # Admin only for storage config
    
    def get_queryset(self):
        queryset = StorageBackend.objects.all()
        
        # Filter by backend type
        backend_type = self.request.query_params.get('backend_type')
        if backend_type:
            queryset = queryset.filter(backend_type=backend_type)
        
        # Filter by active status
        is_active = self.request.query_params.get('is_active')
        if is_active is not None:
            queryset = queryset.filter(is_active=is_active.lower() == 'true')
        
        return queryset.order_by('-is_default', '-is_active', 'name')

    @action(detail=False, methods=['get'])
    def status(self, request):
        """Check storage configuration status"""
        from assets.services import get_default_upload_bucket
        
        status_info = {
            'configured': False,
            'has_backends': False,
            'has_default_backend': False,
            'has_originals_bucket': False,
            'default_backend': None,
            'recommendations': []
        }
        
        # Check if any backends exist
        backends_count = StorageBackend.objects.count()
        status_info['has_backends'] = backends_count > 0
        
        if not status_info['has_backends']:
            status_info['recommendations'].append('Create a storage backend (S3 or MinIO recommended)')
            return Response(status_info)
        
        # Check for default backend
        default_backend = StorageBackend.objects.filter(is_default=True, is_active=True).first()
        status_info['has_default_backend'] = default_backend is not None
        
        if default_backend:
            status_info['default_backend'] = {
                'id': str(default_backend.id),
                'name': default_backend.name,
                'backend_type': default_backend.backend_type,
                'is_active': default_backend.is_active
            }
        else:
            status_info['recommendations'].append('Set a default storage backend')
        
        # Check for originals bucket
        try:
            bucket = get_default_upload_bucket('originals')
            status_info['has_originals_bucket'] = True
        except ValidationError:
            status_info['has_originals_bucket'] = False
            status_info['recommendations'].append('Create an "originals" bucket for photo uploads')
        
        # Overall status
        status_info['configured'] = (
            status_info['has_backends'] and 
            status_info['has_default_backend'] and 
            status_info['has_originals_bucket']
        )
        
        if status_info['configured']:
            status_info['recommendations'].append('✅ Storage is properly configured!')
        
        return Response(status_info)
    

    @action(detail=False, methods=['post'])
    def setup_minio(self, request):
        """Quick setup for MinIO development environment"""
        endpoint_url = request.data.get('endpoint_url', 'http://localhost:9000')
        access_key = request.data.get('access_key', 'minio')
        secret_key = request.data.get('secret_key', 'minio123')
        
        # Create MinIO backend
        backend, backend_created = StorageBackend.objects.get_or_create(
            name='default-minio',
            defaults={
                'backend_type': 'minio',
                'endpoint_url': endpoint_url,
                'region': 'us-east-1',
                'is_default': True,
                'is_active': True,
                'config': {
                    'aws_access_key_id': access_key,
                    'aws_secret_access_key': secret_key,
                    'signature_version': 's3v4'
                }
            }
        )
        
        if backend_created:
            # Set as default and deactivate other defaults
            StorageBackend.objects.filter(is_default=True).exclude(id=backend.id).update(is_default=False)
        
        # Create originals bucket
        from assets.models import StorageBucket
        bucket, bucket_created = StorageBucket.objects.get_or_create(
            backend=backend,
            name='openphotobox-originals',
            defaults={
                'purpose': 'originals',
                'is_active': True
            }
        )
        
        return Response({
            'backend_created': backend_created,
            'bucket_created': bucket_created,
            'backend': {
                'id': str(backend.id),
                'name': backend.name,
                'backend_type': backend.backend_type,
                'endpoint_url': backend.endpoint_url
            },
            'bucket': {
                'id': str(bucket.id),
                'name': bucket.name,
                'purpose': bucket.purpose
            }
        }, status=status.HTTP_201_CREATED)


class StorageBucketViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing storage buckets.
    """
    queryset = StorageBucket.objects.select_related('backend').all()
    serializer_class = StorageBucketSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    def get_queryset(self):
        queryset = StorageBucket.objects.select_related('backend').all()
        
        # Filter by backend
        backend_id = self.request.query_params.get('backend')
        if backend_id:
            queryset = queryset.filter(backend_id=backend_id)
        
        # Filter by purpose
        purpose = self.request.query_params.get('purpose')
        if purpose:
            queryset = queryset.filter(purpose=purpose)
        
        # Filter by active status
        is_active = self.request.query_params.get('is_active')
        if is_active is not None:
            queryset = queryset.filter(is_active=is_active.lower() == 'true')
        
        return queryset.order_by('backend__name', 'purpose', 'display_name')


# Person and Face ViewSets moved to people app


class AlbumViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing photo albums.
    
    Albums are collections of photos that can be organized and shared.
    """
    queryset = Album.objects.all()
    serializer_class = AlbumSerializer
    permission_classes = [permissions.IsAuthenticated]

    @action(detail=True, methods=['post'])
    def add_photos(self, request, pk=None):
        """Add photos to this album"""
        album = self.get_object()
        asset_ids = request.data.get('asset_ids', [])
        
        if not asset_ids:
            return Response(
                {'error': 'asset_ids is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Create album-photo relationships
        album_assets = []
        for i, asset_id in enumerate(asset_ids):
            album_assets.append(
                AlbumAsset(
                    album=album,
                    asset_id=asset_id,
                    order=i
                )
            )
        
        AlbumAsset.objects.bulk_create(
            album_assets,
            ignore_conflicts=True
        )
        
        return Response({
            'message': f'Added {len(asset_ids)} photos to album',
            'album': AlbumSerializer(album).data
        })


# ShareLink ViewSet moved to sharing app


class UploadBatchViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing upload batches.
    
    Upload batches help organize and track multiple related photo uploads.
    """
    queryset = UploadBatch.objects.all()
    serializer_class = UploadBatchSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)
    
    def get_queryset(self):
        return UploadBatch.objects.filter(
            created_by=self.request.user
        ).order_by('-created_at')
    

    @action(detail=False, methods=['post'])
    def create_batch(self, request):
        """
        Create a new upload batch for organizing uploads.
        
        POST /api/upload-batches/create_batch/
        {
            "name": "Family Vacation 2024",
            "description": "Photos from our summer vacation"
        }
        """
        try:
            name = request.data.get('name')
            description = request.data.get('description', '')
            
            # Create upload service and batch
            upload_service = UploadService()
            batch = upload_service.create_upload_batch(
                user=request.user,
                name=name,
                description=description
            )
            
            serializer = UploadBatchSerializer(batch)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
            
        except ValidationError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response(
                {'error': f'Failed to create upload batch: {str(e)}'}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


# Image serving view
def serve_image(request, bucket_id, path):
    """
    Serve images from MinIO storage through Django for access control.
    
    GET /images/<bucket_id>/<path>
    """
    try:
        # Get the storage bucket
        bucket = StorageBucket.objects.get(id=bucket_id)
        backend = bucket.backend
        
        # Only serve from S3/MinIO backends
        if backend.backend_type not in ['s3', 'minio']:
            raise Http404("Image not found")
        
        # Create S3/MinIO client
        from .services import UploadService
        upload_service = UploadService(backend)
        client = upload_service.client
        
        # Get the object from MinIO
        try:
            response = client.get_object(Bucket=bucket.name, Key=path)
            
            # Stream the response
            def stream_content():
                try:
                    for chunk in response['Body'].iter_chunks(chunk_size=8192):
                        yield chunk
                except AttributeError:
                    # Fallback for different boto3 versions
                    while True:
                        chunk = response['Body'].read(8192)
                        if not chunk:
                            break
                        yield chunk
            
            # Create streaming response
            streaming_response = StreamingHttpResponse(
                stream_content(),
                content_type=response.get('ContentType', 'application/octet-stream')
            )
            
            # Add cache headers
            streaming_response['Cache-Control'] = 'public, max-age=3600'
            streaming_response['Content-Length'] = str(response.get('ContentLength', 0))
            
            return streaming_response
            
        except ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchKey':
                raise Http404("Image not found")
            else:
                raise Http404("Error accessing image")
                
    except StorageBucket.DoesNotExist:
        raise Http404("Bucket not found")
    except Exception as e:
        raise Http404("Error serving image")


# Thumbnail serving view
def serve_thumbnail(request, bucket_id, path):
    """
    Serve thumbnails from MinIO storage through Django for access control.
    
    GET /thumbnails/<bucket_id>/<path>
    """
    try:
        # Get the storage bucket
        bucket = StorageBucket.objects.get(id=bucket_id)
        backend = bucket.backend
        
        # Only serve from S3/MinIO backends
        if backend.backend_type not in ['s3', 'minio']:
            raise Http404("Thumbnail not found")
        
        # Create S3/MinIO client
        from .services import UploadService
        upload_service = UploadService(backend)
        client = upload_service.client
        
        # Get the object from MinIO
        try:
            # Handle path prefix for thumbnails
            full_key = path
            if bucket.path_prefix:
                full_key = f"{bucket.path_prefix}/{path}"
            elif bucket.purpose == 'originals':
                # If we're using the originals bucket for thumbnails, add thumbnails/ prefix
                full_key = f"thumbnails/{path}"
            
            response = client.get_object(Bucket=bucket.name, Key=full_key)
            
            # Stream the response
            def stream_content():
                try:
                    for chunk in response['Body'].iter_chunks(chunk_size=8192):
                        yield chunk
                except AttributeError:
                    # Fallback for different boto3 versions
                    while True:
                        chunk = response['Body'].read(8192)
                        if not chunk:
                            break
                        yield chunk
            
            # Create streaming response
            streaming_response = StreamingHttpResponse(
                stream_content(),
                content_type=response.get('ContentType', 'image/jpeg')
            )
            
            # Add cache headers (thumbnails can be cached longer)
            streaming_response['Cache-Control'] = 'public, max-age=86400'  # 24 hours
            streaming_response['Content-Length'] = str(response.get('ContentLength', 0))
            
            return streaming_response
            
        except ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchKey':
                raise Http404("Thumbnail not found")
            else:
                raise Http404("Error accessing thumbnail")
                
    except StorageBucket.DoesNotExist:
        raise Http404("Bucket not found")
    except Exception as e:
        raise Http404("Error serving thumbnail")


# Face thumbnail serving view
def serve_face_thumbnail(request, bucket_id, path):
    """
    Serve face thumbnails from MinIO storage through Django for access control.
    
    GET /face-thumbnails/<bucket_id>/<path>
    """
    try:
        # Get the storage bucket
        bucket = StorageBucket.objects.get(id=bucket_id)
        backend = bucket.backend
        
        # Only serve from S3/MinIO backends
        if backend.backend_type not in ['s3', 'minio']:
            raise Http404("Face thumbnail not found")
        
        # Create S3/MinIO client
        from .services import UploadService
        upload_service = UploadService(backend)
        client = upload_service.client
        
        # Get the object from MinIO
        try:
            # Handle path prefix for face thumbnails
            full_key = path
            if bucket.path_prefix:
                full_key = f"{bucket.path_prefix}/{path}"
            elif bucket.purpose == 'originals':
                # If we're using the originals bucket for face thumbnails, add face-thumbnails/ prefix
                full_key = f"face-thumbnails/{path}"
            
            response = client.get_object(Bucket=bucket.name, Key=full_key)
            
            # Stream the response
            def stream_content():
                try:
                    for chunk in response['Body'].iter_chunks(chunk_size=8192):
                        yield chunk
                except AttributeError:
                    # Fallback for different boto3 versions
                    while True:
                        chunk = response['Body'].read(8192)
                        if not chunk:
                            break
                        yield chunk
            
            # Create streaming response
            streaming_response = StreamingHttpResponse(
                stream_content(),
                content_type=response.get('ContentType', 'image/jpeg')
            )
            
            # Add cache headers (face thumbnails can be cached longer)
            streaming_response['Cache-Control'] = 'public, max-age=86400'  # 24 hours
            streaming_response['Content-Length'] = str(response.get('ContentLength', 0))
            
            return streaming_response
            
        except ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchKey':
                raise Http404("Face thumbnail not found")
            else:
                raise Http404("Error accessing face thumbnail")
                
    except StorageBucket.DoesNotExist:
        raise Http404("Bucket not found")
    except Exception as e:
        raise Http404("Error serving face thumbnail")