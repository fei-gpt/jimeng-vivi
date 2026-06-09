#!/usr/bin/env python3
from pathlib import Path

from worker.feishu_worker import FeishuApi, read_task, requeue_second_segment_with_next_reference_frame

TASK_ID = "vivi-0601-14-01-s02"
ROOT = Path("/app")

path = None
for tenant in (ROOT / "tenants").glob("*"):
    if not tenant.is_dir():
        continue
    for status in ["failed", "running", "reviewing", "done", "pending"]:
        candidate = tenant / "tasks" / status / f"{TASK_ID}.json"
        if candidate.exists():
            path = candidate
            break
    if path:
        break

if not path:
    print(f"not_found {TASK_ID}")
    raise SystemExit(1)

task = read_task(path)
print(f"found {path}")
print(f"status_file {path.parent.name}")
print(f"fail_reason {task.get('fail_reason')}")
ok = requeue_second_segment_with_next_reference_frame(
    task,
    FeishuApi(),
    str(task.get("fail_reason") or "manual requeue after pre-TNS"),
)
print(f"requeued {ok}")
