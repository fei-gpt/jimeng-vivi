#!/usr/bin/env python3
import json
from pathlib import Path

ROOT = Path("/app")
rows = []
roots = [ROOT]
tenants = ROOT / "tenants"
if tenants.exists():
    roots.extend(sorted(path for path in tenants.glob("*") if path.is_dir()))

for base in roots:
    tenant = "__root__" if base == ROOT else base.name
    outputs = base / "outputs"
    if not outputs.exists():
        continue
    for prompt in outputs.glob("*/prompt.txt"):
        try:
            text = prompt.read_text(encoding="utf-8-sig")
        except Exception:
            continue
        if "--image /" not in text:
            continue
        task_id = prompt.parent.name
        status = ""
        updated = ""
        for item in ["running", "done", "reviewing", "failed", "pending"]:
            path = base / "tasks" / item / f"{task_id}.json"
            if not path.exists():
                continue
            status = item
            try:
                task = json.loads(path.read_text(encoding="utf-8-sig"))
                updated = task.get("updated_at") or task.get("created_at") or ""
            except Exception:
                pass
            break
        rows.append((updated, tenant, status, task_id, str(prompt)))

rows.sort()
print("affected", len(rows))
for _, tenant, status, task_id, prompt in rows[-30:]:
    print(f"{tenant} | {status} | {task_id} | {prompt}")
