#!/usr/bin/env python3
import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import List


ROOT = Path(__file__).resolve().parents[1]
TASKS = ROOT / "tasks" / "pending"
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
    parser.add_argument("--account", default=ENV.get("DEFAULT_JIMENG_ACCOUNT", ""), help="Jimeng account profile name.")
    parser.add_argument("--task-id", default="", help="Optional task id.")
    parser.add_argument("--dry-run", action="store_true", help="Print task JSON without writing it to tasks/pending.")
    args = parser.parse_args()

    prompt = Path(args.prompt).expanduser()
    if not prompt.exists():
        raise SystemExit(f"Prompt file does not exist: {prompt}")
    prompt_text = prompt.read_text(encoding="utf-8-sig").strip()
    if not prompt_text:
        raise SystemExit(f"Prompt file is empty: {prompt}")

    timestamp = datetime.now().strftime("%Y%m%d-%H%M")
    task_id = args.task_id or f"{timestamp}-{slug(prompt.stem)}"

    if args.image:
        images = [Path(item).expanduser() for item in args.image]
    else:
        images = select_images(Path(args.image_dir).expanduser(), prompt_text, task_id)

    for image in images:
        if not image.exists():
            raise SystemExit(f"Image file does not exist: {image}")

    TASKS.mkdir(parents=True, exist_ok=True)
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
        "jimeng_account": args.account,
        "status": "pending",
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    if args.dry_run:
        print(json.dumps(task, ensure_ascii=False, indent=2))
    else:
        path = TASKS / f"{task_id}.json"
        path.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
