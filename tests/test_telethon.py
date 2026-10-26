"""Exercise the real Telethon event filters with in-memory events only."""
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

from hexa_userbot import register_handlers, resolve_bot

try:
    from telethon import events, types
except ImportError:
    events = types = None


@unittest.skipIf(events is None, 'Install requirements.txt to test real Telethon event filters')
class TelethonHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        async def entity(peer):
            return types.InputPeerUser(getattr(peer, 'id', peer), 0)

        self.client = NS(add_event_handler=Mock(), get_input_entity=AsyncMock(side_effect=entity))
        self.controller = NS(receive=AsyncMock(), command=AsyncMock())
        register_handlers(self.client, self.controller, 572621020, 123)
        calls = self.client.add_event_handler.call_args_list
        self.incoming, self.edited, self.commands = [call.args[1] for call in calls]
        for builder in [self.incoming, self.edited, self.commands]:
            await builder.resolve(self.client)

    def event(self, text, chat=123, sender=123, outgoing=True, edited=False, forwarded=False):
        message = types.Message(id=42, peer_id=types.PeerUser(chat),
                                from_id=types.PeerUser(sender), message=text, out=outgoing)
        if forwarded:
            message.fwd_from = types.MessageFwdHeader(date=None)
        return (events.MessageEdited if edited else events.NewMessage).Event(message)

    async def test_owner_commands_match_in_saved_bot_and_other_chats(self):
        for chat in [123, 572621020, 789, -100456]:
            for command in ['.battle', '. battle', '.caught', '.stop', '.status',
                            '.help', '.resume', '.CAUGHT']:
                event = self.event(command, chat=chat)
                self.assertTrue(self.commands.filter(event), command)

    async def test_other_senders_incoming_and_forwarded_commands_are_ignored(self):
        for event in [self.event('.caught', chat=789, sender=789),
                      self.event('.caught', outgoing=False),
                      self.event('.caught', forwarded=True),
                      self.event('hi .caught'), self.event('.resumepher')]:
            self.assertIsNone(self.commands.filter(event))

    async def test_removed_commands_do_not_match_the_event_filter(self):
        for command in ['/go', '/stop', '/status', '.hexa battle', '.hexa help',
                        '.hexa stop', '.hexa catch', '.hexa status']:
            self.assertIsNone(self.commands.filter(self.event(command)))

    async def test_configured_sudo_incoming_commands_match_in_private_and_group_chats(self):
        self.client.add_event_handler.reset_mock()
        self.controller.cfg = NS(sudo_users=frozenset({456}))
        register_handlers(self.client, self.controller, 572621020, 123)
        commands = self.client.add_event_handler.call_args_list[-1].args[1]
        await commands.resolve(self.client)
        for chat in [123, -100789]:
            self.assertTrue(commands.filter(self.event('.battle', chat=chat, sender=456, outgoing=False)))
            self.assertTrue(commands.filter(self.event('.help', chat=chat)))
        for event in [self.event('.battle', sender=789, outgoing=False),
                      self.event('.battle', sender=456, outgoing=False, forwarded=True),
                      self.event('/stop', sender=456, outgoing=False)]:
            self.assertIsNone(commands.filter(event))

    async def test_only_bot_messages_and_edits_match_receive_handler(self):
        for builder, edited in [(self.incoming, False), (self.edited, True)]:
            event = self.event('Wild Abra', chat=572621020, sender=572621020,
                               outgoing=False, edited=edited)
            self.assertTrue(builder.filter(event))
            self.assertIsNone(builder.filter(self.event('Wild Abra', chat=572621020,
                                                        sender=123, edited=edited)))
            self.assertIsNone(builder.filter(self.event('Wild Abra', chat=789, sender=789,
                                                        outgoing=False, edited=edited)))

    async def test_numeric_bot_configuration_rejects_a_user_account(self):
        self.client.get_entity = AsyncMock(return_value=NS(id=572621020, bot=False))
        with self.assertRaisesRegex(ValueError, 'does not identify a Telegram bot'):
            await resolve_bot(self.client, '', 572621020)
