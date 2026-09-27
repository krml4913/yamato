"""util._flock / atomic_write on POSIX and (simulated) Windows (windows-research §2.1, §2.5).

Windows itself is never available on this machine, so its branch is exercised by
patching ``os.name`` to ``"nt"`` and putting a small fake in ``sys.modules["msvcrt"]``
(the real module does not exist here either) -- the documented way this ship tests an
OS branch it cannot run for real (board T-028)."""
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from yamato import util


class FakeMsvcrt:
    """Just enough of ``msvcrt`` to exercise ``util.lock_file`` / ``unlock_file``'s
    Windows branch: ``locking(fd, LK_NBLCK, 1)`` grabs an exclusive lock on the file
    behind ``fd`` (keyed by inode, since a real fd number is only good for one open),
    raising ``OSError`` (like the real ``LK_NBLCK`` does) when another holder has it;
    ``LK_UNLCK`` releases it."""

    LK_NBLCK = 1
    LK_UNLCK = 3

    def __init__(self):
        self._locked: set = set()
        self._guard = threading.Lock()

    def _key(self, fd):
        st = os.fstat(fd)
        return (st.st_dev, st.st_ino)

    def locking(self, fd, mode, nbytes):
        key = self._key(fd)
        with self._guard:
            if mode == self.LK_UNLCK:
                self._locked.discard(key)
                return
            if key in self._locked:
                raise OSError("[fake] すでにロックされている")
            self._locked.add(key)


class WindowsSimTestCase(unittest.TestCase):
    """Installs ``FakeMsvcrt`` and gives ``self.as_windows()``, a context manager that
    patches ``os.name`` to ``"nt"`` around the one call under test.

    ``os.name`` is patched only around that call, never for the whole test: ``pathlib``
    picks ``PosixPath`` vs ``WindowsPath`` from ``os.name`` at *instantiation* time (Python
    3.13 refuses to build a ``WindowsPath`` on a real POSIX system), so any ``Path(...)``
    built in ``setUp`` -- or by ``tempfile`` -- must happen before the patch is in effect."""

    def setUp(self):
        self.fake_msvcrt = FakeMsvcrt()
        self._old_msvcrt = sys.modules.get("msvcrt")
        sys.modules["msvcrt"] = self.fake_msvcrt
        self.addCleanup(self._restore_msvcrt)

    def _restore_msvcrt(self):
        if self._old_msvcrt is None:
            sys.modules.pop("msvcrt", None)
        else:
            sys.modules["msvcrt"] = self._old_msvcrt

    def as_windows(self):
        return mock.patch.object(os, "name", "nt")


class FlockTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "x.lock"

    def test_posix_flock_serialises_two_threads(self):
        done = threading.Event()

        def other():
            with util._flock(self.path):
                pass
            done.set()

        with util._flock(self.path):
            t = threading.Thread(target=other)
            t.start()
            time.sleep(0.2)
            self.assertFalse(done.is_set())   # blocked while the main thread holds it
        t.join(5)
        self.assertTrue(done.is_set())


class FlockWindowsTest(WindowsSimTestCase):
    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "x.lock"

    def test_lock_file_uses_msvcrt_locking_not_fcntl(self):
        f = open(self.path, "a")
        self.addCleanup(f.close)
        with self.as_windows():
            util.lock_file(f)
            self.assertIn(self.fake_msvcrt._key(f.fileno()), self.fake_msvcrt._locked)
            util.unlock_file(f)
        self.assertNotIn(self.fake_msvcrt._key(f.fileno()), self.fake_msvcrt._locked)

    def test_flock_serialises_two_threads_via_msvcrt_locking(self):
        # msvcrt only offers a non-blocking mode (LK_NBLCK): util.lock_file must loop on
        # OSError itself, the same way fcntl.flock's blocking LOCK_EX does on POSIX.
        # One as_windows() covers both threads' whole lifetime (started, joined, and only
        # then exited): os.name is process-global, so two independent patches racing their
        # own start/stop from different threads would be a test bug, not a real one.
        done = threading.Event()

        def other():
            with util._flock(self.path):
                pass
            done.set()

        with self.as_windows():
            with util._flock(self.path):
                t = threading.Thread(target=other)
                t.start()
                time.sleep(0.2)
                self.assertFalse(done.is_set())
            t.join(5)
        self.assertTrue(done.is_set())


class AtomicWriteTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "d" / "f.json"

    def test_writes_and_reads_back(self):
        util.atomic_write(self.path, "hello\n")
        self.assertEqual(self.path.read_text(), "hello\n")

    def test_posix_permission_error_is_not_retried(self):
        calls = []

        def replace(src, dst):
            calls.append(1)
            raise PermissionError("boom")

        with mock.patch.object(util.os, "replace", side_effect=replace), \
                mock.patch.object(util.time, "sleep") as sleep:
            with self.assertRaises(PermissionError):
                util.atomic_write(self.path, "x")
        self.assertEqual(len(calls), 1)   # no retry loop on POSIX
        sleep.assert_not_called()


class AtomicWriteWindowsTest(WindowsSimTestCase):
    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "d" / "f.json"

    def test_retries_a_permission_error_then_succeeds(self):
        # a reader with no lock of its own (or a virus scanner) has the destination open
        # for a moment; os.replace on Windows raises PermissionError until it lets go.
        real_replace = os.replace
        fails = {"n": 2}

        def replace(src, dst):
            if fails["n"] > 0:
                fails["n"] -= 1
                raise PermissionError("[fake WinError 32] file in use")
            real_replace(src, dst)

        with self.as_windows(), \
                mock.patch.object(util.os, "replace", side_effect=replace), \
                mock.patch.object(util.time, "sleep") as sleep:
            util.atomic_write(self.path, "hello\n")
        self.assertEqual(self.path.read_text(), "hello\n")
        self.assertEqual(sleep.call_count, 2)

    def test_gives_up_after_the_retry_budget(self):
        with self.as_windows(), \
                mock.patch.object(util.os, "replace", side_effect=PermissionError("boom")), \
                mock.patch.object(util.time, "sleep"):
            with self.assertRaises(PermissionError):
                util.atomic_write(self.path, "x")
        self.assertFalse(self.path.exists())
        # the temp file is cleaned up, not left behind next to the destination
        self.assertEqual(list((Path(self._tmp.name) / "d").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
