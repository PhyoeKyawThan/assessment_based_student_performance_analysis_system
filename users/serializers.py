from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
import re

from rest_framework import serializers

from users.models import User

PUSH_TOKEN_PATTERN = re.compile(r'^Expo(nent)?PushToken\[[^\]\s]{1,128}\]$')


class UserRepresentationSerializer(serializers.ModelSerializer):
    """Read shape for /api/me/ and /api/users/. The password is never present."""

    has_push_token = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = (
            'id', 'username', 'email', 'full_name', 'role',
            'is_active', 'is_staff', 'last_login', 'date_joined', 'has_push_token',
        )
        read_only_fields = fields

    def get_has_push_token(self, user) -> bool:
        # The token itself is a device credential, so only its presence is exposed.
        return bool(user.push_token)


class PushTokenSerializer(serializers.Serializer):
    """Body of POST /api/me/push-token/."""

    push_token = serializers.CharField(max_length=255)

    def validate_push_token(self, value):
        value = value.strip()
        if not PUSH_TOKEN_PATTERN.match(value):
            raise serializers.ValidationError(
                'Expected an Expo push token, for example ExponentPushToken[...].'
            )
        return value


class MeUpdateSerializer(serializers.ModelSerializer):
    """Self service: name and password only."""

    current_password = serializers.CharField(write_only=True, required=False)
    new_password = serializers.CharField(write_only=True, required=False)

    class Meta:
        model = User
        fields = ('full_name', 'current_password', 'new_password')

    def validate(self, attrs):
        has_current = 'current_password' in attrs
        has_new = 'new_password' in attrs
        if has_current != has_new:
            raise serializers.ValidationError(
                'current_password and new_password must be sent together.'
            )
        if has_new:
            user = self.instance
            if not user.check_password(attrs['current_password']):
                raise serializers.ValidationError({'current_password': 'Current password is incorrect.'})
            try:
                validate_password(attrs['new_password'], user)
            except DjangoValidationError as exc:
                raise serializers.ValidationError({'new_password': list(exc.messages)})
        return attrs

    def update(self, instance, validated_data):
        validated_data.pop('current_password', None)
        new_password = validated_data.pop('new_password', None)
        if new_password is not None:
            instance.set_password(new_password)
        return super().update(instance, validated_data)


class UserCreateSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, style={'input_type': 'password'})

    class Meta:
        model = User
        fields = ('username', 'email', 'full_name', 'role', 'password')

    def validate_password(self, value):
        try:
            validate_password(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages))
        return value

    def create(self, validated_data):
        user = User(**validated_data)
        user.set_password(validated_data['password'])
        user.save()
        return user


class UserAdminUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ('full_name', 'role', 'is_active', 'email', 'username')
