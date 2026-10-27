from django.contrib import admin

from users.models import User


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ('email', 'full_name', 'role', 'is_active', 'has_push_token')
    search_fields = ('email', 'username', 'full_name')
    readonly_fields = ('push_token', 'last_login', 'date_joined')

    @admin.display(boolean=True, description='push registered')
    def has_push_token(self, user):
        return bool(user.push_token)
