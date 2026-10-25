from __future__ import annotations

import errno
import os
from pathlib import Path
import signal
import sys
import time


class ProcessError(RuntimeError):
    pass


class ProcessGone(ProcessError):
    pass


class ProcessUnknown(ProcessError):
    pass


def _validate_pid(pid):
    if type(pid) is not int or pid <= 0:
        raise ValueError("pid must be positive")


def _linux_identity(pid):
    _validate_pid(pid)
    try:
        data = Path(f"/proc/{pid}/stat").read_bytes()
    except FileNotFoundError as exc:
        raise ProcessGone("process does not exist") from exc
    except PermissionError as exc:
        raise ProcessUnknown("process identity is not readable") from exc
    except OSError as exc:
        if exc.errno in {errno.ENOENT, errno.ESRCH}:
            raise ProcessGone("process does not exist") from exc
        raise ProcessUnknown("process identity is unknown") from exc
    try:
        closing = data.rindex(b")")
        fields = data[closing + 2:].split()
        recorded_pid = int(data[:data.index(b"(")].strip())
        pgid = int(fields[2])
        start_ticks = int(fields[19])
    except (ValueError, IndexError) as exc:
        raise ProcessUnknown("invalid process identity snapshot") from exc
    if recorded_pid != pid or pgid <= 0 or start_ticks <= 0:
        raise ProcessUnknown("inconsistent process identity snapshot")
    return pid, pgid, start_ticks


def _darwin_identity(pid):
    _validate_pid(pid)
    import ctypes as C

    u32 = C.c_uint32

    class ProcBSDInfo(C.Structure):
        _fields_ = [
            ("pbi_flags", u32), ("pbi_status", u32), ("pbi_xstatus", u32),
            ("pbi_pid", u32), ("pbi_ppid", u32),
            ("pbi_uid", u32), ("pbi_gid", u32),
            ("pbi_ruid", u32), ("pbi_rgid", u32),
            ("pbi_svuid", u32), ("pbi_svgid", u32), ("rfu_1", u32),
            ("pbi_comm", C.c_char * 16), ("pbi_name", C.c_char * 32),
            ("pbi_nfiles", u32), ("pbi_pgid", u32), ("pbi_pjobc", u32),
            ("e_tdev", u32), ("e_tpgid", u32), ("pbi_nice", C.c_int32),
            ("pbi_start_tvsec", C.c_uint64), ("pbi_start_tvusec", C.c_uint64),
        ]

    info = ProcBSDInfo()
    size = C.sizeof(info)
    if size != 136:
        raise ProcessUnknown("unexpected native process identity layout")
    try:
        library = C.CDLL("libproc.dylib", use_errno=True)
        proc_pidinfo = library.proc_pidinfo
        proc_pidinfo.argtypes = [C.c_int, C.c_int, C.c_uint64, C.c_void_p, C.c_int]
        proc_pidinfo.restype = C.c_int
        C.set_errno(0)
        count = proc_pidinfo(pid, 3, 0, C.byref(info), size)
        error = C.get_errno()
    except (OSError, AttributeError) as exc:
        raise ProcessUnknown("native process identity is unavailable") from exc
    if count <= 0:
        if error in {errno.ENOENT, errno.ESRCH}:
            raise ProcessGone("process does not exist")
        raise ProcessUnknown("process identity query failed")
    if count != size or info.pbi_pid != pid or info.pbi_pgid <= 0:
        raise ProcessUnknown("inconsistent process identity snapshot")
    start_us = info.pbi_start_tvsec * 1_000_000 + info.pbi_start_tvusec
    if start_us <= 0 or info.pbi_start_tvusec >= 1_000_000:
        raise ProcessUnknown("invalid process start timestamp")
    return pid, info.pbi_pgid, start_us


def process_identity(pid):
    if sys.platform == "darwin":
        return _darwin_identity(pid)
    if sys.platform.startswith("linux"):
        return _linux_identity(pid)
    raise ProcessUnknown("process identity is unsupported on this platform")


def _coordinator_group():
    try:
        return os.getpgrp()
    except OSError as exc:
        raise ProcessUnknown("coordinator process group is unknown") from exc


def group_empty(pgid):
    if type(pgid) is not int or pgid <= 0 or pgid == _coordinator_group():
        raise ProcessUnknown("unsafe process group")
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return True
    except PermissionError as exc:
        raise ProcessUnknown("process group existence is unknown") from exc
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            return True
        raise ProcessUnknown("process group existence is unknown") from exc
    return False


def _reap(pid):
    try:
        waited, _status = os.waitpid(pid, os.WNOHANG)
        return waited == pid
    except ChildProcessError:
        return False
    except OSError:
        return False


def _recorded(identity):
    if not isinstance(identity, (tuple, list)) or len(identity) != 3:
        raise ValueError("invalid recorded process identity")
    recorded = tuple(identity)
    if any(type(value) is not int or value <= 0 for value in recorded):
        raise ValueError("invalid recorded process identity")
    if recorded[0] != recorded[1] or recorded[1] == _coordinator_group():
        raise ProcessUnknown("unsafe or non-leader process group")
    return recorded


def stop_group(identity, timeout=5, sig=signal.SIGTERM):
    recorded = _recorded(identity)
    if not isinstance(timeout, (int, float)) or timeout < 0:
        raise ValueError("invalid timeout")
    try:
        current = process_identity(recorded[0])
    except ProcessGone:
        if group_empty(recorded[1]):
            return
        raise ProcessUnknown("group leader is gone but its group remains")
    if current != recorded:
        raise ProcessUnknown("process identity changed")
    try:
        os.killpg(recorded[1], sig)
    except ProcessLookupError:
        pass
    except PermissionError as exc:
        raise ProcessUnknown("process group cannot be signaled safely") from exc
    except OSError as exc:
        raise ProcessUnknown("process group signal failed") from exc
    deadline = time.monotonic() + timeout
    while True:
        _reap(recorded[0])
        try:
            process_identity(recorded[0])
            alive = True
        except ProcessGone:
            alive = False
        except ProcessUnknown:
            alive = True
        try:
            empty = group_empty(recorded[1])
        except ProcessUnknown:
            # Darwin can briefly return EPERM while a signaled child exits.
            # Retry proof of death until the deadline, never send another signal
            # on the strength of an unknown identity or release the scope early.
            empty = False
        if not alive and empty:
            return
        if time.monotonic() >= deadline:
            raise ProcessUnknown("process group did not exit")
        time.sleep(min(0.05, max(0, deadline - time.monotonic())))
