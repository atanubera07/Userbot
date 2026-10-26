"""Telegram update gaps must recover owner commands and bot messages."""
import asyncio
import importlib.util
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

from pyrogram_transport import PyrogramTransport


@unittest.skipUnless(importlib.util.find_spec('pyrogram'), 'Install Pyrogram')
class UpdateRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from pyrogram import raw
        self.raw = raw
        self.client = PyrogramTransport.__new__(PyrogramTransport)
        self.client.raw = NS(invoke=AsyncMock())
        self.client._original_updates = AsyncMock()
        self.client._update_lock = asyncio.Lock()
        self.client._remember_state(NS(pts=10, qts=2, date=100, seq=3))

    def state(self, pts):
        return self.raw.types.updates.State(pts=pts, qts=2, date=101, seq=4, unread_count=0)

    async def test_gap_recovers_missed_messages_through_normal_parser(self):
        message = NS(id=20, message='.status')
        difference = self.raw.types.updates.Difference(
            new_messages=[message], new_encrypted_messages=[], other_updates=[],
            chats=[], users=[], state=self.state(15))
        self.client.raw.invoke.return_value = difference
        await self.client._handle_updates(self.raw.types.UpdatesTooLong())
        request = self.client.raw.invoke.await_args.args[0]
        self.assertEqual((request.pts, request.date, request.qts), (10, 100, 2))
        packet = self.client._original_updates.await_args.args[0]
        self.assertIs(packet.updates[0].message, message)
        self.assertEqual(self.client._update_state.pts, 15)

    async def test_sliced_difference_advances_cursor_before_next_page(self):
        self.client.raw.invoke.side_effect = [
            self.raw.types.updates.DifferenceSlice(new_messages=[], new_encrypted_messages=[],
                other_updates=[], chats=[], users=[], intermediate_state=self.state(20)),
            self.raw.types.updates.DifferenceEmpty(date=102, seq=5)]
        await self.client._handle_updates(self.raw.types.UpdatesTooLong())
        calls = self.client.raw.invoke.await_args_list
        self.assertEqual([call.args[0].pts for call in calls], [10, 20])
        self.assertEqual(self.client._update_state.date, 102)

    async def test_expired_backlog_resets_without_replaying_commands(self):
        self.client.raw.invoke.side_effect = [
            self.raw.types.updates.DifferenceTooLong(pts=200), self.state(210)]
        await self.client._handle_updates(self.raw.types.UpdatesTooLong())
        self.assertEqual(self.client._update_state.pts, 210)
        self.client._original_updates.assert_not_awaited()

    async def test_channel_pts_cannot_corrupt_account_cursor(self):
        packet = self.raw.types.Updates(updates=[
            self.raw.types.UpdateDeleteChannelMessages(channel_id=123, messages=[5], pts=999, pts_count=1),
            self.raw.types.UpdateDeleteMessages(messages=[6], pts=12, pts_count=1)],
            users=[], chats=[], date=101, seq=4)
        await self.client._handle_updates(packet)
        self.assertEqual(self.client._update_state.pts, 12)
        self.client._original_updates.assert_awaited_once_with(packet)
