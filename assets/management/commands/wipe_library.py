from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


class Command(BaseCommand):
    help = "Delete all library data (photos/assets, albums, faces, thumbnails, metadata). Optional flags to include sharing and people. Intended for wiping a test database."

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
        include_sharing = bool(options.get("include_sharing"))
        include_people = bool(options.get("include_people"))
        include_notifications = bool(options.get("include_notifications"))
        dry_run = bool(options.get("dry_run"))
        assume_yes = bool(options.get("yes"))

        # Imports here to avoid app loading if not needed elsewhere
        from assets.models import Asset, Album, AlbumAsset, AssetThumbnail, FaceThumbnail, UploadBatch
        from people.models import Face, FaceSearch, Person
        from metadata.models import AssetMetadata, ClipEmbedding, XmpSidecar, AssetKeyword, KeywordTag

        # Optional apps
        SharingModels = None
        NotificationsModel = None
        if include_sharing:
            try:
                from sharing.models import (
                    AccessGrant,
                    UserAsset,
                    SharingLink,
                    UserAssetRebuildLog,
                )  # type: ignore

                SharingModels = {
                    "AccessGrant": AccessGrant,
                    "UserAsset": UserAsset,
                    "SharingLink": SharingLink,
                    "UserAssetRebuildLog": UserAssetRebuildLog,
                }
            except Exception:
                SharingModels = {}
        if include_notifications:
            try:
                from notifications.models import Notification  # type: ignore

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
            "UploadBatch": UploadBatch.objects.count(),
        }
        if include_people:
            counts["Person"] = Person.objects.count()
        if SharingModels:
            for name, Model in SharingModels.items():
                counts[name] = Model.objects.count()
        if NotificationsModel is not None:
            counts["Notification"] = NotificationsModel.objects.count()

        # Show plan
        self.stdout.write("This will delete the following data (row counts):")
        for name, c in counts.items():
            self.stdout.write(f"- {name}: {c}")

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

            # Upload batches (not strictly tied to assets via CASCADE for status tracking)
            deleted, _ = UploadBatch.objects.all().delete()
            self.stdout.write(f"Deleted {deleted} UploadBatch")

            # Optional: people (after faces are gone)
            if include_people:
                deleted, _ = Person.objects.all().delete()
                self.stdout.write(f"Deleted {deleted} Person")

            # Optional: notifications
            if NotificationsModel is not None:
                deleted, _ = NotificationsModel.objects.all().delete()
                self.stdout.write(f"Deleted {deleted} Notification")

        self.stdout.write(self.style.SUCCESS("Library wipe complete."))





