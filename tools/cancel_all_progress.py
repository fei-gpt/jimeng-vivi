from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TASK_STATES = ("pending", "reviewing", "running")
TARGET_STATE = "failed"


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def task_roots() -> list[Path]:
    roots = [ROOT / "tasks"]
    tenants = ROOT / "tenants"
    if tenants.exists():
        for tenant in tenants.iterdir():
            candidate = tenant / "tasks"
            if candidate.exists():
                roots.append(candidate)
    return roots


def unique_target(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for index in range(1, 1000):
        candidate = path.with_name(f"{stem}.cancelled{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"cannot allocate target path for {path}")


def cancel_task(path: Path, target_dir: Path) -> Path:
    task = load_json(path)
    task["status"] = "cancelled"
    task["cancelled_at"] = now()
    task["fail_reason"] = "用户要求取消所有现有进度。"
    task["error"] = "用户要求取消所有现有进度。"
    target = unique_target(target_dir / path.name)
    write_json(target, task)
    path.unlink(missing_ok=True)
    return target


def remove_lock(path: Path) -> None:
    if path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path)


def main() -> None:
    if "--status" in sys.argv:
        rows = []
        for root in task_roots():
            for state in ("pending", "reviewing", "running", "failed", "done"):
                state_dir = root / state
                rows.append({
                    "dir": str(state_dir),
                    "count": len(list(state_dir.glob("*.json"))) if state_dir.exists() else 0,
                })
        locks_dir = ROOT / "locks" / "jimeng"
        locks = [str(path) for path in sorted(locks_dir.iterdir())] if locks_dir.exists() else []
        print(json.dumps({"queues": rows, "locks": locks}, ensure_ascii=False, indent=2))
        return

    cancelled = []
    for root in task_roots():
        for state in TASK_STATES:
            state_dir = root / state
            if not state_dir.exists():
                continue
            target_dir = root / TARGET_STATE
            for path in sorted(state_dir.glob("*.json")):
                cancelled.append((state, str(cancel_task(path, target_dir))))

    removed_locks = []
    locks_dir = ROOT / "locks" / "jimeng"
    if locks_dir.exists():
        for path in sorted(locks_dir.iterdir()):
            removed_locks.append(str(path))
            remove_lock(path)

    print(json.dumps({
        "cancelled_count": len(cancelled),
        "cancelled": cancelled,
        "removed_lock_count": len(removed_locks),
        "removed_locks": removed_locks,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
