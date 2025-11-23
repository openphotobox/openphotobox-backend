from django.core.management.base import BaseCommand
from django.db.models import Count
from django.db import transaction
from people.models import Face


class Command(BaseCommand):
    help = 'Find and remove duplicate Face records per asset with identical bbox and embedding size.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Only report duplicates')
        parser.add_argument('--limit', type=int, default=10000, help='Process up to N faces (for safety)')

    def handle(self, *args, **options):
        dry = options['dry_run']
        limit = options['limit']

        # Heuristic: consider duplicates when same asset and very similar bbox (rounded to 3 decimals)
        def key_for(face):
            return (
                str(face.asset_id),
                round(face.x, 3),
                round(face.y, 3),
                round(face.w, 3),
                round(face.h, 3),
            )

        faces = list(Face.objects.all().order_by('-created_at')[:limit])
        seen = {}
        dupes = []
        for f in faces:
            k = key_for(f)
            if k in seen:
                dupes.append(f.id)
            else:
                seen[k] = f.id

        self.stdout.write(f'Found {len(dupes)} potential duplicate faces (within first {len(faces)}).')
        if dry:
            return
        removed = 0
        with transaction.atomic():
            for fid in dupes:
                Face.objects.filter(id=fid).delete()
                removed += 1
        self.stdout.write(self.style.SUCCESS(f'Removed {removed} duplicates.'))


