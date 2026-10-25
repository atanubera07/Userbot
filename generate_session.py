"""Generate a private Telethon or Pyrogram session locally, without using a website."""
import asyncio
import getpass
import os
from pathlib import Path

from telegram_credentials import normalize_credential, parse_api_hash, parse_api_id


def login_credentials():
    """Use configured credentials or ask locally; no .env file is required."""
    value = os.getenv('TELEGRAM_API_ID', '')
    if not normalize_credential(value):
        value = input('Telegram API ID (from my.telegram.org/apps): ').strip()
    api_id = parse_api_id(value)
    api_hash = os.getenv('TELEGRAM_API_HASH', '')
    if not normalize_credential(api_hash):
        api_hash = getpass.getpass('Telegram API hash (hidden while typing): ').strip()
    return api_id, parse_api_hash(api_hash)


async def generate(destination, library='telethon'):
    if library == 'telethon':
        from telethon import TelegramClient
        from telethon.sessions import StringSession
    elif library == 'pyrogram':
        from pyrogram import Client
    else:
        raise ValueError('Library must be telethon or pyrogram')

    api_id, api_hash = login_credentials()
    # Exclusive creation prevents accidentally replacing an existing session.
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        if library == 'telethon':
            async with TelegramClient(StringSession(), api_id, api_hash) as client:
                value = client.session.save()
        else:
            async with Client('hexa-session-maker', api_id=api_id, api_hash=api_hash, in_memory=True) as client:
                value = await client.export_session_string()
        with os.fdopen(fd, 'w') as output:
            fd = None
            output.write(value + '\n')
    except BaseException:
        if fd is not None:
            os.close(fd)
        destination.unlink(missing_ok=True)
        raise


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', choices=['telethon', 'pyrogram'],
                        help='Session library (default: TELEGRAM_LIBRARY, otherwise telethon)')
    parser.add_argument('--output', type=Path,
                        help='Private output file (default: .session-string or .session-string-pyrogram)')
    args = parser.parse_args()
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parent / '.env', override=False)
        library = args.library or os.getenv('TELEGRAM_LIBRARY', 'telethon').strip().lower()
        if library not in {'telethon', 'pyrogram'}:
            raise ValueError('TELEGRAM_LIBRARY must be telethon or pyrogram')
        name = '.session-string-pyrogram' if library == 'pyrogram' else '.session-string'
        destination = args.output or Path(__file__).resolve().parent / name
        asyncio.run(generate(destination.expanduser().resolve(), library=library))
    except FileExistsError:
        parser.exit(2, 'Error: output already exists; choose a different --output file.\n')
    except ImportError:
        parser.exit(2, 'Error: install dependencies with python -m pip install -r requirements.txt\n')
    except KeyboardInterrupt:
        return 1
    except EOFError:
        parser.exit(2, 'Error: run this script in your own interactive terminal to enter login details.\n')
    except (ValueError, RuntimeError, OSError) as exc:
        parser.exit(2, f'Error: {exc}\n')
    print(f'{library.capitalize()} session saved privately to {destination}.\n'
          'Open the file locally with: nano ' + str(destination) + '\n'
          f'Set TELEGRAM_LIBRARY={library} in your host\'s environment settings.\n'
          'Copy its contents into TELEGRAM_SESSION_STRING in your host\'s secret settings.\n'
          'Keep this file private; do not commit it or paste it into a chat.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
