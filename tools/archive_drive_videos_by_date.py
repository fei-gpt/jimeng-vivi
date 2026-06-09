#!/usr/bin/env python3
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from worker.feishu_worker import FeishuApi, log, read_users_config  # noqa: E402


def folder_name_for_video(name: str, fallback_timestamp: str = "") -> str:
    raw = str(name or "").strip()
    match = re.search(r"(?<!\d)(20\d{6})(?!\d)", raw)
    if match:
        return match.group(1)
    match = re.search(r"vivi-(\d{2})(\d{2})-", raw)
    if match:
        year = datetime.now().strftime("%Y")
        return f"{year}{match.group(1)}{match.group(2)}"
    timestamp = str(fallback_timestamp or "").strip()
    if timestamp.isdigit():
        try:
            return datetime.fromtimestamp(int(timestamp)).strftime("%Y%m%d")
        except (OverflowError, OSError, ValueError):
            pass
    return datetime.now().strftime("%Y%m%d")


def item_token(item: dict) -> str:
    return str(item.get("token") or item.get("file_token") or item.get("obj_token") or "").strip()


def archive_user_videos(api: FeishuApi, owner_open_id: str, ctx: dict, dry_run: bool = False) -> int:
    video_folder_token = str((ctx or {}).get("drive_video_folder_token") or "").strip()
    if not video_folder_token:
        return 0
    moved = 0
    for item in api.list_drive_folder_items(video_folder_token):
        item_type = str(item.get("type") or item.get("obj_type") or "").lower()
        name = str(item.get("name") or item.get("title") or "").strip()
        token = item_token(item)
        if item_type not in {"file", "media"} or not token or not name.lower().endswith(".mp4"):
            continue
        date_folder_name = folder_name_for_video(name, str(item.get("created_time") or item.get("modified_time") or ""))
        folder = api.ensure_drive_folder(video_folder_token, date_folder_name)
        target_token = str(folder.get("token") or folder.get("folder_token") or folder.get("file_token") or "").strip()
        if not target_token:
            continue
        print(f"{'DRY ' if dry_run else ''}MOVE {owner_open_id} {name} -> {date_folder_name}")
        if not dry_run:
            api.move_drive_file(token, target_token)
        moved += 1
    return moved


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    api = FeishuApi()
    users = read_users_config().get("users") or {}
    total = 0
    for owner_open_id, ctx in users.items():
        total += archive_user_videos(api, owner_open_id, ctx or {}, dry_run=dry_run)
    log(f"Archived Feishu drive videos by date: moved={total}; dry_run={dry_run}")
    print(f"moved={total}; dry_run={dry_run}")


if __name__ == "__main__":
    main()
