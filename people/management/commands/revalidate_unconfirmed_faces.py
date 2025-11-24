from django.core.management.base import BaseCommand

from people.tasks import revalidate_unconfirmed_faces


class Command(BaseCommand):
    help = "Revalidate unconfirmed face assignments to find better matches."

    def add_arguments(self, parser):
        parser.add_argument(
            "--person-ids",
            type=str,
            help="Comma-separated list of person UUIDs to limit scope (defaults to all)",
        )
        parser.add_argument("--limit", type=int, default=1000, help="Max faces to process.")
        parser.add_argument(
            "--async", dest="async_", action="store_true", help="Queue via Celery instead of running inline."
        )
        parser.add_argument(
            "--sync", dest="async_", action="store_false", help="Run inline synchronously (default)."
        )
        parser.set_defaults(async_=False)

    def handle(self, *args, **options):
        limit = options["limit"]
        person_ids = None
        if options.get("person_ids"):
            person_ids = [pid.strip() for pid in options["person_ids"].split(",")]

        if options["async_"]:
            task = revalidate_unconfirmed_faces.delay(person_ids=person_ids, limit=limit)
            self.stdout.write(f"Queued revalidate_unconfirmed_faces task {task.id} (limit={limit})")
        else:
            result = revalidate_unconfirmed_faces.run(person_ids=person_ids, limit=limit)
            if result.get("success"):
                self.stdout.write(
                    self.style.SUCCESS(
                        f"✓ Processed {result.get('processed')} faces; "
                        f"reassigned {result.get('reassigned')}, unassigned {result.get('unassigned')}"
                    )
                )
            else:
                self.stdout.write(self.style.ERROR("Revalidation failed"))

