from django.contrib.auth.models import User
from django.db import models


class ServerConfiguration(models.Model):
    """Server configuration settings"""

    # Feature flags
    config_file = models.BooleanField(default=False)
    duplicate_detection = models.BooleanField(default=False)
    email = models.BooleanField(default=False)
    facial_recognition = models.BooleanField(default=True)
    import_faces = models.BooleanField(default=False)
    map = models.BooleanField(default=True)
    oauth = models.BooleanField(default=False)  # OAuth disabled by default
    oauth_auto_launch = models.BooleanField(default=False)  # OAuth auto-launch disabled
    password_login = models.BooleanField(default=True)
    reverse_geocoding = models.BooleanField(default=True)
    search = models.BooleanField(default=True)
    sidecar = models.BooleanField(default=True)
    smart_search = models.BooleanField(default=True)
    trash = models.BooleanField(default=True)

    # Server config
    external_domain = models.CharField(max_length=255, blank=True, default="")
    login_page_message = models.TextField(blank=True, default="")
    map_dark_style_url = models.URLField(blank=True, default="")
    map_light_style_url = models.URLField(blank=True, default="")
    oauth_button_text = models.CharField(max_length=100, blank=True, default="Login with OAuth")
    public_users = models.BooleanField(default=True)
    trash_days = models.IntegerField(default=30)
    user_delete_delay = models.IntegerField(default=7)

    # Media types
    supported_image_types = models.JSONField(default=list, blank=True)
    supported_sidecar_types = models.JSONField(default=list, blank=True)
    supported_video_types = models.JSONField(default=list, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Server Configuration"
        verbose_name_plural = "Server Configurations"

    def __str__(self):
        return f"Server Configuration (ID: {self.id})"

    @property
    def is_initialized(self):
        """Check if server is initialized (has users)"""
        return User.objects.exists()

    @property
    def is_onboarded(self):
        """Check if server is onboarded (has users)"""
        return User.objects.exists()

    @classmethod
    def get_or_create_default(cls):
        """Get or create default server configuration"""
        config, created = cls.objects.get_or_create(
            id=1,
            defaults={
                "supported_image_types": [
                    "image/jpeg",
                    "image/jpg",
                    "image/png",
                    "image/gif",
                    "image/bmp",
                    "image/tiff",
                    "image/webp",
                    "image/avif",
                ],
                "supported_sidecar_types": ["application/xml", "text/xml", "application/json"],
                "supported_video_types": [
                    "video/mp4",
                    "video/avi",
                    "video/mov",
                    "video/wmv",
                    "video/flv",
                    "video/webm",
                    "video/mkv",
                    "video/3gp",
                ],
            },
        )
        return config
