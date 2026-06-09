#!/usr/bin/env python3
import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import List


ROOT = Path(__file__).resolve().parents[1]
TASKS = ROOT / "tasks" / "pending"
TENANTS = ROOT / "tenants"
DEFAULT_IMAGE_LIBRARY = ROOT / "vivi-image"

IMAGE_PAIRS = {
    "blue": ["okivivi-blue.jpg", "okivivi-blue1.jpg"],
    "pink": ["okivivi-pink.jpg", "okivivi-pink1.jpg"],
    "all": ["okivivi-blue.jpg", "okivivi-blue1.jpg", "okivivi-pink.jpg", "okivivi-pink1.jpg"],
}


def load_env(path: Path) -> dict:
    env = {}
    if not path.exists():
        return env
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


ENV = load_env(ROOT / ".env")


def clamp_duration(value: str) -> int:
    try:
        duration = int(float(value))
    except (TypeError, ValueError):
        duration = 15
    return max(4, min(15, duration))


def slug(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-")
    return cleaned[:40] or "task"


def tenant_slug(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value or "")).strip("-").lower()
    return cleaned[:80] or ""


def tenant_root(tenant_id: str) -> Path:
    cleaned = tenant_slug(tenant_id)
    return TENANTS / cleaned if cleaned else ROOT


def tenant_tasks_dir(tenant_id: str) -> Path:
    base = tenant_root(tenant_id)
    return TASKS if base == ROOT else base / "tasks" / "pending"


def task_id_exists(tenant_id: str, task_id: str) -> bool:
    base = tenant_root(tenant_id)
    root = ROOT / "tasks" if base == ROOT else base / "tasks"
    for status in ["pending", "reviewing", "running", "done", "failed", "needs_revision"]:
        if (root / status / f"{task_id}.json").exists():
            return True
    return False


def detect_variant(task_id: str, prompt_text: str) -> str:
    text = f"{task_id}\n{prompt_text}".lower()
    if "variant:all" in text:
        return "all"
    if "variant:pink" in text:
        return "pink"
    if "variant:blue" in text:
        return "blue"
    if ("bree" in text and "sunny" in text) or ("blue" in text and "pink" in text):
        return "all"
    if "pink" in text or "sunny" in text:
        return "pink"
    if "blue" in text or "vivi" in text or "bree" in text:
        return "blue"
    return ENV.get("DEFAULT_IMAGE_VARIANT", "blue").lower()


def select_images(image_dir: Path, prompt_text: str, task_id: str, count: int = 0) -> List[Path]:
    if not image_dir.exists():
        raise SystemExit(f"Image library does not exist: {image_dir}")
    if not image_dir.is_dir():
        raise SystemExit(f"Image library is not a directory: {image_dir}")

    variant = detect_variant(task_id, prompt_text)
    names = IMAGE_PAIRS.get(variant, IMAGE_PAIRS["blue"])
    images = [image_dir / name for name in names]
    missing = [str(image) for image in images if not image.exists()]
    if missing:
        raise SystemExit("Missing required reference image(s): " + ", ".join(missing))
    return images


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a local Jimeng video task JSON.")
    parser.add_argument("--prompt", required=True, help="UTF-8 prompt text file path, WSL path preferred.")
    parser.add_argument("--image", action="append", default=[], help="Reference image path. Repeat as needed.")
    parser.add_argument("--image-dir", default=ENV.get("IMAGE_LIBRARY_DIR", str(DEFAULT_IMAGE_LIBRARY)), help="Image library used when --image is omitted.")
    parser.add_argument("--image-count", default=ENV.get("DEFAULT_IMAGE_COUNT", "2"), help="Compatibility option; fixed pairs are used by default.")
    parser.add_argument("--duration", default="15", help="Duration in seconds, clamped to 4-15. Default: 15.")
    parser.add_argument("--account", default="", help="Jimeng account profile name.")
    parser.add_argument("--tenant-id", default=ENV.get("DEFAULT_TENANT_ID", ""), help="Tenant/user workspace id.")
    parser.add_argument("--owner-open-id", default="", help="Feishu sender open_id that owns this task.")
    parser.add_argument("--script-app-token", default="", help="Tenant script bitable app_token.")
    parser.add_argument("--script-table-id", default="", help="Tenant script bitable table_id.")
    parser.add_argument("--video-app-token", default="", help="Tenant video bitable app_token.")
    parser.add_argument("--video-table-id", default="", help="Tenant video bitable table_id.")
    parser.add_argument("--drive-video-folder-token", default="", help="Tenant video Drive folder token.")
    parser.add_argument("--drive-tables-folder-token", default="", help="Tenant table Drive folder token.")
    parser.add_argument("--task-id", default="", help="Optional task id.")
    parser.add_argument("--dry-run", action="store_true", help="Print task JSON without writing it to tasks/pending.")
    args = parser.parse_args()
    if args.owner_open_id and not args.account.strip():
        raise SystemExit("Missing Jimeng account for this Feishu user. Please add and save a Jimeng account first.")

    prompt = Path(args.prompt).expanduser()
    if not prompt.exists():
        raise SystemExit(f"Prompt file does not exist: {prompt}")
    prompt_text = prompt.read_text(encoding="utf-8-sig").strip()
    if not prompt_text:
        raise SystemExit(f"Prompt file is empty: {prompt}")

    timestamp = datetime.now().strftime("%Y%m%d-%H%M")
    task_id = args.task_id or f"{timestamp}-{slug(prompt.stem)}"
    duplicate_index = 2
    while task_id_exists(args.tenant_id, task_id):
        task_id = f"{args.task_id or timestamp + '-' + slug(prompt.stem)}-{duplicate_index}"
        duplicate_index += 1

    if args.image:
        images = [Path(item).expanduser() for item in args.image]
    else:
        images = select_images(Path(args.image_dir).expanduser(), prompt_text, task_id)

    for image in images:
        if not image.exists():
            raise SystemExit(f"Image file does not exist: {image}")

    tasks_dir = tenant_tasks_dir(args.tenant_id)
    tasks_dir.mkdir(parents=True, exist_ok=True)
    task = {
        "task_id": task_id,
        "prompt_file": str(prompt),
        "images": [str(image) for image in images],
        "image_source": "explicit" if args.image else "auto_pair",
        "image_library": "" if args.image else str(Path(args.image_dir).expanduser()),
        "duration": clamp_duration(args.duration),
        "ratio": "9:16",
        "model_version": "seedance2.0fast_vip",
        "video_resolution": "720p",
        "jimeng_account": args.account or ("" if args.owner_open_id else ENV.get("DEFAULT_JIMENG_ACCOUNT", "")),
        "tenant_id": args.tenant_id,
        "owner_open_id": args.owner_open_id,
        "user_script_app_token": args.script_app_token,
        "user_script_table_id": args.script_table_id,
        "user_video_app_token": args.video_app_token,
        "user_video_table_id": args.video_table_id,
        "drive_video_folder_token": args.drive_video_folder_token,
        "drive_tables_folder_token": args.drive_tables_folder_token,
        "data_isolation_level": "physical" if args.tenant_id else "legacy",
        "status": "pending",
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    if args.dry_run:
        print(json.dumps(task, ensure_ascii=False, indent=2))
    else:
        path = tasks_dir / f"{task_id}.json"
        path.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
