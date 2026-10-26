"""Shared error body: {"code", "detail", "fields"}.

SimpleJWT's own login, refresh and bearer errors keep their plain `detail` body.
"""

from rest_framework import exceptions, status
from rest_framework.views import exception_handler as drf_exception_handler
from rest_framework_simplejwt.exceptions import TokenError

STATUS_CODES = {
    400: 'validation_error',
    401: 'authentication_failed',
    403: 'permission_denied',
    404: 'not_found',
    409: 'conflict',
}

# Both `UniqueValidator` and `UniqueTogetherValidator` report the breach with this code.
UNIQUE_CODES = {'unique'}


class ConflictError(exceptions.APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = 'The request conflicts with existing data.'
    default_code = 'conflict'

    def __init__(self, detail=None, code=None, fields=None):
        if detail is not None and fields:
            detail = {'detail': detail, **fields}
        super().__init__(detail, code)


def _leaves(value):
    if isinstance(value, dict):
        for nested in value.values():
            yield from _leaves(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            yield from _leaves(nested)
    else:
        yield value


def exception_handler(exc, context):
    response = drf_exception_handler(exc, context)
    if response is None:
        return None

    # A token endpoint failure keeps SimpleJWT's body, as the conventions require.
    if isinstance(exc, (TokenError, exceptions.AuthenticationFailed)):
        return response

    data = response.data
    detail = None
    fields = {}
    if isinstance(data, dict):
        for key, value in data.items():
            leaves = [str(leaf) for leaf in _leaves(value)]
            if key in {'detail', 'non_field_errors', 'code'}:
                if leaves and detail is None:
                    detail = leaves[0]
            else:
                fields[key] = leaves
    else:
        leaves = [str(leaf) for leaf in _leaves(data)]
        detail = leaves[0] if leaves else None

    if isinstance(exc, exceptions.ValidationError):
        codes = {getattr(leaf, 'code', None) for leaf in _leaves(exc.detail)}
        if codes & UNIQUE_CODES:
            response.status_code = status.HTTP_409_CONFLICT

    code = STATUS_CODES.get(response.status_code, 'error')
    if detail is None and fields:
        detail = next(iter(next(iter(fields.values()))))
    body = {'code': code, 'detail': detail or getattr(exc, 'default_detail', 'Request failed.')}
    if fields:
        body['fields'] = fields
    response.data = body
    return response
