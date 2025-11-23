from django.contrib import admin
from .models import ServerConfiguration


@admin.register(ServerConfiguration)
class ServerConfigurationAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'is_initialized', 'is_onboarded', 'password_login', 
        'oauth', 'facial_recognition', 'created_at'
    ]
    list_filter = [
        'is_initialized', 'is_onboarded', 'password_login', 'oauth',
        'facial_recognition', 'public_users'
    ]
    fieldsets = (
        ('Feature Flags', {
            'fields': (
                'config_file', 'duplicate_detection', 'email', 'facial_recognition',
                'import_faces', 'map', 'oauth', 'oauth_auto_launch', 'password_login',
                'reverse_geocoding', 'search', 'sidecar', 'smart_search', 'trash'
            )
        }),
        ('Server Configuration', {
            'fields': (
                'external_domain', 'is_initialized', 'is_onboarded', 
                'login_page_message', 'map_dark_style_url', 'map_light_style_url',
                'oauth_button_text', 'public_users', 'trash_days', 'user_delete_delay'
            )
        }),
        ('Media Types', {
            'fields': (
                'supported_image_types', 'supported_sidecar_types', 'supported_video_types'
            )
        }),
    )
    readonly_fields = ['created_at', 'updated_at']
