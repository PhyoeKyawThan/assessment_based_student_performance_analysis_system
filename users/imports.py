from dataclasses import dataclass
from io import BytesIO
from zipfile import BadZipFile

from django.db.models import Q
from django.db.models.functions import Lower
from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from users.models import User
from users.serializers import UserCreateSerializer


HEADERS = ('username', 'email', 'full_name', 'role', 'password')
MAX_FILE_SIZE = 5 * 1024 * 1024
MAX_ROWS = 5000
ALLOWED_ROLES = {User.Role.STUDENT, User.Role.TEACHER}


class UserImportError(ValueError):
    pass


@dataclass
class ImportRow:
    row_number: int
    data: dict
    status: str = 'READY'
    message: str = ''

    def public(self):
        return {
            'row_number': self.row_number,
            'username': self.data.get('username', ''),
            'email': self.data.get('email', ''),
            'full_name': self.data.get('full_name', ''),
            'role': self.data.get('role', ''),
            'status': self.status,
            'message': self.message,
        }


def _clean(value):
    return '' if value is None else str(value).strip()


def parse_user_workbook(upload):
    if not upload.name.lower().endswith('.xlsx'):
        raise UserImportError('Choose an .xlsx workbook.')
    if upload.size > MAX_FILE_SIZE:
        raise UserImportError('The workbook must be 5 MB or smaller.')
    try:
        workbook = load_workbook(
            BytesIO(upload.read()), read_only=True, data_only=True
        )
    except (InvalidFileException, BadZipFile, OSError, ValueError):
        raise UserImportError('The uploaded file is not a readable XLSX workbook.')

    sheet = workbook.active
    iterator = sheet.iter_rows(values_only=True)
    first = next(iterator, None)
    if first is None:
        raise UserImportError('The workbook is empty.')
    actual_headers = tuple(_clean(value).lower().replace(' ', '_') for value in first)
    if actual_headers != HEADERS:
        raise UserImportError(
            'The first row must be: username, email, full_name, role, password.'
        )

    rows = []
    for row_number, values in enumerate(iterator, start=2):
        if row_number > MAX_ROWS + 1:
            raise UserImportError(f'A workbook may contain at most {MAX_ROWS} users.')
        data = dict(zip(HEADERS, (_clean(value) for value in values), strict=False))
        if not any(data.values()):
            continue
        data['email'] = data['email'].lower()
        data['role'] = data['role'].upper().replace('LECTURER', 'TEACHER')
        rows.append(ImportRow(row_number=row_number, data=data))
    workbook.close()
    if not rows:
        raise UserImportError('The workbook has no user rows.')
    return rows


def validate_user_rows(rows):
    usernames = [row.data['username'] for row in rows if row.data['username']]
    emails = [row.data['email'] for row in rows if row.data['email']]
    username_keys = [value.casefold() for value in usernames]
    email_keys = [value.casefold() for value in emails]
    existing = User.objects.annotate(
        username_lower=Lower('username'), email_lower=Lower('email')
    ).filter(
        Q(username_lower__in=username_keys) | Q(email_lower__in=email_keys)
    ).values_list('username', 'email')
    existing_usernames = {username.casefold() for username, _email in existing}
    existing_emails = {email.casefold() for _username, email in existing}
    seen_usernames = set()
    seen_emails = set()
    for row in rows:
        data = row.data
        username_key = data['username'].casefold()
        email_key = data['email'].casefold()
        if not data['username'] or not data['email'] or not data['full_name']:
            row.status = 'INVALID'
            row.message = 'Username, email, and full name are required.'
            continue
        if data['role'] not in ALLOWED_ROLES:
            row.status = 'INVALID'
            row.message = 'Role must be STUDENT or TEACHER.'
            continue
        if username_key in existing_usernames or email_key in existing_emails:
            row.status = 'DUPLICATE'
            row.message = 'Username or email already exists.'
            continue
        if username_key in seen_usernames or email_key in seen_emails:
            row.status = 'DUPLICATE'
            row.message = 'Username or email is repeated in this workbook.'
            continue

        serializer = UserCreateSerializer(data=data)
        if not serializer.is_valid():
            row.status = 'INVALID'
            row.message = ' '.join(
                str(message)
                for messages in serializer.errors.values()
                for message in messages
            )
            continue
        row.data = serializer.validated_data
        seen_usernames.add(username_key)
        seen_emails.add(email_key)
    return rows


def import_summary(rows):
    return {
        'total': len(rows),
        'ready': sum(row.status == 'READY' for row in rows),
        'duplicates': sum(row.status == 'DUPLICATE' for row in rows),
        'invalid': sum(row.status == 'INVALID' for row in rows),
    }
