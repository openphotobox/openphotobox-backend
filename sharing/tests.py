"""
Tests for the sharing system.
"""
from django.test import TestCase
from django.contrib.auth.models import User
from assets.models import Album, Asset
from people.models import Person
from .models import Recipient, AccessGrant, RecipientLink
from .services import RecipientAssetBuilder, SharingQueryService


class SharingSystemTestCase(TestCase):
    """Test the sharing system functionality."""
    
    def setUp(self):
        # Create test user
        self.admin_user = User.objects.create_user(
            username='admin',
            email='admin@example.com',
            password='testpass'
        )
        
        # Create test recipient
        self.recipient = Recipient.objects.create(
            display_name='John Doe',
            email='john@example.com',
            created_by=self.admin_user
        )
        
        # Create test album
        self.album = Album.objects.create(
            title='Family Photos',
            description='Our family vacation photos'
        )
        
        # Create test assets (simplified - would normally have S3 data)
        self.asset1 = Asset.objects.create(
            sha256='test1',
            bucket='test-bucket',
            key='test1.jpg',
            mime_type='image/jpeg',
            width=1920,
            height=1080
        )
        self.asset2 = Asset.objects.create(
            sha256='test2',
            bucket='test-bucket',
            key='test2.jpg',
            mime_type='image/jpeg',
            width=1920,
            height=1080
        )
    
    def test_recipient_creation(self):
        """Test creating a recipient."""
        self.assertEqual(self.recipient.display_name, 'John Doe')
        self.assertEqual(self.recipient.created_by, self.admin_user)
        self.assertTrue(self.recipient.default_show_faces)
        self.assertTrue(self.recipient.default_show_names)
        self.assertTrue(self.recipient.default_allow_downloads)
    
    def test_access_grant_creation(self):
        """Test creating an access grant."""
        grant = AccessGrant.objects.create(
            recipient=self.recipient,
            album=self.album,
            granted_by=self.admin_user
        )
        
        self.assertEqual(grant.grant_type, 'album')
        self.assertEqual(grant.recipient, self.recipient)
        self.assertEqual(grant.album, self.album)
        self.assertIsNone(grant.person)
    
    def test_recipient_link_creation(self):
        """Test creating a secure recipient link."""
        link, token = RecipientLink.create_link(
            recipient=self.recipient,
            created_by=self.admin_user,
            name='Test Link'
        )
        
        self.assertIsNotNone(token)
        self.assertEqual(len(token), 43)  # URL-safe base64 of 32 bytes
        self.assertEqual(link.recipient, self.recipient)
        self.assertEqual(link.name, 'Test Link')
        
        # Test token lookup
        found_link = RecipientLink.get_by_token(token)
        self.assertEqual(found_link, link)
        
        # Test invalid token
        invalid_link = RecipientLink.get_by_token('invalid-token')
        self.assertIsNone(invalid_link)
    
    def test_password_protection(self):
        """Test password-protected links."""
        link, token = RecipientLink.create_link(
            recipient=self.recipient,
            created_by=self.admin_user,
            password='secret123'
        )
        
        # Test correct password
        self.assertTrue(link.verify_password('secret123'))
        
        # Test incorrect password
        self.assertFalse(link.verify_password('wrong'))
        
        # Test no password
        link_no_pass, _ = RecipientLink.create_link(
            recipient=self.recipient,
            created_by=self.admin_user
        )
        self.assertTrue(link_no_pass.verify_password('anything'))
    
    def test_feature_flags(self):
        """Test feature flag inheritance."""
        # Link with no overrides - should use recipient defaults
        link1, _ = RecipientLink.create_link(
            recipient=self.recipient,
            created_by=self.admin_user
        )
        flags1 = link1.get_effective_flags()
        self.assertTrue(flags1['show_faces'])
        self.assertTrue(flags1['show_names'])
        self.assertTrue(flags1['allow_downloads'])
        
        # Link with overrides
        link2, _ = RecipientLink.create_link(
            recipient=self.recipient,
            created_by=self.admin_user,
            show_names=False,
            allow_downloads=False
        )
        flags2 = link2.get_effective_flags()
        self.assertTrue(flags2['show_faces'])  # Uses recipient default
        self.assertFalse(flags2['show_names'])  # Overridden
        self.assertFalse(flags2['allow_downloads'])  # Overridden


# Example usage documentation
USAGE_EXAMPLE = """
# Sharing System Usage Example

## 1. Create a Recipient
recipient = Recipient.objects.create(
    display_name='Jane Smith',
    email='jane@example.com',
    created_by=admin_user
)

## 2. Grant Access to Albums
album_grant = AccessGrant.objects.create(
    recipient=recipient,
    album=family_album,
    granted_by=admin_user
)

## 3. Grant Access to People (optional)
person_grant = AccessGrant.objects.create(
    recipient=recipient,
    person=grandma_person,
    granted_by=admin_user
)

## 4. Create a Sharing Link
link, token = RecipientLink.create_link(
    recipient=recipient,
    created_by=admin_user,
    name='Jane\'s Family Photos',
    expires_at=timezone.now() + timedelta(days=30),
    password='family2023',
    show_names=True,
    allow_downloads=True
)

## 5. Share the Token
# Send the token to jane@example.com
# She can access: https://yoursite.com/r/{token}/

## 6. Recipient Asset Builder (automatic)
# When grants are created/modified, RecipientAsset entries are automatically updated
# Jane will see the union of all assets from her granted albums and people

## 7. Portal API Usage
# GET /r/{token}/                     - Portal info and counts
# GET /r/{token}/assets/              - Timeline of all accessible assets
# GET /r/{token}/people/              - People visible across all assets
# GET /r/{token}/people/{id}/assets/  - Assets where specific person appears
# GET /r/{token}/assets/{id}/         - Asset detail with faces
"""
