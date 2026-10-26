"""Scratch settings: run the test suite on SQLite. Delete after use."""

from core.settings import *  # noqa: F403

DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}}
