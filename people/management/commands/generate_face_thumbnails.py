from django.core.management.base import BaseCommand
from django.db.models import Q
from people.models import Face
from assets.tasks import generate_face_thumbnail


class Command(BaseCommand):
    help = 'Generate face thumbnails for faces that are missing them or not ready'

    def add_arguments(self, parser):
        parser.add_argument(
            '--limit',
            type=int,
            default=200,
            help='Limit the number of faces to process (default: 200)'
        )
        parser.add_argument(
            '--async',
            action='store_true',
            help='Process asynchronously using Celery workers'
        )

    def handle(self, *args, **options):
        qs = (
            Face.objects
            .filter(Q(thumbnail__isnull=True) | Q(thumbnail__is_ready=False))
            .order_by('-quality', '-detection_confidence', '-created_at')
        )
        count = qs.count()
        self.stdout.write(f'Found {count} faces needing thumbnails; processing up to {options["limit"]}...')
        faces = list(qs[: options['limit']])
        processed = 0
        for face in faces:
            try:
                if options['async']:
                    generate_face_thumbnail.delay(str(face.id))
                else:
                    generate_face_thumbnail(str(face.id))
                processed += 1
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'Failed for face {face.id}: {e}'))
        self.stdout.write(self.style.SUCCESS(f'✓ Enqueued/generated thumbnails for {processed} faces'))
