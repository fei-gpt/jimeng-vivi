#!/usr/bin/env python3
import json
import shutil
import time
from datetime import datetime
from pathlib import Path


TENANT = "ou_402611cd232d4982f37708939a575452"
ROOT = Path("/app")
BASE = ROOT / "tenants" / TENANT
TASKS = BASE / "tasks"


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def read_task(status: str, task_id: str) -> dict:
    return json.loads((TASKS / status / f"{task_id}.json").read_text(encoding="utf-8-sig"))


def write_task(status: str, task: dict) -> None:
    task["status"] = status
    task["updated_at"] = now()
    target = TASKS / status / f"{task['task_id']}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")


def move_task(old_status: str, new_status: str, task: dict) -> None:
    source = TASKS / old_status / f"{task['task_id']}.json"
    backup = BASE / "maintenance_backups" / "20260601_0956_fix"
    backup.mkdir(parents=True, exist_ok=True)
    if source.exists():
        shutil.copy2(source, backup / f"{old_status}-{source.name}")
    write_task(new_status, task)
    if source.exists() and source.resolve() != (TASKS / new_status / f"{task['task_id']}.json").resolve():
        source.unlink()


def reset_failed_for_retry(task_id: str, reason: str) -> None:
    task = read_task("failed", task_id)
    task.pop("fail_reason", None)
    task.pop("retry_after_ts", None)
    task["card_approved"] = True
    task["last_retry_reason"] = reason
    task["retry_reset_at"] = now()
    move_task("failed", "reviewing", task)


def patch_park_prompt() -> None:
    task_id = "20260529-0956-park_bench_dare"
    task = read_task("failed", task_id)
    prompt_path = Path(task["prompt_file"])
    text = prompt_path.read_text(encoding="utf-8-sig")
    text = text.replace(
        '对朋友（画外）说：“You think it can’t roast me? Watch this.”',
        '对朋友（画外）说：“You think it can’t read me? Watch this.”',
    )
    text = text.replace(
        '“You’re literally asking a palm-sized object to bully you.”',
        '“You’re literally asking a palm-sized object to judge your choices.”',
    )
    prompt_path.write_text(text, encoding="utf-8")
    output_prompt = BASE / "outputs" / task_id / "prompt.txt"
    if output_prompt.exists():
        output_prompt.write_text(text, encoding="utf-8")
    task.pop("fail_reason", None)
    task.pop("retry_after_ts", None)
    task["card_approved"] = True
    task["last_retry_reason"] = "pre-TNS prompt softened and queued for retry"
    task["prompt_patched_at"] = now()
    move_task("failed", "reviewing", task)


def approve_reviewing(task_id: str) -> None:
    task = read_task("reviewing", task_id)
    task["card_approved"] = True
    task["card_approved_at"] = task.get("card_approved_at") or now()
    task["last_retry_reason"] = "approved during 0956 queue correction"
    task.pop("retry_after_ts", None)
    write_task("reviewing", task)


def main() -> None:
    reset_failed_for_retry("20260529-0956-couch_potato_confession", "ExceedConcurrencyLimit reset for retry")
    reset_failed_for_retry("20260529-0956-friendship_snatch", "ExceedConcurrencyLimit reset for retry")
    reset_failed_for_retry("20260529-0956-traffic_light_banter", "querying timeout reset for continued polling")
    patch_park_prompt()
    approve_reviewing("20260529-0956-farmer_market_negotiation")
    print("0956 task correction complete")


if __name__ == "__main__":
    main()
