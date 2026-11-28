"""Keep a git-checkout install on the latest ``origin/main`` and trace every instance.

``server.py`` calls :func:`start` only when it runs as a script, before it imports
any other ``maxsurf_mcp`` module. For a checkout whose current branch is ``main``:

1. An exclusive per-checkout OS lock serialises concurrent session starts, so
   git and pip never run twice at once and no server imports code while
   another one is updating the files.
2. ``git fetch origin main``, then ``git merge --ff-only origin/main``, only
   when there are no tracked changes and ``HEAD`` is an ancestor of
   ``origin/main``. The updater never resets, stashes, switches branches,
   cleans or force-updates. A refusal leaves the checkout as it was, and the
   server starts on the code it has.
3. When the update changes a dependency file, the new ``requirements.lock`` is
   installed *before* the fast-forward, and only if no other registered server
   of this checkout is alive. Otherwise the whole update waits for a later start.

:meth:`Startup.finish` imports every package module, so a running server never
loads a later revision from disk. It then writes this instance's record and
releases the lock. ``python -m maxsurf_mcp.autoupdate`` lists the records.

Standard library only. Importing another ``maxsurf_mcp`` module here would pin
its pre-update version in ``sys.modules``.
"""
from __future__ import annotations

import atexit
import datetime as _dt
import importlib
import json
import os
from pathlib import Path
import pkgutil
import subprocess
import sys
import tempfile
import time
from typing import Any

try:  # pragma: no cover - platform specific
    import msvcrt
except ImportError:
    msvcrt = None
try:  # pragma: no cover - platform specific
    import fcntl
except ImportError:
    fcntl = None

BRANCH = "main"
REMOTE = "origin"
DEPENDENCY_FILES = ("pyproject.toml", "requirements.lock", "requirements.txt")
DISABLE_ENV = "MAXSURF_MCP_AUTO_UPDATE"
#: Non-secret client identifiers recorded so an instance can be matched to its session.
CLIENT_ENV = ("CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_HOST_SESSION_ID", "CLAUDE_PID",
              "CODEX_THREAD_ID", "CODEX_SESSION_ID")

LOCK_WAIT_S = 90.0
FETCH_TIMEOUT_S = 30.0
GIT_TIMEOUT_S = 60.0
PIP_TIMEOUT_S = 900.0


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _git(root: Path, *args: str, timeout: float = GIT_TIMEOUT_S) -> subprocess.CompletedProcess:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never", LC_ALL="C")
    return subprocess.run(
        ["git", *args], cwd=root, env=env, capture_output=True, text=True,
        timeout=timeout, stdin=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _out(root: Path, *args: str) -> str | None:
    result = _git(root, *args)
    return result.stdout.strip() if result.returncode == 0 else None


def _short(text: str) -> str:
    text = " ".join((text or "").split())
    return text[-300:]


# --- locking ---------------------------------------------------------------
# Duplicated from audit.py on purpose: see the module docstring.

def _try_lock(handle: Any) -> bool:
    try:
        if msvcrt is not None:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        elif fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(handle: Any) -> None:
    try:
        if msvcrt is not None:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        elif fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    handle.close()


def acquire_lock(path: Path, wait_s: float) -> Any | None:
    """Return an open, exclusively locked handle, or None after ``wait_s``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+b")
    if handle.seek(0, os.SEEK_END) == 0:
        handle.write(b"\0")
        handle.flush()
    deadline = time.monotonic() + wait_s
    while True:
        if _try_lock(handle):
            return handle
        if time.monotonic() >= deadline:
            handle.close()
            return None
        time.sleep(0.25)


# --- instance registry -------------------------------------------------------

def pid_alive(pid: int) -> bool:
    # shortcut: a reused PID reads as alive, which only defers a dependency
    # update; compare process creation times if that ever matters.
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() == 5  # access denied: it exists
        try:
            code = ctypes.c_ulong()
            kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)  # signal 0 only probes on POSIX; never call this on Windows
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def read_instances(state: Path, prune: bool = False) -> list[dict[str, Any]]:
    records = []
    for path in sorted((state / "instances").glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        record["alive"] = pid_alive(int(record.get("pid", 0)))
        if prune and not record["alive"]:
            path.unlink(missing_ok=True)
            continue
        records.append(record)
    return records


def _log(state: Path, entry: dict[str, Any]) -> None:
    try:
        state.mkdir(parents=True, exist_ok=True)
        with open(state / "update.log", "a", encoding="utf-8") as log:
            log.write(json.dumps({"at": _now(), "pid": os.getpid(), **entry}) + "\n")
    except OSError:
        pass


# --- update ------------------------------------------------------------------

def _install_requirements(root: Path, revision: str) -> tuple[bool, str]:
    """Install ``revision``'s pinned requirements into this interpreter."""
    for name in ("requirements.lock", "requirements.txt"):
        content = _git(root, "show", f"{revision}:{name}")
        if content.returncode == 0:
            break
    else:
        return False, "no requirements file at the target revision"
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / name
        target.write_text(content.stdout, encoding="utf-8")
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
             "--no-input", "-q", "-r", str(target)],
            cwd=root, capture_output=True, text=True, timeout=PIP_TIMEOUT_S,
            stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return result.returncode == 0, _short(result.stderr or result.stdout)


def _update(root: Path, state: Path) -> dict[str, Any]:
    branch = _out(root, "symbolic-ref", "--short", "-q", "HEAD")
    if branch != BRANCH:
        return {"status": "skipped", "reason": f"checkout is on {branch or 'a detached HEAD'}, not {BRANCH}"}
    if _git(root, "diff-index", "--quiet", "HEAD", "--").returncode != 0:
        return {"status": "skipped", "reason": "tracked files have local changes"}
    fetch = _git(root, "fetch", "--quiet", "--no-tags", REMOTE, BRANCH, timeout=FETCH_TIMEOUT_S)
    if fetch.returncode != 0:
        return {"status": "skipped", "reason": f"fetch failed: {_short(fetch.stderr)}"}
    head = _out(root, "rev-parse", "HEAD")
    target = _out(root, "rev-parse", f"refs/remotes/{REMOTE}/{BRANCH}")
    if not head or not target:
        return {"status": "skipped", "reason": f"cannot resolve HEAD or {REMOTE}/{BRANCH}"}
    if head == target:
        return {"status": "up_to_date"}
    if _git(root, "merge-base", "--is-ancestor", head, target).returncode != 0:
        return {"status": "skipped", "reason": f"local {BRANCH} is not an ancestor of {REMOTE}/{BRANCH}"}
    deps = _git(root, "diff", "--quiet", head, target, "--", *DEPENDENCY_FILES).returncode == 1
    outcome: dict[str, Any] = {"from": head, "to": target, "dependencies_changed": deps}
    if deps:
        others = [r["pid"] for r in read_instances(state, prune=True) if r["alive"] and r["pid"] != os.getpid()]
        if others:
            return {**outcome, "status": "deferred",
                    "reason": f"dependency change needs all servers stopped; running: {others}"}
        ok, detail = _install_requirements(root, target)
        if not ok:
            return {**outcome, "status": "skipped", "reason": f"dependency install failed: {detail}"}
    merge = _git(root, "merge", "--ff-only", "--quiet", target)
    if merge.returncode != 0:
        return {**outcome, "status": "skipped", "reason": f"fast-forward refused: {_short(merge.stderr)}"}
    return {**outcome, "status": "updated"}


class Startup:
    """Lock, update outcome and identity of one starting server."""

    def __init__(self, root: Path, state: Path, lock: Any | None, update: dict[str, Any]):
        self.root, self.state, self.lock, self.update = root, state, lock, update
        self.started_at = _now()
        self.commit = _out(root, "rev-parse", "HEAD")
        self.branch = _out(root, "symbolic-ref", "--short", "-q", "HEAD")
        self.dirty = _git(root, "diff-index", "--quiet", "HEAD", "--").returncode != 0
        self.record_path = state / "instances" / f"{os.getpid()}.json"

    def describe(self) -> dict[str, Any]:
        return {
            "pid": os.getpid(), "ppid": os.getppid(), "started_at": self.started_at,
            "commit": self.commit, "branch": self.branch, "dirty": self.dirty,
            "root": str(self.root), "python": sys.executable,
            "client": {k: os.environ[k] for k in CLIENT_ENV if os.environ.get(k)},
            "update": self.update,
        }

    def finish(self) -> None:
        """Load all package code, register this instance and release the lock."""
        try:
            package = sys.modules[__name__.rpartition(".")[0]]
            for info in pkgutil.iter_modules(package.__path__):
                try:
                    importlib.import_module(f"{package.__name__}.{info.name}")
                except Exception:  # a module this platform cannot import is never loaded later either
                    pass
            self.record_path.parent.mkdir(parents=True, exist_ok=True)
            self.record_path.write_text(json.dumps(self.describe(), indent=1), encoding="utf-8")
            atexit.register(self.record_path.unlink, missing_ok=True)
        except OSError:
            pass
        finally:
            if self.lock is not None:
                _unlock(self.lock)
                self.lock = None


def _state_dir(root: Path) -> Path | None:
    try:
        top = _out(root, "rev-parse", "--show-toplevel")
        git_dir = _out(root, "rev-parse", "--absolute-git-dir")
    except (OSError, subprocess.SubprocessError):
        return None  # no git: an installed bundle, not a checkout
    if not top or not git_dir or Path(top).resolve() != root.resolve():
        return None  # not the root of a checkout (e.g. a bundle unpacked inside another repo)
    return Path(git_dir) / "maxsurf-mcp"


def start(server_file: str) -> Startup | None:
    """Update this checkout if it is safe. Never raises, so the server always starts."""
    root = Path(server_file).resolve().parent
    state = _state_dir(root)
    if state is None:
        return None
    lock = None
    try:
        # Taken even when updates are disabled: another start may be mid-update.
        lock = acquire_lock(state / "update.lock", LOCK_WAIT_S)
        if os.environ.get(DISABLE_ENV, "").strip().lower() in {"0", "false", "no", "off"}:
            update = {"status": "disabled", "reason": f"{DISABLE_ENV} is off"}
        elif lock is None:
            update = {"status": "skipped", "reason": "another server held the update lock too long"}
        else:
            update = _update(root, state)
        startup = Startup(root, state, lock, update)
    except Exception as exc:  # the updater must never stop the server
        if lock is not None:
            _unlock(lock)
        update = {"status": "error", "reason": f"{type(exc).__name__}: {_short(str(exc))}"}
        try:
            startup = Startup(root, state, None, update)
        except Exception:
            _log(state, update)
            return None
    _log(state, {"update": update, "commit": startup.commit, "branch": startup.branch})
    detail = f" ({update['reason']})" if update.get("reason") else ""
    print(f"maxsurf-mcp update: {update['status']} at {(startup.commit or '?')[:9]}"
          f" on {startup.branch or 'detached HEAD'}{detail}", file=sys.stderr, flush=True)
    return startup


def status(root: Path) -> dict[str, Any]:
    """Every registered instance of the checkout at ``root``, marked against main."""
    state = _state_dir(root)
    if state is None:
        raise SystemExit(f"{root} is not the root of a git checkout")
    head = _out(root, "rev-parse", "HEAD")
    remote = _out(root, "rev-parse", f"refs/remotes/{REMOTE}/{BRANCH}")
    instances = read_instances(state, prune=True)
    latest = remote or head  # origin/main as of the last fetch
    for record in instances:
        record["current"] = bool(record.get("commit")) and record["commit"] == latest and not record.get("dirty")
    return {"checkout": str(root), "head": head, "branch": _out(root, "symbolic-ref", "--short", "-q", "HEAD"),
            f"{REMOTE}/{BRANCH}": remote, "instances": instances}


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    paths = [a for a in args if not a.startswith("--")]
    root = Path(paths[0]) if paths else Path(__file__).resolve().parent.parent
    report = status(root)
    if "--json" in args:
        print(json.dumps(report, indent=1))
        return 0
    print(f"checkout {report['checkout']} on {report['branch']} at {(report['head'] or '?')[:9]}"
          f" ({REMOTE}/{BRANCH} {(report[f'{REMOTE}/{BRANCH}'] or '?')[:9]})")
    for r in report["instances"]:
        client = ",".join(f"{k}={v}" for k, v in r.get("client", {}).items()) or "-"
        print(f"  pid {r['pid']:>6} ppid {r.get('ppid', '?'):>6}  started {r.get('started_at')}  "
              f"{(r.get('commit') or '?')[:9]} {r.get('branch')}{' dirty' if r.get('dirty') else ''}  "
              f"{'current' if r['current'] else 'OUTDATED'}  update={r.get('update', {}).get('status')}  {client}")
    if not report["instances"]:
        print("  no running instances registered")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
