from django.contrib import admin

from .models import Album, AlbumAsset, AlbumShare


@admin.register(Album)
class AlbumAdmin(admin.ModelAdmin):
    list_display = ["title", "owner", "created_at", "updated_at"]
    list_filter = ["created_at", "owner"]
    search_fields = ["title", "description", "owner__username"]
    readonly_fields = ["id", "created_at", "updated_at"]
    fieldsets = [
        (None, {"fields": ["id", "owner", "title", "description", "cover_asset"]}),
        ("Timestamps", {"fields": ["created_at", "updated_at"]}),
    ]


@admin.register(AlbumAsset)
class AlbumAssetAdmin(admin.ModelAdmin):
    list_display = ["album", "asset", "order", "created_at"]
    list_filter = ["created_at"]
    search_fields = ["album__title"]
    readonly_fields = ["id", "created_at"]


@admin.register(AlbumShare)
class AlbumShareAdmin(admin.ModelAdmin):
    list_display = ["album", "shared_with", "permission_level", "shared_by", "created_at"]
    list_filter = ["permission_level", "created_at"]
    search_fields = ["album__title", "shared_with__username", "shared_by__username"]
    readonly_fields = ["id", "created_at", "updated_at"]
    fieldsets = [
        (None, {"fields": ["id", "album", "shared_with", "permission_level", "shared_by"]}),
        ("Timestamps", {"fields": ["created_at", "updated_at"]}),
    ]
