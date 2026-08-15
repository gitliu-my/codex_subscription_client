"""Reusable ChatGPT/Codex subscription authentication and model client."""

from .api_keys import (
    ApiKeyRecord,
    ApiKeyStore,
    FileSecretStore,
    MacOSKeychainSecretStore,
    default_secret_store,
)
from .auth import (
    AuthStatus,
    ChatGPTIdentity,
    CodexOAuth,
    CodexOAuthConfig,
    CodexOAuthError,
    FileTokenStore,
    OAuthTokens,
    extract_chatgpt_account_id,
    extract_chatgpt_identity,
    is_headless_environment,
)
from .client import (
    CodexClientProfile,
    CodexBackendError,
    CodexResponse,
    CodexSubscriptionClient,
    SubscriptionModel,
    ToolCall,
    image_to_url,
)

__all__ = [
    "ApiKeyRecord",
    "ApiKeyStore",
    "AuthStatus",
    "ChatGPTIdentity",
    "CodexBackendError",
    "CodexClientProfile",
    "CodexOAuth",
    "CodexOAuthConfig",
    "CodexOAuthError",
    "CodexResponse",
    "CodexSubscriptionClient",
    "FileTokenStore",
    "FileSecretStore",
    "MacOSKeychainSecretStore",
    "OAuthTokens",
    "SubscriptionModel",
    "ToolCall",
    "extract_chatgpt_account_id",
    "extract_chatgpt_identity",
    "default_secret_store",
    "is_headless_environment",
    "image_to_url",
]

__version__ = "0.9.0"
