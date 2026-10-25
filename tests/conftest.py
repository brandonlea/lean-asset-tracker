"""Make `pytest` work straight after `git clone`.

app.database raises at import time if DATABASE_URL is unset, which is right for
the real app but turns a contributor's first `pytest` run into a confusing
RuntimeError. The unit tests never open a connection, so a placeholder DSN is
enough to let the modules import.

A real DATABASE_URL in the environment always wins, so this can't mask the
setting you actually meant to use.
"""
import os

os.environ.setdefault("DATABASE_URL", "postgresql://unused:unused@127.0.0.1:1/unused")
