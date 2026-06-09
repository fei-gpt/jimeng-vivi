#!/usr/bin/env python3
import json
import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path("/app")
TASK_IDS = {"vivi-0601-09-03", "vivi-0601-09-04", "vivi-0601-10-01"}
TENANT = "ou_402611cd232d4982f37708939a575452"
base = ROOT / "tenants" / TENANT
stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
archive_root = base / "outputs_bad_image_prompt" / stamp
archive_root.mkdir(parents=True, exist_ok=True)

for task_id in sorted(TASK_IDS):
    task_path = None
    status = ""
    for item in ["running", "done", "reviewing", "failed", "pending"]:
        candidate = base / "tasks" / item / f"{task_id}.json"
        if candidate.exists():
            task_path = candidate
            status = item
            break
    if not task_path:
        print(f"missing {task_id}")
        continue
    task = json.loads(task_path.read_text(encoding="utf-8-sig"))
    output_dir = base / "outputs" / task_id
    if output_dir.exists():
        target = archive_root / task_id
        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(output_dir), str(target))
        print(f"archived {task_id} -> {target}")

    for key in [
        "submit_id",
        "output_dir",
        "video_file",
        "video_url",
        "feishu_video_file_token",
        "feishu_video_access",
        "downloaded_at",
        "download_file",
        "fail_reason",
        "retry_after_ts",
        "last_retry_reason",
        "progress_running_notified_at",
        "progress_success_notified_at",
        "progress_failed_notified_at",
        "dreamina_prompt_has_image_mention",
    ]:
        task.pop(key, None)
    task["card_approved"] = True
    task["card_approved_at"] = task.get("card_approved_at") or datetime.now().isoformat(timespec="seconds")
    task["regenerated_after_bad_image_prompt"] = datetime.now().isoformat(timespec="seconds")
    task["updated_at"] = datetime.now().isoformat(timespec="seconds")
    if int(task.get("segment_count") or 1) <= 1:
        task["generation_state"] = ""

    review_dir = base / "tasks" / "reviewing"
    review_dir.mkdir(parents=True, exist_ok=True)
    new_path = review_dir / f"{task_id}.json"
    new_path.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
    if task_path.exists() and task_path.resolve() != new_path.resolve():
        task_path.unlink()
    print(f"requeued {task_id} from {status}")
