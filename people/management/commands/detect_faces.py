from django.core.management.base import BaseCommand
from django.db import transaction
from assets.models import Asset
from people.tasks import detect_faces, cluster_faces


class Command(BaseCommand):
    help = 'Detect faces in assets using InsightFace'

    def add_arguments(self, parser):
        parser.add_argument(
            '--asset-id',
            type=str,
            help='Detect faces for a specific asset ID'
        )
        parser.add_argument(
            '--all',
            action='store_true',
            help='Detect faces for all assets'
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=10,
            help='Limit number of assets to process (default: 10)'
        )
        parser.add_argument(
            '--async',
            action='store_true',
            help='Process asynchronously using Celery workers'
        )
        parser.add_argument(
            '--min-faces',
            type=int,
            default=3,
            help='Minimum number of faces required to create a person (default: 3)'
        )

    def handle(self, *args, **options):
        if options['asset_id']:
            # Process specific asset
            try:
                asset = Asset.objects.get(id=options['asset_id'])
                self.process_asset(asset, options['async'])
            except Asset.DoesNotExist:
                self.stdout.write(
                    self.style.ERROR(f'Asset {options["asset_id"]} not found')
                )
                return
        elif options['all']:
            # Process all assets
            assets = Asset.objects.all()[:options['limit']]
            self.stdout.write(f'Detecting faces for {len(assets)} assets...')
            
            for asset in assets:
                self.process_asset(asset, options['async'])
        else:
            # Process recent assets
            assets = Asset.objects.order_by('-created_at')[:options['limit']]
            self.stdout.write(f'Detecting faces for {len(assets)} recent assets...')
            
            for asset in assets:
                self.process_asset(asset, options['async'])
        
        # Run clustering if not using async
        if not options['async']:
            self.stdout.write(f'\nRunning face clustering (min_faces={options["min_faces"]})...')
            try:
                result = cluster_faces('dummy', options['min_faces'])
                if result['success']:
                    self.stdout.write(
                        self.style.SUCCESS(f'✓ Clustering complete: {result["persons_created"]} persons created, {result["faces_assigned"]} faces assigned')
                    )
                else:
                    self.stdout.write(
                        self.style.ERROR(f'✗ Clustering failed: {result.get("error", "Unknown error")}')
                    )
            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(f'✗ Clustering error: {str(e)}')
                )

    def process_asset(self, asset, use_async=False):
        """Process face detection for a single asset."""
        self.stdout.write(f'Processing asset {asset.id}...')
        
        if use_async:
            # Queue the task for async processing
            try:
                task = detect_faces.delay(str(asset.id))
                self.stdout.write(f'  Queued face detection task {task.id}')
            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(f'  ✗ Failed to queue async task: {str(e)}')
                )
        else:
            # Process synchronously
            try:
                result = detect_faces(str(asset.id))
                if result['success']:
                    self.stdout.write(
                        self.style.SUCCESS(f'  ✓ Detected {result["faces_detected"]} faces')
                    )
                else:
                    self.stdout.write(
                        self.style.ERROR(f'  ✗ Failed: {result.get("error", "Unknown error")}')
                    )
            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(f'  ✗ Error: {str(e)}')
                )

