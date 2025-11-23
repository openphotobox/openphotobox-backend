from django.core.management.base import BaseCommand
from django.db import transaction
from assets.models import Asset
from metadata.tasks import process_asset_metadata


class Command(BaseCommand):
    help = 'Process metadata for assets using async batch processing'

    def add_arguments(self, parser):
        parser.add_argument(
            '--limit',
            type=int,
            default=50,
            help='Limit number of assets to process (default: 50)'
        )
        parser.add_argument(
            '--offset',
            type=int,
            default=0,
            help='Offset for pagination (default: 0)'
        )
        parser.add_argument(
            '--all',
            action='store_true',
            help='Process all assets (ignores limit)'
        )

    def handle(self, *args, **options):
        if options['all']:
            assets = Asset.objects.all()
            self.stdout.write(f'Processing metadata for ALL {assets.count()} assets...')
        else:
            assets = Asset.objects.all()[options['offset']:options['offset'] + options['limit']]
            self.stdout.write(f'Processing metadata for {len(assets)} assets (offset: {options["offset"]})...')
        
        # Queue all tasks asynchronously
        queued_tasks = []
        for i, asset in enumerate(assets):
            try:
                task = process_asset_metadata.delay(str(asset.id))
                queued_tasks.append((asset.id, task.id))
                self.stdout.write(f'  [{i+1:3d}] Queued asset {asset.id} -> task {task.id}')
            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(f'  [{i+1:3d}] Failed to queue asset {asset.id}: {e}')
                )
        
        self.stdout.write(f'\n{self.style.SUCCESS("✓")} Queued {len(queued_tasks)} tasks for async processing')
        self.stdout.write(f'Monitor progress with: celery -A openphotobox_backend inspect active')
        self.stdout.write(f'Check results with: celery -A openphotobox_backend inspect stats')

