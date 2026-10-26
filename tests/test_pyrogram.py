"""Offline compatibility tests using the real Pyrogram handlers and a mocked client."""
import asyncio
import importlib.util
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch

from pyrogram_transport import PyrogramMessage, PyrogramTransport


@unittest.skipIf(importlib.util.find_spec('pyrogram') is None, 'Install Pyrogram to verify this transport')
class PyrogramAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Import under the running event loop for Pyrogram's sync compatibility layer.
        import pyrogram
        self.raw = NS(is_connected=False, is_initialized=False,
                      connect=AsyncMock(return_value=True), initialize=AsyncMock(),
                      start=AsyncMock(), terminate=AsyncMock(), disconnect=AsyncMock(),
                      invoke=AsyncMock(), authorize=AsyncMock(),
                      get_me=AsyncMock(return_value=NS(id=123, is_bot=False, username='owner_name')),
                      get_users=AsyncMock(), get_chat=AsyncMock(), get_messages=AsyncMock(),
                      send_message=AsyncMock(), request_callback_answer=AsyncMock(return_value=NS(message=None)),
                      add_handler=Mock())
        self.constructor = patch('pyrogram.Client', return_value=self.raw)
        self.make = self.constructor.start()
        self.addCleanup(self.constructor.stop)
        self.transport = PyrogramTransport(12345, 'a' * 32, '/tmp/hexa_pyrogram.session', 'test-session')

    def message(self, text='', caption=None, rows=(), ident=3, chat=572621020,
                sender=572621020, outgoing=False, **extra):
        value = dict(id=ident, text=text, caption=caption, chat=NS(id=chat),
                     from_user=NS(id=sender), outgoing=outgoing, edit_date=None,
                     reply_markup=NS(inline_keyboard=[[NS(text=label, callback_data=data)
                                                      for label, data in row] for row in rows]))
        value.update(extra)
        return NS(**value)

    async def test_string_session_uses_memory_and_preserves_api_configuration(self):
        kwargs = self.make.call_args.kwargs
        self.assertEqual(kwargs['api_id'], 12345)
        self.assertEqual(kwargs['api_hash'], 'a' * 32)
        self.assertEqual(kwargs['session_string'], 'test-session')
        self.assertTrue(kwargs['in_memory'])
        self.assertEqual(kwargs['name'], 'hexa_pyrogram')
        self.assertEqual(kwargs['workdir'], '/tmp')
        self.assertEqual(kwargs['sleep_threshold'], 0)

    async def test_authorized_headless_connect_starts_updates_without_login_prompt(self):
        await self.transport.connect()
        self.assertTrue(await self.transport.is_user_authorized())
        self.raw.connect.assert_awaited_once()
        self.raw.invoke.assert_awaited_once()
        self.assertEqual(type(self.raw.invoke.await_args.args[0]).__name__, 'GetState')
        self.raw.initialize.assert_awaited_once()
        self.raw.authorize.assert_not_awaited()
        self.raw.start.assert_not_awaited()

    async def test_unauthorized_headless_connect_never_prompts(self):
        self.raw.connect.return_value = False
        await self.transport.connect()
        self.assertFalse(await self.transport.is_user_authorized())
        self.raw.initialize.assert_not_awaited()
        self.raw.get_me.assert_not_awaited()
        self.raw.authorize.assert_not_awaited()
        self.raw.start.assert_not_awaited()

    async def test_revoked_session_reports_unauthorized_at_every_login_phase(self):
        from pyrogram.errors import AuthKeyUnregistered
        for phase in ['connect', 'invoke', 'get_me']:
            with self.subTest(phase=phase):
                for name in ['connect', 'invoke', 'get_me', 'initialize', 'disconnect']:
                    getattr(self.raw, name).reset_mock()
                    getattr(self.raw, name).side_effect = None
                self.raw.connect.return_value = True
                self.raw.get_me.return_value = NS(id=123, is_bot=False)
                getattr(self.raw, phase).side_effect = AuthKeyUnregistered()
                self.raw.is_connected = True
                await self.transport.connect()
                self.assertFalse(await self.transport.is_user_authorized())
                self.raw.initialize.assert_not_awaited()
                self.raw.authorize.assert_not_awaited()
                await self.transport.disconnect()
                self.raw.disconnect.assert_awaited_once()

    async def test_malformed_session_errors_are_friendly_and_do_not_repeat_input(self):
        import binascii
        import struct
        private_input = 'private-session-value'
        for error_type in [binascii.Error, struct.error, ValueError]:
            for method in ['connect', 'start']:
                with self.subTest(error=error_type, method=method):
                    self.raw.connect.side_effect = None
                    self.raw.start.side_effect = None
                    getattr(self.raw, method).side_effect = error_type(private_input)
                    with self.assertRaisesRegex(ValueError, 'Invalid Pyrogram session string') as result:
                        await getattr(self.transport, method)()
                    self.assertNotIn(private_input, str(result.exception))
                    self.raw.authorize.assert_not_awaited()

    async def test_caption_keyboard_and_callback_notice_are_preserved(self):
        message = self.message(caption='Wild Abra [Psychic]\nLv. 4 • HP 20/20',
                               rows=[[('Battle', b'exact-callback')]])
        self.raw.request_callback_answer.return_value = NS(message='Please complete verification.')
        wrapped = self.transport.wrap(message)
        self.assertIsInstance(wrapped, PyrogramMessage)
        self.assertEqual(wrapped.raw_text, message.caption)
        self.assertEqual(wrapped.buttons[0][0].text, 'Battle')
        answer = await wrapped.click(0, 0)
        self.raw.request_callback_answer.assert_awaited_once_with(
            chat_id=572621020, message_id=3, callback_data=b'exact-callback', timeout=15)
        self.assertEqual(answer.message, 'Please complete verification.')

    async def test_url_button_is_not_opened_or_used_as_callback(self):
        message = self.message(rows=[[('Battle', None)]])
        message.reply_markup.inline_keyboard[0][0].url = 'https://example.com'
        with self.assertRaisesRegex(ValueError, 'not a callback button'):
            await self.transport.wrap(message).click(0, 0)
        self.raw.request_callback_answer.assert_not_awaited()

    async def test_history_and_deleted_messages_match_controller_contract(self):
        newest, older = self.message('new', ident=4), self.message('old', ident=3)

        async def history(chat, limit):
            self.assertEqual((chat, limit), (572621020, 12))
            yield newest
            yield NS(empty=True)
            yield older

        self.raw.get_chat_history = Mock(side_effect=history)
        history_messages = await self.transport.get_messages(NS(id=572621020), limit=12)
        self.assertEqual([message.id for message in history_messages], [4, 3])
        self.raw.get_messages.return_value = NS(empty=True)
        self.assertIsNone(await self.transport.get_messages(572621020, ids=7))
        self.raw.get_messages.assert_awaited_once_with(572621020, message_ids=7, replies=0)
        self.raw.get_messages.return_value = older
        self.assertEqual((await self.transport.get_messages(572621020, ids=3)).raw_text, 'old')

    async def test_literal_alerts_and_bot_identity(self):
        from pyrogram.enums import ParseMode
        self.raw.send_message.return_value = self.message('alert', ident=9)
        result = await self.transport.send_message(NS(id=-100123), '<b>Literal</b>', parse_mode=None)
        self.assertEqual(result.id, 9)
        self.raw.send_message.assert_awaited_once_with(-100123, '<b>Literal</b>', parse_mode=ParseMode.DISABLED)
        self.raw.get_users.return_value = NS(id=572621020, is_bot=True, username='HeXamonbot')
        self.assertTrue((await self.transport.get_entity('@HeXamonbot')).bot)
        self.raw.get_chat.return_value = NS(id=-100456)
        self.assertEqual(await self.transport.get_input_entity(-100456), -100456)

    async def test_bot_sessions_are_rejected_as_owner(self):
        self.raw.get_me.return_value = NS(id=123, is_bot=True)
        with self.assertRaisesRegex(ValueError, 'user-account session'):
            await self.transport.get_me()

    async def test_disconnection_stops_waiter_and_closes_dispatcher(self):
        self.raw.is_connected = self.raw.is_initialized = True
        waiter = asyncio.create_task(self.transport.run_until_disconnected())
        await asyncio.sleep(0)
        await self.transport.disconnect()
        await asyncio.wait_for(waiter, 1)
        self.raw.terminate.assert_awaited_once()
        self.raw.disconnect.assert_awaited_once()
        self.assertFalse(await self.transport.is_user_authorized())

    async def test_new_and_edited_bot_messages_route_with_sender_chat_checks(self):
        from pyrogram.handlers import EditedMessageHandler
        controller = NS(receive=AsyncMock(), command=AsyncMock())
        self.transport.register_controller_handlers(controller, NS(id=572621020), 123)
        incoming, edited, commands = [call.args[0] for call in self.raw.add_handler.call_args_list]
        self.assertIsInstance(edited, EditedMessageHandler)
        message = self.message(caption='A wild Abra (Lv. 4) has appeared')
        for handler in [incoming, edited]:
            self.assertTrue(await handler.filters(self.raw, message))
            await handler.callback(self.raw, message)
        self.assertEqual(controller.receive.await_count, 2)
        self.assertEqual(controller.receive.await_args.args[0].message.raw_text, message.caption)
        for other in [self.message(chat=789), self.message(sender=789), self.message(outgoing=True)]:
            self.assertFalse(await incoming.filters(self.raw, other))
            await incoming.callback(self.raw, other)
        self.assertEqual(controller.receive.await_count, 2)

    async def test_saved_messages_command_uses_sender_ownership_even_when_not_outgoing(self):
        controller = NS(receive=AsyncMock(), command=AsyncMock())
        self.transport.register_controller_handlers(controller, 572621020, 123)
        handler = self.raw.add_handler.call_args_list[-1].args[0]
        saved = self.message('.caught', chat=123, sender=123, outgoing=False)
        self.assertTrue(await handler.filters(self.raw, saved))
        await handler.callback(self.raw, saved)
        event = controller.command.await_args.args[0]
        self.assertTrue(event.out)
        self.assertEqual((event.chat_id, event.raw_text), (123, '.caught'))
        for other in [self.message('.caught', chat=123, sender=789),
                      self.message('.caught', chat=-100123, sender=789)]:
            self.assertFalse(await handler.filters(self.raw, other))
            await handler.callback(self.raw, other)
        self.assertEqual(controller.command.await_count, 1)

    async def test_owner_status_from_private_and_group_chats_replies_in_that_chat(self):
        from hexa_userbot import Controller, Settings
        controller = Controller(self.transport, 572621020, 123, Settings(notify='me'))
        self.transport.register_controller_handlers(controller, 572621020, 123)
        handler = self.raw.add_handler.call_args_list[-1].args[0]
        for chat in [789, -100123, -100987]:
            message = self.message('.status', chat=chat, sender=123, outgoing=True)
            self.assertTrue(await handler.filters(self.raw, message))
            await handler.callback(self.raw, message)
            self.assertEqual(self.raw.send_message.await_args.args[0], chat)
        for sender in [789, None]:
            message = self.message('.battle', chat=-100123, sender=sender, outgoing=True)
            await handler.callback(self.raw, message)
        self.assertFalse(controller.running)
        self.assertEqual(self.raw.send_message.await_count, 3)

    async def test_forwarded_and_inline_command_metadata_reaches_owner_guard(self):
        from hexa_userbot import Controller, Settings
        controller = Controller(self.transport, NS(id=572621020), 123,
                                Settings(mode='battle', notify='me', delay=0, hunt_delay=0))
        self.transport.register_controller_handlers(controller, 572621020, 123)
        handler = self.raw.add_handler.call_args_list[-1].args[0]
        for extra in [{'forward_from': NS(id=999)}, {'via_bot': NS(id=888)}]:
            await handler.callback(self.raw, self.message('.caught', chat=123, sender=123, **extra))
        self.assertFalse(controller.running)
        self.raw.send_message.assert_not_awaited()

    async def test_shared_controller_battles_and_catches_through_pyrogram_callbacks(self):
        from hexa_userbot import Controller, Settings, normalized
        messages = {}

        async def current(chat, message_ids, replies):
            return messages.get(message_ids)

        self.raw.get_messages.side_effect = current
        self.raw.send_message.return_value = self.message('/hunt', ident=1, outgoing=True)
        moves = [[('Tackle', 'weak'), ('Psychic', 'strong'), ('Poke Ball', 'balls'), ('Run', 'run')]]
        for mode in ['battle', 'catch']:
            with self.subTest(mode=mode):
                self.raw.request_callback_answer.reset_mock()
                controller = Controller(self.transport, NS(id=572621020), 123,
                                        Settings(mode=mode, targets={normalized('Abra')},
                                                 notify='me', delay=0, hunt_delay=0))
                controller.running = True
                controller.generation = 1
                await controller.send_hunt()
                name = 'Rhydon' if mode == 'battle' else 'Abra'
                encounter = self.message(f'A wild {name} (Lv. 20) has appeared',
                                         rows=[[('Battle', 'battle')]], ident=2)
                messages[2] = encounter
                await controller.process(self.transport.wrap(encounter))
                self.assertEqual(self.raw.request_callback_answer.await_args.kwargs['callback_data'], 'battle')
                caption = (f'Wild {name} [Psychic]\nLv. 20 • HP 80/80\nCurrent turn: Player\n'
                           'Tackle [Normal]\nPower: 40, Accuracy: 100\n'
                           'Psychic [Psychic]\nPower: 90, Accuracy: 100')
                messages[3] = self.message(caption=caption, rows=moves)
                await controller.process(self.transport.wrap(messages[3]))
                self.assertEqual(self.raw.request_callback_answer.await_args.kwargs['callback_data'],
                                 'strong' if mode == 'battle' else 'weak')
                if mode == 'catch':
                    messages[3] = self.message(caption=caption.replace('80/80', '40/80'), rows=moves)
                    await controller.process(self.transport.wrap(messages[3]))
                    self.assertEqual(self.raw.request_callback_answer.await_args.kwargs['callback_data'], 'balls')
                    messages[3] = self.message('Choose which ball?', rows=[[('Regular Ball x5', 'throw')]])
                    await controller.process(self.transport.wrap(messages[3]))
                    self.assertEqual(self.raw.request_callback_answer.await_args.kwargs['callback_data'], 'throw')
                    self.assertEqual(controller.throws, 1)
                else:
                    self.assertEqual(controller.throws, 0)
