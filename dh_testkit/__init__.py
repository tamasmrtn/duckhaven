"""Shared, package-agnostic test helpers for DuckHaven integration suites.

Lives at the repo root (rather than under any ``*/tests`` tree) so it is
importable from the per-package pytest runs (``duckhaven-api`` and
``duckhaven-agent``) and from the cross-component harness without colliding
with each package's own ``tests`` package. Each integration ``conftest.py``
puts the repo root on ``sys.path`` before importing from here.
"""
