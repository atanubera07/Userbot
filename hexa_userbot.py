"""HeXamon userbot with separate battle and catch modes.

Python 3.10+, Telethon 1.x or Pyrogram 2.x. Configure .env, then run python hexa_userbot.py.
Commands sent by the logged-in account or configured sudo users in any chat:
    .battle   battle ordinary Pokemon without catching
    .caught    catch listed targets, weakening when needed
    .stop    pause the worker
    .status  report the selected mode and encounter
    .help    show commands

.resume starts the selected mode. A space after the dot is optional.
Special encounters and verification questions pause for manual handling.
Use --check for offline configuration validation or --self-test for mocked tests.
"""
import argparse
import asyncio
import contextlib
import hashlib
import json
import logging
import math
import os
import re
import signal
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from telegram_credentials import parse_api_hash, parse_api_id

LOG = logging.getLogger('hexa')

MODES = {'battle', 'catch'}
LIBRARIES = {'telethon', 'pyrogram'}
COMMAND_PATTERN = re.compile(r'(?i)^[ \t]*\.[ \t]*(battle|caught|resume|stop|status|help)[ \t]*\Z')
HELP_TEXT = (
    'HeXamon commands (account owner and configured sudo users):\n'
    '.battle — battle all ordinary wild Pokémon; catching disabled\n'
    '.caught — catch listed targets, weakening when needed\n'
    '.stop — pause automation\n'
    '.status — show mode, stage, and limits\n'
    '.help — show this message\n'
    '.resume — start the selected mode again\n'
    'Before starting or changing modes, finish any active encounter and '
    'answer pending verification manually.'
)

TARGETS = ['Starly', 'Metang', 'Golett', 'Fletchling', 'Bramblin', 'Quaxly', 'Treecko', 'Noibat', 'Tinkatink', 'Axew', 'Charcadet', 'Litwick', 'Beldum', 'Frigibax', 'Bagon', 'Bulbasaur', 'Swablu', 'Arrokuda', 'Porygon', 'Litten', 'Larvitar', 'Flabebe', 'Goomy', 'Slakoth', 'Vigoroth', 'Slaking', 'Timburr', 'Rookidee', 'Smoliv', 'Arboliva', 'Corviknight', 'Zorua', 'Gallade', 'Gardevoir', 'Archaludon', 'Duraludon', 'Gible', 'Garchomp', 'Talonflame', 'Magikarp', 'Gyarados', 'Manectric', 'Abra', 'Alakazam', 'Chandelure', 'Metagross', 'Venusaur', 'Charizard', 'Squirtle', 'Blastoise', 'Gastly', 'Gengar', 'Iron Thorns', 'Ampharos', 'Aerodactyl', 'Primarina', 'Florges', 'Meowscarada', 'Sprigatito', 'Incineroar', 'Kangaskhan', 'Kommo-o', 'Jangmo-o', 'Rufflet', 'Pawmi', 'Pawmot', 'Iron Leaves', 'Houndoom', 'Goodra', 'Impidimp', 'Grimmsnarl', 'Scyther', 'Scizor', 'Houndour', 'Frillish', 'Jellicent']


# Separate legendary encounter list: alert and pause; never merge into TARGETS.
LEGENDARY_TARGETS = ['Articuno', 'Zapdos', 'Moltres', 'Mewtwo', 'Mew', 'Raikou', 'Entei', 'Suicune', 'Lugia', 'Ho-Oh', 'Celebi', 'Regirock', 'Regice', 'Registeel', 'Latias', 'Latios', 'Kyogre', 'Groudon', 'Rayquaza', 'Jirachi', 'Deoxys', 'Uxie', 'Mesprit', 'Azelf', 'Dialga', 'Palkia', 'Heatran', 'Regigigas', 'Giratina', 'Cresselia', 'Phione', 'Manaphy', 'Darkrai', 'Shaymin', 'Arceus', 'Victini', 'Cobalion', 'Terrakion', 'Virizion', 'Tornadus', 'Thundurus', 'Landorus', 'Reshiram', 'Zekrom', 'Kyurem', 'Keldeo', 'Meloetta', 'Genesect', 'Xerneas', 'Yveltal', 'Zygarde', 'Diancie', 'Hoopa', 'Volcanion', 'Type: Null', 'Silvally', 'Tapu Koko', 'Tapu Lele', 'Tapu Bulu', 'Tapu Fini', 'Cosmog', 'Cosmoem', 'Solgaleo', 'Lunala', 'Necrozma', 'Magearna', 'Marshadow', 'Zeraora', 'Meltan', 'Melmetal', 'Nihilego', 'Buzzwole', 'Pheromosa', 'Xurkitree', 'Celesteela', 'Kartana', 'Guzzlord', 'Poipole', 'Naganadel', 'Stakataka', 'Blacephalon', 'Zacian', 'Zamazenta', 'Eternatus', 'Kubfu', 'Urshifu', 'Regieleki', 'Regidrago', 'Glastrier', 'Spectrier', 'Calyrex', 'Zarude', 'Enamorus', 'Wo-Chien', 'Chien-Pao', 'Ting-Lu', 'Chi-Yu', 'Koraidon', 'Miraidon', 'Okidogi', 'Munkidori', 'Fezandipiti', 'Ogerpon', 'Terapagos', 'Pecharunt']


def clean(text):
    text = unicodedata.normalize('NFKC', text or '')
    return ''.join(c for c in text if unicodedata.category(c) != 'Cf')


def normalized(text):
    return ''.join(c for c in unicodedata.normalize('NFKD', clean(text)).casefold()
                   if c.isalnum() and not unicodedata.combining(c))


normalize = normalized
LEGENDARY_NAMES = {normalized(x) for x in LEGENDARY_TARGETS}
BALLS = {'regularball', 'greatball', 'ultraball', 'repeatball'}
ENCOUNTER = re.compile(
    r'^\s*(?:a\s+)?wild\s+(.+?)\s*\(\s*(?:lv\.?|level)\s*[:.]?\s*\d+\s*\)'
    r'(?:\s+has\s+appeared)?\s*[!☆★✨]*\s*$', re.I | re.M)
WILD = re.compile(
    r'^\s*Wild\s+(.+?)\s*\[[^\]\n]*\]\s*\n\s*Lv\.?\s*\d+\s*[•·|]?\s*'
    r'HP\s*:?\s*([\d,]+)\s*/\s*([\d,]+)', re.I | re.M)
MOVES = re.compile(
    r'^\s*([^\n\[\]]+?)\s*\[[^\]\n]*\]\s*\n\s*Power:\s*(\d+)'
    r'(?:\s*,\s*Accuracy:|\s*$)', re.I | re.M)
STOCK = re.compile(r'\s*(?:[x×]\s*([\d,]+)|\(([\d,]+)\))\s*$', re.I)


SAFARI_STOCK = re.compile(r"\bSafari\s+Balls?\s+remaining\s*:\s*([\d,]+)", re.I)
CATCH_ADVICE = re.compile(
    r"^\s*Lower\s+.+?['’]s\s+HP\s+for\s+a\s+better\s+chance\s+of\s+catching\s+it[.!]?\s*$", re.I)
BALL_MENU_PROMPTS = {
    'choosewhichball', 'chooseaball', 'chooseapokeball', 'chooseapokeballtothrow',
    'whichballwouldyouliketouse', 'whichballwouldyouliketothrow',
    'whichpokeballwouldyouliketouse', 'whichpokeballwouldyouliketothrow',
}


def encounter_name(text):
    match = ENCOUNTER.search(clean(text))
    return match.group(1).strip(' *_') if match else None


def ball_details(label):
    label = clean(label).strip()
    match = STOCK.search(label)
    count = int((match.group(1) or match.group(2)).replace(',', '')) if match else None
    name = normalized(label[:match.start()] if match else label)
    name = {'regular': 'regularball', 'repeat': 'repeatball'}.get(name, name)
    return (name, count) if name in BALLS else None


def choose_ball(labels, wanted):
    candidates = [x for x in labels if (info := ball_details(x))
                  and info[0] == normalized(wanted) and info[1] != 0]
    return candidates[0] if len(candidates) == 1 else None


def menu_label(labels):
    choices = [x for x in labels if normalized(x) in {'pokeball', 'pokeballs'}
               and STOCK.search(clean(x)) is None]
    return choices[0] if len(choices) == 1 else None



def switch_label(labels):
    """Prefer the named team; otherwise use the lowest numbered slot offered by the bot."""
    team = [label for label in labels if 'huntteam' in normalized(label)]
    if len(team) == 1:
        return team[0]
    if len(team) > 1:
        return None
    slots = [(int(normalized(label)), label) for label in labels
             if re.fullmatch(r'[1-9]\d*', normalized(label))]
    if not slots:
        return None
    lowest = min(number for number, _ in slots)
    candidates = [label for number, label in slots if number == lowest]
    return candidates[0] if len(candidates) == 1 else None


def screen_key(msg):
    return (msg.id, getattr(msg, 'edit_date', None), msg.raw_text or '',
            tuple(tuple(b.text for b in row) for row in (msg.buttons or [])))


def action_key(msg, labels, label):
    return screen_key(msg), label



def escape_confirmed(text):
    return bool(re.search(
        r"^\s*(?:you\s+(?:successfully\s+)?(?:ran away|fled)\b|"
        r"you\s+(?:successfully\s+)?escaped\s+(?:from\s+(?:the\s+)?battle|safely)\b|"
        r"(?:you\s+)?got away safely\b)", clean(text), re.I | re.M))


def terminal(text, selected_name=''):
    text = clean(text)
    if escape_confirmed(text) or re.search(r'^\s*you caught\b', text, re.I | re.M):
        return True
    # Ball failures can prefix the outcome on the same line, e.g.
    # "Your Regular ball failed and the wild Gible fled."
    species = re.escape(selected_name) if selected_name else r"[^\n]+?"
    if re.search(r"\bwild[ \t]+" + species +
                 r"[ \t]+(?:has[ \t]+)?(?:fled|fainted|ran away)\b", text, re.I):
        return True
    return bool(selected_name and re.search(
        r'^\s*' + re.escape(selected_name) + r'\s+(?:has\s+)?(?:fled|fainted|ran away)\b',
        text, re.I | re.M))



def verification_prompt(text):
    """Conservative pause detector only; never solve or answer a challenge."""
    text = clean(text).strip()
    if not text:
        return False
    explicit = re.search(
        r"\b(?:verification|verify (?:that )?you(?:'re| are)|captcha|human (?:check|verification)|"
        r"prove (?:that )?you(?:'re| are)|anti[- ]?(?:bot|automation))\b", text, re.I)
    if explicit:
        return True
    # Known game UI must not be mistaken for a verification question.
    if (normalized(text) in BALL_MENU_PROMPTS or 'chooseyournextpokemon' in normalized(text)
            or encounter_name(text) or WILD.search(text)):
        return False
    # The supplied challenge has no 'verification' heading. Unclassified
    # standalone questions are handed to the user, including unfamiliar wording.
    # Challenges may append ': 🔎 your turn...' after the question.
    return bool(re.search(r'[?？]', text))


def calculate_health_percentage(max_hp, current_hp):
    if max_hp <= 0 or not 0 <= current_hp <= max_hp:
        raise ValueError('Invalid wild HP: require 0 <= current <= maximum, maximum > 0')
    return 100 * current_hp / max_hp


@dataclass
class Settings:
    targets: set = field(default_factory=lambda: {normalized(x) for x in TARGETS})
    mode: str = 'battle'
    ball: str = 'Regular Ball'
    delay: float = 5
    hunt_delay: float = 1
    max_throws: int = 30
    max_attacks: int = 10
    timeout: float = 45
    attack_move: str = ''
    notify: object = -1004490697963
    alert_tag: str = '@Kill_switch8'
    sudo_users: frozenset = field(default_factory=frozenset)

    @classmethod
    def load(cls):
        selected_mode = os.getenv('HEXA_MODE', 'battle').strip().lower()
        if selected_mode not in MODES:
            raise ValueError('HEXA_MODE must be battle or catch')
        mode = os.getenv('HEXA_TARGET_MODE', 'combined').strip().lower()
        if mode not in {'combined', 'legacy'}:
            raise ValueError('HEXA_TARGET_MODE must be combined or legacy')
        ball = os.getenv('HEXA_BALL', 'Regular Ball').strip()
        if normalized(ball) not in BALLS:
            raise ValueError('HEXA_BALL must be Regular Ball, Great Ball, Ultra Ball, or Repeat Ball')
        def number(name, default, minimum):
            value = float(os.getenv(name, str(default)))
            if not math.isfinite(value):
                raise ValueError(name + ' must be finite')
            return max(minimum, value)
        notify = os.getenv('HEXA_NOTIFY_CHAT', '-1004490697963').strip() or '-1004490697963'
        if re.fullmatch(r'-?\d+', notify):
            notify = int(notify)
        alert_tag = os.getenv('HEXA_ALERT_TAG', '@Kill_switch8').strip()
        if not re.fullmatch(r'@[A-Za-z0-9_]{5,32}', alert_tag):
            raise ValueError('HEXA_ALERT_TAG must be an @username with 5–32 letters, digits or underscores')
        sudo_text = os.getenv('HEXA_SUDO_USERS', '').strip()
        sudo_ids = sudo_text.split(',') if sudo_text else []
        if any(not re.fullmatch(r'[0-9]+', value.strip()) or int(value.strip()) <= 0
               for value in sudo_ids):
            raise ValueError('HEXA_SUDO_USERS must contain comma-separated positive numeric user IDs')
        return cls(targets={normalized(x) for x in TARGETS}, mode=selected_mode,
                   ball=ball, delay=number('HEXA_DELAY', 5, 3),
                   hunt_delay=number('HEXA_HUNT_DELAY', 1, 0.5),
                   max_throws=max(1, int(os.getenv('HEXA_MAX_THROWS', '30'))),
                   max_attacks=max(1, int(os.getenv('HEXA_MAX_ATTACKS', '10'))),
                   timeout=number('HEXA_TIMEOUT', 45, 1),
                   attack_move=os.getenv('HEXA_ATTACK_MOVE', '').strip(), notify=notify, alert_tag=alert_tag,
                   sudo_users=frozenset(int(value.strip()) for value in sudo_ids))



async def resolve_alert_chat(client, destination):
    if destination == 'me':
        return 'me'
    try:
        return await client.get_input_entity(destination)
    except ValueError:
        # Numeric group IDs need this account's access hash, as bot IDs do.
        if isinstance(destination, int):
            async for dialog in client.iter_dialogs():
                if dialog.id == destination:
                    return dialog.input_entity
        raise ValueError('Alert chat could not be resolved. Join/open that group with this Telegram account, '
                         'then set HEXA_NOTIFY_CHAT to its @username or signed chat ID.') from None


class Controller:
    def __init__(self, client, peer, owner, settings, owner_profile=None):
        self.client, self.peer, self.owner, self.cfg = client, peer, owner, settings
        self.alert_peer = settings.notify
        self.alert_tag = settings.alert_tag
        self.alert_error = None
        self.alert_state = 'saved_messages' if settings.notify in ('me', owner) else 'configured'
        self._alert_resolve_after = 0.0
        self.targets = {'*'} if settings.targets == {'*'} else {normalized(x) for x in settings.targets}
        if settings.mode not in MODES:
            raise ValueError('Mode must be battle or catch')
        self.mode = settings.mode
        self.command_lock = asyncio.Lock()
        self.running = False
        self.state = 'idle'
        self.generation = 0
        self.queue = asyncio.Queue(maxsize=200)
        self.active = self.hunt_id = self.encounter_id = None
        self.selected_name = ''
        self.selected_ball = settings.ball
        self.encounter_mode = 'battle'
        self.wild_hp = (None, None)
        self.require_weaken = False
        self.final_throw_screen = None
        self.throws = self.attacks = 0
        self.reason = 'Not started'
        self.verification_text = ''
        self.verification_message_id = None
        # Remember only the latest action on each message. A -> B -> A is a valid
        # screen transition; a global set of all prior screens incorrectly stalls it.
        self.pending = OrderedDict()
        self.worker = None
        self.last_progress = 0
        self.observed = OrderedDict()
        self.escape_reason = None
        self.escape_pending = False
        self.timeout_restarts = 0

    def progress(self):
        self.last_progress = asyncio.get_running_loop().time()

    async def note(self, text):
        LOG.info('%s', text)
        message = f'Hexa | Account {self.owner} | {self.alert_tag}\n{text}'
        occurred_at = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
        group_configured = self.cfg.notify not in ('me', self.owner)
        if group_configured and self.alert_peer == self.owner:
            # A startup cache/membership failure must not leave alerts routed to
            # Saved Messages permanently. Retry resolution at most once a minute.
            now = asyncio.get_running_loop().time()
            if now >= self._alert_resolve_after:
                self._alert_resolve_after = now + 60
                try:
                    self.alert_peer = await resolve_alert_chat(self.client, self.cfg.notify)
                except Exception as exc:
                    self.alert_error = type(exc).__name__
                    LOG.warning('Alert destination resolution failed: %s', self.alert_error)
            if self.alert_peer == self.owner:
                await self._fallback_alert(message, occurred_at, self.alert_error or 'UnresolvedDestination')
                return
        try:
            await self.client.send_message(self.alert_peer, message, parse_mode=None)
        except Exception as exc:
            self.alert_error = type(exc).__name__
            self.alert_state = 'failed'
            LOG.exception('Could not deliver alert to configured chat')
            if group_configured:
                await self._fallback_alert(message, occurred_at, self.alert_error)
        else:
            self.alert_error = None
            self.alert_state = 'configured' if group_configured else 'saved_messages'
            LOG.info('Alert delivered: account=%s destination=%s', self.owner, self.cfg.notify)

    async def _fallback_alert(self, message, occurred_at, error_name):
        self.alert_error = error_name
        self.alert_state = 'fallback'
        # Show only the exception class, never raw RPC/HTTP exception text.
        diagnostic = (f'Group alert delivery failed.\nEvent time: {occurred_at}\n'
                      f'Destination: {self.cfg.notify}\nReason: {error_name}\n')
        try:
            await self.client.send_message(self.owner, diagnostic + message, parse_mode=None)
            LOG.info('Alert fallback delivered: account=%s reason=%s', self.owner, error_name)
        except Exception:
            self.alert_state = 'failed'
            LOG.exception('Could not deliver fallback alert to Saved Messages')

    async def stop(self, reason='Stopped by user', notify=True, state='paused'):
        self.running = False
        self.generation += 1
        self.reason = reason
        self.state = state
        worker, self.worker = self.worker, None
        if worker and worker is not asyncio.current_task():
            worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker
        if notify:
            await self.note(reason)

    async def start(self, mode=None):
        selected_mode = mode or self.mode
        if selected_mode not in MODES:
            raise ValueError('Mode must be battle or catch')
        if self.running:
            await self.note(
                f'Already running in {self.mode} mode. '
                'Use .stop and finish the current encounter manually before changing modes.'
            )
            return
        self.mode = selected_mode
        self.generation += 1
        self.queue = asyncio.Queue(maxsize=200)
        self.pending.clear()
        self.active = self.hunt_id = self.encounter_id = None
        self.running = True
        self.reason = 'Running'
        self.timeout_restarts = 0
        self.verification_text = ''
        self.verification_message_id = None
        self.progress()
        self.worker = asyncio.create_task(self.run(self.generation))
        await self.note(f'Started {self.mode} mode. Finish any pre-existing battle and answer any pending verification manually. Verification questions, legendary encounters, Halloween forms, shiny text or unsupported controls pause automation.')

    async def command(self, event):
        # Recheck the actual sender here as well as in the transport filters.
        sender = getattr(event, 'sender_id', None)
        authorized = (sender == self.owner and getattr(event, 'out', False)
                      or sender in self.cfg.sudo_users)
        if (not authorized
                or getattr(event, 'chat_id', None) is None
                or getattr(event, 'fwd_from', None) is not None
                or getattr(event, 'via_bot_id', None) is not None):
            return
        text = (event.raw_text or '').strip().casefold()
        match = COMMAND_PATTERN.fullmatch(text)
        if not match:
            return
        action = {'caught': 'catch', 'resume': 'start'}.get(match.group(1), match.group(1))
        LOG.info('Authorized command received: %s; sender=%s', action, sender)
        async with self.command_lock:
            if action in MODES:
                await self.start(action)
            elif action == 'start':
                await self.start()
            elif action == 'stop':
                await self.stop()
            elif action == 'status':
                await self.client.send_message(event.chat_id,
                    f'Account {self.owner}: {self.reason}; mode={self.mode}; stage={self.state}; '
                    f'target={self.selected_name or "none"}; encounter={self.encounter_mode}; '
                    f'ball={self.selected_ball}; throws={self.throws}/{self.cfg.max_throws}; '
                    f'attacks={self.attacks}/{self.cfg.max_attacks}; timeout recoveries={self.timeout_restarts}; '
                    f'alerts={self.alert_state}; last_alert_error={self.alert_error or "none"}',
                    parse_mode=None,
                )
            else:
                await self.client.send_message(event.chat_id,
                    f'Account {self.owner}\n' + HELP_TEXT, parse_mode=None)
            LOG.info('Authorized command completed: %s; sender=%s', action, sender)

    async def pause_verification(self, text, message_id=None):
        self.verification_text = clean(text)
        self.verification_message_id = message_id
        await self.stop(
            'Verification detected — hunting paused. Reply to the question manually in the HeXamon chat. '
            'After answering and finishing any active battle, send .resume to start a fresh hunt. '
            'There is no automatic answer or automatic resume.\n\n' + self.verification_text[:2800],
            state='verification')

    async def safety(self, msg):
        text = clean(msg.raw_text)
        if verification_prompt(text):
            await self.pause_verification(text, msg.id)
            return True
        if 'daily hunt limit reached' in text.casefold():
            await self.stop('Daily hunt limit reached.')
            return True
        # The event announcement can arrive separately, before the encounter.
        # Stop before target filtering or any Battle/Run/ball button is clicked.
        halloween_announcement = re.search(
            r'\brare\s+halloween\s+form\s+has\s+appeared\b', text, re.I)
        halloween_encounter = (
            ('🎃' in text or re.search(r'\bhalloween\b', text, re.I))
            and re.search(r'\b(?:a\s+wild|wild)\s+.+', text, re.I))
        if halloween_announcement or halloween_encounter:
            await self.stop(
                '🎃 Halloween Pokémon detected. Automation paused for manual catching. '
                'Inspect the HeXamon chat and finish this encounter before .resume.\n' + text[:2800])
            return True
        wild = WILD.search(text)
        wild_name = encounter_name(text) or (wild.group(1).strip() if wild else None)
        if wild_name and normalized(wild_name) in LEGENDARY_NAMES:
            await self.stop(
                f'Legendary alert: {wild_name}. Automation paused for manual catching. '
                'Finish this encounter in the HeXamon chat before .resume.\n' + text[:2800])
            return True
        if re.search(r'\bshiny\b', text, re.I):
            await self.stop('Shiny detected. Inspect the bot chat; automation paused.\n' + text[:3000])
            return True
        return False

    async def receive(self, event):
        if not self.running:
            return
        msg = event.message
        # A verification edit may belong to the previous hunt. Pause before
        # filtering ordinary stale messages by their IDs.
        if verification_prompt(msg.raw_text):
            await self.pause_verification(msg.raw_text, msg.id)
            return
        if self.hunt_id is not None and msg.id <= self.hunt_id:
            return
        if await self.safety(msg):
            return
        try:
            self.queue.put_nowait((self.generation, msg))
        except asyncio.QueueFull:
            await self.stop('Message queue overflow; inspect the bot chat.')

    async def send_hunt(self):
        if not self.running:
            return
        self.state = 'encounter'
        self.active = self.encounter_id = None
        self.selected_name = ''
        self.selected_ball = self.cfg.ball
        self.encounter_mode = 'battle'
        self.throws = self.attacks = 0
        self.wild_hp = (None, None)
        self.require_weaken = False
        self.final_throw_screen = None
        self.pending.clear()
        self.observed.clear()
        self.escape_reason = None
        self.escape_pending = False
        # Replies arriving during send are buffered, then filtered by the sent ID.
        self.hunt_id = None
        sent = await self.client.send_message(self.peer, '/hunt')
        self.hunt_id = sent.id
        self.progress()
        LOG.info('HUNT sent message=%s', sent.id)

    async def next_hunt(self):
        await asyncio.sleep(self.cfg.hunt_delay)
        await self.send_hunt()

    async def click(self, msg, label, kind=None):
        # Defense in depth: battle mode cannot open a ball menu or throw, even
        # when a stale update or callback reaches an unexpected handler.
        if self.mode == 'battle' and (
                kind == 'throw' or ball_details(label) is not None
                or menu_label([label]) or normalized(label) == 'catch'):
            await self.stop('Catching is disabled in battle mode.')
            return False
        labels = [b.text for row in (msg.buttons or []) for b in row]
        key = action_key(msg, labels, label)
        if not self.running or self.pending.get(msg.id) == key:
            return False
        matches = [(i, j) for i, row in enumerate(msg.buttons or [])
                   for j, b in enumerate(row) if b.text == label]
        if len(matches) != 1:
            await self.stop(f'Button missing or ambiguous: {label!r}. Inspect the SCREEN log.')
            return False
        if kind == 'throw' and self.throws >= self.cfg.max_throws:
            await self.begin_escape(msg, f'Throw limit reached ({self.cfg.max_throws}); catching unsuccessful')
            return False
        if kind == 'attack' and self.attacks >= self.cfg.max_attacks:
            await self.stop('Attack limit reached; inspect battle manually.')
            return False
        token = self.generation
        await asyncio.sleep(self.cfg.delay)
        if not self.running or token != self.generation:
            return False
        # Recheck AFTER the delay: the working script fetched only before sleeping.
        fresh = await self.client.get_messages(self.peer, ids=msg.id)
        if not self.running or token != self.generation:
            return False
        if fresh is None:
            await self.stop('Action message was deleted; inspect chat.')
            return False
        if await self.safety(fresh):
            return False
        if screen_key(fresh) != screen_key(msg):
            LOG.info('Screen changed before click; queueing current version')
            try:
                self.queue.put_nowait((token, fresh))
            except asyncio.QueueFull:
                await self.stop('Message queue overflow while refreshing the screen.')
            return False
        self.pending[msg.id] = key
        self.pending.move_to_end(msg.id)
        if len(self.pending) > 512:
            self.pending.popitem(last=False)
        if kind == 'throw':
            self.throws += 1
            if self.throws >= self.cfg.max_throws:
                self.final_throw_screen = screen_key(fresh)
        elif kind == 'attack':
            self.attacks += 1
        LOG.info('CLICK message=%s label=%r stage=%s', msg.id, label, self.state)
        # An exception here may follow a remotely successful action. run() pauses;
        # it must never retry the same click automatically.
        answer = await fresh.click(*matches[0])
        self.progress()
        response = getattr(answer, 'message', None)
        LOG.info('CALLBACK %r', response)
        if response and await self.safety(SimpleNamespace(raw_text=response, id=msg.id)):
            pass
        elif response and self.state == 'escaping' and escape_confirmed(response):
            await self.next_hunt()
        elif response and self.mode == 'catch' and CATCH_ADVICE.fullmatch(clean(response)):
            await self.handle_catch_advice(fresh, response)
        elif response and re.search(r'cooldown|\bwait\b|not enough|no .*(?:ball|pokemon)|cannot|can.t', response, re.I):
            await self.stop('Bot callback requires attention: ' + response)
        return True

    async def handle_catch_advice(self, clicked, response):
        """Acknowledge known game advice; never blindly repeat the last click."""
        LOG.info('CATCH ADVICE (not an error): %s', response)
        updated = await self.client.get_messages(self.peer, ids=clicked.id)
        if updated is None or not self.running:
            return
        if await self.safety(updated):
            return
        changed = screen_key(updated) != screen_key(clicked)
        labels = [b.text for row in (updated.buttons or []) for b in row]
        hp = WILD.search(clean(updated.raw_text))
        weaken_instead = False
        if not changed and menu_label(labels) and hp:
            name, current, maximum = hp.groups()
            current, maximum = int(current.replace(',', '')), int(maximum.replace(',', ''))
            calculate_health_percentage(maximum, current)
            # The direct-catch exception for max HP <=50 may be rejected by the
            # bot. In that case follow its advice via a different action: weaken.
            if normalized(name) == normalized(self.selected_name) and current / maximum > 0.5:
                self.require_weaken = True
                weaken_instead = True
                LOG.info('Ball menu stayed closed; weaken target to <=50%% before opening it again')
        if changed or weaken_instead:
            try:
                self.queue.put_nowait((self.generation, updated))
            except asyncio.QueueFull:
                await self.stop('Message queue overflow after catching advice; inspect chat.')
        else:
            # A throw may already have been accepted. Its edited result, not
            # a repeated callback on the same screen, determines the next action.
            LOG.info('Waiting for the bot screen/result after catching advice; no duplicate click sent')

    async def catch_safari(self, msg, labels):
        stock = SAFARI_STOCK.search(clean(msg.raw_text))
        if not stock:
            await self.stop('Safari catch screen has no readable ball count. Inspect SCREEN log.')
            return
        count = int(stock.group(1).replace(',', ''))
        if count <= 0:
            await self.stop('No Safari Balls remaining; finish manually.')
            return
        catches = [x for x in labels if normalized(x) == 'catch']
        if not labels:
            return
        if len(catches) != 1:
            await self.stop('Safari screen has no unique Catch button. Inspect SCREEN log.')
            return
        self.active = msg.id
        self.state = 'catching'
        self.selected_ball = 'Safari Ball'
        LOG.info('CATCH Safari target=%s balls_remaining=%s', self.selected_name, count)
        await self.click(msg, catches[0], kind='throw')

    def final_throw_finished(self, msg, labels):
        if self.final_throw_screen is None:
            return True
        previous_text, previous_buttons = self.final_throw_screen[2:]
        text = clean(msg.raw_text)
        failed = re.search(r'\bbroke (?:free|out)\b|\bescaped from (?:the )?(?:poke ?)?ball\b|'
                           r'\bfailed to catch\b', text, re.I)
        if failed and (msg.raw_text or '') != previous_text:
            return True
        # Restored battle controls indicate the throwing animation has finished.
        return (not any(ball_details(x) for x in labels)
                and any(normalized(x) == 'run' for x in labels)
                and ((msg.raw_text or '') != previous_text
                     or tuple(tuple(b.text for b in row) for row in (msg.buttons or [])) != previous_buttons))

    async def begin_escape(self, msg, reason):
        """Back out of menus, then Run; never throw again once escape begins."""
        if not self.running:
            return
        generation = self.generation
        if self.escape_reason is None:
            self.escape_reason = reason
            self.state = 'escaping'
            await self.note(reason + '. Leaving this encounter and restarting hunting.')
        if not self.running or generation != self.generation:
            return
        if await self.safety(msg):
            return
        if terminal(msg.raw_text, self.selected_name):
            await self.next_hunt()
            return
        if self.encounter_mode == 'safari':
            # Safari has no battle/Run screen; /hunt skips its catch encounter.
            await self.next_hunt()
            return
        labels = [b.text for row in (msg.buttons or []) for b in row]
        if not labels:
            return  # Wait for animation/result before choosing a control.
        if self.escape_pending:
            return  # Run may have succeeded remotely; never repeat blindly.
        runs = [x for x in labels if normalized(x) == 'run']
        backs = [x for x in labels if normalized(x) == 'back']
        if len(runs) == 1:
            self.escape_pending = True
            clicked = await self.click(msg, runs[0])
            if not clicked and self.state == 'escaping':
                self.escape_pending = False
        elif len(backs) == 1:
            await self.click(msg, backs[0])
        elif 'chooseyournextpokemon' in normalized(msg.raw_text):
            choice = switch_label(labels)
            if choice is None:
                await self.stop('Cannot leave encounter: no unique Pokemon replacement. Inspect chat.')
                return
            self.active = msg.id
            LOG.info('SWITCH before escape: selecting offered Pokemon=%r', choice)
            await self.click(msg, choice)
        else:
            await self.stop('Cannot leave encounter: no unique Run or Back button. Inspect chat.')

    async def recover_timeout(self, generation):
        """Recover missed updates without creating recursive workers or resuming manual pauses."""
        if not self.running or generation != self.generation:
            return
        self.timeout_restarts += 1
        self.progress()  # One recovery per timeout window, including animations.
        await self.note(f'No progress for {self.cfg.timeout:g} seconds. Automatically recovering hunting.')
        if not self.running or generation != self.generation:
            return
        # The event stream can miss a result/new screen after reconnection.
        # Read-only history inspection also checks for fresh verification/shiny messages.
        recent = await self.client.get_messages(self.peer, limit=12)
        if not self.running or generation != self.generation:
            return
        incoming = sorted((m for m in (recent or []) if not getattr(m, 'out', False)
                           and (self.hunt_id is None or m.id > self.hunt_id)),
                          key=lambda m: m.id, reverse=True)
        for msg in incoming:
            if await self.safety(msg):
                return
        if not self.running or generation != self.generation:
            return
        minimum = self.active if self.active is not None else self.encounter_id
        candidates = [m for m in incoming if minimum is None or m.id >= minimum]
        current = next((m for m in candidates if terminal(m.raw_text, self.selected_name)
                        or WILD.search(clean(m.raw_text)) or encounter_name(m.raw_text)
                        or 'chooseyournextpokemon' in normalized(m.raw_text)
                        or SAFARI_STOCK.search(clean(m.raw_text))), None)
        if current is None and minimum is not None:
            current = await self.client.get_messages(self.peer, ids=minimum)
            if not self.running or generation != self.generation:
                return
            if current is not None and await self.safety(current):
                return
        if current is not None:
            previous = self.observed.get(current.id)
            if previous != screen_key(current):
                await self.process(current)
                # A changed screen is processed once using normal rules/limits.
                return
            if terminal(current.raw_text, self.selected_name):
                await self.next_hunt()
                return
        if self.state == 'encounter':
            await self.next_hunt()
        elif current is None:
            await self.stop('Timed-out encounter screen is unavailable; inspect chat before .resume.')
        elif self.escape_reason is not None:
            # We already tried Back/Run. Its outcome is uncertain; do not replay it.
            await self.stop('Escape outcome not confirmed after timeout; inspect chat before .resume.')
        else:
            await self.begin_escape(current, 'Timeout recovery')

    async def process(self, snapshot):
        if not self.running or (self.hunt_id is not None and snapshot.id <= self.hunt_id):
            return
        msg = await self.client.get_messages(self.peer, ids=snapshot.id)
        if msg is None or not self.running:
            return
        if await self.safety(msg):
            return
        self.observed[msg.id] = screen_key(msg)
        self.observed.move_to_end(msg.id)
        if len(self.observed) > 512:
            self.observed.popitem(last=False)
        text = clean(msg.raw_text)
        labels = [b.text for row in (msg.buttons or []) for b in row]
        LOG.info('SCREEN message=%s stage=%s buttons=%r text=%r', msg.id, self.state, labels, text[:300])
        name = encounter_name(text)
        if self.state == 'encounter':
            if name:
                if self.mode == 'catch' and '*' not in self.targets and normalized(name) not in self.targets:
                    LOG.info('SKIP non-target=%r', name)
                    await self.next_hunt()
                    return
                battle = [x for x in labels if normalized(x) == 'battle']
                safari = SAFARI_STOCK.search(text)
                catches = [x for x in labels if normalized(x) == 'catch']
                if not labels:
                    return  # Telegram may deliver the caption before its keyboard.
                if safari and len(catches) == 1 and not battle:
                    self.selected_name = name
                    self.encounter_id = msg.id
                    self.encounter_mode = 'safari'
                    if self.mode == 'battle':
                        await self.stop('Safari has Catch-only controls; battle mode paused for manual handling.')
                        return
                    await self.catch_safari(msg, labels)
                    return
                if len(battle) != 1:
                    await self.stop('Target encounter has no unique Battle button or recognized Safari Catch screen. Inspect SCREEN log.')
                    return
                self.selected_name = name
                self.selected_ball = self.cfg.ball
                self.encounter_id = msg.id
                self.state = 'battle'
                await self.click(msg, battle[0])
            elif 'an expert trainer' in text.casefold():
                await self.next_hunt()
            elif labels or re.search(r'cooldown|please wait|try again|too (?:fast|many)', text, re.I):
                await self.stop('Unexpected hunt response. Inspect SCREEN log: ' + text[:300])
            return
        if self.state not in {'battle', 'catching', 'switching', 'escaping'}:
            return
        if self.active is not None and msg.id < self.active:
            return
        if terminal(text, self.selected_name):
            if self.encounter_id is not None and msg.id >= self.encounter_id:
                await self.next_hunt()
            return
        if self.escape_reason is not None or self.throws >= self.cfg.max_throws:
            if self.escape_reason is None and not self.final_throw_finished(msg, labels):
                LOG.info('Waiting for final throw outcome before leaving encounter')
                return
            await self.begin_escape(msg, self.escape_reason or
                                    f'Throw limit reached ({self.cfg.max_throws}); catching unsuccessful')
            return
        if self.encounter_mode == 'safari':
            if name and normalized(name) != normalized(self.selected_name):
                await self.stop('Safari species differs from selected encounter. Inspect chat.')
                return
            if SAFARI_STOCK.search(text):
                await self.catch_safari(msg, labels)
            elif labels:
                await self.stop('Unsupported Safari result. Inspect SCREEN log: ' + text[:300])
            return
        # An encounter edit queued before the Battle click is harmless.
        if name and msg.id == self.encounter_id:
            if self.active is None:
                battle = next((x for x in labels if normalized(x) == 'battle'), None)
                if battle:
                    await self.click(msg, battle)
            return
        if 'chooseyournextpokemon' in normalized(text):
            if not labels:
                return
            choice = switch_label(labels)
            if choice is None:
                await self.stop('No unique Hunt Team or numbered Pokemon choice; inspect switch screen.')
                return
            self.active = msg.id
            self.state = 'switching'
            LOG.info('SWITCH message=%s selecting offered Pokemon=%r', msg.id, choice)
            await self.click(msg, choice)
            return
        hp = WILD.search(text)
        is_ball_menu = any(ball_details(x) is not None for x in labels)
        if hp:
            name, current, maximum = hp.groups()
            if normalized(name) != normalized(self.selected_name):
                await self.stop('Battle species differs from the selected encounter. Inspect chat.')
                return
            current, maximum = int(current.replace(',', '')), int(maximum.replace(',', ''))
            calculate_health_percentage(maximum, current)
            self.wild_hp = current, maximum
            if current == 0 and self.mode == 'battle':
                await self.next_hunt()
                return
        if not hp and not is_ball_menu:
            if not labels:
                return  # transient animation/result; progress deadline still applies
            await self.stop('Unsupported battle screen. Inspect SCREEN log: ' + text[:300])
            return
        self.active = msg.id
        if not labels:
            return
        current, maximum = self.wild_hp
        if maximum is None:
            await self.stop('Cannot read wild HP; inspect SCREEN log.')
            return
        if current == 0:
            return
        if re.search(r'\bno usable\b|\bout of\b[^\n]*\bballs\b', text, re.I):
            await self.stop('Battle requires manual attention: ' + text[:250])
            return
        if self.mode == 'battle':
            self.state = 'battle'
            if is_ball_menu:
                back = [x for x in labels if normalized(x) == 'back']
                if len(back) == 1:
                    await self.click(msg, back[0])
                else:
                    await self.stop('Ball menu has no unique Back button; battle mode paused.')
                return
            moves = self.damaging_moves(text, labels)
            if not moves:
                await self.stop('No recognized damaging move; inspect battle manually.')
                return
            # Highest power; ties keep the first move listed in the caption.
            choice = max(moves, key=lambda move: (move[0], -move[1]))
            await self.click(msg, choice[2], kind='attack')
            return
        LOG.info('DECISION target=%s wild_hp=%s/%s percent=%.1f keyboard=%s ball=%s',
                 self.selected_name, current, maximum, 100 * current / maximum,
                 'ball-menu' if is_ball_menu else 'battle', self.selected_ball)
        if (maximum > 50 or self.require_weaken) and current / maximum > 0.5:
            self.state = 'battle'
            if is_ball_menu:
                back = [x for x in labels if normalized(x) == 'back']
                if len(back) == 1:
                    await self.click(msg, back[0])
                else:
                    await self.stop('Need to weaken target, but ball menu has no unique Back button.')
                return
            moves = self.damaging_moves(text, labels)
            if not moves:
                await self.stop('No recognized damaging move' +
                                (f' matching {self.cfg.attack_move!r}' if self.cfg.attack_move else '') +
                                '; inspect SCREEN log.')
                return
            await self.click(msg, min(moves, key=lambda move: (move[0], move[1]))[2], kind='attack')
            return
        self.state = 'catching'
        if is_ball_menu:
            ball = choose_ball(labels, self.selected_ball)
            if ball:
                await self.click(msg, ball, kind='throw')
            else:
                await self.stop('Selected ball is empty, unavailable or ambiguous: ' + self.selected_ball)
        else:
            menu = menu_label(labels)
            if menu:
                await self.click(msg, menu)
            else:
                await self.stop('No recognized Poke Ball menu button; inspect SCREEN log.')

    def damaging_moves(self, text, labels):
        moves = [(int(power), i, label) for i, (move, power) in enumerate(MOVES.findall(text))
                 for label in labels if normalized(move) == normalized(label) and int(power) > 0]
        if self.cfg.attack_move:
            moves = [x for x in moves if normalized(x[2]) == normalized(self.cfg.attack_move)]
        return moves

    async def run(self, generation):
        try:
            await self.send_hunt()
            while self.running and generation == self.generation:
                # Duplicate updates never reset the progress timeout.
                remaining = self.cfg.timeout - (asyncio.get_running_loop().time() - self.last_progress)
                try:
                    if remaining <= 0:
                        raise asyncio.TimeoutError
                    token, msg = await asyncio.wait_for(self.queue.get(), remaining)
                except asyncio.TimeoutError:
                    await self.recover_timeout(generation)
                    continue
                if token == generation:
                    await self.process(msg)
        except asyncio.CancelledError:
            raise
        except Exception:
            LOG.exception('Automation paused')
            await self.stop('Telegram/action error. See terminal log; inspect chat before .resume.')


def api_credentials():
    return (parse_api_id(os.getenv('TELEGRAM_API_ID', '')),
            parse_api_hash(os.getenv('TELEGRAM_API_HASH', '')))


def runtime_config():
    api_id, api_hash = api_credentials()
    username = os.getenv('HEXA_BOT_USERNAME', '@HeXamonbot').strip()
    bot_id = os.getenv('HEXA_BOT_ID', '').strip()
    if bot_id and int(bot_id) <= 0:
        raise ValueError('HEXA_BOT_ID must be a positive user ID')
    if not username and not bot_id:
        raise ValueError('Set HEXA_BOT_USERNAME or HEXA_BOT_ID')
    session = os.getenv('TELEGRAM_SESSION') or os.getenv('TELEGRAM_SESSION_FILE') or str(Path.home() / '.hexa' / 'hexa_userbot')
    session_string = os.getenv('TELEGRAM_SESSION_STRING', '').strip().strip('\"\'')
    return api_id, api_hash, username, int(bot_id) if bot_id else None, session, session_string


def telegram_library():
    library = os.getenv('TELEGRAM_LIBRARY', 'telethon').strip().lower()
    if library not in LIBRARIES:
        raise ValueError('TELEGRAM_LIBRARY must be telethon or pyrogram')
    return library


async def resolve_bot(client, username, bot_id):
    if username:
        entity = await client.get_entity(username)
        if not getattr(entity, 'bot', False):
            raise ValueError('HEXA_BOT_USERNAME does not identify a Telegram bot')
        if bot_id is not None and entity.id != bot_id:
            raise ValueError('HEXA_BOT_USERNAME and HEXA_BOT_ID identify different accounts')
        return entity
    try:
        entity = await client.get_entity(await client.get_input_entity(bot_id))
    except ValueError:
        entity = None
    if entity is not None:
        if not getattr(entity, 'bot', False):
            raise ValueError('HEXA_BOT_ID does not identify a Telegram bot')
        return entity
    async for dialog in client.iter_dialogs():
        if dialog.id == bot_id and getattr(dialog.entity, 'bot', False):
            return dialog.entity
    raise ValueError('Bot is not cached. Set HEXA_BOT_USERNAME to its exact @username or open that bot and press Start in Telegram.')


def register_handlers(client, controller, peer, owner):
    if hasattr(client, 'register_controller_handlers'):
        client.register_controller_handlers(controller, peer, owner)
        return
    from telethon import events

    client.add_event_handler(controller.receive, events.NewMessage(chats=peer, from_users=peer, incoming=True))
    client.add_event_handler(controller.receive, events.MessageEdited(chats=peer, from_users=peer, incoming=True))
    sudo_users = getattr(getattr(controller, 'cfg', None), 'sudo_users', frozenset())
    client.add_event_handler(controller.command, events.NewMessage(
        from_users=sorted({owner, *sudo_users}), outgoing=None if sudo_users else True,
        forwards=False, pattern=COMMAND_PATTERN))


class HealthServer:
    """Small public status endpoint; userbot control remains in Telegram."""

    def __init__(self):
        self.ready = False
        self.closing = False
        self.server = None
        self.handlers = set()

    async def start(self, port, host='0.0.0.0'):
        self.closing = False
        self.server = await asyncio.start_server(self.handle, host, port, limit=8192)

    async def close(self):
        self.ready = False
        self.closing = True
        if self.server is not None:
            self.server.close()
        tasks = list(self.handlers)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self.server is not None:
            await self.server.wait_closed()

    async def handle(self, reader, writer):
        task = asyncio.current_task()
        self.handlers.add(task)
        status, body, content_type = '400 Bad Request', b'Bad request\n', 'text/plain'
        head = False
        try:
            if self.closing:
                return
            try:
                request = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 5)
                line = request.split(b'\r\n', 1)[0].decode('ascii')
                method, target, version = line.split(' ')
                head = method == 'HEAD'
                if version not in {'HTTP/1.0', 'HTTP/1.1'}:
                    raise ValueError('Unsupported HTTP version')
                if method not in {'GET', 'HEAD'}:
                    status, body = '405 Method Not Allowed', b'Use GET or HEAD\n'
                elif target.split('?', 1)[0] not in {'/', '/healthz'}:
                    status, body = '404 Not Found', b'Not found\n'
                else:
                    status = '200 OK' if self.ready else '503 Service Unavailable'
                    content_type = 'application/json'
                    body = json.dumps({
                        'service': 'hexa-userbot',
                        'status': 'ready' if self.ready else 'starting',
                        'controls': 'Send .help as the account owner or a configured sudo user.',
                    }).encode() + b'\n'
            except (ValueError, UnicodeDecodeError, asyncio.IncompleteReadError,
                    asyncio.LimitOverrunError, asyncio.TimeoutError):
                pass
            response = (
                f'HTTP/1.1 {status}\r\nContent-Type: {content_type}; charset=utf-8\r\n'
                f'Content-Length: {len(body)}\r\nCache-Control: no-store\r\n'
                'Connection: close\r\n\r\n'
            ).encode() + (b'' if head else body)
            writer.write(response)
            await asyncio.wait_for(writer.drain(), 5)
        except (ConnectionError, asyncio.TimeoutError):
            pass
        finally:
            self.handlers.discard(task)
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()


def web_port():
    try:
        port = int(os.getenv('PORT', '10000'))
    except ValueError:
        raise ValueError('PORT must be an integer between 1 and 65535') from None
    if not 1 <= port <= 65535:
        raise ValueError('PORT must be an integer between 1 and 65535')
    return port


async def main(autostart=False, mode=None, non_interactive=False, http_port=None):
    settings = Settings.load()
    if mode is not None:
        settings.mode = mode
    api_id, api_hash, username, bot_id, session_path, session_string = runtime_config()
    library = telegram_library()
    if non_interactive and not session_string:
        raise ValueError('TELEGRAM_SESSION_STRING is required for hosting. '
                         'Run python generate_session.py in your own terminal first.')
    try:
        if library == 'telethon':
            from telethon import TelegramClient
            from telethon.errors import RPCError
            from telethon.sessions import StringSession
        else:
            from pyrogram_transport import PyrogramTransport
            from pyrogram.errors import RPCError
    except ImportError as exc:
        raise RuntimeError('Install dependencies: python -m pip install -r requirements.txt') from exc
    if library == 'pyrogram' and not (os.getenv('TELEGRAM_SESSION') or os.getenv('TELEGRAM_SESSION_FILE')):
        session_path = str(Path.home() / '.hexa' / 'hexa_pyrogram')
    session_path = Path(session_path).expanduser().resolve()
    session_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # All spellings of the same SQLite session use the same guard.
    canonical = str(session_path)
    if canonical.endswith('.session'):
        canonical = canonical[:-8]
    if session_string:
        fingerprint = hashlib.sha256(session_string.encode()).hexdigest()[:24]
        lock_dir = os.getenv('HEXA_LOCK_DIR', '').strip()
        guard_dir = Path(lock_dir).expanduser().resolve() if lock_dir else Path.home() / '.hexa'
        guard_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        prefix = 'string-' if library == 'telethon' else 'pyrogram-'
        guard_path = guard_dir / (prefix + fingerprint + '.lock')
    else:
        guard_path = Path(canonical + '.lock')
    import fcntl
    with open(guard_path, 'a') as guard:
        try:
            fcntl.flock(guard.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('Another copy is using this session. Stop that copy first.') from exc
        try:
            if library == 'telethon':
                session = StringSession(session_string) if session_string else str(session_path)
                client = TelegramClient(session, api_id, api_hash, flood_sleep_threshold=0)
            else:
                client = PyrogramTransport(api_id, api_hash, str(session_path), session_string)
        except (ValueError, TypeError):
            raise ValueError(f'Invalid {library} session. TELEGRAM_SESSION_STRING must match '
                             'TELEGRAM_LIBRARY; generate a matching session locally.') from None
        controller = None
        health = HealthServer() if http_port is not None else None
        loop = asyncio.get_running_loop()
        main_task = asyncio.current_task()
        shutdown_requested = False
        signal_registered = False

        def request_shutdown():
            nonlocal shutdown_requested
            if not shutdown_requested:
                shutdown_requested = True
                main_task.cancel()

        try:
            try:
                loop.add_signal_handler(signal.SIGTERM, request_shutdown)
                signal_registered = True
            except (NotImplementedError, RuntimeError):
                pass  # Platforms without loop signal support keep the normal exit behavior.
            if health is not None:
                await health.start(http_port)
            if non_interactive:
                await client.connect()
                if not await client.is_user_authorized():
                    raise RuntimeError('Telegram session is no longer authorized. '
                                       'Generate a new TELEGRAM_SESSION_STRING locally.')
            else:
                await client.start()
            profile = await client.get_me()
            owner = profile.id
            LOG.info('Telegram account connected: id=%s username=%s', owner, getattr(profile, 'username', None) or '(none)')
            peer = await resolve_bot(client, username, bot_id)
            alert_resolution_error = None
            try:
                alert_peer = await resolve_alert_chat(client, settings.notify)
            except (ValueError, RPCError) as exc:
                LOG.warning('Could not resolve the configured alert chat; using Saved Messages.')
                alert_peer = owner
                alert_resolution_error = type(exc).__name__
            controller = Controller(client, peer, owner, settings, owner_profile=profile)
            controller.alert_peer = alert_peer
            if alert_resolution_error is not None:
                controller.alert_state = 'fallback'
                controller.alert_error = alert_resolution_error
            # Concurrent update dispatch keeps .stop responsive during delays.
            register_handlers(client, controller, peer, owner)
            if health is not None:
                health.ready = True
            print(f'Ready: selected mode={controller.mode}; library={library}; {len(settings.targets)} catch targets.\n'
                  'Send .battle, .caught, or .help as the owner or a configured sudo user.\n'
                  'Finish any existing encounter and pending verification manually before starting.')
            if autostart:
                await controller.start()
            await client.run_until_disconnected()
        except asyncio.CancelledError:
            if not shutdown_requested:
                raise
        finally:
            if health is not None:
                health.ready = False
            try:
                if controller:
                    await controller.stop(notify=False)
            finally:
                try:
                    if health is not None:
                        await health.close()
                finally:
                    try:
                        await client.disconnect()
                    finally:
                        if signal_registered:
                            loop.remove_signal_handler(signal.SIGTERM)


def cli():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--check', action='store_true', help='Validate settings without connecting')
    parser.add_argument('--self-test', action='store_true', help='Run offline mocked tests')
    parser.add_argument('--autostart', action='store_true', help='Start a fresh hunt immediately')
    parser.add_argument('--mode', choices=sorted(MODES), help='Initial mode (otherwise HEXA_MODE, default battle)')
    parser.add_argument('--non-interactive', action='store_true',
                        help='Hosting: require a StringSession and never prompt for login')
    parser.add_argument('--web', action='store_true',
                        help='Serve public status on 0.0.0.0:$PORT for Render Free Web')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    if args.self_test:
        import unittest
        tests_dir = Path(__file__).resolve().parent / 'tests'
        if not tests_dir.is_dir():
            parser.exit(2, 'Error: tests/ is missing; extract the complete project to run --self-test.\n')
        logging.disable(logging.CRITICAL)
        suite = unittest.defaultTestLoader.discover(str(tests_dir))
        return 0 if unittest.TextTestRunner(verbosity=1).run(suite).wasSuccessful() else 1
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parent / '.env', override=False)
        http_port = web_port() if args.web else None
        if args.check:
            settings = Settings.load()
            config = runtime_config()
            library = telegram_library()
            if args.non_interactive and not config[5]:
                raise ValueError('TELEGRAM_SESSION_STRING is required for non-interactive hosting')
            print(f'Configuration valid: mode={args.mode or settings.mode}; library={library}; '
                  f'{len(settings.targets)} catch targets. No Telegram connection made.')
        else:
            asyncio.run(main(args.autostart, args.mode, args.non_interactive, http_port))
    except ImportError:
        parser.exit(2, 'Error: install dependencies with python -m pip install -r requirements.txt\n')
    except KeyboardInterrupt:
        pass
    except (ValueError, RuntimeError, OSError) as exc:
        parser.exit(2, f'Error: {exc}\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(cli())


