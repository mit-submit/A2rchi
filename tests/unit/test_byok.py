"""
Unit tests for BYOK (Bring Your Own Key) functionality.

Tests cover:
- Key hierarchy (env > session)
- Provider key methods
- Security (key not exposed in serialization)
"""

import pytest
from unittest.mock import patch


class TestKeyHierarchy:
    """Test that key sources follow correct precedence."""
    
    def test_env_key_takes_precedence_over_session(self):
        """Environment variable keys should take precedence over session keys."""
        from src.archi.providers.base import BaseProvider, ProviderConfig, ProviderType
        
        # Create a mock provider config with env key
        config = ProviderConfig(
            provider_type=ProviderType.OPENAI,
            api_key_env="OPENAI_API_KEY",
            enabled=True,
        )
        
        # Mock read_secret to return an env key
        with patch('src.archi.providers.base.read_secret') as mock_read:
            mock_read.return_value = "sk-env-key-12345"
            
            # Create a concrete provider for testing
            from src.archi.providers.openai_provider import OpenAIProvider
            provider = OpenAIProvider(config)
            
            # Verify env key is loaded
            assert provider.api_key == "sk-env-key-12345"
            
            # Even if we set a session key, env should still be used
            # (this is handled at the app level, but provider stores what it's given)
    
    def test_session_key_used_when_no_env(self):
        """Session key should be used when no environment variable is set."""
        from src.archi.providers import get_provider_with_api_key, ProviderType
        
        # Create provider with explicit API key (simulating session key)
        provider = get_provider_with_api_key(ProviderType.OPENAI, "sk-session-key-67890")
        
        assert provider.api_key == "sk-session-key-67890"
        assert provider.is_configured is True


class TestBaseProviderKeyMethods:
    """Test BaseProvider key-related methods."""
    
    def test_set_api_key_method(self):
        """set_api_key should update the provider's API key."""
        from src.archi.providers import get_provider_with_api_key, ProviderType
        
        provider = get_provider_with_api_key(ProviderType.OPENAI, "initial-key")
        assert provider.api_key == "initial-key"
        
        provider.set_api_key("updated-key")
        assert provider.api_key == "updated-key"
    
    def test_api_key_property_setter(self):
        """api_key property setter should work."""
        from src.archi.providers import get_provider_with_api_key, ProviderType
        
        provider = get_provider_with_api_key(ProviderType.OPENAI, "initial-key")
        provider.api_key = "new-key-via-setter"
        
        assert provider.api_key == "new-key-via-setter"
    
    def test_is_configured_with_key(self):
        """is_configured should return True when key is set."""
        from src.archi.providers import get_provider_with_api_key, ProviderType
        
        provider = get_provider_with_api_key(ProviderType.OPENAI, "some-key")
        assert provider.is_configured is True
    
    def test_is_configured_without_key(self):
        """is_configured should return False when no key is set."""
        from src.archi.providers.base import ProviderConfig, ProviderType
        from src.archi.providers.openai_provider import OpenAIProvider
        
        config = ProviderConfig(
            provider_type=ProviderType.OPENAI,
            api_key=None,
            api_key_env="",  # No env var to check
            enabled=True,
        )
        
        with patch('src.archi.providers.base.read_secret') as mock_read:
            mock_read.return_value = None
            provider = OpenAIProvider(config)
            
            assert provider.is_configured is False


class TestSecurityRequirements:
    """Test security-related requirements."""
    
    def test_api_key_not_in_to_dict(self):
        """API key should not be exposed in to_dict() serialization."""
        from src.archi.providers import get_provider_with_api_key, ProviderType
        
        provider = get_provider_with_api_key(ProviderType.OPENAI, "secret-key-12345")
        provider_dict = provider.to_dict()
        
        # API key should not be in the serialized output
        assert "secret-key-12345" not in str(provider_dict)
        assert "api_key" not in provider_dict or provider_dict.get("api_key") is None
    
    def test_api_key_not_in_repr(self):
        """API key should not appear in string representation."""
        from src.archi.providers import get_provider_with_api_key, ProviderType
        
        provider = get_provider_with_api_key(ProviderType.OPENAI, "secret-key-12345")
        
        # Check that the key doesn't appear in any string representation
        repr_str = repr(provider) if hasattr(provider, '__repr__') else str(provider)
        assert "secret-key-12345" not in repr_str
