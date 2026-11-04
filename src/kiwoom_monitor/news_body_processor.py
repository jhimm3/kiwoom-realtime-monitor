"""Explicit general BODY processor entry; no UI, account or AI composition."""
from __future__ import annotations

import argparse
import asyncio
import json
import signal
from pathlib import Path

from kiwoom_monitor.central_server.news_jobs import NewsJobRunner
from kiwoom_monitor.central_server.async_lifetime import await_owned_task
from kiwoom_monitor.infrastructure.news_body_client import NewsBodyClient


async def run_news_body_processor(client: NewsBodyClient, stop: asyncio.Event, *, fetcher=None):
    """Own only this processor's admitted work, including native receipt delivery."""
    options={'body_input':client.load_body_input,'body_accept':client.accept_body_result,'now_provider':client.server_now}
    if fetcher is not None:
        options['fetcher']=fetcher
    runner=NewsJobRunner(client,**options)
    await runner.start()
    owned=asyncio.create_task(runner.wait(),name='news-body-owner')
    stopping=asyncio.create_task(stop.wait(),name='news-body-stop')
    primary=None
    try:
        done,_=await asyncio.wait((owned,stopping),return_when=asyncio.FIRST_COMPLETED)
        if owned in done:
            await owned
    except BaseException as error:
        primary=error
        raise
    finally:
        async def close_owned():
            stopping.cancel()
            try:
                await asyncio.gather(stopping,return_exceptions=True)
            finally:
                try:
                    await runner.close()
                finally:
                    await asyncio.gather(owned,return_exceptions=True)
        try:
            await await_owned_task(asyncio.create_task(close_owned(),name='news-body-close'))
        except BaseException as error:
            if primary is None:
                raise
            if isinstance(primary,asyncio.CancelledError):
                if not isinstance(error,asyncio.CancelledError):
                    raise primary from error
                if error.__cause__ is not None:
                    raise primary from error.__cause__
            if error is not primary:
                primary.add_note('NEWS_BODY_CLOSE_FAILED:'+type(error).__name__)


async def _execute(client):
    stop=asyncio.Event()
    loop=asyncio.get_running_loop()
    previous={}
    for name in ('SIGINT','SIGTERM','SIGBREAK'):
        sig=getattr(signal,name,None)
        if sig is not None:
            previous[sig]=signal.signal(sig,lambda *_: loop.call_soon_threadsafe(stop.set))
    try:
        await run_news_body_processor(client,stop)
    finally:
        for sig,handler in previous.items():
            signal.signal(sig,handler)


def main(arguments=None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--connection',type=Path,required=True)
    parser.add_argument('--owner',required=True)
    args=parser.parse_args(arguments)
    try:
        value=json.loads(args.connection.read_text(encoding='utf-8-sig'))
        if not isinstance(value,dict) or value.get('mode') not in {'local_server','personal_server'}:
            raise ValueError('an explicit server producer connection is required')
        client=NewsBodyClient(value['server_url'],args.owner,value['access_token'])
        asyncio.run(_execute(client))
    except (OSError,ValueError,RuntimeError,KeyError) as error:
        print(json.dumps({'status':'failed','error':type(error).__name__}))
        return 1
    print(json.dumps({'status':'stopped'}))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
