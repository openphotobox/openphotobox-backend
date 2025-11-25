"""
Permission helper functions for album-based sharing.

These functions determine what resources (albums, assets, people, faces, etc.) 
a user can access based on album ownership and sharing permissions.
"""

from django.db.models import Q, QuerySet


def get_accessible_albums(user) -> QuerySet:
    """
    Get all albums that a user can access.
    
    This includes:
    - Albums the user owns
    - Albums that have been shared with the user
    
    Args:
        user: The Django User instance
        
    Returns:
        QuerySet of Album objects the user can access
    """
    from albums.models import Album
    
    if not user or not user.is_authenticated:
        return Album.objects.none()
    
    return Album.objects.filter(
        Q(owner=user) | Q(shares__shared_with=user)
    ).distinct()


def get_accessible_assets(user) -> QuerySet:
    """
    Get all assets that a user can access.
    
    This includes:
    - Assets in albums the user owns
    - Assets in albums shared with the user
    - Assets owned by the user that aren't in any album
    
    Args:
        user: The Django User instance
        
    Returns:
        QuerySet of Asset objects the user can access
    """
    from assets.models import Asset
    from albums.models import AlbumAsset
    
    if not user or not user.is_authenticated:
        return Asset.objects.none()
    
    # Get accessible albums
    accessible_albums = get_accessible_albums(user)
    
    # Get assets in accessible albums
    assets_in_albums = Asset.objects.filter(
        id__in=AlbumAsset.objects.filter(album__in=accessible_albums).values_list("asset_id", flat=True)
    )
    
    # Get assets owned by user that aren't in any album
    assets_not_in_albums = Asset.objects.filter(
        owner=user
    ).exclude(
        id__in=AlbumAsset.objects.values_list("asset_id", flat=True)
    )
    
    # Combine both querysets
    return assets_in_albums | assets_not_in_albums


def can_view_album(user, album) -> bool:
    """
    Check if a user can view an album.
    
    Args:
        user: The Django User instance
        album: The Album instance
        
    Returns:
        True if user can view the album, False otherwise
    """
    if not user or not user.is_authenticated:
        return False
    
    # Owner can always view
    if album.owner == user:
        return True
    
    # Check if album is shared with user
    return album.shares.filter(shared_with=user).exists()


def can_contribute_to_album(user, album) -> bool:
    """
    Check if a user can contribute to an album (add/remove photos).
    
    Args:
        user: The Django User instance
        album: The Album instance
        
    Returns:
        True if user can contribute to the album, False otherwise
    """
    if not user or not user.is_authenticated:
        return False
    
    # Owner can always contribute
    if album.owner == user:
        return True
    
    # Check if user has "contribute" permission via share
    share = album.shares.filter(shared_with=user).first()
    return share and share.permission_level == "contribute"


def can_edit_album(user, album) -> bool:
    """
    Check if a user can edit an album (change title, description, etc.).
    Only the owner can edit album metadata.
    
    Args:
        user: The Django User instance
        album: The Album instance
        
    Returns:
        True if user can edit the album, False otherwise
    """
    if not user or not user.is_authenticated:
        return False
    
    return album.owner == user


def can_view_asset(user, asset) -> bool:
    """
    Check if a user can view an asset.
    
    Args:
        user: The Django User instance
        asset: The Asset instance
        
    Returns:
        True if user can view the asset, False otherwise
    """
    from albums.models import AlbumAsset
    
    if not user or not user.is_authenticated:
        return False
    
    # Owner can always view
    if asset.owner == user:
        return True
    
    # Check if asset is in any album the user can access
    accessible_albums = get_accessible_albums(user)
    return AlbumAsset.objects.filter(
        asset=asset,
        album__in=accessible_albums
    ).exists()


def can_edit_asset(user, asset) -> bool:
    """
    Check if a user can edit an asset (change description, etc.).
    Only the owner can edit asset metadata.
    
    Args:
        user: The Django User instance
        asset: The Asset instance
        
    Returns:
        True if user can edit the asset, False otherwise
    """
    if not user or not user.is_authenticated:
        return False
    
    return asset.owner == user


def filter_accessible_asset_ids(user, asset_ids):
    """
    Filter a list of asset IDs to only those the user can access.
    
    This is useful for bulk operations where you want to silently
    filter out inaccessible items rather than error.
    
    Args:
        user: The Django User instance
        asset_ids: List/set of asset IDs
        
    Returns:
        Set of asset IDs the user can access
    """
    if not asset_ids:
        return set()
    
    accessible_assets = get_accessible_assets(user)
    return set(
        accessible_assets.filter(id__in=asset_ids).values_list("id", flat=True)
    )

