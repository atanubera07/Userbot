"""Real Pyrogram message construction and retry behavior without Telegram access."""
import asyncio
import importlib.util
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

from pyrogram_transport import PyrogramTransport, secure_message_id


@unittest.skipIf(importlib.util.find_spec('pyrogram') is None, 'Install Pyrogram to verify message IDs')
class MessageIdTests(unittest.IsolatedAsyncioTestCase):
    def transport(self, name='offline'):
        # Construct the actual client and use its real send_message method.
        # Storage is never opened and invoke never reaches a network connection.
        from pyrogram import raw
        transport = PyrogramTransport(12345, 'a' * 32, '/tmp/' + name, 'unused-offline-session')
        self.addCleanup(transport.raw.executor.shutdown, wait=False)
        transport.raw.resolve_peer = AsyncMock(return_value=raw.types.InputPeerChat(chat_id=42))
        transport.raw.invoke = AsyncMock(return_value=self.sent())
        return transport

    def sent(self):
        from pyrogram import raw
        return raw.types.UpdateShortSentMessage(id=7, pts=1, pts_count=1, date=1793000000, out=True)

    async def test_independent_services_do_not_reuse_ids_at_the_same_clock_second(self):
        from pyrogram.session.internals.msg_id import MsgId
        transports = [self.transport('offline-' + str(i)) for i in range(5)]
        requests = []
        with patch('pyrogram.session.internals.msg_id.time.time', return_value=1793000000), \
                patch('pyrogram_transport.secrets.randbelow', side_effect=[101, 202, 303, 404, 505]):
            for transport in transports:
                # Separate Render processes each have their own fresh MsgId
                # counter. Reset it to reproduce those independent processes.
                with patch.object(MsgId, 'last_time', 0), patch.object(MsgId, 'offset', 0):
                    result = await transport.send_message(-42, 'shared group alert')
                self.assertEqual(result.raw_text, 'shared group alert')
                requests.append(transport.raw.invoke.await_args.args[0])
        ids = [request.random_id for request in requests]
        self.assertEqual(len(set(ids)), 5)
        self.assertTrue(all(0 < ident < 2 ** 63 for ident in ids))
        self.assertTrue(all(request.peer.chat_id == 42 for request in requests))

    async def test_message_ids_stay_within_the_positive_signed_long_range(self):
        for source_value in [0, 2 ** 63 - 2]:
            with self.subTest(source_value=source_value), \
                    patch('pyrogram_transport.secrets.randbelow', return_value=source_value):
                ident = secure_message_id()
                self.assertIsInstance(ident, int)
                self.assertTrue(0 < ident < 2 ** 63)

    async def test_duplicate_rejection_rebuilds_once_with_a_new_message_id(self):
        from pyrogram.errors import RandomIdDuplicate
        transport = self.transport()
        transport.raw.invoke.side_effect = [RandomIdDuplicate(), self.sent()]
        with patch('pyrogram_transport.secrets.randbelow', side_effect=[101, 202]):
            result = await transport.send_message(-42, 'alert')
        self.assertEqual(result.raw_text, 'alert')
        self.assertEqual(transport.raw.invoke.await_count, 2)
        first, second = [call.args[0] for call in transport.raw.invoke.await_args_list]
        self.assertIsNot(first, second)
        self.assertNotEqual(first.random_id, second.random_id)
        self.assertEqual(first.message, second.message)
        self.assertEqual(first.peer.chat_id, second.peer.chat_id)

    async def test_duplicate_rejection_does_not_loop(self):
        from pyrogram.errors import RandomIdDuplicate
        transport = self.transport()
        transport.raw.invoke.side_effect = RandomIdDuplicate()
        with self.assertRaises(RandomIdDuplicate):
            await transport.send_message(-42, 'alert')
        self.assertEqual(transport.raw.invoke.await_count, 2)

    async def test_permissions_timeouts_and_flood_waits_are_not_resent(self):
        from pyrogram.errors import ChatWriteForbidden, FloodWait
        transport = self.transport()
        for error in [ChatWriteForbidden(), asyncio.TimeoutError(), FloodWait(30), OSError('offline')]:
            with self.subTest(error=type(error).__name__):
                transport.raw.invoke.reset_mock()
                transport.raw.invoke.side_effect = error
                with self.assertRaises(type(error)):
                    await transport.send_message(-42, 'alert')
                self.assertEqual(transport.raw.invoke.await_count, 1)

    async def test_session_network_retry_keeps_the_original_random_id(self):
        from pyrogram.session.session import Session
        transport = self.transport()
        session = Session.__new__(Session)
        session.client = NS(name='offline')
        session.is_started = asyncio.Event()
        session.is_started.set()
        session.send = AsyncMock(side_effect=[OSError('temporary offline'), self.sent()])

        async def invoke(query):
            return await session.invoke(query, retries=1)

        transport.raw.invoke.side_effect = invoke
        with patch('pyrogram.session.session.asyncio.sleep', new=AsyncMock()), \
                patch('pyrogram_transport.secrets.randbelow', return_value=101) as random_source:
            await transport.send_message(-42, 'alert')
        self.assertEqual(session.send.await_count, 2)
        first, second = [call.args[0] for call in session.send.await_args_list]
        self.assertIs(first, second)
        self.assertEqual(first.random_id, second.random_id)
        random_source.assert_called_once()
