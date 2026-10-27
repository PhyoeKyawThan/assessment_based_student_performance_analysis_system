from unittest.mock import patch

from django.test import TestCase

from core.notifications import BATCH_SIZE, send_push
from core.testing import mock_push_transport
from users.models import User


def token(index):
    return f'ExponentPushToken[batch-{index}]'


class SendPushTests(TestCase):
    def configured(self):
        return self.settings(EXPO_ACCESS_TOKEN='expo-server-token')

    def recipients(self, count=1):
        users = [
            User(
                username=f'push{i}',
                email=f'push{i}@university.edu',
                full_name=f'Push {i}',
                role=User.Role.STUDENT,
                push_token=token(i),
            )
            for i in range(count)
        ]
        return User.objects.bulk_create(users)

    def test_no_message_without_a_registered_token(self):
        student = self.recipients(1)[0]
        student.push_token = ''
        student.save(update_fields=['push_token'])

        with self.configured(), mock_push_transport() as batches:
            accepted = send_push([student], 'Title', 'Body')

        self.assertEqual(accepted, 0)
        self.assertEqual(batches, [])

    def test_unset_access_token_logs_and_skips_the_call(self):
        with self.settings(EXPO_ACCESS_TOKEN=''), mock_push_transport() as batches:
            accepted = send_push(self.recipients(2), 'Title', 'Body')

        self.assertEqual(accepted, 0)
        self.assertEqual(batches, [])

    def test_message_shape(self):
        with self.configured(), mock_push_transport() as batches:
            accepted = send_push(self.recipients(1), 'Your mark is ready', 'CS101 · Quiz 1: 8.00', data={'type': 'mark'})

        self.assertEqual(accepted, 1)
        self.assertEqual(
            batches[0][0],
            {
                'to': token(0),
                'title': 'Your mark is ready',
                'body': 'CS101 · Quiz 1: 8.00',
                'data': {'type': 'mark'},
            },
        )

    def test_audiences_larger_than_the_service_limit_are_split(self):
        with self.configured(), mock_push_transport() as batches:
            accepted = send_push(self.recipients(BATCH_SIZE + 20), 'Title', 'Body')

        self.assertEqual(accepted, BATCH_SIZE + 20)
        self.assertEqual([len(batch) for batch in batches], [BATCH_SIZE, 20])

    def test_a_registered_token_is_forgotten_when_the_device_is_gone(self):
        gone = self.recipients(1)[0]

        def rejected(message):
            return {
                'status': 'error',
                'message': 'NotRegistered',
                'details': {'error': 'DeviceNotRegistered'},
            }

        with self.configured(), mock_push_transport(rejected) as batches:
            accepted = send_push([gone], 'Title', 'Body')

        self.assertEqual(accepted, 0)
        self.assertEqual(len(batches), 1)
        gone.refresh_from_db()
        self.assertEqual(gone.push_token, '')

    def test_a_transport_failure_leaves_the_tokens_in_place(self):
        students = self.recipients(2)

        with self.configured(), patch('urllib.request.urlopen', side_effect=OSError('connection reset')):
            accepted = send_push(students, 'Title', 'Body')

        self.assertEqual(accepted, 0)
        for student in students:
            student.refresh_from_db()
            self.assertTrue(student.push_token)
