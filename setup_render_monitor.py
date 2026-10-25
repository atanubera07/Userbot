"""Create an external cron-job.org HTTP schedule for a Render health endpoint."""
import argparse
import getpass
from http.client import HTTPException
import json
import os
import re
import sys
import warnings
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


API_URL = 'https://api.cron-job.org'
DEFAULT_URL = 'https://hexa-userbot.onrender.com/healthz'
JOB_TITLE = 'HeXa Render monitor'


class SetupError(Exception):
    """A configuration/API error whose message is safe to show locally."""


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


def make_job(url=DEFAULT_URL, interval=15):
    if interval not in (10, 15):
        raise SetupError('The interval must be 10 or 15 minutes.')
    try:
        target = urlsplit(url)
        valid_url = (target.scheme == 'https' and target.hostname
                     and not target.username and not target.password
                     and not target.fragment)
    except ValueError:
        valid_url = False
    if not valid_url:
        raise SetupError('Use an HTTPS health URL without credentials or a fragment.')
    return {
        'title': JOB_TITLE, 'url': url, 'enabled': True,
        'saveResponses': False, 'requestTimeout': 30, 'requestMethod': 0,
        'schedule': {
            'timezone': 'UTC', 'expiresAt': 0,
            'hours': [-1], 'mdays': [-1], 'minutes': list(range(0, 60, interval)),
            'months': [-1], 'wdays': [-1],
        },
    }


def api_request(api_key, method, path, payload=None):
    """Call only the fixed API origin; never disclose response bodies or keys."""
    if method not in {'GET', 'PUT', 'PATCH'} or not re.fullmatch(r'/jobs(?:/[1-9][0-9]*)?', path):
        raise SetupError('Invalid cron-job.org API operation.')
    body = None if payload is None else json.dumps(payload).encode('utf-8')
    try:
        request = Request(API_URL + path, data=body, method=method,
                          headers={'Authorization': 'Bearer ' + api_key,
                                   'Content-Type': 'application/json', 'Accept': 'application/json'})
        # Do not forward the bearer header through a redirect, even to another
        # HTTPS origin. The documented API endpoint does not require redirects.
        with build_opener(_NoRedirects()).open(request, timeout=30) as response:
            content = response.read(2_000_001)
        if len(content) > 2_000_000:
            raise SetupError('Cron-job.org returned an oversized response.')
        result = json.loads(content)
    except HTTPError as error:
        raise SetupError(f'Cron-job.org API returned HTTP {error.code}. '
                         'Check the API key, account limits, and service status.') from None
    except (URLError, TimeoutError, OSError, HTTPException):
        raise SetupError('Could not complete the cron-job.org API request. '
                         'Check connectivity and service status.') from None
    except (ValueError, UnicodeError):
        raise SetupError('Cron-job.org returned an unreadable response or rejected the request format.') from None
    if not isinstance(result, dict):
        raise SetupError('Cron-job.org returned an unexpected response format.')
    return result


def _job_id(value):
    if type(value) is not int or value <= 0:
        raise SetupError('Cron-job.org did not return a valid job ID.')
    return value


def ensure_job(job, api_key, request=None):
    """Update one exact match or create one job, then verify saved settings."""
    request = request or api_request
    listing = request(api_key, 'GET', '/jobs')
    jobs = listing.get('jobs')
    if listing.get('someFailed'):
        raise SetupError('Cron-job.org could not list every job. No jobs were changed; retry later.')
    if not isinstance(jobs, list) or any(not isinstance(item, dict) for item in jobs):
        raise SetupError('Cron-job.org returned an incomplete job list. No jobs were changed.')
    matches = [item for item in jobs if item.get('title') == job['title'] and item.get('url') == job['url']]
    if len(matches) > 1:
        raise SetupError('Multiple jobs match this title and URL. Remove the duplicates in '
                         'cron-job.org before retrying; no jobs were changed.')
    job_id = _job_id(matches[0].get('jobId')) if matches else None
    try:
        if job_id is None:
            saved = request(api_key, 'PUT', '/jobs', {'job': job})
            job_id = _job_id(saved.get('jobId'))
        else:
            request(api_key, 'PATCH', f'/jobs/{job_id}', {'job': job})
        details = request(api_key, 'GET', f'/jobs/{job_id}').get('jobDetails')
        if (not isinstance(details, dict) or details.get('enabled') is not True
                or details.get('url') != job['url']
                or type(details.get('requestMethod')) is not int or details.get('requestMethod') != 0
                or not isinstance(details.get('schedule'), dict)
                or any(details['schedule'].get(key) != value for key, value in job['schedule'].items())):
            raise SetupError('The saved job settings could not be verified.')
    except SetupError as error:
        raise SetupError(str(error) + ' The save result may be uncertain. Run this helper again; '
                         'it checks existing jobs before saving. Do not repeat manual create requests.') from None
    return job_id


def _api_key():
    value = os.getenv('CRON_JOB_API_KEY', '').strip()
    if not value:
        if not sys.stdin.isatty():
            raise SetupError('Set CRON_JOB_API_KEY privately, or run locally to enter the API key at a hidden prompt.')
        try:
            with warnings.catch_warnings():
                # getpass normally falls back to echoed input if the terminal
                # cannot hide typing. Refuse that fallback for this API key.
                warnings.simplefilter('error', getpass.GetPassWarning)
                value = getpass.getpass('Cron-job.org API key (hidden): ').strip()
        except getpass.GetPassWarning:
            raise SetupError('This terminal cannot hide input. Set CRON_JOB_API_KEY privately instead.') from None
        except EOFError:
            raise SetupError('Run locally to enter the API key, or set CRON_JOB_API_KEY privately.') from None
    if not value:
        raise SetupError('A cron-job.org API key is required. Create one in your account settings.')
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default=DEFAULT_URL, help='HTTPS health endpoint')
    parser.add_argument('--interval', type=int, choices=(10, 15), default=15,
                        help='Request interval in minutes (default: 15; 10 gives an idle-time margin)')
    parser.add_argument('--dry-run', action='store_true', help='Print the job JSON without credentials or network access')
    args = parser.parse_args(argv)
    try:
        job = make_job(args.url, args.interval)
        if args.dry_run:
            print(json.dumps({'job': job}, indent=2))
            return 0
        job_id = ensure_job(job, _api_key())
    except SetupError as error:
        print(f'Error: {error}', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    print(f'Job {job_id}: every {args.interval} minutes, GET {job["url"]}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
