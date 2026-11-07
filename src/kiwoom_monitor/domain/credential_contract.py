"""Provider field names shared by DB metadata and the credential vault."""

PROVIDER_FIELDS = {
    "kiwoom_real": ("app_key", "secret_key"),
    "kiwoom_mock": ("app_key", "secret_key"),
    "naver": ("client_id", "client_secret"),
    "dart": ("api_key",), "openai": ("api_key",),
    "gemini": ("api_key",), "claude": ("api_key",),
}


def tr_connection_target(value):
    """Validate a public binding reference, never credentials or order authority."""
    import re
    import uuid
    if (not isinstance(value, dict) or set(value) != {
            "scope", "credential_profile_id", "binding_revision"}
            or not isinstance(value["credential_profile_id"], str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", value["credential_profile_id"])
            or type(value["binding_revision"]) is not int
            or not 1 <= value["binding_revision"] < 2**63):
        raise ValueError("TR_CONNECTION_SETTINGS_INVALID")
    scope = value["scope"]
    if (not isinstance(scope, dict) or set(scope) != {"broker", "environment", "account_ref"}
            or scope["broker"] != "kiwoom" or scope["environment"] not in ("real", "mock")):
        raise ValueError("TR_CONNECTION_SETTINGS_INVALID")
    try:
        account_ref = str(uuid.UUID(scope["account_ref"]))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("TR_CONNECTION_SETTINGS_INVALID") from None
    if account_ref != scope["account_ref"]:
        raise ValueError("TR_CONNECTION_SETTINGS_INVALID")
    return {"scope": dict(scope), "credential_profile_id": value["credential_profile_id"],
            "binding_revision": value["binding_revision"]}


def same_tr_account(left, right):
    return left["scope"] == right["scope"]


def tr_connection_settings(value, *, stored=False):
    fields = {"mode", "execution_target", "data_target"} | ({"revision"} if stored else set())
    if (not isinstance(value, dict) or set(value) != fields
            or value["mode"] not in ("integrated", "separated")):
        raise ValueError("TR_CONNECTION_SETTINGS_INVALID")
    revision = value.get("revision")
    if stored and (type(revision) is not int or not 0 <= revision < 2**63):
        raise ValueError("TR_CONNECTION_SETTINGS_INVALID")
    if stored and revision == 0:
        if value != {"mode": "separated", "execution_target": None, "data_target": None, "revision": 0}:
            raise ValueError("TR_CONNECTION_SETTINGS_INVALID")
        return dict(value)
    execution = tr_connection_target(value["execution_target"])
    data = tr_connection_target(value["data_target"])
    if value["mode"] == "integrated" and not same_tr_account(execution, data):
        raise ValueError("TR_CONNECTION_SETTINGS_INVALID")
    result = {"mode": value["mode"], "execution_target": execution, "data_target": data}
    return {**result, "revision": revision} if stored else result
