"""Composed login + permission decorators for views."""

from django.contrib.auth.decorators import (
    login_required,
    permission_required,
    user_passes_test,
)

# The four whatax roles; holding any of them grants the app's shared surfaces.
_ANY_WHATAX_PERMS = (
    "whatax.basic_access",
    "whatax.view_structures",
    "whatax.manage_payments",
    "whatax.admin_access",
)


def basic_access_required(view):
    """USER: own dashboard."""
    return login_required(permission_required("whatax.basic_access")(view))


def any_access_required(view):
    """ANY whatax role: shared read-only surfaces (e.g. Characters)."""
    check = user_passes_test(lambda u: any(u.has_perm(p) for p in _ANY_WHATAX_PERMS))
    return login_required(check(view))


def structures_required(view):
    """STRUCTURES: read-only pop schedule & warnings."""
    return login_required(permission_required("whatax.view_structures")(view))


def staff_required(view):
    """STAFF: fix payments, balances, all records."""
    return login_required(permission_required("whatax.manage_payments")(view))


def admin_required(view):
    """ADMIN: configuration & dangerous actions."""
    return login_required(permission_required("whatax.admin_access")(view))
