"""Regression tests adapted from the user's test.py; Telegram is mocked."""
import asyncio
import os
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

from hexa_userbot import (
    BALLS, LEGENDARY_TARGETS, TARGETS, WILD, Controller, Settings,
    ball_details, calculate_health_percentage, choose_ball, clean,
    encounter_name, escape_confirmed, menu_label, normalized, resolve_alert_chat,
    resolve_bot, runtime_config, screen_key, switch_label, terminal, verification_prompt,
)

class BattleTests(unittest.IsolatedAsyncioTestCase):
    def msg(self, text, labels=(), ident=3, rows=None):
        rows = rows if rows is not None else [labels]
        return NS(id=ident, raw_text=text, buttons=[[NS(text=b) for b in row] for row in rows if row],
                  click=AsyncMock(return_value=NS(message=None)), edit_date=None)

    async def asyncSetUp(self):
        self.messages = {}
        async def get_messages(peer, ids=None, limit=None):
            if limit is not None:
                return sorted(self.messages.values(), key=lambda m: m.id, reverse=True)[:limit]
            return self.messages.get(ids)
        self.client = NS(get_messages=AsyncMock(side_effect=get_messages),
                         send_message=AsyncMock(return_value=NS(id=1)))
        self.cfg = Settings(targets={'*'}, delay=0, hunt_delay=0, notify='me')
        self.c = Controller(self.client, 572621020, 123, self.cfg)
        self.c.running = True
        self.c.generation = 1
        await self.c.send_hunt()

    async def feed(self, msg):
        self.messages[msg.id] = msg
        await self.c.process(msg)

    def caption(self, current=85, maximum=85, own=221, name='Rhydon'):
        return (f'Wild {name} [Ground / Rock]\nLv. 23 • HP {current}/{maximum}\n'
                f'Current turn: Player\nArcanine [Fire]\nLv. 73 • HP {own}/221\n'
                'Retaliate [Normal]\nPower: 70, Accuracy: 100\n'
                'Reversal [Fighting]\nPower: 60, Accuracy: 100\n'
                'Take Down [Normal]\nPower: 90, Accuracy: 85\n'
                'Flamethrower [Fire]\nPower: 90, Accuracy: 100')

    moves = ['Retaliate', 'Reversal', 'Take Down', 'Flamethrower', 'Poke Ball', 'Run', 'Pokemons']

    balls = [['Regular Ball x94', 'Great Ball x39', 'Ultra Ball x24'], ['Repeat Ball x14'], ['Back']]

    async def encounter(self, name='Rhydon'):
        msg = self.msg(f'A wild {name} (Lv. 23) has appeared', ['Battle', 'EV Yield'], ident=2)
        await self.feed(msg)
        msg.click.assert_awaited_once_with(0, 0)
        return msg

    question = 'Which one does not belong with the others: zebra, orange, grape, lemon?'

    async def test_verification_screenshot_pauses_without_answer(self):
        question = self.msg(self.question, ident=4)
        await self.c.receive(NS(message=question))
        self.assertFalse(self.c.running)
        self.assertEqual(self.c.state, 'verification')
        self.assertEqual(self.c.verification_message_id, 4)
        self.assertEqual(self.c.verification_text, self.question)
        question.click.assert_not_awaited()
        # Only the initial hunt and a notification to Saved Messages.
        self.assertEqual(self.client.send_message.await_count, 2)
        self.assertEqual(self.client.send_message.await_args.args[0], 'me')
        self.assertIn(self.question, self.client.send_message.await_args.args[1])

    async def test_verification_processed_directly(self):
        await self.encounter()
        question = self.msg(self.question, ident=4)
        await self.feed(question)
        self.assertFalse(self.c.running)
        question.click.assert_not_awaited()

    async def test_legendary_list_all_pause_before_action(self):
        for name in LEGENDARY_TARGETS:
            self.c.running = True
            self.client.send_message.reset_mock()
            msg = self.msg(f'A wild {name} (Lv. 23) has appeared', ['Battle'], ident=2)
            await self.feed(msg)
            self.assertFalse(self.c.running, name)
            self.assertIn(f'Legendary alert: {name}', self.c.reason)
            self.assertIn(self.c.cfg.alert_tag, self.client.send_message.await_args.args[1])
            msg.click.assert_not_awaited()
            self.assertFalse(any(c.args[1] == '/hunt' for c in self.client.send_message.await_args_list))

    async def test_legendary_battle_caption_pauses(self):
        msg = self.msg(self.caption(name='Type: Null'), self.moves, ident=2)
        await self.feed(msg)
        self.assertFalse(self.c.running)
        self.assertIn('Type: Null', self.c.reason)
        msg.click.assert_not_awaited()

    async def test_legendary_receive_and_timeout_stay_paused(self):
        msg = self.msg('A wild Ho-Oh (Lv. 23) has appeared', ['Battle'], ident=2)
        await self.c.receive(NS(message=msg))
        self.assertFalse(self.c.running)
        self.assertTrue(self.c.queue.empty())
        self.client.send_message.reset_mock()
        await self.c.recover_timeout(self.c.generation)
        self.client.send_message.assert_not_awaited()
        msg.click.assert_not_awaited()

    async def test_own_legendary_does_not_trigger_alert(self):
        text = self.caption().replace('Arcanine [Fire]', 'Mewtwo [Psychic]')
        self.assertFalse(await self.c.safety(self.msg(text)))
        self.assertTrue(self.c.running)

    async def test_legendary_alert_failure_still_pauses(self):
        self.client.send_message.side_effect = RuntimeError('notification unavailable')
        await self.feed(self.msg('A wild Mewtwo (Lv. 23) has appeared', ['Battle'], ident=2))
        self.assertFalse(self.c.running)

    async def test_daily_limit_pauses_without_disconnecting(self):
        msg = self.msg('Daily hunt limit reached', ident=2)
        await self.feed(msg)
        self.assertFalse(self.c.running)
        self.assertEqual(self.c.state, 'paused')

    async def test_halloween_announcement_pauses_and_alerts(self):
        msg = self.msg('🎃 A rare Halloween form has appeared!', ident=2)
        await self.feed(msg)
        self.assertFalse(self.c.running)
        self.assertIn('Halloween Pokémon detected', self.c.reason)
        self.assertIn('Halloween Pokémon detected', self.client.send_message.await_args.args[1])
        msg.click.assert_not_awaited()

    async def test_halloween_non_target_caption_never_skips(self):
        self.client.send_message.reset_mock()
        msg = self.msg('A wild Lunatone 🎃 (Lv. 11) has appeared', ['Battle', 'EV Yield'], ident=2)
        await self.feed(msg)
        self.assertFalse(self.c.running)
        msg.click.assert_not_awaited()
        self.assertFalse(any(call.args[1] == '/hunt' for call in self.client.send_message.await_args_list))

    async def test_halloween_receive_pauses_before_queueing(self):
        msg = self.msg('A wild Lunatone 🎃 (Lv. 11) has appeared', ['Battle'], ident=2)
        await self.c.receive(NS(message=msg))
        self.assertFalse(self.c.running)
        self.assertTrue(self.c.queue.empty())
        msg.click.assert_not_awaited()

    async def test_halloween_timeout_does_not_restart(self):
        await self.feed(self.msg('🎃 A rare Halloween form has appeared!', ident=2))
        self.client.send_message.reset_mock()
        await self.c.recover_timeout(self.c.generation)
        self.client.send_message.assert_not_awaited()

    async def test_shiny_pauses_before_click(self):
        msg = self.msg('A wild Shiny Abra (Lv. 2) has appeared', ['Battle'], ident=2)
        await self.feed(msg)
        msg.click.assert_not_awaited()
        self.assertIn('Shiny', self.c.reason)

    async def test_shiny_notification_failure_still_pauses(self):
        self.client.send_message.side_effect = RuntimeError('notification unavailable')
        await self.feed(self.msg('Shiny found!', ident=2))
        self.assertFalse(self.c.running)

    async def test_timeout_does_not_resume_user_stop(self):
        generation = self.c.generation
        await self.c.stop(notify=False)
        calls = self.client.send_message.await_count
        await self.c.recover_timeout(generation)
        self.assertEqual(self.client.send_message.await_count, calls)
        self.assertFalse(self.c.running)

    async def test_username_resolution_and_id_validation(self):
        bot = NS(id=572621020, bot=True)
        client = NS(get_entity=AsyncMock(return_value=bot))
        self.assertIs(await resolve_bot(client, '@HeXamonbot', None), bot)
        with self.assertRaises(ValueError):
            await resolve_bot(client, '@HeXamonbot', 123)


    async def test_unlisted_species_battles(self):
        await self.encounter('Caterpie')
        self.assertTrue(self.c.running)

    async def test_attacks_at_low_and_full_hp_without_balls(self):
        await self.encounter()
        for hp in [85, 20, 1]:
            msg = self.msg(self.caption(current=hp), self.moves)
            await self.feed(msg)
            msg.click.assert_awaited_once_with(0, 2)
            self.assertEqual(self.c.throws, 0)
        self.assertEqual(self.c.attacks, 3)

    async def test_configured_attack_move(self):
        self.cfg.attack_move = 'Flamethrower'
        await self.encounter()
        msg = self.msg(self.caption(current=1), self.moves)
        await self.feed(msg)
        msg.click.assert_awaited_once_with(0, 3)

    async def test_zero_wild_hp_starts_next_hunt(self):
        await self.encounter()
        self.client.send_message.reset_mock()
        await self.feed(self.msg(self.caption(current=0)))
        self.assertEqual(self.client.send_message.await_args.args[1], '/hunt')

    async def test_confirmed_faint_starts_next_hunt(self):
        await self.encounter()
        self.client.send_message.reset_mock()
        await self.feed(self.msg('The wild Rhydon fainted.'))
        self.assertEqual(self.client.send_message.await_args.args[1], '/hunt')

    async def test_safari_never_catches(self):
        msg = self.msg('A wild Rhydon (Lv. 23) has appeared\nSafari Balls remaining: 45', ['Catch', 'EV Yield'], ident=2)
        await self.feed(msg)
        self.assertFalse(self.c.running)
        msg.click.assert_not_awaited()

    async def test_ball_menu_returns_to_battle(self):
        await self.encounter()
        msg = self.msg(self.caption(current=1), rows=self.balls)
        await self.feed(msg)
        msg.click.assert_awaited_once_with(2, 0)
        self.assertEqual(self.c.throws, 0)

    async def test_click_guard_blocks_all_catching_controls(self):
        for label in ['Poke Ball', 'Regular Ball x5', 'Catch']:
            self.c.running = True
            msg = self.msg('Choose', [label])
            self.messages[msg.id] = msg
            await self.c.click(msg, label)
            msg.click.assert_not_awaited()
            self.assertFalse(self.c.running)

    async def test_fainted_team_switch(self):
        await self.encounter()
        msg = self.msg('Choose your next pokemon', ['1', '2', 'View Team'])
        await self.feed(msg)
        msg.click.assert_awaited_once_with(0, 0)
        self.assertEqual(self.c.state, 'switching')

    async def test_attack_cap_pauses(self):
        await self.encounter()
        self.c.attacks = self.cfg.max_attacks
        msg = self.msg(self.caption(), self.moves)
        await self.feed(msg)
        msg.click.assert_not_awaited()
        self.assertFalse(self.c.running)

    async def test_timeout_escapes_stalled_battle(self):
        await self.encounter()
        msg = self.msg(self.caption(), self.moves)
        await self.feed(msg)
        msg.click.reset_mock()
        await self.c.recover_timeout(self.c.generation)
        msg.click.assert_awaited_once_with(0, 5)

    async def test_verification_question_with_trailing_turn_alerts(self):
        self.client.send_message.reset_mock()
        msg = self.msg('What is the chemical symbol for gold?: 🔎 your turn...', ident=4)
        await self.c.receive(NS(message=msg))
        self.assertFalse(self.c.running)
        self.assertEqual(self.c.state, 'verification')
        msg.click.assert_not_awaited()
        self.assertIn(self.c.alert_tag, self.client.send_message.await_args.args[1])
        self.assertIn('chemical symbol for gold', self.client.send_message.await_args.args[1])
        self.assertFalse(any(c.args[1] == '/hunt' for c in self.client.send_message.await_args_list))

    async def test_question_suffix_variants_pause(self):
        for text in ['What is the chemical symbol for gold?: 🔎 your turn...',
                     'Which is different？ Answer below.', 'What is 2 + 2?\nYour turn...',
                     'Which animal is a mammal? ⏳ 30 seconds']:
            self.assertTrue(verification_prompt(text), text)
        for text in ['Choose which ball?', 'Choose your next pokemon',
                     'A wild Abra (Lv. 2) has appeared']:
            self.assertFalse(verification_prompt(text), text)

    async def test_suffix_verification_timeout_remains_paused(self):
        await self.feed(self.msg('What is the chemical symbol for gold?: 🔎 your turn...', ident=4))
        self.client.send_message.reset_mock()
        await self.c.recover_timeout(self.c.generation)
        self.assertFalse(self.c.running)
        self.client.send_message.assert_not_awaited()

