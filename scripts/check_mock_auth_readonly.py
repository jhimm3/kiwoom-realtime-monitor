"""Compare configured profiles and probe mock OAuth without exposing credentials."""
import json
from pathlib import Path
from kiwoom_monitor.infrastructure.kiwoom_rest.local_config import LocalApiConfig
from kiwoom_monitor.infrastructure.kiwoom_rest.client import KiwoomRestClient
from kiwoom_monitor.infrastructure.kiwoom_rest.settings import KiwoomSettings

env = {}
for line in Path('X:/kiwoom-monitor/deploy/synology/.env').read_text(encoding='utf-8-sig').splitlines():
    if '=' in line and not line.lstrip().startswith('#'):
        name, value = line.split('=', 1)
        env[name.strip()] = value.strip().strip('"').strip("'")
key = env.get('KIWOOM_MOCK_APP_KEY', '')
secret = env.get('KIWOOM_MOCK_SECRET_KEY', '')
local = LocalApiConfig(Path('C:/Users/pc-1/Documents/ChatGPT/kiwoom-realtime-monitor/data/api.env')).load_profiles()
result = {
    'nas_mock_key_present': bool(key), 'nas_mock_secret_present': bool(secret),
    'local_mock_key_present': bool(local.mock_app_key),
    'local_mock_secret_present': bool(local.mock_secret_key),
    'nas_matches_local_mock_key': key == local.mock_app_key,
    'nas_matches_local_mock_secret': secret == local.mock_secret_key,
    'nas_mock_equals_nas_real_key': key == env.get('KIWOOM_APP_KEY',''),
}
if key and secret:
    try:
        client = KiwoomRestClient(KiwoomSettings(key, secret, 'mock'))
        client.get_access_token()
        result['fresh_mock_token_issued'] = True
    except Exception as error:
        result['fresh_mock_token_issued'] = False
        message = str(error)
        for sensitive in (key, secret):
            message = message.replace(sensitive, '[REDACTED]')
        result['fresh_mock_error'] = message
print(json.dumps(result, ensure_ascii=True, indent=2))
