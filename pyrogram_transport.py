"""Pyrogram 2.x adapter for the shared HeXamon controller.

Only ordinary public Pyrogram methods are used. Session strings remain native to
their selected library; this adapter never converts or exports a supplied string.
"""
import asyncio
import binascii
import struct
from pathlib import Path
from types import SimpleNamespace


def peer_id(peer):
    return getattr(peer, 'id', peer)


def entity(value):
    kind = getattr(value, 'type', None)
    kind = getattr(kind, 'value', kind)
    return SimpleNamespace(id=value.id,
                           bot=bool(getattr(value, 'is_bot', False) or kind == 'bot'),
                           username=getattr(value, 'username', None))


class PyrogramMessage:
    """Expose the message fields and callback click required by Controller."""

    def __init__(self, transport, message):
        self.transport = transport
        self.message = message
        self.id = message.id
        self.raw_text = str(getattr(message, 'text', None) or getattr(message, 'caption', None) or '')
        self.edit_date = getattr(message, 'edit_date', None)
        self.out = bool(getattr(message, 'outgoing', False))
        keyboard = getattr(getattr(message, 'reply_markup', None), 'inline_keyboard', None) or []
        self.buttons = [[SimpleNamespace(text=button.text) for button in row] for row in keyboard]

    async def click(self, row, column):
        keyboard = getattr(getattr(self.message, 'reply_markup', None), 'inline_keyboard', None) or []
        button = keyboard[row][column]
        data = getattr(button, 'callback_data', None)
        if data is None:
            raise ValueError('Selected control is not a callback button; inspect the bot chat.')
        return await self.transport.raw.request_callback_answer(
            chat_id=self.message.chat.id, message_id=self.id, callback_data=data, timeout=15)


class PyrogramTransport:
    """Provide the small Telethon-shaped interface used by the controller."""

    def __init__(self, api_id, api_hash, session_path, session_string=''):
        # Import inside the running async entrypoint. Pyrogram 2's compatibility
        # layer expects an event loop, including on Python 3.12/Termux Python 3.13.
        from pyrogram import Client
        from pyrogram.enums import ParseMode

        path = Path(session_path)
        name = path.name[:-8] if path.name.endswith('.session') else path.name
        self.raw = Client(name=name, workdir=str(path.parent), api_id=api_id, api_hash=api_hash,
                          session_string=session_string or None, in_memory=bool(session_string),
                          sleep_threshold=0, workers=4, parse_mode=ParseMode.DISABLED)
        self._authorized = False
        self._closed = asyncio.Event()

    async def start(self):
        self._closed.clear()
        try:
            await self.raw.start()
        except (binascii.Error, struct.error, ValueError):
            raise ValueError('Invalid Pyrogram session string. Use a Pyrogram 2 session '
                             'with TELEGRAM_LIBRARY=pyrogram.') from None
        self._authorized = True
        return self

    async def connect(self):
        """Connect without ever invoking Pyrogram's interactive authorize()."""
        from pyrogram import raw
        from pyrogram.errors import Unauthorized

        self._closed.clear()
        self._authorized = False
        try:
            self._authorized = bool(await self.raw.connect())
        except Unauthorized:
            return
        except (binascii.Error, struct.error, ValueError):
            raise ValueError('Invalid Pyrogram session string. Use a Pyrogram 2 session '
                             'with TELEGRAM_LIBRARY=pyrogram.') from None
        if not self._authorized:
            return
        try:
            # Match the authorized branch of Pyrogram Client.start(), while
            # deliberately omitting its interactive authorization branch.
            await self.raw.invoke(raw.functions.updates.GetState())
            self.raw.me = await self.raw.get_me()
            await self.raw.initialize()
        except Unauthorized:
            # Stored user_id only indicates a previously authorized session.
            # A revoked key can still fail GetState or get_me after connect.
            self._authorized = False

    async def is_user_authorized(self):
        return self._authorized

    async def disconnect(self):
        self._closed.set()
        try:
            if self.raw.is_initialized:
                await self.raw.terminate()
        finally:
            if self.raw.is_connected:
                await self.raw.disconnect()
            self._authorized = False

    async def run_until_disconnected(self):
        while self.raw.is_connected and not self._closed.is_set():
            try:
                await asyncio.wait_for(self._closed.wait(), 1)
            except asyncio.TimeoutError:
                continue
        if not self._closed.is_set():
            raise ConnectionError('Pyrogram disconnected; restart the service after inspecting its logs.')

    async def get_me(self):
        value = await self.raw.get_me()
        if getattr(value, 'is_bot', False):
            raise ValueError('Use a Telegram user-account session, not a bot session.')
        return entity(value)

    async def get_entity(self, peer):
        return entity(await self.raw.get_users(peer_id(peer)))

    async def get_input_entity(self, peer):
        from pyrogram.errors import PeerIdInvalid

        if peer == 'me':
            return 'me'
        try:
            return (await self.raw.get_chat(peer_id(peer))).id
        except (PeerIdInvalid, ValueError):
            raise ValueError('Chat is not cached or accessible to this account.') from None

    async def iter_dialogs(self):
        async for dialog in self.raw.get_dialogs():
            chat = dialog.chat
            yield SimpleNamespace(id=chat.id, input_entity=chat.id, entity=entity(chat))

    def wrap(self, message):
        if message is None or getattr(message, 'empty', False):
            return None
        return PyrogramMessage(self, message)

    async def send_message(self, peer, text, parse_mode=None):
        from pyrogram.enums import ParseMode

        # Controller's parse_mode=None means literal text, not Pyrogram's
        # Markdown+HTML default. Notifications must not interpret game text.
        return self.wrap(await self.raw.send_message(peer_id(peer), text, parse_mode=ParseMode.DISABLED))

    async def get_messages(self, peer, ids=None, limit=None):
        if limit is not None:
            messages = []
            async for message in self.raw.get_chat_history(peer_id(peer), limit=limit):
                wrapped = self.wrap(message)
                if wrapped is not None:
                    messages.append(wrapped)
            return messages
        if ids is None:
            raise ValueError('Specify message ids or a history limit.')
        result = await self.raw.get_messages(peer_id(peer), message_ids=ids, replies=0)
        if isinstance(result, (list, tuple)):
            return [wrapped for message in result if (wrapped := self.wrap(message)) is not None]
        return self.wrap(result)

    def register_controller_handlers(self, controller, peer, owner):
        from pyrogram import filters
        from pyrogram.handlers import EditedMessageHandler, MessageHandler

        bot_id = peer_id(peer)

        async def from_game(_, client, message):
            return (getattr(getattr(message, 'chat', None), 'id', None) == bot_id
                    and getattr(getattr(message, 'from_user', None), 'id', None) == bot_id
                    and not getattr(message, 'outgoing', False))

        async def from_owner(_, client, message):
            return (getattr(getattr(message, 'chat', None), 'id', None) in (owner, bot_id)
                    and getattr(getattr(message, 'from_user', None), 'id', None) == owner)

        async def receive(client, message):
            # Check again in the callback so directly dispatched messages cannot
            # bypass the sender/chat restriction.
            if await from_game(None, client, message):
                wrapped = self.wrap(message)
                if wrapped is not None:
                    await controller.receive(SimpleNamespace(message=wrapped))

        async def command(client, message):
            if not await from_owner(None, client, message):
                return
            forwarded = (getattr(message, 'forward_from', None)
                         or getattr(message, 'forward_from_chat', None)
                         or getattr(message, 'forward_sender_name', None)
                         or getattr(message, 'forward_date', None))
            via = getattr(message, 'via_bot', None)
            await controller.command(SimpleNamespace(
                raw_text=str(getattr(message, 'text', None) or getattr(message, 'caption', None) or ''),
                # Saved Messages may have outgoing=False in Pyrogram. Ownership
                # is established above using the actual sender and control chat.
                out=True, chat_id=message.chat.id, fwd_from=forwarded,
                via_bot_id=getattr(via, 'id', None)))

        game_filter = filters.create(from_game, name='HeXamonIncoming')
        owner_filter = filters.create(from_owner, name='HeXamonOwner')
        self.raw.add_handler(MessageHandler(receive, game_filter))
        self.raw.add_handler(EditedMessageHandler(receive, game_filter))
        self.raw.add_handler(MessageHandler(command, owner_filter))
