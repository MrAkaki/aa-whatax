"""Composed login + permission decorators for views."""

from django.contrib.auth.decorators import login_required, permission_required


def basic_access_required(view):
    """USER: own dashboard."""
    return login_required(permission_required("whatax.basic_access")(view))


def structures_required(view):
    """STRUCTURES: read-only pop schedule & warnings."""
    return login_required(permission_required("whatax.view_structures")(view))


def staff_required(view):
    """STAFF: fix payments, balances, all records."""
    return login_required(permission_required("whatax.manage_payments")(view))


def admin_required(view):
    """ADMIN: configuration & dangerous actions."""
    return login_required(permission_required("whatax.admin_access")(view))
