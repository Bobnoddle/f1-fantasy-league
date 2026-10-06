"""Test package.

Exists so ``from tests.helpers import ...`` resolves under a bare
``pytest`` invocation. Without it pytest puts ``tests/`` on sys.path instead of
the repository root, and those imports fail in CI but pass under
``python -m pytest``.
"""
