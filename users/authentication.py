from django.conf import settings
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.settings import api_settings

ACCESS_COOKIE = 'access_token'
REFRESH_COOKIE = 'refresh_token'


class CookieJWTAuthentication(JWTAuthentication):
    """Read the access token from a cookie, but only when no Bearer header is sent."""

    def authenticate(self, request):
        if self.get_header(request) is not None:
            return super().authenticate(request)

        raw_token = request.COOKIES.get(ACCESS_COOKIE)
        if raw_token is None:
            return None

        validated_token = self.get_validated_token(raw_token)
        return self.get_user(validated_token), validated_token


def is_web_client(request) -> bool:
    return request.headers.get('Client-Type', 'mobile').lower() == 'web'


def cookie_kwargs(lifetime) -> dict:
    return {
        'max_age': int(lifetime.total_seconds()),
        'httponly': True,
        'secure': not settings.DEBUG,
        'samesite': 'Lax',
    }


def set_token_cookies(response, access: str, refresh: str) -> None:
    response.set_cookie(ACCESS_COOKIE, access, **cookie_kwargs(api_settings.ACCESS_TOKEN_LIFETIME))
    response.set_cookie(REFRESH_COOKIE, refresh, **cookie_kwargs(api_settings.REFRESH_TOKEN_LIFETIME))


def clear_token_cookies(response) -> None:
    for name in (ACCESS_COOKIE, REFRESH_COOKIE):
        response.delete_cookie(name, samesite='Lax')
