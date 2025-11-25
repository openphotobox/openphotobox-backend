import numpy as np
from django.db import connection
from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from .services import embed_text


class ClipSearchView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        """Search assets by CLIP text embedding; returns ranked asset IDs with similarity.

        Query params:
          - q (text), limit (default 50)
          - people (comma-separated UUIDs) or person (single UUID)
          - people_mode: 'all' | 'any' (default 'all')
          - albums (comma-separated UUIDs) or album (single UUID)
        """
        query = request.query_params.get("q", "")
        if not query:
            return Response({"results": []})

        try:
            limit = int(request.query_params.get("limit", 50))
        except Exception:
            limit = 50

        text_emb = embed_text(query)
        emb_list = np.asarray(text_emb, dtype=np.float32).astype(float).tolist()
        emb_str = "[" + ",".join(str(x) for x in emb_list) + "]"

        # Filter to only accessible assets
        from albums.permissions import get_accessible_assets

        accessible_assets = get_accessible_assets(request.user)
        allowed_asset_ids = set(accessible_assets.values_list("id", flat=True))

        # Optional additional filtering by people and albums
        try:
            # Build allowed set by people filters
            people_param = request.query_params.get("people") or request.query_params.get("person_ids")
            single_person = request.query_params.get("person")
            people_mode = (request.query_params.get("people_mode") or "all").lower()
            people_ids = []
            if people_param:
                people_ids = [p.strip() for p in people_param.split(",") if p and p.strip()]
            if single_person:
                people_ids = [single_person]

            if people_ids:
                from people.models import Face

                if people_mode not in ("all", "any"):
                    people_mode = "all"
                if people_mode == "all":
                    # Intersect assets that contain each specified person
                    intersect_ids = None
                    for pid in people_ids:
                        ids_for_pid = set(Face.objects.filter(person_id=pid).values_list("asset_id", flat=True))
                        intersect_ids = ids_for_pid if intersect_ids is None else (intersect_ids & ids_for_pid)
                        if not intersect_ids:
                            break
                    allowed_asset_ids = intersect_ids or set()
                else:
                    allowed_asset_ids = set(
                        Face.objects.filter(person_id__in=people_ids).values_list("asset_id", flat=True).distinct()
                    )

            # Apply album filters (any-of)
            albums_param = request.query_params.get("albums") or request.query_params.get("album_ids")
            single_album = request.query_params.get("album")
            album_ids = []
            if albums_param:
                album_ids = [a.strip() for a in albums_param.split(",") if a and a.strip()]
            if single_album:
                album_ids = [single_album]
            if album_ids:
                from assets.models import AlbumAsset

                album_asset_ids = set(
                    AlbumAsset.objects.filter(album_id__in=album_ids).values_list("asset_id", flat=True).distinct()
                )
                if allowed_asset_ids is None:
                    allowed_asset_ids = album_asset_ids
                else:
                    allowed_asset_ids = allowed_asset_ids & album_asset_ids
        except Exception:
            # On any issues with optional filters, fall back to no restriction
            allowed_asset_ids = None

        # Strategy: query more rows if filters are present, then filter client-side
        multiplier = 1
        rows_filtered = []
        while True:
            effective_limit = limit * max(multiplier, 1)
            # Cap to a reasonable upper bound to avoid heavy queries
            effective_limit = min(effective_limit, max(limit * 10, 500))

            # Cosine similarity via pgvector: 1 - (<=>) on normalized vectors
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT ce.asset_id,
                           1 - (ce.embedding <=> %s::vector) AS cosine_sim,
                           (ce.embedding <=> %s::vector) AS distance
                    FROM clip_embeddings AS ce
                    ORDER BY distance ASC
                    LIMIT %s
                    """,
                    [emb_str, emb_str, effective_limit],
                )
                rows = cursor.fetchall()

            # Post-filter if needed, preserving order
            if allowed_asset_ids is not None:
                rows_filtered = [r for r in rows if str(r[0]) in {str(x) for x in allowed_asset_ids}]
            else:
                rows_filtered = rows

            if len(rows_filtered) >= limit or effective_limit >= max(limit * 10, 500):
                break
            multiplier += 2

        results = [
            {"asset_id": str(row[0]), "similarity": float(row[1]), "distance": float(row[2])}
            for row in rows_filtered[:limit]
        ]
        return Response({"results": results})


class ClipNeighborsView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        """Return nearest neighbor assets by CLIP embedding for a given asset.

        Query params:
          - asset_id (required)
          - k (default 5): number of neighbors per asset
          - max_distance (optional): filter results to distances <= max_distance
        """
        asset_id = request.query_params.get("asset_id")
        if not asset_id:
            return Response({"results": [], "error": "asset_id is required"})
        try:
            k = int(request.query_params.get("k", 5))
        except Exception:
            k = 5
        try:
            max_distance = (
                float(request.query_params.get("max_distance"))
                if request.query_params.get("max_distance") is not None
                else None
            )
        except Exception:
            max_distance = None

        # Verify the asset is accessible to the user
        from albums.permissions import can_view_asset
        from assets.models import Asset

        try:
            asset = Asset.objects.get(id=asset_id)
            if not can_view_asset(request.user, asset):
                return Response({"error": "Asset not found or not accessible"}, status=status.HTTP_404_NOT_FOUND)
        except Asset.DoesNotExist:
            return Response({"error": "Asset not found"}, status=status.HTTP_404_NOT_FOUND)

        # Ensure the asset exists in clip_embeddings
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM clip_embeddings WHERE asset_id = %s LIMIT 1", [asset_id])
            row = cursor.fetchone()
            if not row:
                return Response({"results": []})

        # Get accessible asset IDs for filtering
        from albums.permissions import get_accessible_assets

        accessible_assets = get_accessible_assets(request.user)
        accessible_asset_ids = set(str(aid) for aid in accessible_assets.values_list("id", flat=True))

        # Use pgvector index to get nearest neighbors via lateral join
        # Fetch more than k to account for filtering
        fetch_limit = min(k * 5, 100)  # Fetch extra, but cap at reasonable limit

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT nn.asset_id AS neighbor_asset_id,
                       (ce.embedding <=> nn.embedding) AS distance,
                       1 - (ce.embedding <=> nn.embedding) AS similarity
                FROM clip_embeddings AS ce
                JOIN LATERAL (
                  SELECT asset_id, embedding
                  FROM clip_embeddings
                  WHERE asset_id <> ce.asset_id
                  ORDER BY embedding <=> ce.embedding ASC
                  LIMIT %s
                ) AS nn ON TRUE
                WHERE ce.asset_id = %s
                ORDER BY distance ASC
                """,
                [fetch_limit, asset_id],
            )
            rows = cursor.fetchall()

        # Filter to accessible assets only
        results = []
        for neighbor_asset_id, distance, similarity in rows:
            if max_distance is not None and float(distance) > max_distance:
                continue
            # Only include accessible assets
            if str(neighbor_asset_id) not in accessible_asset_ids:
                continue
            results.append(
                {
                    "asset_id": str(neighbor_asset_id),
                    "distance": float(distance),
                    "similarity": float(similarity),
                }
            )
            # Stop once we have k results
            if len(results) >= k:
                break

        return Response({"results": results})


# Create your views here.
