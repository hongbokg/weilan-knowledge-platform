"""Loopback, bounded relay to independent instances of the validated OCR model.

No model prompts or responses are logged. The parser uses non-streaming calls.
Each backend owns one context; there is never concurrent inference in a context.
"""
import asyncio
import os
import time
from contextlib import asynccontextmanager
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from starlette.requests import ClientDisconnect
from pool_metrics import Telemetry

BACKENDS = tuple(os.environ.get('VLM_BACKENDS', 'http://127.0.0.1:16590,http://127.0.0.1:16591').split(','))
MAX_BODY = 32 * 1024 * 1024
MAX_RESPONSE = 16 * 1024 * 1024
MAX_TOTAL_BODY = 128 * 1024 * 1024
MAX_WAITING = 8
BODY_TIMEOUT = 30
WAIT_TIMEOUT = 180
INFERENCE_TIMEOUT = 180

@asynccontextmanager
async def lifespan(app):
    app.state.available = asyncio.Queue()
    for backend in BACKENDS:
        if not backend.startswith('http://127.0.0.1:'):
            raise RuntimeError('loopback_backend_required')
        app.state.available.put_nowait(backend)
    app.state.client = httpx.AsyncClient(timeout=httpx.Timeout(180, connect=5), trust_env=False,
                                       limits=httpx.Limits(max_connections=len(BACKENDS)))
    app.state.active = 0
    app.state.waiting = 0
    app.state.receiving = 0
    app.state.body_bytes = 0
    app.state.tokens = asyncio.Queue()
    for _ in range(len(BACKENDS) + MAX_WAITING):
        app.state.tokens.put_nowait(True)
    app.state.telemetry = Telemetry()
    try:
        yield
    finally:
        await app.state.client.aclose()

app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

@app.get('/health')
async def health():
    return {'slots': len(BACKENDS), 'active': app.state.active, 'waiting': app.state.waiting,
            'receiving': app.state.receiving, 'waiting_limit': MAX_WAITING,
            'admitted': len(BACKENDS) + MAX_WAITING - app.state.tokens.qsize(),
            'request_capacity': len(BACKENDS) + MAX_WAITING,
            'body_bytes': app.state.body_bytes, 'body_budget': MAX_TOTAL_BODY,
            'metrics': app.state.telemetry.snapshot()}

@app.get('/v1/models')
async def models():
    return {'object': 'list', 'data': [{'id': 'mineru-vlm', 'object': 'model'}]}

@app.post('/v1/chat/completions')
async def completion(request: Request):
    # Reserve without awaiting, BEFORE reading a potentially slow/large body.
    # A waiting-count check alone races when many uploads start together.
    if app.state.waiting >= MAX_WAITING:
        app.state.telemetry.counts['queue_rejected'] += 1
        return JSONResponse({'error': 'ocr_pool_queue_full'}, status_code=429,
                            headers={'Retry-After': '5'})
    try:
        app.state.tokens.get_nowait()
    except asyncio.QueueEmpty:
        app.state.telemetry.counts['queue_rejected'] += 1
        return JSONResponse({'error': 'ocr_pool_queue_full'}, status_code=429,
                            headers={'Retry-After': '5'})
    app.state.telemetry.counts['accepted'] += 1
    request.state.pool_body_bytes = 0
    try:
        return await admitted_completion(request)
    except asyncio.CancelledError:
        app.state.telemetry.counts['canceled'] += 1
        raise
    except ClientDisconnect:
        app.state.telemetry.counts['canceled'] += 1
        return JSONResponse({'error': 'ocr_client_disconnected'}, status_code=499)
    finally:
        app.state.body_bytes -= request.state.pool_body_bytes
        app.state.tokens.put_nowait(True)


async def admitted_completion(request):
    data = bytearray()
    app.state.receiving += 1
    try:
        async with asyncio.timeout(BODY_TIMEOUT):
            async for block in request.stream():
                if len(data) + len(block) > MAX_BODY:
                    app.state.telemetry.counts['invalid_rejected'] += 1
                    return JSONResponse({'error': 'ocr_request_too_large'}, status_code=413)
                if app.state.body_bytes + len(block) > MAX_TOTAL_BODY:
                    app.state.telemetry.counts['queue_rejected'] += 1
                    return JSONResponse({'error': 'ocr_body_memory_budget'}, status_code=429,
                                        headers={'Retry-After': '5'})
                app.state.body_bytes += len(block)
                request.state.pool_body_bytes += len(block)
                data.extend(block)
    except TimeoutError:
        app.state.telemetry.counts['timeouts'] += 1
        return JSONResponse({'error': 'ocr_body_timeout'}, status_code=408)
    finally:
        app.state.receiving -= 1
    import json
    try:
        payload = json.loads(data)
        if not isinstance(payload, dict) or payload.get('stream'):
            app.state.telemetry.counts['invalid_rejected'] += 1
            return JSONResponse({'error': 'non_streaming_request_required'}, status_code=400)
    except (ValueError, TypeError):
        app.state.telemetry.counts['invalid_rejected'] += 1
        return JSONResponse({'error': 'invalid_ocr_request'}, status_code=400)
    started = time.monotonic()
    try:
        try:
            backend = app.state.available.get_nowait()
        except asyncio.QueueEmpty:
            if app.state.waiting >= MAX_WAITING:
                app.state.telemetry.counts['queue_rejected'] += 1
                return JSONResponse({'error': 'ocr_pool_queue_full'}, status_code=429,
                                    headers={'Retry-After': '5'})
            app.state.waiting += 1
            try:
                backend = await asyncio.wait_for(app.state.available.get(), timeout=WAIT_TIMEOUT)
            except asyncio.TimeoutError:
                app.state.telemetry.counts['timeouts'] += 1
                return JSONResponse({'error': 'ocr_pool_wait_timeout'}, status_code=504)
            finally:
                app.state.waiting -= 1
    finally:
        app.state.telemetry.wait.add(time.monotonic() - started)
    app.state.active += 1
    started = time.monotonic()
    try:
        async with asyncio.timeout(INFERENCE_TIMEOUT), app.state.client.stream('POST', backend + '/v1/chat/completions', content=bytes(data),
                                          headers={'Content-Type': 'application/json'}) as upstream:
            if upstream.status_code != 200:
                app.state.telemetry.counts['failed'] += 1
                return JSONResponse({'error': 'ocr_backend_failed', 'status': upstream.status_code}, status_code=502)
            body = bytearray()
            async for block in upstream.aiter_bytes():
                body.extend(block)
                if len(body) > MAX_RESPONSE:
                    app.state.telemetry.counts['failed'] += 1
                    return JSONResponse({'error': 'ocr_response_too_large'}, status_code=502)
            app.state.telemetry.counts['completed'] += 1
            return Response(bytes(body), media_type='application/json')
    except (httpx.TimeoutException, TimeoutError):
        app.state.telemetry.counts['timeouts'] += 1
        return JSONResponse({'error': 'ocr_backend_timeout'}, status_code=504)
    except httpx.HTTPError:
        app.state.telemetry.counts['failed'] += 1
        return JSONResponse({'error': 'ocr_backend_unavailable'}, status_code=503)
    finally:
        app.state.active -= 1
        app.state.telemetry.inference.add(time.monotonic() - started)
        app.state.available.put_nowait(backend)
