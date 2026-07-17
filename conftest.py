"""Root conftest: give every pytest run its own scratch basetemp.

On this Windows setup, pytest runs happen under two different security
contexts (the developer shell and a sandboxed tool runner). A *fixed*
--basetemp directory — including pytest's default %TEMP%/pytest-of-<user> —
ends up owned by whichever context ran first and is then
PermissionError-locked for the other. Making basetemp unique per process
avoids sharing entirely. Old scratch dirs are cleaned best-effort;
foreign-context leftovers are ignored.
"""

import os
import shutil

import pytest


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    # Runs before the built-in tmpdir plugin materializes TempPathFactory,
    # so setting the option here is equivalent to passing --basetemp.
    if config.option.basetemp:
        return
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tmp")
    os.makedirs(root, exist_ok=True)
    for entry in os.listdir(root):
        if entry.startswith("pytest-scratch-"):
            shutil.rmtree(os.path.join(root, entry), ignore_errors=True)
    config.option.basetemp = os.path.join(root, f"pytest-scratch-{os.getpid()}")
