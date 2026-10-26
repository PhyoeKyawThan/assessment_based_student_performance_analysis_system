"""Query parameter parsing for the exact-match filters in the resource contract."""

from uuid import UUID

from rest_framework.exceptions import ValidationError


def uuid_param(name, raw):
    try:
        return UUID(str(raw))
    except ValueError:
        raise ValidationError({name: ['Must be a UUID.']})


def bool_param(name, raw):
    text = str(raw).lower()
    if text in ('true', '1'):
        return True
    if text in ('false', '0'):
        return False
    raise ValidationError({name: ['Must be true or false.']})
