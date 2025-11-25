"""
Upload services for handling file uploads to local filesystem storage.

Supports multiple storage backends with different base paths,
allowing users to organize photos across different drives or locations.
"""

import hashlib
import os
import uuid
from datetime import datetime

from django.core.exceptions import ValidationError
from PIL import Image

from .models import Asset, StorageBackend, StorageBucket


class UploadService:
    """
    Service for handling file uploads to local filesystem storage.
    """

    def __init__(self, storage_backend=None):
        """Initialize with a specific storage backend or use default."""
        if storage_backend is None:
            storage_backend = StorageBackend.objects.filter(is_default=True).first()
            if not storage_backend:
                raise ValidationError("No default storage backend configured")

        self.backend = storage_backend

    def ensure_storage_directory(self, directory_path):
        """
        Ensure a storage directory exists, creating it if necessary.

        Args:
            directory_path (Path): Path to directory

        Raises:
            ValidationError: If directory cannot be created or is not writable
        """
        try:
            directory_path.mkdir(parents=True, exist_ok=True)
            # Check if directory is writable
            if not os.access(directory_path, os.W_OK):
                raise ValidationError(f"Storage directory is not writable: {directory_path}")
        except OSError as e:
            raise ValidationError(f"Failed to create storage directory: {e}")

    def generate_upload_key(self, filename):
        """
        Generate a unique storage key with date-based organization.

        Args:
            filename (str): Original filename

        Returns:
            str: Generated storage key in format YYYY/MM/DD/{uuid}.{ext}
        """
        # Extract file extension
        if "." in filename:
            name, ext = filename.rsplit(".", 1)
            ext = f".{ext.lower()}"
        else:
            ext = ""

        # Generate unique ID
        unique_id = str(uuid.uuid4())

        # Organize by date
        date_prefix = datetime.now().strftime("%Y/%m/%d")

        return f"{date_prefix}/{unique_id}{ext}"

    def get_file_path(self, asset):
        """
        Get the absolute filesystem path for an asset.

        Args:
            asset (Asset): Asset object

        Returns:
            Path: Absolute path to the file

        Raises:
            ValidationError: If base path is not configured
        """
        base_path = asset.storage_bucket.backend.get_base_path()
        bucket_purpose = asset.storage_bucket.purpose
        storage_key = asset.storage_key

        return base_path / bucket_purpose / storage_key

    def save_uploaded_file(self, file, bucket, metadata=None, owner=None):
        """
        Save an uploaded file to local filesystem storage.

        This method handles:
        - File hash calculation for deduplication
        - Image dimension extraction
        - Directory creation
        - File saving to disk
        - Asset record creation
        - Triggering of async tasks for metadata/thumbnails

        Args:
            file: Django UploadedFile object
            bucket (StorageBucket): Target storage bucket
            metadata (dict, optional): Additional metadata
            owner (User, optional): User who owns this asset

        Returns:
            Asset: Created or existing asset record

        Raises:
            ValidationError: If storage operations fail
        """
        # Read file content for hashing
        file_content = file.read()
        file.seek(0)  # Reset for later use

        # Calculate SHA256 hash for deduplication
        sha256_hash = hashlib.sha256(file_content).hexdigest()

        # Check if file already exists
        existing = Asset.objects.filter(sha256=sha256_hash).first()
        if existing:
            return existing

        # Generate storage key with date organization
        storage_key = self.generate_upload_key(file.name)

        # Get full filesystem path
        base_path = bucket.backend.get_base_path()
        full_path = base_path / bucket.purpose / storage_key

        # Ensure directory exists
        self.ensure_storage_directory(full_path.parent)

        # Extract image dimensions
        width = 0
        height = 0
        try:
            img = Image.open(file)
            width, height = img.size
            file.seek(0)  # Reset after PIL reads it
        except Exception:
            # Not an image or couldn't read dimensions
            pass

        # Save file to disk
        try:
            with open(full_path, "wb") as dest:
                dest.write(file_content)
        except OSError as e:
            raise ValidationError(f"Failed to save file to storage: {e}")

        # Create asset record
        metadata = metadata or {}
        
        if not owner:
            raise ValidationError("Owner is required for asset creation")
        
        asset = Asset.objects.create(
            sha256=sha256_hash,
            owner=owner,
            storage_bucket=bucket,
            storage_key=storage_key,
            mime_type=file.content_type or "application/octet-stream",
            width=width,
            height=height,
            taken_at=metadata.get("taken_at"),
            description=metadata.get("description", ""),
        )

        # Trigger metadata processing
        try:
            from metadata.tasks import process_asset_metadata

            process_asset_metadata.delay(str(asset.id))
        except ImportError:
            pass

        # Trigger thumbnail generation
        try:
            from .tasks import generate_asset_thumbnails

            generate_asset_thumbnails.delay(str(asset.id))
        except ImportError:
            pass

        return asset


def get_default_upload_bucket(purpose="originals"):
    """Get the default upload bucket for the specified purpose."""
    try:
        # Get default backend
        backend = StorageBackend.objects.filter(is_default=True, is_active=True).first()
        if not backend:
            raise ValidationError("No default storage backend configured")

        # Get bucket for purpose
        bucket = StorageBucket.objects.filter(backend=backend, purpose=purpose, is_active=True).first()

        if not bucket:
            raise ValidationError(f"No {purpose} bucket configured for default backend")

        return bucket

    except StorageBucket.DoesNotExist:
        raise ValidationError(f"No {purpose} bucket found")
