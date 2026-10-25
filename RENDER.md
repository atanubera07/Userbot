# Deploy on Render Free Web

This setup uses **one Free Web Service**, with no paid worker, disk, or database.
It is for on-demand use: Render sleeps the service after 15 minutes without
inbound web traffic. Open its website URL to wake it, wait for the Telegram
connection to be ready, then send a mode command in Telegram.

**Telegram messages do not wake a sleeping Render service.** Hunting pauses when
the service sleeps or restarts and does not resume automatically.

## 1. Open the GitHub repository

Your GitHub connection named `atanu` belongs to account **atanubera07**. Use the
private repository **[atanubera07/Userbot](https://github.com/atanubera07/Userbot)**.
The project files belong at its root, including `render.yaml`, `.python-version`,
`requirements.txt`, `hexa_userbot.py`, and `generate_session.py`.

A private repository works when your Render account can access it. Keep `.env`,
`.session-string`, and all `*.session` files out of GitHub.

## 2. Generate your private Telegram session

On your own computer or Termux, install the requirements and run:

```bash
python -m pip install -r requirements.txt
python generate_session.py
```

The generator asks for your API ID and API hash from
[my.telegram.org/apps](https://my.telegram.org/apps) if they are not configured.
It does not need a `.env` file. Enter your Telegram phone number, login code, and
two-step password locally when asked. The script saves the result in
`.session-string`. You will copy its
contents directly into Render's secret settings. Do not upload that file to
GitHub or paste its contents into a chat.

## 3. Create the free service

[Deploy Userbot to Render Free](https://render.com/deploy?repo=https://github.com/atanubera07/Userbot)

1. Sign in to [Render](https://dashboard.render.com), choose **New → Blueprint**,
   connect GitHub, and select `atanubera07/Userbot`.
2. Select the branch containing `render.yaml`, usually `main`. Use Blueprint path
   `render.yaml` and leave the root directory empty.
3. Confirm the service is a **Web Service** on the **Free** compute plan. The
   Blueprint must not create a Background Worker, persistent disk, or database.
4. Enter these values when Render prompts for secrets:

   | Key | Value |
   | --- | --- |
   | `TELEGRAM_API_ID` | Your API application ID |
   | `TELEGRAM_API_HASH` | Your API application hash |
   | `TELEGRAM_SESSION_STRING` | Contents of your local `.session-string` file |

5. Review the plan and usage settings, then create the Blueprint. Check the logs
   for the Telegram **Ready** message.

If creating a Web Service manually, use these settings:

| Setting | Value |
| --- | --- |
| Runtime | Python 3 |
| Compute plan | Free |
| Build command | `pip install -r requirements.txt` |
| Start command | `python -u hexa_userbot.py --non-interactive --web` |
| Health-check path | `/healthz` |
| Instances | One |
| Automatic deploys / previews | Disabled |
| Disk / database | None |

Include `.python-version` to select Python 3.12. Add the three secrets above and
the optional alert settings from `.env.example`. Render supplies `PORT`; the
HTTP listener binds to `0.0.0.0` on that port, defaulting to `10000` locally.

The service's public root URL shows basic information, and `/healthz` is its
HTTP health endpoint. These endpoints do not control the userbot. Keep using the
owner-only Telegram commands.

## 4. Wake it and start a mode

Open your service's `https://…onrender.com` URL. Waking a sleeping service usually
takes about one minute. Wait for its Telegram connection to be ready, finish any
existing encounter or verification manually, then send one of these commands in
Saved Messages or the HeXamon private chat:

```text
.hexa battle
.hexa catch
.hexa status
.hexa stop
```

To change modes, send `.hexa stop`, finish the current encounter manually, then
send the other mode command. Every start begins a fresh `/hunt`.

After 15 minutes without inbound HTTP or WebSocket traffic, Render may put the
service to sleep even while the userbot is communicating with Telegram. Its
outbound Telegram connection does not count as incoming web traffic. Open the
URL again when you want to use it; hunting still requires an explicit command
after a restart. This package does not send artificial keep-alive requests.

## Updates without overlapping Telegram sessions

Free services have no persistent disk. Their local lock cannot protect two
separate Render instances, and ordinary redeploys can briefly run old and new
instances together. For a planned update, use this replacement procedure:

1. Send `.hexa stop` and finish the active encounter manually.
2. **Suspend the old service** in Render. Wait until it has fully stopped.
3. Push the updated project files to GitHub.
4. Create a **new Free Web Service with a different service name** from the
   updated repository. Use the manual settings above and add your Telegram
   secrets only after the old service is stopped.
5. Wait for the replacement's **Ready** log, then start a mode in Telegram. Keep
   the old service suspended; never resume it while the replacement is in use.

Do not try to deploy while the old service is suspended: Render can reject that
deploy. Creating a separate replacement avoids relying on this sequence. You
can delete the suspended old service after verifying the replacement.

Render can also restart or replace Free instances during maintenance. The
replacement procedure avoids overlap during your planned updates, but this free
setup cannot guarantee uninterrupted operation or a single Telegram connection
through every provider maintenance event. Do not run another copy elsewhere
with the same StringSession.

## Free limits and troubleshooting

- Free instance hours are shared across your workspace: currently **750 hours
  per calendar month**. Render suspends Free web services after this allowance
  is exhausted.
- Build minutes and outbound bandwidth also have included limits. Extra usage
  can be billed if you have a payment method and your spending settings allow
  it. Without a payment method, exceeding an allowance can suspend services or
  disable further builds. Check Render's usage and billing settings.
- **Sleeping or no response in Telegram:** open the service's website URL, wait
  for readiness, and send a mode command again.
- **Missing or expired session:** generate one locally, update Render's secret,
  and follow the replacement procedure above.
- **Alert group unavailable:** alerts fall back to Saved Messages. Set
  `HEXA_NOTIFY_CHAT=me` if you prefer alerts there.
- **No Ready log:** check the build/start logs and confirm the three required
  secrets are present. Do not publish those logs if they contain login secrets.

Official references: [Free-service limits](https://render.com/docs/free),
[Web Services](https://render.com/docs/web-services),
[deployment lifecycle](https://render.com/docs/deploys), and
[deploy-hook restrictions](https://render.com/docs/deploy-hooks).
