"""Authenticated BODY-only HTTP; native attempts remain the authority."""
from __future__ import annotations

import json
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from .async_lifetime import await_native_call
from kiwoom_monitor.domain.news_observation import NewsBodyConflict


def create_news_body_router(store, authorize, *, now_provider):
    router=APIRouter()

    async def native(function,*args,**kwargs):
        try:
            return await await_native_call(function,*args,**kwargs)
        except (NewsBodyConflict,ValueError) as error:
            raise HTTPException(status_code=409,detail=str(error)) from error

    @router.post('/api/v1/news/body-jobs/claim')
    async def claim(owner: str=Depends(authorize)):
        return {'job':await native(store.claim_news_body_job,owner,now=now_provider().timestamp())}

    @router.get('/api/v1/news/body-jobs/{key}/input')
    async def body_input(key: str, attempts: int=Query(ge=1), owner: str=Depends(authorize)):
        return await native(store.load_news_body_job_input,owner,key,attempts)

    @router.get('/api/v1/news/body-jobs/{key}/receipt')
    async def receipt(key: str, attempts: int=Query(ge=1), owner: str=Depends(authorize)):
        return await native(store.load_news_body_job_receipt,owner,key,attempts)

    @router.post('/api/v1/news/body-jobs/{key}/result')
    async def result(key: str, request: Request, owner: str=Depends(authorize)):
        body=bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body)>2_000_000*12:
                raise HTTPException(status_code=413,detail='BODY result exceeds its input contract')
        try:
            document=json.loads(body)
        except (ValueError,UnicodeError,RecursionError) as error:
            raise HTTPException(status_code=400,detail='invalid BODY result JSON') from error
        return await native(store.accept_news_body_job_result,owner,key,document)

    return router
