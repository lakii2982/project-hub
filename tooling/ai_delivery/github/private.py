"""Trusted-process storage, POSIX locks and strict private filesystem checks.

Root is trusted startup configuration, never a business input. Production uses
production_store() only. Low-level injection exists for isolated tests. This is
not an OS sandbox: the integrator must keep workers under a different identity
or deny this directory using its actual worker isolation mechanism.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import pwd
import sqlite3
import stat

from .errors import Refused


class PrivateStore:
    def __init__(self, root):
        configured = Path(root).absolute()
        if configured.is_symlink():
            raise Refused('private store symlink forbidden')
        self.root = configured.resolve()
        try:
            self.root.mkdir(mode=0o700)
        except FileExistsError:
            pass
        self.check()
        with self.lock('bootstrap'):
            for name in ('state', 'grants'):
                with self.connection(name) as db:
                    db.execute('BEGIN IMMEDIATE')
                    if name == 'state':
                        db.executescript('''
                        CREATE TABLE IF NOT EXISTS requests (
                          repository TEXT NOT NULL, request_id TEXT NOT NULL,
                          issue_number INTEGER, confirmed_revision INTEGER NOT NULL DEFAULT 0,
                          payload_digest TEXT, PRIMARY KEY(repository, request_id));
                        CREATE TABLE IF NOT EXISTS operations (
                          repository TEXT NOT NULL, request_id TEXT NOT NULL,
                          action TEXT NOT NULL CHECK(action IN ('issue_create','issue_update')),
                          revision INTEGER NOT NULL CHECK(revision > 0), payload_digest TEXT NOT NULL,
                          state TEXT NOT NULL CHECK(state IN ('pending','dispatching','confirmed','rejected','unknown')),
                          issue_number INTEGER, updated_at TEXT NOT NULL,
                          PRIMARY KEY(repository, request_id, action, revision));
                        CREATE INDEX IF NOT EXISTS operations_revision_updated
                          ON operations(revision DESC, updated_at DESC);
                        ''')
                    else:
                        db.execute('''CREATE TABLE IF NOT EXISTS grants (
                          id TEXT PRIMARY KEY, principal TEXT NOT NULL, repository TEXT NOT NULL,
                          request_id TEXT NOT NULL, actions TEXT NOT NULL, revision INTEGER NOT NULL,
                          payload_digest TEXT NOT NULL, expires_at TEXT NOT NULL, revoked_at TEXT,
                          source_ref TEXT NOT NULL)''')
                    db.commit()

    @staticmethod
    def _verify(info, mode, *, directory=False):
        kind = stat.S_ISDIR if directory else stat.S_ISREG
        if not kind(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != mode:
            raise Refused('private store owner/type/mode check failed')
        if not directory and info.st_nlink != 1:
            raise Refused('private store hard links forbidden')

    def check(self):
        if self.root.resolve() != self.root:
            raise Refused('private store symlink forbidden')
        self._verify(self.root.lstat(), 0o700, directory=True)
        for path in self.root.iterdir():
            try:
                info = path.lstat()
            except FileNotFoundError:
                if path.name in {'state.sqlite3-journal', 'state.sqlite3-wal', 'state.sqlite3-shm',
                                 'grants.sqlite3-journal', 'grants.sqlite3-wal', 'grants.sqlite3-shm'}:
                    continue
                raise
            self._verify(info, 0o600)

    def _open(self, filename):
        self.check()
        fd = os.open(self.root / filename, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            self._verify(os.fstat(fd), 0o600)
        except BaseException:
            os.close(fd)
            raise
        return fd

    @contextmanager
    def lock(self, identity):
        name = hashlib.sha256(identity.encode('utf-8')).hexdigest() + '.lock'
        fd = self._open(name)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            self.check()
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def request_lock(self, repository, request_id):
        # All actions and revisions deliberately share one lock.
        return self.lock(repository + '\0' + request_id)

    @contextmanager
    def connection(self, name):
        if name not in {'state', 'grants'}:
            raise Refused('unknown private database')
        filename = name + '.sqlite3'
        fd = self._open(filename)
        os.close(fd)
        db = sqlite3.connect(self.root / filename, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA journal_mode=DELETE')
            db.execute('PRAGMA synchronous=FULL')
            self.check()
            yield db
            self.check()
        finally:
            db.close()

    @contextmanager
    def transaction(self, name='state'):
        with self.connection(name) as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise


def production_store():
    """One fixed state location per controller OS account, across every checkout.

    No environment/Issue/worker/request option can choose another state database.
    Never call this factory from tests, workers or untrusted request handlers.
    """
    home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    return PrivateStore(home / '.project-hub-github')
