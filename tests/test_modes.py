"""Mode selection and complete command-to-result flows without Telegram access."""
import asyncio
import os
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

from hexa_userbot import Controller, Settings, normalized, runtime_config


class ModeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.messages = {}
        self.hunt_sent = asyncio.Event()

        async def get_messages(peer, ids=None, limit=None):
            if limit is not None:
                return sorted(self.messages.values(), key=lambda msg: msg.id, reverse=True)[:limit]
            return self.messages.get(ids)

        async def send_message(peer, text, **kwargs):
            if text == '/hunt':
                self.hunt_sent.set()
            return NS(id=1)

        self.client = NS(get_messages=AsyncMock(side_effect=get_messages),
                         send_message=AsyncMock(side_effect=send_message))
        self.cfg = Settings(targets={normalized('Abra')}, delay=0, hunt_delay=0, notify='me')
        self.c = Controller(self.client, 572621020, 123, self.cfg)

    async def asyncTearDown(self):
        await self.c.stop(notify=False)

    def command(self, text, **overrides):
        return NS(**dict(dict(raw_text=text, out=True, chat_id=123,
                              fwd_from=None, via_bot_id=None), **overrides))

    def message(self, text, labels=(), ident=3, rows=None):
        clicked = asyncio.Event()

        async def click(*args):
            clicked.set()
            return NS(message=None)

        rows = rows if rows is not None else [labels]
        return NS(id=ident, raw_text=text, buttons=[[NS(text=x) for x in row] for row in rows if row],
                  edit_date=None, click=AsyncMock(side_effect=click), clicked=clicked)

    def caption(self, hp=80, maximum=80, name='Abra'):
        return (f'Wild {name} [Psychic]\nLv. 20 • HP {hp}/{maximum}\n'
                'Current turn: Player\n'
                'Tackle [Normal]\nPower: 40, Accuracy: 100\n'
                'Psychic [Psychic]\nPower: 90, Accuracy: 100')

    moves = ['Tackle', 'Psychic', 'Poke Ball', 'Run', 'Pokemons']

    async def start(self, mode):
        self.hunt_sent.clear()
        await self.c.command(self.command(f'.hexa {mode}'))
        await asyncio.wait_for(self.hunt_sent.wait(), 1)

    async def feed_until_click(self, msg):
        self.messages[msg.id] = msg
        await self.c.receive(NS(message=msg))
        await asyncio.wait_for(msg.clicked.wait(), 1)

    async def test_battle_command_queue_attack_faint_and_next_hunt(self):
        await self.start('battle')
        # Rhydon is deliberately absent from the catch list.
        encounter = self.message('A wild Rhydon (Lv. 20) has appeared', ['Battle'], ident=2)
        await self.feed_until_click(encounter)
        battle = self.message(self.caption(hp=1, name='Rhydon'), self.moves)
        await self.feed_until_click(battle)
        battle.click.assert_awaited_once_with(0, 1)
        self.assertEqual(self.c.throws, 0)
        self.hunt_sent.clear()
        result = self.message('The wild Rhydon fainted!', ident=4)
        self.messages[result.id] = result
        await self.c.receive(NS(message=result))
        await asyncio.wait_for(self.hunt_sent.wait(), 1)
        self.assertEqual(self.c.mode, 'battle')

    async def test_catch_command_queue_weaken_throw_and_next_hunt(self):
        await self.start('catch')
        encounter = self.message('A wild Abra (Lv. 20) has appeared', ['Battle'], ident=2)
        await self.feed_until_click(encounter)
        battle = self.message(self.caption(), self.moves)
        await self.feed_until_click(battle)
        battle.click.assert_awaited_once_with(0, 0)
        low = self.message(self.caption(hp=40), self.moves)
        await self.feed_until_click(low)
        low.click.assert_awaited_once_with(0, 2)
        balls = self.message('Choose which ball?', rows=[['Regular Ball x5'], ['Back']])
        await self.feed_until_click(balls)
        balls.click.assert_awaited_once_with(0, 0)
        self.assertEqual(self.c.throws, 1)
        self.hunt_sent.clear()
        result = self.message('You caught a wild Abra.', ident=4)
        self.messages[result.id] = result
        await self.c.receive(NS(message=result))
        await asyncio.wait_for(self.hunt_sent.wait(), 1)
        self.assertEqual(self.c.mode, 'catch')

    async def test_running_mode_cannot_change_mid_encounter(self):
        await self.start('battle')
        worker = self.c.worker
        sends = self.client.send_message.await_count
        await self.c.command(self.command('.hexa catch'))
        self.assertEqual(self.c.mode, 'battle')
        self.assertIs(self.c.worker, worker)
        self.assertEqual(self.client.send_message.await_count, sends + 1)
        self.assertIn('Already running in battle mode', self.client.send_message.await_args.args[1])

    async def test_stop_then_switch_in_both_directions(self):
        await self.start('battle')
        first = self.c.worker
        await self.c.command(self.command('.hexa stop'))
        self.assertTrue(first.done())
        await self.start('catch')
        self.assertEqual(self.c.mode, 'catch')
        self.assertIsNot(self.c.worker, first)
        second = self.c.worker
        await self.c.command(self.command('.hexa stop'))
        self.assertTrue(second.done())
        await self.start('battle')
        self.assertEqual(self.c.mode, 'battle')

    async def test_go_restarts_the_selected_mode(self):
        await self.start('catch')
        await self.c.command(self.command('/stop'))
        self.hunt_sent.clear()
        await self.c.command(self.command('/go'))
        await asyncio.wait_for(self.hunt_sent.wait(), 1)
        self.assertEqual(self.c.mode, 'catch')

    async def test_duplicate_start_does_not_spawn_another_worker(self):
        await asyncio.gather(self.c.command(self.command('.hexa catch')),
                             self.c.command(self.command('.hexa battle')))
        await asyncio.wait_for(self.hunt_sent.wait(), 1)
        self.assertEqual(sum(call.args[1] == '/hunt' for call in self.client.send_message.await_args_list), 1)
        self.assertEqual(self.c.mode, 'catch')

    async def test_unauthorized_and_forwarded_commands_do_nothing(self):
        for event in [self.command('.hexa catch', out=False),
                      self.command('.hexa catch', chat_id=-100456),
                      self.command('.hexa catch', fwd_from=NS()),
                      self.command('.hexa catch', via_bot_id=789)]:
            await self.c.command(event)
        self.client.send_message.assert_not_awaited()
        self.assertFalse(self.c.running)

    async def test_commands_in_bot_chat_with_entity_peer(self):
        self.c.peer = NS(id=572621020)
        await self.c.command(self.command('.hexa catch', chat_id=572621020))
        await asyncio.wait_for(self.hunt_sent.wait(), 1)
        self.assertEqual(self.c.mode, 'catch')

    async def test_unrelated_messages_and_extra_arguments_do_not_start(self):
        for text in ['hi .hexa catch', '.hexacatch', '/go now', '.hexa catch extra', '.hexa nonsense']:
            await self.c.command(self.command(text))
        self.assertFalse(self.c.running)
        self.assertFalse(any(call.args[1] == '/hunt' for call in self.client.send_message.await_args_list))

    async def test_case_insensitive_commands(self):
        await self.c.command(self.command('  .HEXA CATCH  '))
        await asyncio.wait_for(self.hunt_sent.wait(), 1)
        self.assertEqual(self.c.mode, 'catch')

    async def test_status_and_help_respond_in_control_chat(self):
        await self.c.command(self.command('.hexa status'))
        self.assertEqual(self.client.send_message.await_args.args[0], 123)
        self.assertIn('mode=battle', self.client.send_message.await_args.args[1])
        for text in ['.hexa', '.hexa help']:
            await self.c.command(self.command(text))
            self.assertIn('.hexa catch', self.client.send_message.await_args.args[1])
        self.assertFalse(self.c.running)

    async def test_stop_cancels_battle_attack_before_click(self):
        await self.start('battle')
        await self.feed_until_click(self.message('A wild Abra (Lv. 20) has appeared', ['Battle'], ident=2))
        self.cfg.delay = 60
        battle = self.message(self.caption(), self.moves)
        self.messages[battle.id] = battle
        await self.c.receive(NS(message=battle))
        # The worker is either queued or sleeping; neither may act after /stop.
        await asyncio.sleep(0)
        worker = self.c.worker
        await self.c.command(self.command('.hexa stop'))
        self.assertTrue(worker.done())
        battle.click.assert_not_awaited()
        self.assertFalse(self.c.running)

    async def test_specials_and_verification_pause_both_modes(self):
        for mode in ['battle', 'catch']:
            for text in ['A wild Mewtwo (Lv. 20) has appeared',
                         'A wild Shiny Abra (Lv. 20) has appeared',
                         '🎃 A rare Halloween form has appeared!',
                         'Which animal is a mammal?', 'Daily hunt limit reached']:
                await self.start(mode)
                worker = self.c.worker
                msg = self.message(text, ['Battle'], ident=2)
                await self.c.receive(NS(message=msg))
                self.assertFalse(self.c.running, (mode, text))
                self.assertTrue(worker.done())
                msg.click.assert_not_awaited()
                # Normal later messages and timeout recovery cannot restart it.
                sends = self.client.send_message.await_count
                await self.c.receive(NS(message=self.message('Verification completed', ident=4)))
                await self.c.recover_timeout(self.c.generation)
                self.assertEqual(self.client.send_message.await_count, sends)


class ConfigurationTests(unittest.TestCase):
    def test_missing_api_credentials_are_rejected(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, 'TELEGRAM_API_ID'):
                runtime_config()

    def test_api_configuration_uses_the_users_credentials(self):
        with patch.dict(os.environ, {'TELEGRAM_API_ID': '12345', 'TELEGRAM_API_HASH': 'f' * 32}, clear=True):
            self.assertEqual(runtime_config()[:2], (12345, 'f' * 32))

    def test_default_and_invalid_modes(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(Settings.load().mode, 'battle')
        with patch.dict(os.environ, {'HEXA_MODE': 'catch'}, clear=True):
            self.assertEqual(Settings.load().mode, 'catch')
        with patch.dict(os.environ, {'HEXA_MODE': 'typo'}, clear=True):
            with self.assertRaisesRegex(ValueError, 'HEXA_MODE'):
                Settings.load()
