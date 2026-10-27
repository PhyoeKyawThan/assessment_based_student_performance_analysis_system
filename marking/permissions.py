from rest_framework.permissions import SAFE_METHODS, BasePermission

from teaching.permissions import is_admin, owns_plan
from users.models import User


class IsResultWriter(BasePermission):
    """Admins and teachers write marks on their own plans. Students only read their own rows."""

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if request.method in SAFE_METHODS:
            return True
        return is_admin(user) or user.role == User.Role.TEACHER

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        return owns_plan(request.user, obj.assessment.lecture_plan)
