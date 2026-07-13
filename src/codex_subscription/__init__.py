"""Reusable ChatGPT/Codex subscription authentication and model client."""

from .auth import (
    AuthStatus,
    CodexOAuth,
    CodexOAuthConfig,
    CodexOAuthError,
    FileTokenStore,
    OAuthTokens,
    extract_chatgpt_account_id,
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
    "AuthStatus",
    "CodexBackendError",
    "CodexClientProfile",
    "CodexOAuth",
    "CodexOAuthConfig",
    "CodexOAuthError",
    "CodexResponse",
    "CodexSubscriptionClient",
    "FileTokenStore",
    "OAuthTokens",
    "SubscriptionModel",
    "ToolCall",
    "extract_chatgpt_account_id",
    "image_to_url",
]

__version__ = "0.2.0"
