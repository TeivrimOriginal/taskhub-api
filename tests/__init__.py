"""Test-suite for TaskHub.

Layout:
* ``tests/unit``       — no database, no network, milliseconds each;
* ``tests/integration``— full request cycles through ASGI against a real PostgreSQL.

`conftest.py` builds the schema once per session and wraps every test in a transaction that
is rolled back afterwards, which is what keeps integration tests isolated and fast.
"""
