from django.core.management.base import BaseCommand
from django.db import transaction
from assets.models import StorageBucket, AssetThumbnail, FaceThumbnail
from assets.services import get_default_upload_bucket


class Command(BaseCommand):
    help = 'Migrate thumbnails from separate bucket to subdirectory in originals bucket'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be migrated without actually doing it'
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Force migration even if thumbnails bucket already exists'
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        force = options['force']

        # Get the originals bucket
        try:
            originals_bucket = get_default_upload_bucket('originals')
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'Failed to get originals bucket: {e}')
            )
            return

        # Check if thumbnails bucket already exists
        try:
            existing_thumbnails_bucket = StorageBucket.objects.get(
                purpose='thumbnails', 
                is_active=True
            )
            
            if not force:
                self.stdout.write(
                    self.style.WARNING(
                        f'Thumbnails bucket already exists: {existing_thumbnails_bucket.name}'
                    )
                )
                self.stdout.write(
                    'Use --force to migrate anyway, or delete the existing bucket first'
                )
                return
            
            self.stdout.write(
                f'Found existing thumbnails bucket: {existing_thumbnails_bucket.name}'
            )
            
        except StorageBucket.DoesNotExist:
            existing_thumbnails_bucket = None

        # Count thumbnails that would be affected
        asset_thumbnails_count = AssetThumbnail.objects.count()
        face_thumbnails_count = FaceThumbnail.objects.count()
        
        self.stdout.write(f'Found {asset_thumbnails_count} asset thumbnails')
        self.stdout.write(f'Found {face_thumbnails_count} face thumbnails')
        
        if asset_thumbnails_count == 0 and face_thumbnails_count == 0:
            self.stdout.write(
                self.style.WARNING('No thumbnails found to migrate')
            )
            return

        # Show what will happen
        self.stdout.write('')
        self.stdout.write('Migration plan:')
        self.stdout.write(f'  - Originals bucket: {originals_bucket.name}')
        self.stdout.write(f'  - Thumbnails will be stored in: {originals_bucket.name}/thumbnails/')
        self.stdout.write(f'  - Asset thumbnails: {asset_thumbnails_count}')
        self.stdout.write(f'  - Face thumbnails: {face_thumbnails_count}')
        
        if existing_thumbnails_bucket:
            self.stdout.write(f'  - Existing thumbnails bucket will be: {existing_thumbnails_bucket.name}')

        if dry_run:
            self.stdout.write('')
            self.stdout.write(
                self.style.WARNING('DRY RUN - No changes will be made')
            )
            return

        # Confirm migration
        confirm = input('\nProceed with migration? (y/N): ')
        if confirm.lower() != 'y':
            self.stdout.write('Migration cancelled')
            return

        # Perform migration
        try:
            with transaction.atomic():
                # Create new thumbnails bucket using same bucket as originals
                if existing_thumbnails_bucket:
                    # Update existing bucket to use same bucket as originals
                    existing_thumbnails_bucket.name = originals_bucket.name
                    existing_thumbnails_bucket.backend = originals_bucket.backend
                    existing_thumbnails_bucket.path_prefix = 'thumbnails'
                    existing_thumbnails_bucket.save()
                    
                    new_thumbnails_bucket = existing_thumbnails_bucket
                    self.stdout.write('Updated existing thumbnails bucket configuration')
                else:
                    # Create new thumbnails bucket
                    new_thumbnails_bucket = StorageBucket.objects.create(
                        backend=originals_bucket.backend,
                        name=originals_bucket.name,  # Same bucket as originals
                        display_name='Thumbnails',
                        purpose='thumbnails',
                        is_active=True,
                        path_prefix='thumbnails'  # Subdirectory within bucket
                    )
                    self.stdout.write('Created new thumbnails bucket configuration')

                # Update all asset thumbnails to use new bucket
                updated_asset_thumbnails = AssetThumbnail.objects.update(
                    storage_bucket=new_thumbnails_bucket
                )
                self.stdout.write(f'Updated {updated_asset_thumbnails} asset thumbnails')

                # Update all face thumbnails to use new bucket
                updated_face_thumbnails = FaceThumbnail.objects.update(
                    storage_bucket=new_thumbnails_bucket
                )
                self.stdout.write(f'Updated {updated_face_thumbnails} face thumbnails')

                self.stdout.write('')
                self.stdout.write(
                    self.style.SUCCESS('✓ Migration completed successfully!')
                )
                
                self.stdout.write('')
                self.stdout.write('Next steps:')
                self.stdout.write('1. Regenerate thumbnails to upload them to the new location:')
                self.stdout.write('   python manage.py regenerate_thumbnails --force')
                self.stdout.write('')
                self.stdout.write('2. Verify thumbnails are working in the frontend')
                self.stdout.write('')
                self.stdout.write('3. Optionally clean up old thumbnail files from storage')

        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'Migration failed: {e}')
            )
            raise
