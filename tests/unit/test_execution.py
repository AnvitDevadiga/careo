import os
import sys
import time
from pathlib import Path

import pytest

from careo.execution import EXITED, OUTPUT_LIMIT, SPAWN_ERROR, TIMEOUT, run_program

ENV = {"PATH": os.environ.get("PATH", "")}


def run(code: str, stdin: bytes = b"", timeout: float = 5, limit: int = 1 << 20, cwd=None):
    return run_program([sys.executable, "-c", code], cwd or Path.cwd(), ENV, stdin, timeout, limit)


def test_captures_stdout_stderr_exit():
    r = run(
        "import sys; d=sys.stdin.read(); print(d.upper()); sys.stderr.write('e'); sys.exit(3)",
        b"hi",
    )
    assert r.status == EXITED and r.exit_code == 3
    assert r.stdout == b"HI\n" and r.stderr == b"e"


def test_timeout_kills_and_is_classified():
    t = time.monotonic()
    r = run("import time; time.sleep(30)", timeout=0.5)
    assert r.status == TIMEOUT and r.exit_code is None
    assert time.monotonic() - t < 5


def test_output_limit_kills_runaway_output():
    r = run("import sys\nwhile True: sys.stdout.write('x'*65536)", limit=100_000)
    assert r.status == OUTPUT_LIMIT and r.stdout_truncated
    assert len(r.stdout) == 100_000


def test_stderr_limit():
    r = run("import sys; sys.stderr.write('e'*50000)", limit=1000)
    assert r.status == OUTPUT_LIMIT and r.stderr_truncated


def test_crash_by_signal():
    r = run("import os, signal; os.kill(os.getpid(), signal.SIGSEGV)")
    assert r.status == EXITED and r.exit_code == -11


def test_spawn_error():
    r = run_program(["/nonexistent/prog"], Path.cwd(), ENV, b"", 5, 100)
    assert r.status == SPAWN_ERROR and r.error


def test_program_ignoring_large_stdin():
    r = run("print('ok')", stdin=b"x" * 5_000_000)
    assert r.status == EXITED and r.stdout == b"ok\n"


def test_env_is_exactly_what_was_passed():
    r = run_program(
        [sys.executable, "-c", "import os,json; print(json.dumps(sorted(os.environ)))"],
        Path.cwd(),
        {"PATH": ENV["PATH"], "ONLY": "1"},
        b"",
        5,
        1 << 20,
    )
    names = set(eval(r.stdout))
    assert "ONLY" in names and "HOME" not in names


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.mark.parametrize("timeout", [5.0, 0.5])
def test_grandchildren_are_killed(tmp_path, timeout):
    pidfile = tmp_path / "pid"
    code = (
        "import subprocess, sys, time\n"
        "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"open({str(pidfile)!r}, 'w').write(str(c.pid))\n"
        + ("time.sleep(30)\n" if timeout < 1 else "")
    )
    t = time.monotonic()
    run(code, timeout=timeout)
    assert time.monotonic() - t < 10
    pid = int(pidfile.read_text())
    deadline = time.monotonic() + 5
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _alive(pid)
