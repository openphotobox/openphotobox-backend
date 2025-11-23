from django.core.management.base import BaseCommand
from people.tasks import cluster_faces


class Command(BaseCommand):
    help = 'Cluster faces and create Person records'

    def add_arguments(self, parser):
        parser.add_argument(
            '--min-faces',
            type=int,
            default=3,
            help='Minimum number of faces required to create a person (default: 3)'
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Force clustering even if there are few unassigned faces'
        )
        parser.add_argument(
            '--async',
            action='store_true',
            help='Process asynchronously using Celery workers'
        )

    def handle(self, *args, **options):
        self.stdout.write(f'Clustering faces (min_faces={options["min_faces"]}, force={options["force"]})...')
        
        if options['async']:
            # Queue the task for async processing
            try:
                task = cluster_faces.delay(force=options['force'], min_faces_per_person=options['min_faces'])
                self.stdout.write(f'Queued clustering task {task.id}')
            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(f'Failed to queue async task: {str(e)}')
                )
        else:
            # Process synchronously
            try:
                result = cluster_faces(force=options['force'], min_faces_per_person=options['min_faces'])
                if result['success']:
                    self.stdout.write(
                        self.style.SUCCESS(f'✓ Clustering complete!')
                    )
                    self.stdout.write(f'  Persons created: {result["persons_created"]}')
                    self.stdout.write(f'  Faces assigned: {result["faces_assigned"]}')
                    self.stdout.write(f'  Faces processed: {result["faces_processed"]}')
                    if result.get('unassigned_remaining', 0) > 0:
                        self.stdout.write(f'  Unassigned remaining: {result["unassigned_remaining"]}')
                    if result.get('message'):
                        self.stdout.write(f'  Note: {result["message"]}')
                else:
                    self.stdout.write(
                        self.style.ERROR(f'✗ Clustering failed: {result.get("error", "Unknown error")}')
                    )
            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(f'✗ Error: {str(e)}')
                )

