import logging
import re
import time
from io import BytesIO
from typing import Any, Dict, Optional

import numpy as np
import requests
from celery import shared_task
from django.conf import settings
from PIL import ExifTags, Image

from assets.models import Asset

from .models import AssetKeyword, AssetMetadata, ClipEmbedding, KeywordTag
from .services import embed_image_bytes

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3)
def process_asset_metadata(self, asset_id: str) -> Dict[str, Any]:
    """
    Process metadata for an uploaded asset.

    This task extracts EXIF data, generates keywords, and creates CLIP embeddings
    for semantic search capabilities.
    """
    try:
        asset = Asset.objects.get(id=asset_id)
        logger.info(f"Processing metadata for asset {asset_id}")

        # Download the image for processing
        image_data = _download_asset_image(asset)
        if not image_data:
            raise Exception("Failed to download asset image")

        # Extract EXIF data
        exif_data = _extract_exif_data(image_data)

        # Also extract embedded XMP dates (Lightroom often writes capture date to XMP)
        try:
            image_data.seek(0)
            raw_bytes = image_data.getvalue()
            xmp_dates = _extract_xmp_dates_from_bytes(raw_bytes)
            # Merge into exif-style dict only if EXIF is missing these fields
            for k in ("CreateDate", "DateCreated"):
                if xmp_dates.get(k) and not exif_data.get(k):
                    exif_data[k] = xmp_dates[k]
        except Exception as e:
            logger.warning(f"XMP date extraction failed for asset {asset_id}: {e}")

        # Ensure width/height are populated on Asset
        try:
            image_data.seek(0)
            dims = _get_image_dimensions(image_data, exif_data)
            if dims is not None:
                img_w, img_h = dims
                if asset.width != img_w or asset.height != img_h:
                    asset.width = img_w
                    asset.height = img_h
                    asset.save(update_fields=["width", "height"])
        except Exception as e:
            logger.warning(f"Could not compute dimensions for asset {asset_id}: {e}")

        # Ensure taken_at is populated from EXIF/XMP capture date if available
        try:
            taken_dt = _extract_taken_datetime(exif_data)
            if taken_dt is not None:
                # If datetime is naive, apply configured default capture timezone
                if taken_dt.tzinfo is None:
                    from zoneinfo import ZoneInfo

                    default_tz_name = getattr(settings, "OPENPHOTOBOX", {}).get(
                        "DEFAULT_CAPTURE_TZ", "America/New_York"
                    )
                    aware_dt = taken_dt.replace(tzinfo=ZoneInfo(default_tz_name))
                else:
                    aware_dt = taken_dt
                if asset.taken_at != aware_dt:
                    asset.taken_at = aware_dt
                    asset.save(update_fields=["taken_at"])
        except Exception as e:
            logger.warning(f"Could not extract taken_at for asset {asset_id}: {e}")

        # Create or update metadata record
        metadata, created = AssetMetadata.objects.get_or_create(
            asset=asset,
            defaults={
                "exif_data": exif_data,
                "camera_make": exif_data.get("Make", "") or "",
                "camera_model": exif_data.get("Model", "") or "",
                "lens_model": exif_data.get("LensModel", "") or "",
                "iso": exif_data.get("ISOSpeedRatings"),
                "aperture": _format_aperture(exif_data.get("FNumber")) or "",
                "shutter_speed": _format_shutter_speed(exif_data.get("ExposureTime")) or "",
                "focal_length": _format_focal_length(exif_data.get("FocalLength")) or "",
                "white_balance": exif_data.get("WhiteBalance", "") or "",
                "color_space": exif_data.get("ColorSpace", "") or "",
                "software": exif_data.get("Software", "") or "",
            },
        )

        if not created:
            # Update existing metadata
            metadata.exif_data = exif_data
            metadata.camera_make = exif_data.get("Make", "") or ""
            metadata.camera_model = exif_data.get("Model", "") or ""
            metadata.lens_model = exif_data.get("LensModel", "") or ""
            metadata.iso = exif_data.get("ISOSpeedRatings")
            metadata.aperture = _format_aperture(exif_data.get("FNumber")) or ""
            metadata.shutter_speed = _format_shutter_speed(exif_data.get("ExposureTime")) or ""
            metadata.focal_length = _format_focal_length(exif_data.get("FocalLength")) or ""
            metadata.white_balance = exif_data.get("WhiteBalance", "") or ""
            metadata.color_space = exif_data.get("ColorSpace", "") or ""
            metadata.software = exif_data.get("Software", "") or ""
            metadata.save()

        # Extract description from TIFF/IPTC metadata
        description = _extract_description_from_metadata(exif_data)
        description_extracted = False
        if description and not asset.description:
            asset.description = description
            asset.save()
            description_extracted = True

        # Generate keywords from EXIF data
        _generate_keywords_from_exif(asset, exif_data)

        # Generate CLIP embedding for semantic search (only if async processing is available)
        try:
            generate_clip_embedding.delay(asset_id)
        except Exception as e:
            logger.warning(f"Could not queue CLIP embedding task: {e}")

        # Detect faces in the image
        try:
            from people.tasks import detect_faces

            detect_faces.delay(asset_id)
        except Exception as e:
            logger.warning(f"Could not queue face detection task: {e}")

        logger.info(f"Successfully processed metadata for asset {asset_id}")
        return {
            "success": True,
            "asset_id": asset_id,
            "metadata_created": created,
            "exif_fields_extracted": len([k for k, v in exif_data.items() if v]),
            "description_extracted": description_extracted,
        }

    except Asset.DoesNotExist:
        logger.error(f"Asset {asset_id} not found")
        return {"success": False, "error": "Asset not found"}
    except Exception as exc:
        logger.error(f"Error processing metadata for asset {asset_id}: {exc}")
        # Retry the task
        raise self.retry(exc=exc, countdown=60 * (2**self.request.retries))


@shared_task(bind=True, max_retries=3)
def generate_clip_embedding(self, asset_id: str) -> Dict[str, Any]:
    """
    Generate CLIP embedding for semantic search.

    This is a placeholder for now - in a real implementation, you would:
    1. Load a CLIP model (e.g., from transformers library)
    2. Process the image through the model
    3. Store the embedding vector
    """
    try:
        asset = Asset.objects.get(id=asset_id)
        logger.info(f"Generating CLIP embedding for asset {asset_id}")

        # Download full image for CLIP (avoid Range header to ensure full content)
        img_resp = requests.get(asset.storage_url, timeout=30)
        img_resp.raise_for_status()
        image_bytes = img_resp.content

        # Compute real CLIP embedding (L2-normalized float32 length 512)
        start = time.time()
        embedding_np = embed_image_bytes(image_bytes)
        elapsed_ms = int((time.time() - start) * 1000)
        embedding_vector = embedding_np.astype(float).tolist()

        # Create or update embedding record
        embedding, created = ClipEmbedding.objects.get_or_create(
            asset=asset,
            defaults={
                "embedding": embedding_vector,
                "model_name": "ViT-B/32",
                "model_version": "1.0",
                "processing_time_ms": elapsed_ms,
                "image_size_used": "224x224",
                "embedding_norm": float(np.linalg.norm(embedding_np)),
                "confidence_score": 1.0,
            },
        )

        if not created:
            embedding.embedding = embedding_vector
            embedding.processing_time_ms = elapsed_ms
            embedding.embedding_norm = float(np.linalg.norm(embedding_np))
            embedding.save(update_fields=["embedding", "processing_time_ms", "embedding_norm", "updated_at"])

        logger.info(f"Successfully generated CLIP embedding for asset {asset_id}")
        return {
            "success": True,
            "asset_id": asset_id,
            "embedding_created": created,
            "embedding_dimensions": len(embedding_vector),
        }

    except Asset.DoesNotExist:
        logger.error(f"Asset {asset_id} not found")
        return {"success": False, "error": "Asset not found"}
    except Exception as exc:
        logger.error(f"Error generating CLIP embedding for asset {asset_id}: {exc}")
        raise self.retry(exc=exc, countdown=60 * (2**self.request.retries))


def _download_asset_image(asset: Asset) -> Optional[BytesIO]:
    """Download asset image data for processing."""
    try:
        # Use the Django proxy URL to get the image
        # Add headers to request only the first few KB for EXIF data
        headers = {
            "Range": "bytes=0-65536"  # First 64KB should contain all EXIF data
        }

        response = requests.get(asset.storage_url, headers=headers, timeout=10)
        response.raise_for_status()
        return BytesIO(response.content)
    except Exception as e:
        logger.error(f"Failed to download image for asset {asset.id}: {e}")
        return None


def _extract_exif_data(image_data: BytesIO) -> Dict[str, Any]:
    """Extract EXIF data from image."""
    try:
        image = Image.open(image_data)
        exif_data = {}
        if hasattr(image, "_getexif") and image._getexif() is not None:
            exif = image._getexif()
            for tag_id, value in exif.items():
                tag = ExifTags.TAGS.get(tag_id, tag_id)
                # Convert PIL types to JSON-serializable types
                # Decode Windows XP fields with UTF-16LE if present
                if tag in ("XPComment", "XPSubject", "XPTitle", "XPKeywords", "XPAuthor") and isinstance(
                    value, (bytes, bytearray)
                ):
                    try:
                        safe_value = bytes(value).decode("utf-16le", errors="ignore").rstrip("\x00")
                    except Exception:
                        safe_value = _convert_pil_value(value)
                    exif_data[tag] = safe_value

        # Final sanitation to ensure JSON-serializable values recursively
        exif_data = _sanitize_for_json(exif_data)
        return exif_data
    except Exception as e:
        logger.error(f"Failed to extract EXIF data: {e}")
        return {}


def _extract_taken_datetime(exif_data: Dict[str, Any]):
    """Extract capture datetime from EXIF/IPTC-like fields.

    Preference order: DateTimeOriginal, CreateDate, DateCreated, DateTimeDigitized, DateTime
    Expected EXIF format is 'YYYY:MM:DD HH:MM:SS'.
    Returns a timezone-naive datetime in local format; caller can normalize.
    """
    from datetime import datetime

    candidates = [
        exif_data.get("DateTimeOriginal"),
        exif_data.get("CreateDate"),
        exif_data.get("DateCreated"),
        exif_data.get("DateTimeDigitized"),
        exif_data.get("DateTime"),
    ]

    for value in candidates:
        if not value:
            continue
        # Some libraries may provide bytes
        if isinstance(value, bytes):
            try:
                value = value.decode("utf-8", errors="ignore")
            except Exception:
                value = str(value)

        s = str(value).strip()
        # Common EXIF format: YYYY:MM:DD HH:MM:SS
        try:
            if len(s) >= 10 and s[4] == ":" and s[7] == ":":
                # Replace first two ':' with '-' to make it ISO-like
                date_part = s[:10].replace(":", "-")
                time_part = s[11:19] if len(s) >= 19 else "00:00:00"
                return datetime.fromisoformat(f"{date_part} {time_part}")
            # Already ISO-like
            return datetime.fromisoformat(s.replace("T", " "))
        except Exception:
            continue

    return None


def _extract_xmp_dates_from_bytes(raw_bytes: bytes) -> Dict[str, str]:
    """Extract CreateDate / DateCreated from embedded XMP packet.

    Lightroom frequently stores capture date/time in XMP fields rather than EXIF
    for scanned images. We scan the bytes for a simple XMP packet and pull
    common fields. Returns a dict of string dates (ISO-like), which our
    existing _extract_taken_datetime() can parse.
    """
    try:
        text = None
        # Fast path: try decoding as utf-8 ignoring errors; XMP is XML utf-8
        try:
            text = raw_bytes.decode("utf-8", errors="ignore")
        except Exception:
            return {}

        # Patterns for typical XMP date tags
        # Examples: <xmp:CreateDate>1992-06-12T00:00:00</xmp:CreateDate>
        #           <photoshop:DateCreated>1992-06-12</photoshop:DateCreated>
        patterns = {
            "CreateDate": r"<xmp:CreateDate>([^<]+)</xmp:CreateDate>",
            "DateCreated": r"<photoshop:DateCreated>([^<]+)</photoshop:DateCreated>",
        }
        found: Dict[str, str] = {}
        for key, pat in patterns.items():
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                val = m.group(1).strip()
                # Normalize Z to +00:00 for fromisoformat compatibility
                if val.endswith("Z"):
                    val = val[:-1] + "+00:00"
                found[key] = val
        return found
    except Exception:
        return {}


def _get_image_dimensions(image_data: BytesIO, exif_data: Dict[str, Any]) -> Optional[tuple[int, int]]:
    """Get image width/height, corrected for EXIF orientation when available.

    Tries using the provided buffer; if it fails to open, returns None.
    """
    try:
        image = Image.open(image_data)
        width, height = image.size

        # Adjust for EXIF orientation if present (6 and 8 imply rotation 90/270)
        orientation = exif_data.get("Orientation")
        if isinstance(orientation, (int, str)):
            try:
                orientation_val = int(orientation)
            except Exception:
                orientation_val = None
            if orientation_val in (6, 8):
                width, height = height, width

        return int(width), int(height)
    except Exception:
        return None


def _convert_pil_value(value):
    """Convert PIL-specific types to JSON-serializable types."""
    # Try different import paths for IFDRational
    try:
        from PIL.ExifTags import IFDRational
    except ImportError:
        try:
            from PIL.TiffImagePlugin import IFDRational
        except ImportError:
            # If we can't import IFDRational, just check for the type by name
            IFDRational = None

    if IFDRational and isinstance(value, IFDRational):
        # Convert rational to float
        return float(value)
    elif hasattr(value, "numerator") and hasattr(value, "denominator"):
        # Handle rational-like objects
        try:
            return float(value)
        except Exception as e:
            logger.error(e)
            return str(value)
    elif isinstance(value, (tuple, list)):
        # Convert tuples/lists recursively
        return [_convert_pil_value(item) for item in value]
    elif isinstance(value, (bytes, bytearray)):
        # Convert bytes: prefer UTF-8 text if it doesn't contain control chars, otherwise hex
        b = bytes(value)
        try:
            text = b.decode("utf-8", errors="ignore")
        except Exception:
            text = None
        if text is None or any((ord(ch) < 32 and ch not in ("\t", "\n", "\r")) for ch in text):
            # Use hex string to avoid control characters like NUL which Postgres rejects
            return b.hex()
        return text
    elif hasattr(value, "__dict__"):
        # Convert objects to string representation
        return str(value)
    else:
        return value


def _sanitize_for_json(value):
    """Recursively convert values to JSON-serializable forms.

    - bytes/bytearray -> UTF-8 string (fallback to repr if decode fails)
    - dict -> sanitize keys and values
    - list/tuple -> sanitize each element
    """
    try:
        if isinstance(value, (bytes, bytearray)):
            try:
                return bytes(value).decode("utf-8", errors="ignore")
            except Exception:
                return str(value)
        if isinstance(value, str):
            # Remove NULs and non-printable control characters (keep tab/newline/carriage return)
            cleaned = value.replace("\x00", "")
            cleaned = "".join(ch for ch in cleaned if ord(ch) >= 32 or ch in ("\t", "\n", "\r"))
            return cleaned
        if isinstance(value, dict):
            return {str(k): _sanitize_for_json(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [_sanitize_for_json(v) for v in value]
        return value
    except Exception:
        return str(value)


def _format_aperture(fnumber: Optional[float]) -> Optional[str]:
    """Format aperture value."""
    if fnumber is None:
        return None
    return f"f/{fnumber:.1f}"


def _format_shutter_speed(exposure_time: Optional[float]) -> Optional[str]:
    """Format shutter speed value."""
    if exposure_time is None:
        return None
    if exposure_time < 1:
        return f"1/{int(1/exposure_time)}"
    return f"{exposure_time:.1f}s"


def _format_focal_length(focal_length: Optional[float]) -> Optional[str]:
    """Format focal length value."""
    if focal_length is None:
        return None
    return f"{int(focal_length)}mm"


def _extract_description_from_metadata(exif_data: Dict[str, Any]) -> Optional[str]:
    """Extract description from TIFF/IPTC metadata."""
    # Common fields that contain descriptions/captions
    description_fields = [
        "ImageDescription",  # TIFF ImageDescription
        "XPComment",  # Windows XP Comment
        "XPSubject",  # Windows XP Subject
        "XPTitle",  # Windows XP Title
        "UserComment",  # User comment
        "Caption",  # Caption field
        "Description",  # Description field
        "Comment",  # Comment field
    ]

    for field in description_fields:
        if exif_data.get(field):
            description = exif_data[field]

            # Handle different data types
            if isinstance(description, bytes):
                try:
                    description = description.decode("utf-8", errors="ignore")
                except Exception as e:
                    logger.warning(f"Failed to decode description: {e}")
                    description = str(description)
            else:
                description = str(description)

            # Clean up but preserve newlines
            description = description.strip()
            if description and len(description) > 0:
                # Replace different newline representations with standard \n
                description = description.replace("\r\n", "\n").replace("\r", "\n")
                return description

    return None


def _generate_keywords_from_exif(asset: Asset, exif_data: Dict[str, Any]) -> None:
    """Generate keywords from EXIF data."""
    keywords_to_create = []

    # Camera make/model keywords
    if exif_data.get("Make"):
        keywords_to_create.append(exif_data["Make"].lower())
    if exif_data.get("Model"):
        keywords_to_create.append(exif_data["Model"].lower())

    # Lens keywords
    if exif_data.get("LensModel"):
        keywords_to_create.append(exif_data["LensModel"].lower())

    # Create keyword tags and associate with asset
    for keyword_name in keywords_to_create:
        keyword, created = KeywordTag.objects.get_or_create(
            name=keyword_name, defaults={"slug": keyword_name.replace(" ", "-")}
        )

        if created:
            keyword.usage_count = 1
            keyword.save()
        else:
            keyword.usage_count += 1
            keyword.save()

        # Create asset-keyword relationship
        AssetKeyword.objects.get_or_create(asset=asset, keyword=keyword, defaults={"source": "exif", "confidence": 1.0})
