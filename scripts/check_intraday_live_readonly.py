"""Read-only live NAS checks; never sends Kiwoom queries or orders."""
import asyncio
import json
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen

from kiwoom_monitor.infrastructure.central_server_config import DataSourceConfig
from kiwoom_monitor.infrastructure.system_ssl import system_ssl_context


async def main():
    source = DataSourceConfig(Path('C:/Users/pc-1/Documents/ChatGPT/kiwoom-realtime-monitor/data/data_source.json')).load()
    def get(path):
        started = time.monotonic()
        request = Request(source.server_url + path, headers={'Authorization': 'Bearer ' + source.access_token})
        with urlopen(request, timeout=10, context=system_ssl_context()) as response:
            value = json.load(response)
        return {'milliseconds': round((time.monotonic()-started)*1000), 'data': value}
    report = {'checked_at': datetime.now().isoformat(), 'mode': source.mode}
    for name, path in [
        ('health','/health'), ('resources','/api/v1/diagnostics/resources'),
        ('ranking_before','/api/v1/market/snapshots/ranking?subject=5&limit=1'),
        ('vi','/api/v1/market/events?kind=vi&limit=10'),
        ('cohort','/api/v1/market/events?kind=cohort&limit=10'),
        ('upper_limit','/api/v1/market/events?kind=upper_limit&limit=10'),
        ('references','/api/v1/content/stock_price_references?limit=20'),
        ('comparisons','/api/v1/market/trade-value-comparisons?code=005930&trading_date=2026-09-15&limit=1500'),
        ('journal_bars','/api/v1/market/minute-bars?code=001210&trading_date=2026-09-14&market=COMBINED'),
        ('candidates','/api/v1/research/candidates?limit=5'),
    ]:
        try:
            report[name] = await asyncio.to_thread(get, path)
        except Exception as error:
            report[name] = {'error': type(error).__name__ + ': ' + str(error)}
    import websockets
    counts = Counter()
    first = {}
    url = source.server_url.replace('https://','wss://').replace('http://','ws://') + '/api/v1/realtime'
    async with websockets.connect(url, additional_headers={'Authorization':'Bearer '+source.access_token}) as socket:
        deadline = time.monotonic() + 40
        # No subscribe message: observing the existing broadcast cannot change upstream registrations.
        while time.monotonic() < deadline:
            try:
                raw = await asyncio.wait_for(socket.recv(), timeout=max(.1, deadline-time.monotonic()))
            except asyncio.TimeoutError:
                break
            event = json.loads(raw)
            kind = str(event.get('type','unknown'))
            counts[kind] += 1
            first.setdefault(kind, event)
    report['websocket'] = {'duration_seconds':40, 'counts':dict(counts), 'first_events':first}
    report['ranking_after'] = await asyncio.to_thread(get, '/api/v1/market/snapshots/ranking?subject=5&limit=1')
    output = Path('reports/INTRADAY_LIVE_CHECK_20260915.json')
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Saved', output, 'WS counts:', dict(counts))
    for key in ('health','resources','candidates'):
        print(key, json.dumps(report[key],ensure_ascii=True)[:1300])


if __name__ == '__main__':
    asyncio.run(main())
