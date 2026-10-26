# Automatic web requests for Render

This optional setup uses the free [cron-job.org](https://cron-job.org/en/) service
to request `https://hexa-userbot.onrender.com/healthz` every **10 minutes**.
Requests come from its servers, so your phone and the userbot do not need to run
the scheduler. No Telegram credentials are sent to cron-job.org.

## Activate once

1. Create a free cron-job.org account and verify your email.
2. Open its [Console](https://console.cron-job.org), then **Settings**, and generate
   an API key. Keep this separate from your Telegram API ID, hash, and session.
3. Download `setup_render_monitor.py` from this repository and run it locally:

   ```bash
   python setup_render_monitor.py
   ```

4. Enter the cron-job.org API key at the hidden prompt. The script creates or
   updates a job named **HeXa Render monitor**, then verifies its saved settings.
   The key is not saved in a file or sent to your Render service.
5. Open **Cronjobs** in the Console. Confirm the job is enabled and use its test
   run, then check the execution history after the next scheduled request.

The script uses only Python's standard library; it runs separately from the bot
and does not need `requirements.txt`. Run it once: you can close Termux afterward.
An existing `CRON_JOB_API_KEY` environment variable can replace the hidden prompt.

Creating or downloading this script does **not** activate a scheduler. Activation
requires running it with your cron-job.org API key or configuring the job in the
Console. Do not paste the key into chat or commit it to GitHub.

## Inspect or change the interval

Preview the exact request configuration without an API key or network access:

```bash
python setup_render_monitor.py --dry-run
```

The default schedule runs at minutes **00, 10, 20, 30, 40, and 50** of every hour,
in UTC. This gives requests a margin before Render's 15-minute idle threshold.
An exact 15-minute interval is also available, but scheduling delays can allow
Render to sleep between requests:

```bash
python setup_render_monitor.py --interval 15
```

Re-running the script updates its existing matching job. It leaves unrelated jobs
alone. If it finds multiple matching jobs or an incomplete job list, it stops so
you can resolve the duplicate or service error in the Console.

For a different Render service, pass its public HTTPS health URL with `--url`.
That creates a separate matching job; it does not disable the old URL's job.

## Manual setup without the script

In cron-job.org, create a cronjob with these settings:

| Setting | Value |
| --- | --- |
| Title | `HeXa Render monitor` |
| URL | `https://hexa-userbot.onrender.com/healthz` |
| Enabled | Yes |
| Method | GET |
| Schedule | Every 10 minutes, every day |
| Request timeout | 30 seconds |
| Save responses | Off |

This setup adds web requests only. It does not send Telegram messages or restart
hunting. After an actual Render restart, finish any pending game question or
encounter and send `.hexa battle` or `.hexa catch` again.

## Limits and stopping

cron-job.org is free. Render still applies its workspace's 750 monthly free
instance hours and other usage limits; keeping the service awake consumes those
hours. Neither provider guarantees uninterrupted operation.

A cold Render instance can take about a minute to wake, longer than cron-job.org's
normal 30-second timeout. The first request may fail while still waking the app.
Check subsequent runs. Repeated failures can automatically disable a cron-job.org
job; resolve its cause before re-enabling it.

To stop automatic requests, disable or delete **HeXa Render monitor** in the
cron-job.org Console. The setup script does not need to remain running.

References: [cron-job.org API](https://docs.cron-job.org/rest-api.html),
[cron-job.org FAQ](https://cron-job.org/en/faq/), and
[Render Free limits](https://render.com/docs/free).
