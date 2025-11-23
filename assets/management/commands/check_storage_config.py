from django.core.management.base import BaseCommand
from assets.models import StorageBucket, StorageBackend
from assets.services import get_default_upload_bucket, UploadService
from botocore.exceptions import ClientError


class Command(BaseCommand):
    help = 'Check current storage configuration and bucket status'

    def handle(self, *args, **options):
        self.stdout.write('=== Storage Configuration Check ===\n')

        # Check storage backends
        backends = StorageBackend.objects.filter(is_active=True)
        self.stdout.write(f'Active storage backends: {backends.count()}')
        
        for backend in backends:
            self.stdout.write(f'  - {backend.name} ({backend.backend_type})')
            if backend.is_default:
                self.stdout.write('    ✓ Default backend')
            if backend.endpoint_url:
                self.stdout.write(f'    Endpoint: {backend.endpoint_url}')
            if backend.region:
                self.stdout.write(f'    Region: {backend.region}')

        self.stdout.write('')

        # Check storage buckets
        buckets = StorageBucket.objects.filter(is_active=True).select_related('backend')
        self.stdout.write(f'Active storage buckets: {buckets.count()}')
        
        for bucket in buckets:
            self.stdout.write(f'  - {bucket.display_name} ({bucket.purpose})')
            self.stdout.write(f'    Bucket name: {bucket.name}')
            self.stdout.write(f'    Backend: {bucket.backend.name}')
            if bucket.path_prefix:
                self.stdout.write(f'    Path prefix: {bucket.path_prefix}')
            else:
                self.stdout.write('    Path prefix: (none)')

        self.stdout.write('')

        # Check originals bucket
        try:
            originals_bucket = get_default_upload_bucket('originals')
            self.stdout.write(f'✓ Originals bucket found: {originals_bucket.name}')
            
            # Test if bucket exists in storage
            try:
                upload_service = UploadService(originals_bucket.backend)
                client = upload_service.client
                response = client.list_objects_v2(Bucket=originals_bucket.name, MaxKeys=1)
                self.stdout.write(f'✓ Originals bucket exists in storage')
                
                # Count objects
                count_response = client.list_objects_v2(Bucket=originals_bucket.name)
                object_count = count_response.get('KeyCount', 0)
                self.stdout.write(f'  Objects in bucket: {object_count}')
                
            except ClientError as e:
                if e.response['Error']['Code'] == 'NoSuchBucket':
                    self.stdout.write(
                        self.style.ERROR(f'✗ Originals bucket "{originals_bucket.name}" does not exist in storage!')
                    )
                else:
                    self.stdout.write(
                        self.style.ERROR(f'✗ Error checking originals bucket: {e}')
                    )
            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(f'✗ Error connecting to storage: {e}')
                )
                
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'✗ No originals bucket found: {e}')
            )

        self.stdout.write('')

        # Check thumbnails bucket
        try:
            thumbnails_bucket = StorageBucket.objects.get(purpose='thumbnails', is_active=True)
            self.stdout.write(f'✓ Thumbnails bucket found: {thumbnails_bucket.name}')
            self.stdout.write(f'  Backend: {thumbnails_bucket.backend.name}')
            self.stdout.write(f'  Path prefix: {thumbnails_bucket.path_prefix or "(none)"}')
            
            # Check if it matches originals bucket
            try:
                originals_bucket = get_default_upload_bucket('originals')
                if (thumbnails_bucket.name == originals_bucket.name and 
                    thumbnails_bucket.backend.id == originals_bucket.backend.id):
                    self.stdout.write('✓ Thumbnails bucket matches originals bucket')
                else:
                    self.stdout.write(
                        self.style.WARNING('⚠ Thumbnails bucket does not match originals bucket')
                    )
                    self.stdout.write(f'  Originals: {originals_bucket.name} ({originals_bucket.backend.name})')
                    self.stdout.write(f'  Thumbnails: {thumbnails_bucket.name} ({thumbnails_bucket.backend.name})')
            except Exception:
                pass
                
        except StorageBucket.DoesNotExist:
            self.stdout.write(
                self.style.WARNING('⚠ No thumbnails bucket found')
            )

        self.stdout.write('')

        # Check thumbnail records
        from assets.models import AssetThumbnail, FaceThumbnail
        asset_thumbnails = AssetThumbnail.objects.count()
        face_thumbnails = FaceThumbnail.objects.count()
        
        self.stdout.write(f'Asset thumbnails in database: {asset_thumbnails}')
        self.stdout.write(f'Face thumbnails in database: {face_thumbnails}')

        self.stdout.write('')
        self.stdout.write('=== Summary ===')
        
        # Overall status
        try:
            originals_bucket = get_default_upload_bucket('originals')
            thumbnails_bucket = StorageBucket.objects.get(purpose='thumbnails', is_active=True)
            
            if (thumbnails_bucket.name == originals_bucket.name and 
                thumbnails_bucket.backend.id == originals_bucket.backend.id and
                thumbnails_bucket.path_prefix == 'thumbnails'):
                self.stdout.write(
                    self.style.SUCCESS('✓ Storage configuration looks good!')
                )
            else:
                self.stdout.write(
                    self.style.WARNING('⚠ Storage configuration needs fixing')
                )
                self.stdout.write('Run: python manage.py fix_thumbnails_bucket')
                
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'✗ Storage configuration has issues: {e}')
            )
            self.stdout.write('Run: python manage.py fix_thumbnails_bucket')
