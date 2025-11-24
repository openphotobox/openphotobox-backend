"""
Views for the people app.
"""

import numpy as np
from django.conf import settings
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from assets.models import Asset
from assets.serializers import AssetGallerySerializer
from openphotobox_backend.pagination import AssetCursorPagination

from .models import Face, Person, PersonMergeSuggestion
from .serializers import (
    FaceAssignmentSerializer,
    FaceConfirmationSerializer,
    FaceSerializer,
    FaceUnassignmentSerializer,
    ManualFaceCreateSerializer,
    PersonMergeRequestSerializer,
    PersonMergeSuggestionSerializer,
    PersonSerializer,
)
from .tasks import _calculate_centroid, create_fresh_face_app


class PersonViewSet(viewsets.ModelViewSet):
    """ViewSet for managing people and face recognition."""

    queryset = Person.objects.all()
    serializer_class = PersonSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        # Annotate with face_count for sorting
        queryset = super().get_queryset().annotate(face_count=Count("faces"))
        search = self.request.query_params.get("search")
        if search:
            queryset = queryset.filter(Q(display_name__icontains=search) | Q(aka__icontains=search))
        return queryset.order_by("-face_count", "display_name")

    @action(detail=True, methods=["post"])
    def merge(self, request, pk=None):
        """Merge people.
        Supports two forms:
        - Detail merge (default target=pk): { source_person_ids: [...], delete_source_persons?: bool }
        - Single-source merge (default source=pk): { target_person_id: uuid, delete_source_persons?: bool }
        """
        body = request.data or {}
        source_ids = body.get("source_person_ids")
        target_in_body = body.get("target_person_id")
        delete_sources = body.get("delete_source_persons", True)
        payload = {}
        if source_ids:
            # Treat pk as the target person
            payload = {
                "target_person_id": pk,
                "source_person_ids": source_ids,
                "delete_source_persons": delete_sources,
            }
        elif target_in_body:
            # Treat pk as a single source person
            payload = {
                "target_person_id": target_in_body,
                "source_person_ids": [pk],
                "delete_source_persons": delete_sources,
            }
        else:
            return Response(
                {"detail": "Provide source_person_ids or target_person_id"}, status=status.HTTP_400_BAD_REQUEST
            )
        serializer = PersonMergeRequestSerializer(data=payload)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        target_id = data["target_person_id"]
        source_ids = data["source_person_ids"]
        delete_sources = data["delete_source_persons"]

        with transaction.atomic():
            try:
                target = Person.objects.get(id=target_id)
            except Person.DoesNotExist:
                return Response({"detail": "Target person not found"}, status=status.HTTP_404_NOT_FOUND)

            # Reassign faces from each source to target
            total_reassigned = 0

            # Preserve target's custom name if it has one; otherwise adopt a non-generic source name
            def is_generic(name: str):
                return (
                    not name
                    or name.strip().lower() in {"person", "unknown"}
                    or name.strip().lower().startswith("person ")
                )

            for sid in source_ids:
                if str(sid) == str(target.id):
                    continue
                try:
                    source = Person.objects.get(id=sid)
                except Person.DoesNotExist:
                    continue
                reassigned = Face.objects.filter(person=source).update(person=target)
                total_reassigned += reassigned
                # If target name is generic and source has a better name, adopt it
                if is_generic(target.display_name) and not is_generic(source.display_name):
                    target.display_name = source.display_name
                # Merge aliases
                try:
                    source_aliases = [a for a in (source.aka or []) if a]
                    target_aliases = set(target.aka or [])
                    for alias in source_aliases:
                        target_aliases.add(alias)
                    target.aka = list(target_aliases)
                except Exception:
                    pass

                # If requested, delete the source person after reassignment
                if delete_sources:
                    source.delete()

            # Update target centroid and counts
            faces_qs = Face.objects.filter(person=target)
            if faces_qs.exists():
                # Pick best headshot by quality
                best_face = faces_qs.order_by("-quality", "-detection_confidence", "-created_at").first()
                target.headshot_face = best_face
                # Recompute centroid
                centroid = _calculate_centroid(list(faces_qs))
                target.embedding_centroid = centroid
                target.embedding_count = faces_qs.count()
                target.save()

        return Response({"message": "Merged successfully", "faces_reassigned": total_reassigned})

    @action(detail=False, methods=["post"], url_path="bulk-merge")
    def bulk_merge(self, request):
        """Bulk merge: specify target_person_id and source_person_ids."""
        serializer = PersonMergeRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        # Delegate to detail merge for actual logic
        request._full_data = {
            "source_person_ids": data["source_person_ids"],
            "delete_source_persons": data["delete_source_persons"],
        }
        return self.merge(request, pk=str(data["target_person_id"]))

    @action(detail=True, methods=["get"], url_path="candidate-faces")
    def candidate_faces(self, request, pk=None):
        """Get unconfirmed (candidate) faces for a person.
        These are faces that were auto-assigned but haven't been confirmed by a user.
        """
        try:
            person = Person.objects.get(id=pk)
        except Person.DoesNotExist:
            return Response({"detail": "Person not found"}, status=status.HTTP_404_NOT_FOUND)

        # Get unconfirmed faces for this person
        candidate_faces = (
            Face.objects.filter(person=person, confirmed=False)
            .select_related("asset")
            .order_by("-quality", "-detection_confidence", "-created_at")
        )

        # Paginate if needed
        page = self.paginate_queryset(candidate_faces)
        if page is not None:
            serializer = FaceSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = FaceSerializer(candidate_faces, many=True)
        return Response(serializer.data)


class FaceViewSet(viewsets.ModelViewSet):
    """ViewSet for managing face detection results."""

    queryset = Face.objects.select_related("person", "asset").all()
    serializer_class = FaceSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()
        person_id = self.request.query_params.get("person")
        if person_id:
            queryset = queryset.filter(person_id=person_id)
        return queryset.order_by("-created_at")

    @action(detail=False, methods=["post"], url_path="assign")
    def assign(self, request):
        """Assign one or more faces to a person.
        Body: { face_ids: [uuid,...], person_id: uuid }
        """
        serializer = FaceAssignmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        face_ids = data["face_ids"]
        person_id = data["person_id"]

        try:
            person = Person.objects.get(id=person_id)
        except Person.DoesNotExist:
            return Response({"detail": "Person not found"}, status=status.HTTP_404_NOT_FOUND)

        with transaction.atomic():
            # Assign faces and mark as confirmed (manual assignment)
            updated = Face.objects.filter(id__in=face_ids).update(
                person=person, confirmed=True, confirmed_by=request.user, confirmed_at=timezone.now()
            )
            # Recompute centroid and counts if any faces assigned
            faces_qs = Face.objects.filter(person=person)
            if faces_qs.exists():
                centroid = _calculate_centroid(list(faces_qs))
                person.embedding_centroid = centroid
                person.embedding_count = faces_qs.count()
                # Update headshot to best quality if none set
                best_face = faces_qs.order_by("-quality", "-detection_confidence", "-created_at").first()
                if best_face:
                    person.headshot_face = best_face
                person.save()

        return Response({"updated": updated}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"], url_path="unassigned-assets")
    def unassigned_assets(self, request):
        """List assets that have at least one unassigned face (person is null).
        Cursor-paginated and uses the asset gallery serializer for lightweight payload.
        Query params: cursor, limit
        """
        # Find asset ids that have any face with person null
        asset_ids = Face.objects.filter(person__isnull=True).values_list("asset_id", flat=True).distinct()
        qs = Asset.objects.filter(id__in=asset_ids).prefetch_related("thumbnails").order_by("-taken_at", "-created_at")

        paginator = AssetCursorPagination()
        page = paginator.paginate_queryset(qs, request)
        serializer = AssetGallerySerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    @action(detail=False, methods=["post"], url_path="unassign")
    def unassign(self, request):
        """Unassign one or more faces from any person (set person to null).
        Body: { face_ids: [uuid,...] }
        """
        serializer = FaceUnassignmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        face_ids = serializer.validated_data["face_ids"]

        with transaction.atomic():
            # Track affected persons to recompute their stats
            affected_person_ids = set(
                Face.objects.filter(id__in=face_ids, person__isnull=False).values_list("person_id", flat=True)
            )
            # Clear person links and reset confirmation (manual unassignment)
            updated = Face.objects.filter(id__in=face_ids).update(
                person=None, confirmed=False, confirmed_by=None, confirmed_at=None
            )

            # Recompute centroid, counts and headshot for affected persons
            for pid in affected_person_ids:
                try:
                    person = Person.objects.get(id=pid)
                except Person.DoesNotExist:
                    continue
                faces_qs = Face.objects.filter(person=person)
                if faces_qs.exists():
                    centroid = _calculate_centroid(list(faces_qs))
                    person.embedding_centroid = centroid
                    person.embedding_count = faces_qs.count()
                    best_face = faces_qs.order_by("-quality", "-detection_confidence", "-created_at").first()
                    person.headshot_face = best_face
                else:
                    person.embedding_centroid = None
                    person.embedding_count = 0
                    person.headshot_face = None
                person.save()

        return Response({"updated": updated}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["post"], url_path="bulk-confirm")
    def bulk_confirm(self, request):
        """Confirm one or more faces (mark as confirmed by user).
        Body: { face_ids: [uuid,...] }
        """
        serializer = FaceConfirmationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        face_ids = serializer.validated_data["face_ids"]

        with transaction.atomic():
            # Mark faces as confirmed by current user
            updated = Face.objects.filter(id__in=face_ids).update(
                confirmed=True, confirmed_by=request.user, confirmed_at=timezone.now()
            )

            # Trigger revalidation after confirming faces to improve other assignments
            try:
                from .tasks import revalidate_unconfirmed_faces

                # Schedule with small delay to batch multiple confirmations
                revalidate_unconfirmed_faces.apply_async(countdown=30)
            except Exception:
                pass  # Non-critical, can be manually triggered

        return Response({"updated": updated}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["post"], url_path="manual-create")
    def manual_create(self, request):
        """Create a manual face selection on an asset and enqueue embedding + thumbnail.
        Body: { asset_id: uuid, x:0..1, y:0..1, w:0..1, h:0..1, person_id?: uuid }
        """
        serializer = ManualFaceCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        asset_id = data["asset_id"]
        x = float(data["x"])
        y = float(data["y"])
        w = float(data["w"])
        h = float(data["h"])
        person_id = data.get("person_id")

        if w <= 0 or h <= 0:
            return Response({"detail": "Width and height must be > 0"}, status=status.HTTP_400_BAD_REQUEST)
        if x + w > 1.0 or y + h > 1.0:
            return Response({"detail": "Box exceeds image bounds"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            asset = Asset.objects.get(id=asset_id)
        except Asset.DoesNotExist:
            return Response({"detail": "Asset not found"}, status=status.HTTP_404_NOT_FOUND)

        # Create face with placeholder embedding and quality; real embedding computed async
        # Mark as confirmed if assigned to a person (manual assignment)
        face = Face.objects.create(
            asset=asset,
            person_id=person_id,
            x=x,
            y=y,
            w=w,
            h=h,
            embedding=b"",  # filled in by async job
            quality=0.0,
            detection_model="manual",
            detection_confidence=1.0,
            confirmed=bool(person_id),
            confirmed_by=request.user if person_id else None,
            confirmed_at=timezone.now() if person_id else None,
        )

        # Compute embedding synchronously so assignment UI has similarity immediately
        try:
            import cv2 as _cv2
            import numpy as _np
            import requests

            resp = requests.get(asset.storage_url, timeout=15)
            resp.raise_for_status()
            img_array = _np.frombuffer(resp.content, dtype=_np.uint8)
            image = _cv2.imdecode(img_array, _cv2.IMREAD_COLOR)
            if image is not None:
                H, W = image.shape[:2]
                x1 = max(0, int(x * W))
                y1 = max(0, int(y * H))
                x2 = min(W, int((x + w) * W))
                y2 = min(H, int((y + h) * H))
                if x2 > x1 and y2 > y1:
                    crop = image[y1:y2, x1:x2]
                    app = create_fresh_face_app()
                    dets = app.get(crop)
                    if dets:
                        det = max(dets, key=lambda d: (d.bbox[2] - d.bbox[0]) * (d.bbox[3] - d.bbox[1]))
                        emb = det.embedding.astype(_np.float32)
                        det_score = float(getattr(det, "det_score", 1.0))
                    else:
                        emb = _np.zeros((512,), dtype=_np.float32)
                        det_score = 0.5
                    # Quality heuristic
                    quality = det_score * min(w * h * 10, 1.0)
                    face.embedding = emb.tobytes()
                    face.quality = quality
                    face.detection_confidence = det_score
                    face.save(update_fields=["embedding", "quality", "detection_confidence", "updated_at"])
        except Exception:
            # leave embedding empty if anything fails; UI will still work without similarity
            pass
        try:
            from assets.tasks import generate_face_thumbnail

            generate_face_thumbnail.delay(str(face.id))
        except Exception:
            pass

        return Response(FaceSerializer(face).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"], url_path="candidates")
    def candidates(self, request, pk=None):
        """Return candidate persons for this face, ranked by similarity.
        Query params: limit (default 50)
        """

        def _l2_normalize(vec: np.ndarray) -> np.ndarray:
            norm = float(np.linalg.norm(vec))
            return (vec / norm).astype(np.float32) if norm > 0.0 else vec.astype(np.float32)

        try:
            face = Face.objects.select_related("asset").get(id=pk)
        except Face.DoesNotExist:
            return Response({"detail": "Face not found"}, status=status.HTTP_404_NOT_FOUND)

        try:
            limit = int(request.query_params.get("limit", 50))
        except Exception:
            limit = 50

        cfg = getattr(settings, "OPENPHOTOBOX", {})
        max_prototypes = int(cfg.get("FACE_ASSIGNMENT_MAX_PROTOTYPES_PER_PERSON", 3))
        # Handle empty or missing embeddings; for manual faces, try a quick on-the-fly embedding
        raw = np.frombuffer(face.embedding or b"", dtype=np.float32)
        face_emb = _l2_normalize(raw)
        if face_emb.size == 0 and getattr(face, "detection_model", "") == "manual":
            try:
                # Download image
                import cv2 as _cv2
                import numpy as _np
                import requests

                resp = requests.get(face.asset.storage_url, timeout=15)
                resp.raise_for_status()
                img_array = _np.frombuffer(resp.content, dtype=_np.uint8)
                image = _cv2.imdecode(img_array, _cv2.IMREAD_COLOR)
                if image is not None:
                    H, W = image.shape[:2]
                    x1 = max(0, int(face.x * W))
                    y1 = max(0, int(face.y * H))
                    x2 = min(W, int((face.x + face.w) * W))
                    y2 = min(H, int((face.y + face.h) * H))
                    if x2 > x1 and y2 > y1:
                        crop = image[y1:y2, x1:x2]
                        app = create_fresh_face_app()
                        dets = app.get(crop)
                        if dets:
                            det = max(dets, key=lambda d: (d.bbox[2] - d.bbox[0]) * (d.bbox[3] - d.bbox[1]))
                            emb = det.embedding.astype(_np.float32)
                            face_emb = _l2_normalize(emb)
            except Exception:
                # If any error, leave face_emb empty and continue without similarity
                pass

        # Build scores for each person based on max of centroid similarity and prototype similarities
        # Include all people so you can assign to an empty person if desired
        persons_qs = Person.objects.all().annotate(face_count=Count("faces"))
        candidates = []
        for person in persons_qs:
            best_sim = -1.0

            # Compare against up to N prototypes (best quality faces) for this person
            proto_qs = Face.objects.filter(person=person).order_by("-quality", "-detection_confidence", "-created_at")
            if max_prototypes > 0:
                proto_qs = proto_qs[:max_prototypes]
            # Only compute similarity if the target has a valid embedding
            if face_emb.size > 0:
                for proto in proto_qs:
                    proto_emb = _l2_normalize(np.frombuffer(proto.embedding or b"", dtype=np.float32))
                    if proto_emb.size == face_emb.size:
                        sim = float(
                            np.dot(face_emb, proto_emb) / (np.linalg.norm(face_emb) * np.linalg.norm(proto_emb))
                        )
                        if sim > best_sim:
                            best_sim = sim

            # Fallback/also compare to centroid if present
            if face_emb.size > 0 and person.embedding_centroid is not None:
                centroid = _l2_normalize(np.array(person.embedding_centroid, dtype=np.float32))
                if centroid.size == face_emb.size and centroid.size > 0:
                    sim = float(np.dot(face_emb, centroid) / (np.linalg.norm(face_emb) * np.linalg.norm(centroid)))
                    if sim > best_sim:
                        best_sim = sim

            # Compute headshot url using serializer helper logic
            headshot_url = None
            try:
                headshot = person.headshot_face
                if headshot and hasattr(headshot, "thumbnail") and headshot.thumbnail and headshot.thumbnail.is_ready:
                    headshot_url = headshot.thumbnail.storage_url
                else:
                    candidate_face = (
                        Face.objects.select_related("thumbnail")
                        .filter(person=person, thumbnail__is_ready=True)
                        .order_by("-quality", "-detection_confidence", "-created_at")
                        .first()
                    )
                    if candidate_face and candidate_face.thumbnail:
                        headshot_url = candidate_face.thumbnail.storage_url
            except Exception:
                headshot_url = None

            candidates.append(
                {
                    "id": str(person.id),
                    "display_name": person.display_name,
                    "headshot_url": headshot_url,
                    "face_count": getattr(person, "face_count", 0),
                    # Only include similarity when we actually computed it (>= 0 and face_emb valid)
                    **({"similarity": best_sim} if (face_emb.size > 0 and best_sim >= 0.0) else {}),
                }
            )

        # If no similarity values were computed but the face has an embedding, fallback to KNN over faces
        any_similarity = any("similarity" in c for c in candidates)
        if (not any_similarity) and face_emb.size > 0:
            try:
                emb_list = np.asarray(face_emb, dtype=np.float32).astype(float).tolist()
                emb_str = "[" + ",".join(str(x) for x in emb_list) + "]"
                from django.db import connection

                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT f.person_id,
                               MAX(1 - (fs.embedding <=> %s::vector)) AS cosine_sim
                        FROM faces AS f
                        JOIN face_search AS fs ON fs.face_id = f.id
                        WHERE f.person_id IS NOT NULL
                        GROUP BY f.person_id
                        ORDER BY cosine_sim DESC
                        LIMIT %s
                        """,
                        [emb_str, limit],
                    )
                    rows = cursor.fetchall()
                id_to_idx = {c["id"]: i for i, c in enumerate(candidates)}
                for person_id, sim in rows:
                    if person_id is None:
                        continue
                    pid = str(person_id)
                    if pid in id_to_idx:
                        candidates[id_to_idx[pid]]["similarity"] = float(sim)
                    else:
                        try:
                            p = Person.objects.get(id=pid)
                        except Person.DoesNotExist:
                            continue
                        # compute headshot url similarly
                        headshot_url = None
                        try:
                            headshot = p.headshot_face
                            if (
                                headshot
                                and hasattr(headshot, "thumbnail")
                                and headshot.thumbnail
                                and headshot.thumbnail.is_ready
                            ):
                                headshot_url = headshot.thumbnail.storage_url
                            else:
                                candidate_face = (
                                    Face.objects.select_related("thumbnail")
                                    .filter(person=p, thumbnail__is_ready=True)
                                    .order_by("-quality", "-detection_confidence", "-created_at")
                                    .first()
                                )
                                if candidate_face and candidate_face.thumbnail:
                                    headshot_url = candidate_face.thumbnail.storage_url
                        except Exception:
                            headshot_url = None
                        candidates.append(
                            {
                                "id": pid,
                                "display_name": p.display_name,
                                "headshot_url": headshot_url,
                                "face_count": p.faces.count(),
                                "similarity": float(sim),
                            }
                        )
            except Exception:
                pass

        # Sort by similarity desc if present, then by face_count desc
        def sort_key(c):
            return (c.get("similarity", -1.0), c.get("face_count", 0))

        candidates.sort(key=sort_key, reverse=True)
        if limit > 0:
            candidates = candidates[:limit]

        return Response({"results": candidates})


class PersonMergeSuggestionViewSet(viewsets.ModelViewSet):
    """ViewSet for managing person merge suggestions."""

    queryset = PersonMergeSuggestion.objects.select_related("person_a", "person_b", "reviewed_by").all()
    serializer_class = PersonMergeSuggestionSerializer
    permission_classes = [permissions.IsAuthenticated]
