"""Real local HTTP requests to the Free Web status endpoint; no Telegram access."""
import asyncio
import json
import os
import unittest
from unittest.mock import patch

from hexa_userbot import HealthServer, web_port


class HealthServerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.health = HealthServer()
        await self.health.start(0, host='127.0.0.1')
        self.port = self.health.server.sockets[0].getsockname()[1]

    async def asyncTearDown(self):
        await self.health.close()

    async def request(self, path='/healthz', method='GET'):
        reader, writer = await asyncio.open_connection('127.0.0.1', self.port)
        writer.write(f'{method} {path} HTTP/1.1\r\nHost: localhost\r\n\r\n'.encode())
        await writer.drain()
        response = await asyncio.wait_for(reader.read(), 2)
        writer.close()
        await writer.wait_closed()
        return response.split(b'\r\n\r\n', 1)

    async def test_readiness_is_unavailable_until_telegram_ready(self):
        headers, body = await self.request()
        self.assertIn(b'503 Service Unavailable', headers)
        self.assertEqual(json.loads(body)['status'], 'starting')
        self.health.ready = True
        headers, body = await self.request()
        self.assertIn(b'200 OK', headers)
        self.assertEqual(json.loads(body)['status'], 'ready')

    async def test_root_is_public_status_and_head_has_no_body(self):
        self.health.ready = True
        headers, body = await self.request('/')
        self.assertIn(b'200 OK', headers)
        self.assertEqual(set(json.loads(body)), {'service', 'status', 'controls'})
        self.assertIn(b'Cache-Control: no-store', headers)
        headers, body = await self.request('/', method='HEAD')
        self.assertIn(b'200 OK', headers)
        self.assertEqual(body, b'')

    async def test_http_cannot_start_stop_or_read_private_files(self):
        self.health.ready = True
        for path in ['/go', '/stop', '/.env', '/.session-string', '/api/start']:
            headers, _ = await self.request(path)
            self.assertIn(b'404 Not Found', headers)
        for method in ['POST', 'PUT', 'DELETE']:
            headers, _ = await self.request('/', method=method)
            self.assertIn(b'405 Method Not Allowed', headers)

    async def test_oversized_request_is_rejected(self):
        reader, writer = await asyncio.open_connection('127.0.0.1', self.port)
        writer.write(b'GET / HTTP/1.1\r\nX-Large: ' + b'x' * 9000 + b'\r\n\r\n')
        await writer.drain()
        response = await asyncio.wait_for(reader.read(), 2)
        self.assertIn(b'400 Bad Request', response)
        writer.close()
        await writer.wait_closed()

    async def test_shutdown_closes_idle_requests(self):
        reader, writer = await asyncio.open_connection('127.0.0.1', self.port)
        writer.write(b'GET / HTTP/1.1\r\n')
        await writer.drain()
        await asyncio.sleep(0)
        await self.health.close()
        self.assertEqual(await asyncio.wait_for(reader.read(), 2), b'')
        self.assertEqual(self.health.handlers, set())
        writer.close()
        await writer.wait_closed()


class PortTests(unittest.TestCase):
    def test_render_default_custom_and_invalid_ports(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(web_port(), 10000)
        with patch.dict(os.environ, {'PORT': '12345'}, clear=True):
            self.assertEqual(web_port(), 12345)
        for value in ['', 'abc', '0', '-1', '65536']:
            with patch.dict(os.environ, {'PORT': value}, clear=True):
                with self.assertRaisesRegex(ValueError, 'PORT must'):
                    web_port()
