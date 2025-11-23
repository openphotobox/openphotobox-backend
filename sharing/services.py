"""
Services for managing recipient asset materialized views and sharing logic.
"""
import logging
from typing import List, Optional, Set
from django.db import transaction
from django.utils import timezone
from django.contrib.auth.models import User
from .models import UserSharingProfile, AccessGrant, UserAsset, UserAssetRebuildLog

logger = logging.getLogger(__name__)


class UserAssetBuilder:
    """
    Service for building and maintaining the UserAsset materialized view.
    This ensures users only see assets they have access to via grants.
    """
    
    @classmethod
    def rebuild_for_user(
        cls, 
        user: User, 
        trigger_type: str = 'manual',
        trigger_details: dict = None
    ) -> UserAssetRebuildLog:
        """
        Rebuild all UserAsset entries for a specific user.
        Returns a log entry tracking the rebuild progress.
        """
        log_entry = UserAssetRebuildLog.objects.create(
            user=user,
            trigger_type=trigger_type,
            trigger_details=trigger_details or {}
        )
        
        try:
            with transaction.atomic():
                # Get all asset IDs this user should have access to
                target_asset_ids = cls._compute_user_assets(user)
                
                # Get current asset IDs
                current_asset_ids = set(
                    UserAsset.objects.filter(user=user)
                    .values_list('asset_id', flat=True)
                )
                
                # Calculate differences
                to_add = target_asset_ids - current_asset_ids
                to_remove = current_asset_ids - target_asset_ids
                
                # Remove assets no longer accessible
                if to_remove:
                    removed_count = UserAsset.objects.filter(
                        user=user,
                        asset_id__in=to_remove
                    ).delete()[0]
                    log_entry.assets_removed = removed_count
                
                # Add new assets
                if to_add:
                    # Get grant context for each asset
                    user_assets = []
                    for asset_id in to_add:
                        source_grants = cls._get_grants_for_asset(user, asset_id)
                        user_assets.append(
                            UserAsset(
                                user=user,
                                asset_id=asset_id,
                                source_grants=[str(g.id) for g in source_grants]
                            )
                        )
                    
                    UserAsset.objects.bulk_create(user_assets)
                    log_entry.assets_added = len(user_assets)
                
                # Mark as completed
                log_entry.status = 'completed'
                log_entry.completed_at = timezone.now()
                log_entry.save()
                
                user_name = user.get_full_name() or user.username
                logger.info(
                    f"Rebuilt assets for {user_name}: "
                    f"+{log_entry.assets_added}, -{log_entry.assets_removed}"
                )
                
        except Exception as e:
            log_entry.status = 'failed'
            log_entry.error_message = str(e)
            log_entry.completed_at = timezone.now()
            log_entry.save()
            user_name = user.get_full_name() or user.username
            logger.error(f"Failed to rebuild assets for {user_name}: {e}")
            raise
        
        return log_entry
    
    @classmethod
    def rebuild_all(cls, trigger_type: str = 'manual') -> List[UserAssetRebuildLog]:
        """
        Rebuild UserAsset entries for all users with sharing grants.
        """
        logs = []
        # Get all users who have access grants
        users_with_grants = User.objects.filter(access_grants__isnull=False).distinct()
        for user in users_with_grants:
            log = cls.rebuild_for_user(
                user, 
                trigger_type=trigger_type,
                trigger_details={'scope': 'all_users'}
            )
            logs.append(log)
        return logs
    
    @classmethod
    def handle_grant_created(cls, grant: AccessGrant) -> UserAssetRebuildLog:
        """
        Handle a new AccessGrant by updating the user's asset list.
        """
        return cls.rebuild_for_user(
            grant.user,
            trigger_type='grant_created',
            trigger_details={
                'grant_id': str(grant.id),
                'grant_type': grant.grant_type,
                'target_id': str(grant.album_id or grant.person_id)
            }
        )
    
    @classmethod
    def handle_grant_deleted(cls, user: User, grant_details: dict) -> UserAssetRebuildLog:
        """
        Handle AccessGrant deletion by updating the user's asset list.
        """
        return cls.rebuild_for_user(
            user,
            trigger_type='grant_deleted',
            trigger_details=grant_details
        )
    
    @classmethod
    def handle_album_changed(cls, album_id: str) -> List[UserAssetRebuildLog]:
        """
        Handle changes to an album's contents by updating all affected users.
        """
        # Find all users with grants to this album
        affected_users = User.objects.filter(
            access_grants__album_id=album_id
        ).distinct()
        
        logs = []
        for user in affected_users:
            log = cls.rebuild_for_user(
                user,
                trigger_type='album_changed',
                trigger_details={'album_id': album_id}
            )
            logs.append(log)
        
        return logs
    
    @classmethod
    def _compute_user_assets(cls, user: User) -> Set[str]:
        """
        Compute the complete set of asset IDs a user should have access to
        based on their current grants.
        """
        from assets.models import Asset
        from people.models import Face
        
        asset_ids = set()
        
        # Get assets from album grants
        album_grants = user.access_grants.filter(grant_type='album').select_related('album')
        for grant in album_grants:
            if grant.album:
                # Get all assets in this album
                album_asset_ids = grant.album.albumasset_set.values_list('asset_id', flat=True)
                asset_ids.update(str(aid) for aid in album_asset_ids)
        
        # Get assets from person grants
        person_grants = user.access_grants.filter(grant_type='person').select_related('person')
        for grant in person_grants:
            if grant.person:
                # Get all assets where this person appears
                person_asset_ids = Face.objects.filter(
                    person=grant.person
                ).values_list('asset_id', flat=True)
                asset_ids.update(str(aid) for aid in person_asset_ids)
        
        return asset_ids
    
    @classmethod
    def _get_grants_for_asset(cls, user: User, asset_id: str) -> List[AccessGrant]:
        """
        Get all grants that provide access to a specific asset for a user.
        """
        from people.models import Face
        
        grants = []
        
        # Check album grants
        album_grants = user.access_grants.filter(
            grant_type='album',
            album__albumasset__asset_id=asset_id
        ).select_related('album')
        grants.extend(album_grants)
        
        # Check person grants
        person_grants = user.access_grants.filter(
            grant_type='person',
            person__faces__asset_id=asset_id
        ).select_related('person')
        grants.extend(person_grants)
        
        return grants


class SharingQueryService:
    """
    Service for executing sharing-related queries with proper user scoping.
    All queries ensure users only see data they have access to via sharing.
    """
    
    @classmethod
    def get_user_assets(
        cls,
        user: User,
        cursor_taken_at=None,
        limit: int = 200
    ):
        """
        Get assets visible to a user via sharing, ordered by taken_at (timeline view).
        """
        from assets.models import Asset
        
        queryset = Asset.objects.filter(
            shared_with_users__user=user
        ).order_by('-taken_at', '-created_at')
        
        if cursor_taken_at:
            queryset = queryset.filter(taken_at__lt=cursor_taken_at)
        
        return queryset[:limit]
    
    @classmethod
    def get_user_people(cls, user: User, limit: int = 100, offset: int = 0):
        """
        Get people visible to a user with photo counts.
        Only includes people who appear in the user's accessible assets via sharing.
        """
        from people.models import Person, Face
        from django.db.models import Count, Q
        
        # Find people who appear in this user's shared assets
        people_with_counts = Person.objects.filter(
            faces__asset__shared_with_users__user=user
        ).annotate(
            photo_count=Count(
                'faces__asset',
                filter=Q(faces__asset__shared_with_users__user=user),
                distinct=True
            )
        ).filter(
            photo_count__gt=0
        ).order_by('-photo_count', 'display_name')[offset:offset + limit]
        
        return people_with_counts
    
    @classmethod
    def get_user_person_assets(
        cls,
        user: User,
        person_id: str,
        cursor_taken_at=None,
        limit: int = 200
    ):
        """
        Get assets where a specific person appears, scoped to user's shared access.
        """
        from assets.models import Asset
        
        queryset = Asset.objects.filter(
            shared_with_users__user=user,
            faces__person_id=person_id
        ).distinct().order_by('-taken_at', '-created_at')
        
        if cursor_taken_at:
            queryset = queryset.filter(taken_at__lt=cursor_taken_at)
        
        return queryset[:limit]
    
    @classmethod
    def get_asset_faces_for_user(cls, user: User, asset_id: str, show_names: bool = True):
        """
        Get faces on an asset, but only if the user has access to that asset via sharing.
        Optionally hide person names based on sharing settings.
        """
        from people.models import Face
        from django.db.models import Case, When, Value
        from django.db import models
        
        # First verify the user has access to this asset
        has_access = UserAsset.objects.filter(
            user=user,
            asset_id=asset_id
        ).exists()
        
        if not has_access:
            return Face.objects.none()
        
        # Get faces with conditional name display
        faces = Face.objects.filter(asset_id=asset_id).select_related('person')
        
        if not show_names:
            # Don't include person names in the result
            faces = faces.annotate(
                person_name=Value(None, output_field=models.CharField())
            )
        else:
            faces = faces.annotate(
                person_name=Case(
                    When(person__isnull=False, then='person__display_name'),
                    default=Value(None),
                    output_field=models.CharField()
                )
            )
        
        return faces
    
    @classmethod
    def verify_user_asset_access(cls, user: User, asset_id: str) -> bool:
        """
        Verify that a user has access to a specific asset via sharing.
        """
        return UserAsset.objects.filter(
            user=user,
            asset_id=asset_id
        ).exists()
