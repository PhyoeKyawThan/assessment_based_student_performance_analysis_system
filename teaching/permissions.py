from rest_framework.permissions import SAFE_METHODS, BasePermission

from teaching.models import Assessment, AssessmentDistribution, LecturePlan
from users.models import User


def is_admin(user):
    return user.role == User.Role.ADMIN or user.is_superuser


def plan_of(obj):
    if isinstance(obj, Assessment):
        return obj.distribution.lecture_plan
    if isinstance(obj, AssessmentDistribution):
        return obj.lecture_plan
    return obj


def owns_plan(user, plan):
    return is_admin(user) or plan.lecturer_id == user.pk


class IsPlanWriter(BasePermission):
    """Any signed-in user reads what their role can see; admin or the owning teacher writes."""

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
        return owns_plan(request.user, plan_of(obj))
