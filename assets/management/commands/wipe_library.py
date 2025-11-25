import shutil
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


class Command(BaseCommand):
    help = (
        "Delete all library data (photos/assets, albums, faces, thumbnails, metadata) including physical "
        "directories on disk (originals/thumbnails). Optional flags to include sharing and people. "
        "Intended for wiping a test database."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Run non-interactively and skip confirmation prompt.",
        )
        parser.add_argument(
            "--include-sharing",
            action="store_true",
            help="Also delete sharing data (access grants, user assets, links, rebuild logs).",
        )
        parser.add_argument(
            "--include-people",
            action="store_true",
            help="Also delete people (persons). Faces are deleted automatically with assets either way.",
        )
        parser.add_argument(
            "--include-notifications",
            action="store_true",
            help="Also delete notifications.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be deleted (counts) without deleting anything.",
        )

    def handle(self, *args, **options):
        include_people = bool(options.get("include_people"))
        include_notifications = bool(options.get("include_notifications"))
        dry_run = bool(options.get("dry_run"))
        assume_yes = bool(options.get("yes"))

        # Imports here to avoid app loading if not needed elsewhere
        from assets.models import Album, AlbumAsset, Asset, AssetThumbnail, StorageBucket
        from metadata.models import AssetKeyword, AssetMetadata, ClipEmbedding, KeywordTag, XmpSidecar
        from people.models import Face, FaceSearch, FaceThumbnail, Person

        # Optional apps
        SharingModels = None
        NotificationsModel = None
        if include_notifications:
            try:
                from notifications.models import Notification

                NotificationsModel = Notification
            except Exception:
                NotificationsModel = None

        # Build counts
        counts = {
            "Asset": Asset.objects.count(),
            "Album": Album.objects.count(),
            "AlbumAsset": AlbumAsset.objects.count(),
            "AssetThumbnail": AssetThumbnail.objects.count(),
            "Face": Face.objects.count(),
            "FaceSearch": FaceSearch.objects.count(),
            "FaceThumbnail": FaceThumbnail.objects.count(),
            "AssetMetadata": AssetMetadata.objects.count(),
            "ClipEmbedding": ClipEmbedding.objects.count(),
            "XmpSidecar": XmpSidecar.objects.count(),
            "AssetKeyword": AssetKeyword.objects.count(),
            "KeywordTag": KeywordTag.objects.count(),
        }
        if include_people:
            counts["Person"] = Person.objects.count()
        if SharingModels:
            for name, Model in SharingModels.items():
                counts[name] = Model.objects.count()
        if NotificationsModel is not None:
            counts["Notification"] = NotificationsModel.objects.count()

        # Collect storage directories to delete
        storage_dirs = []
        for bucket in StorageBucket.objects.select_related("backend").all():
            try:
                base_path = bucket.backend.get_base_path()
                # For local storage, the full path is base_path / bucket.name
                bucket_path = Path(base_path) / bucket.name
                if bucket_path.exists():
                    storage_dirs.append((bucket.purpose, str(bucket_path)))
            except Exception as e:
                self.stdout.write(
                    self.style.WARNING(f"Warning: Could not determine path for bucket '{bucket.display_name}': {e}")
                )

        # Show plan
        self.stdout.write("This will delete the following data (row counts):")
        for name, c in counts.items():
            self.stdout.write(f"- {name}: {c}")

        if storage_dirs:
            self.stdout.write("\nThe following directories will be deleted from disk:")
            for purpose, path in storage_dirs:
                self.stdout.write(f"- {purpose}: {path}")
        else:
            self.stdout.write(self.style.WARNING("\nWarning: No storage directories found to delete."))

        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run only. No data deleted."))
            return

        if not assume_yes:
            confirm = input("Type DELETE to confirm wiping the library: ").strip()
            if confirm != "DELETE":
                raise CommandError("Aborted.")

        with transaction.atomic():
            # Optional: sharing first (to avoid dangling references in materialized tables)
            if SharingModels:
                for name in [
                    "UserAssetRebuildLog",
                    "UserAsset",
                    "SharingLink",
                    "AccessGrant",
                ]:
                    Model = SharingModels.get(name)
                    if Model:
                        deleted, _ = Model.objects.all().delete()
                        self.stdout.write(f"Deleted {deleted} {name}")

            # Albums (removes through model via CASCADE)
            deleted, _ = Album.objects.all().delete()
            self.stdout.write(f"Deleted {deleted} Album")

            # Faces are deleted via Asset CASCADE, but FaceSearch/FaceThumbnail count listed for clarity
            # Assets (CASCADEs to thumbnails, metadata, faces, clip, xmp, keywords, through rows)
            deleted, _ = Asset.objects.all().delete()
            self.stdout.write(f"Deleted {deleted} Asset")

            # Optional: people (after faces are gone)
            if include_people:
                deleted, _ = Person.objects.all().delete()
                self.stdout.write(f"Deleted {deleted} Person")

            # Optional: notifications
            if NotificationsModel is not None:
                deleted, _ = NotificationsModel.objects.all().delete()
                self.stdout.write(f"Deleted {deleted} Notification")

        # Delete physical directories
        if storage_dirs:
            self.stdout.write("\nDeleting storage directories from disk...")
            for purpose, path in storage_dirs:
                try:
                    shutil.rmtree(path)
                    self.stdout.write(f"Deleted {purpose} directory: {path}")
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"Error deleting {purpose} directory {path}: {e}"))

        self.stdout.write(self.style.SUCCESS("Library wipe complete."))
