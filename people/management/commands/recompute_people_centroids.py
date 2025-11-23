from django.core.management.base import BaseCommand
from django.db import transaction

from people.models import Face, Person
from people.tasks import _calculate_centroid


class Command(BaseCommand):
    help = "Recompute centroid embeddings and headshots for all persons."

    def handle(self, *args, **options):
        updated = 0
        with transaction.atomic():
            for person in Person.objects.all():
                faces_qs = Face.objects.filter(person=person)
                if faces_qs.exists():
                    centroid = _calculate_centroid(list(faces_qs))
                    best_face = faces_qs.order_by("-quality", "-detection_confidence", "-created_at").first()
                    person.embedding_centroid = centroid
                    person.embedding_count = faces_qs.count()
                    person.headshot_face = best_face
                    person.save(update_fields=["embedding_centroid", "embedding_count", "headshot_face", "updated_at"])
                    updated += 1
        self.stdout.write(self.style.SUCCESS(f"Updated {updated} persons"))
