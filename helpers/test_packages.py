"""Tests for helpers/packages.py's fetch retry. Run: python3 -m unittest discover -s helpers"""

import hashlib
import pathlib
import sys
import tempfile
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import packages  # noqa: E402

BODY = b"hello"
WANT = hashlib.sha256(BODY).hexdigest()


class Response:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return BODY


def opener(*steps):
    """A fake urlopen: each call raises the next step if it is an exception, else returns a body."""
    steps = list(steps)
    calls = []

    def urlopen(request, timeout):
        calls.append(request.full_url)
        step = steps.pop(0) if len(steps) > 1 else steps[0]
        if isinstance(step, Exception):
            raise step
        return Response()

    urlopen.calls = calls
    return urlopen


def unreachable():
    return urllib.error.URLError(OSError(101, "Network is unreachable"))


class FetchRetry(unittest.TestCase):
    def run_fetch(self, fake, want=WANT):
        with tempfile.TemporaryDirectory() as d:
            path = pathlib.Path(d) / "f"
            with (
                mock.patch.object(packages.urllib.request, "urlopen", fake),
                mock.patch.object(packages.time, "sleep") as sleep,
            ):
                try:
                    packages.fetch("https://example.invalid/f", path, want)
                except SystemExit as e:
                    return e, sleep, path.exists()
            return None, sleep, path.read_bytes() == BODY

    def test_fails_then_succeeds(self):
        fake = opener(unreachable(), TimeoutError(), urllib.error.HTTPError("u", 503, "x", {}, None), "ok")
        error, sleep, ok = self.run_fetch(fake)
        self.assertIsNone(error)
        self.assertTrue(ok)
        self.assertEqual(len(fake.calls), 4)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [2, 4, 8])

    def test_always_fails(self):
        fake = opener(unreachable())
        error, _, _ = self.run_fetch(fake)
        self.assertIsNotNone(error)
        self.assertEqual(len(fake.calls), packages.FETCH_ATTEMPTS)

    def test_always_fails_message(self):
        fake = opener(unreachable())
        with mock.patch.object(packages, "fail", side_effect=SystemExit) as fail:
            self.run_fetch(fake)
        message = fail.call_args.args[0]
        self.assertIn("https://example.invalid/f", message)
        self.assertIn("5 attempts", message)

    def test_client_error_not_retried(self):
        fake = opener(urllib.error.HTTPError("u", 404, "Not Found", {}, None))
        error, _, _ = self.run_fetch(fake)
        self.assertIsNotNone(error)
        self.assertEqual(len(fake.calls), 1)

    def test_wrong_digest_still_refused(self):
        error, _, exists = self.run_fetch(opener("ok"), want="0" * 64)
        self.assertIsNotNone(error)
        self.assertFalse(exists)


if __name__ == "__main__":
    unittest.main()
