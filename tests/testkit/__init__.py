"""Shared, package-agnostic test helpers for DuckHaven integration suites.

Lives at ``tests/testkit`` and is imported as the top-level ``testkit`` package.
``[tool.pytest.ini_options] pythonpath = ["tests"]`` puts ``tests/`` on
``sys.path``, so this imports cleanly from the per-package pytest runs
(``duckhaven-api`` / ``duckhaven-agent``) and the cross-component harness — its
unique name never collides with each service's own ``tests`` package.
"""
