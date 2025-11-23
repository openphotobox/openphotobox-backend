from django.core.management.base import BaseCommand
from django.db import transaction
from people.models import Person, Face, PersonMergeSuggestion
from people.tasks import cluster_faces


class Command(BaseCommand):
    help = 'Reset people: unassign all faces, delete people and merge suggestions, optionally re-cluster.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--recluster',
            action='store_true',
            help='Trigger reclustering after reset'
        )
        parser.add_argument(
            '--async',
            action='store_true',
            help='Run reclustering asynchronously'
        )
        parser.add_argument(
            '--min-faces',
            type=int,
            default=None,
            help='Override min faces per person for recluster'
        )

    def handle(self, *args, **options):
        self.stdout.write('Resetting people and unassigning faces...')
        with transaction.atomic():
            # Unassign all faces
            updated = Face.objects.filter(person__isnull=False).update(person=None)
            self.stdout.write(f'Unassigned {updated} faces')

            # Delete merge suggestions first due to FKs
            deleted_suggestions, _ = PersonMergeSuggestion.objects.all().delete()
            self.stdout.write(f'Deleted {deleted_suggestions} merge suggestions')

            # Delete people
            deleted_people, _ = Person.objects.all().delete()
            self.stdout.write(f'Deleted {deleted_people} people')

        if options['recluster']:
            self.stdout.write('Triggering recluster...')
            try:
                if options['async']:
                    task = cluster_faces.delay(min_faces_per_person=options['min_faces'])
                    self.stdout.write(f'Queued cluster_faces task {task.id}')
                else:
                    result = cluster_faces(min_faces_per_person=options['min_faces'])
                    if result.get('success'):
                        self.stdout.write(self.style.SUCCESS('✓ Reclustering complete'))
                    else:
                        self.stdout.write(self.style.ERROR(f'✗ Reclustering failed: {result.get("error", "Unknown error")}'))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'✗ Error triggering recluster: {e}'))

        self.stdout.write(self.style.SUCCESS('✓ Reset complete'))
