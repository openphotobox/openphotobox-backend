"""
Management command to set up local filesystem storage.

Usage:
    # Interactive mode
    python manage.py setup_local_storage

    # Non-interactive mode
    python manage.py setup_local_storage --path /var/photos --name "My Photos" --default
"""

from django.conf import settings
from django.core.management.base import BaseCommand

from assets.models import StorageBackend, StorageBucket


class Command(BaseCommand):
    help = "Set up local filesystem storage for OpenPhotobox"

    def add_arguments(self, parser):
        parser.add_argument(
            "--path",
            type=str,
            help="Base path for photo storage (e.g., /var/photos)",
        )
        parser.add_argument(
            "--name",
            type=str,
            help="Name for the storage backend (e.g., 'Primary Storage')",
        )
        parser.add_argument(
            "--default",
            action="store_true",
            help="Set this as the default storage backend",
        )
        parser.add_argument(
            "--originals-bucket",
            type=str,
            help="Name for originals bucket (default: 'originals')",
        )
        parser.add_argument(
            "--thumbnails-bucket",
            type=str,
            help="Name for thumbnails bucket (default: 'thumbnails')",
        )

    def handle(self, *args, **options):
        # Check if running in interactive or non-interactive mode
        interactive = not all([options.get("path"), options.get("name")])

        if interactive:
            self.stdout.write(self.style.SUCCESS("\n=== Local Storage Setup ===\n"))
            storage_data = self._interactive_mode()
        else:
            storage_data = self._non_interactive_mode(options)

        # Create storage backend
        backend, created = self._create_backend(storage_data)

        if created:
            self.stdout.write(self.style.SUCCESS(f"\n✓ Created storage backend: {backend.name}"))
        else:
            self.stdout.write(self.style.SUCCESS(f"\n✓ Updated storage backend: {backend.name}"))

        # Create buckets
        originals_bucket = self._create_bucket(backend, "originals", storage_data.get("originals_bucket", "originals"))
        thumbnails_bucket = self._create_bucket(
            backend, "thumbnails", storage_data.get("thumbnails_bucket", "thumbnails")
        )

        # Summary
        self.stdout.write("\n" + "=" * 60)
        self.stdout.write(self.style.SUCCESS("Storage Configuration Complete!"))
        self.stdout.write("=" * 60)
        self.stdout.write(f"  Backend Name: {backend.name}")
        self.stdout.write(f"  Storage Path: {backend.get_base_path()}")
        self.stdout.write(f"  Default Backend: {'Yes' if backend.is_default else 'No'}")
        self.stdout.write("\n  Buckets:")
        self.stdout.write(f"    - Originals: {originals_bucket.name}")
        self.stdout.write(f"    - Thumbnails: {thumbnails_bucket.name}")
        self.stdout.write("\n  File Organization:")
        self.stdout.write(f"    {backend.get_base_path()}/")
        self.stdout.write("      originals/YYYY/MM/DD/{uuid}.jpg")
        self.stdout.write("      thumbnails/YYYY/MM/DD/{uuid}_md.jpg")
        self.stdout.write("=" * 60)
        self.stdout.write(self.style.SUCCESS("\n✓ You can now upload photos!\n"))

    def _interactive_mode(self):
        """Collect configuration data interactively."""
        self.stdout.write("This wizard will help you set up local filesystem storage.\n")

        # Get default path from settings
        default_path = settings.OPENPHOTOBOX.get("DEFAULT_STORAGE_PATH", "/var/photos")

        # Storage path
        path = input(f"Storage path (default: {default_path}): ").strip()
        if not path:
            path = default_path

        # Backend name
        name = input("Backend name (default: 'Local Storage'): ").strip()
        if not name:
            name = "Local Storage"

        # Set as default
        is_default = input("Set as default backend? (Y/n): ").strip().lower()
        is_default = is_default != "n"

        # Bucket names
        originals = input("Originals bucket name (default: 'originals'): ").strip()
        if not originals:
            originals = "originals"

        thumbnails = input("Thumbnails bucket name (default: 'thumbnails'): ").strip()
        if not thumbnails:
            thumbnails = "thumbnails"

        return {
            "path": path,
            "name": name,
            "is_default": is_default,
            "originals_bucket": originals,
            "thumbnails_bucket": thumbnails,
        }

    def _non_interactive_mode(self, options):
        """Use command line options for configuration."""
        return {
            "path": options["path"],
            "name": options["name"],
            "is_default": options.get("default", False),
            "originals_bucket": options.get("originals_bucket", "originals"),
            "thumbnails_bucket": options.get("thumbnails_bucket", "thumbnails"),
        }

    def _create_backend(self, data):
        """Create or update storage backend."""
        name = data["name"]
        path = data["path"]
        is_default = data["is_default"]

        # If setting as default, unset other defaults
        if is_default:
            StorageBackend.objects.filter(is_default=True).update(is_default=False)

        # Create or update backend
        backend, created = StorageBackend.objects.update_or_create(
            name=name,
            defaults={
                "backend_type": "local",
                "config": {"base_path": path},
                "is_default": is_default,
                "is_active": True,
            },
        )

        # Ensure storage directory exists
        import os

        os.makedirs(path, exist_ok=True)
        os.makedirs(os.path.join(path, "originals"), exist_ok=True)
        os.makedirs(os.path.join(path, "thumbnails"), exist_ok=True)

        return backend, created

    def _create_bucket(self, backend, purpose, bucket_name):
        """Create or update a storage bucket."""
        display_name = purpose.capitalize()

        bucket, created = StorageBucket.objects.update_or_create(
            backend=backend,
            purpose=purpose,
            defaults={
                "name": bucket_name,
                "display_name": display_name,
                "is_active": True,
            },
        )

        if created:
            self.stdout.write(self.style.SUCCESS(f"  ✓ Created {purpose} bucket: {bucket_name}"))
        else:
            self.stdout.write(self.style.SUCCESS(f"  ✓ Updated {purpose} bucket: {bucket_name}"))

        return bucket
