import logging
from io import BytesIO
from typing import Any, Dict, List, Optional

import cv2
import numpy as np
from celery import shared_task
from django.conf import settings
from insightface.app import FaceAnalysis

from assets.models import Asset

from .models import Face, FaceSearch, Person

logger = logging.getLogger(__name__)

# Global face analysis app (loaded once)
_face_app = None


def _l2_normalize(vector: np.ndarray) -> np.ndarray:
    """Return a unit-length copy of the vector (no-op if zero norm)."""
    norm = float(np.linalg.norm(vector))
    if norm > 0.0:
        return (vector / norm).astype(np.float32)
    return vector.astype(np.float32)


def get_face_app():
    """Get or create the InsightFace app instance."""
    global _face_app
    if _face_app is None:
        logger.info("Initializing InsightFace with buffalo_l model...")
        try:
            _face_app = FaceAnalysis(
                name="buffalo_l",
                providers=["CPUExecutionProvider"],  # Use CPU for now, can be changed to GPU
            )
            _face_app.prepare(ctx_id=-1, det_size=(320, 320))  # Use CPU (-1) and smaller size
            logger.info("InsightFace initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize InsightFace: {e}")
            _face_app = None
            raise
    return _face_app


def create_fresh_face_app():
    """Create a fresh InsightFace app instance for each task."""
    logger.info("Creating fresh InsightFace instance...")
    try:
        app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
        app.prepare(ctx_id=-1, det_size=(320, 320))  # Use CPU (-1) and smaller size
        logger.info("Fresh InsightFace instance created successfully")
        return app
    except Exception as e:
        logger.error(f"Failed to create fresh InsightFace instance: {e}")
        raise


@shared_task(bind=True, max_retries=3)
def detect_faces(self, asset_id: str) -> Dict[str, Any]:
    """
    Detect faces in an asset and create Face records.

    Args:
        asset_id: UUID of the asset to process

    Returns:
        Dict with success status and face detection results
    """
    try:
        asset = Asset.objects.get(id=asset_id)
        logger.info(f"Detecting faces in asset {asset_id}")
        openphotobox_cfg = getattr(settings, "OPENPHOTOBOX", {})
        replace_existing = False
        skip_if_exists = False
        # Idempotency: optionally skip or replace to avoid duplicates when re-queued repeatedly
        existing_count = asset.faces.count()
        if existing_count > 0 and skip_if_exists and not replace_existing:
            logger.info(f"Skipping face detection for {asset_id}: {existing_count} faces already present")
            return {
                "success": True,
                "asset_id": asset_id,
                "faces_detected": existing_count,
                "message": "Skipped because faces already exist",
            }
        if existing_count > 0 and replace_existing:
            # Safer behavior: try to update in-place where possible, only delete if unmatched
            logger.info(f"Re-detection with REPLACE_EXISTING: will update matching faces in-place for asset {asset_id}")

        # Download the image
        image_data = _download_asset_image(asset)
        if not image_data:
            raise Exception("Failed to download asset image")

        # Load image with OpenCV
        image_array = np.frombuffer(image_data.getvalue(), np.uint8)
        image = cv2.imdecode(image_array, cv2.IMREAD_COLOR)

        if image is None:
            raise Exception("Failed to decode image")

        # Get face analysis app - use fresh instance for Celery workers
        try:
            app = create_fresh_face_app()
        except Exception as e:
            logger.error(f"Failed to create InsightFace app: {e}")
            return {"success": False, "asset_id": asset_id, "error": f"InsightFace initialization failed: {str(e)}"}

        # Detect faces
        faces = app.get(image)
        # Filter detections by detection score if configured
        min_score = float(getattr(settings, "OPENPHOTOBOX", {}).get("FACE_DETECTION_MIN_SCORE", 0.0))
        if min_score > 0:
            faces = [f for f in faces if getattr(f, "det_score", 1.0) >= min_score]

        # Create or update Face records
        created_faces = []
        updated_faces = 0
        # Build existing list for IoU matching if we intend to replace
        existing_faces = list(asset.faces.all()) if (existing_count > 0 and replace_existing) else []

        def iou(a, b):
            # a,b are dicts with x,y,w,h normalized
            ax1, ay1, aw, ah = a["x"], a["y"], a["w"], a["h"]
            bx1, by1, bw, bh = b["x"], b["y"], b["w"], b["h"]
            ax2, ay2 = ax1 + aw, ay1 + ah
            bx2, by2 = bx1 + bw, by1 + bh
            inter_w = max(0.0, min(ax2, bx2) - max(ax1, bx1))
            inter_h = max(0.0, min(ay2, by2) - max(ay1, by1))
            inter = inter_w * inter_h
            union = aw * ah + bw * bh - inter
            return (inter / union) if union > 0 else 0.0

        matched_existing_ids = set()
        for face in faces:
            # Convert bounding box to normalized coordinates
            bbox = face.bbox
            height, width = image.shape[:2]

            x = bbox[0] / width
            y = bbox[1] / height
            w = (bbox[2] - bbox[0]) / width
            h = (bbox[3] - bbox[1]) / height

            # Get embedding
            embedding = face.embedding.astype(np.float32)

            # Calculate quality score (combination of detection confidence and face size)
            detection_confidence = face.det_score
            face_area = w * h
            quality = detection_confidence * min(face_area * 10, 1.0)  # Boost quality for larger faces

            # Update-in-place if overlapping with an existing face (IoU >= 0.5)
            matched = None
            if existing_faces:
                new_box = {"x": x, "y": y, "w": w, "h": h}
                best_iou = 0.0
                for ef in existing_faces:
                    if ef.id in matched_existing_ids:
                        continue
                    old_box = {"x": ef.x, "y": ef.y, "w": ef.w, "h": ef.h}
                    cur_iou = iou(new_box, old_box)
                    if cur_iou >= 0.5 and cur_iou > best_iou:
                        best_iou = cur_iou
                        matched = ef

            if matched is not None:
                # Preserve person link; update box, embedding, quality
                matched.x = x
                matched.y = y
                matched.w = w
                matched.h = h
                matched.embedding = embedding.tobytes()
                matched.quality = quality
                matched.detection_model = "buffalo_l"
                matched.detection_confidence = detection_confidence
                matched.save()
                matched_existing_ids.add(matched.id)
                updated_faces += 1
                face_record = matched
            else:
                face_record = Face.objects.create(
                    asset=asset,
                    x=x,
                    y=y,
                    w=w,
                    h=h,
                    embedding=embedding.tobytes(),
                    quality=quality,
                    detection_model="buffalo_l",
                    detection_confidence=detection_confidence,
                )
                created_faces.append(face_record)

            # Upsert FaceSearch with L2-normalized embedding for cosine search
            try:
                FaceSearch.objects.update_or_create(
                    face=face_record,
                    defaults={
                        "embedding": _l2_normalize(embedding),
                    },
                )
            except Exception as e:
                logger.warning(f"Failed to upsert FaceSearch for face {face_record.id}: {e}")

            # Generate a face thumbnail asynchronously for quick UI display
            try:
                from assets.tasks import generate_face_thumbnail  # Local import to avoid circular deps

                generate_face_thumbnail.apply_async(args=[str(face_record.id)])
            except Exception as e:
                logger.warning(f"Failed to enqueue face thumbnail generation for {face_record.id}: {e}")

        # Delete unmatched old faces only if replace_existing
        if existing_faces and replace_existing:
            to_delete = [ef for ef in existing_faces if ef.id not in matched_existing_ids]
            if to_delete:
                Face.objects.filter(id__in=[f.id for f in to_delete]).delete()
                logger.info(f"Removed {len(to_delete)} stale faces for asset {asset_id}")

        logger.info(f"Detected {len(created_faces)} faces in asset {asset_id} (updated {updated_faces})")

        # Trigger follow-up recognition pipeline if we found faces
        if created_faces:
            try:
                # Prefer KNN assignment; small delay to allow other faces to arrive
                assign_delay_sec = int(openphotobox_cfg.get("FACE_ASSIGN_SCHEDULE_DELAY_SEC", 15))
                from .tasks import assign_faces_knn  # Local import

                assign_faces_knn.apply_async(kwargs={"limit": 1000}, countdown=assign_delay_sec)
            except Exception as e:
                logger.warning(f"Failed to enqueue KNN assignment: {e}")

        return {
            "success": True,
            "asset_id": asset_id,
            "faces_detected": len(created_faces),
            "face_ids": [str(f.id) for f in created_faces],
        }

    except Asset.DoesNotExist:
        logger.error(f"Asset {asset_id} not found")
        return {"success": False, "error": "Asset not found"}
    except Exception as exc:
        logger.error(f"Error detecting faces in asset {asset_id}: {exc}")

        # Don't retry on segmentation faults or worker crashes
        if any(keyword in str(exc) for keyword in ["SIGSEGV", "WorkerLostError", "segmentation fault"]):
            logger.error(f"Non-retryable error for asset {asset_id}: {exc}")
            return {"success": False, "asset_id": asset_id, "error": f"Non-retryable error: {str(exc)}"}

        raise self.retry(exc=exc, countdown=60 * (2**self.request.retries))


def _download_asset_image(asset: Asset) -> Optional[BytesIO]:
    """Read asset image data for processing from local filesystem."""
    try:
        # Read from local filesystem
        from assets.services import UploadService

        upload_service = UploadService(asset.storage_bucket.backend)
        file_path = upload_service.get_file_path(asset)

        with open(file_path, "rb") as f:
            return BytesIO(f.read())
    except Exception as e:
        logger.error(f"Failed to read image for asset {asset.id}: {e}")
        return None


def _calculate_centroid(faces: List[Face]) -> np.ndarray:
    """Calculate the centroid embedding for a list of faces."""
    embeddings = []
    for face in faces:
        # Exclude manual faces from centroid to avoid biasing recognition
        if getattr(face, "detection_model", "") == "manual":
            continue
        raw = np.frombuffer(face.embedding or b"", dtype=np.float32)
        if raw.size == 0:
            continue
        embedding = _l2_normalize(raw)
        embeddings.append(embedding)

    if not embeddings:
        return np.zeros((512,), dtype=np.float32)
    embeddings = np.array(embeddings)
    centroid = np.mean(embeddings, axis=0)
    return _l2_normalize(centroid)


@shared_task(bind=True, max_retries=3)
def assign_faces_knn(self, limit: int = 500) -> Dict[str, Any]:
    """Assign unassigned faces using KNN on pgvector.
    For each unassigned face with a search embedding, find nearest neighbors by cosine distance.
    If any neighbor has a person, assign to that person.
    Person creation is handled by clustering; KNN does not create people unless explicitly enabled
    via OPENPHOTOBOX.FACE_KNN_ALLOW_PERSON_CREATION=True.
    """
    try:
        cfg = getattr(settings, "OPENPHOTOBOX", {})
        max_distance = float(cfg.get("FACE_SEARCH_MAX_DISTANCE", 0.5))
        min_faces = int(cfg.get("FACE_SEARCH_MIN_FACES", 3))
        allow_create = bool(cfg.get("FACE_KNN_ALLOW_PERSON_CREATION", False))

        # Pull candidate faces with embeddings
        faces = (
            Face.objects.filter(person__isnull=True)
            .filter(face_search__isnull=False)
            .select_related("asset")
            .order_by("-quality", "-detection_confidence", "-created_at")[:limit]
        )
        processed = 0
        assigned = 0
        created_people = 0

        for face in faces:
            processed += 1
            src = FaceSearch.objects.get(face=face)
            # KNN via raw SQL to leverage pgvector cosine distance
            from django.db import connection

            # Prepare embedding as a Python list of floats and as a vector literal string
            emb_list = np.asarray(src.embedding, dtype=np.float32).astype(float).tolist()
            emb_str = "[" + ",".join(str(x) for x in emb_list) + "]"
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT f.id, f.person_id,
                           1 - (fs.embedding <=> %s::vector) AS cosine_sim,
                           (fs.embedding <=> %s::vector) AS distance
                    FROM faces AS f
                    JOIN face_search AS fs ON fs.face_id = f.id
                    WHERE f.id <> %s
                    ORDER BY distance ASC
                    LIMIT %s
                    """,
                    [emb_str, emb_str, str(face.id), max(min_faces, 10)],
                )
                rows = cursor.fetchall()

            # Filter by distance cutoff
            neighbors = [
                {"face_id": r[0], "person_id": r[1], "similarity": float(r[2]), "distance": float(r[3])}
                for r in rows
                if float(r[3]) <= max_distance
            ]
            if not neighbors:
                continue

            # Assign to an existing person if any neighbor has one
            neighbor_with_person = next((n for n in neighbors if n["person_id"]), None)
            if neighbor_with_person:
                person = Person.objects.get(id=neighbor_with_person["person_id"])
                face.person = person
                face.confirmed = False  # KNN assignments are unconfirmed candidates
                face.save(update_fields=["person", "confirmed", "updated_at"])
                # Update centroid for completeness
                faces_qs = Face.objects.filter(person=person).only("embedding")
                centroid = (
                    _calculate_centroid(list(faces_qs))
                    if faces_qs.exists()
                    else _l2_normalize(np.frombuffer(face.embedding, dtype=np.float32))
                )
                person.embedding_centroid = centroid
                person.embedding_count = faces_qs.count()
                if (not person.headshot_face) or (face.quality > person.headshot_face.quality):
                    person.headshot_face = face
                person.save(update_fields=["embedding_centroid", "embedding_count", "headshot_face", "updated_at"])
                assigned += 1
                display_name = (person.display_name or "").strip() or "Unnamed"
                logger.info(
                    f"KNN assigned face {face.id} to existing person {display_name} ({person.id}) "
                    f"(distance={neighbor_with_person['distance']:.3f})"
                )
                continue

            # If no neighbor has a person: do NOT create a person here by default.
            # Leave the face unassigned for the clustering job to handle.
            if allow_create and len(neighbors) >= min_faces:
                person = Person.objects.create(
                    display_name="",
                    headshot_face=face,
                )
                face.person = person
                face.confirmed = False  # KNN assignments are unconfirmed candidates
                face.save(update_fields=["person", "confirmed", "updated_at"])
                faces_qs = Face.objects.filter(person=person).only("embedding")
                person.embedding_centroid = (
                    _calculate_centroid(list(faces_qs))
                    if faces_qs.exists()
                    else _l2_normalize(np.frombuffer(face.embedding, dtype=np.float32))
                )
                person.embedding_count = faces_qs.count()
                person.save(update_fields=["embedding_centroid", "embedding_count", "updated_at"])
                created_people += 1
                logger.info(f"KNN created new person {person.id} for face {face.id} (neighbors={len(neighbors)})")
            else:
                logger.debug(
                    f"KNN found {len(neighbors)} close neighbors for face {face.id} but no existing person; "
                    f"skipping person creation (allow_create={allow_create})."
                )

        return {"success": True, "processed": processed, "assigned": assigned, "persons_created": created_people}
    except Exception as exc:
        logger.error(f"Error in assign_faces_knn: {exc}")
        raise self.retry(exc=exc, countdown=60 * (2**self.request.retries))


@shared_task(bind=True, max_retries=3)
def revalidate_unconfirmed_faces(self, person_ids: Optional[List[str]] = None, limit: int = 1000) -> Dict[str, Any]:
    """Re-evaluate unconfirmed face assignments to see if better matches exist.

    This task continuously improves face assignments as users confirm more faces.
    Only unconfirmed faces are re-evaluated; confirmed faces are never auto-changed.

    Args:
        person_ids: Optional list of person UUIDs to limit scope (defaults to all)
        limit: Max number of faces to process per run (default 1000)

    Returns:
        Dict with success status, processed count, and reassignment statistics
    """
    try:
        cfg = getattr(settings, "OPENPHOTOBOX", {})
        max_distance = float(cfg.get("FACE_SEARCH_MAX_DISTANCE", 0.5))
        min_similarity_improvement = float(cfg.get("FACE_REVALIDATION_MIN_IMPROVEMENT", 0))

        # Query unconfirmed faces (either assigned or unassigned)
        faces_qs = (
            Face.objects.filter(confirmed=False).filter(face_search__isnull=False).select_related("person", "asset")
        )

        # Optionally filter by person_ids
        if person_ids:
            faces_qs = faces_qs.filter(person_id__in=person_ids)

        # Order by priority: assigned faces first (candidates), then by quality
        faces_qs = faces_qs.order_by("-person_id", "-quality", "-detection_confidence", "-created_at")[:limit]

        processed = 0
        reassigned = 0
        unassigned = 0

        for face in faces_qs:
            processed += 1
            current_person_id = face.person_id

            # Get face embedding
            try:
                src = FaceSearch.objects.get(face=face)
            except FaceSearch.DoesNotExist:
                logger.warning(f"Face {face.id} missing FaceSearch record, skipping")
                continue

            # KNN to find best matches
            from django.db import connection

            emb_list = np.asarray(src.embedding, dtype=np.float32).astype(float).tolist()
            emb_str = "[" + ",".join(str(x) for x in emb_list) + "]"

            with connection.cursor() as cursor:
                # Find nearest confirmed faces (as they are ground truth)
                cursor.execute(
                    """
                    SELECT f.id, f.person_id, f.confirmed,
                           1 - (fs.embedding <=> %s::vector) AS cosine_sim,
                           (fs.embedding <=> %s::vector) AS distance
                    FROM faces AS f
                    JOIN face_search AS fs ON fs.face_id = f.id
                    WHERE f.id <> %s AND f.person_id IS NOT NULL
                    ORDER BY distance ASC
                    LIMIT 20
                    """,
                    [emb_str, emb_str, str(face.id)],
                )
                rows = cursor.fetchall()

            if not rows:
                continue

            # Build person match scores: prefer confirmed faces, weight by similarity
            person_scores = {}
            for row in rows:
                face_id, person_id, confirmed, similarity, distance = row
                if distance > max_distance:
                    continue

                person_id_str = str(person_id)
                # Weight confirmed faces more heavily
                weight = 2.0 if confirmed else 1.0
                weighted_sim = float(similarity) * weight

                if person_id_str not in person_scores:
                    person_scores[person_id_str] = {"max_sim": weighted_sim, "count": 0}
                else:
                    person_scores[person_id_str]["max_sim"] = max(person_scores[person_id_str]["max_sim"], weighted_sim)
                person_scores[person_id_str]["count"] += 1

            if not person_scores:
                # No good matches found; unassign if currently assigned
                if current_person_id:
                    face.person = None
                    face.save(update_fields=["person", "updated_at"])
                    unassigned += 1
                    logger.info(f"Revalidation unassigned face {face.id} (no good matches)")
                continue

            # Find best matching person
            best_person_id = max(person_scores.items(), key=lambda x: (x[1]["max_sim"], x[1]["count"]))[0]
            best_score = person_scores[best_person_id]["max_sim"]

            # Decide whether to reassign
            should_reassign = False

            if not current_person_id:
                # Face is unassigned; assign to best match
                should_reassign = True
            elif str(current_person_id) != best_person_id:
                # Face is assigned to different person; check if new match is significantly better
                current_score = person_scores.get(str(current_person_id), {}).get("max_sim", 0.0)
                if best_score > current_score + min_similarity_improvement:
                    should_reassign = True

            if should_reassign and str(current_person_id) != best_person_id:
                # Reassign to better match
                try:
                    new_person = Person.objects.get(id=best_person_id)
                    old_person_id = current_person_id
                    face.person = new_person
                    # Keep as unconfirmed after reassignment
                    face.save(update_fields=["person", "updated_at"])
                    reassigned += 1

                    # Update centroids for both old and new persons
                    for pid in [old_person_id, new_person.id]:
                        if pid is None:
                            continue
                        try:
                            person = Person.objects.get(id=pid)
                            faces_qs_for_person = Face.objects.filter(person=person).only("embedding")
                            if faces_qs_for_person.exists():
                                centroid = _calculate_centroid(list(faces_qs_for_person))
                                person.embedding_centroid = centroid
                                person.embedding_count = faces_qs_for_person.count()
                                # Get best face for headshot (without select_related to avoid defer conflict)
                                best_face = (
                                    Face.objects.filter(person=person)
                                    .order_by("-quality", "-detection_confidence", "-created_at")
                                    .first()
                                )
                                person.headshot_face = best_face
                            else:
                                person.embedding_centroid = None
                                person.embedding_count = 0
                                person.headshot_face = None
                            person.save(
                                update_fields=["embedding_centroid", "embedding_count", "headshot_face", "updated_at"]
                            )
                        except Person.DoesNotExist:
                            continue

                    old_name = "None" if old_person_id is None else f"person {old_person_id}"
                    logger.info(
                        f"Revalidation reassigned face {face.id} from {old_name} to "
                        f"{new_person.display_name or 'Unnamed'} ({new_person.id}) "
                        f"(score improved from {person_scores.get(str(old_person_id), {}).get('max_sim', 0.0):.3f} "
                        f"to {best_score:.3f})"
                    )
                except Person.DoesNotExist:
                    logger.warning(f"Target person {best_person_id} not found for face {face.id}")
                    continue

        result = {
            "success": True,
            "processed": processed,
            "reassigned": reassigned,
            "unassigned": unassigned,
        }
        logger.info(f"Revalidation complete: {result}")
        return result

    except Exception as exc:
        logger.error(f"Error in revalidate_unconfirmed_faces: {exc}")
        raise self.retry(exc=exc, countdown=60 * (2**self.request.retries))
