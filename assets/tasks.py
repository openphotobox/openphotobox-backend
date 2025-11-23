import logging
from io import BytesIO
from typing import Any, Dict, Optional

import requests
from celery import shared_task
from django.utils import timezone
from PIL import Image, ImageOps

from people.models import FaceThumbnail

from .models import Asset, AssetThumbnail, StorageBucket
from .services import UploadService

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3)
def generate_asset_thumbnails(self, asset_id: str, sizes: list[str] | None = None) -> Dict[str, Any]:
    """Generate thumbnails for an asset at different sizes.

    Args:
        asset_id: UUID of the asset to process
        sizes: List of thumbnail size labels. Pass None (default) to use all sizes.
               (Using None avoids a mutable list as a default argument.)

    Returns:
        Dict with generation results
    """
    try:
        # Get the asset
        try:
            asset = Asset.objects.get(id=asset_id)
        except Asset.DoesNotExist:
            logger.error(f"Asset {asset_id} not found")
            return {"success": False, "error": "Asset not found"}

        if sizes is None:
            sizes = ["xs", "sm", "md", "lg"]

        # Download original image
        image_data = _download_asset_image(asset)
        if not image_data:
            logger.error(f"Failed to download image for asset {asset_id}")
            return {"success": False, "error": "Failed to download image"}

        # Load image with PIL
        try:
            original_image = Image.open(image_data)
            original_image = ImageOps.exif_transpose(original_image)  # Handle rotation

            if original_image.mode != "RGB":
                original_image = original_image.convert("RGB")

        except Exception as e:
            logger.error(f"Failed to load image: {e}")
            return {"success": False, "error": f"Failed to load image: {str(e)}"}

        # Get thumbnails bucket (or create if doesn't exist)
        thumbnails_bucket = _get_thumbnails_bucket()

        # Generate thumbnails for each size
        created_thumbnails = []
        for size in sizes:
            try:
                thumbnail = _generate_single_thumbnail(asset, original_image, size, thumbnails_bucket)
                if thumbnail:
                    created_thumbnails.append(thumbnail)
            except Exception as e:
                logger.error(f"Failed to generate {size} thumbnail for asset {asset_id}: {e}")
                continue

        logger.info(f"Generated {len(created_thumbnails)} thumbnails for asset {asset_id}")

        return {
            "success": True,
            "asset_id": asset_id,
            "thumbnails_generated": len(created_thumbnails),
            "thumbnail_sizes": [t.size for t in created_thumbnails],
        }

    except Exception as exc:
        logger.error(f"Error generating thumbnails for asset {asset_id}: {exc}")
        raise self.retry(exc=exc, countdown=60 * (2**self.request.retries))


@shared_task(bind=True, max_retries=3)
def generate_face_thumbnail(self, face_id: str) -> Dict[str, Any]:
    """
    Generate a cropped thumbnail for a detected face.

    Args:
        face_id: UUID of the face to process

    Returns:
        Dict with generation results
    """
    try:
        # Import here to avoid circular imports
        from people.models import Face

        # Get the face
        try:
            face = Face.objects.select_related("asset").get(id=face_id)
        except Face.DoesNotExist:
            logger.error(f"Face {face_id} not found")
            return {"success": False, "error": "Face not found"}

        # Download original image
        image_data = _download_asset_image(face.asset)
        if not image_data:
            logger.error(f"Failed to download image for face {face_id}")
            return {"success": False, "error": "Failed to download image"}

        # Load image with PIL
        try:
            original_image = Image.open(image_data)
            original_image = ImageOps.exif_transpose(original_image)  # Handle rotation

            if original_image.mode != "RGB":
                original_image = original_image.convert("RGB")

        except Exception as e:
            logger.error(f"Failed to load image: {e}")
            return {"success": False, "error": f"Failed to load image: {str(e)}"}

        # Get face thumbnails bucket
        thumbnails_bucket = _get_thumbnails_bucket()

        # Generate face thumbnail
        face_thumbnail = _generate_face_crop(face, original_image, thumbnails_bucket)

        if face_thumbnail:
            logger.info(f"Generated face thumbnail for face {face_id}")
            return {"success": True, "face_id": face_id, "thumbnail_id": str(face_thumbnail.id)}
        else:
            return {"success": False, "error": "Failed to generate face thumbnail"}

    except Exception as exc:
        logger.error(f"Error generating face thumbnail for face {face_id}: {exc}")
        raise self.retry(exc=exc, countdown=60 * (2**self.request.retries))


def _download_asset_image(asset: Asset) -> Optional[BytesIO]:
    """Download asset image data for processing."""
    try:
        # Download the full image (not just the first 64KB like for metadata)
        response = requests.get(asset.storage_url, stream=True)
        response.raise_for_status()

        image_data = BytesIO()
        for chunk in response.iter_content(chunk_size=8192):
            image_data.write(chunk)

        image_data.seek(0)
        return image_data

    except Exception as e:
        logger.error(f"Failed to download asset image: {e}")
        return None


def _get_thumbnails_bucket() -> StorageBucket:
    """Get the thumbnails storage bucket (uses same bucket as originals with subdirectory)."""
    from .services import get_default_upload_bucket

    # Try to get existing thumbnails bucket
    try:
        return StorageBucket.objects.get(purpose="thumbnails", is_active=True)
    except StorageBucket.DoesNotExist:
        # If no thumbnails bucket exists, use the originals bucket directly
        # The path_prefix will be handled in the upload logic
        originals_bucket = get_default_upload_bucket("originals")

        logger.info(
            f"No thumbnails bucket found, using originals bucket: {originals_bucket.name} with thumbnails/ prefix"
        )
        return originals_bucket


def _generate_single_thumbnail(
    asset: Asset, original_image: Image.Image, size: str, bucket: StorageBucket
) -> Optional[AssetThumbnail]:
    """Generate a single thumbnail for an asset."""
    try:
        # Get target size in pixels
        target_size = AssetThumbnail.get_size_pixels(size)

        # Create thumbnail while maintaining aspect ratio
        thumbnail_image = original_image.copy()
        thumbnail_image.thumbnail((target_size, target_size), Image.Resampling.LANCZOS)

        # Generate storage key
        file_key = f"assets/{asset.id}/{size}.jpg"

        # Save thumbnail to memory
        thumbnail_data = BytesIO()
        thumbnail_image.save(thumbnail_data, format="JPEG", quality=85, optimize=True)
        thumbnail_size = thumbnail_data.tell()
        thumbnail_data.seek(0)

        # Upload to storage
        upload_service = UploadService(bucket.backend)
        try:
            # Handle path prefix for thumbnails
            full_key = file_key
            if bucket.path_prefix:
                full_key = f"{bucket.path_prefix}/{file_key}"
            elif bucket.purpose == "originals":
                # If we're using the originals bucket for thumbnails, add thumbnails/ prefix
                full_key = f"thumbnails/{file_key}"

            # Upload thumbnail to storage
            upload_service.client.put_object(
                Bucket=bucket.name,
                Key=full_key,
                Body=thumbnail_data.getvalue(),
                ContentType="image/jpeg",
                ContentLength=thumbnail_size,
            )
            logger.info(f"Uploaded thumbnail {full_key} to storage")
        except Exception as e:
            logger.error(f"Failed to upload thumbnail to storage: {e}")
            # Still create the database record even if upload fails

        # Create or update thumbnail record
        thumbnail, created = AssetThumbnail.objects.update_or_create(
            asset=asset,
            size=size,
            defaults={
                "storage_bucket": bucket,
                "storage_key": file_key,
                "width": thumbnail_image.width,
                "height": thumbnail_image.height,
                "file_size": thumbnail_size,
                "is_ready": True,
                "generated_at": timezone.now(),
            },
        )

        return thumbnail

    except Exception as e:
        logger.error(f"Failed to generate {size} thumbnail: {e}")
        return None


def _generate_face_crop(face, original_image: Image.Image, bucket: StorageBucket) -> Optional[FaceThumbnail]:
    """Generate a cropped thumbnail for a face."""
    try:
        # Convert normalized coordinates to absolute coordinates
        img_width, img_height = original_image.size

        # Get face bounding box (with some padding)
        padding = 0.2  # 20% padding around the face
        x = max(0, face.x - padding * face.w)
        y = max(0, face.y - padding * face.h)
        w = min(1.0 - x, face.w + 2 * padding * face.w)
        h = min(1.0 - y, face.h + 2 * padding * face.h)

        # Convert to pixel coordinates
        left = int(x * img_width)
        top = int(y * img_height)
        right = int((x + w) * img_width)
        bottom = int((y + h) * img_height)

        # Crop face region
        face_crop = original_image.crop((left, top, right, bottom))

        # Resize to square thumbnail
        face_thumbnail = face_crop.resize((128, 128), Image.Resampling.LANCZOS)

        # Generate storage key
        file_key = f"faces/{face.id}/thumbnail.jpg"

        # Save thumbnail to memory
        thumbnail_data = BytesIO()
        face_thumbnail.save(thumbnail_data, format="JPEG", quality=85, optimize=True)
        thumbnail_size = thumbnail_data.tell()
        thumbnail_data.seek(0)

        # Upload to storage
        upload_service = UploadService(bucket.backend)
        try:
            # Handle path prefix for face thumbnails
            full_key = file_key
            if bucket.path_prefix:
                full_key = f"{bucket.path_prefix}/{file_key}"
            elif bucket.purpose == "originals":
                # If we're using the originals bucket for face thumbnails, add face-thumbnails/ prefix
                full_key = f"face-thumbnails/{file_key}"

            # Upload face thumbnail to storage
            upload_service.client.put_object(
                Bucket=bucket.name,
                Key=full_key,
                Body=thumbnail_data.getvalue(),
                ContentType="image/jpeg",
                ContentLength=thumbnail_size,
            )
            logger.info(f"Uploaded face thumbnail {full_key} to storage")
        except Exception as e:
            logger.error(f"Failed to upload face thumbnail to storage: {e}")
            # Still create the database record even if upload fails

        # Create or update face thumbnail record
        face_thumbnail_obj, created = FaceThumbnail.objects.update_or_create(
            face=face,
            defaults={
                "storage_bucket": bucket,
                "storage_key": file_key,
                "size": 128,
                "file_size": thumbnail_size,
                "is_ready": True,
                "generated_at": timezone.now(),
            },
        )

        return face_thumbnail_obj

    except Exception as e:
        logger.error(f"Failed to generate face thumbnail: {e}")
        return None
