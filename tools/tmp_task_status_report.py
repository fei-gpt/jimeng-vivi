#!/usr/bin/env python3
import json
from pathlib import Path

ROOT = Path("/app")
STATUSES = ["pending", "reviewing", "running", "done", "failed", "needs_revision"]


def task_roots():
    roots = [ROOT]
    tenants = ROOT / "tenants"
    if tenants.exists():
        roots.extend(sorted(path for path in tenants.glob("*") if path.is_dir()))
    return roots


def read_task(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        return {"task_id": path.stem, "fail_reason": str(exc)}


def main() -> None:
    rows = []
    for base in task_roots():
        tenant = "__root__" if base == ROOT else base.name
        for status in STATUSES:
            directory = base / "tasks" / status
            if not directory.exists():
                continue
            paths = sorted(directory.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
            for path in paths:
                rows.append((status, tenant, read_task(path), path))

    print("total", len(rows))
    for status in STATUSES:
        items = [row for row in rows if row[0] == status]
        if not items:
            continue
        print(f"\n[{status}] {len(items)}")
        for _, tenant, task, path in items[:50]:
            extra = []
            for key, label in [
                ("model_version", "model"),
                ("script_duration", "dur"),
                ("generation_state", "state"),
                ("queued_behind_task_id", "queued_after"),
                ("submit_id", "submit"),
            ]:
                value = task.get(key)
                if value:
                    extra.append(f"{label}={value}")
            if task.get("segment_count"):
                extra.append(f"seg={task.get('segment_index')}/{task.get('segment_count')}")
            if task.get("fail_reason"):
                fail = str(task.get("fail_reason")).replace("\n", " ")[:180]
                extra.append(f"fail={fail}")
            updated = task.get("updated_at") or task.get("created_at") or ""
            print(f"- {tenant} {task.get('task_id', path.stem)} {'; '.join(extra)} updated={updated}")


if __name__ == "__main__":
    main()
