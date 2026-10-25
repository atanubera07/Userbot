"""Hosting login must never fall back to interactive login prompts."""
import contextlib
import asyncio
import hashlib
import importlib.util
import io
import os
import signal
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch

from hexa_userbot import main, telegram_library

try:
    import telethon
except ImportError:
    telethon = None


class HeadlessConfigurationTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_string_is_rejected_before_connecting(self):
        with patch.dict(os.environ, {'TELEGRAM_API_ID': '12345', 'TELEGRAM_API_HASH': 'a' * 32}, clear=True):
            with self.assertRaisesRegex(ValueError, 'TELEGRAM_SESSION_STRING is required'):
                await main(non_interactive=True)


class LocalCredentialTests(unittest.TestCase):
    def test_library_selection_is_explicit_and_validated(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(telegram_library(), 'telethon')
        with patch.dict(os.environ, {'TELEGRAM_LIBRARY': 'Pyrogram'}, clear=True):
            self.assertEqual(telegram_library(), 'pyrogram')
        with patch.dict(os.environ, {'TELEGRAM_LIBRARY': 'unknown'}, clear=True):
            with self.assertRaisesRegex(ValueError, 'TELEGRAM_LIBRARY'):
                telegram_library()

    def test_missing_dotenv_prompts_for_credentials_locally(self):
        from generate_session import login_credentials
        with patch.dict(os.environ, {}, clear=True), \
                patch('builtins.input', return_value='12345') as api_input, \
                patch('generate_session.getpass.getpass', return_value='a' * 32) as hash_input:
            self.assertEqual(login_credentials(), (12345, 'a' * 32))
        api_input.assert_called_once()
        hash_input.assert_called_once()

    def test_existing_credentials_do_not_prompt(self):
        from generate_session import login_credentials
        with patch.dict(os.environ, {'TELEGRAM_API_ID': '12345', 'TELEGRAM_API_HASH': 'a' * 32}, clear=True), \
                patch('builtins.input') as api_input, patch('generate_session.getpass.getpass') as hash_input:
            self.assertEqual(login_credentials(), (12345, 'a' * 32))
        api_input.assert_not_called()
        hash_input.assert_not_called()

    def test_invalid_credentials_are_rejected_before_telegram_connects(self):
        from generate_session import login_credentials
        for api_id, api_hash in [('0', 'a' * 32), ('word', 'a' * 32), ('12345', 'bad-hash')]:
            with patch.dict(os.environ, {'TELEGRAM_API_ID': api_id, 'TELEGRAM_API_HASH': api_hash}, clear=True):
                with self.assertRaises(ValueError):
                    login_credentials()


@unittest.skipIf(telethon is None, 'Install requirements.txt to test the hosting login path')
class HeadlessLoginTests(unittest.IsolatedAsyncioTestCase):
    def client(self, authorized):
        return NS(connect=AsyncMock(), is_user_authorized=AsyncMock(return_value=authorized),
                  start=AsyncMock(), disconnect=AsyncMock(), get_me=AsyncMock(return_value=NS(id=123)),
                  get_entity=AsyncMock(return_value=NS(id=572621020, bot=True)),
                  add_event_handler=Mock(), send_message=AsyncMock(), run_until_disconnected=AsyncMock())

    async def boot(self, client, extra_env=None, http_port=None):
        with tempfile.TemporaryDirectory() as temp:
            env = {'TELEGRAM_API_ID': '12345', 'TELEGRAM_API_HASH': 'a' * 32,
                   'TELEGRAM_SESSION_STRING': 'test-private-session', 'HEXA_NOTIFY_CHAT': 'me',
                   'TELEGRAM_SESSION_FILE': str(Path(temp)/'worker')}
            env.update(extra_env or {})
            with patch.dict(os.environ, env, clear=True), \
                    patch('telethon.TelegramClient', return_value=client), \
                    patch('telethon.sessions.StringSession', return_value=object()), \
                    patch('hexa_userbot.Path.home', return_value=Path(temp)), \
                    contextlib.redirect_stdout(io.StringIO()):
                await main(mode='catch', non_interactive=True, http_port=http_port)

    async def test_revoked_session_fails_without_prompting_and_disconnects(self):
        client = self.client(False)
        with self.assertRaisesRegex(RuntimeError, 'no longer authorized'):
            await self.boot(client)
        client.start.assert_not_awaited()
        client.connect.assert_awaited_once()
        client.disconnect.assert_awaited_once()
        client.get_me.assert_not_awaited()

    async def test_valid_session_boots_handlers_without_starting_hunt(self):
        client = self.client(True)
        await self.boot(client)
        client.start.assert_not_awaited()
        client.connect.assert_awaited_once()
        self.assertEqual(client.add_event_handler.call_count, 3)
        client.send_message.assert_not_awaited()
        client.run_until_disconnected.assert_awaited_once()
        client.disconnect.assert_awaited_once()

    async def test_sigterm_disconnects_and_exits_cleanly(self):
        client = self.client(True)
        loop = asyncio.get_running_loop()
        handlers = {}

        def register(sig, callback):
            handlers[sig] = callback

        async def terminate():
            handlers[signal.SIGTERM]()
            await asyncio.sleep(0)

        client.run_until_disconnected.side_effect = terminate
        with patch.object(loop, 'add_signal_handler', side_effect=register), \
                patch.object(loop, 'remove_signal_handler') as remove:
            await self.boot(client)
        client.disconnect.assert_awaited_once()
        remove.assert_called_once_with(signal.SIGTERM)

    async def test_ordinary_cancellation_is_not_swallowed(self):
        client = self.client(True)
        client.run_until_disconnected.side_effect = asyncio.CancelledError
        with self.assertRaises(asyncio.CancelledError):
            await self.boot(client)
        client.disconnect.assert_awaited_once()

    async def test_persistent_session_guard_blocks_duplicate_and_releases(self):
        import fcntl
        client = self.client(True)
        with tempfile.TemporaryDirectory() as persistent:
            async def check_duplicate():
                with self.assertRaisesRegex(RuntimeError, 'Another copy'):
                    await main(non_interactive=True)

            client.run_until_disconnected.side_effect = check_duplicate
            await self.boot(client, {'HEXA_LOCK_DIR': persistent})
            fingerprint = hashlib.sha256(b'test-private-session').hexdigest()[:24]
            path = Path(persistent)/('string-' + fingerprint + '.lock')
            self.assertTrue(path.exists())
            with path.open('a') as guard:
                fcntl.flock(guard.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        client.connect.assert_awaited_once()

    async def test_web_health_only_ready_after_telegram_setup_and_closed_on_exit(self):
        client = self.client(True)
        health = NS(ready=False, start=AsyncMock(), close=AsyncMock())

        async def connecting():
            self.assertFalse(health.ready)

        async def running():
            self.assertTrue(health.ready)

        client.connect.side_effect = connecting
        client.run_until_disconnected.side_effect = running
        with patch('hexa_userbot.HealthServer', return_value=health):
            await self.boot(client, http_port=10000)
        health.start.assert_awaited_once_with(10000)
        health.close.assert_awaited_once()
        self.assertFalse(health.ready)
        client.disconnect.assert_awaited_once()


@unittest.skipUnless(importlib.util.find_spec('pyrogram'), 'Install Pyrogram to test its hosting path')
class PyrogramRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def boot(self, authorized):
        client = NS(connect=AsyncMock(), is_user_authorized=AsyncMock(return_value=authorized),
                    start=AsyncMock(), disconnect=AsyncMock(), get_me=AsyncMock(return_value=NS(id=123)),
                    get_entity=AsyncMock(return_value=NS(id=572621020, bot=True)),
                    register_controller_handlers=Mock(), send_message=AsyncMock(),
                    run_until_disconnected=AsyncMock())
        self.client = client
        with tempfile.TemporaryDirectory() as temp:
            env = {'TELEGRAM_LIBRARY': 'pyrogram', 'TELEGRAM_API_ID': '12345',
                   'TELEGRAM_API_HASH': 'a' * 32, 'TELEGRAM_SESSION_STRING': 'test-pyrogram-session',
                   'TELEGRAM_SESSION_FILE': str(Path(temp)/'worker'), 'HEXA_NOTIFY_CHAT': 'me'}
            with patch.dict(os.environ, env, clear=True), \
                    patch('pyrogram_transport.PyrogramTransport', return_value=client) as factory, \
                    patch('hexa_userbot.Path.home', return_value=Path(temp)), \
                    contextlib.redirect_stdout(io.StringIO()):
                await main(mode='battle', non_interactive=True)
                factory.assert_called_once_with(12345, 'a' * 32, str(Path(temp)/'worker'), 'test-pyrogram-session')

    async def test_pyrogram_backend_boots_without_interactive_login(self):
        await self.boot(True)
        self.client.start.assert_not_awaited()
        self.client.connect.assert_awaited_once()
        self.client.register_controller_handlers.assert_called_once()
        self.client.send_message.assert_not_awaited()
        self.client.run_until_disconnected.assert_awaited_once()
        self.client.disconnect.assert_awaited_once()

    async def test_unauthorized_pyrogram_session_disconnects_without_prompt(self):
        with self.assertRaisesRegex(RuntimeError, 'no longer authorized'):
            await self.boot(False)
        self.client.start.assert_not_awaited()
        self.client.register_controller_handlers.assert_not_called()
        self.client.disconnect.assert_awaited_once()


@unittest.skipIf(telethon is None, 'Install requirements.txt to test the local session generator')
class SessionGeneratorTests(unittest.IsolatedAsyncioTestCase):
    async def test_pyrogram_session_is_exported_to_private_file_without_printing(self):
        from generate_session import generate
        from unittest.mock import MagicMock
        client = NS(export_session_string=AsyncMock(return_value='test-pyrogram-value'))
        context = MagicMock(__aenter__=AsyncMock(return_value=client), __aexit__=AsyncMock(return_value=False))
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp)/'.session-string-pyrogram'
            with patch.dict(os.environ, {'TELEGRAM_API_ID': '12345', 'TELEGRAM_API_HASH': 'a' * 32}, clear=True), \
                    patch('pyrogram.Client', return_value=context) as factory:
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    await generate(target, library='pyrogram')
                self.assertEqual(output.getvalue(), '')
                self.assertEqual(target.read_text(), 'test-pyrogram-value\n')
                self.assertEqual(target.stat().st_mode & 0o777, 0o600)
                factory.assert_called_once_with('hexa-session-maker', api_id=12345, api_hash='a' * 32, in_memory=True)

    async def test_session_is_saved_privately_without_printing_or_overwriting_it(self):
        from generate_session import generate
        client = NS(session=NS(save=Mock(return_value='test-session-value')))
        # Special methods are resolved on the class, so use a proper mock context.
        from unittest.mock import MagicMock
        context = MagicMock(__aenter__=AsyncMock(return_value=client), __aexit__=AsyncMock(return_value=False))
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp)/'.session-string'
            with patch.dict(os.environ, {'TELEGRAM_API_ID': '12345', 'TELEGRAM_API_HASH': 'a' * 32}, clear=True), \
                    patch('telethon.TelegramClient', return_value=context):
                capture = io.StringIO()
                with contextlib.redirect_stdout(capture):
                    await generate(target)
                self.assertEqual(capture.getvalue(), '')
                self.assertEqual(target.read_text(), 'test-session-value\n')
                self.assertEqual(target.stat().st_mode & 0o777, 0o600)
                with self.assertRaises(FileExistsError):
                    await generate(target)
                self.assertEqual(target.read_text(), 'test-session-value\n')
