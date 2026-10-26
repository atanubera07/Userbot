# HeXamon userbot

A standalone Python userbot for your Telegram account, combining the battle
behavior from `test.py` and the catching behavior from `hexa_v4.py`. It uses the
Telethon 1.x or Pyrogram 2.x and dot commands. It does not require LEGENDBOT,
a database, or an assistant bot token.

**Free Render setup:** use the included `render.yaml` and [Render guide](RENDER.md).
It uses a free web service with a status endpoint and no paid disk. Render sleeps
the service after 15 minutes without inbound traffic; open its URL to wake it,
then start your chosen mode in Telegram. Hunting stops while the service sleeps.
The GitHub connection named `atanu` belongs to account `atanubera07`.

For automatic web requests every 10 minutes, use the optional
[free external monitor](MONITOR.md). Run `setup_render_monitor.py` once locally
with your own cron-job.org API key. It does not activate until you configure it.

[Deploy Userbot to Render Free](https://render.com/deploy?repo=https://github.com/atanubera07/Userbot)

## Install and sign in

Use Python 3.10 or newer on Linux, macOS, or Termux. Keep the process running on
your computer or server while using the commands in Telegram.

```bash
cd hexa-userbot
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` and fill in `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` from
<https://my.telegram.org/apps>. Keep these values and your session private.
The script loads `.env` from its own folder; existing environment variables take
precedence.

API credentials accept matching outer quotes and invisible clipboard characters
at the edges. The ID must still contain only decimal digits, and the hash must
contain exactly 32 hexadecimal characters. Validation errors describe formatting
without displaying your credentials.

```bash
python hexa_userbot.py --check
python hexa_userbot.py
```

On first run, enter your phone number, Telegram login code, and two-step
verification password if prompted **in your own terminal**. Alternatively, put
your existing private session string in `TELEGRAM_SESSION_STRING` in `.env`.
Set `TELEGRAM_LIBRARY=pyrogram` for a Pyrogram string; Telethon is the default.
No session maker website is needed.

Open `@HeXamonbot` and press Start with that account first. Finish any existing
encounter and answer any pending game verification before starting automation.
After the terminal prints `Ready`, send a command from that account in any chat
where you can send messages, including Saved Messages, private chats, and groups.

## Commands

| Command | Action |
| --- | --- |
| `.battle` | Start battling all ordinary wild Pokémon |
| `.catch` or `.caught` | Start catching Pokémon from `TARGETS` |
| `.stop` | Pause automation and cancel pending delayed actions |
| `.status` | Show mode, stage, selected species, limits, and recoveries |
| `.help` | Show commands |
| `.resume` | Start the currently selected mode |

A space after the dot is optional: `. battle` also starts battle mode. The old
`.hexa ...` commands and `/go`, `/stop`, and `/status` are no longer accepted.

The logged-in account always has access. To allow other Telegram accounts to
control this userbot, set `HEXA_SUDO_USERS` to their positive numeric user IDs,
separated by commas, then redeploy. Leave it blank for owner-only access.
Use the same list on multiple deployments to control them from one account:
send a command in a shared group to control every userbot that can see it, or
send it privately to a particular userbot account to control just that account.
Messages in the sudo account's Saved Messages cannot reach other accounts.
Usernames, anonymous group identities, forwarded commands,
and commands sent through inline bots are ignored. Status and help replies appear
in the chat where you sent the command and include the userbot's account ID.
Start/stop and encounter alerts include that ID and use your
configured notification destination.

To change modes, send `.stop`, finish the current encounter manually, then
send `.catch` (or `.caught`) or `.battle`. A mode command while already running keeps
the current worker and reports the active mode. Only one worker runs at a time.
Stopping does not finish an encounter for you. Every start sends a fresh `/hunt`.

The selected mode lasts until you change it or restart the process. After a
process restart, `.resume` uses `HEXA_MODE` from `.env` (default `battle`). By default,
launching the script only connects and waits for a command. To start immediately:

```bash
python hexa_userbot.py --mode catch --autostart
```

## Battle mode

- Battles ordinary wild Pokémon, including species outside the catch list.
- Uses the highest listed positive move power. Ties use the first listed move.
- Continues attacking at low HP and starts another hunt after zero wild HP or a
  confirmed faint/escape result.
- Never opens a ball menu or throws a ball. An unexpected ball menu is backed
  out of when possible.
- Pauses on Safari Catch-only encounters for you to handle manually.

## Catch mode

- Uses your updated 104-name `TARGETS` list near the top of `hexa_userbot.py`. Edit that
  list before launching to change catch targets. Unlisted ordinary species are
  skipped with another `/hunt`.
- Uses the lowest listed positive move power to weaken targets whose maximum
  HP exceeds 50. Opens the ball menu at 50% HP or less.
- For maximum HP of 50 or less, tries catching directly. If the game keeps the
  menu closed and asks for weakening, follows the same 50% threshold.
- Uses the configured `HEXA_BALL`. It pauses if that ball is unavailable or empty,
  rather than automatically upgrading to another ball.
- Recognizes Safari encounters by their explicit ball count and uses the Catch
  button and Safari Balls directly.
- After the final allowed throw resolves unsuccessfully, attempts to leave and
  hunt again. An uncertain outcome pauses instead of blindly replaying a click.
- At zero wild HP, waits for the game's result message before hunting again.

Move power alone does not predict damage. Weakening can still knock out a target.

## Alerts and pauses in both modes

Shiny text, listed legendary encounters, Halloween forms, verification questions,
daily hunt limits, unsupported screens, and action errors pause automation.
The legendary list contains your 106 names, including Hoopa Unbound, and is
kept separate from catch targets.
You handle these encounters and verification questions manually. They do not
resume automatically; send a mode command or `.resume` after resolving the situation.
The userbot never generates or submits verification answers.

If one of your Pokémon faints, the userbot chooses a unique Hunt Team button, or
the lowest numbered slot offered. Ambiguous choices pause. A 45-second progress
timeout checks recent messages for missed results and updated screens. If needed,
it tries to leave the stalled encounter; an unconfirmed escape requires manual
attention. A stopped worker stays stopped during timeout recovery.

Alerts keep your scripts' defaults: group `-1004490697963`, tagging
`@Kill_switch8`. That tag is the notification recipient regardless of which
account signs in. Change `HEXA_NOTIFY_CHAT` and `HEXA_ALERT_TAG` in `.env` to use
another destination or recipient. Set `HEXA_NOTIFY_CHAT=me` for Saved Messages.
Group alerts require your account to belong to the group and be able to send
messages. Resolution or delivery failures fall back to Saved Messages.

## Settings

| Variable | Default / purpose |
| --- | --- |
| `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` | Required: your application credentials |
| `TELEGRAM_LIBRARY` | `telethon` (default) or `pyrogram` |
| `TELEGRAM_SESSION_STRING` | Private session string matching the selected library |
| `TELEGRAM_SESSION_FILE` | Optional local session path; default `~/.hexa/hexa_userbot` |
| `TELEGRAM_SESSION` | Older session-path alias; takes precedence over `TELEGRAM_SESSION_FILE` |
| `HEXA_BOT_USERNAME` | `@HeXamonbot` |
| `HEXA_BOT_ID` | Optional positive numeric bot identity check |
| `HEXA_MODE` | `battle` or `catch`; default `battle` |
| `HEXA_SUDO_USERS` | Optional comma-separated positive numeric user IDs; these accounts can use all controls |
| `HEXA_BALL` | `Regular Ball`, `Great Ball`, `Ultra Ball`, or `Repeat Ball` |
| `HEXA_ATTACK_MOVE` | Optional exact damaging move name; overrides move choice in both modes |
| `HEXA_DELAY` | `5` seconds per click; minimum `3` |
| `HEXA_HUNT_DELAY` | `1` second between hunts; minimum `0.5` |
| `HEXA_MAX_ATTACKS` | `10` per encounter; reaching this limit pauses |
| `HEXA_MAX_THROWS` | `30` per encounter in catch mode |
| `HEXA_TIMEOUT` | `45` seconds without progress |
| `HEXA_NOTIFY_CHAT` | Alert destination: `me`, group username, or signed chat ID |
| `HEXA_ALERT_TAG` | Recipient's `@username` |
| `HEXA_LOCK_DIR` | Optional StringSession guard directory for hosts with a shared disk; unused on Render Free |
| `PORT` | HTTP status port when using `--web`; Render supplies it, otherwise `10000` |

`HEXA_TARGET_MODE=combined` or `legacy` is accepted for compatibility with your
old setup; both use the current `TARGETS` list. Only one copy should use a session;
a local file lock prevents duplicate copies of this userbot from sharing one.
Use a separate session if you also run another userbot.

## Offline checks

```bash
python hexa_userbot.py --self-test
python -m unittest discover -s tests -q
```

The tests mock Telegram messages and clicks, including complete battle and catch
flows, mode switching, limits, cancellation, and manual pauses. They do not log in
to Telegram. `--check` validates configuration without connecting. Actual game
message formatting must match the supported screens; unknown controls pause.

Verified with Python 3.12, Telethon 1.45.0, and Pyrogram 2.0.106:
274 offline tests passed. Tests include real Telegram-library
event filters, local HTTP requests, and mocked hosting login; integration checks are skipped if their
dependencies are missing. No live Telegram account was connected during
verification.

## Deploy from a GitHub repository

Upload the extracted project files to the **root** of your own GitHub repository.
Include hidden configuration files such as `.python-version` and `.dockerignore`.
Keep `.env`, `.session-string`, and all Telegram session files private; they are
excluded by `.gitignore`. There is no need to upload the ZIP itself.

Hosting requires a private StringSession because cloud workers cannot prompt for
your phone number and login code. On your own computer or Termux, install the
requirements and run:

```bash
python generate_session.py
```

This logs in interactively in your local terminal and writes `.session-string`
with private file permissions. Copy that file's contents into the host's secret
environment variable named `TELEGRAM_SESSION_STRING`.

The generator asks for your API ID and API hash if they are missing, so it does
not need a `.env` file. Existing environment variables or a local `.env` still
work. To view dotfiles in Termux, use `ls -a` instead of `ls`.

Set these hosting variables:

- `TELEGRAM_API_ID`
- `TELEGRAM_API_HASH`
- `TELEGRAM_SESSION_STRING`
- Optional `HEXA_MODE`, `HEXA_NOTIFY_CHAT`, `HEXA_ALERT_TAG`, and other settings

### Heroku

The included `Procfile`, `app.json`, and `.python-version` describe a Python
worker deployment. Connect your GitHub repository to a Heroku app, add the
configuration variables above, deploy the branch, and enable **one worker dyno**.
Choose a worker plan that stays running. No web dyno or database is required.

The worker command is:

```bash
python hexa_userbot.py --non-interactive
```

If you use Heroku's deploy-template flow, point it at your own repository:
`https://heroku.com/deploy?template=https://github.com/atanubera07/Userbot`.
This link can also be used
as the target of a Deploy to Heroku button in your repository's README.

### Railway, Render, or a VPS

For Render, follow [RENDER.md](RENDER.md). The default Blueprint uses **Free Web**,
without a disk or database. It starts with `--web` to serve `/` and `/healthz` on
Render's port. These public routes show readiness only; controls stay in Telegram.
The service sleeps when idle, and Telegram messages cannot wake it.

On Railway, deploy the repository using its Dockerfile and add the secret
environment variables. If configuring Render manually, create a **Web Service**
using Python and the **Free** compute plan, install with
`pip install -r requirements.txt`, and start with
`python -u hexa_userbot.py --non-interactive --web`. Use `/healthz` for the health
check, leave the disk empty, and keep automatic deploys and previews disabled.

On a VPS, run the same command under your preferred process manager, or build the
Docker image and pass your private configuration at runtime:

```bash
docker build -t hexa-userbot .
docker run -d --name hexa-userbot --restart unless-stopped --env-file .env hexa-userbot
```

Keep `TELEGRAM_SESSION_STRING` in that local `.env`. Never bake secrets into the
Docker image. The included image starts the worker without interactive prompts.

After deployment, check the logs for `Ready`, then send `.battle` or
`.catch` in Saved Messages. A restart connects and waits for a command;
automatic hunting on restart is disabled. Keep one replica and stop the previous
deployment before enabling another one with the same session. The session lock
only protects copies sharing the same local filesystem.

### Use a Pyrogram session

Set `TELEGRAM_LIBRARY=pyrogram` and put your **Pyrogram session string** in
`TELEGRAM_SESSION_STRING`. Keep your API ID and hash in the same Render
environment settings. Choose **Save, rebuild, and deploy** so the new dependency
and adapter are included. Both battle and catch commands work with either library.

To generate a Pyrogram session locally, run:

```bash
python generate_session.py --library pyrogram
```

This asks for missing API credentials locally, then saves
`.session-string-pyrogram`. A `.env` file is optional. Keep that file private.
Telethon remains the default (`TELEGRAM_LIBRARY=telethon`), with its session
generator saving `.session-string`. Session strings belong to their respective
library; selecting a library does not convert a string from another library.
Use a separate local session-file path for each library if you configure one
manually. Pyrogram uses its own default local session path.

### Netlify and Vercel

This userbot keeps a Telegram connection open and retains encounter state in
memory. It needs a continuously running worker. Netlify Functions and Vercel
Functions have execution limits and can stop their processes, so neither is a
reliable direct host for this implementation. Background functions and cron
invocations do not provide a continuously running Telegram listener.

See [Netlify function limits](https://docs.netlify.com/build/functions/configuration/)
and [Vercel function limits](https://vercel.com/docs/functions/limitations).

Pyrogram update gaps are recovered through Telegram GetDifference, including
missed owner commands and bot messages. Expired backlogs reset to fresh updates;
resend commands after a restart. Account identity and accepted command actions
appear in private service logs, without API credentials or session strings.

Pyrogram uses securely randomized message IDs so simultaneous alerts from
separate deployments do not share its default timestamp-based IDs. If Telegram
explicitly rejects an ID collision, the adapter retries that message once with
a fresh ID. Other delivery errors still use the Saved Messages fallback;
permission failures and uncertain network outcomes are not retried by the adapter.

If the alert group cannot be resolved at startup, later alerts retry resolution
at most once a minute, so group delivery can recover without restarting. Saved
Messages fallback notices include the event time in UTC, configured destination,
and error class. `.status` shows the current alert route and last error, and a
successful alert clears that error. This distinguishes old fallback notices
from new delivery failures.
