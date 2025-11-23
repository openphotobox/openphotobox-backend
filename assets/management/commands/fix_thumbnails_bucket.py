from django.core.management.base import BaseCommand
from django.db import transaction
from assets.models import StorageBucket, AssetThumbnail, FaceThumbnail
from assets.services import get_default_upload_bucket, UploadService
from botocore.exceptions import ClientError


class Command(BaseCommand):
    help = 'Fix thumbnails bucket configuration to use the same bucket as originals'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be fixed without actually doing it'
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Force fix even if thumbnails bucket seems correct'
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        force = options['force']

        # Get the originals bucket
        try:
            originals_bucket = get_default_upload_bucket('originals')
            self.stdout.write(f'Originals bucket: {originals_bucket.name}')
            self.stdout.write(f'Originals backend: {originals_bucket.backend.name} ({originals_bucket.backend.backend_type})')
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'Failed to get originals bucket: {e}')
            )
            return

        # Test if originals bucket actually exists in storage
        try:
            upload_service = UploadService(originals_bucket.backend)
            client = upload_service.client
            
            # Try to list objects in the bucket to verify it exists
            response = client.list_objects_v2(Bucket=originals_bucket.name, MaxKeys=1)
            self.stdout.write(f'✓ Originals bucket "{originals_bucket.name}" exists in storage')
        except ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchBucket':
                self.stdout.write(
                    self.style.ERROR(f'Originals bucket "{originals_bucket.name}" does not exist in storage!')
                )
                self.stdout.write('You need to create this bucket first in your MinIO/S3 instance.')
                return
            else:
                self.stdout.write(
                    self.style.ERROR(f'Error checking originals bucket: {e}')
                )
                return
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'Error connecting to storage: {e}')
            )
            return

        # Check current thumbnails bucket configuration
        try:
            thumbnails_bucket = StorageBucket.objects.get(purpose='thumbnails', is_active=True)
            self.stdout.write(f'Current thumbnails bucket: {thumbnails_bucket.name}')
            self.stdout.write(f'Current thumbnails backend: {thumbnails_bucket.backend.name}')
            self.stdout.write(f'Current path prefix: {thumbnails_bucket.path_prefix or "(none)"}')
            
            # Check if thumbnails bucket name matches originals
            if thumbnails_bucket.name == originals_bucket.name:
                self.stdout.write('✓ Thumbnails bucket name matches originals bucket')
            else:
                self.stdout.write(
                    self.style.WARNING(f'⚠ Thumbnails bucket name "{thumbnails_bucket.name}" does not match originals bucket "{originals_bucket.name}"')
                )
            
            # Check if thumbnails bucket backend matches originals
            if thumbnails_bucket.backend.id == originals_bucket.backend.id:
                self.stdout.write('✓ Thumbnails bucket backend matches originals bucket')
            else:
                self.stdout.write(
                    self.style.WARNING(f'⚠ Thumbnails bucket backend does not match originals bucket')
                )
            
            # Check if path prefix is set
            if thumbnails_bucket.path_prefix == 'thumbnails':
                self.stdout.write('✓ Path prefix is correctly set to "thumbnails"')
            else:
                self.stdout.write(
                    self.style.WARNING(f'⚠ Path prefix is "{thumbnails_bucket.path_prefix}" but should be "thumbnails"')
                )
                
        except StorageBucket.DoesNotExist:
            self.stdout.write('No thumbnails bucket found - will create one')
            thumbnails_bucket = None

        # Count existing thumbnails
        asset_thumbnails_count = AssetThumbnail.objects.count()
        face_thumbnails_count = FaceThumbnail.objects.count()
        
        self.stdout.write(f'Existing asset thumbnails: {asset_thumbnails_count}')
        self.stdout.write(f'Existing face thumbnails: {face_thumbnails_count}')

        # Determine if fix is needed
        needs_fix = False
        if thumbnails_bucket is None:
            needs_fix = True
            self.stdout.write('Fix needed: No thumbnails bucket exists')
        elif (thumbnails_bucket.name != originals_bucket.name or 
              thumbnails_bucket.backend.id != originals_bucket.backend.id or
              thumbnails_bucket.path_prefix != 'thumbnails'):
            needs_fix = True
            self.stdout.write('Fix needed: Thumbnails bucket configuration is incorrect')
        elif force:
            needs_fix = True
            self.stdout.write('Fix needed: --force flag specified')

        if not needs_fix:
            self.stdout.write(
                self.style.SUCCESS('✓ Thumbnails bucket configuration is correct!')
            )
            return

        # Show what will be fixed
        self.stdout.write('')
        self.stdout.write('Fix plan:')
        if thumbnails_bucket is None:
            self.stdout.write('  - Create new thumbnails bucket record')
        else:
            self.stdout.write('  - Update existing thumbnail records to point to originals bucket')
            self.stdout.write('  - Delete old thumbnails bucket record')
            self.stdout.write('  - All thumbnails will now use the originals bucket with thumbnails/ path prefix')
        self.stdout.write(f'  - Thumbnails will be stored in: {originals_bucket.name}/thumbnails/')
        self.stdout.write(f'  - Backend: {originals_bucket.backend.name}')
        if asset_thumbnails_count > 0 or face_thumbnails_count > 0:
            self.stdout.write(f'  - Update {asset_thumbnails_count + face_thumbnails_count} existing thumbnail records')

        if dry_run:
            self.stdout.write('')
            self.stdout.write(
                self.style.WARNING('DRY RUN - No changes will be made')
            )
            return

        # Confirm fix
        confirm = input('\nProceed with fix? (y/N): ')
        if confirm.lower() != 'y':
            self.stdout.write('Fix cancelled')
            return

        # Perform fix
        try:
            with transaction.atomic():
                if thumbnails_bucket is None:
                    # No thumbnails bucket exists, so we need to create one
                    # We'll create one with a different name to avoid the unique constraint
                    new_thumbnails_bucket = StorageBucket.objects.create(
                        backend=originals_bucket.backend,
                        name=f"{originals_bucket.name}-thumbnails",
                        display_name='Thumbnails',
                        purpose='thumbnails',
                        is_active=True,
                        path_prefix='thumbnails'
                    )
                    self.stdout.write('✓ Created new thumbnails bucket record')
                else:
                    # Update existing thumbnail records to point to originals bucket
                    if asset_thumbnails_count > 0:
                        updated_count = AssetThumbnail.objects.update(
                            storage_bucket=originals_bucket
                        )
                        self.stdout.write(f'✓ Updated {updated_count} asset thumbnail records to point to originals bucket')

                    if face_thumbnails_count > 0:
                        updated_count = FaceThumbnail.objects.update(
                            storage_bucket=originals_bucket
                        )
                        self.stdout.write(f'✓ Updated {updated_count} face thumbnail records to point to originals bucket')
                    
                    # Delete the old thumbnails bucket record
                    thumbnails_bucket.delete()
                    self.stdout.write('✓ Deleted old thumbnails bucket record')
                    
                    # We don't need to create a new thumbnails bucket record
                    # All thumbnails will now point directly to the originals bucket
                    # The path_prefix will be handled in the upload logic
                    self.stdout.write('✓ All thumbnails now point to originals bucket')

                self.stdout.write('')
                self.stdout.write(
                    self.style.SUCCESS('✓ Fix completed successfully!')
                )
                
                self.stdout.write('')
                self.stdout.write('Next steps:')
                self.stdout.write('1. Test thumbnail generation:')
                self.stdout.write('   python manage.py regenerate_thumbnails --limit 1')
                self.stdout.write('')
                self.stdout.write('2. If successful, regenerate all thumbnails:')
                self.stdout.write('   python manage.py regenerate_thumbnails --missing-only')

        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'Fix failed: {e}')
            )
            raise
