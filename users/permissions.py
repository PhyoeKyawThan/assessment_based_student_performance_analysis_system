from rest_framework.permissions import SAFE_METHODS, BasePermission

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

class IsAdminRoleOrReadOnly(BasePermission):
    """Any signed-in user may read; only ADMIN or superuser may write."""
    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        if request.method in SAFE_METHODS:
            return True
        return IsAdminRole().has_permission(request, view)