from rest_framework.permissions import BasePermission

class IsSuperAdmin(BasePermission):
    """Allows access only to superusers."""
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_superuser)

class IsAdminRole(BasePermission):
    """Allows access to users with ADMIN role or superusers."""
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and (request.user.role == 'ADMIN' or request.user.is_superuser))

class IsLecturerRole(BasePermission):
    """Allows access to teachers/lecturers, admins, or superusers."""
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and (request.user.role == 'TEACHER' or request.user.role == 'ADMIN' or request.user.is_superuser))

class IsStudentRole(BasePermission):
    """Allows access strictly to students."""
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.role == 'STUDENT')