"""
Upload services for handling file uploads to cloud storage.

Primary focus: S3/MinIO with presigned URLs (first-class citizens)
- Efficient direct-to-cloud uploads
- No server bandwidth usage
- Scalable and reliable

LocalFS support is provided for development/testing only.
"""

import hashlib
import uuid
from datetime import datetime, timedelta

import boto3
from botocore.exceptions import ClientError, NoCredentialsError
from django.core.exceptions import ValidationError

from .models import Asset, StorageBackend, StorageBucket, UploadBatch


class UploadService:
    """
    Service for handling file uploads to S3/MinIO with presigned URLs.
    """

    def __init__(self, storage_backend=None):
        """Initialize with a specific storage backend or use default."""
        if storage_backend is None:
            storage_backend = StorageBackend.objects.filter(is_default=True).first()
            if not storage_backend:
                raise ValidationError("No default storage backend configured")

        self.backend = storage_backend
        self._client = None

    @property
    def client(self):
        """Get or create S3/MinIO client."""
        if self._client is None:
            config = self.backend.config or {}

            # Get credentials from config or environment
            aws_access_key_id = config.get("aws_access_key_id") or "minio"  # Default for MinIO
            aws_secret_access_key = config.get("aws_secret_access_key") or "minio123"

            client_config = {
                "aws_access_key_id": aws_access_key_id,
                "aws_secret_access_key": aws_secret_access_key,
            }

            if self.backend.endpoint_url:
                client_config["endpoint_url"] = self.backend.endpoint_url

            if self.backend.region:
                client_config["region_name"] = self.backend.region

            self._client = boto3.client("s3", **client_config)

        return self._client

    def generate_presigned_upload_url(self, bucket, file_key, content_type, expires_in=3600, sha256=None):
        """
        Generate a presigned URL for uploading a file to S3/MinIO.

        Args:
            bucket (StorageBucket): Target storage bucket
            file_key (str): Object key/path in the bucket
            content_type (str): MIME type of the file
            expires_in (int): URL expiration time in seconds (default 1 hour)

        Returns:
            dict: Contains 'upload_url', 'fields', and 'file_key'
        """
        try:
            # Generate presigned POST for direct browser uploads
            conditions = [
                {"Content-Type": content_type},
                ["content-length-range", 1, 100 * 1024 * 1024],  # 1 byte to 100MB
            ]

            fields = {"Content-Type": content_type}

            # If client computed a SHA-256, store it as object metadata for reliable deduplication
            # S3/MinIO expose user metadata via x-amz-meta-<key>, returned in HeadObject Metadata
            if sha256:
                fields["x-amz-meta-sha256"] = sha256
                conditions.append({"x-amz-meta-sha256": sha256})

            response = self.client.generate_presigned_post(
                Bucket=bucket.name, Key=file_key, Fields=fields, Conditions=conditions, ExpiresIn=expires_in
            )

            return {
                "upload_url": response["url"],
                "fields": response["fields"],
                "file_key": file_key,
                "bucket_id": str(bucket.id),
                "expires_at": (datetime.now() + timedelta(seconds=expires_in)).isoformat(),
            }

        except (ClientError, NoCredentialsError) as e:
            raise ValidationError(f"Failed to generate presigned URL: {str(e)}")

    def generate_upload_key(self, filename, upload_batch_id=None):
        """
        Generate a unique storage key for the uploaded file.

        Args:
            filename (str): Original filename
            upload_batch_id (str, optional): Upload batch ID for organization

        Returns:
            str: Generated storage key
        """
        # Extract file extension
        if "." in filename:
            name, ext = filename.rsplit(".", 1)
            ext = f".{ext.lower()}"
        else:
            ext = ""

        # Generate unique ID
        unique_id = str(uuid.uuid4())

        # Organize by date and batch
        date_prefix = datetime.now().strftime("%Y/%m/%d")

        if upload_batch_id:
            return f"{date_prefix}/batch_{upload_batch_id}/{unique_id}{ext}"
        else:
            return f"{date_prefix}/{unique_id}{ext}"

    def create_upload_batch(self, user, name=None, description=""):
        """Create a new upload batch for organizing uploads."""
        batch = UploadBatch.objects.create(
            created_by=user,
            name=name or f"Upload {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            description=description,
            status="pending",
        )
        return batch

    def complete_upload(self, file_key, bucket_id, upload_batch_id=None, metadata=None):
        """
        Complete the upload process by creating an Asset record.
        This should be called after the file has been successfully uploaded to S3/MinIO.

        Args:
            file_key (str): The storage key of the uploaded file
            bucket_id (str): Storage bucket ID
            upload_batch_id (str, optional): Upload batch ID
            metadata (dict, optional): Additional metadata about the file

        Returns:
            Asset: Created asset record
        """
        try:
            bucket = StorageBucket.objects.get(id=bucket_id)
        except StorageBucket.DoesNotExist:
            raise ValidationError(f"Storage bucket {bucket_id} not found")

        # Get file info from S3/MinIO
        try:
            response = self.client.head_object(Bucket=bucket.name, Key=file_key)
            content_type = response["ContentType"]
            etag = response["ETag"].strip('"')  # Remove quotes from ETag
            # Prefer client-provided sha256 stored in object metadata when available
            obj_metadata = response.get("Metadata", {}) or {}
            sha_from_metadata = obj_metadata.get("sha256")

        except ClientError as e:
            raise ValidationError(f"File not found in storage: {str(e)}")

        # Extract metadata
        metadata = metadata or {}
        width = metadata.get("width", 0)
        height = metadata.get("height", 0)
        taken_at = metadata.get("taken_at")

        # Use metadata SHA-256 when present; fall back to ETag (may be MD5/multipart token)
        content_hash = sha_from_metadata or etag

        # If an asset with this hash already exists, return it instead of creating
        existing = Asset.objects.filter(sha256=content_hash).first()
        if existing:
            return existing

        # Create asset record
        asset = Asset.objects.create(
            sha256=content_hash,
            storage_bucket=bucket,
            storage_key=file_key,
            mime_type=content_type,
            width=width,
            height=height,
            taken_at=taken_at,
            description=metadata.get("description", ""),
            visibility="shared",  # Default visibility
        )

        # Update upload batch if provided
        if upload_batch_id:
            try:
                batch = UploadBatch.objects.get(id=upload_batch_id)
                batch.total_files += 1
                batch.processed_files += 1
                batch.status = "completed" if batch.processed_files >= batch.total_files else "processing"
                batch.save()
            except UploadBatch.DoesNotExist:
                pass  # Batch might have been deleted

        # Trigger metadata processing and thumbnail generation
        try:
            from metadata.tasks import process_asset_metadata

            process_asset_metadata.delay(str(asset.id))
        except ImportError:
            pass  # Metadata app might not be available

        # Trigger thumbnail generation
        try:
            from .tasks import generate_asset_thumbnails

            generate_asset_thumbnails.delay(str(asset.id))
        except ImportError:
            pass  # Tasks might not be available

        return asset

    def handle_direct_upload(self, file, file_key, bucket_id, upload_batch_id=None):
        """
        Handle direct file upload for LocalFS storage backend.

        Args:
            file: Django UploadedFile object
            file_key (str): The storage key for the file
            bucket_id (str): Storage bucket ID
            upload_batch_id (str, optional): Upload batch ID

        Returns:
            Asset: Created asset record
        """
        try:
            bucket = StorageBucket.objects.get(id=bucket_id)
        except StorageBucket.DoesNotExist:
            raise ValidationError(f"Storage bucket {bucket_id} not found")

        # Verify this is a LocalFS backend
        if bucket.backend.backend_type != "local":
            raise ValidationError("Direct upload only supported for LocalFS backends")

        # For LocalFS, we would save the file to the local filesystem here
        # This is a placeholder implementation - you'd need to implement the actual
        # file saving logic based on your LocalFS backend configuration

        # Calculate file hash for deduplication
        file_content = file.read()
        file.seek(0)  # Reset file pointer
        sha256_hash = hashlib.sha256(file_content).hexdigest()

        # If an asset with this hash already exists, return it
        existing = Asset.objects.filter(sha256=sha256_hash).first()
        if existing:
            return existing

        # Create asset record
        asset = Asset.objects.create(
            sha256=sha256_hash,
            storage_bucket=bucket,
            storage_key=file_key,
            mime_type=file.content_type,
            width=0,  # Would extract from image metadata in real implementation
            height=0,  # Would extract from image metadata in real implementation
            taken_at=None,  # Would extract from EXIF in real implementation
            description="",
            visibility="shared",  # Default visibility
        )

        # Update upload batch if provided
        if upload_batch_id:
            try:
                batch = UploadBatch.objects.get(id=upload_batch_id)
                batch.total_files += 1
                batch.processed_files += 1
                batch.status = "completed" if batch.processed_files >= batch.total_files else "processing"
                batch.save()
            except UploadBatch.DoesNotExist:
                pass  # Batch might have been deleted

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
