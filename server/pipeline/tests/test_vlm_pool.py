import unittest
from unittest.mock import patch
import httpx
from fastapi.testclient import TestClient
import vlm_pool

class RelayTests(unittest.TestCase):
    def client(self,handler):
        original=httpx.AsyncClient
        def factory(**kwargs):
            return original(transport=httpx.MockTransport(handler),**kwargs)
        return patch.object(vlm_pool.httpx,'AsyncClient',factory)

    def test_preserves_body_and_rotates_available_backends(self):
        seen=[]
        def handle(request):
            seen.append((request.url.port,request.content))
            return httpx.Response(200,json={'choices':[{'message':{'content':'<fcel>A<nl>'}}]})
        with self.client(handle), TestClient(vlm_pool.app) as client:
            for _ in range(3):
                r=client.post('/v1/chat/completions',json={'model':'mineru-vlm','messages':[],'stream':False})
                self.assertEqual(r.status_code,200)
                self.assertEqual(r.json()['choices'][0]['message']['content'],'<fcel>A<nl>')
            self.assertEqual([v[0] for v in seen],[16590,16591,16590])
            self.assertEqual(seen[0][1],seen[1][1])
            self.assertEqual(client.get('/health').json()['active'],0)

    def test_failure_body_is_redacted_and_slot_released(self):
        with self.client(lambda request:httpx.Response(500,text='private upstream content')),TestClient(vlm_pool.app) as client:
            r=client.post('/v1/chat/completions',json={})
            self.assertEqual(r.status_code,502)
            self.assertNotIn('private',r.text)
            self.assertEqual(vlm_pool.app.state.available.qsize(),2)

    def test_stream_and_invalid_json_rejected(self):
        with self.client(lambda request:httpx.Response(200,json={})),TestClient(vlm_pool.app) as client:
            self.assertEqual(client.post('/v1/chat/completions',json={'stream':True}).status_code,400)
            self.assertEqual(client.post('/v1/chat/completions',content='bad').status_code,400)

    def test_waiting_is_bounded(self):
        with self.client(lambda request:httpx.Response(200,json={})),TestClient(vlm_pool.app) as client:
            vlm_pool.app.state.waiting=8
            self.assertEqual(client.post('/v1/chat/completions',json={}).status_code,429)

if __name__=='__main__':unittest.main()
