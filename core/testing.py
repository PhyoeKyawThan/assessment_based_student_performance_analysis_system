"""Helpers shared by the test suites: user factory and an authenticated client."""

import json
from contextlib import contextmanager
from unittest.mock import patch
from uuid import uuid4

from rest_framework.test import APIClient

from users.models import User

PASSWORD = 'Str0ngPass-2026!'


def make_user(role=User.Role.STUDENT, password=PASSWORD, **extra):
    handle = uuid4().hex[:10]
    user = User(
        **{
            'username': f'user.{handle}',
            'email': f'{handle}@university.edu',
            'full_name': f'User {handle}',
            'role': role,
            **extra,
        }
    )
    user.set_password(password)
    user.save()
    return user


def api_client(user=None, password=PASSWORD):
    """A client that logs in over POST /api/token/ and sends the Bearer header."""
    client = APIClient()
    if user is not None:
        response = client.post(
            '/api/token/', {'email': user.email, 'password': password}, format='json'
        )
        assert response.status_code == 200, response.data
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {response.data["access"]}')
    return client


class _FakeExpoResponse:
    def __init__(self, tickets):
        self._body = json.dumps({'data': tickets}).encode('utf-8')

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


@contextmanager
def mock_push_transport(ticket_factory=None):
    """Stand in for exp.host so a push test never opens a socket.

    Yields the list of message batches the service tried to deliver. `ticket_factory` turns one
    message into the ticket Expo reports back for it; the default accepts everything.
    """
    batches = []

    def fake_urlopen(request, timeout=None):
        messages = json.loads(request.data.decode('utf-8'))
        batches.append(messages)
        factory = ticket_factory or (lambda message: {'status': 'ok', 'id': 'ticket'})
        return _FakeExpoResponse([factory(message) for message in messages])

    with patch('urllib.request.urlopen', side_effect=fake_urlopen):
        yield batches
