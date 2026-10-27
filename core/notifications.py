"""Outbound push notifications through the Expo push service.

The project has no task queue, so a delivery is a single synchronous POST made after the API
has already written its data. `EXPO_ACCESS_TOKEN` is optional: without it the call is logged and
skipped, which keeps local runs and the test suite free of network traffic.
"""

import json
import logging
import urllib.error
import urllib.request

from django.conf import settings
from users.models import User

logger = logging.getLogger(__name__)

# The Expo service rejects a request carrying more than this many messages.
BATCH_SIZE = 100


def push_is_configured() -> bool:
    return bool(getattr(settings, 'EXPO_ACCESS_TOKEN', ''))


def send_push(users, title, body, data=None) -> int:
    """Send the same message to every user that registered a token; returns how many were accepted."""
    return _deliver([
        (user, {'title': title, 'body': body, 'data': data or {}})
        for user in users
    ])


def send_personalized(messages) -> int:
    """Deliver one message per recipient, given as (user, title, body, data) tuples."""
    return _deliver([
        (user, {'title': title, 'body': body, 'data': data or {}})
        for user, title, body, data in messages
    ])


def _deliver(pending):
    """Push in service-sized batches so a whole class needs one request, not one per student."""
    pending = [entry for entry in pending if entry[0].push_token]
    if not pending:
        return 0

    if not push_is_configured():
        logger.info(
            'push skipped, EXPO_ACCESS_TOKEN unset: %s to %d recipient(s)',
            pending[0][1]['title'],
            len(pending),
        )
        return 0

    accepted = 0
    for start in range(0, len(pending), BATCH_SIZE):
        batch = pending[start:start + BATCH_SIZE]
        payload = [{'to': user.push_token, **fields} for user, fields in batch]
        try:
            tickets = _post(payload)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            logger.warning('push delivery failed for %d recipient(s): %s', len(batch), exc)
            continue
        accepted += _settle(batch, tickets)
    return accepted


def _post(payload):
    request = urllib.request.Request(
        settings.EXPO_PUSH_URL,
        data=json.dumps(payload).encode('utf-8'),
        headers={
            'Authorization': f'Bearer {settings.EXPO_ACCESS_TOKEN}',
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        },
        method='POST',
    )
    with urllib.request.urlopen(request, timeout=settings.EXPO_PUSH_TIMEOUT) as response:
        body = json.loads(response.read().decode('utf-8'))
    return body.get('data') or []


def _settle(batch, tickets):
    """Count the accepted tickets and forget the tokens Expo says are gone."""
    accepted = 0
    stale = []
    for (user, _fields), ticket in zip(batch, tickets):
        if ticket.get('status') == 'ok':
            accepted += 1
            continue
        error = (ticket.get('details') or {}).get('error') or ticket.get('message')
        logger.warning('push rejected for %s: %s', user.email, error)
        if error == 'DeviceNotRegistered':
            stale.append(user.pk)
    if stale:
        User.objects.filter(pk__in=stale).update(push_token='')
    return accepted
