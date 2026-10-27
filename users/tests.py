from django.test import TestCase

from core.testing import PASSWORD, api_client, make_user
from users.models import User


class LoginTests(TestCase):
    def test_mobile_login_returns_tokens_and_no_cookies(self):
        user = make_user()

        response = api_client().post(
            '/api/token/', {'email': user.email, 'password': PASSWORD}, format='json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn('access', response.data)
        self.assertIn('refresh', response.data)
        self.assertNotIn('access_token', response.cookies)

    def test_web_login_sets_httponly_cookies_and_hides_tokens(self):
        user = make_user()

        response = api_client().post(
            '/api/token/',
            {'email': user.email, 'password': PASSWORD},
            format='json',
            HTTP_CLIENT_TYPE='web',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {'message': 'Login successful (HttpOnly cookies set)'})
        self.assertIn('access_token', response.cookies)
        self.assertIn('refresh_token', response.cookies)
        for name in ('access_token', 'refresh_token'):
            self.assertTrue(response.cookies[name]['httponly'])
            self.assertEqual(response.cookies[name]['samesite'], 'Lax')

    def test_web_login_sets_secure_cookies_when_debug_is_off(self):
        user = make_user()
        password = PASSWORD

        with self.settings(DEBUG=False):
            response = api_client().post(
                '/api/token/',
                {'email': user.email, 'password': password},
                format='json',
                HTTP_CLIENT_TYPE='web',
            )

        self.assertTrue(response.cookies['access_token']['secure'])
        self.assertTrue(response.cookies['refresh_token']['secure'])
        self.assertEqual(response.cookies['access_token']['max-age'], 60 * 60)
        self.assertEqual(response.cookies['refresh_token']['max-age'], 60 * 60 * 24 * 7)

    def test_wrong_password_is_401_with_simplejwt_body(self):
        user = make_user()

        response = api_client().post(
            '/api/token/', {'email': user.email, 'password': 'not-the-password'}, format='json'
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.data, {'detail': 'No active account found with the given credentials'})

    def test_inactive_user_cannot_log_in(self):
        user = make_user(is_active=False)

        response = api_client().post(
            '/api/token/', {'email': user.email, 'password': PASSWORD}, format='json'
        )

        self.assertEqual(response.status_code, 401)


class CookieAuthenticationTests(TestCase):
    def logged_in_web_client(self):
        user = make_user()
        client = api_client()
        response = client.post(
            '/api/token/',
            {'email': user.email, 'password': PASSWORD},
            format='json',
            HTTP_CLIENT_TYPE='web',
        )
        self.assertEqual(response.status_code, 200)
        return user, client

    def test_cookie_authenticates_protected_route(self):
        user, client = self.logged_in_web_client()

        response = client.get('/api/me/')

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['email'], user.email)

    def test_bearer_header_wins_over_cookie(self):
        _user, client = self.logged_in_web_client()

        response = client.get(
            '/api/me/', HTTP_AUTHORIZATION='Bearer not-a-valid-token'
        )

        self.assertEqual(response.status_code, 401)

    def test_anonymous_get_me_is_401(self):
        response = api_client().get('/api/me/')

        self.assertEqual(response.status_code, 401)


class RefreshTests(TestCase):
    def test_mobile_refresh_rotates_pair(self):
        user = make_user()
        login = api_client().post(
            '/api/token/', {'email': user.email, 'password': PASSWORD}, format='json'
        )
        client = api_client()

        response = client.post(
            '/api/token/refresh/', {'refresh': login.data['refresh']}, format='json'
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertIn('access', response.data)
        self.assertNotEqual(response.data['refresh'], login.data['refresh'])

    def test_old_mobile_refresh_is_blacklisted(self):
        user = make_user()
        login = api_client().post(
            '/api/token/', {'email': user.email, 'password': PASSWORD}, format='json'
        )

        api_client().post('/api/token/refresh/', {'refresh': login.data['refresh']}, format='json')
        second = api_client().post(
            '/api/token/refresh/', {'refresh': login.data['refresh']}, format='json'
        )

        self.assertEqual(second.status_code, 401)

    def test_web_refresh_rotates_cookies_without_exposing_tokens(self):
        user = make_user()
        client = api_client()
        login = client.post(
            '/api/token/',
            {'email': user.email, 'password': PASSWORD},
            format='json',
            HTTP_CLIENT_TYPE='web',
        )
        old_refresh = login.cookies['refresh_token'].value

        response = client.post('/api/token/refresh/', {}, HTTP_CLIENT_TYPE='web')

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data, {'message': 'Token refreshed'})
        self.assertNotIn('access', response.data)
        self.assertNotEqual(response.cookies['refresh_token'].value, old_refresh)

        replay = api_client().post(
            '/api/token/refresh/', {'refresh': old_refresh}, format='json'
        )
        self.assertEqual(replay.status_code, 401)

    def test_web_refresh_without_cookie_is_401(self):
        response = api_client().post('/api/token/refresh/', {}, HTTP_CLIENT_TYPE='web')

        self.assertEqual(response.status_code, 401)


class LogoutTests(TestCase):
    def test_logout_blacklists_refresh_and_clears_web_cookies(self):
        user = make_user()
        client = api_client()
        login = client.post(
            '/api/token/',
            {'email': user.email, 'password': PASSWORD},
            format='json',
            HTTP_CLIENT_TYPE='web',
        )
        refresh_token = login.cookies['refresh_token'].value

        response = client.post('/api/token/logout/', {}, HTTP_CLIENT_TYPE='web')

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.cookies['access_token'].value, '')
        self.assertEqual(response.cookies['refresh_token'].value, '')

        after = api_client().post(
            '/api/token/refresh/', {'refresh': refresh_token}, format='json'
        )
        self.assertEqual(after.status_code, 401)

    def test_mobile_logout_blacklists_body_token(self):
        user = make_user()
        login = api_client().post(
            '/api/token/', {'email': user.email, 'password': PASSWORD}, format='json'
        )
        access = api_client(user)

        response = access.post(
            '/api/token/logout/', {'refresh': login.data['refresh']}, format='json'
        )

        self.assertEqual(response.status_code, 204, response.data)
        self.assertEqual(
            api_client().post(
                '/api/token/refresh/', {'refresh': login.data['refresh']}, format='json'
            ).status_code,
            401,
        )

    def test_logout_requires_authentication(self):
        response = api_client().post('/api/token/logout/', {}, format='json')

        self.assertEqual(response.status_code, 401)


class MeTests(TestCase):
    def test_get_me_never_returns_password(self):
        user = make_user(role=User.Role.STUDENT)

        data = api_client(user).get('/api/me/').data

        self.assertEqual(
            set(data),
            {
                'id', 'username', 'email', 'full_name', 'role',
                'is_active', 'is_staff', 'last_login', 'date_joined',
                'has_push_token',
            },
        )
        self.assertEqual(data['role'], 'STUDENT')

    def test_patch_full_name(self):
        user = make_user()
        client = api_client(user)

        response = client.patch('/api/me/', {'full_name': 'Aye Aye'}, format='json')

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['full_name'], 'Aye Aye')
        user.refresh_from_db()
        self.assertEqual(user.full_name, 'Aye Aye')

    def test_role_email_and_active_are_not_editable_here(self):
        user = make_user()
        original_email = user.email

        response = api_client(user).patch(
            '/api/me/',
            {'role': 'ADMIN', 'email': 'other@university.edu', 'is_active': False},
            format='json',
        )

        self.assertEqual(response.status_code, 200, response.data)
        user.refresh_from_db()
        self.assertEqual(user.role, User.Role.STUDENT)
        self.assertEqual(user.email, original_email)
        self.assertTrue(user.is_active)

    def test_password_change_needs_both_fields(self):
        user = make_user()

        response = api_client(user).patch(
            '/api/me/', {'new_password': 'Another-Str0ng-2026!'}, format='json'
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data['code'], 'validation_error')

    def test_password_change_rejects_wrong_current_and_weak_new(self):
        user = make_user()
        client = api_client(user)

        wrong = client.patch(
            '/api/me/',
            {'current_password': 'nope', 'new_password': 'Another-Str0ng-2026!'},
            format='json',
        )
        self.assertEqual(wrong.status_code, 400)
        self.assertIn('current_password', wrong.data['fields'])

        weak = client.patch(
            '/api/me/',
            {'current_password': PASSWORD, 'new_password': '123456'},
            format='json',
        )
        self.assertEqual(weak.status_code, 400)
        self.assertIn('new_password', weak.data['fields'])

    def test_password_change_then_old_password_cannot_log_in(self):
        user = make_user()
        new_password = 'Another-Str0ng-2026!'

        response = api_client(user).patch(
            '/api/me/',
            {'current_password': PASSWORD, 'new_password': new_password},
            format='json',
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            api_client().post(
                '/api/token/', {'email': user.email, 'password': PASSWORD}, format='json'
            ).status_code,
            401,
        )
        api_client(user, password=new_password)


class UserAdminTests(TestCase):
    def setUp(self):
        self.admin = make_user(role=User.Role.ADMIN)
        self.teacher = make_user(role=User.Role.TEACHER)
        self.student = make_user(role=User.Role.STUDENT)
        self.client = api_client(self.admin)

    def test_student_cannot_list_users(self):
        response = api_client(self.student).get('/api/users/')

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data['code'], 'permission_denied')

    def test_create_user_returns_full_representation(self):
        response = self.client.post(
            '/api/users/',
            {
                'username': 'aye.aye',
                'email': 'aye@university.edu',
                'full_name': 'Aye Aye',
                'role': 'STUDENT',
                'password': 'a-long-password-2026',
            },
            format='json',
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertNotIn('password', response.data)
        self.assertEqual(response.data['role'], 'STUDENT')
        created = User.objects.get(email='aye@university.edu')
        self.assertTrue(created.check_password('a-long-password-2026'))

    def test_duplicate_email_and_username_are_conflicts(self):
        duplicate_email = self.client.post(
            '/api/users/',
            {
                'username': 'fresh.name',
                'email': self.teacher.email,
                'full_name': 'Other Name',
                'role': 'TEACHER',
                'password': 'a-long-password-2026',
            },
            format='json',
        )
        self.assertEqual(duplicate_email.status_code, 409, duplicate_email.data)
        self.assertEqual(duplicate_email.data['code'], 'conflict')

        duplicate_username = self.client.post(
            '/api/users/',
            {
                'username': self.teacher.username,
                'email': 'fresh@university.edu',
                'full_name': 'Other Name',
                'role': 'TEACHER',
                'password': 'a-long-password-2026',
            },
            format='json',
        )
        self.assertEqual(duplicate_username.status_code, 409)

    def test_weak_password_on_create_is_400(self):
        response = self.client.post(
            '/api/users/',
            {
                'username': 'weak.one',
                'email': 'weak@university.edu',
                'full_name': 'Weak One',
                'role': 'STUDENT',
                'password': '123456',
            },
            format='json',
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data['code'], 'validation_error')
        self.assertIn('password', response.data['fields'])

    def test_invalid_role_is_400(self):
        response = self.client.post(
            '/api/users/',
            {
                'username': 'odd.one',
                'email': 'odd@university.edu',
                'full_name': 'Odd One',
                'role': 'PROVOST',
                'password': 'a-long-password-2026',
            },
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('role', response.data['fields'])

    def test_patch_changes_role_and_active_then_returns_representation(self):
        response = self.client.patch(
            f'/api/users/{self.student.pk}/',
            {'role': 'TEACHER', 'is_active': False},
            format='json',
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['role'], 'TEACHER')
        self.assertFalse(response.data['is_active'])
        self.assertIn('date_joined', response.data)

    def test_filters(self):
        make_user(role=User.Role.TEACHER, full_name='U Hla')

        self.assertEqual(
            [row['role'] for row in self.client.get('/api/users/?role=TEACHER').data['results']],
            ['TEACHER', 'TEACHER'],
        )
        inactive = self.client.patch(
            f'/api/users/{self.student.pk}/', {'is_active': False}, format='json'
        )
        self.assertEqual(inactive.status_code, 200)
        self.assertEqual(
            [row['id'] for row in self.client.get('/api/users/?is_active=false').data['results']],
            [str(self.student.pk)],
        )
        self.assertEqual(
            [row['full_name'] for row in self.client.get('/api/users/?search=U Hla').data['results']],
            ['U Hla'],
        )

    def test_pagination_defaults_and_page_size(self):
        for _ in range(25):
            make_user()

        default = self.client.get('/api/users/').data
        self.assertEqual(default['count'], 28)
        self.assertEqual(len(default['results']), 20)
        self.assertIsNotNone(default['next'])

        small = self.client.get('/api/users/?page_size=5').data
        self.assertEqual(len(small['results']), 5)

        second_page = self.client.get('/api/users/?page_size=5&page=2').data
        self.assertEqual(len(second_page['results']), 5)
        self.assertNotEqual(
            [row['id'] for row in second_page['results']],
            [row['id'] for row in small['results']],
        )

        oversized = self.client.get('/api/users/?page_size=500').data
        self.assertEqual(len(oversized['results']), 28)

    def test_unknown_user_id_is_404(self):
        self.assertEqual(self.client.get('/api/users/00000000-0000-4000-8000-000000000000/').status_code, 404)
        self.assertEqual(self.client.get('/api/users/not-a-uuid/').status_code, 404)

    def test_delete_removes_a_user_with_no_references(self):
        target = make_user()

        response = self.client.delete(f'/api/users/{target.pk}/')

        self.assertEqual(response.status_code, 204)
        self.assertFalse(User.objects.filter(pk=target.pk).exists())


class PushTokenTests(TestCase):
    TOKEN = 'ExponentPushToken[9y-Tn8mQxQqLkV3pB7dRfe]'

    def test_post_registers_the_device_token(self):
        user = make_user()

        response = api_client(user).post(
            '/api/me/push-token/', {'push_token': self.TOKEN}, format='json'
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data, {'has_push_token': True})
        user.refresh_from_db()
        self.assertEqual(user.push_token, self.TOKEN)

    def test_a_second_device_replaces_the_first(self):
        user = make_user(push_token=self.TOKEN)
        other = 'ExpoPushToken[aaaaaaaaaa]'

        api_client(user).post('/api/me/push-token/', {'push_token': other}, format='json')

        user.refresh_from_db()
        self.assertEqual(user.push_token, other)

    def test_only_expo_token_shapes_are_accepted(self):
        for value in ('', 'https://onesignal.com/api/v1/notifications', 'ExponentPushToken[]'):
            with self.subTest(value=value):
                response = api_client(make_user()).post(
                    '/api/me/push-token/', {'push_token': value}, format='json'
                )
                self.assertEqual(response.status_code, 400, response.data)
                self.assertEqual(response.data['code'], 'validation_error')
                self.assertIn('push_token', response.data['fields'])

    def test_delete_clears_the_token(self):
        user = make_user(push_token=self.TOKEN)

        response = api_client(user).delete('/api/me/push-token/')

        self.assertEqual(response.status_code, 204)
        user.refresh_from_db()
        self.assertEqual(user.push_token, '')

    def test_me_reports_presence_but_never_the_token(self):
        user = make_user(push_token=self.TOKEN)

        data = api_client(user).get('/api/me/').data

        self.assertTrue(data['has_push_token'])
        self.assertNotIn('push_token', data)

    def test_anonymous_cannot_register(self):
        response = api_client().post('/api/me/push-token/', {'push_token': self.TOKEN}, format='json')

        self.assertEqual(response.status_code, 401)

