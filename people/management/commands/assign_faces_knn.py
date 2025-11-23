from django.core.management.base import BaseCommand
from people.tasks import assign_faces_knn


class Command(BaseCommand):
    help = 'Assign unassigned faces using KNN on pgvector.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=500, help='Max faces to process.')
        parser.add_argument('--async', dest='async_', action='store_true', help='Queue via Celery instead of running inline.')
        parser.add_argument('--sync', dest='async_', action='store_false', help='Run inline synchronously (default).')
        parser.set_defaults(async_=False)

    def handle(self, *args, **options):
        limit = options['limit']
        if options['async_']:
            task = assign_faces_knn.delay(limit=limit)
            self.stdout.write(f'Queued assign_faces_knn task {task.id} (limit={limit})')
        else:
            result = assign_faces_knn.run(limit=limit)
            if result.get('success'):
                self.stdout.write(self.style.SUCCESS(
                    f"✓ Processed {result.get('processed')} faces; assigned {result.get('assigned')}, created {result.get('persons_created')}"
                ))
            else:
                self.stdout.write(self.style.ERROR('Assignment failed'))
