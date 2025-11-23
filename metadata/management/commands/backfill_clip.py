from django.core.management.base import BaseCommand

from assets.models import Asset
from metadata.models import ClipEmbedding
from metadata.tasks import generate_clip_embedding


class Command(BaseCommand):
    help = "Backfill or recompute CLIP embeddings for assets."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=500, help="Max assets to process")
        parser.add_argument("--async", dest="async_", action="store_true", help="Queue tasks asynchronously")
        parser.add_argument("--force", action="store_true", help="Recompute even if an embedding already exists")
        parser.add_argument(
            "--only-zero",
            dest="only_zero",
            action="store_true",
            help="Recompute only assets whose embedding is all zeros",
        )

    def handle(self, *args, **options):
        limit = int(options["limit"])
        async_ = bool(options["async_"])
        force = bool(options["force"])
        only_zero = bool(options["only_zero"])

        assets_qs = Asset.objects.all().order_by("-created_at")

        if only_zero:
            # Target assets where a ClipEmbedding exists but is likely all zeros
            target_ids = []
            # Iterate lazily to avoid loading all embeddings at once
            for ce in ClipEmbedding.objects.select_related("asset").only("id", "asset_id", "embedding")[
                : max(limit, 10000)
            ]:
                try:
                    vec = ce.embedding
                    # Treat as zero if sum of abs values ~ 0
                    if not vec or (isinstance(vec, (list, tuple)) and sum(abs(float(x)) for x in vec) == 0.0):
                        target_ids.append(ce.asset_id)
                        if len(target_ids) >= limit:
                            break
                except Exception:
                    # If we cannot parse, skip
                    continue
            assets = Asset.objects.filter(id__in=target_ids)
        elif force:
            # Recompute all, regardless of existing embeddings
            assets = assets_qs[:limit]
        else:
            # Default: only assets missing embeddings
            assets = Asset.objects.filter(clip_embedding__isnull=True).order_by("-created_at")[:limit]

        count = 0
        for asset in assets:
            if async_:
                generate_clip_embedding.delay(str(asset.id))
            else:
                generate_clip_embedding.run(str(asset.id))
            count += 1

        self.stdout.write(self.style.SUCCESS(f"Queued/generated CLIP embeddings for {count} assets"))
