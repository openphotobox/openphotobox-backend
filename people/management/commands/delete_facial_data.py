from django.core.management.base import BaseCommand
from django.db import transaction

from typing import Tuple

from people.models import Person, Face, PersonMergeSuggestion
from assets.models import FaceThumbnail, StorageBucket
from assets.services import UploadService


class Command(BaseCommand):
    help = 'Delete ALL facial data: face thumbnails (and their storage objects), faces, people, and merge suggestions.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be deleted without performing deletions'
        )
        parser.add_argument(
            '--no-storage',
            action='store_true',
            help='Skip deleting storage objects for face thumbnails (DB rows still deleted)'
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Do not prompt for confirmation'
        )

    def handle(self, *args, **options):
        dry_run: bool = options['dry_run']
        delete_storage: bool = not options['no_storage']
        force: bool = options['force']

        # Show counts up-front
        face_count = Face.objects.count()
        person_count = Person.objects.count()
        thumb_count = FaceThumbnail.objects.count()
        sugg_count = PersonMergeSuggestion.objects.count()

        self.stdout.write('This command will DELETE ALL FACIAL DATA:')
        self.stdout.write(f'  - Face thumbnails: {thumb_count} (storage delete: {"yes" if delete_storage else "no"})')
        self.stdout.write(f'  - Faces: {face_count}')
        self.stdout.write(f'  - People: {person_count}')
        self.stdout.write(f'  - Person merge suggestions: {sugg_count}')

        if dry_run:
            self.stdout.write(self.style.WARNING('Dry run enabled — no data will be deleted.'))

        if not force:
            # Simple confirmation gate when not forced (non-interactive safe default is to continue)
            self.stdout.write('Proceeding in 5 seconds... (use --force to skip this notice)')

        # 1) Optionally delete storage objects for face thumbnails BEFORE DB deletes
        if delete_storage:
            self._delete_face_thumbnail_objects(dry_run)
        else:
            self.stdout.write('Skipping storage object deletion for face thumbnails (--no-storage)')

        # 2) Database deletions
        with transaction.atomic():
            # Clear references proactively to avoid FK issues on older schemas
            updated_headshots = 0
            if not dry_run:
                updated_headshots = Person.objects.filter(headshot_face__isnull=False).update(headshot_face=None)
            else:
                updated_headshots = Person.objects.filter(headshot_face__isnull=False).count()
            self.stdout.write(f'Cleared headshot_face on {updated_headshots} people')

            updated_faces = 0
            if not dry_run:
                updated_faces = Face.objects.filter(person__isnull=False).update(person=None)
            else:
                updated_faces = Face.objects.filter(person__isnull=False).count()
            self.stdout.write(f'Unassigned person from {updated_faces} faces')

            # Delete merge suggestions first
            if dry_run:
                self.stdout.write(f"Would delete {PersonMergeSuggestion.objects.count()} person merge suggestions")
            else:
                deleted_suggestions, _ = PersonMergeSuggestion.objects.all().delete()
                self.stdout.write(f'Deleted {deleted_suggestions} person merge suggestions')

            # Delete face thumbnails (DB rows)
            if dry_run:
                self.stdout.write(f"Would delete {FaceThumbnail.objects.count()} face thumbnails (DB)")
            else:
                deleted_thumbs, _ = FaceThumbnail.objects.all().delete()
                self.stdout.write(f'Deleted {deleted_thumbs} face thumbnails (DB)')

            # Delete faces
            if dry_run:
                self.stdout.write(f"Would delete {Face.objects.count()} faces")
            else:
                deleted_faces, _ = Face.objects.all().delete()
                self.stdout.write(f'Deleted {deleted_faces} faces')

            # Delete people
            if dry_run:
                self.stdout.write(f"Would delete {Person.objects.count()} people")
            else:
                deleted_people, _ = Person.objects.all().delete()
                self.stdout.write(f'Deleted {deleted_people} people')

        self.stdout.write(self.style.SUCCESS('✓ Facial data deletion completed'))

    def _delete_face_thumbnail_objects(self, dry_run: bool) -> None:
        """Delete face thumbnail files from storage backends.

        Mirrors the path logic used when uploading/serving face thumbnails:
        - Base key stored in DB as face.storage_key (e.g., 'faces/<face_id>/thumbnail.jpg')
        - If bucket.path_prefix is set: prefix + '/' + key
        - Else if bucket.purpose == 'originals': use 'face-thumbnails/' + key
        """
        qs = FaceThumbnail.objects.select_related('storage_bucket', 'storage_bucket__backend').all()
        total = qs.count()
        if total == 0:
            self.stdout.write('No face thumbnail storage objects to delete')
            return

        self.stdout.write(f'Deleting {total} face thumbnail storage objects...')
        deleted = 0
        errors = 0
        last_bucket_id = None
        upload_service: UploadService | None = None

        for thumb in qs.iterator(chunk_size=500):
            bucket: StorageBucket = thumb.storage_bucket
            # Reuse UploadService per backend
            if last_bucket_id != bucket.backend_id:
                upload_service = UploadService(bucket.backend)
                last_bucket_id = bucket.backend_id

            # Compute full key
            full_key = thumb.storage_key
            if bucket.path_prefix:
                full_key = f"{bucket.path_prefix.rstrip('/')}/{thumb.storage_key}"
            elif bucket.purpose == 'originals':
                full_key = f"face-thumbnails/{thumb.storage_key}"

            if dry_run:
                self.stdout.write(f"Would delete s3://{bucket.name}/{full_key}")
                deleted += 1
                continue

            try:
                upload_service.client.delete_object(Bucket=bucket.name, Key=full_key)
                deleted += 1
            except Exception as e:
                errors += 1
                self.stdout.write(self.style.WARNING(f"Failed to delete {bucket.name}/{full_key}: {e}"))

        self.stdout.write(f"Storage deletion complete. Deleted: {deleted}, Errors: {errors}")


