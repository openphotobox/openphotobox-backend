"""
Custom pagination classes for OpenPhotobox API.
"""
from rest_framework.pagination import CursorPagination


class AssetCursorPagination(CursorPagination):
    """
    Custom cursor pagination for assets.
    Orders by taken_at (descending) then created_at (descending).
    """
    page_size = 50
    page_size_query_param = 'limit'
    max_page_size = 200
    ordering = ['-taken_at', '-created_at']  # Match the view ordering exactly
    cursor_query_param = 'cursor'
    page_query_description = 'Cursor for pagination'


class DefaultCursorPagination(CursorPagination):
    """
    Default cursor pagination for other models.
    Orders by created_at (descending).
    """
    page_size = 50
    page_size_query_param = 'limit'
    max_page_size = 200
    ordering = '-created_at'
    cursor_query_param = 'cursor'
    page_query_description = 'Cursor for pagination'
