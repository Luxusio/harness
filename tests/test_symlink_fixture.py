"""Real symlink fixtures with an explicit Windows privilege prerequisite."""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock


def create_symlink(link, target, *, target_is_directory=False):
    """Skip only a real Windows privilege refusal; preserve every other error."""
    try:
        Path(link).symlink_to(target, target_is_directory=target_is_directory)
    except OSError as error:
        if os.name == "nt" and getattr(error, "winerror", None) == 1314:
            reason = "Windows symlink privilege is unavailable (WinError 1314)"
            try:
                import pytest
            except ModuleNotFoundError as missing:
                if missing.name != "pytest":
                    raise
                raise unittest.SkipTest(reason) from error
            # In injected callbacks the product may catch Exception. pytest's
            # BaseException skip must escape that handler, rather than look
            # like a successfully refused attack that was never performed.
            pytest.skip(reason)
        raise


class SymlinkPrerequisiteTests(unittest.TestCase):
    def test_only_windows_privilege_refusal_skips(self):
        try:
            import pytest
            native_skip = pytest.skip.Exception
        except ModuleNotFoundError as missing:
            if missing.name != "pytest":
                raise
            native_skip = unittest.SkipTest
        error = OSError("symlink privilege fixture")
        error.winerror = 1314
        for platform in ("nt", "posix"):
            with self.subTest(platform=platform), \
                    mock.patch(f"{__name__}.os", SimpleNamespace(name=platform)), \
                    mock.patch.object(Path, "symlink_to", side_effect=error):
                expected = native_skip if platform == "nt" else OSError
                with self.assertRaises(expected):
                    create_symlink("fixture-link", "fixture-target")

    def test_other_creation_errors_propagate_unchanged(self):
        for code in (5, 32, None):
            error = OSError("real creation failure")
            if code is not None:
                error.winerror = code
            with self.subTest(winerror=code), \
                    mock.patch(f"{__name__}.os", SimpleNamespace(name="nt")), \
                    mock.patch.object(Path, "symlink_to", side_effect=error):
                with self.assertRaises(OSError) as raised:
                    create_symlink("fixture-link", "fixture-target")
                self.assertIs(raised.exception, error)
