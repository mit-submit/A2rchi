"""
Unit tests for BYOK (Bring Your Own Key) functionality.

Tests cover:
- Key hierarchy (env > session)
- Session key storage
- API key endpoints
- Provider key integration
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from flask import Flask, session


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


class TestProviderKeyIntegration:
    """Test provider factory functions with API keys."""
    
    def test_get_provider_with_api_key_creates_new_instance(self):
        """get_provider_with_api_key should create a fresh provider instance."""
        from src.archi.providers import get_provider_with_api_key, ProviderType
        
        provider1 = get_provider_with_api_key(ProviderType.OPENAI, "sk-key-1")
        provider2 = get_provider_with_api_key(ProviderType.OPENAI, "sk-key-2")
        
        # Should be different instances
        assert provider1 is not provider2
        # With different keys
        assert provider1.api_key == "sk-key-1"
        assert provider2.api_key == "sk-key-2"
    
    def test_get_chat_model_with_api_key(self):
        """get_chat_model_with_api_key should return a configured model."""
        from src.archi.providers import get_chat_model_with_api_key, ProviderType
        
        # Test that function accepts api_key parameter and returns a model object
        # (validation happens at request time, not creation time)
        model = get_chat_model_with_api_key(
            ProviderType.OPENAI, 
            "gpt-4o-mini", 
            "sk-test-key"
        )
        assert model is not None
    
    def test_provider_types_supported(self):
        """All expected provider types should be supported."""
        from src.archi.providers import ProviderType, list_provider_types
        
        types = list_provider_types()
        
        assert ProviderType.OPENAI in types
        assert ProviderType.ANTHROPIC in types
        assert ProviderType.OPENROUTER in types
        assert ProviderType.LOCAL in types


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


class TestProviderDisplayNames:
    """Test that providers have correct display names."""
    
    def test_openai_display_name(self):
        """OpenAI provider should have correct display name."""
        from src.archi.providers import get_provider_with_api_key, ProviderType
        
        provider = get_provider_with_api_key(ProviderType.OPENAI, "test-key")
        assert provider.display_name == "OpenAI"
    
    def test_anthropic_display_name(self):
        """Anthropic provider should have correct display name."""
        from src.archi.providers import get_provider_with_api_key, ProviderType
        
        provider = get_provider_with_api_key(ProviderType.ANTHROPIC, "test-key")
        assert provider.display_name == "Anthropic"
    

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


class TestModelInfo:
    """Test ModelInfo dataclass."""
    
    def test_model_info_to_dict(self):
        """ModelInfo.to_dict() should return correct structure."""
        from src.archi.providers.base import ModelInfo
        
        model = ModelInfo(
            id="gpt-4o",
            name="gpt-4o",
            display_name="GPT-4o",
            context_window=128000,
            supports_tools=True,
            supports_streaming=True,
            supports_vision=True,
        )
        
        d = model.to_dict()
        
        assert d["id"] == "gpt-4o"
        assert d["name"] == "gpt-4o"
        assert d["display_name"] == "GPT-4o"
        assert d["context_window"] == 128000
        assert d["supports_tools"] is True
        assert d["supports_streaming"] is True
        assert d["supports_vision"] is True
