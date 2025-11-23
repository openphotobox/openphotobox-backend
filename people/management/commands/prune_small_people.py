from django.core.management.base import BaseCommand
from django.db.models import Count
from django.db import transaction
from people.models import Person, Face


class Command(BaseCommand):
    help = 'Delete or reset people with fewer than N faces. Unassigns their faces back to unassigned.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--min-faces',
            type=int,
            default=2,
            help='Minimum face count to keep a person (default: 2)'
        )
        parser.add_argument(
            '--min-assets',
            type=int,
            default=2,
            help='Minimum distinct asset count to keep a person (default: 2)'
        )
        parser.add_argument(
            '--skip-custom-names',
            action='store_true',
            help='Skip persons whose display_name is not the auto-generated "Person X" format'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Only report what would be changed without applying modifications'
        )

    def handle(self, *args, **options):
        threshold = options['min_faces']
        min_assets = options['min_assets']
        skip_custom = options['skip_custom_names']
        dry_run = options['dry_run']

        qs = Person.objects.annotate(
            face_count=Count('faces'),
            asset_count=Count('faces__asset', distinct=True),
        )
        small = qs.filter(face_count__lt=threshold) | qs.filter(asset_count__lt=min_assets)

        if skip_custom:
            # Keep only auto-generated names like "Person 12"
            small = small.filter(display_name__regex=r'^Person\s+\d+$')

        total = small.distinct().count()
        self.stdout.write(f'Found {total} person(s) with < {threshold} faces or < {min_assets} assets')

        if dry_run:
            for p in small.distinct()[:50]:
                self.stdout.write(f'- {p.id} {p.display_name} (faces={getattr(p, "face_count", "?")}, assets={getattr(p, "asset_count", "?")})')
            self.stdout.write('Dry run complete; no changes applied.')
            return

        removed = 0
        with transaction.atomic():
            for person in small.distinct():
                # Unassign faces
                count = Face.objects.filter(person=person).update(person=None)
                # Delete person
                person.delete()
                removed += 1
                self.stdout.write(f'Removed {person.display_name} (unassigned {count} face(s))')

        self.stdout.write(self.style.SUCCESS(f'✓ Pruned {removed} person(s)'))


