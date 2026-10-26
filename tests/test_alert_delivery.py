"""Alert delivery, failure diagnostics and recovery without Telegram access."""
import asyncio
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

from hexa_userbot import Controller, Settings


class AlertDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.group = -1001234567890
        self.client = NS(send_message=AsyncMock(return_value=NS(id=1)),
                         get_input_entity=AsyncMock(return_value=self.group))
        self.cfg = Settings(notify=self.group)
        self.c = Controller(self.client, 572621020, 123, self.cfg)

    async def test_successful_group_alert_does_not_use_fallback(self):
        await self.c.note('Started battle mode')
        self.client.send_message.assert_awaited_once()
        peer, text = self.client.send_message.await_args.args
        self.assertEqual(peer, self.group)
        self.assertIn('Account 123', text)
        self.assertIn('@Kill_switch8', text)
        self.assertIn('Started battle mode', text)
        self.assertNotIn('delivery failed', text)
        self.assertEqual(self.c.alert_state, 'configured')
        self.assertIsNone(self.c.alert_error)

    async def test_failed_group_send_falls_back_without_resending_to_group(self):
        self.client.send_message.side_effect = [RuntimeError('secret-detail-not-for-chat'), NS(id=1)]
        await self.c.note('Verification detected')
        calls = self.client.send_message.await_args_list
        self.assertEqual([call.args[0] for call in calls], [self.group, self.c.owner])
        text = calls[-1].args[1]
        self.assertIn('RuntimeError', text)
        self.assertIn(str(self.group), text)
        self.assertIn('UTC', text)
        self.assertIn('Verification detected', text)
        self.assertNotIn('secret-detail-not-for-chat', text)

    async def test_delivery_failure_preserves_running_encounter_state(self):
        self.c.running = True
        self.c.state = 'attack'
        self.c.reason = 'Running'
        self.c.generation = 17
        self.client.send_message.side_effect = [OSError('uncertain network result'), NS(id=1)]
        await self.c.note('Progress alert')
        self.assertTrue(self.c.running)
        self.assertEqual(self.c.state, 'attack')
        self.assertEqual(self.c.reason, 'Running')
        self.assertEqual(self.c.generation, 17)
        self.assertEqual(self.client.send_message.await_count, 2)

    async def test_verification_remains_paused_when_both_destinations_fail(self):
        self.c.running = True
        self.client.send_message.side_effect = RuntimeError('offline')
        await self.c.pause_verification('What is the capital of France?', message_id=8)
        self.assertFalse(self.c.running)
        self.assertEqual(self.c.state, 'verification')
        self.assertEqual(self.c.verification_message_id, 8)
        self.assertIn('capital', self.c.verification_text)
        self.assertEqual(self.client.send_message.await_count, 2)
        self.assertEqual(self.c.alert_state, 'failed')
        self.assertEqual(self.c.alert_error, 'RuntimeError')

    async def test_cancellation_is_not_hidden_or_resent(self):
        self.client.send_message.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.c.note('Cancelled alert')
        self.client.send_message.assert_awaited_once()

    async def test_startup_fallback_recovers_when_group_resolution_becomes_available(self):
        self.c.alert_peer = self.c.owner
        self.c.alert_state = 'fallback'
        self.c.alert_error = 'ValueError'
        with patch('hexa_userbot.resolve_alert_chat', new=AsyncMock(return_value=self.group)) as resolve:
            await self.c.note('Recovered group alert')
        resolve.assert_awaited_once_with(self.client, self.group)
        self.assertEqual(self.c.alert_peer, self.group)
        self.assertEqual(self.c.alert_state, 'configured')
        self.assertIsNone(self.c.alert_error)
        self.client.send_message.assert_awaited_once()
        self.assertEqual(self.client.send_message.await_args.args[0], self.group)

    async def test_unresolved_group_uses_cooldown_then_recovers(self):
        self.c.alert_peer = self.c.owner
        self.c.alert_state = 'fallback'
        resolve = AsyncMock(side_effect=[ValueError('hidden-resolution-detail'), self.group])
        with patch('hexa_userbot.resolve_alert_chat', new=resolve):
            await self.c.note('First unresolved alert')
            deadline = self.c._alert_resolve_after
            self.assertGreater(deadline, asyncio.get_running_loop().time() + 59)
            await self.c.note('Second unresolved alert')
            self.assertEqual(resolve.await_count, 1)
            self.assertEqual(self.c._alert_resolve_after, deadline)
            self.c._alert_resolve_after = 0  # Simulate expiry without sleeping.
            await self.c.note('Recovered alert')
        self.assertEqual(resolve.await_count, 2)
        calls = self.client.send_message.await_args_list
        self.assertEqual([call.args[0] for call in calls], [self.c.owner, self.c.owner, self.group])
        self.assertIn('ValueError', calls[0].args[1])
        self.assertNotIn('hidden-resolution-detail', calls[0].args[1])
        self.assertIn('Second unresolved alert', calls[1].args[1])
        self.assertEqual(self.c.alert_state, 'configured')
        self.assertIsNone(self.c.alert_error)

    async def test_status_reports_failure_and_clears_it_after_success(self):
        self.client.send_message.side_effect = [RuntimeError('hidden'), NS(id=1)]
        await self.c.note('Group delivery failed')
        self.client.send_message.side_effect = None
        event = NS(raw_text='.status', sender_id=self.c.owner, out=True,
                   chat_id=self.group, fwd_from=None, via_bot_id=None)
        await self.c.command(event)
        self.assertIn('alerts=fallback', self.client.send_message.await_args.args[1])
        self.assertIn('last_alert_error=RuntimeError', self.client.send_message.await_args.args[1])
        await self.c.note('Group delivery recovered')
        await self.c.command(event)
        self.assertIn('alerts=configured', self.client.send_message.await_args.args[1])
        self.assertIn('last_alert_error=none', self.client.send_message.await_args.args[1])

    async def test_intentional_saved_messages_destination_does_not_retry_group_resolution(self):
        c = Controller(self.client, 572621020, 123, Settings(notify='me'))
        with patch('hexa_userbot.resolve_alert_chat', new=AsyncMock()) as resolve:
            await c.note('Private alert by choice')
        resolve.assert_not_awaited()
        self.client.send_message.assert_awaited_once()
        self.assertEqual(self.client.send_message.await_args.args[0], 'me')
        self.assertEqual(c.alert_state, 'saved_messages')
        self.assertIsNone(c.alert_error)

