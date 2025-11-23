from django.core.management.base import BaseCommand
from django.db.models import Q
from assets.models import Asset
from metadata.tasks import process_asset_metadata
from people.tasks import detect_faces
from datetime import datetime, timedelta


class Command(BaseCommand):
    help = 'Requeue assets for processing with advanced filtering options'

    def add_arguments(self, parser):
        parser.add_argument(
            '--metadata-only',
            action='store_true',
            help='Only requeue metadata processing (skip face detection)'
        )
        parser.add_argument(
            '--faces-only',
            action='store_true',
            help='Only requeue face detection (skip metadata processing)'
        )
        parser.add_argument(
            '--limit',
            type=int,
            help='Limit the number of assets to process'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be processed without actually queuing tasks'
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=10,
            help='Number of assets to process in each batch (default: 10)'
        )
        parser.add_argument(
            '--recent',
            type=int,
            help='Only process assets uploaded in the last N days'
        )
        parser.add_argument(
            '--no-metadata',
            action='store_true',
            help='Only process assets that have no metadata'
        )
        parser.add_argument(
            '--missing-dimensions',
            action='store_true',
            help='Only process assets where width/height are zero'
        )
        parser.add_argument(
            '--no-faces',
            action='store_true',
            help='Only process assets that have no face detection'
        )
        parser.add_argument(
            '--mime-type',
            help='Only process assets with specific MIME type (e.g., image/jpeg)'
        )
        parser.add_argument(
            '--asset-ids',
            nargs='+',
            help='Process specific asset IDs'
        )

    def handle(self, *args, **options):
        metadata_only = options['metadata_only']
        faces_only = options['faces_only']
        limit = options['limit']
        dry_run = options['dry_run']
        batch_size = options['batch_size']
        recent_days = options['recent']
        no_metadata = options['no_metadata']
        no_faces = options['no_faces']
        missing_dimensions = options['missing_dimensions']
        mime_type = options['mime_type']
        asset_ids = options['asset_ids']

        # Validate arguments
        if metadata_only and faces_only:
            self.stdout.write(
                self.style.ERROR('Cannot specify both --metadata-only and --faces-only')
            )
            return

        # Build query
        queryset = Asset.objects.all()
        
        # Filter by specific asset IDs
        if asset_ids:
            queryset = queryset.filter(id__in=asset_ids)
        
        # Filter by recent uploads
        if recent_days:
            cutoff_date = datetime.now() - timedelta(days=recent_days)
            queryset = queryset.filter(created_at__gte=cutoff_date)
        
        # Filter by MIME type
        if mime_type:
            queryset = queryset.filter(mime_type=mime_type)
        
        # Filter by metadata status
        if no_metadata:
            queryset = queryset.filter(metadata__isnull=True)
        
        # Filter by face detection status
        if no_faces:
            queryset = queryset.filter(faces__isnull=True)

        # Filter by missing dimensions
        if missing_dimensions:
            queryset = queryset.filter(width__lte=0) | queryset.filter(height__lte=0)
        
        # Order by creation date (newest first)
        queryset = queryset.order_by('-created_at')
        
        # Apply limit
        if limit:
            queryset = queryset[:limit]

        total_assets = queryset.count()
        
        if total_assets == 0:
            self.stdout.write(
                self.style.WARNING('No assets found matching the criteria')
            )
            return

        # Show what will be processed
        self.stdout.write(f'Found {total_assets} assets matching criteria')
        
        if metadata_only:
            self.stdout.write('Mode: Metadata processing only')
        elif faces_only:
            self.stdout.write('Mode: Face detection only')
        else:
            self.stdout.write('Mode: Both metadata and face detection')

        # Show filters applied
        filters_applied = []
        if asset_ids:
            filters_applied.append(f'Specific IDs: {len(asset_ids)} assets')
        if recent_days:
            filters_applied.append(f'Last {recent_days} days')
        if mime_type:
            filters_applied.append(f'MIME type: {mime_type}')
        if no_metadata:
            filters_applied.append('No metadata')
        if no_faces:
            filters_applied.append('No face detection')
        if missing_dimensions:
            filters_applied.append('Missing dimensions (width/height <= 0)')
        
        if filters_applied:
            self.stdout.write(f'Filters: {", ".join(filters_applied)}')

        if dry_run:
            self.stdout.write(
                self.style.WARNING('DRY RUN - No tasks will be queued')
            )
            self.stdout.write(f'Would process {total_assets} assets in batches of {batch_size}')
            
            # Show sample of assets that would be processed
            sample_assets = queryset[:5]
            self.stdout.write('Sample assets:')
            for asset in sample_assets:
                self.stdout.write(f'  - {asset.id} ({asset.mime_type}) - {asset.created_at.strftime("%Y-%m-%d %H:%M")}')
            if total_assets > 5:
                self.stdout.write(f'  ... and {total_assets - 5} more')
            
            return

        # Process assets in batches
        processed_count = 0
        metadata_tasks = 0
        face_tasks = 0
        errors = 0

        for i in range(0, total_assets, batch_size):
            batch = queryset[i:i + batch_size]
            batch_assets = list(batch)
            
            self.stdout.write(f'Processing batch {i//batch_size + 1} ({len(batch_assets)} assets)...')
            
            for asset in batch_assets:
                try:
                    # Queue metadata processing
                    if not faces_only:
                        process_asset_metadata.delay(str(asset.id))
                        metadata_tasks += 1
                    
                    # Queue face detection
                    if not metadata_only:
                        detect_faces.delay(str(asset.id))
                        face_tasks += 1
                    
                    processed_count += 1
                    
                except Exception as e:
                    self.stdout.write(
                        self.style.ERROR(f'Failed to queue asset {asset.id}: {e}')
                    )
                    errors += 1
                    continue

        # Summary
        self.stdout.write('')
        if errors > 0:
            self.stdout.write(
                self.style.WARNING(f'⚠️  Completed with {errors} errors')
            )
        
        self.stdout.write(
            self.style.SUCCESS(f'✓ Successfully queued {processed_count} assets')
        )
        
        if not faces_only:
            self.stdout.write(f'  - {metadata_tasks} metadata processing tasks')
        if not metadata_only:
            self.stdout.write(f'  - {face_tasks} face detection tasks')
        
        self.stdout.write('')
        self.stdout.write('Tasks have been queued for processing by the worker.')
        self.stdout.write('Make sure your Celery worker is running: python start_worker.py')
        
        # Show processing details
        if not faces_only:
            self.stdout.write('')
            self.stdout.write('Metadata processing will:')
            self.stdout.write('  - Extract EXIF data and descriptions')
            self.stdout.write('  - Generate keywords from metadata')
            self.stdout.write('  - Create CLIP embeddings for semantic search')
            self.stdout.write('  - Trigger face detection automatically')
        
        if not metadata_only:
            self.stdout.write('')
            self.stdout.write('Face detection will:')
            self.stdout.write('  - Detect faces using InsightFace buffalo_l model')
            self.stdout.write('  - Extract face embeddings')
            self.stdout.write('  - Automatically cluster faces into persons (if ≥3 faces)')

