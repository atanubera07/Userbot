"""Generate a private Telethon StringSession locally, without using a website."""
import asyncio
import getpass
import os
import re
from pathlib import Path


def login_credentials():
    """Use configured credentials or ask locally; no .env file is required."""
    value = os.getenv('TELEGRAM_API_ID', '').strip()
    if not value:
        value = input('Telegram API ID (from my.telegram.org/apps): ').strip()
    try:
        api_id = int(value)
    except ValueError:
        raise ValueError('Telegram API ID must be a positive integer') from None
    if api_id <= 0:
        raise ValueError('Telegram API ID must be a positive integer')
    api_hash = os.getenv('TELEGRAM_API_HASH', '').strip()
    if not api_hash:
        api_hash = getpass.getpass('Telegram API hash (hidden while typing): ').strip()
    if not re.fullmatch(r'[a-fA-F0-9]{32}', api_hash):
        raise ValueError('Telegram API hash must contain exactly 32 hexadecimal characters')
    return api_id, api_hash


async def generate(destination):
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    api_id, api_hash = login_credentials()
    # Exclusive creation prevents accidentally replacing an existing session.
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        async with TelegramClient(StringSession(), api_id, api_hash) as client:
            value = client.session.save()
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
    parser.add_argument('--output', type=Path,
                        default=Path(__file__).resolve().parent / '.session-string',
                        help='Private output file (default: .session-string beside this script)')
    args = parser.parse_args()
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parent / '.env', override=False)
        asyncio.run(generate(args.output.expanduser().resolve()))
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
    print(f'Session saved privately to {args.output}.\n'
          'Open the file locally with: nano ' + str(args.output) + '\n'
          'Copy its contents into TELEGRAM_SESSION_STRING in your host\'s secret settings.\n'
          'Keep this file private; do not commit it or paste it into a chat.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
