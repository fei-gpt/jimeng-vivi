#!/usr/bin/env python3
import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from create_task import ROOT, clamp_duration


REQUESTS = ROOT / "script_requests"
LOGS = ROOT / "logs" / "script_requests"


def now_id() -> str:
    return datetime.now().strftime("scriptreq-%Y%m%d-%H%M%S")


def write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Persist a text prompt request and generate OKIVIVI script tasks.")
    parser.add_argument("--count", default="1", help="Number of scripts to generate.")
    parser.add_argument("--duration", default="15", help="Video duration, clamped to 4-15 seconds.")
    parser.add_argument("--script-duration", default="", help="Script/story duration for DeepSeek, usually 15 or 30 seconds.")
    parser.add_argument("--brief", default="", help="Free-text generation requirement.")
    parser.add_argument("--source", default="local_text_input", help="Request source label.")
    parser.add_argument("--product-id", default="", help="Reserved for server product workflow.")
    parser.add_argument("--product-url", default="", help="Reserved for server product workflow.")
    parser.add_argument("--product-json", default="", help="Reserved product payload JSON string.")
    parser.add_argument("--image-variant", default="", help="Image group hint: auto, blue, pink, or all.")
    parser.add_argument("--character-mode", default="", help="Character mode: single_vivi or bree_sunny.")
    parser.add_argument("--model-version", default="", help="Dreamina multimodal model version.")
    parser.add_argument("--payload-file", default="", help="JSON payload file with count/duration/brief/product fields.")
    args = parser.parse_args()

    if args.payload_file:
        payload_path = Path(args.payload_file).expanduser()
        if not payload_path.is_absolute():
            payload_path = ROOT / payload_path
        payload = json.loads(payload_path.read_text(encoding="utf-8-sig"))
        args.count = str(payload.get("count") or args.count)
        args.duration = str(payload.get("duration") or args.duration)
        args.script_duration = str(payload.get("script_duration") or args.script_duration or "")
        args.brief = str(payload.get("brief") or args.brief or "")
        args.source = str(payload.get("source") or args.source)
        args.product_id = str(payload.get("product_id") or args.product_id or "")
        args.product_url = str(payload.get("product_url") or args.product_url or "")
        args.image_variant = str(payload.get("image_variant") or args.image_variant or "")
        args.character_mode = str(payload.get("character_mode") or args.character_mode or "")
        args.model_version = str(payload.get("model_version") or args.model_version or "")
        if payload.get("product_payload") and not args.product_json:
            args.product_json = json.dumps(payload.get("product_payload"), ensure_ascii=False)

    request_id = now_id()
    count = max(1, min(20, int(float(args.count or 1))))
    duration = clamp_duration(args.duration)
    script_duration = int(float(args.script_duration or args.duration or 15))
    script_duration = 30 if script_duration > 15 else 15
    brief = (args.brief or "").strip()

    product_payload: Any = ""
    if args.product_json.strip():
        try:
            product_payload = json.loads(args.product_json)
        except json.JSONDecodeError:
            product_payload = args.product_json.strip()

    request = {
        "request_id": request_id,
        "source": args.source,
        "count": count,
        "duration": duration,
        "script_duration": script_duration,
        "brief": brief,
        "product_id": args.product_id.strip(),
        "product_url": args.product_url.strip(),
        "product_payload": product_payload,
        "image_variant": args.image_variant.strip().lower(),
        "character_mode": args.character_mode.strip().lower(),
        "model_version": args.model_version.strip(),
        "status": "running",
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    request_path = REQUESTS / f"{request_id}.json"
    write_json(request_path, request)

    command = [
        sys.executable,
        "worker/generate_scripts.py",
        "--count",
        str(count),
        "--duration",
        str(duration),
        "--script-duration",
        str(script_duration),
    ]
    if brief:
        command += ["--brief", brief]
    image_variant = args.image_variant.strip().lower()
    if image_variant and image_variant != "auto":
        command += ["--image-variant", image_variant]
    character_mode = args.character_mode.strip().lower()
    if character_mode:
        command += ["--character-mode", character_mode]
    model_version = args.model_version.strip()
    if model_version:
        command += ["--model-version", model_version]

    LOGS.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        command,
        cwd=str(ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=360,
    )
    log_path = LOGS / f"{request_id}.log"
    log_path.write_text(proc.stdout, encoding="utf-8")

    created = [line.strip() for line in proc.stdout.splitlines() if line.strip().endswith(".json")]
    request["status"] = "done" if proc.returncode == 0 else "failed"
    request["completed_at"] = datetime.now().isoformat(timespec="seconds")
    request["created_tasks"] = created
    request["log_file"] = str(log_path)
    if proc.returncode != 0:
        request["error"] = proc.stdout[-2000:]
    write_json(request_path, request)

    print(f"request_id={request_id}")
    print(f"request_file={request_path}")
    print(f"log_file={log_path}")
    if created:
        print("created_tasks:")
        for item in created:
            print(item)
    if proc.returncode != 0:
        print(proc.stdout[-2000:], file=sys.stderr)
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
