#!/usr/bin/env python3
import os
import signal
from pathlib import Path


def main() -> int:
    current = os.getpid()
    stopped = 0
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        pid = int(proc.name)
        if pid == current:
            continue
        try:
            cmdline = (proc / "cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", errors="ignore")
        except OSError:
            continue
        if "worker/prompt_ui_server.py" not in cmdline:
            continue
        try:
            os.kill(pid, signal.SIGTERM)
            stopped += 1
        except OSError:
            pass
    print(f"stopped={stopped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
