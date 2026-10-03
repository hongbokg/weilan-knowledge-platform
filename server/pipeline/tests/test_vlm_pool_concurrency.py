import asyncio
import unittest
from unittest.mock import patch
import httpx
import vlm_pool


class ConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_slow_body_flood_is_bounded_before_body_is_read(self):
        release = asyncio.Event()
        class SlowInput(httpx.AsyncByteStream):
            async def __aiter__(self):
                await release.wait()
                yield b'{}'
        original = httpx.AsyncClient
        with patch.object(vlm_pool.httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(lambda request:httpx.Response(200,json={})),**kw)):
            async with vlm_pool.app.router.lifespan_context(vlm_pool.app):
                async with original(transport=httpx.ASGITransport(app=vlm_pool.app),base_url='http://test') as client:
                    tasks = [asyncio.create_task(client.post('/v1/chat/completions',content=SlowInput())) for _ in range(24)]
                    try:
                        for _ in range(100):
                            if vlm_pool.app.state.receiving==10:break
                            await asyncio.sleep(.005)
                        self.assertEqual(vlm_pool.app.state.receiving,10)
                        self.assertEqual(vlm_pool.app.state.tokens.qsize(),0)
                    finally:
                        release.set()
                    results = await asyncio.wait_for(asyncio.gather(*tasks),5)
                    self.assertEqual(sum(r.status_code==429 for r in results),14)
                    self.assertEqual(vlm_pool.app.state.tokens.qsize(),10)

    async def test_body_timeout_does_not_hold_capacity(self):
        class SlowInput(httpx.AsyncByteStream):
            async def __aiter__(self):
                await asyncio.sleep(1)
                yield b'{}'
        original = httpx.AsyncClient
        with patch.object(vlm_pool.httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(lambda request:httpx.Response(200,json={})),**kw)),patch.object(vlm_pool,'BODY_TIMEOUT',.01):
            async with vlm_pool.app.router.lifespan_context(vlm_pool.app):
                async with original(transport=httpx.ASGITransport(app=vlm_pool.app),base_url='http://test') as client:
                    response = await client.post('/v1/chat/completions',content=SlowInput())
                    self.assertEqual(response.status_code,408)
                    self.assertEqual(vlm_pool.app.state.receiving,0)
                    self.assertEqual(vlm_pool.app.state.tokens.qsize(),10)

    async def test_aggregate_body_budget_releases_bytes(self):
        original = httpx.AsyncClient
        with patch.object(vlm_pool.httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(lambda request:httpx.Response(200,json={})),**kw)),patch.object(vlm_pool,'MAX_TOTAL_BODY',1):
            async with vlm_pool.app.router.lifespan_context(vlm_pool.app):
                async with original(transport=httpx.ASGITransport(app=vlm_pool.app),base_url='http://test') as client:
                    response = await client.post('/v1/chat/completions',content=b'{}')
                    self.assertEqual(response.status_code,429)
                    self.assertEqual(vlm_pool.app.state.body_bytes,0)
                    self.assertEqual(vlm_pool.app.state.tokens.qsize(),10)

    async def test_flood_is_bounded_and_output_preserved(self):
        release = asyncio.Event()
        actual = peak = 0
        async def upstream(request):
            nonlocal actual, peak
            actual += 1
            peak = max(peak, actual)
            await release.wait()
            actual -= 1
            return httpx.Response(200, json={'choices':[{'message':{'content':'<fcel>型号<nl>'}}]})
        original = httpx.AsyncClient
        def factory(**kwargs):
            return original(transport=httpx.MockTransport(upstream), **kwargs)
        with patch.object(vlm_pool.httpx, 'AsyncClient', factory):
            async with vlm_pool.app.router.lifespan_context(vlm_pool.app):
                async with original(transport=httpx.ASGITransport(app=vlm_pool.app), base_url='http://test') as client:
                    tasks = [asyncio.create_task(client.post('/v1/chat/completions',json={'messages':[]})) for _ in range(24)]
                    try:
                        for _ in range(100):
                            if vlm_pool.app.state.active == 2 and vlm_pool.app.state.waiting == 8:
                                break
                            await asyncio.sleep(.005)
                        health = (await client.get('/health')).json()
                        self.assertEqual(health['active'], 2)
                        self.assertEqual(health['waiting'], 8)
                        self.assertEqual(health['admitted'], 10)
                    finally:
                        release.set()
                    results = await asyncio.wait_for(asyncio.gather(*tasks), 5)
                    self.assertEqual(peak, 2)
                    self.assertEqual(sum(r.status_code==429 for r in results), 14)
                    self.assertEqual(sum(r.status_code==200 for r in results), 10)
                    self.assertTrue(all('型号' in r.json()['choices'][0]['message']['content'] for r in results if r.status_code==200))
                    self.assertEqual(vlm_pool.app.state.tokens.qsize(), 10)
                    self.assertEqual(vlm_pool.app.state.available.qsize(), 2)

    async def test_timeout_is_counted_and_capacity_released(self):
        async def upstream(request):
            await asyncio.sleep(1)
            return httpx.Response(200,json={})
        original = httpx.AsyncClient
        with patch.object(vlm_pool.httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(upstream),**kw)),patch.object(vlm_pool,'INFERENCE_TIMEOUT',.01):
            async with vlm_pool.app.router.lifespan_context(vlm_pool.app):
                async with original(transport=httpx.ASGITransport(app=vlm_pool.app),base_url='http://test') as client:
                    response = await client.post('/v1/chat/completions',json={})
                    self.assertEqual(response.status_code,504)
                    self.assertEqual(vlm_pool.app.state.telemetry.counts['timeouts'],1)
                    self.assertEqual(vlm_pool.app.state.tokens.qsize(),10)
                    self.assertEqual(vlm_pool.app.state.available.qsize(),2)

    async def test_canceled_waiter_releases_capacity(self):
        original = httpx.AsyncClient
        with patch.object(vlm_pool.httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(lambda request:httpx.Response(200,json={})),**kw)):
            async with vlm_pool.app.router.lifespan_context(vlm_pool.app):
                saved = [vlm_pool.app.state.available.get_nowait() for _ in range(2)]
                async with original(transport=httpx.ASGITransport(app=vlm_pool.app),base_url='http://test') as client:
                    task = asyncio.create_task(client.post('/v1/chat/completions',json={}))
                    for _ in range(100):
                        if vlm_pool.app.state.waiting:break
                        await asyncio.sleep(.005)
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):await task
                    self.assertEqual(vlm_pool.app.state.waiting,0)
                    self.assertEqual(vlm_pool.app.state.tokens.qsize(),10)
                    for backend in saved:vlm_pool.app.state.available.put_nowait(backend)


if __name__ == '__main__':
    unittest.main()
