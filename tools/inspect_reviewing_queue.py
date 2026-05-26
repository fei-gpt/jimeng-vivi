import json
from pathlib import Path

root = Path.home() / "okivivi"
for path in sorted((root / "tasks" / "reviewing").glob("*.json")):
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        print(path.name, "ERROR", exc)
        continue
    fields = {
        "task_id": data.get("task_id"),
        "status": data.get("status"),
        "jimeng_account": data.get("jimeng_account"),
        "review_auto_scan_enabled": data.get("review_auto_scan_enabled"),
        "review_confirm_value": data.get("review_confirm_value"),
        "retry_after_ts": data.get("retry_after_ts"),
        "last_retry_reason": data.get("last_retry_reason"),
    }
    print(path.name)
    print(json.dumps(fields, ensure_ascii=False, indent=2))
