import uuid
import hashlib
import secrets
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone


class UserSharingProfile(models.Model):
    """
    Extended profile for users who can receive shared photo albums.
    One-to-one with Django User model to store sharing-specific settings.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='sharing_profile')
    
    # Sharing notes (for admin reference)
    notes = models.TextField(blank=True)
    
    # Feature flags (can be overridden per link)
    default_show_faces = models.BooleanField(default=True)
    default_show_names = models.BooleanField(default=True)
    default_allow_downloads = models.BooleanField(default=True)
    
    # Admin tracking
    created_by = models.ForeignKey(
        User, 
        on_delete=models.CASCADE, 
        related_name='created_sharing_profiles'
    )
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'user_sharing_profiles'
        ordering = ['user__first_name', 'user__last_name', 'user__username']
    
    def __str__(self):
        return f"Sharing profile for {self.user.get_full_name() or self.user.username}"
    
    @property
    def display_name(self):
        """Get the user's display name."""
        return self.user.get_full_name() or self.user.username
    
    @property
    def email(self):
        """Get the user's email."""
        return self.user.email


class AccessGrant(models.Model):
    """
    Grants a user access to specific albums or people.
    Usually grants are to albums; person grants are optional for advanced filtering.
    """
    GRANT_TYPES = [
        ('album', 'Album Access'),
        ('person', 'Person Access'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='access_grants')
    
    # What is being granted access to (exactly one should be set)
    album = models.ForeignKey(
        'assets.Album',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='access_grants'
    )
    person = models.ForeignKey(
        'people.Person',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='access_grants'
    )
    
    # Grant type for easier querying
    grant_type = models.CharField(max_length=10, choices=GRANT_TYPES)
    
    # Admin tracking
    granted_by = models.ForeignKey(User, on_delete=models.CASCADE)
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'access_grants'
        indexes = [
            models.Index(fields=['user', 'grant_type']),
            models.Index(fields=['album']),
            models.Index(fields=['person']),
        ]
        constraints = [
            # Ensure exactly one of album or person is set
            models.CheckConstraint(
                check=(
                    (models.Q(album__isnull=False) & models.Q(person__isnull=True)) |
                    (models.Q(album__isnull=True) & models.Q(person__isnull=False))
                ),
                name='exactly_one_grant_target'
            ),
            # Unique grants per user
            models.UniqueConstraint(
                fields=['user', 'album'],
                name='unique_user_album_grant',
                condition=models.Q(album__isnull=False)
            ),
            models.UniqueConstraint(
                fields=['user', 'person'],
                name='unique_user_person_grant',
                condition=models.Q(person__isnull=False)
            ),
        ]
    
    def save(self, *args, **kwargs):
        # Auto-set grant_type based on what's populated
        if self.album:
            self.grant_type = 'album'
        elif self.person:
            self.grant_type = 'person'
        super().save(*args, **kwargs)
    
    def __str__(self):
        target = self.album.title if self.album else self.person.display_name
        user_name = self.user.get_full_name() or self.user.username
        return f"{user_name} → {target}"


class UserAsset(models.Model):
    """
    Materialized view of all assets a user can access via sharing.
    This is the union of all assets from their album/person grants.
    Updated automatically when grants or album contents change.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='shared_assets')
    asset = models.ForeignKey('assets.Asset', on_delete=models.CASCADE, related_name='shared_with_users')
    
    # Track which grant(s) provide access to this asset
    source_grants = models.JSONField(default=list)  # List of grant IDs
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'user_assets'
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'asset'],
                name='unique_user_asset'
            )
        ]
        indexes = [
            models.Index(fields=['user', 'asset']),
            models.Index(fields=['asset']),
        ]
    
    def __str__(self):
        user_name = self.user.get_full_name() or self.user.username
        return f"{user_name} → {self.asset.id}"


class SharingLink(models.Model):
    """
    Secure, expiring tokens for loginless access to user sharing portals.
    Tokens are hashed for security; only the hash is stored.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sharing_links')
    
    # Security: store only hashes, never plaintext tokens
    token_hash = models.CharField(max_length=64, unique=True, db_index=True)
    password_hash = models.CharField(max_length=128, blank=True)  # Optional password protection
    
    # Feature flags (override user defaults from sharing profile)
    show_faces = models.BooleanField(null=True, blank=True)  # None = use user default
    show_names = models.BooleanField(null=True, blank=True)
    allow_downloads = models.BooleanField(null=True, blank=True)
    
    # Expiration and usage tracking
    expires_at = models.DateTimeField(null=True, blank=True)
    access_count = models.PositiveIntegerField(default=0)
    last_accessed_at = models.DateTimeField(null=True, blank=True)
    
    # Admin tracking
    created_by = models.ForeignKey(User, on_delete=models.CASCADE)
    name = models.CharField(max_length=255, blank=True)  # Optional name for admin reference
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'sharing_links'
        indexes = [
            models.Index(fields=['token_hash']),
            models.Index(fields=['expires_at']),
            models.Index(fields=['user']),
        ]
    
    @classmethod
    def create_link(cls, user, created_by, expires_at=None, password=None, **kwargs):
        """
        Create a new sharing link with secure token generation.
        Returns (link_instance, plaintext_token).
        """
        # Generate secure random token (32 bytes = 256 bits)
        plaintext_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(plaintext_token.encode()).hexdigest()
        
        # Hash password if provided
        password_hash = ''
        if password:
            password_hash = hashlib.sha256(password.encode()).hexdigest()
        
        link = cls.objects.create(
            user=user,
            created_by=created_by,
            token_hash=token_hash,
            password_hash=password_hash,
            expires_at=expires_at,
            **kwargs
        )
        
        return link, plaintext_token
    
    @classmethod
    def get_by_token(cls, plaintext_token):
        """
        Retrieve a link by its plaintext token (constant-time comparison).
        Returns None if not found or expired.
        """
        token_hash = hashlib.sha256(plaintext_token.encode()).hexdigest()
        
        try:
            link = cls.objects.select_related('user').get(token_hash=token_hash)
            
            # Check expiration
            if link.expires_at and link.expires_at < timezone.now():
                return None
                
            return link
        except cls.DoesNotExist:
            return None
    
    def verify_password(self, password):
        """
        Verify password against stored hash (constant-time comparison).
        Returns True if password is correct or no password is set.
        """
        if not self.password_hash:
            return True  # No password required
        
        password_hash = hashlib.sha256(password.encode()).hexdigest()
        return secrets.compare_digest(self.password_hash, password_hash)
    
    def record_access(self):
        """Record that this link was accessed."""
        self.access_count += 1
        self.last_accessed_at = timezone.now()
        self.save(update_fields=['access_count', 'last_accessed_at'])
    
    def get_effective_flags(self):
        """Get the effective feature flags, falling back to user defaults."""
        # Get or create sharing profile for the user
        profile, _ = UserSharingProfile.objects.get_or_create(
            user=self.user,
            defaults={'created_by': self.created_by}
        )
        
        return {
            'show_faces': self.show_faces if self.show_faces is not None else profile.default_show_faces,
            'show_names': self.show_names if self.show_names is not None else profile.default_show_names,
            'allow_downloads': self.allow_downloads if self.allow_downloads is not None else profile.default_allow_downloads,
        }
    
    def __str__(self):
        name = self.name or f"Link {str(self.id)[:8]}"
        user_name = self.user.get_full_name() or self.user.username
        return f"{name} → {user_name}"


class UserAssetRebuildLog(models.Model):
    """
    Log of UserAsset rebuilds for monitoring and debugging.
    """
    STATUS_CHOICES = [
        ('running', 'Running'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        User, 
        on_delete=models.CASCADE, 
        null=True, 
        blank=True,
        related_name='asset_rebuild_logs'
    )  # None = full rebuild
    
    # What triggered the rebuild
    trigger_type = models.CharField(max_length=50)  # 'grant_created', 'grant_deleted', 'album_changed', 'manual'
    trigger_details = models.JSONField(default=dict)
    
    # Results
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='running')
    assets_added = models.PositiveIntegerField(default=0)
    assets_removed = models.PositiveIntegerField(default=0)
    error_message = models.TextField(blank=True)
    
    # Timing
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        db_table = 'user_asset_rebuild_logs'
        ordering = ['-started_at']
        indexes = [
            models.Index(fields=['user', '-started_at']),
            models.Index(fields=['status']),
        ]
    
    def __str__(self):
        user_name = (self.user.get_full_name() or self.user.username) if self.user else "All users"
        return f"Rebuild {user_name} - {self.trigger_type} ({self.status})"
