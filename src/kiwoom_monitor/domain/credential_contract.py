"""Provider field names shared by DB metadata and the credential vault."""

PROVIDER_FIELDS = {
    "kiwoom_real": ("app_key", "secret_key"),
    "kiwoom_mock": ("app_key", "secret_key"),
    "naver": ("client_id", "client_secret"),
    "dart": ("api_key",), "openai": ("api_key",),
    "gemini": ("api_key",), "claude": ("api_key",),
}
