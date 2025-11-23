from django.core.management.base import BaseCommand
from django.db.models import Count

from assets.models import Asset
from assets.tasks import generate_asset_thumbnails


class Command(BaseCommand):
    help = "Regenerate thumbnails for all assets or specific assets"

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, help="Limit the number of assets to process")
        parser.add_argument(
            "--dry-run", action="store_true", help="Show what would be processed without actually queuing tasks"
        )
        parser.add_argument(
            "--batch-size", type=int, default=10, help="Number of assets to process in each batch (default: 10)"
        )
        parser.add_argument(
            "--sizes",
            nargs="+",
            choices=["xs", "sm", "md", "lg"],
            default=["xs", "sm", "md", "lg"],
            help="Thumbnail sizes to generate (default: all sizes)",
        )
        parser.add_argument("--force", action="store_true", help="Force regeneration even if thumbnails already exist")
        parser.add_argument(
            "--missing-only", action="store_true", help="Only generate thumbnails for assets that have no thumbnails"
        )
        parser.add_argument("--asset-ids", nargs="+", help="Specific asset IDs to process (UUIDs)")

    def handle(self, *args, **options):
        limit = options["limit"]
        dry_run = options["dry_run"]
        batch_size = options["batch_size"]
        sizes = options["sizes"]
        force = options["force"]
        missing_only = options["missing_only"]
        asset_ids = options["asset_ids"]

        # Get assets to process
        if asset_ids:
            # Process specific assets
            assets = Asset.objects.filter(id__in=asset_ids).order_by("-created_at")
            if not assets.exists():
                self.stdout.write(self.style.ERROR(f"No assets found with IDs: {asset_ids}"))
                return
        else:
            # Get all assets
            assets = Asset.objects.all().order_by("-created_at")

            if missing_only:
                # Only assets without thumbnails
                assets = assets.annotate(thumbnail_count=Count("thumbnails")).filter(thumbnail_count=0)

            if limit:
                assets = assets[:limit]

        total_assets = assets.count()

        if total_assets == 0:
            self.stdout.write(self.style.WARNING("No assets found to process"))
            return

        # Show what will be processed
        self.stdout.write(f"Found {total_assets} assets to process")
        self.stdout.write(f'Thumbnail sizes: {", ".join(sizes)}')

        if force:
            self.stdout.write("Mode: Force regeneration (will overwrite existing thumbnails)")
        elif missing_only:
            self.stdout.write("Mode: Generate missing thumbnails only")
        else:
            self.stdout.write("Mode: Generate all thumbnails (skip existing)")

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN - No tasks will be queued"))
            self.stdout.write(f"Would process {total_assets} assets in batches of {batch_size}")

            # Show some examples
            sample_assets = assets[:5]
            self.stdout.write("\nSample assets that would be processed:")
            for asset in sample_assets:
                existing_thumbnails = asset.thumbnails.filter(is_ready=True).count()
                self.stdout.write(f"  - {asset.id}: {existing_thumbnails} existing thumbnails")
            return

        # Process assets in batches
        processed_count = 0
        thumbnail_tasks = 0
        skipped_count = 0

        for i in range(0, total_assets, batch_size):
            batch = assets[i : i + batch_size]
            batch_assets = list(batch)

            self.stdout.write(f"Processing batch {i//batch_size + 1} ({len(batch_assets)} assets)...")

            for asset in batch_assets:
                try:
                    # Check if thumbnails already exist
                    existing_thumbnails = asset.thumbnails.filter(is_ready=True)
                    existing_sizes = set(existing_thumbnails.values_list("size", flat=True))

                    # Determine which sizes to generate
                    sizes_to_generate = []
                    for size in sizes:
                        if force or size not in existing_sizes:
                            sizes_to_generate.append(size)

                    if not sizes_to_generate:
                        skipped_count += 1
                        self.stdout.write(f"  Skipping {asset.id} - all thumbnails already exist")
                        continue

                    # Queue thumbnail generation
                    generate_asset_thumbnails.delay(str(asset.id), sizes_to_generate)
                    thumbnail_tasks += 1
                    processed_count += 1

                    self.stdout.write(f'  Queued {asset.id} for sizes: {", ".join(sizes_to_generate)}')

                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"Failed to queue asset {asset.id}: {e}"))
                    continue

        # Summary
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"✓ Successfully queued {processed_count} assets"))

        if skipped_count > 0:
            self.stdout.write(f"  - {skipped_count} assets skipped (thumbnails already exist)")

        self.stdout.write(f"  - {thumbnail_tasks} thumbnail generation tasks")

        self.stdout.write("")
        self.stdout.write("Tasks have been queued for processing by the worker.")
        self.stdout.write("Make sure your Celery worker is running: python start_worker.py")

        self.stdout.write("")
        self.stdout.write("Thumbnail generation will:")
        self.stdout.write("  - Download original images from storage")
        self.stdout.write("  - Generate thumbnails in sizes: " + ", ".join(sizes))
        self.stdout.write("  - Upload thumbnails to storage")
        self.stdout.write("  - Update database records")

        # Show thumbnail size information
        self.stdout.write("")
        self.stdout.write("Thumbnail sizes:")
        size_info = {"xs": "150px (Extra Small)", "sm": "300px (Small)", "md": "600px (Medium)", "lg": "1200px (Large)"}
        for size in sizes:
            self.stdout.write(f"  - {size}: {size_info[size]}")
