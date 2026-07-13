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
    CodexBackendError,
    CodexResponse,
    CodexSubscriptionClient,
    ToolCall,
)

__all__ = [
    "AuthStatus",
    "CodexBackendError",
    "CodexOAuth",
    "CodexOAuthConfig",
    "CodexOAuthError",
    "CodexResponse",
    "CodexSubscriptionClient",
    "FileTokenStore",
    "OAuthTokens",
    "ToolCall",
    "extract_chatgpt_account_id",
]

__version__ = "0.1.0"
