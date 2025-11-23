from django.core.management.base import BaseCommand

from assets.models import Asset
from metadata.tasks import process_asset_metadata


class Command(BaseCommand):
    help = "Process metadata for existing assets"

    def add_arguments(self, parser):
        parser.add_argument("--asset-id", type=str, help="Process metadata for a specific asset ID")
        parser.add_argument("--all", action="store_true", help="Process metadata for all assets")
        parser.add_argument("--limit", type=int, default=10, help="Limit number of assets to process (default: 10)")
        parser.add_argument("--async", action="store_true", help="Process asynchronously using Celery workers")

    def handle(self, *args, **options):
        if options["asset_id"]:
            # Process specific asset
            try:
                asset = Asset.objects.get(id=options["asset_id"])
                self.process_asset(asset, options["async"])
            except Asset.DoesNotExist:
                self.stdout.write(self.style.ERROR(f'Asset {options["asset_id"]} not found'))
                return
        elif options["all"]:
            # Process all assets
            assets = Asset.objects.all()[: options["limit"]]
            self.stdout.write(f"Processing metadata for {len(assets)} assets...")

            for asset in assets:
                self.process_asset(asset, options["async"])
        else:
            # Process recent assets
            assets = Asset.objects.order_by("-created_at")[: options["limit"]]
            self.stdout.write(f"Processing metadata for {len(assets)} recent assets...")

            for asset in assets:
                self.process_asset(asset, options["async"])

    def process_asset(self, asset, use_async=False):
        """Process metadata for a single asset."""
        self.stdout.write(f"Processing asset {asset.id}...")

        if use_async:
            # Queue the task for async processing
            try:
                task = process_asset_metadata.delay(str(asset.id))
                self.stdout.write(f"  Queued task {task.id} for async processing")
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"  ✗ Failed to queue async task: {str(e)}"))
        else:
            # Process synchronously
            try:
                result = process_asset_metadata(str(asset.id))
                if result["success"]:
                    self.stdout.write(self.style.SUCCESS("  ✓ Processed successfully"))
                    if result.get("metadata_created"):
                        self.stdout.write("    Created new metadata record")
                    if result.get("exif_fields_extracted", 0) > 0:
                        self.stdout.write(f'    Extracted {result["exif_fields_extracted"]} EXIF fields')
                    if result.get("description_extracted"):
                        self.stdout.write("    Extracted description from metadata")
                else:
                    self.stdout.write(self.style.ERROR(f'  ✗ Failed: {result.get("error", "Unknown error")}'))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"  ✗ Error: {str(e)}"))
