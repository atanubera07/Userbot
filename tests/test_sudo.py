"""Authorization and command flows for shared Telegram controllers, offline."""
import asyncio
import os
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

from hexa_userbot import Controller, Settings


class SudoConfigurationTests(unittest.TestCase):
    def test_empty_allowlist_defaults_to_owner_only(self):
        for value in ['', '   ']:
            with patch.dict(os.environ, {'HEXA_SUDO_USERS': value}, clear=True):
                self.assertEqual(Settings.load().sudo_users, frozenset())

    def test_numeric_ids_are_parsed_and_duplicates_removed(self):
        with patch.dict(os.environ, {'HEXA_SUDO_USERS': ' 456,789,456 '}, clear=True):
            self.assertEqual(Settings.load().sudo_users, frozenset({456, 789}))

    def test_invalid_allowlist_rejects_whole_configuration(self):
        for value in ['0', '-100123', '@username', '456,', ',456', '456,,789',
                      '456 789', '1.5', '456,invalid', '１２３']:
            with self.subTest(value=value), patch.dict(os.environ, {'HEXA_SUDO_USERS': value}, clear=True):
                with self.assertRaisesRegex(ValueError, 'HEXA_SUDO_USERS'):
                    Settings.load()


class SudoCommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = NS(send_message=AsyncMock(return_value=NS(id=1)),
                         get_messages=AsyncMock(return_value=[]))
        self.cfg = Settings(sudo_users=frozenset({456}), notify='me', delay=0, hunt_delay=0)
        self.c = Controller(self.client, 572621020, 123, self.cfg)

    async def asyncTearDown(self):
        await self.c.stop(notify=False)

    def event(self, text, **overrides):
        return NS(**dict(dict(raw_text=text, sender_id=456, out=False,
                              chat_id=-100789, fwd_from=None, via_bot_id=None), **overrides))

    async def test_sudo_can_start_stop_and_resume_both_modes(self):
        for command, mode in [('.battle', 'battle'), ('.caught', 'catch')]:
            await self.c.command(self.event(command))
            await asyncio.sleep(0)
            self.assertTrue(self.c.running)
            self.assertEqual(self.c.mode, mode)
            self.assertTrue(any(c.args[:2] == (572621020, '/hunt')
                                for c in self.client.send_message.await_args_list))
            worker = self.c.worker
            await self.c.command(self.event('.stop'))
            self.assertFalse(self.c.running)
            self.assertTrue(worker.done())
            await self.c.command(self.event('.resume'))
            self.assertTrue(self.c.running)
            self.assertEqual(self.c.mode, mode)
            await self.c.stop(notify=False)

    async def test_sudo_status_help_reply_to_source_with_account_identity(self):
        for chat in [456, -100789]:
            for command in ['.status', '.help']:
                await self.c.command(self.event(command, chat_id=chat))
                reply = self.client.send_message.await_args.args
                self.assertEqual(reply[0], chat)
                self.assertIn('Account 123', reply[1])

    async def test_unauthorized_missing_sender_forward_inline_and_anonymous_rejected(self):
        for override in [{'sender_id': 789}, {'sender_id': None}, {'sender_id': 789, 'out': True},
                         {'sender_id': 123}, {'fwd_from': NS()}, {'via_bot_id': 99},
                         {'chat_id': None}, {'sender_id': -100789}]:
            await self.c.command(self.event('.battle', **override))
        await self.c.command(NS(raw_text='.battle', out=True, chat_id=123))
        self.assertFalse(self.c.running)
        self.client.send_message.assert_not_awaited()

    async def test_owner_retains_access_when_not_in_sudo_list(self):
        await self.c.command(self.event('.battle', sender_id=123, out=True))
        self.assertTrue(self.c.running)

    async def test_removing_sudo_revokes_access(self):
        self.cfg.sudo_users = frozenset()
        await self.c.command(self.event('.battle'))
        self.assertFalse(self.c.running)
        self.client.send_message.assert_not_awaited()

    async def test_sudo_cannot_use_removed_or_extended_commands(self):
        for text in ['/go', '/stop', '/status', '.hexa battle', '.battle extra', '.stop\n.battle']:
            await self.c.command(self.event(text))
        self.assertFalse(self.c.running)
        self.client.send_message.assert_not_awaited()

    async def test_one_group_command_controls_multiple_accounts(self):
        other = Controller(self.client, 572621020, 999, self.cfg)
        try:
            await asyncio.gather(self.c.command(self.event('.battle')),
                                 other.command(self.event('.battle')))
            self.assertTrue(self.c.running)
            self.assertTrue(other.running)
            await self.c.command(self.event('.status'))
            await other.command(self.event('.status'))
            replies = [call.args[1] for call in self.client.send_message.await_args_list]
            self.assertTrue(any('Account 123:' in text for text in replies))
            self.assertTrue(any('Account 999:' in text for text in replies))
        finally:
            await other.stop(notify=False)
