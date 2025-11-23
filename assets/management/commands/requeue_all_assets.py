from django.core.management.base import BaseCommand

from assets.models import Asset
from assets.tasks import generate_asset_thumbnails
from metadata.tasks import process_asset_metadata
from people.tasks import detect_faces


class Command(BaseCommand):
    help = "Requeue all assets for metadata and face detection processing"

    def add_arguments(self, parser):
        parser.add_argument(
            "--metadata-only", action="store_true", help="Only requeue metadata processing (skip face detection)"
        )
        parser.add_argument(
            "--faces-only", action="store_true", help="Only requeue face detection (skip metadata processing)"
        )
        parser.add_argument(
            "--thumbnails-only",
            action="store_true",
            help="Only requeue thumbnail generation (skip metadata and face detection)",
        )
        parser.add_argument(
            "--missing-dimensions", action="store_true", help="Only process assets where width/height are zero"
        )
        parser.add_argument("--limit", type=int, help="Limit the number of assets to process")
        parser.add_argument(
            "--dry-run", action="store_true", help="Show what would be processed without actually queuing tasks"
        )
        parser.add_argument(
            "--batch-size", type=int, default=10, help="Number of assets to process in each batch (default: 10)"
        )

    def handle(self, *args, **options):
        metadata_only = options["metadata_only"]
        faces_only = options["faces_only"]
        thumbnails_only = options["thumbnails_only"]
        missing_dimensions = options["missing_dimensions"]
        limit = options["limit"]
        dry_run = options["dry_run"]
        batch_size = options["batch_size"]

        # Validate arguments
        exclusive_options = [metadata_only, faces_only, thumbnails_only]
        if sum(exclusive_options) > 1:
            self.stdout.write(self.style.ERROR("Cannot specify multiple --*-only options"))
            return

        # Get all assets
        assets = Asset.objects.all().order_by("-created_at")
        if missing_dimensions:
            assets = assets.filter(width__lte=0) | assets.filter(height__lte=0)

        if limit:
            assets = assets[:limit]

        total_assets = assets.count()

        if total_assets == 0:
            self.stdout.write(self.style.WARNING("No assets found to process"))
            return

        # Show what will be processed
        self.stdout.write(f"Found {total_assets} assets to process")

        if metadata_only:
            self.stdout.write("Mode: Metadata processing only")
        elif faces_only:
            self.stdout.write("Mode: Face detection only")
        elif thumbnails_only:
            self.stdout.write("Mode: Thumbnail generation only")
        else:
            self.stdout.write("Mode: Metadata, face detection, and thumbnail generation")

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN - No tasks will be queued"))
            self.stdout.write(f"Would process {total_assets} assets in batches of {batch_size}")
            return

        # Process assets in batches
        processed_count = 0
        metadata_tasks = 0
        face_tasks = 0
        thumbnail_tasks = 0

        for i in range(0, total_assets, batch_size):
            batch = assets[i : i + batch_size]
            batch_assets = list(batch)

            self.stdout.write(f"Processing batch {i//batch_size + 1} ({len(batch_assets)} assets)...")

            for asset in batch_assets:
                try:
                    # Queue metadata processing
                    if not faces_only and not thumbnails_only:
                        process_asset_metadata.delay(str(asset.id))
                        metadata_tasks += 1

                    # Queue face detection
                    if not metadata_only and not thumbnails_only:
                        detect_faces.delay(str(asset.id))
                        face_tasks += 1

                    # Queue thumbnail generation
                    if not metadata_only and not faces_only:
                        generate_asset_thumbnails.delay(str(asset.id))
                        thumbnail_tasks += 1

                    processed_count += 1

                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"Failed to queue asset {asset.id}: {e}"))
                    continue

        # Summary
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"✓ Successfully queued {processed_count} assets"))

        if not faces_only and not thumbnails_only:
            self.stdout.write(f"  - {metadata_tasks} metadata processing tasks")
        if not metadata_only and not thumbnails_only:
            self.stdout.write(f"  - {face_tasks} face detection tasks")
        if not metadata_only and not faces_only:
            self.stdout.write(f"  - {thumbnail_tasks} thumbnail generation tasks")

        self.stdout.write("")
        self.stdout.write("Tasks have been queued for processing by the worker.")
        self.stdout.write("Make sure your Celery worker is running: python start_worker.py")

        if not faces_only and not thumbnails_only:
            self.stdout.write("")
            self.stdout.write("Metadata processing will:")
            self.stdout.write("  - Extract EXIF data and descriptions")
            self.stdout.write("  - Generate keywords from metadata")
            self.stdout.write("  - Create CLIP embeddings for semantic search")
            self.stdout.write("  - Trigger face detection automatically")

        if not metadata_only and not thumbnails_only:
            self.stdout.write("")
            self.stdout.write("Face detection will:")
            self.stdout.write("  - Detect faces using InsightFace buffalo_l model")
            self.stdout.write("  - Extract face embeddings")
            self.stdout.write("  - Automatically cluster faces into persons (if ≥3 faces)")

        if not metadata_only and not faces_only:
            self.stdout.write("")
            self.stdout.write("Thumbnail generation will:")
            self.stdout.write("  - Generate thumbnails in 4 sizes: xs (150px), sm (300px), md (600px), lg (1200px)")
            self.stdout.write("  - Upload thumbnails to storage")
            self.stdout.write("  - Update database records")
