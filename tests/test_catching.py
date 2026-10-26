"""Regression tests adapted from the user's hexa_v4.py; Telegram is mocked."""
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

class CatchTests(unittest.IsolatedAsyncioTestCase):
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
        self.cfg = Settings(targets={'*'}, mode='catch', delay=0, hunt_delay=0, notify='me')
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

    async def test_verification_cancels_pending_click(self):
        await self.encounter()
        self.cfg.delay = 1
        battle = self.msg(self.caption(), self.moves)
        self.messages[battle.id] = battle
        worker = asyncio.create_task(self.c.process(battle))
        self.c.worker = worker
        await asyncio.sleep(0)
        await self.c.receive(NS(message=self.msg(self.question, ident=4)))
        battle.click.assert_not_awaited()
        self.assertTrue(worker.cancelled())
        self.assertFalse(self.c.running)

    async def test_verification_cancels_delayed_next_hunt(self):
        self.cfg.hunt_delay = 1
        worker = asyncio.create_task(self.c.next_hunt())
        self.c.worker = worker
        await asyncio.sleep(0)
        await self.c.receive(NS(message=self.msg(self.question, ident=4)))
        self.assertTrue(worker.cancelled())
        hunts = [c for c in self.client.send_message.await_args_list if c.args[1] == '/hunt']
        self.assertEqual(len(hunts), 1)

    async def test_verification_persists_despite_normal_updates(self):
        await self.c.receive(NS(message=self.msg(self.question, ident=4)))
        sends = self.client.send_message.await_count
        await self.c.receive(NS(message=self.msg('Verification completed', ident=5)))
        await self.c.receive(NS(message=self.msg('A wild Abra (Lv. 2) has appeared', ['Battle'], ident=6)))
        await self.c.send_hunt()
        self.assertEqual(self.client.send_message.await_count, sends)
        self.assertFalse(self.c.running)

    async def test_verification_resumes_only_with_explicit_go(self):
        await self.c.receive(NS(message=self.msg(self.question, ident=4)))
        await self.c.command(NS(out=True, chat_id=123, raw_text='.status'))
        self.assertFalse(self.c.running)
        self.assertIn('stage=verification', self.client.send_message.await_args.args[1])
        await self.c.command(NS(out=True, chat_id=123, raw_text='.resume'))
        await asyncio.sleep(0)
        self.assertTrue(self.c.running)
        self.assertEqual(self.c.verification_text, '')
        await self.c.stop(notify=False)

    async def test_verification_on_previous_hunt_message(self):
        self.c.hunt_id = 10
        await self.c.receive(NS(message=self.msg(self.question, ident=9)))
        self.assertFalse(self.c.running)

    async def test_verification_edited_in_during_click_delay(self):
        await self.encounter()
        old = self.msg(self.caption(), self.moves)
        question = self.msg(self.question, ident=old.id)
        self.client.get_messages.side_effect = [old, question]
        await self.c.process(old)
        old.click.assert_not_awaited()
        self.assertFalse(self.c.running)
        self.assertEqual(self.c.state, 'verification')

    async def test_verification_callback_pauses(self):
        encounter = self.msg('A wild Abra (Lv. 2) has appeared', ['Battle'], ident=2)
        encounter.click.return_value = NS(message='Please complete verification before continuing.')
        await self.feed(encounter)
        self.assertFalse(self.c.running)
        self.assertEqual(self.c.state, 'verification')

    async def test_verification_notification_failure_still_stops(self):
        self.client.send_message.side_effect = RuntimeError('notification unavailable')
        await self.c.receive(NS(message=self.msg(self.question, ident=4)))
        self.assertFalse(self.c.running)
        self.assertEqual(self.c.state, 'verification')

    async def test_verification_parser_and_normal_ui(self):
        self.assertTrue(verification_prompt(self.question))
        self.assertTrue(verification_prompt('Please complete verification'))
        self.assertTrue(verification_prompt('Are you human?'))
        self.assertTrue(verification_prompt('What is 2 + 3?'))
        self.assertTrue(verification_prompt('Pick the odd one out？'))
        self.assertFalse(verification_prompt('Choose your next Pokémon?'))
        self.assertFalse(verification_prompt('A wild Abra (Lv. 2) has appeared'))
        self.assertFalse(verification_prompt(self.caption()))
        self.assertFalse(verification_prompt('You caught a wild Abra.'))
        self.assertFalse(verification_prompt(''))

    async def test_screenshot_hp_advice_not_fatal(self):
        await self.encounter('Gardevoir')
        battle = self.msg(self.caption(current=15, maximum=40, name='Gardevoir'), self.moves)
        battle.click.return_value = NS(message="Lower Gardevoir's HP for a better chance of catching it")
        await self.feed(battle)
        self.assertTrue(self.c.running)
        self.assertEqual(self.c.state, 'catching')
        self.assertEqual(self.c.throws, 0)
        self.assertTrue(self.c.queue.empty())  # no blind repeat of an unchanged low-HP screen
        menu = self.msg(battle.raw_text, rows=self.balls)
        await self.feed(menu)
        menu.click.assert_awaited_once_with(0, 0)
        self.assertEqual(self.c.throws, 1)

    async def test_hp_advice_with_updated_ball_menu_queues_throw(self):
        await self.encounter('Gardevoir')
        battle = self.msg(self.caption(current=15, maximum=40, name='Gardevoir'), self.moves)
        menu = self.msg(battle.raw_text, rows=self.balls)
        async def opened(*args):
            self.messages[menu.id] = menu
            return NS(message="Lower Gardevoir’s HP for a better chance of catching it")
        battle.click.side_effect = opened
        await self.feed(battle)
        self.assertTrue(self.c.running)
        token, pending = self.c.queue.get_nowait()
        self.assertEqual(token, self.c.generation)
        await self.c.process(pending)
        menu.click.assert_awaited_once_with(0, 0)
        self.assertEqual(self.c.throws, 1)

    async def test_closed_menu_advice_weakens_low_max_target(self):
        await self.encounter('Gardevoir')
        battle = self.msg(self.caption(current=40, maximum=40, name='Gardevoir'), self.moves)
        battle.click.return_value = NS(message="Lower Gardevoir's HP for a better chance of catching it")
        await self.feed(battle)
        self.assertTrue(self.c.running)
        self.assertTrue(self.c.require_weaken)
        self.assertEqual(self.c.throws, 0)
        token, pending = self.c.queue.get_nowait()
        self.assertEqual(token, self.c.generation)
        battle.click.reset_mock(side_effect=True, return_value=True)
        battle.click.return_value = NS(message=None)
        await self.c.process(pending)
        battle.click.assert_awaited_once_with(0, 1)  # Reversal, not another menu click
        self.assertEqual(self.c.attacks, 1)
        low = self.msg(self.caption(current=20, maximum=40, name='Gardevoir'), self.moves)
        await self.feed(low)
        low.click.assert_awaited_once_with(0, 4)
        menu = self.msg(low.raw_text, rows=self.balls)
        await self.feed(menu)
        menu.click.assert_awaited_once_with(0, 0)
        self.assertEqual(self.c.throws, 1)
        await self.c.send_hunt()
        self.assertFalse(self.c.require_weaken)

    async def test_hp_advice_after_throw_never_duplicates_throw(self):
        await self.encounter('Gardevoir')
        menu = self.msg(self.caption(current=15, maximum=40, name='Gardevoir'), rows=self.balls)
        menu.click.return_value = NS(message="Lower Gardevoir's HP for a better chance of catching it")
        await self.feed(menu)
        await self.feed(menu)
        self.assertTrue(self.c.running)
        self.assertEqual(menu.click.await_count, 1)
        self.assertEqual(self.c.throws, 1)
        self.assertTrue(self.c.queue.empty())

    async def test_genuine_callback_failure_with_hp_hint_still_pauses(self):
        await self.encounter('Gardevoir')
        battle = self.msg(self.caption(current=15, maximum=40, name='Gardevoir'), self.moves)
        battle.click.return_value = NS(message="Cannot use this ball. Lower Gardevoir's HP first.")
        await self.feed(battle)
        self.assertFalse(self.c.running)
        self.assertIn('requires attention', self.c.reason)

    async def test_verification_in_advice_refresh_still_pauses(self):
        await self.encounter('Gardevoir')
        battle = self.msg(self.caption(current=15, maximum=40, name='Gardevoir'), self.moves)
        question = self.msg(self.question)
        async def changed(*args):
            self.messages[question.id] = question
            return NS(message="Lower Gardevoir's HP for a better chance of catching it")
        battle.click.side_effect = changed
        await self.feed(battle)
        self.assertFalse(self.c.running)
        self.assertEqual(self.c.state, 'verification')

    async def test_ball_menu_callback_question_not_verification(self):
        await self.encounter()
        battle = self.msg(self.caption(current=40), self.moves)
        battle.click.return_value = NS(message='Choose which ball?')
        await self.feed(battle)
        self.assertTrue(self.c.running)
        self.assertEqual(self.c.state, 'catching')
        menu = self.msg(self.caption(current=40), rows=self.balls)
        await self.feed(menu)
        menu.click.assert_awaited_once_with(0, 0)
        self.assertEqual(self.c.throws, 1)

    async def test_question_only_ball_menu_is_not_verification(self):
        await self.encounter()
        await self.feed(self.msg(self.caption(current=40), self.moves))
        menu = self.msg('Choose which ball?', rows=self.balls)
        await self.feed(menu)
        menu.click.assert_awaited_once_with(0, 0)
        self.assertTrue(self.c.running)

    async def test_safari_screenshot_catch_and_retry(self):
        encounter = self.msg('A wild Togekiss (Lv. 23) has appeared\n/hunt limit: 97/150\nSafari Balls remaining: 45',
                             ['Catch', 'EV Yield'], ident=2)
        await self.feed(encounter)
        encounter.click.assert_awaited_once_with(0, 0)
        self.assertEqual(self.c.selected_ball, 'Safari Ball')
        self.assertEqual(self.c.throws, 1)
        await self.feed(encounter)
        self.assertEqual(self.c.throws, 1)
        failed = self.msg('Oh no. The wild Togekiss broke free.\nA wild Togekiss (Lv. 23) has appeared\n'
                          '/hunt limit: 97/150\nSafari Balls remaining: 44', ['Catch', 'EV Yield'], ident=2)
        await self.feed(failed)
        failed.click.assert_awaited_once_with(0, 0)
        self.assertEqual(self.c.throws, 2)
        await self.feed(self.msg('You caught a wild Togekiss.', ['View Stats'], ident=3))
        self.assertEqual(self.c.state, 'encounter')
        self.assertEqual(self.c.encounter_mode, 'battle')

    async def test_safari_empty_pauses(self):
        msg = self.msg('A wild Togekiss (Lv. 23) has appeared\nSafari Balls remaining: 0', ['Catch', 'EV Yield'], ident=2)
        await self.feed(msg)
        msg.click.assert_not_awaited()
        self.assertFalse(self.c.running)
        self.assertIn('No Safari Balls', self.c.reason)

    async def test_safari_throw_limit(self):
        msg = self.msg('A wild Togekiss (Lv. 23) has appeared\nSafari Balls remaining: 45', ['Catch', 'EV Yield'], ident=2)
        await self.feed(msg)
        self.c.throws = self.cfg.max_throws
        retry = self.msg('A wild Togekiss (Lv. 23) has appeared\nSafari Balls remaining: 44', ['Catch', 'EV Yield'], ident=2)
        await self.feed(retry)
        retry.click.assert_not_awaited()
        self.assertEqual(self.c.state, 'encounter')

    async def test_catch_without_safari_marker_pauses(self):
        msg = self.msg('A wild Abra (Lv. 23) has appeared', ['Catch', 'EV Yield'], ident=2)
        await self.feed(msg)
        msg.click.assert_not_awaited()
        self.assertFalse(self.c.running)

    async def test_safari_still_pauses_for_verification(self):
        msg = self.msg('A wild Togekiss (Lv. 23) has appeared\nSafari Balls remaining: 45', ['Catch', 'EV Yield'], ident=2)
        await self.feed(msg)
        await self.c.receive(NS(message=self.msg(self.question, ident=3)))
        self.assertEqual(self.c.state, 'verification')
        self.assertFalse(self.c.running)

    async def test_complete_screenshot_flow(self):
        await self.encounter()
        high = self.msg('Battle begins\n' + self.caption(), self.moves)
        await self.feed(high)
        high.click.assert_awaited_once_with(0, 1)  # lowest power: Reversal
        low = self.msg(self.caption(current=40), self.moves)
        await self.feed(low)
        low.click.assert_awaited_once_with(0, 4)
        menu = self.msg(self.caption(current=40), rows=self.balls)
        await self.feed(menu)
        menu.click.assert_awaited_once_with(0, 0)
        failed = self.msg('Oh no. The wild Rhydon broke free\nRhydon used Horn Attack.\nDeal 8 damage.\n' +
                          self.caption(current=40, own=213), rows=[['Regular Ball x93'], ['Repeat Ball x14'], ['Back']])
        await self.feed(failed)
        failed.click.assert_awaited_once_with(0, 0)
        self.assertEqual(self.c.throws, 2)
        result = self.msg('You caught a wild Rhydon.', ['View Stats', 'View Pokedex', 'Nickname', 'Release'], ident=4)
        await self.feed(result)
        await self.feed(result)
        self.assertEqual(sum(x.args[1] == '/hunt' for x in self.client.send_message.await_args_list), 2)
        result.click.assert_not_awaited()

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

    async def test_normal_target_uses_configured_ball(self):
        await self.encounter('Rhydon')
        self.assertEqual(self.c.selected_ball, self.c.cfg.ball)
        menu = self.msg(self.caption(current=40), rows=self.balls)
        await self.feed(menu)
        menu.click.assert_awaited_once_with(0, 0)

    async def test_low_maximum_caught_at_full_hp(self):
        await self.encounter('Abra')
        msg = self.msg(self.caption(current=30, maximum=30, name='Abra'), self.moves)
        await self.feed(msg)
        msg.click.assert_awaited_once_with(0, 4)

    async def test_exact_half_opens_balls(self):
        await self.encounter()
        msg = self.msg(self.caption(current=50, maximum=100), self.moves)
        await self.feed(msg)
        msg.click.assert_awaited_once_with(0, 4)

    async def test_high_hp_ball_menu_goes_back_then_attacks(self):
        await self.encounter()
        menu = self.msg(self.caption(), rows=self.balls)
        await self.feed(menu)
        menu.click.assert_awaited_once_with(2, 0)
        attack = self.msg(self.caption(), self.moves)
        await self.feed(attack)
        attack.click.assert_awaited_once_with(0, 1)

    async def test_same_screen_can_recur_after_menu(self):
        await self.encounter()
        first = self.msg(self.caption(), self.moves)
        await self.feed(first)
        await self.feed(self.msg(self.caption(), rows=self.balls))
        again = self.msg(self.caption(), self.moves)
        await self.feed(again)
        again.click.assert_awaited_once_with(0, 1)
        await self.feed(again)
        self.assertEqual(again.click.await_count, 1)

    async def test_new_turn_with_edit_date(self):
        await self.encounter()
        first = self.msg(self.caption(), self.moves)
        await self.feed(first)
        again = self.msg(self.caption(), self.moves)
        again.edit_date = 'new-turn'
        await self.feed(again)
        again.click.assert_awaited_once_with(0, 1)

    async def test_reordered_move_buttons(self):
        await self.encounter()
        msg = self.msg(self.caption(), ['Run', 'Poke Ball', 'Retaliate', 'Reversal'])
        await self.feed(msg)
        msg.click.assert_awaited_once_with(0, 3)

    async def test_preferred_move(self):
        self.cfg.attack_move = 'Retaliate'
        await self.encounter()
        msg = self.msg(self.caption(), self.moves)
        await self.feed(msg)
        msg.click.assert_awaited_once_with(0, 0)

    async def test_missing_preferred_move_pauses(self):
        self.cfg.attack_move = 'False Swipe'
        await self.encounter()
        msg = self.msg(self.caption(), self.moves)
        await self.feed(msg)
        msg.click.assert_not_awaited()
        self.assertFalse(self.c.running)

    async def test_empty_selected_ball_no_upgrade(self):
        await self.encounter()
        msg = self.msg(self.caption(current=40), ['Regular Ball x0', 'Ultra Ball x20', 'Back'])
        await self.feed(msg)
        msg.click.assert_not_awaited()
        self.assertFalse(self.c.running)

    async def test_throw_limit(self):
        await self.encounter()
        self.c.throws = self.cfg.max_throws
        msg = self.msg(self.caption(current=40), rows=self.balls)
        await self.feed(msg)
        msg.click.assert_awaited_once_with(2, 0)  # Back, never another ball.
        self.assertEqual(self.c.state, 'escaping')
        self.assertTrue(self.c.running)

    async def test_attack_limit(self):
        await self.encounter()
        self.c.attacks = self.cfg.max_attacks
        msg = self.msg(self.caption(), self.moves)
        await self.feed(msg)
        msg.click.assert_not_awaited()
        self.assertIn('Attack limit', self.c.reason)

    async def test_duplicate_does_not_consume_limit(self):
        await self.encounter()
        msg = self.msg(self.caption(current=40), rows=self.balls)
        await self.feed(msg)
        await self.feed(msg)
        self.assertEqual(self.c.throws, 1)

    async def test_switch_hunt_team(self):
        await self.encounter()
        msg = self.msg('Choose your next Pokémon.', ['Arcanine (Hunt Team)', 'Pikachu'], ident=4)
        await self.feed(msg)
        msg.click.assert_awaited_once_with(0, 0)
        next_screen = self.msg(self.caption(current=40), self.moves, ident=5)
        await self.feed(next_screen)
        next_screen.click.assert_awaited_once_with(0, 4)

    async def test_screenshot_numbered_switch_continues_catching(self):
        await self.encounter('Scizor')
        await self.feed(self.msg(self.caption(current=53, maximum=151, name='Scizor'), rows=self.balls))
        fainted = self.msg('Oh no. The wild Scizor broke free\nArcanine fainted.\n' +
                           self.caption(current=53, maximum=151, own=0, name='Scizor') +
                           '\nChoose your next pokemon.', ['1', '2', 'View Team'])
        await self.feed(fainted)
        fainted.click.assert_awaited_once_with(0, 0)
        self.assertTrue(self.c.running)
        self.assertEqual(self.c.state, 'switching')
        await self.feed(fainted)
        self.assertEqual(fainted.click.await_count, 1)
        restored = self.msg(self.caption(current=53, maximum=151, own=221, name='Scizor'), self.moves)
        await self.feed(restored)
        restored.click.assert_awaited_once_with(0, 4)
        self.assertTrue(self.c.running)

    async def test_numbered_switch_uses_lowest_offered_slot(self):
        await self.encounter()
        msg = self.msg('Choose your next Pokémon.', ['View Team', '3', '2'])
        await self.feed(msg)
        msg.click.assert_awaited_once_with(0, 2)

    async def test_named_hunt_team_preferred_over_numbered_slots(self):
        self.assertEqual(switch_label(['1', '2', 'Arcanine (Hunt Team)', 'View Team']), 'Arcanine (Hunt Team)')
        self.assertEqual(switch_label(['View Team', '2', '1']), '1')
        self.assertEqual(switch_label(['View Team', '2️⃣']), '2️⃣')
        self.assertIsNone(switch_label(['View Team']))
        self.assertIsNone(switch_label(['1', '1', 'View Team']))
        self.assertIsNone(switch_label(['0', 'View Team']))

    async def test_thirty_throws_switches_before_running(self):
        await self.encounter()
        self.c.throws = 30
        msg = self.msg('Arcanine fainted.\nChoose your next pokemon.', ['1', '2', 'View Team'])
        await self.feed(msg)
        msg.click.assert_awaited_once_with(0, 0)
        self.assertEqual(self.c.state, 'escaping')
        battle = self.msg(self.caption(current=40), self.moves)
        await self.feed(battle)
        battle.click.assert_awaited_once_with(0, 5)

    async def test_timeout_recovers_unseen_numbered_switch(self):
        await self.encounter()
        await self.feed(self.msg(self.caption(), self.moves))
        switch = self.msg('Choose your next pokemon.', ['1', '2', 'View Team'])
        self.messages[switch.id] = switch
        await self.c.recover_timeout(self.c.generation)
        switch.click.assert_awaited_once_with(0, 0)
        self.assertTrue(self.c.running)

    async def test_switch_screen_verification_still_pauses(self):
        await self.encounter()
        switch = self.msg('Verification required.\nChoose your next pokemon.', ['1', '2', 'View Team'])
        await self.feed(switch)
        switch.click.assert_not_awaited()
        self.assertFalse(self.c.running)
        self.assertEqual(self.c.state, 'verification')

    async def test_numbers_outside_switch_prompt_are_not_clicked(self):
        await self.encounter()
        msg = self.msg('Pick an option', ['1', '2', 'View Team'])
        await self.feed(msg)
        msg.click.assert_not_awaited()
        self.assertFalse(self.c.running)

    async def test_switch_missing_team_pauses(self):
        await self.encounter()
        await self.feed(self.msg('Choose your next pokemon.', ['Pikachu'], ident=4))
        self.assertFalse(self.c.running)

    async def test_older_battle_ignored_after_switch(self):
        await self.encounter()
        await self.feed(self.msg('Choose your next pokemon.', ['Hunt Team'], ident=4))
        old = self.msg(self.caption(), self.moves, ident=3)
        await self.feed(old)
        old.click.assert_not_awaited()

    async def test_stale_screen_requeued_before_click(self):
        await self.encounter()
        old = self.msg(self.caption(), self.moves)
        fresh = self.msg(self.caption(current=40), self.moves)
        self.client.get_messages.side_effect = [old, fresh]
        await self.c.process(old)
        old.click.assert_not_awaited()
        token, current = self.c.queue.get_nowait()
        self.assertEqual(token, self.c.generation)
        self.assertIs(current, fresh)
        self.client.get_messages.side_effect = None
        self.client.get_messages.return_value = fresh
        await self.c.process(current)
        fresh.click.assert_awaited_once_with(0, 4)
        self.assertEqual(self.c.attacks, 0)

    async def test_unknown_controls_pause(self):
        await self.encounter()
        msg = self.msg(self.caption(), ['Something Else'])
        await self.feed(msg)
        msg.click.assert_not_awaited()
        self.assertFalse(self.c.running)

    async def test_missing_caption_waits_for_keyboard(self):
        msg = self.msg('A wild Abra (Lv. 2) has appeared', ident=2)
        await self.feed(msg)
        self.assertTrue(self.c.running)
        await self.encounter('Abra')

    async def test_no_buttons_waits_for_result(self):
        await self.encounter()
        msg = self.msg('You threw a Regular Ball!')
        await self.feed(msg)
        self.assertTrue(self.c.running)
        msg.click.assert_not_awaited()

    async def test_replacement_target_list(self):
        expected = {normalized(x) for x in TARGETS}
        self.assertEqual(Settings().targets, expected)
        self.assertIn(normalized('Charizard'), expected)
        self.assertIn(normalized('Abra'), expected)
        self.assertIn(normalized('Iron Leaves'), expected)
        self.assertIn(normalized('Jellicent'), expected)
        self.assertNotIn(normalized('Ho-Oh'), expected)
        self.assertNotIn(normalized('Mewtwo'), expected)
        self.assertEqual(len(TARGETS), len(expected))
        self.assertNotIn(normalized('Rhydon'), expected)

    async def test_unlisted_skipped(self):
        self.c.targets = Settings().targets
        msg = self.msg('A wild Rhydon (Lv. 23) has appeared', ['Battle', 'EV Yield'], ident=2)
        await self.feed(msg)
        msg.click.assert_not_awaited()
        self.assertEqual(sum(x.args[1] == '/hunt' for x in self.client.send_message.await_args_list), 2)

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

    async def test_bot_cooldown_callback_pauses(self):
        msg = self.msg('A wild Abra (Lv. 2) has appeared', ['Battle'], ident=2)
        msg.click.return_value = NS(message='Please wait before clicking')
        await self.feed(msg)
        self.assertFalse(self.c.running)

    async def test_zero_hp_waits(self):
        await self.encounter()
        msg = self.msg(self.caption(current=0), self.moves)
        await self.feed(msg)
        msg.click.assert_not_awaited()
        self.assertTrue(self.c.running)

    async def test_wild_fainted_restarts_hunt(self):
        await self.encounter()
        await self.feed(self.msg('The wild Rhydon fainted!', ident=4))
        self.assertEqual(self.c.state, 'encounter')
        self.assertEqual(self.c.selected_name, '')

    async def test_player_faint_not_terminal(self):
        self.assertFalse(terminal('Arcanine fainted!\nChoose your next pokemon.', 'Rhydon'))

    async def test_result_edited_on_encounter_message(self):
        await self.encounter()
        await self.feed(self.msg('You caught a wild Rhydon.', ident=2))
        self.assertEqual(self.c.state, 'encounter')

    async def test_species_mismatch_pauses(self):
        await self.encounter()
        msg = self.msg(self.caption(name='Abra'), self.moves)
        await self.feed(msg)
        self.assertFalse(self.c.running)
        msg.click.assert_not_awaited()

    async def test_old_message_cutoff(self):
        msg = self.msg('A wild Abra (Lv. 2) has appeared', ['Battle'], ident=1)
        await self.feed(msg)
        msg.click.assert_not_awaited()

    async def test_expert_trainer_skipped(self):
        await self.feed(self.msg('You met an expert trainer!', ident=2))
        self.assertEqual(sum(x.args[1] == '/hunt' for x in self.client.send_message.await_args_list), 2)

    async def test_stop_cancels_delayed_click(self):
        await self.encounter()
        self.cfg.delay = 1
        msg = self.msg(self.caption(), self.moves)
        self.messages[msg.id] = msg
        worker = asyncio.create_task(self.c.process(msg))
        self.c.worker = worker
        await asyncio.sleep(0)
        await self.c.command(NS(out=True, chat_id=123, raw_text='.stop'))
        msg.click.assert_not_awaited()
        self.assertTrue(worker.cancelled())

    async def test_group_alert_tags_fixed_username_for_different_login(self):
        cfg = Settings(notify=-1001234567890)
        profile = NS(id=123, username='my_username', first_name='Name', last_name=None)
        controller = Controller(self.client, self.c.peer, 123, cfg, owner_profile=profile)
        await controller.note('Shiny detected. Automation paused.')
        self.client.send_message.assert_awaited_with(
            -1001234567890, 'Hexa | @Kill_switch8\nShiny detected. Automation paused.', parse_mode=None)

    async def test_no_username_login_still_tags_fixed_recipient(self):
        profile = NS(username=None, first_name='Kshitij', last_name='Raj')
        controller = Controller(self.client, self.c.peer, 123, self.cfg, owner_profile=profile)
        await controller.note('Stopped')
        self.assertIn('@Kill_switch8', self.client.send_message.await_args.args[1])
        self.assertNotIn('Kshitij Raj', self.client.send_message.await_args.args[1])
        self.assertNotIn('@None', self.client.send_message.await_args.args[1])

    async def test_no_profile_alert_still_tags_fixed_recipient(self):
        await self.c.note('Stopped')
        self.assertIn('@Kill_switch8', self.client.send_message.await_args.args[1])

    async def test_group_delivery_failure_falls_back_to_saved_messages(self):
        self.cfg.notify = -1001234567890
        self.c.alert_peer = self.cfg.notify
        self.client.send_message.reset_mock()
        self.client.send_message.side_effect = [RuntimeError('group cannot send'), NS(id=9)]
        await self.c.note('Verification detected')
        self.assertEqual(self.client.send_message.await_args_list[0].args[0], -1001234567890)
        self.assertEqual(self.client.send_message.await_args_list[1].args[0], self.c.owner)
        self.assertIn('Verification detected', self.client.send_message.await_args_list[1].args[1])
        self.assertIn('@Kill_switch8', self.client.send_message.await_args_list[1].args[1])

    async def test_fixed_alert_tag_default_and_override(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(Settings.load().alert_tag, '@Kill_switch8')
        with patch.dict(os.environ, {'HEXA_ALERT_TAG': '@another_user'}, clear=True):
            settings = Settings.load()
            profile = NS(username='different_login')
            controller = Controller(self.client, self.c.peer, 123, settings, owner_profile=profile)
            await controller.note('Test alert')
            self.assertEqual(self.client.send_message.await_args.args[1], 'Hexa | @another_user\nTest alert')
        with patch.dict(os.environ, {'HEXA_ALERT_TAG': '@bad username'}, clear=True):
            with self.assertRaisesRegex(ValueError, 'HEXA_ALERT_TAG'):
                Settings.load()

    async def test_default_alert_group(self):
        self.assertEqual(Settings().notify, -1004490697963)
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(Settings.load().notify, -1004490697963)

    async def test_group_setting_signed_id(self):
        with patch.dict(os.environ, {'HEXA_NOTIFY_CHAT': '-1001234567890'}, clear=True):
            self.assertEqual(Settings.load().notify, -1001234567890)
        with patch.dict(os.environ, {'HEXA_NOTIFY_CHAT': '@my_alerts_group'}, clear=True):
            self.assertEqual(Settings.load().notify, '@my_alerts_group')

    async def test_group_id_resolution_from_dialogs(self):
        peer = object()
        async def dialogs():
            yield NS(id=-1001234567890, input_entity=peer)
        client = NS(get_input_entity=AsyncMock(side_effect=ValueError('not cached')), iter_dialogs=dialogs)
        self.assertIs(await resolve_alert_chat(client, -1001234567890), peer)

    async def test_group_username_resolved(self):
        peer = object()
        client = NS(get_input_entity=AsyncMock(return_value=peer))
        self.assertIs(await resolve_alert_chat(client, '@my_alerts_group'), peer)
        client.get_input_entity.assert_awaited_once_with('@my_alerts_group')

    async def test_invalid_alert_group_has_actionable_error(self):
        client = NS(get_input_entity=AsyncMock(side_effect=ValueError('unknown username')))
        with self.assertRaisesRegex(ValueError, 'HEXA_NOTIFY_CHAT'):
            await resolve_alert_chat(client, '@invalid_group')

    async def test_status_reports_limits(self):
        await self.c.command(NS(out=True, chat_id=123, raw_text='.status'))
        self.assertIn('throws=0/30', self.client.send_message.await_args.args[1])
        self.assertIn('attacks=0/10', self.client.send_message.await_args.args[1])

    async def test_go_duplicate_does_not_spawn_worker(self):
        await self.c.command(NS(out=True, chat_id=123, raw_text='.resume'))
        self.assertIsNone(self.c.worker)
        self.assertIn('Already running', self.client.send_message.await_args.args[1])

    async def test_go_stop_worker_lifecycle(self):
        await self.c.stop(notify=False)
        await self.c.command(NS(out=True, chat_id=123, raw_text='.resume'))
        await asyncio.sleep(0)
        worker = self.c.worker
        self.assertTrue(self.c.running)
        await self.c.stop(notify=False)
        self.assertTrue(worker.done())
        self.assertFalse(self.c.running)

    async def test_requested_default_limits(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings.load()
        self.assertEqual((settings.max_throws, settings.max_attacks, settings.timeout), (30, 10, 45))
        self.assertEqual((Settings().max_throws, Settings().max_attacks), (30, 10))

    async def test_thirtieth_failed_throw_back_run_next_hunt(self):
        await self.encounter()
        self.c.throws = 29
        text = self.caption(current=40)
        menu = self.msg(text, rows=self.balls)
        await self.feed(menu)
        self.assertEqual(self.c.throws, 30)
        menu.click.assert_awaited_once_with(0, 0)
        await self.feed(menu)
        self.assertEqual(menu.click.await_count, 1)
        self.assertIsNone(self.c.escape_reason)
        animation = self.msg('★★★')
        await self.feed(animation)
        self.assertIsNone(self.c.escape_reason)
        failed_text = 'Oh no. The wild Rhydon broke free\n' + self.caption(current=40, own=213)
        failed = self.msg(failed_text, rows=[['Regular Ball x93'], ['Back']])
        await self.feed(failed)
        failed.click.assert_awaited_once_with(1, 0)
        self.assertEqual(self.c.state, 'escaping')
        battle = self.msg(failed_text, self.moves)
        await self.feed(battle)
        battle.click.assert_awaited_once_with(0, 5)
        await self.feed(battle)
        self.assertEqual(battle.click.await_count, 1)
        await self.feed(self.msg('You ran away from the wild Rhydon!', ident=4))
        self.assertTrue(self.c.running)
        self.assertEqual(self.c.state, 'encounter')
        self.assertEqual(self.c.throws, 0)
        self.assertIsNone(self.c.escape_reason)
        self.assertEqual(sum(c.args[1] == '/hunt' for c in self.client.send_message.await_args_list), 2)

    async def test_thirtieth_successful_throw_does_not_run(self):
        await self.encounter()
        self.c.throws = 29
        menu = self.msg(self.caption(current=40), rows=self.balls)
        await self.feed(menu)
        self.assertEqual(self.c.throws, 30)
        result = self.msg('You caught a wild Rhydon.', ['View Stats', 'Release'], ident=4)
        await self.feed(result)
        result.click.assert_not_awaited()
        self.assertEqual(self.c.state, 'encounter')
        self.assertEqual(self.c.throws, 0)

    async def test_tenth_attack_is_last_attack(self):
        await self.encounter()
        self.c.attacks = 9
        battle = self.msg(self.caption(), self.moves)
        await self.feed(battle)
        self.assertEqual(self.c.attacks, 10)
        next_turn = self.msg(self.caption(current=80, own=213), self.moves)
        await self.feed(next_turn)
        next_turn.click.assert_not_awaited()
        self.assertEqual(self.c.attacks, 10)
        self.assertFalse(self.c.running)

    async def test_timeout_recovers_missed_fainted_message(self):
        await self.encounter()
        await self.feed(self.msg(self.caption(), self.moves))
        self.messages[4] = self.msg('The wild Rhydon fainted!', ident=4)
        await self.c.recover_timeout(self.c.generation)
        self.assertTrue(self.c.running)
        self.assertEqual(self.c.state, 'encounter')
        self.assertIsNone(self.c.escape_reason)
        self.assertEqual(self.c.timeout_restarts, 1)

    async def test_timeout_recovers_missed_catch_result(self):
        await self.encounter()
        await self.feed(self.msg(self.caption(current=40), rows=self.balls))
        self.messages[4] = self.msg('You caught a wild Rhydon.', ident=4)
        await self.c.recover_timeout(self.c.generation)
        self.assertTrue(self.c.running)
        self.assertEqual(self.c.state, 'encounter')
        self.assertEqual(self.c.throws, 0)

    async def test_timeout_recovers_missed_updated_battle(self):
        await self.encounter()
        await self.feed(self.msg(self.caption(), self.moves))
        low = self.msg(self.caption(current=40), self.moves)
        self.messages[3] = low
        await self.c.recover_timeout(self.c.generation)
        low.click.assert_awaited_once_with(0, 4)
        self.assertTrue(self.c.running)
        self.assertIsNone(self.c.escape_reason)
        self.assertEqual(self.c.attacks, 1)

    async def test_timeout_stalled_battle_runs_without_repeating_attack(self):
        await self.encounter()
        battle = self.msg(self.caption(), self.moves)
        await self.feed(battle)
        self.assertEqual(self.c.attacks, 1)
        await self.c.recover_timeout(self.c.generation)
        self.assertEqual(battle.click.await_args_list[-1].args, (0, 5))
        self.assertEqual(self.c.attacks, 1)
        self.assertTrue(self.c.escape_pending)
        self.assertEqual(self.c.state, 'escaping')
        await self.feed(self.msg('Got away safely!', ident=4))
        self.assertEqual(self.c.state, 'encounter')
        self.assertTrue(self.c.running)

    async def test_timeout_stalled_ball_menu_backs_out_without_another_throw(self):
        await self.encounter()
        menu = self.msg(self.caption(current=40), rows=self.balls)
        await self.feed(menu)
        self.assertEqual(self.c.throws, 1)
        await self.c.recover_timeout(self.c.generation)
        self.assertEqual(menu.click.await_args_list[-1].args, (2, 0))
        self.assertEqual(self.c.throws, 1)
        battle = self.msg(menu.raw_text, self.moves)
        await self.feed(battle)
        battle.click.assert_awaited_once_with(0, 5)

    async def test_escape_confirmation_callback_starts_next_hunt(self):
        await self.encounter()
        self.c.throws = self.cfg.max_throws
        battle = self.msg(self.caption(current=40), self.moves)
        battle.click.return_value = NS(message='You ran away successfully!')
        await self.feed(battle)
        self.assertTrue(self.c.running)
        self.assertEqual(self.c.state, 'encounter')
        self.assertFalse(self.c.escape_pending)

    async def test_unconfirmed_run_does_not_repeat(self):
        await self.encounter()
        battle = self.msg(self.caption(), self.moves)
        await self.feed(battle)
        await self.c.recover_timeout(self.c.generation)
        clicks = battle.click.await_count
        await self.c.recover_timeout(self.c.generation)
        self.assertEqual(battle.click.await_count, clicks)
        self.assertFalse(self.c.running)
        self.assertIn('Escape outcome not confirmed', self.c.reason)

    async def test_timeout_verification_remains_manual(self):
        await self.encounter()
        self.messages[4] = self.msg(self.question, ident=4)
        generation = self.c.generation
        await self.c.recover_timeout(generation)
        self.assertFalse(self.c.running)
        self.assertEqual(self.c.state, 'verification')
        calls = self.client.send_message.await_count
        await self.c.recover_timeout(generation)
        self.assertEqual(self.client.send_message.await_count, calls)

    async def test_timeout_shiny_and_daily_limit_remain_manual(self):
        for text in ['A wild Shiny Abra (Lv. 2) has appeared', 'Daily hunt limit reached']:
            await self.asyncSetUp()
            self.messages[2] = self.msg(text, ident=2)
            await self.c.recover_timeout(self.c.generation)
            self.assertFalse(self.c.running)
            self.assertEqual(sum(c.args[1] == '/hunt' for c in self.client.send_message.await_args_list), 1)

    async def test_timeout_does_not_resume_user_stop(self):
        generation = self.c.generation
        await self.c.stop(notify=False)
        calls = self.client.send_message.await_count
        await self.c.recover_timeout(generation)
        self.assertEqual(self.client.send_message.await_count, calls)
        self.assertFalse(self.c.running)

    async def test_stop_during_timeout_notification_prevents_recovery(self):
        async def on_notification(peer, text, **kwargs):
            if 'Automatically recovering' in text:
                await self.c.stop(notify=False)
            return NS(id=1)
        self.client.send_message.side_effect = on_notification
        await self.c.recover_timeout(self.c.generation)
        self.assertFalse(self.c.running)
        self.assertEqual(sum(c.args[1] == '/hunt' for c in self.client.send_message.await_args_list), 1)

    async def test_run_failure_does_not_start_hunt(self):
        await self.encounter()
        self.c.throws = self.cfg.max_throws
        battle = self.msg(self.caption(current=40), self.moves)
        battle.click.return_value = NS(message='Cannot run from this battle')
        await self.feed(battle)
        self.assertFalse(self.c.running)
        self.assertEqual(sum(c.args[1] == '/hunt' for c in self.client.send_message.await_args_list), 1)

    async def test_verification_while_escaping_never_runs(self):
        await self.encounter()
        self.c.throws = self.cfg.max_throws
        await self.feed(self.msg(self.caption(current=40), rows=self.balls))
        question = self.msg(self.question, ident=4)
        await self.c.receive(NS(message=question))
        self.assertFalse(self.c.running)
        self.assertEqual(self.c.state, 'verification')
        question.click.assert_not_awaited()

    async def test_screenshot_inline_ball_failure_flee_starts_next_hunt(self):
        await self.encounter('Gible')
        await self.feed(self.msg(self.caption(current=44, maximum=44, name='Gible'), rows=self.balls))
        result = self.msg('Your Regular ball failed and the wild Gible fled.')
        await self.feed(result)
        result.click.assert_not_awaited()
        self.assertTrue(self.c.running)
        self.assertEqual(self.c.state, 'encounter')
        self.assertEqual(self.c.timeout_restarts, 0)
        self.assertIsNone(self.c.escape_reason)
        self.assertEqual(sum(c.args[1] == '/hunt' for c in self.client.send_message.await_args_list), 2)

    async def test_timeout_recovers_missed_inline_flee_result(self):
        await self.encounter('Gible')
        await self.feed(self.msg(self.caption(current=44, maximum=44, name='Gible'), rows=self.balls))
        self.messages[3] = self.msg('Your Regular ball failed and the wild Gible fled.')
        await self.c.recover_timeout(self.c.generation)
        self.assertTrue(self.c.running)
        self.assertEqual(self.c.state, 'encounter')
        self.assertIsNone(self.c.escape_reason)

    async def test_inline_flee_while_escaping_confirms_result(self):
        await self.encounter('Gible')
        self.c.throws = self.cfg.max_throws
        await self.feed(self.msg(self.caption(current=44, maximum=44, name='Gible'), self.moves))
        self.assertEqual(self.c.state, 'escaping')
        await self.feed(self.msg('Your Regular ball failed and the wild Gible fled.'))
        self.assertEqual(self.c.state, 'encounter')
        self.assertTrue(self.c.running)

    async def test_inline_result_does_not_confuse_player_faint_or_break_free(self):
        self.assertTrue(terminal('Your Repeat Ball failed and the wild Ho-Oh fled.', 'Ho-Oh'))
        self.assertTrue(terminal('Psycho Cut hit and the wild Buneary fainted.', 'Buneary'))
        self.assertFalse(terminal('Oh no. The wild Gible broke free. Mewtwo fainted.', 'Gible'))
        self.assertFalse(terminal('The wild Gible escaped from the ball.', 'Gible'))
        self.assertFalse(terminal('Your Regular ball failed and the wild Abra fled.', 'Gible'))
        self.assertFalse(terminal('The wild Gible used Tackle. Mewtwo fainted.', 'Gible'))

    async def test_escape_result_parser(self):
        for text in ['You ran away!', 'You successfully ran away from Rhydon.',
                     'Got away safely!', 'You escaped from the battle.', 'You fled!']:
            self.assertTrue(terminal(text, 'Rhydon'))
        for text in ['You could not escape!', 'You cannot run!', 'The wild Rhydon escaped from the ball.']:
            self.assertFalse(escape_confirmed(text))

    async def test_timeout_recovers_repeatedly_in_same_worker(self):
        self.cfg.timeout = 0.005
        task = asyncio.create_task(self.c.run(self.c.generation))
        self.c.worker = task
        for _ in range(100):
            if self.c.timeout_restarts >= 2:
                break
            await asyncio.sleep(0.001)
        self.assertGreaterEqual(self.c.timeout_restarts, 2)
        self.assertTrue(self.c.running)
        self.assertIs(self.c.worker, task)
        self.assertGreaterEqual(sum(c.args[1] == '/hunt' for c in self.client.send_message.await_args_list), 3)
        await self.c.stop(notify=False)
        self.assertTrue(task.done())

    async def test_uncertain_click_error_does_not_retry(self):
        msg = self.msg('A wild Abra (Lv. 2) has appeared', ['Battle'], ident=2)
        msg.click.side_effect = RuntimeError('callback timed out')
        self.messages[2] = msg
        await self.c.queue.put((self.c.generation, msg))
        await self.c.run(self.c.generation)
        msg.click.assert_awaited_once()
        self.assertFalse(self.c.running)

    async def test_queue_overflow_pauses(self):
        self.c.queue = asyncio.Queue(maxsize=1)
        msg = self.msg('Waiting', ident=2)
        await self.c.receive(NS(message=msg))
        await self.c.receive(NS(message=msg))
        self.assertFalse(self.c.running)

    async def test_parsers(self):
        for name in ['Ho-Oh', 'Tapu Koko', 'Farfetch’d', 'Porygon-Z', 'Flabébé']:
            self.assertEqual(encounter_name(f'A wild {name} (Lv. 23) has appeared'), name)
            self.assertEqual(WILD.search(self.caption(name=name)).group(1), name)
        self.assertEqual(normalized('Flabébé'), normalized('Flabebe'))
        self.assertEqual(encounter_name('A\u200b wild Abra (Lv. 2) has appeared'), 'Abra')
        self.assertEqual(choose_ball(['Regular Ball ×1,234'], 'Regular Ball'), 'Regular Ball ×1,234')
        self.assertEqual(choose_ball(['Repeat Ball (20)'], 'Repeat Ball'), 'Repeat Ball (20)')
        self.assertIsNone(choose_ball(['Regular Ball x0'], 'Regular Ball'))
        self.assertEqual(menu_label(['Poke Ball', 'Regular Ball x20']), 'Poke Ball')
        self.assertFalse(terminal('Oh no. The wild Rhydon broke free\nRhydon used Horn Attack.'))
        self.assertFalse(terminal('The wild Rhydon escaped from the ball.'))
        for args in [(0, 0), (20, 21), (20, -1)]:
            with self.assertRaises(ValueError):
                calculate_health_percentage(*args)

    async def test_legacy_settings(self):
        with patch.dict(os.environ, {'HEXA_TARGET_MODE': 'legacy'}, clear=True):
            self.assertEqual(Settings.load().targets, {normalized(x) for x in TARGETS})

    async def test_invalid_settings(self):
        for overrides in [{'HEXA_DELAY': 'nan'}, {'HEXA_TIMEOUT': 'inf'}, {'HEXA_BALL': 'Master Ball'},
                          {'HEXA_TARGET_MODE': 'typo'}]:
            with patch.dict(os.environ, overrides, clear=True), self.assertRaises(ValueError):
                Settings.load()

    async def test_session_aliases(self):
        with patch.dict(os.environ, {'TELEGRAM_API_ID': '12345', 'TELEGRAM_API_HASH': 'a' * 32, 'TELEGRAM_SESSION_FILE': '/tmp/test_hexa'}, clear=True):
            self.assertEqual(runtime_config()[4], '/tmp/test_hexa')
        with patch.dict(os.environ, {'TELEGRAM_API_ID': '12345', 'TELEGRAM_API_HASH': 'a' * 32, 'TELEGRAM_SESSION': '/tmp/working', 'TELEGRAM_SESSION_FILE': '/tmp/newer'}, clear=True):
            self.assertEqual(runtime_config()[4], '/tmp/working')

    async def test_username_resolution_and_id_validation(self):
        bot = NS(id=572621020, bot=True)
        client = NS(get_entity=AsyncMock(return_value=bot))
        self.assertIs(await resolve_bot(client, '@HeXamonbot', None), bot)
        with self.assertRaises(ValueError):
            await resolve_bot(client, '@HeXamonbot', 123)

    async def test_bot_id_dialog_fallback(self):
        bot = NS(id=572621020, bot=True)
        async def dialogs():
            yield NS(id=572621020, entity=bot)
        client = NS(get_entity=AsyncMock(), get_input_entity=AsyncMock(side_effect=ValueError('not cached')), iter_dialogs=dialogs)
        self.assertIs(await resolve_bot(client, '', 572621020), bot)

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

