"""Domain logic (TECHNICAL.md §4 layering rule).

Plain functions/classes that take models and return values — no request, no
worker. Unit-testable in isolation with ESI/Janice mocked at ``providers.py``.
``views`` and ``tasks`` are thin shells over this package.
"""
