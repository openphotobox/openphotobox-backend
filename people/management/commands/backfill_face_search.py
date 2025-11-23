import numpy as np
from django.core.management.base import BaseCommand
from django.db import transaction

from people.models import Face, FaceSearch
from people.tasks import _l2_normalize


class Command(BaseCommand):
    help = "Backfill the face_search table from existing faces."

    def add_arguments(self, parser):
        parser.add_argument("--batch", type=int, default=1000, help="Batch size for processing.")

    def handle(self, *args, **options):
        batch = options["batch"]
        total = 0
        created = 0
        updated = 0
        qs = Face.objects.all().order_by("created_at").values_list("id", "embedding")
        batch_items = []
        for face_id, emb_bytes in qs.iterator(chunk_size=batch):
            batch_items.append((face_id, emb_bytes))
            if len(batch_items) >= batch:
                c, u = self._process_batch(batch_items)
                created += c
                updated += u
                total += len(batch_items)
                self.stdout.write(f"Processed {total} faces (created={created}, updated={updated})")
                batch_items = []
        if batch_items:
            c, u = self._process_batch(batch_items)
            created += c
            updated += u
            total += len(batch_items)
        self.stdout.write(self.style.SUCCESS(f"Done. Processed {total} faces (created={created}, updated={updated})"))

    def _process_batch(self, batch_items):
        created = 0
        updated = 0
        with transaction.atomic():
            for face_id, emb_bytes in batch_items:
                emb = np.frombuffer(emb_bytes, dtype=np.float32)
                emb = _l2_normalize(emb)
                obj, is_created = FaceSearch.objects.update_or_create(face_id=face_id, defaults={"embedding": emb})
                if is_created:
                    created += 1
                else:
                    updated += 1
        return created, updated
