"""Helpers shared by the test suites: user factory and an authenticated client."""

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
