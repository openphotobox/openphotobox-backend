import numpy as np
from django.core.management.base import BaseCommand
from django.db import transaction

from people.models import Face, Person
from people.tasks import _calculate_centroid


class Command(BaseCommand):
    help = "Merge people whose centroid similarity exceeds a threshold."

    def add_arguments(self, parser):
        parser.add_argument("--threshold", type=float, default=0.75, help="Cosine similarity threshold")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        thr = options["threshold"]
        dry = options["dry_run"]
        people = list(Person.objects.exclude(embedding_centroid__isnull=True))
        merged = 0
        visited = set()

        def to_array(x):
            if x is None:
                return None
            a = np.array(x, dtype=np.float32)
            if a.size == 0:
                return None
            return a

        def cos(a, b):
            return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))

        with transaction.atomic():
            for i, p in enumerate(people):
                if p.id in visited:
                    continue
                visited.add(p.id)
                for q in people[i + 1 :]:
                    if q.id in visited:
                        continue
                    pa = to_array(p.embedding_centroid)
                    qa = to_array(q.embedding_centroid)
                    if pa is None or qa is None:
                        continue
                    if pa.shape != qa.shape:
                        continue
                    sim = cos(pa, qa)
                    if sim >= thr:
                        self.stdout.write(f"Merging {q.id} into {p.id} (sim={sim:.3f})")
                        if dry:
                            continue
                        # Reassign faces
                        Face.objects.filter(person=q).update(person=p)
                        # Update centroid/count
                        faces_qs = Face.objects.filter(person=p)
                        if faces_qs.exists():
                            p.embedding_centroid = _calculate_centroid(list(faces_qs))
                            p.embedding_count = faces_qs.count()
                            # Keep non-empty name
                            if not p.display_name and q.display_name:
                                p.display_name = q.display_name
                            p.save()
                        q.delete()
                        visited.add(q.id)
                        merged += 1

        self.stdout.write(self.style.SUCCESS(f"Completed. Merged {merged} pairs."))
