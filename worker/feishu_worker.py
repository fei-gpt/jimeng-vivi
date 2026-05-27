#!/usr/bin/env python3
import json
import hashlib
import os
import re
import shutil
import subprocess
import threading
import time
import traceback
import urllib.error
import urllib.request
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple


ROOT = Path(__file__).resolve().parents[1]
TASK_DIRS = {
    "pending": ROOT / "tasks" / "pending",
    "reviewing": ROOT / "tasks" / "reviewing",
    "running": ROOT / "tasks" / "running",
    "done": ROOT / "tasks" / "done",
    "failed": ROOT / "tasks" / "failed",
    "needs_revision": ROOT / "tasks" / "needs_revision",
}
OUTPUTS = ROOT / "outputs"
LOGS = ROOT / "logs"
LOCKS = ROOT / "locks"
TENANTS = ROOT / "tenants"
REVIEW_TABLE_STATE = ROOT / "review_table_state.json"
BITABLE_STATE = ROOT / "bitable_state.json"
USERS_CONFIG = ROOT / "users.json"
WORKSPACE_INIT_LOCK = threading.Lock()
ACTION_DEDUPE_LOCK = threading.Lock()
ACTION_DEDUPE: Dict[str, float] = {}

MODEL_OPTIONS = [
    ("Seedance 2.0", "seedance2.0"),
    ("2.0 Fast", "seedance2.0fast"),
    ("2.0 VIP", "seedance2.0_vip"),
    ("Fast VIP", "seedance2.0fast_vip"),
]
SUPPORTED_MODELS = {value for _, value in MODEL_OPTIONS}


def load_env(path: Path) -> Dict[str, str]:
    env: Dict[str, str] = {}
    if not path.exists():
        return env
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


ENV = {**load_env(ROOT / ".env"), **os.environ}


def setting(name: str, default: str = "") -> str:
    return ENV.get(name, default)


def read_users_config() -> dict:
    if not USERS_CONFIG.exists():
        return {"users": {}}
    try:
        data = json.loads(USERS_CONFIG.read_text(encoding="utf-8-sig"))
    except Exception:
        return {"users": {}}
    if "users" not in data:
        data = {"users": data}
    if not isinstance(data.get("users"), dict):
        data["users"] = {}
    return data


def write_users_config(data: dict) -> None:
    USERS_CONFIG.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def default_tenant_id(open_id: str) -> str:
    return slug(open_id or setting("DEFAULT_TENANT_ID", "default"), "default")


def user_context(open_id: str = "") -> dict:
    users = read_users_config().get("users", {})
    configured = dict(users.get(open_id or "") or {})
    tenant_id = str(configured.get("tenant_id") or default_tenant_id(open_id)).strip()
    return {
        "tenant_id": tenant_id,
        "owner_open_id": open_id or "",
        "owner_name": str(configured.get("name") or tenant_id),
        "jimeng_account": str(configured.get("jimeng_account") or setting("DEFAULT_JIMENG_ACCOUNT", "")),
        "image_library": str(configured.get("image_library") or setting("IMAGE_LIBRARY_DIR", str(ROOT / "vivi-image"))),
        "script_app_token": str(configured.get("script_app_token") or ""),
        "script_table_id": str(configured.get("script_table_id") or ""),
        "video_app_token": str(configured.get("video_app_token") or ""),
        "video_table_id": str(configured.get("video_table_id") or ""),
        "script_url": str(configured.get("script_url") or ""),
        "video_url": str(configured.get("video_url") or ""),
        "enabled": configured.get("enabled", True),
    }


def ensure_user_config(open_id: str) -> dict:
    data = read_users_config()
    users = data.setdefault("users", {})
    if open_id and open_id not in users:
        users[open_id] = {
            "tenant_id": default_tenant_id(open_id),
            "name": default_tenant_id(open_id),
            "jimeng_account": setting("DEFAULT_JIMENG_ACCOUNT", ""),
            "image_library": setting("IMAGE_LIBRARY_DIR", str(ROOT / "vivi-image")),
            "enabled": True,
        }
        write_users_config(data)
        log(f"Created default user config for open_id={open_id}; tenant_id={users[open_id]['tenant_id']}")
    return user_context(open_id)


def update_user_config(open_id: str, updates: dict) -> dict:
    data = read_users_config()
    users = data.setdefault("users", {})
    if open_id:
        users.setdefault(open_id, {
            "tenant_id": default_tenant_id(open_id),
            "name": default_tenant_id(open_id),
            "enabled": True,
        })
        users[open_id].update({k: v for k, v in updates.items() if v is not None})
        write_users_config(data)
    return user_context(open_id)


def card_user_value(user_ctx: Optional[dict] = None) -> dict:
    ctx = user_ctx or user_context("")
    return {
        "tenant_id": ctx.get("tenant_id", ""),
        "owner_open_id": ctx.get("owner_open_id", ""),
        "jimeng_account": ctx.get("jimeng_account", ""),
        "image_library": ctx.get("image_library", ""),
    }


def card_user_context(value: Optional[dict]) -> dict:
    value = value or {}
    owner_open_id = str(
        value.get("owner_open_id")
        or value.get("actor_open_id")
        or value.get("operator_open_id")
        or value.get("click_open_id")
        or ""
    ).strip()
    ctx = user_context(owner_open_id)
    overrides = {
        "tenant_id": str(value.get("tenant_id") or ctx.get("tenant_id") or "").strip(),
        "owner_open_id": owner_open_id or str(ctx.get("owner_open_id") or "").strip(),
        "jimeng_account": str(value.get("jimeng_account") or ctx.get("jimeng_account") or "").strip(),
        "image_library": str(value.get("image_library") or ctx.get("image_library") or "").strip(),
    }
    ctx.update({k: v for k, v in overrides.items() if v})
    return ctx


def user_workspace_ready(user_ctx: Optional[dict]) -> bool:
    ctx = user_ctx or {}
    return all(
        str(ctx.get(key) or "").strip()
        for key in ["script_app_token", "script_table_id"]
    )


def user_workspace_available(api: "FeishuApi", user_ctx: Optional[dict]) -> bool:
    ctx = user_ctx or {}
    if not user_workspace_ready(ctx):
        return False
    state = user_configured_bitable_state(str(ctx.get("owner_open_id") or ""), ctx)
    if not state:
        return False
    try:
        ensure_bitable_control_fields(api, state)
        ensure_script_table_fields(api, state)
        return True
    except Exception as exc:
        log(f"Configured user workspace is unavailable: owner={ctx.get('owner_open_id', '')}; error={exc}")
        return False


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def timestamp_slug() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def log(message: str) -> None:
    LOGS.mkdir(parents=True, exist_ok=True)
    line = f"[{now()}] {message}"
    print(line, flush=True)
    with (LOGS / "worker.log").open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def action_seen_recently(key: str, ttl_seconds: int = 180) -> bool:
    now_ts = time.time()
    with ACTION_DEDUPE_LOCK:
        for cached_key, cached_at in list(ACTION_DEDUPE.items()):
            if now_ts - cached_at > ttl_seconds:
                ACTION_DEDUPE.pop(cached_key, None)
        if key in ACTION_DEDUPE:
            return True
        ACTION_DEDUPE[key] = now_ts
        return False


class UserRequestStateMachine:
    """Serialize side-effecting bot requests per user and drop exact duplicates."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: Dict[str, str] = {}
        self._queues: Dict[str, Deque[Tuple[str, str, Callable[[], None]]]] = {}

    def submit(self, user_key: str, signature: str, label: str, job: Callable[[], None]) -> str:
        user_key = user_key or "__unknown__"
        with self._lock:
            queue = self._queues.setdefault(user_key, deque())
            if self._active.get(user_key) == signature or any(item[0] == signature for item in queue):
                log(f"User request duplicate ignored: user={user_key}; label={label}; signature={signature}")
                return "duplicate"
            if self._active.get(user_key):
                queue.append((signature, label, job))
                log(f"User request queued: user={user_key}; label={label}; queue_size={len(queue)}")
                return "queued"
            self._active[user_key] = signature
        self._start(user_key, signature, label, job)
        log(f"User request started: user={user_key}; label={label}")
        return "started"

    def _start(self, user_key: str, signature: str, label: str, job: Callable[[], None]) -> None:
        def runner() -> None:
            try:
                job()
            except Exception as exc:
                log(f"User request job failed: user={user_key}; label={label}; error={exc}\n{traceback.format_exc()}")
            finally:
                self._complete(user_key, signature)

        threading.Thread(target=runner, daemon=True).start()

    def _complete(self, user_key: str, signature: str) -> None:
        next_item: Optional[Tuple[str, str, Callable[[], None]]] = None
        with self._lock:
            if self._active.get(user_key) == signature:
                self._active.pop(user_key, None)
            queue = self._queues.get(user_key)
            if queue:
                next_item = queue.popleft()
                self._active[user_key] = next_item[0]
                if not queue:
                    self._queues.pop(user_key, None)
            else:
                self._queues.pop(user_key, None)
        if next_item:
            next_signature, next_label, next_job = next_item
            log(f"User request dequeued: user={user_key}; label={next_label}")
            self._start(user_key, next_signature, next_label, next_job)


USER_REQUESTS = UserRequestStateMachine()


def request_signature(action: str, value: dict, keys: List[str]) -> str:
    payload = {"action": action}
    for key in keys:
        payload[key] = value.get(key)
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return action + ":" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def request_toast(state: str, started: str, queued: str = "已加入队列，将按顺序处理") -> dict:
    if state == "duplicate":
        return {"toast": {"type": "success", "content": "相同请求已在处理中，请勿重复提交"}}
    if state == "queued":
        return {"toast": {"type": "info", "content": queued}}
    return {"toast": {"type": "success", "content": started}}


def notify_text(api: "FeishuApi", text: str, open_id: str = "") -> None:
    target = str(open_id or "").strip()
    if target:
        api.text_to_open_id(target, text)
    else:
        api.text(text)


def notify_card(api: "FeishuApi", card: dict, open_id: str = "") -> None:
    target = str(open_id or "").strip()
    if target:
        api.card_to_open_id(target, card)
    else:
        api.card(card)


def notify_task_text(api: "FeishuApi", task: Optional[dict], text: str) -> None:
    notify_text(api, text, str((task or {}).get("owner_open_id") or ""))


def notify_task_card(api: "FeishuApi", task: Optional[dict], card: dict) -> None:
    notify_card(api, card, str((task or {}).get("owner_open_id") or ""))


def ensure_dirs() -> None:
    for path in TASK_DIRS.values():
        path.mkdir(parents=True, exist_ok=True)
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    LOCKS.mkdir(parents=True, exist_ok=True)
    TENANTS.mkdir(parents=True, exist_ok=True)


def recover_interrupted_running_tasks() -> None:
    for path in iter_task_paths("running"):
        try:
            task = read_task(path)
            task["last_retry_reason"] = "worker restarted while task was running"
            task["retry_after_ts"] = time.time() + int(setting("RESTART_RECOVERY_DELAY_SECONDS", "30"))
            move_task(path, "reviewing", task)
            log(f"Recovered interrupted running task into reviewing queue: {task.get('task_id')}")
        except Exception as exc:
            log(f"Failed to recover running task {path}: {exc}\n{traceback.format_exc()}")
    for path in (LOCKS / "jimeng").glob("*.lock"):
        try:
            try:
                data = json.loads(path.read_text(encoding="utf-8-sig"))
            except Exception:
                data = {}
            pid = data.get("pid")
            alive = False
            if isinstance(pid, int) and pid > 0:
                try:
                    os.kill(pid, 0)
                    alive = True
                except OSError:
                    alive = False
            if alive:
                log(f"Kept active Jimeng account lock: {path.name}; pid={pid}")
                continue
            path.unlink()
            log(f"Removed stale Jimeng account lock: {path.name}")
        except Exception as exc:
            log(f"Failed to remove stale Jimeng account lock {path}: {exc}")


def clamp_duration(value: Any) -> int:
    try:
        duration = int(float(value))
    except (TypeError, ValueError):
        duration = int(setting("DEFAULT_DURATION", "15"))
    return max(4, min(15, duration))


def normalize_model(value: Any) -> str:
    model = str(value or setting("DEFAULT_MODEL", "seedance2.0fast_vip")).strip()
    return model if model in SUPPORTED_MODELS else setting("DEFAULT_MODEL", "seedance2.0fast_vip")


def model_allows_parallel(value: Any) -> bool:
    model = normalize_model(value)
    parallel_models = {
        item.strip()
        for item in setting("PARALLEL_MODEL_VERSIONS", "seedance2.0_vip,seedance2.0fast_vip").split(",")
        if item.strip()
    }
    return model in parallel_models or model.endswith("_vip")


def slug(value: str, fallback: str = "manual") -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value or "")).strip("-").lower()
    return cleaned[:40] or fallback


def tenant_id_for_task(task: Optional[dict]) -> str:
    if not task:
        return ""
    return slug(str(task.get("tenant_id") or ""), "")


def tenant_root(task: Optional[dict]) -> Path:
    tenant_id = tenant_id_for_task(task)
    return TENANTS / tenant_id if tenant_id else ROOT


def task_dirs_for(task: Optional[dict] = None) -> Dict[str, Path]:
    base = tenant_root(task)
    if base == ROOT:
        return TASK_DIRS
    return {status: base / "tasks" / status for status in TASK_DIRS}


def task_dir(status: str, task: Optional[dict] = None) -> Path:
    return task_dirs_for(task)[status]


def output_dir_for_task(task: dict) -> Path:
    base = tenant_root(task)
    return (base / "outputs" / task["task_id"]) if base != ROOT else (OUTPUTS / task["task_id"])


def prompt_dir_for_task(task: dict, kind: str) -> Path:
    base = tenant_root(task)
    return base / "prompts" / kind


def tenant_log(task: Optional[dict], message: str) -> None:
    base = tenant_root(task)
    if base == ROOT:
        return
    log_dir = base / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    with (log_dir / "tasks.log").open("a", encoding="utf-8") as f:
        f.write(f"[{now()}] {message}\n")


def iter_status_dirs(status: str) -> List[Path]:
    dirs = [TASK_DIRS[status]]
    tenant_base = TENANTS
    if tenant_base.exists():
        for tenant in sorted(tenant_base.iterdir()):
            if tenant.is_dir():
                dirs.append(tenant / "tasks" / status)
    return dirs


def iter_task_paths(status: str) -> List[Path]:
    paths: List[Path] = []
    for directory in iter_status_dirs(status):
        if directory.exists():
            paths.extend(directory.glob("*.json"))
    return sorted(paths, key=lambda p: p.stat().st_mtime, reverse=True)


def http_json(method: str, url: str, payload: Optional[dict] = None, headers: Optional[dict] = None) -> dict:
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Content-Type", "application/json; charset=utf-8")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} {url}: {detail}") from exc


def http_multipart(method: str, url: str, fields: Dict[str, Any], files: Dict[str, Path], headers: Optional[dict] = None) -> dict:
    boundary = f"----okivivi-{uuid.uuid4().hex}"
    body = bytearray()

    def add_line(value: str = "") -> None:
        body.extend(value.encode("utf-8"))
        body.extend(b"\r\n")

    for name, value in fields.items():
        add_line(f"--{boundary}")
        add_line(f'Content-Disposition: form-data; name="{name}"')
        add_line()
        add_line(str(value))

    for name, path in files.items():
        file_path = Path(path)
        add_line(f"--{boundary}")
        add_line(
            f'Content-Disposition: form-data; name="{name}"; filename="{file_path.name}"'
        )
        add_line("Content-Type: application/octet-stream")
        add_line()
        body.extend(file_path.read_bytes())
        body.extend(b"\r\n")

    add_line(f"--{boundary}--")
    req = urllib.request.Request(url, data=bytes(body), method=method)
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} {url}: {detail}") from exc


class FeishuApi:
    def __init__(self) -> None:
        self.app_id = setting("FEISHU_APP_ID")
        self.app_secret = setting("FEISHU_APP_SECRET")
        self.base = setting("FEISHU_OPENAPI_BASE", "https://open.feishu.cn").rstrip("/")
        self.chat_id = setting("FEISHU_CHAT_ID")
        self.open_id = setting("FEISHU_BOT_OPEN_ID")
        self._token = ""
        self._token_expire_at = 0.0
        if not self.app_id or not self.app_secret:
            raise SystemExit("FEISHU_APP_ID and FEISHU_APP_SECRET are required in .env")
        if not self.chat_id and not self.open_id:
            log("FEISHU_CHAT_ID/FEISHU_BOT_OPEN_ID is empty; outbound default target is disabled. Send 'whoami' to the bot to discover your open_id.")

    def token(self) -> str:
        if self._token and time.time() < self._token_expire_at - 120:
            return self._token
        data = http_json(
            "POST",
            f"{self.base}/open-apis/auth/v3/tenant_access_token/internal",
            {"app_id": self.app_id, "app_secret": self.app_secret},
        )
        if data.get("code") != 0:
            raise RuntimeError(f"Failed to get Feishu token: {data}")
        self._token = data["tenant_access_token"]
        self._token_expire_at = time.time() + int(data.get("expire", 7200))
        return self._token

    def send(self, msg_type: str, content: dict) -> dict:
        receive_id_type = "chat_id" if self.chat_id else "open_id"
        receive_id = self.chat_id or self.open_id
        if not receive_id:
            raise RuntimeError("No FEISHU_CHAT_ID or FEISHU_BOT_OPEN_ID configured for outbound message.")
        return self.send_to(receive_id_type, receive_id, msg_type, content)

    def send_to(self, receive_id_type: str, receive_id: str, msg_type: str, content: dict) -> dict:
        payload = {
            "receive_id": receive_id,
            "msg_type": msg_type,
            "content": json.dumps(content, ensure_ascii=False),
        }
        return http_json(
            "POST",
            f"{self.base}/open-apis/im/v1/messages?receive_id_type={receive_id_type}",
            payload,
            {"Authorization": f"Bearer {self.token()}"},
        )

    def text(self, text: str) -> None:
        self.send("text", {"text": text})

    def text_to_open_id(self, open_id: str, text: str) -> None:
        self.send_to("open_id", open_id, "text", {"text": text})

    def card(self, card: dict) -> None:
        self.send("interactive", card)

    def card_to_open_id(self, open_id: str, card: dict) -> None:
        self.send_to("open_id", open_id, "interactive", card)

    def upload_file_to_drive(self, path: Path, name: str = "") -> dict:
        file_path = Path(path)
        if not file_path.exists():
            raise RuntimeError(f"Feishu upload file not found: {file_path}")
        folder_token = setting("FEISHU_DOC_FOLDER_TOKEN", "").strip()
        if not folder_token:
            raise RuntimeError("FEISHU_DOC_FOLDER_TOKEN is required to upload generated videos.")
        file_name = name or file_path.name
        data = http_multipart(
            "POST",
            f"{self.base}/open-apis/drive/v1/medias/upload_all",
            {
                "file_name": file_name,
                "parent_type": "explorer",
                "parent_node": folder_token,
                "size": file_path.stat().st_size,
            },
            {"file": file_path},
            {"Authorization": f"Bearer {self.token()}"},
        )
        if data.get("code") != 0:
            raise RuntimeError(f"Failed to upload file to Feishu drive: {data}")
        file_token = ((data.get("data") or {}).get("file_token") or "").strip()
        if not file_token:
            raise RuntimeError(f"Feishu upload response missing file_token: {data}")
        doc_base = setting("FEISHU_DOC_BASE", "https://ncnrqomkm3wb.feishu.cn").rstrip("/")
        return {
            "file_token": file_token,
            "url": f"{doc_base}/drive/file/{file_token}",
            "name": file_name,
        }

    def create_doc(self, title: str, content: str) -> dict:
        payload = {"title": title}
        folder_token = setting("FEISHU_DOC_FOLDER_TOKEN", "").strip()
        if folder_token:
            payload["folder_token"] = folder_token
        data = http_json(
            "POST",
            f"{self.base}/open-apis/docx/v1/documents",
            payload,
            {"Authorization": f"Bearer {self.token()}"},
        )
        if data.get("code") != 0:
            raise RuntimeError(f"Failed to create Feishu doc: {data}")
        document = (data.get("data") or {}).get("document") or {}
        document_id = document.get("document_id")
        revision_id = document.get("revision_id")
        if not document_id:
            raise RuntimeError(f"Feishu doc create response missing document_id: {data}")
        self.append_doc_text(document_id, revision_id, content)
        doc_base = setting("FEISHU_DOC_BASE", "https://waytoagi.feishu.cn").rstrip("/")
        return {
            "document_id": document_id,
            "revision_id": revision_id,
            "url": f"{doc_base}/docx/{document_id}",
        }

    def create_review_table_doc(self, title: str, original_prompt: str) -> dict:
        payload = {"title": title}
        folder_token = setting("FEISHU_DOC_FOLDER_TOKEN", "").strip()
        if folder_token:
            payload["folder_token"] = folder_token
        data = http_json(
            "POST",
            f"{self.base}/open-apis/docx/v1/documents",
            payload,
            {"Authorization": f"Bearer {self.token()}"},
        )
        if data.get("code") != 0:
            raise RuntimeError(f"Failed to create Feishu doc: {data}")
        document = (data.get("data") or {}).get("document") or {}
        document_id = document.get("document_id")
        revision_id = document.get("revision_id")
        if not document_id:
            raise RuntimeError(f"Feishu doc create response missing document_id: {data}")

        headers = ["原文案", "修改文案", "确认", "视频链接"]
        row = [original_prompt, original_prompt, "待确认", "待生成"]
        descendants = [
            {
                "block_id": "review_table",
                "block_type": 31,
                "table": {
                    "property": {
                        "row_size": 2,
                        "column_size": 4,
                        "column_width": [320, 420, 120, 240],
                        "header_row": True,
                    }
                },
                "children": [f"cell_{idx}" for idx in range(8)],
            }
        ]
        for idx, content in enumerate(headers + row):
            text_id = f"cell_{idx}_text"
            text_key = "text"
            descendants.append(
                {
                    "block_id": f"cell_{idx}",
                    "block_type": 32,
                    "table_cell": {},
                    "children": [text_id],
                }
            )
            descendants.append(
                {
                    "block_id": text_id,
                    "block_type": 2,
                    text_key: {
                        "elements": [
                            {
                                "text_run": {
                                    "content": content or " ",
                                    "text_element_style": {"bold": idx < 4},
                                }
                            }
                        ]
                    },
                    "children": [],
                }
            )
        body = {"index": 0, "children_id": ["review_table"], "descendants": descendants}
        query = f"?document_revision_id={revision_id}" if revision_id is not None else ""
        table_data = http_json(
            "POST",
            f"{self.base}/open-apis/docx/v1/documents/{document_id}/blocks/{document_id}/descendant{query}",
            body,
            {"Authorization": f"Bearer {self.token()}"},
        )
        if table_data.get("code") != 0:
            raise RuntimeError(f"Failed to create Feishu review table: {table_data}")
        relations = ((table_data.get("data") or {}).get("block_id_relations") or [])
        block_ids = {item.get("temporary_block_id"): item.get("block_id") for item in relations}
        doc_base = setting("FEISHU_DOC_BASE", "https://waytoagi.feishu.cn").rstrip("/")
        return {
            "document_id": document_id,
            "revision_id": revision_id,
            "url": f"{doc_base}/docx/{document_id}",
            "table_response": table_data,
            "table_cell_ids": block_ids,
            "modified_text_cell_id": block_ids.get("cell_5", "cell_5"),
            "confirm_cell_id": block_ids.get("cell_6", "cell_6"),
            "video_cell_id": block_ids.get("cell_7", "cell_7"),
        }

    def create_shared_review_table_doc(self, title: str, total_rows: int) -> dict:
        total_rows = max(2, int(total_rows))
        payload = {"title": title}
        folder_token = setting("FEISHU_DOC_FOLDER_TOKEN", "").strip()
        if folder_token:
            payload["folder_token"] = folder_token
        data = http_json(
            "POST",
            f"{self.base}/open-apis/docx/v1/documents",
            payload,
            {"Authorization": f"Bearer {self.token()}"},
        )
        if data.get("code") != 0:
            raise RuntimeError(f"Failed to create Feishu doc: {data}")
        document = (data.get("data") or {}).get("document") or {}
        document_id = document.get("document_id")
        revision_id = document.get("revision_id")
        if not document_id:
            raise RuntimeError(f"Feishu doc create response missing document_id: {data}")

        headers = ["原文案", "修改文案", "确认", "视频链接"]
        cell_count = total_rows * 4
        descendants = [
            {
                "block_id": "review_table",
                "block_type": 31,
                "table": {
                    "property": {
                        "row_size": total_rows,
                        "column_size": 4,
                        "column_width": [320, 420, 120, 260],
                        "header_row": True,
                    }
                },
                "children": [f"cell_{idx}" for idx in range(cell_count)],
            }
        ]
        for idx in range(cell_count):
            children = [f"cell_{idx}_text"]
            descendants.append(
                {
                    "block_id": f"cell_{idx}",
                    "block_type": 32,
                    "table_cell": {},
                    "children": children,
                }
            )
            content = headers[idx] if idx < 4 else " "
            descendants.append(
                {
                    "block_id": f"cell_{idx}_text",
                    "block_type": 2,
                    "text": {
                        "elements": [
                            {
                                "text_run": {
                                    "content": content,
                                    "text_element_style": {"bold": idx < 4},
                                }
                            }
                        ]
                    },
                    "children": [],
                }
            )
        body = {"index": 0, "children_id": ["review_table"], "descendants": descendants}
        query = f"?document_revision_id={revision_id}" if revision_id is not None else ""
        table_data = http_json(
            "POST",
            f"{self.base}/open-apis/docx/v1/documents/{document_id}/blocks/{document_id}/descendant{query}",
            body,
            {"Authorization": f"Bearer {self.token()}"},
        )
        if table_data.get("code") != 0:
            raise RuntimeError(f"Failed to create Feishu shared review table: {table_data}")
        relations = ((table_data.get("data") or {}).get("block_id_relations") or [])
        block_ids = {item.get("temporary_block_id"): item.get("block_id") for item in relations}
        rows = []
        for row_index in range(1, total_rows):
            base = row_index * 4
            rows.append(
                {
                    "row_index": row_index,
                    "original_cell_id": block_ids.get(f"cell_{base}"),
                    "modified_cell_id": block_ids.get(f"cell_{base + 1}"),
                    "confirm_cell_id": block_ids.get(f"cell_{base + 2}"),
                    "video_cell_id": block_ids.get(f"cell_{base + 3}"),
                    "task_id": "",
                }
            )
        doc_base = setting("FEISHU_DOC_BASE", "https://waytoagi.feishu.cn").rstrip("/")
        return {
            "document_id": document_id,
            "revision_id": revision_id,
            "url": f"{doc_base}/docx/{document_id}",
            "title": title,
            "total_rows": total_rows,
            "next_row": 1,
            "rows": rows,
            "created_at": now(),
        }

    def append_text_to_cell(self, document_id: str, cell_id: str, content: str) -> None:
        payload = {
            "children": [
                {
                    "block_type": 2,
                    "text": {"elements": [{"text_run": {"content": content or " "}}]},
                }
            ],
            "index": -1,
        }
        data = http_json(
            "POST",
            f"{self.base}/open-apis/docx/v1/documents/{document_id}/blocks/{cell_id}/children?document_revision_id=-1",
            payload,
            {"Authorization": f"Bearer {self.token()}"},
        )
        if data.get("code") != 0:
            raise RuntimeError(f"Failed to write Feishu table cell: {data}")

    def block_text(self, document_id: str, block_id: str) -> str:
        chunks: List[str] = []
        page_token = ""
        while True:
            query = f"?page_size=500&with_descendants=true"
            if page_token:
                query += f"&page_token={page_token}"
            data = http_json(
                "GET",
                f"{self.base}/open-apis/docx/v1/documents/{document_id}/blocks/{block_id}/children{query}",
                None,
                {"Authorization": f"Bearer {self.token()}"},
            )
            if data.get("code") != 0:
                raise RuntimeError(f"Failed to read Feishu block children: {data}")
            for item in (data.get("data") or {}).get("items") or []:
                text = item.get("text") or item.get("page") or {}
                for element in text.get("elements") or []:
                    run = element.get("text_run") or {}
                    if "content" in run:
                        chunks.append(str(run.get("content") or ""))
            if not (data.get("data") or {}).get("has_more"):
                break
            page_token = (data.get("data") or {}).get("page_token") or ""
            if not page_token:
                break
        return "\n".join(chunk.rstrip("\n") for chunk in chunks).strip()

    def create_review_bitable(self, title: str = "") -> dict:
        folder_token = setting("FEISHU_DOC_FOLDER_TOKEN", "").strip()
        title = title or setting("FEISHU_BITABLE_TITLE", "即梦视频工作流表")
        payload = {"name": title, "time_zone": "Asia/Shanghai"}
        if folder_token:
            payload["folder_token"] = folder_token
        data = http_json(
            "POST",
            f"{self.base}/open-apis/bitable/v1/apps",
            payload,
            {"Authorization": f"Bearer {self.token()}"},
        )
        if data.get("code") != 0:
            raise RuntimeError(f"Failed to create Feishu bitable app: {data}")
        app = (data.get("data") or {}).get("app") or {}
        app_token = app.get("app_token")
        if not app_token:
            raise RuntimeError(f"Feishu bitable create response missing app_token: {data}")
        headers = [
            {"field_name": "任务ID", "type": 1},
            {"field_name": "文案", "type": 1},
            {"field_name": "对话中文", "type": 1},
            {
                "field_name": "图片",
                "type": 3,
                "property": {
                    "options": [
                        {"name": "blue", "color": 2},
                        {"name": "pink", "color": 5},
                        {"name": "all", "color": 0},
                    ]
                },
            },
            {
                "field_name": "模型",
                "type": 3,
                "property": {
                    "options": [
                        {"name": "seedance2.0", "color": 0},
                        {"name": "seedance2.0fast", "color": 2},
                        {"name": "seedance2.0_vip", "color": 3},
                        {"name": "seedance2.0fast_vip", "color": 4},
                    ]
                },
            },
            {"field_name": "备注", "type": 1},
            {
                "field_name": "确认",
                "type": 3,
                "property": {"options": [{"name": "确认", "color": 2}, {"name": "待修改", "color": 5}]},
            },
            {"field_name": "状态", "type": 1},
            {"field_name": "视频链接", "type": 1},
            {"field_name": "错误原因", "type": 1},
        ]
        table_data = http_json(
            "POST",
            f"{self.base}/open-apis/bitable/v1/apps/{app_token}/tables",
            {"table": {"name": "工作流", "default_view_name": "表格", "fields": headers}},
            {"Authorization": f"Bearer {self.token()}"},
        )
        if table_data.get("code") != 0:
            raise RuntimeError(f"Failed to create Feishu bitable table: {table_data}")
        table_id = (table_data.get("data") or {}).get("table_id")
        if not table_id:
            raise RuntimeError(f"Feishu bitable table response missing table_id: {table_data}")
        self.delete_default_data_tables(app_token, keep_table_id=table_id)
        return {
            "app_token": app_token,
            "table_id": table_id,
            "url": app.get("url") or f"{setting('FEISHU_DOC_BASE', 'https://ncnrqomkm3wb.feishu.cn').rstrip('/')}/base/{app_token}",
            "title": title,
            "created_at": now(),
            "combined_bitable": True,
            "script_app_token": app_token,
            "script_table_id": table_id,
            "script_url": app.get("url") or f"{setting('FEISHU_DOC_BASE', 'https://ncnrqomkm3wb.feishu.cn').rstrip('/')}/base/{app_token}",
        }

    def create_script_bitable(self, title: str = "") -> dict:
        folder_token = setting("FEISHU_DOC_FOLDER_TOKEN", "").strip()
        title = title or setting("FEISHU_SCRIPT_BITABLE_TITLE", "即梦视频文案库")
        payload = {"name": title, "time_zone": "Asia/Shanghai"}
        if folder_token:
            payload["folder_token"] = folder_token
        data = http_json(
            "POST",
            f"{self.base}/open-apis/bitable/v1/apps",
            payload,
            {"Authorization": f"Bearer {self.token()}"},
        )
        if data.get("code") != 0:
            raise RuntimeError(f"Failed to create Feishu script bitable app: {data}")
        app = (data.get("data") or {}).get("app") or {}
        app_token = app.get("app_token")
        if not app_token:
            raise RuntimeError(f"Feishu script bitable create response missing app_token: {data}")
        table = self.create_bitable_table(
            app_token,
            "文案库",
            [
                {"field_name": "任务ID", "type": 1},
                {"field_name": "文案", "type": 1},
                {
                    "field_name": "确认",
                    "type": 3,
                    "property": {"options": [{"name": "确认", "color": 2}, {"name": "待修改", "color": 5}]},
                },
                {
                    "field_name": "图片",
                    "type": 3,
                    "property": {
                        "options": [
                            {"name": "blue", "color": 2},
                            {"name": "pink", "color": 5},
                            {"name": "all", "color": 0},
                        ]
                    },
                },
                {
                    "field_name": "模型",
                    "type": 3,
                    "property": {
                        "options": [
                            {"name": "seedance2.0", "color": 0},
                            {"name": "seedance2.0fast", "color": 2},
                            {"name": "seedance2.0_vip", "color": 3},
                            {"name": "seedance2.0fast_vip", "color": 4},
                        ]
                    },
                },
                {"field_name": "备注", "type": 1},
            ],
        )
        self.delete_default_data_tables(app_token, keep_table_id=table["table_id"])
        return {
            "script_app_token": app_token,
            "script_table_id": table["table_id"],
            "script_url": app.get("url") or f"{setting('FEISHU_DOC_BASE', 'https://ncnrqomkm3wb.feishu.cn').rstrip('/')}/base/{app_token}",
            "script_title": title,
            "script_created_at": now(),
        }

    def create_bitable_table(self, app_token: str, name: str, fields: List[dict]) -> dict:
        data = http_json(
            "POST",
            f"{self.base}/open-apis/bitable/v1/apps/{app_token}/tables",
            {"table": {"name": name, "default_view_name": "表格", "fields": fields}},
            {"Authorization": f"Bearer {self.token()}"},
        )
        if data.get("code") != 0:
            raise RuntimeError(f"Failed to create Feishu bitable table {name}: {data}")
        table_id = (data.get("data") or {}).get("table_id")
        if not table_id:
            raise RuntimeError(f"Feishu bitable table response missing table_id: {data}")
        return {"table_id": table_id}

    def delete_bitable_table(self, app_token: str, table_id: str) -> None:
        data = http_json(
            "DELETE",
            f"{self.base}/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}",
            None,
            {"Authorization": f"Bearer {self.token()}"},
        )
        if data.get("code") != 0:
            raise RuntimeError(f"Failed to delete Feishu bitable table {table_id}: {data}")

    def delete_default_data_tables(self, app_token: str, keep_table_id: str = "") -> None:
        try:
            for table in self.list_bitable_tables(app_token):
                table_id = str(table.get("table_id") or table.get("id") or table.get("tableId") or "")
                name = str(table.get("name") or table.get("table_name") or table.get("tableName") or "").strip()
                normalized = name.replace(" ", "").lower()
                if table_id and table_id != keep_table_id and normalized in {"数据表", "table1"}:
                    self.delete_bitable_table(app_token, table_id)
                    log(f"Deleted default bitable table: {name} {table_id}")
        except Exception as exc:
            log(f"Failed to delete default data table for {app_token}: {exc}")

    def list_bitable_tables(self, app_token: str) -> List[dict]:
        data = http_json(
            "GET",
            f"{self.base}/open-apis/bitable/v1/apps/{app_token}/tables?page_size=100",
            None,
            {"Authorization": f"Bearer {self.token()}"},
        )
        if data.get("code") != 0:
            raise RuntimeError(f"Failed to list Feishu bitable tables: {data}")
        return (data.get("data") or {}).get("items") or []

    def create_bitable_record(self, app_token: str, table_id: str, fields: dict) -> dict:
        data = http_json(
            "POST",
            f"{self.base}/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records",
            {"fields": fields},
            {"Authorization": f"Bearer {self.token()}"},
        )
        if data.get("code") != 0:
            raise RuntimeError(f"Failed to create Feishu bitable record: {data}")
        record = (data.get("data") or {}).get("record") or {}
        record_id = record.get("record_id") or record.get("id")
        if not record_id:
            raise RuntimeError(f"Feishu bitable record response missing record_id: {data}")
        return record

    def get_bitable_record(self, app_token: str, table_id: str, record_id: str) -> dict:
        data = http_json(
            "GET",
            f"{self.base}/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}",
            None,
            {"Authorization": f"Bearer {self.token()}"},
        )
        if data.get("code") != 0:
            raise RuntimeError(f"Failed to read Feishu bitable record: {data}")
        return (data.get("data") or {}).get("record") or {}

    def list_bitable_records(self, app_token: str, table_id: str) -> List[dict]:
        items: List[dict] = []
        page_token = ""
        while True:
            query = "?page_size=100"
            if page_token:
                query += f"&page_token={page_token}"
            data = http_json(
                "GET",
                f"{self.base}/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records{query}",
                None,
                {"Authorization": f"Bearer {self.token()}"},
            )
            if data.get("code") != 0:
                raise RuntimeError(f"Failed to list Feishu bitable records: {data}")
            body = data.get("data") or {}
            items.extend(body.get("items") or [])
            if not body.get("has_more"):
                break
            page_token = body.get("page_token") or ""
            if not page_token:
                break
        return items

    def update_bitable_record(self, app_token: str, table_id: str, record_id: str, fields: dict) -> None:
        data = http_json(
            "PUT",
            f"{self.base}/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}",
            {"fields": fields},
            {"Authorization": f"Bearer {self.token()}"},
        )
        if data.get("code") != 0:
            raise RuntimeError(f"Failed to update Feishu bitable record: {data}")

    def delete_bitable_record(self, app_token: str, table_id: str, record_id: str) -> None:
        data = http_json(
            "DELETE",
            f"{self.base}/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}",
            None,
            {"Authorization": f"Bearer {self.token()}"},
        )
        if data.get("code") != 0:
            raise RuntimeError(f"Failed to delete Feishu bitable record: {data}")

    def list_bitable_fields(self, app_token: str, table_id: str) -> List[dict]:
        items: List[dict] = []
        page_token = ""
        while True:
            query = "?page_size=100"
            if page_token:
                query += f"&page_token={page_token}"
            data = http_json(
                "GET",
                f"{self.base}/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/fields{query}",
                None,
                {"Authorization": f"Bearer {self.token()}"},
            )
            if data.get("code") != 0:
                raise RuntimeError(f"Failed to list Feishu bitable fields: {data}")
            body = data.get("data") or {}
            items.extend(body.get("items") or [])
            if not body.get("has_more"):
                break
            page_token = body.get("page_token") or ""
            if not page_token:
                break
        return items

    def list_bitable_views(self, app_token: str, table_id: str) -> List[dict]:
        items: List[dict] = []
        page_token = ""
        while True:
            query = "?page_size=100"
            if page_token:
                query += f"&page_token={page_token}"
            data = http_json(
                "GET",
                f"{self.base}/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/views{query}",
                None,
                {"Authorization": f"Bearer {self.token()}"},
            )
            if data.get("code") != 0:
                raise RuntimeError(f"Failed to list Feishu bitable views: {data}")
            body = data.get("data") or {}
            items.extend(body.get("items") or [])
            if not body.get("has_more"):
                break
            page_token = body.get("page_token") or ""
            if not page_token:
                break
        return items

    def update_bitable_view_task_sort(self, app_token: str, table_id: str, view_id: str, task_field_id: str) -> None:
        data = http_json(
            "PATCH",
            f"{self.base}/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/views/{view_id}",
            {"property": {"sortInfo": [{"fieldId": task_field_id, "desc": True}]}},
            {"Authorization": f"Bearer {self.token()}"},
        )
        if data.get("code") != 0:
            raise RuntimeError(f"Failed to update Feishu bitable view sort: {data}")

    def delete_bitable_field(self, app_token: str, table_id: str, field_id: str) -> None:
        data = http_json(
            "DELETE",
            f"{self.base}/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/fields/{field_id}",
            None,
            {"Authorization": f"Bearer {self.token()}"},
        )
        if data.get("code") != 0:
            raise RuntimeError(f"Failed to delete Feishu bitable field: {data}")

    def create_bitable_text_field(self, app_token: str, table_id: str, field_name: str) -> None:
        data = http_json(
            "POST",
            f"{self.base}/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/fields",
            {"field_name": field_name, "type": 1},
            {"Authorization": f"Bearer {self.token()}"},
        )
        if data.get("code") != 0:
            raise RuntimeError(f"Failed to create Feishu bitable field {field_name}: {data}")

    def create_bitable_choice_field(self, app_token: str, table_id: str, field_name: str, options: List[dict]) -> None:
        data = http_json(
            "POST",
            f"{self.base}/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/fields",
            {
                "field_name": field_name,
                "type": 3,
                "property": {"options": options},
            },
            {"Authorization": f"Bearer {self.token()}"},
        )
        if data.get("code") != 0:
            raise RuntimeError(f"Failed to create Feishu bitable choice field {field_name}: {data}")

    def create_bitable_image_choice_field(self, app_token: str, table_id: str) -> None:
        self.create_bitable_choice_field(
            app_token,
            table_id,
            "图片",
            [
                {"name": "blue", "color": 2},
                {"name": "pink", "color": 5},
                {"name": "all", "color": 0},
            ],
        )

    def append_doc_text(self, document_id: str, revision_id: Optional[int], content: str) -> None:
        children = []
        paragraphs = content.replace("\r\n", "\n").split("\n")
        for paragraph in paragraphs:
            children.append(
                {
                    "block_type": 2,
                    "text": {
                        "elements": [
                            {
                                "text_run": {
                                    "content": paragraph or " ",
                                }
                            }
                        ]
                    },
                }
            )
        payload = {"children": children, "index": 0}
        query = f"?document_revision_id={revision_id}" if revision_id is not None else ""
        data = http_json(
            "POST",
            f"{self.base}/open-apis/docx/v1/documents/{document_id}/blocks/{document_id}/children{query}",
            payload,
            {"Authorization": f"Bearer {self.token()}"},
        )
        if data.get("code") != 0:
            raise RuntimeError(f"Failed to write Feishu doc content: {data}")

    def append_video_link_to_doc(self, document_id: str, cell_id: str, video_link: str) -> None:
        payload = {
            "children": [
                {
                    "block_type": 2,
                    "text": {"elements": [{"text_run": {"content": video_link}}]},
                }
            ],
            "index": -1,
        }
        data = http_json(
            "POST",
            f"{self.base}/open-apis/docx/v1/documents/{document_id}/blocks/{cell_id}/children?document_revision_id=-1",
            payload,
            {"Authorization": f"Bearer {self.token()}"},
        )
        if data.get("code") != 0:
            raise RuntimeError(f"Failed to write video link into Feishu doc table: {data}")

    def doc_raw_content(self, document_id: str) -> str:
        data = http_json(
            "GET",
            f"{self.base}/open-apis/docx/v1/documents/{document_id}/raw_content",
            None,
            {"Authorization": f"Bearer {self.token()}"},
        )
        if data.get("code") != 0:
            raise RuntimeError(f"Failed to read Feishu doc content: {data}")
        return ((data.get("data") or {}).get("content") or "").strip()


def task_path(status: str, task_id: str, task: Optional[dict] = None) -> Path:
    return task_dir(status, task) / f"{task_id}.json"


def read_task(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_task(status: str, task: dict) -> Path:
    task["status"] = status
    task["updated_at"] = now()
    path = task_path(status, task["task_id"], task)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
    tenant_log(task, f"{task['task_id']} -> {status}")
    return path


def status_for_task(task_id: str) -> Optional[str]:
    for status in TASK_DIRS:
        for directory in iter_status_dirs(status):
            if (directory / f"{task_id}.json").exists():
                return status
    return None


def move_task(from_path: Path, status: str, task: dict) -> Path:
    new_path = write_task(status, task)
    if from_path.exists() and from_path.resolve() != new_path.resolve():
        from_path.unlink()
    return new_path


def read_review_table_state() -> dict:
    if not REVIEW_TABLE_STATE.exists():
        return {}
    return json.loads(REVIEW_TABLE_STATE.read_text(encoding="utf-8-sig"))


def write_review_table_state(state: dict) -> None:
    REVIEW_TABLE_STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def ensure_shared_review_table(api: FeishuApi) -> dict:
    state = read_review_table_state()
    if state.get("document_id") and state.get("rows"):
        return state
    title = setting("FEISHU_REVIEW_TABLE_TITLE", "即梦视频生成审核总表")
    total_rows = int(setting("FEISHU_REVIEW_TABLE_ROWS", "200"))
    state = api.create_shared_review_table_doc(title, total_rows)
    write_review_table_state(state)
    log(f"Created shared Feishu review table: {state.get('url')}")
    return state


def read_bitable_state() -> dict:
    if not BITABLE_STATE.exists():
        return {}
    return json.loads(BITABLE_STATE.read_text(encoding="utf-8-sig"))


def write_bitable_state(state: dict) -> None:
    BITABLE_STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def configured_bitable_state(task: Optional[dict]) -> dict:
    if not task:
        return {}
    script_app_token = task.get("user_script_app_token") or task.get("configured_script_app_token")
    script_table_id = task.get("user_script_table_id") or task.get("configured_script_table_id")
    video_app_token = task.get("user_video_app_token") or task.get("configured_video_app_token") or script_app_token
    video_table_id = task.get("user_video_table_id") or task.get("configured_video_table_id") or script_table_id
    if not (script_app_token and script_table_id):
        return {}
    base = setting("FEISHU_DOC_BASE", "https://ncnrqomkm3wb.feishu.cn").rstrip("/")
    return {
        "app_token": script_app_token,
        "table_id": script_table_id,
        "url": f"{base}/base/{script_app_token}",
        "script_app_token": script_app_token,
        "script_table_id": script_table_id,
        "script_url": f"{base}/base/{script_app_token}",
        "tenant_id": task.get("tenant_id", ""),
        "owner_open_id": task.get("owner_open_id", ""),
        "source": "user_config",
    }


def user_configured_bitable_state(open_id: str, configured: dict) -> dict:
    script_app_token = str(configured.get("script_app_token") or "").strip()
    script_table_id = str(configured.get("script_table_id") or "").strip()
    video_app_token = str(configured.get("video_app_token") or script_app_token or "").strip()
    video_table_id = str(configured.get("video_table_id") or script_table_id or "").strip()
    if not (script_app_token and script_table_id):
        return {}
    base = setting("FEISHU_DOC_BASE", "https://ncnrqomkm3wb.feishu.cn").rstrip("/")
    return {
        "app_token": script_app_token,
        "table_id": script_table_id,
        "url": str(configured.get("script_url") or configured.get("video_url") or f"{base}/base/{script_app_token}"),
        "script_app_token": script_app_token,
        "script_table_id": script_table_id,
        "script_url": str(configured.get("script_url") or f"{base}/base/{script_app_token}"),
        "tenant_id": str(configured.get("tenant_id") or default_tenant_id(open_id)),
        "owner_open_id": open_id,
        "source": "user_config",
    }


def ensure_review_bitable(api: FeishuApi, task: Optional[dict] = None) -> dict:
    configured = configured_bitable_state(task)
    if configured:
        ensure_bitable_control_fields(api, configured)
        ensure_script_table_fields(api, configured)
        return configured
    state = read_bitable_state()
    if state.get("app_token") and state.get("table_id") and state.get("script_app_token") and state.get("script_table_id"):
        ensure_bitable_control_fields(api, state)
        ensure_script_table_fields(api, state)
        return state
    if state.get("app_token") and state.get("table_id"):
        state = ensure_script_bitable(api, state)
        ensure_bitable_control_fields(api, state)
        ensure_script_table_fields(api, state)
        write_bitable_state(state)
        return state
    state = api.create_review_bitable()
    write_bitable_state(state)
    log(f"Created Feishu bitable review table: {state.get('url')}")
    return state


def initialize_user_workspace(api: FeishuApi, user_ctx: dict) -> dict:
    owner_open_id = str(user_ctx.get("owner_open_id") or "").strip()
    if not owner_open_id:
        raise RuntimeError("无法识别当前用户 open_id，不能初始化个人工作区。")
    with WORKSPACE_INIT_LOCK:
        current_ctx = user_context(owner_open_id)
        if user_workspace_ready(current_ctx):
            state = user_configured_bitable_state(owner_open_id, current_ctx)
            try:
                ensure_bitable_control_fields(api, state)
                ensure_script_table_fields(api, state)
                if (
                    current_ctx.get("video_app_token") != state.get("app_token")
                    or current_ctx.get("video_table_id") != state.get("table_id")
                    or current_ctx.get("video_url") != state.get("url")
                ):
                    current_ctx = update_user_config(
                        owner_open_id,
                        {
                            "video_app_token": state.get("app_token", ""),
                            "video_table_id": state.get("table_id", ""),
                            "video_url": state.get("url", ""),
                        },
                    )
                log(
                    "Reused initialized user workspace: "
                    f"open_id={owner_open_id}; tenant_id={current_ctx.get('tenant_id')}; "
                    f"script={state.get('script_url')}; video={state.get('url')}"
                )
                return {**state, "user_ctx": current_ctx, "reused": True}
            except Exception as exc:
                log(
                    "Existing user workspace cannot be reused; creating timestamped replacement: "
                    f"open_id={owner_open_id}; error={exc}"
                )

        stamp = timestamp_slug()
        table_title = f"{setting('FEISHU_BITABLE_TITLE', '即梦视频工作流表')}-{stamp}"
        state = api.create_review_bitable(table_title)
        state = ensure_script_bitable(api, state)
        ensure_bitable_control_fields(api, state)
        ensure_script_table_fields(api, state)
        updated = update_user_config(
            owner_open_id,
            {
                "script_app_token": state.get("script_app_token", ""),
                "script_table_id": state.get("script_table_id", ""),
                "video_app_token": state.get("app_token", ""),
                "video_table_id": state.get("table_id", ""),
                "script_url": state.get("script_url", ""),
                "video_url": state.get("url", ""),
                "workspace_initialized_at": now(),
            },
        )
        log(
            "Initialized user workspace: "
            f"open_id={owner_open_id}; tenant_id={updated.get('tenant_id')}; "
            f"script={state.get('script_url')}; video={state.get('url')}"
        )
        return {**state, "user_ctx": updated, "reused": False}


def ensure_script_bitable(api: FeishuApi, state: dict) -> dict:
    if state.get("script_app_token") and state.get("script_table_id"):
        return state
    if setting("COMBINED_BITABLE", "1").strip() != "0" and state.get("app_token") and state.get("table_id"):
        state["script_app_token"] = state["app_token"]
        state["script_table_id"] = state["table_id"]
        state["script_url"] = state.get("url", "")
        state["combined_bitable"] = True
        return state
    script_state = api.create_script_bitable(str(state.get("pending_script_title") or ""))
    state.update(script_state)
    log(f"Created standalone Feishu script bitable: {script_state.get('script_url')}")
    return state


def ensure_script_table_fields(api: FeishuApi, state: dict) -> None:
    app_token = state.get("script_app_token") or state.get("app_token")
    table_id = state.get("script_table_id")
    if not app_token or not table_id:
        return
    api.delete_default_data_tables(app_token, keep_table_id=table_id)
    existing = {field.get("field_name") for field in api.list_bitable_fields(app_token, table_id)}
    for field_name in ["任务ID", "文案", "对话中文", "备注", "状态", "视频链接", "错误原因"]:
        if field_name not in existing:
            api.create_bitable_text_field(app_token, table_id, field_name)
            log(f"Created script table field: {field_name}")
    if "确认" not in existing:
        api.create_bitable_choice_field(
            app_token,
            table_id,
            "确认",
            [{"name": "确认", "color": 2}, {"name": "待修改", "color": 5}],
        )
        log("Created script table field: 确认")
    if "图片" not in existing:
        api.create_bitable_image_choice_field(app_token, table_id)
        log("Created script table field: 图片")
    if "模型" not in existing:
        api.create_bitable_choice_field(
            app_token,
            table_id,
            "模型",
            [
                {"name": "seedance2.0", "color": 0},
                {"name": "seedance2.0fast", "color": 2},
                {"name": "seedance2.0_vip", "color": 3},
                {"name": "seedance2.0fast_vip", "color": 4},
            ],
        )
        log("Created script table field: 模型")
    ensure_bitable_reverse_task_sort(api, app_token, table_id, "script table")


def ensure_bitable_control_fields(api: FeishuApi, state: dict) -> None:
    app_token = state.get("app_token")
    table_id = state.get("table_id")
    if not app_token or not table_id:
        return
    api.delete_default_data_tables(app_token, keep_table_id=table_id)
    existing = {field.get("field_name") for field in api.list_bitable_fields(app_token, table_id)}
    for field_name in ["任务ID", "文案", "对话中文", "备注", "状态", "视频链接", "错误原因"]:
        if field_name not in existing:
            api.create_bitable_text_field(app_token, table_id, field_name)
            log(f"Created control table field: {field_name}")
    if "确认" not in existing:
        api.create_bitable_choice_field(
            app_token,
            table_id,
            "确认",
            [{"name": "确认", "color": 2}, {"name": "待修改", "color": 5}],
        )
        log("Created control table field: 确认")
    if "图片" not in existing:
        api.create_bitable_image_choice_field(app_token, table_id)
        log("Created control table field: 图片")
    if "模型" not in existing:
        api.create_bitable_choice_field(
            app_token,
            table_id,
            "模型",
            [
                {"name": "seedance2.0", "color": 0},
                {"name": "seedance2.0fast", "color": 2},
                {"name": "seedance2.0_vip", "color": 3},
                {"name": "seedance2.0fast_vip", "color": 4},
            ],
        )
        log("Created control table field: 模型")
    ensure_bitable_reverse_task_sort(api, app_token, table_id, "control table")


def ensure_bitable_reverse_task_sort(api: FeishuApi, app_token: str, table_id: str, label: str) -> None:
    try:
        task_field_id = ""
        for field in api.list_bitable_fields(app_token, table_id):
            if field.get("field_name") == "任务ID":
                task_field_id = str(field.get("field_id") or "")
                break
        if not task_field_id:
            log(f"Skipped reverse sort for {label}: 任务ID field_id not found")
            return
        for view in api.list_bitable_views(app_token, table_id):
            view_id = str(view.get("view_id") or view.get("id") or "")
            if view_id:
                api.update_bitable_view_task_sort(app_token, table_id, view_id, task_field_id)
        log(f"Ensured reverse task sort for {label}")
    except Exception as exc:
        log(f"Failed to ensure reverse task sort for {label}: {exc}")


def bitable_record_url(state: dict, table_id: str, record_id: str) -> str:
    base_url = (state.get("script_url") or state.get("url") or "").rstrip("/")
    if not base_url:
        token = state.get("script_app_token") or state.get("app_token", "")
        base_url = f"{setting('FEISHU_DOC_BASE', 'https://ncnrqomkm3wb.feishu.cn').rstrip('/')}/base/{token}"
    return f"{base_url}?table={table_id}&record={record_id}"


def is_combined_bitable_state(state: dict) -> bool:
    return bool(
        state.get("app_token")
        and state.get("table_id")
        and state.get("script_app_token")
        and state.get("script_table_id")
        and state.get("app_token") == state.get("script_app_token")
        and state.get("table_id") == state.get("script_table_id")
    )


def normalize_dedupe_text(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip()


def bitable_choice_text(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("text") or value.get("name") or value.get("value") or "").strip()
    if isinstance(value, list):
        return ",".join(bitable_choice_text(item) for item in value if bitable_choice_text(item)).strip()
    return str(value or "").strip()


def record_id_of(record: dict) -> str:
    return str(record.get("record_id") or record.get("id") or "").strip()


def find_duplicate_script_record(api: FeishuApi, state: dict, task_id: str, prompt: str) -> Optional[dict]:
    script_table_id = state.get("script_table_id")
    script_app_token = state.get("script_app_token") or state.get("app_token")
    if not script_table_id or not script_app_token:
        return None
    target_task_id = str(task_id).strip()
    target_prompt = normalize_dedupe_text(prompt)
    for record in api.list_bitable_records(script_app_token, script_table_id):
        fields = record.get("fields") or {}
        if str(fields.get("任务ID") or "").strip() != target_task_id:
            continue
        if normalize_dedupe_text(str(fields.get("文案") or "")) != target_prompt:
            continue
        record_id = record_id_of(record)
        if record_id:
            return {**record, "record_id": record_id}
    return None


def find_duplicate_review_record(api: FeishuApi, state: dict, task_id: str, script_record_id: str) -> Optional[dict]:
    if is_combined_bitable_state(state):
        return None
    app_token = state.get("app_token")
    table_id = state.get("table_id")
    if not app_token or not table_id:
        return None
    target_task_id = str(task_id).strip()
    target_script_id = str(script_record_id).strip()
    for record in api.list_bitable_records(app_token, table_id):
        fields = record.get("fields") or {}
        if str(fields.get("任务ID") or "").strip() != target_task_id:
            continue
        if str(fields.get("文案记录ID") or "").strip() != target_script_id:
            continue
        record_id = record_id_of(record)
        if record_id:
            return {**record, "record_id": record_id}
    return None


def create_script_record(task: dict, api: FeishuApi, prompt: str, state: dict) -> dict:
    script_table_id = state.get("script_table_id")
    if not script_table_id:
        raise RuntimeError("Feishu script table is not configured.")
    existing = find_duplicate_script_record(api, state, task["task_id"], prompt)
    if existing:
        record_id = existing["record_id"]
        log(f"Reusing duplicate script record for {task['task_id']}: {record_id}")
        return {
            "record_id": record_id,
            "record_url": existing.get("record_url")
            or bitable_record_url({**state, "url": state.get("script_url") or state.get("url")}, script_table_id, record_id),
            "deduped": True,
        }
    record = api.create_bitable_record(
        state.get("script_app_token") or state["app_token"],
        script_table_id,
        {
            "任务ID": task["task_id"],
            "文案": prompt,
            "对话中文": task.get("dialogue_translation") or "",
            "图片": task.get("image_suggestion") or task.get("image_variant") or "",
            "模型": normalize_model(task.get("model_version")),
            "确认": "",
            "备注": task.get("manual_note") or task.get("brief") or "",
        },
    )
    record_id = record.get("record_id") or record.get("id")
    return {
        "record_id": record_id,
        "record_url": record.get("record_url") or bitable_record_url({**state, "url": state.get("script_url") or state.get("url")}, script_table_id, record_id),
    }


def create_review_record(task: dict, api: FeishuApi, prompt: str) -> dict:
    state = ensure_review_bitable(api, task)
    script_record = create_script_record(task, api, prompt, state)
    if is_combined_bitable_state(state):
        record_id = script_record["record_id"]
        return {
            **state,
            "record_id": record_id,
            "record_url": script_record["record_url"],
            "script_record_id": record_id,
            "script_record_url": script_record["record_url"],
            "deduped": bool(script_record.get("deduped")),
        }
    existing_review = find_duplicate_review_record(api, state, task["task_id"], script_record["record_id"])
    if existing_review:
        record_id = existing_review["record_id"]
        log(f"Reusing duplicate review record for {task['task_id']}: {record_id}")
        return {
            **state,
            "record_id": record_id,
            "record_url": existing_review.get("record_url") or bitable_record_url(state, state["table_id"], record_id),
            "script_record_id": script_record["record_id"],
            "script_record_url": script_record["record_url"],
            "deduped": True,
        }
    fields = {
        "任务ID": task["task_id"],
        "文案链接": script_record["record_url"],
        "文案记录ID": script_record["record_id"],
        "确认": "",
        "状态": "",
        "视频链接": "",
        "错误原因": "",
    }
    record = api.create_bitable_record(state["app_token"], state["table_id"], fields)
    return {
        **state,
        "record_id": record.get("record_id") or record.get("id"),
        "record_url": record.get("record_url") or "",
        "script_record_id": script_record["record_id"],
        "script_record_url": script_record["record_url"],
    }


def ensure_script_review_record(task: dict, api: FeishuApi) -> None:
    if task.get("script_bitable_record_id"):
        return
    state = ensure_review_bitable(api, task)
    prompt = Path(task["prompt_file"]).read_text(encoding="utf-8-sig").strip()
    script_record = create_script_record(task, api, prompt, state)
    task["review_backend"] = "bitable"
    task["script_bitable_app_token"] = state.get("script_app_token", state.get("app_token", ""))
    task["script_bitable_table_id"] = state.get("script_table_id", "")
    task["script_bitable_record_id"] = script_record["record_id"]
    task["script_bitable_record_url"] = script_record["record_url"]
    if is_combined_bitable_state(state):
        task["review_bitable_app_token"] = state.get("app_token", "")
        task["review_bitable_table_id"] = state.get("table_id", "")
        task["review_bitable_record_id"] = script_record["record_id"]
        task["review_bitable_url"] = state.get("url", "")
        task["review_bitable_record_url"] = script_record["record_url"]
        task["review_auto_scan_enabled"] = True
    task["script_review_status"] = "waiting"
    task["deduped_script_record"] = bool(script_record.get("deduped"))
    task["review_doc_url"] = script_record["record_url"]
    task["review_doc_created_at"] = now()


def find_bitable_record_by_task_id(api: FeishuApi, task_id: str, task: Optional[dict] = None) -> Optional[dict]:
    state = ensure_review_bitable(api, task)
    app_token = state.get("app_token")
    table_id = state.get("table_id")
    if not app_token or not table_id:
        return None
    for record in api.list_bitable_records(app_token, table_id):
        fields = record.get("fields") or {}
        if str(fields.get("任务ID") or "").strip() == str(task_id).strip():
            record_id = record.get("record_id") or record.get("id")
            if not record_id:
                continue
            return {**record, "record_id": record_id, "app_token": app_token, "table_id": table_id, "url": state.get("url", "")}
    return None


def update_task_workflow_record(api: FeishuApi, task: dict, fields: dict, label: str = "workflow") -> None:
    updated_keys: set[tuple[str, str, str]] = set()
    script_key = (
        str(task.get("script_bitable_app_token") or ""),
        str(task.get("script_bitable_table_id") or ""),
        str(task.get("script_bitable_record_id") or ""),
    )
    review_key = (
        str(task.get("review_bitable_app_token") or ""),
        str(task.get("review_bitable_table_id") or ""),
        str(task.get("review_bitable_record_id") or ""),
    )
    for app_token, table_id, record_id in [script_key, review_key]:
        if not app_token or not table_id or not record_id:
            continue
        key = (app_token, table_id, record_id)
        if key in updated_keys:
            continue
        try:
            api.update_bitable_record(app_token, table_id, record_id, fields)
            updated_keys.add(key)
        except Exception as exc:
            log(f"Failed to update {label} bitable record for {task.get('task_id')}: {exc}")


def cleanup_duplicate_bitable_records_for_state(api: FeishuApi, state: dict) -> None:
    ensure_bitable_control_fields(api, state)
    ensure_script_table_fields(api, state)
    script_app_token = state.get("script_app_token") or state.get("app_token")
    script_table_id = state.get("script_table_id")
    if script_app_token and script_table_id:
        seen_scripts: set[tuple[str, str]] = set()
        for record in api.list_bitable_records(script_app_token, script_table_id):
            fields = record.get("fields") or {}
            key = (
                str(fields.get("任务ID") or "").strip(),
                normalize_dedupe_text(str(fields.get("文案") or "")),
            )
            record_id = record_id_of(record)
            if not key[0] or not key[1] or not record_id:
                continue
            if key in seen_scripts:
                api.delete_bitable_record(script_app_token, script_table_id, record_id)
                log(f"Deleted duplicate script record: {record_id}")
            else:
                seen_scripts.add(key)

    app_token = state.get("app_token")
    table_id = state.get("table_id")
    if app_token and table_id:
        seen_reviews: set[tuple[str, str]] = set()
        for record in api.list_bitable_records(app_token, table_id):
            fields = record.get("fields") or {}
            key = (
                str(fields.get("任务ID") or "").strip(),
                str(fields.get("文案记录ID") or "").strip(),
            )
            record_id = record_id_of(record)
            if not key[0] or not key[1] or not record_id:
                continue
            if key in seen_reviews:
                api.delete_bitable_record(app_token, table_id, record_id)
                log(f"Deleted duplicate review record: {record_id}")
            else:
                seen_reviews.add(key)


def cleanup_duplicate_bitable_records(api: FeishuApi) -> None:
    states: List[dict] = []
    legacy_state = read_bitable_state()
    if (
        legacy_state.get("app_token")
        and legacy_state.get("table_id")
        and legacy_state.get("script_app_token")
        and legacy_state.get("script_table_id")
    ):
        states.append({**legacy_state, "source": "legacy_state"})

    for open_id, configured in read_users_config().get("users", {}).items():
        state = user_configured_bitable_state(str(open_id), configured if isinstance(configured, dict) else {})
        if state:
            states.append(state)

    seen_state_keys: set[tuple[str, str, str, str]] = set()
    for state in states:
        key = (
            str(state.get("app_token") or ""),
            str(state.get("table_id") or ""),
            str(state.get("script_app_token") or ""),
            str(state.get("script_table_id") or ""),
        )
        if key in seen_state_keys:
            continue
        seen_state_keys.add(key)
        try:
            cleanup_duplicate_bitable_records_for_state(api, state)
            log(
                "Cleaned duplicate bitable records: "
                f"source={state.get('source')}; owner={state.get('owner_open_id', '')}; "
                f"script_table={state.get('script_table_id')}; video_table={state.get('table_id')}"
            )
        except Exception as exc:
            log(f"Failed to clean duplicate bitable records for {key}: {exc}")


def import_bitable_task(api: FeishuApi, task_id: str, user_ctx: Optional[dict] = None) -> Optional[Path]:
    seed_task = {
        "task_id": str(task_id),
        "tenant_id": (user_ctx or {}).get("tenant_id", ""),
        "owner_open_id": (user_ctx or {}).get("owner_open_id", ""),
        "user_script_app_token": (user_ctx or {}).get("script_app_token", ""),
        "user_script_table_id": (user_ctx or {}).get("script_table_id", ""),
        "user_video_app_token": (user_ctx or {}).get("video_app_token", ""),
        "user_video_table_id": (user_ctx or {}).get("video_table_id", ""),
    }
    record = find_bitable_record_by_task_id(api, task_id, seed_task)
    if not record:
        return None
    fields = record.get("fields") or {}
    state = ensure_review_bitable(api, seed_task)
    script_record_id = str(fields.get("文案记录ID") or "").strip()
    script_fields: Dict[str, Any] = {}
    if script_record_id and state.get("script_table_id"):
        script_record = api.get_bitable_record(state.get("script_app_token") or state["app_token"], state["script_table_id"], script_record_id)
        script_fields = script_record.get("fields") or {}
    prompt = str(
        script_fields.get("文案")
        or script_fields.get("修改文案")
        or script_fields.get("原文案")
        or fields.get("文案")
        or fields.get("修改文案")
        or fields.get("原文案")
        or ""
    ).strip()
    if not prompt:
        raise RuntimeError(f"多维表格任务 {task_id} 的文案为空。")

    prompt_dir = prompt_dir_for_task(seed_task, "bitable")
    prompt_dir.mkdir(parents=True, exist_ok=True)
    prompt_file = prompt_dir / f"{task_id}.txt"
    prompt_file.write_text(prompt + "\n", encoding="utf-8")

    task = {
        "task_id": str(task_id),
        "prompt_file": str(prompt_file),
        "images": [],
        "image_source": "manual_bitable",
        "image_library": (user_ctx or {}).get("image_library") or setting("IMAGE_LIBRARY_DIR", str(ROOT / "vivi-image")),
        "duration": clamp_duration(setting("DEFAULT_DURATION", "15")),
        "ratio": setting("DEFAULT_RATIO", "9:16"),
        "model_version": normalize_model(script_fields.get("模型") or fields.get("模型") or setting("DEFAULT_MODEL", "seedance2.0fast_vip")),
        "video_resolution": setting("DEFAULT_RESOLUTION", "720p"),
        "jimeng_account": (user_ctx or {}).get("jimeng_account") or setting("DEFAULT_JIMENG_ACCOUNT", ""),
        "tenant_id": seed_task.get("tenant_id", ""),
        "owner_open_id": seed_task.get("owner_open_id", ""),
        "user_script_app_token": seed_task.get("user_script_app_token", ""),
        "user_script_table_id": seed_task.get("user_script_table_id", ""),
        "user_video_app_token": seed_task.get("user_video_app_token", ""),
        "user_video_table_id": seed_task.get("user_video_table_id", ""),
        "data_isolation_level": "physical" if seed_task.get("tenant_id") else "legacy",
        "review_backend": "bitable",
        "review_bitable_app_token": record["app_token"],
        "review_bitable_table_id": record["table_id"],
        "review_bitable_record_id": record["record_id"],
        "script_bitable_app_token": state.get("script_app_token", state.get("app_token", "")),
        "script_bitable_table_id": state.get("script_table_id", ""),
        "script_bitable_record_id": script_record_id,
        "review_bitable_url": record.get("url", ""),
        "review_bitable_record_url": record.get("record_url") or "",
        "script_bitable_record_url": str(fields.get("文案链接") or ""),
        "review_doc_url": record.get("record_url") or record.get("url", ""),
        "created_at": now(),
        "imported_from_bitable_at": now(),
    }
    path = write_task("reviewing", task)
    log(f"Imported bitable task {task_id} into local queue: {path}")
    return path


def allocate_review_table_row(task: dict, api: FeishuApi, prompt: str) -> dict:
    state = ensure_shared_review_table(api)
    next_row = int(state.get("next_row", 1))
    rows = state.get("rows") or []
    if next_row > len(rows):
        raise RuntimeError(f"Feishu review table is full: {state.get('url')}")
    row = rows[next_row - 1]
    required = ["original_cell_id", "modified_cell_id", "confirm_cell_id", "video_cell_id"]
    missing = [key for key in required if not row.get(key)]
    if missing:
        raise RuntimeError(f"Feishu review table row {next_row} is missing cell ids: {missing}")
    document_id = state["document_id"]
    api.append_text_to_cell(document_id, row["original_cell_id"], prompt)
    api.append_text_to_cell(document_id, row["modified_cell_id"], prompt)
    api.append_text_to_cell(document_id, row["confirm_cell_id"], "待确认")
    api.append_text_to_cell(document_id, row["video_cell_id"], "待生成")
    row["task_id"] = task["task_id"]
    row["allocated_at"] = now()
    state["next_row"] = next_row + 1
    write_review_table_state(state)
    return {**state, **row}


def prompt_preview(prompt_file: str, limit: int = 1200) -> str:
    text = Path(prompt_file).read_text(encoding="utf-8").strip()
    return text if len(text) <= limit else text[:limit] + "\n...(已截断)"


def ensure_review_doc(task: dict, api: FeishuApi) -> None:
    if task.get("review_bitable_record_id") or task.get("review_doc_id"):
        return
    prompt = Path(task["prompt_file"]).read_text(encoding="utf-8-sig").strip()
    if setting("FEISHU_REVIEW_BACKEND", "bitable").strip().lower() == "bitable":
        record = create_review_record(task, api, prompt)
        task["review_backend"] = "bitable"
        task["review_bitable_app_token"] = record["app_token"]
        task["review_bitable_table_id"] = record["table_id"]
        task["review_bitable_record_id"] = record["record_id"]
        task["script_bitable_app_token"] = record.get("script_app_token", record.get("app_token", ""))
        task["script_bitable_table_id"] = record.get("script_table_id", "")
        task["script_bitable_record_id"] = record.get("script_record_id", "")
        task["script_bitable_record_url"] = record.get("script_record_url", "")
        task["review_bitable_url"] = record.get("url", "")
        task["review_bitable_record_url"] = record.get("record_url", "")
        task["review_doc_url"] = record.get("record_url") or record.get("url", "")
        task["review_doc_created_at"] = now()
        task["deduped_review_record"] = bool(record.get("deduped"))
        return
    row = allocate_review_table_row(task, api, prompt)
    task["review_backend"] = "docx"
    task["review_doc_id"] = row["document_id"]
    task["review_doc_url"] = row["url"]
    task["review_doc_title"] = row.get("title") or setting("FEISHU_REVIEW_TABLE_TITLE", "即梦视频生成审核总表")
    task["review_doc_row_index"] = row.get("row_index")
    task["review_doc_original_cell_id"] = row.get("original_cell_id")
    task["review_doc_modified_text_cell_id"] = row.get("modified_cell_id")
    task["review_doc_confirm_cell_id"] = row.get("confirm_cell_id")
    task["review_doc_video_cell_id"] = row.get("video_cell_id")
    task["review_doc_created_at"] = now()


def extract_modified_prompt_from_review_doc(raw_content: str, title: str = "") -> str:
    normalized = raw_content.replace("\r\n", "\n")
    sections = [section.strip() for section in normalized.split("\n\n") if section.strip()]
    if sections and title and sections[0] == title:
        sections = sections[1:]
    headers = ["原文案", "修改文案", "确认", "视频链接"]
    if len(sections) >= 8 and sections[:4] == headers:
        modified = sections[5].strip()
        if modified and modified not in {"待确认", "待生成"}:
            return modified

    lines = [line.strip() for line in normalized.split("\n")]
    lines = [line for line in lines if line]
    if lines and title and lines[0] == title:
        lines = lines[1:]
    header_positions = [idx for idx, line in enumerate(lines) if line in headers]
    if len(header_positions) >= 4:
        data_start = header_positions[3] + 1
        data_lines = lines[data_start:]
        if len(data_lines) >= 4:
            modified = data_lines[1].strip()
            if modified and modified not in {"待确认", "待生成"}:
                return modified
        start = header_positions[3] + 1
        end = len(lines)
        modified = "\n".join(lines[start:end]).strip()
        if modified and modified not in {"待确认", "待生成"}:
            return modified
    # Fallback for markdown-style tables, useful if a reviewer copied the table out and back.
    for line in lines:
        if line.startswith("|") and "修改文案" not in line and "---" not in line:
            cells = [cell.strip().replace("<br>", "\n") for cell in line.strip("|").split("|")]
            if len(cells) >= 2 and cells[1]:
                return cells[1]
    content = "\n".join(lines).strip()
    if not content:
        raise RuntimeError("Feishu review doc is empty.")
    return content


IMAGE_GROUPS = {
    "blue": ["okivivi-blue.jpg", "okivivi-blue1.jpg"],
    "pink": ["okivivi-pink.jpg", "okivivi-pink1.jpg"],
    "all": ["okivivi-blue.jpg", "okivivi-blue1.jpg", "okivivi-pink.jpg", "okivivi-pink1.jpg"],
}


def image_library_dir(task: dict) -> Path:
    configured = task.get("image_library") or setting("IMAGE_LIBRARY_DIR", str(ROOT / "vivi-image"))
    path = Path(str(configured)).expanduser()
    if path.exists():
        return path
    fallback = Path(setting("IMAGE_LIBRARY_DIR", str(ROOT / "vivi-image"))).expanduser()
    return fallback


def normalize_image_path(image: Any, task: Optional[dict] = None) -> str:
    raw = str(image or "").strip()
    if not raw:
        return raw
    path = Path(raw).expanduser()
    if path.exists():
        return str(path)
    name = path.name
    if name:
        candidates = [
            image_library_dir(task or {}) / name,
            Path(setting("IMAGE_LIBRARY_DIR", str(ROOT / "vivi-image"))).expanduser() / name,
            ROOT / "vivi-image" / name,
        ]
        for candidate in candidates:
            if candidate.exists():
                return str(candidate)
    return str(path)


def normalize_task_images(task: dict) -> None:
    images = [normalize_image_path(image, task) for image in task.get("images", []) if str(image or "").strip()]
    if images:
        seen = set()
        task["images"] = [item for item in images if not (item in seen or seen.add(item))]


def resolve_selected_images(selection: str, task: dict) -> List[str]:
    text = str(selection or "").strip()
    if not text:
        return []
    lowered = text.lower()
    image_dir = image_library_dir(task)
    if lowered in IMAGE_GROUPS:
        return [normalize_image_path(image_dir / name, task) for name in IMAGE_GROUPS[lowered]]

    candidates = []
    for part in re.split(r"[\n,，;；]+", text):
        item = part.strip().strip('"').strip("'")
        if not item:
            continue
        key = item.lower()
        if key in IMAGE_GROUPS:
            candidates.extend(normalize_image_path(image_dir / name, task) for name in IMAGE_GROUPS[key])
            continue
        path = Path(item).expanduser()
        if not path.is_absolute():
            path = image_dir / item
        candidates.append(normalize_image_path(path, task))
    seen = set()
    result = []
    for item in candidates:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def ensure_task_images_from_suggestion(task: dict) -> None:
    normalize_task_images(task)
    if task.get("images"):
        return
    selection = task.get("image_suggestion") or task.get("image_variant") or ""
    selected = resolve_selected_images(selection, task)
    if selected:
        task["images"] = selected
        task["image_source"] = "bot_card"
        task["script_image_value"] = selection


def refresh_prompt_from_review_doc(task: dict, api: FeishuApi) -> None:
    if task.get("review_backend") == "bitable" or task.get("review_bitable_record_id"):
        app_token = task.get("review_bitable_app_token")
        table_id = task.get("review_bitable_table_id")
        record_id = task.get("review_bitable_record_id")
        if not app_token or not table_id or not record_id:
            raise RuntimeError("Feishu bitable review record info is incomplete.")
        try:
            record = api.get_bitable_record(app_token, table_id, record_id)
        except Exception as exc:
            if "RecordIdNotFound" in str(exc) and task.get("script_bitable_record_id"):
                for key in [
                    "review_bitable_record_id",
                    "review_bitable_record_url",
                    "review_doc_url",
                    "review_bitable_app_token",
                    "review_bitable_table_id",
                ]:
                    task.pop(key, None)
                task["review_record_missing_at"] = now()
                task["_review_not_confirmed"] = True
                log(f"Review record missing; cleared stale review reference for {task.get('task_id')}")
                return
            raise
        fields = record.get("fields") or {}
        state = read_bitable_state()
        confirm_value = bitable_choice_text(fields.get("确认"))
        if confirm_value != "确认":
            task["review_confirm_value"] = confirm_value
            task["review_doc_synced_at"] = now()
            task["_review_not_confirmed"] = True
            return
        script_fields: Dict[str, Any] = {}
        script_app_token = task.get("script_bitable_app_token") or state.get("script_app_token") or app_token
        script_table_id = task.get("script_bitable_table_id") or state.get("script_table_id")
        script_record_id = task.get("script_bitable_record_id") or str(fields.get("文案记录ID") or "").strip()
        if script_table_id and script_record_id:
            script_record = api.get_bitable_record(script_app_token, script_table_id, script_record_id)
            script_fields = script_record.get("fields") or {}
            task["script_bitable_app_token"] = script_app_token
            task["script_bitable_table_id"] = script_table_id
            task["script_bitable_record_id"] = script_record_id
            task["script_bitable_record_url"] = str(fields.get("文案链接") or task.get("script_bitable_record_url") or "")
            script_model_choice = bitable_choice_text((script_fields or fields).get("模型"))
            if script_model_choice:
                task["model_version"] = normalize_model(script_model_choice)
        selected_images = list(task.get("images") or [])
        if not selected_images:
            script_image_choice = bitable_choice_text((script_fields or fields).get("图片"))
            selected_images = resolve_selected_images(script_image_choice, task)
            if selected_images:
                task["images"] = selected_images
                task["image_source"] = "script_bitable"
                task["script_image_value"] = script_image_choice
        if not selected_images:
            task["review_doc_synced_at"] = now()
            task["_review_not_confirmed"] = True
            log(f"Task confirmed but workflow table image selection is empty, ignored silently: {task.get('task_id')}")
            return
        content = str(
            script_fields.get("文案")
            or script_fields.get("修改文案")
            or script_fields.get("原文案")
            or fields.get("文案")
            or fields.get("修改文案")
            or fields.get("原文案")
            or ""
        ).strip()
        if not content:
            raise RuntimeError(f"Feishu bitable 工作流表 文案 is empty: {record_id}")
        prompt_file = Path(task["prompt_file"])
        prompt_file.write_text(content + "\n", encoding="utf-8")
        task["review_prompt_source"] = "script_table"
        task["review_doc_synced_at"] = now()
        return
    document_id = task.get("review_doc_id")
    if not document_id:
        return
    modified_cell_id = task.get("review_doc_modified_text_cell_id")
    if modified_cell_id:
        content = api.block_text(document_id, modified_cell_id)
    else:
        content = api.doc_raw_content(document_id)
    if not content:
        raise RuntimeError(f"Feishu review doc is empty: {document_id}")
    if not modified_cell_id:
        title = str(task.get("review_doc_title") or "").strip()
        content = extract_modified_prompt_from_review_doc(content, title)
    prompt_file = Path(task["prompt_file"])
    prompt_file.write_text(content.strip() + "\n", encoding="utf-8")
    task["review_doc_synced_at"] = now()


def promote_script_to_review(task: dict, api: FeishuApi) -> bool:
    if task.get("review_bitable_record_id") or task.get("review_doc_id"):
        return True
    script_app_token = task.get("script_bitable_app_token")
    script_table_id = task.get("script_bitable_table_id")
    script_record_id = task.get("script_bitable_record_id")
    if not script_app_token or not script_table_id or not script_record_id:
        return False
    try:
        record = api.get_bitable_record(script_app_token, script_table_id, script_record_id)
    except Exception as exc:
        if "RecordIdNotFound" in str(exc):
            task["script_review_status"] = "missing"
            task["fail_reason"] = "Feishu script table record was deleted before confirmation."
            task["_script_record_missing"] = True
            log(f"Script record missing; task will stop waiting: {task.get('task_id')}")
            return False
        raise
    fields = record.get("fields") or {}
    confirm_value = bitable_choice_text(fields.get("确认"))
    if confirm_value != "确认":
        task["script_confirm_value"] = confirm_value
        task["script_review_checked_at"] = now()
        return False
    content = str(fields.get("文案") or "").strip()
    if not content:
        task["script_confirm_value"] = confirm_value
        task["script_review_checked_at"] = now()
        return False
    script_image_choice = bitable_choice_text(fields.get("图片"))
    selected_images = resolve_selected_images(script_image_choice, task)
    if not selected_images:
        task["script_confirm_value"] = confirm_value
        task["script_review_checked_at"] = now()
        task["script_image_value"] = script_image_choice
        log(f"Script confirmed but image selection is empty, waiting: {task.get('task_id')}")
        return False
    script_model_choice = bitable_choice_text(fields.get("模型"))
    if script_model_choice:
        task["model_version"] = normalize_model(script_model_choice)
    prompt_file = Path(task["prompt_file"])
    prompt_file.write_text(content + "\n", encoding="utf-8")
    task["images"] = selected_images
    task["image_source"] = "script_bitable"
    task["script_image_value"] = script_image_choice
    review = create_review_record(task, api, content)
    task["review_backend"] = "bitable"
    task["review_bitable_app_token"] = review["app_token"]
    task["review_bitable_table_id"] = review["table_id"]
    task["review_bitable_record_id"] = review["record_id"]
    task["review_bitable_url"] = review.get("url", "")
    task["review_bitable_record_url"] = review.get("record_url", "")
    task["review_doc_url"] = review.get("record_url") or review.get("url", "")
    task["script_bitable_record_url"] = review.get("script_record_url", task.get("script_bitable_record_url", ""))
    task["script_review_status"] = "confirmed"
    task["script_review_confirmed_at"] = now()
    task["review_auto_scan_enabled"] = True
    task["deduped_review_record"] = bool(review.get("deduped"))
    return True


def review_card(task: dict) -> dict:
    task_id = task["task_id"]
    params = " | ".join(
        [
            str(task.get("model_version") or "-"),
            str(task.get("ratio") or "-"),
            f"{task.get('duration') or '-'}s",
            str(task.get("video_resolution") or "-"),
        ]
    )
    if task.get("images"):
        image_names = [Path(path).name for path in task.get("images", [])]
        image_lines = ", ".join(image_names[:4])
    else:
        suggestion = task.get("image_suggestion") or "请在表格填写"
        image_lines = f"待选: {suggestion}"
    script_url = task.get("script_bitable_record_url") or ""
    review_url = task.get("review_bitable_record_url") or task.get("review_doc_url") or task.get("review_bitable_url") or ""
    if script_url and review_url and script_url == review_url:
        script_line = f"[工作流表]({script_url})"
        review_line = ""
    else:
        script_line = f"[文案表]({script_url})" if script_url else "文案表失败"
        review_line = f" | [视频表]({review_url})" if review_url else ""
    if task.get("review_backend") == "bitable" or task.get("review_bitable_record_id"):
        review_note = "在同一张工作流表中确认文案、图片和模型；选择“确认”后自动生成。"
    else:
        review_note = f"写入第 {task.get('review_doc_row_index', '?')} 行；确认后生成。"
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": f"待审核: {task_id}"},
            "template": "blue",
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": f"**参数** {params}\n**图片** {image_lines}\n{script_line}{review_line}"}},
            {
                "tag": "note",
                "elements": [
                    {
                        "tag": "plain_text",
                        "content": review_note,
                    }
                ],
            },
        ],
    }


def task_prompt_text(task: dict) -> str:
    try:
        return Path(str(task.get("prompt_file") or "")).read_text(encoding="utf-8-sig").strip()
    except Exception:
        return str(task.get("prompt") or "").strip()


def compact_task_title(task_id: str) -> str:
    return re.sub(r"^(?:okivivi-|manual-)", "", str(task_id or "")).strip()


def script_review_card(task: dict, show_prompt: bool = False, show_dialogue: bool = False, approved: bool = False) -> dict:
    task_id = task["task_id"]
    params = " | ".join(
        [
            str(task.get("model_version") or "-"),
            str(task.get("ratio") or "-"),
            f"{task.get('duration') or '-'}s",
            str(task.get("video_resolution") or "-"),
        ]
    )
    image_names = [Path(path).name for path in task.get("images", [])]
    if not image_names:
        image_names = [str(task.get("image_suggestion") or "待自动匹配")]
    prompt = task_prompt_text(task)
    dialogue_cn = str(task.get("dialogue_translation") or "").strip() or "暂无对话中文。"
    user_value = card_user_value({
        "tenant_id": task.get("tenant_id", ""),
        "owner_open_id": task.get("owner_open_id", ""),
        "owner_name": task.get("owner_name", ""),
        "jimeng_account": task.get("jimeng_account", ""),
        "image_library": task.get("image_library", ""),
    })
    base_value = {"task_id": task_id, **user_value}
    elements: List[dict] = [
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**参数** {params}\n**图片** {', '.join(image_names)}",
            },
        }
    ]
    actions = [
        {
            "tag": "button",
            "text": {"tag": "plain_text", "content": "关闭文案" if show_prompt else "文案"},
            "value": {"action": "toggle_script_prompt", "show_prompt": not show_prompt, "show_dialogue": show_dialogue, **base_value},
        },
        {
            "tag": "button",
            "text": {"tag": "plain_text", "content": "关闭对话中文" if show_dialogue else "对话中文"},
            "value": {"action": "toggle_dialogue_cn", "show_prompt": show_prompt, "show_dialogue": not show_dialogue, **base_value},
        },
        {
            "tag": "button",
            "text": {"tag": "plain_text", "content": "已通过" if approved or task.get("card_approved") else "通过"},
            "type": "primary",
            "value": {"action": "card_approve_task", "show_prompt": show_prompt, "show_dialogue": show_dialogue, **base_value},
        },
    ]
    elements.append({"tag": "action", "actions": actions})
    if show_prompt:
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": f"**文案**\n```text\n{prompt[:7800]}\n```"},
        })
    if show_dialogue:
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": f"**对话中文**\n```text\n{dialogue_cn[:4000]}\n```"},
        })
    elements.append({
        "tag": "note",
        "elements": [
            {
                "tag": "plain_text",
                "content": "在机器人卡片内确认；点击“通过”后直接提交即梦生成，表格只用于记录状态和视频链接。",
            }
        ],
    })
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": f"文案待确认: {compact_task_title(task_id)}"},
            "template": "turquoise",
        },
        "elements": elements,
    }


def result_download_dir(task: dict) -> Path:
    configured = str(setting("RESULT_DOWNLOAD_DIR") or "").strip()
    if configured:
        base = Path(configured).expanduser()
        if "{tenant_id}" in configured:
            base = Path(configured.format(tenant_id=tenant_id_for_task(task), owner_open_id=task.get("owner_open_id", ""))).expanduser()
    else:
        base = tenant_root(task) / "downloads"
    base.mkdir(parents=True, exist_ok=True)
    return base


def result_download_path(task: dict) -> Path:
    task_id = str(task.get("task_id") or "video")
    return result_download_dir(task) / f"{task_id}.mp4"


def result_card(task: dict, downloaded: Optional[bool] = None) -> dict:
    task_id = str(task.get("task_id") or "")
    video_url = str(task.get("video_url") or "").strip()
    downloaded = bool(task.get("downloaded_at") or task.get("download_file")) if downloaded is None else downloaded
    download_path = str(task.get("download_file") or result_download_path(task))
    user_value = card_user_value({
        "tenant_id": task.get("tenant_id", ""),
        "owner_open_id": task.get("owner_open_id", ""),
        "owner_name": task.get("owner_name", ""),
        "jimeng_account": task.get("jimeng_account", ""),
        "image_library": task.get("image_library", ""),
    })
    base_value = {"task_id": task_id, **user_value}
    link_line = f"[{task_id}.mp4]({video_url})" if video_url else "视频链接生成中"
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "green" if downloaded else "blue",
            "title": {"tag": "plain_text", "content": f"返回结果: {compact_task_title(task_id)}"},
        },
        "elements": [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**任务ID** {task_id}\n**视频链接** {link_line}",
                },
            },
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "已下载" if downloaded else "下载"},
                        "type": "primary" if not downloaded else "default",
                        "value": {"action": "result_download", **base_value},
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "文件位置"},
                        "value": {"action": "result_location", **base_value},
                    },
                ],
            },
            {
                "tag": "note",
                "elements": [
                    {
                        "tag": "plain_text",
                        "content": f"本地/服务器保存位置：{download_path}",
                    }
                ],
            },
        ],
    }


def mark_result_downloaded(task: dict) -> Path:
    source = Path(str(task.get("video_file") or ""))
    if not source.exists():
        fallback = output_dir_for_task(task) / "video.mp4"
        if fallback.exists():
            source = fallback
    if not source.exists():
        raise RuntimeError(f"本地视频文件不存在: {source}")
    target = result_download_path(task)
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() != target.resolve():
        shutil.copyfile(source, target)
    task["download_file"] = str(target)
    task["downloaded_at"] = now()
    write_task("done", task)
    return target


def undownloaded_done_tasks(owner_open_id: str = "", limit: int = 50) -> List[dict]:
    tasks = []
    for task in collect_tasks("done", limit * 3):
        if not task.get("video_url"):
            continue
        if owner_open_id and str(task.get("owner_open_id") or "") != owner_open_id:
            continue
        if task.get("downloaded_at") or task.get("download_file"):
            continue
        tasks.append(task)
        if len(tasks) >= limit:
            break
    return tasks


def result_status_card(owner_open_id: str = "") -> dict:
    pending = undownloaded_done_tasks(owner_open_id)
    count = len(pending)
    top_task = pending[0] if pending else {}
    ctx = user_context(owner_open_id) if owner_open_id else user_context("")
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue" if count else "green",
            "title": {"tag": "plain_text", "content": "返回结果状态栏"},
        },
        "elements": [
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": f"当前未下载视频：**{count}** 条"},
            },
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "跳转到最上方未下载"},
                        "type": "primary",
                        "value": {
                            "action": "result_jump_first",
                            "task_id": top_task.get("task_id", ""),
                            **card_user_value(ctx),
                        },
                    }
                ],
            },
        ],
    }


def prompt_entry_card(user_ctx: Optional[dict] = None) -> dict:
    default_model = setting("DEFAULT_MODEL", "seedance2.0fast_vip")
    count_select = {
        "tag": "select_static",
        "placeholder": {"tag": "plain_text", "content": "条数"},
        "initial_option": "1",
        "name": "count",
        "required": True,
        "options": [
            {"text": {"tag": "plain_text", "content": f"{count} 条"}, "value": str(count)}
            for count in [1, 2, 3, 5, 10]
        ],
    }
    duration_select = {
        "tag": "select_static",
        "placeholder": {"tag": "plain_text", "content": "时长"},
        "initial_option": "15",
        "name": "script_duration",
        "required": True,
        "options": [
            {"text": {"tag": "plain_text", "content": "15s"}, "value": "15"},
            {"text": {"tag": "plain_text", "content": "30s"}, "value": "30"},
        ],
    }
    role_select = {
        "tag": "select_static",
        "placeholder": {"tag": "plain_text", "content": "角色"},
        "initial_option": "single_vivi",
        "name": "character_mode",
        "required": True,
        "options": [
            {"text": {"tag": "plain_text", "content": "1. vivi"}, "value": "single_vivi"},
            {"text": {"tag": "plain_text", "content": "2. bree, sunny"}, "value": "bree_sunny"},
        ],
    }
    model_select = {
        "tag": "select_static",
        "placeholder": {"tag": "plain_text", "content": "模型"},
        "initial_option": default_model,
        "name": "model_version",
        "required": True,
        "options": [
            {"text": {"tag": "plain_text", "content": label}, "value": value}
            for label, value in MODEL_OPTIONS
        ],
    }
    return {
        "config": {"wide_screen_mode": False},
        "header": {
            "title": {"tag": "plain_text", "content": "OKIVIVI 文案生成入口"},
            "template": "blue",
        },
        "elements": [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": (
                        "**选择后生成。**\n"
                        "生成后在机器人卡片内确认。"
                    ),
                },
            },
            {
                "tag": "form",
                "name": "prompt_form",
                "elements": [
                    {
                        "tag": "column_set",
                        "flex_mode": "none",
                        "background_style": "default",
                        "columns": [
                            {"tag": "column", "width": "weighted", "weight": 1, "elements": [count_select]},
                            {"tag": "column", "width": "weighted", "weight": 1, "elements": [duration_select]},
                        ],
                    },
                    {
                        "tag": "column_set",
                        "flex_mode": "none",
                        "background_style": "default",
                        "columns": [
                            {"tag": "column", "width": "weighted", "weight": 1, "elements": [role_select]},
                            {"tag": "column", "width": "weighted", "weight": 1, "elements": [model_select]},
                        ],
                    },
                    {
                        "tag": "input",
                        "name": "brief",
                        "multiline": True,
                        "placeholder": {
                            "tag": "plain_text",
                            "content": "备注内容：填写文案大意，例如宿舍 group project，疲惫但好笑",
                        },
                        "default_value": "",
                    },
                    {
                        "tag": "button",
                        "name": "prompt_submit",
                        "action_type": "form_submit",
                        "text": {"tag": "plain_text", "content": "生成文案"},
                        "type": "primary",
                        "value": {"action": "prompt_form_submit", **card_user_value(user_ctx)},
                    },
                ],
            },
            {
                "tag": "note",
                "elements": [
                    {
                        "tag": "plain_text",
                        "content": "生成后会发送机器人确认卡片；表格只记录文案、状态和视频链接。",
                    }
                ],
            },
        ],
    }


def manual_prompt_entry_card(user_ctx: Optional[dict] = None) -> dict:
    default_model = setting("DEFAULT_MODEL", "seedance2.0fast_vip")
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "OKIVIVI 文案输入"},
            "template": "green",
        },
        "elements": [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": (
                        "**批量粘贴完整分镜文案。**\n"
                        "多条文案请用单独一行 `&` 分隔；系统会按 `&` 自动判断文案条数。\n"
                        "示例：第一条完整分镜\\n&\\n第二条完整分镜"
                    ),
                },
            },
            {
                "tag": "form",
                "name": "manual_prompt_form",
                "elements": [
                    {
                        "tag": "select_static",
                        "placeholder": {"tag": "plain_text", "content": "视频时长"},
                        "initial_option": "15",
                        "name": "manual_duration",
                        "required": True,
                        "options": [
                            {"text": {"tag": "plain_text", "content": f"{second}s"}, "value": str(second)}
                            for second in range(4, 16)
                        ],
                    },
                    {
                        "tag": "select_static",
                        "placeholder": {"tag": "plain_text", "content": "角色"},
                        "initial_option": "single_vivi",
                        "name": "manual_character_mode",
                        "required": True,
                        "options": [
                            {"text": {"tag": "plain_text", "content": "1. vivi"}, "value": "single_vivi"},
                            {"text": {"tag": "plain_text", "content": "2. bree, sunny"}, "value": "bree_sunny"},
                        ],
                    },
                    {
                        "tag": "select_static",
                        "placeholder": {"tag": "plain_text", "content": "调用模型"},
                        "initial_option": default_model,
                        "name": "manual_model_version",
                        "required": True,
                        "options": [
                            {"text": {"tag": "plain_text", "content": label}, "value": value}
                            for label, value in MODEL_OPTIONS
                        ],
                    },
                    {
                        "tag": "input",
                        "name": "manual_prompt",
                        "placeholder": {
                            "tag": "plain_text",
                            "content": "粘贴完整分镜文案；多条文案请用单独一行 & 分隔",
                        },
                        "default_value": "",
                        "required": True,
                        "multiline": True,
                    },
                    {
                        "tag": "input",
                        "name": "manual_note",
                        "placeholder": {"tag": "plain_text", "content": "备注，可留空"},
                        "default_value": "",
                    },
                    {
                        "tag": "button",
                        "name": "manual_prompt_submit",
                        "action_type": "form_submit",
                        "text": {"tag": "plain_text", "content": "上传到工作流表"},
                        "type": "primary",
                        "value": {"action": "manual_prompt_submit", **card_user_value(user_ctx)},
                    },
                ],
            },
            {
                "tag": "note",
                "elements": [
                    {
                        "tag": "plain_text",
                        "content": "上传后会发送机器人确认卡片；多条文案请用单独一行 & 分隔。",
                    }
                ],
            },
        ],
    }


def workspace_setup_card(user_ctx: Optional[dict] = None) -> dict:
    ctx = user_ctx or user_context("")
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {"tag": "plain_text", "content": "初始化 OKIVIVI 工作区"},
        },
        "elements": [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": (
                        "**首次使用只需要点一次。**\n"
                        "我会自动为你创建并绑定：工作流表、个人任务目录。"
                    ),
                },
            },
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "初始化我的工作区"},
                        "type": "primary",
                        "value": {"action": "setup_workspace", **card_user_value(ctx)},
                    }
                ],
            },
        ],
    }


def welcome_text() -> str:
    return (
        "欢迎使用 OKIVIVI 机器人。\n"
        "请直接输入数字选择功能：\n"
        "1：DeepSeek文案生成\n"
        "2：文案自行输入\n"
        "3：DeepSeek动画生成\n"
        "4：账号管理\n\n"
        "首次使用请点击我发出的“初始化我的工作区”按钮。"
    )


def welcome_card(user_ctx: Optional[dict] = None) -> dict:
    ctx = (user_ctx or {}).get("user_ctx") if isinstance(user_ctx, dict) and isinstance(user_ctx.get("user_ctx"), dict) else user_ctx
    ctx = ctx or user_context("")
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {"tag": "plain_text", "content": "欢迎使用 OKIVIVI 机器人"},
        },
        "elements": [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": "请选择要使用的功能。首次使用请先点击我发出的“初始化我的工作区”按钮。",
                },
            },
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "1 DeepSeek文案生成"},
                        "type": "primary",
                        "value": {"action": "menu_prompt_generate", **card_user_value(ctx)},
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "2 文案自行输入"},
                        "type": "primary",
                        "value": {"action": "menu_manual_prompt", **card_user_value(ctx)},
                    },
                ],
            },
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "3 DeepSeek动画生成（待开发）"},
                        "value": {"action": "menu_animation_todo", **card_user_value(ctx)},
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "4 账号管理"},
                        "value": {"action": "menu_account_management", **card_user_value(ctx)},
                    },
                ],
            },
        ],
    }


def jimeng_account_help() -> str:
    return (
        "即梦账号管理：\n"
        "1：查看已保存账号\n"
        "2：查看当前账号\n"
        "3：新增账号并获取授权链接\n"
        "4：保存刚授权账号\n"
        "5：切换账号"
    )


def account_management_card(user_ctx: Optional[dict] = None) -> dict:
    ctx = user_ctx or user_context("")
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {"tag": "plain_text", "content": "即梦账号管理"},
        },
        "elements": [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": (
                        f"当前默认账号：**{jimeng_account_display_name(str(ctx.get('jimeng_account') or '')) if ctx.get('jimeng_account') else '(未设置)'}**\n"
                        "选择下面按钮即可操作。"
                    ),
                },
            },
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "1 账号列表"},
                        "value": {"action": "account_list", **card_user_value(ctx)},
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "2 当前账号"},
                        "value": {"action": "account_current", **card_user_value(ctx)},
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "3 新增账号"},
                        "type": "primary",
                        "value": {"action": "account_add", **card_user_value(ctx)},
                    },
                ],
            },
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "4 保存刚授权账号"},
                        "value": {"action": "account_save_pending", **card_user_value(ctx)},
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "5 切换账号"},
                        "value": {"action": "account_switch_menu", **card_user_value(ctx)},
                    },
                ],
            },
        ],
    }


def account_switch_card(user_ctx: Optional[dict] = None) -> dict:
    ctx = user_ctx or user_context("")
    accounts = saved_jimeng_accounts()
    actions = []
    for index, account in enumerate(accounts[:8], start=1):
        label = jimeng_account_display_name(account, index)
        actions.append({
            "tag": "button",
            "text": {"tag": "plain_text", "content": label},
            "type": "primary" if account == ctx.get("jimeng_account") else "default",
            "value": {"action": "account_switch", "account_name": account, **card_user_value(ctx)},
        })
    if not actions:
        actions = [{
            "tag": "button",
            "text": {"tag": "plain_text", "content": "先新增账号"},
            "type": "primary",
            "value": {"action": "account_add", **card_user_value(ctx)},
        }]
    rows = []
    for idx in range(0, len(actions), 3):
        rows.append({"tag": "action", "actions": actions[idx:idx + 3]})
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {"tag": "plain_text", "content": "切换即梦账号"},
        },
        "elements": [
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": "点击要使用的账号。"},
            },
            *rows,
        ],
    }


def task_brief(task: dict) -> str:
    task_id = str(task.get("task_id") or "(unknown)")
    model = normalize_model(task.get("model_version"))
    account = str(task.get("jimeng_account") or setting("DEFAULT_JIMENG_ACCOUNT", "") or "(default)")
    tenant = str(task.get("tenant_id") or "(no-tenant)")
    retry_after = float(task.get("retry_after_ts") or 0)
    retry_note = ""
    if retry_after and time.time() < retry_after:
        retry_note = f" | retry_after {max(0, int(retry_after - time.time()))}s"
    return f"{task_id} | {account} | {model} | {tenant}{retry_note}"


def collect_tasks(status: str, limit: int = 20) -> List[dict]:
    tasks: List[dict] = []
    if status not in TASK_DIRS:
        return tasks
    for path in iter_task_paths(status):
        try:
            task = read_task(path)
        except Exception as exc:
            tasks.append({"task_id": path.stem, "model_version": "invalid-json", "jimeng_account": "", "tenant_id": "", "error": str(exc)})
            if len(tasks) >= limit:
                break
            continue
        tasks.append(task)
        if len(tasks) >= limit:
            break
    return tasks


def run_jimeng_account_command(args: List[str]) -> str:
    script = ROOT / "jimeng_account.sh"
    if not script.exists():
        return f"未找到账号脚本: {script}"
    proc = subprocess.run(
        ["bash", str(script), *args],
        cwd=str(ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=60,
    )
    return proc.stdout.strip() or "(empty)"


def jimeng_profile_exists(account_name: str) -> bool:
    profile = ROOT / "accounts" / "jimeng" / account_name / "home"
    return profile.exists()


def jimeng_account_root() -> Path:
    return ROOT / "accounts" / "jimeng"


def saved_jimeng_accounts() -> List[str]:
    base = jimeng_account_root()
    if not base.exists():
        return []
    accounts = []
    for path in base.iterdir():
        if path.name.startswith("_"):
            continue
        if path.is_dir() and (path / "home").exists():
            accounts.append(path.name)
    return sorted(accounts)


def jimeng_account_meta_path(account_name: str) -> Path:
    return jimeng_account_root() / account_name / "meta.json"


def read_jimeng_account_meta(account_name: str) -> dict:
    path = jimeng_account_meta_path(account_name)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def write_jimeng_account_meta(account_name: str, updates: dict) -> dict:
    meta = read_jimeng_account_meta(account_name)
    meta.update({k: v for k, v in updates.items() if v not in {None, ""}})
    path = jimeng_account_meta_path(account_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def extract_account_display_name(text: str) -> str:
    if not text:
        return ""
    patterns = [
        r'"(?:nickname|nick_name|screen_name|user_name|username|name)"\s*:\s*"([^"]{2,40})"',
        r"'(?:nickname|nick_name|screen_name|user_name|username|name)'\s*:\s*'([^']{2,40})'",
        r"(?:nickname|nick_name|screen_name|user_name|username|name)\s*[:=]\s*([^\n\r,，]{2,40})",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            value = str(match.group(1) or "").strip().strip('"\' ')
            if value and not value.lower().startswith(("http", "dreamina", "jimeng")):
                return value
    user_match = re.search(r"user_id\s*[:=]\s*([0-9]{6,})", text, flags=re.IGNORECASE)
    if user_match:
        uid = user_match.group(1)
        return f"即梦账号 {uid[-4:]}"
    return ""


def discover_jimeng_account_display_name(account_name: str) -> str:
    profile_home = jimeng_account_root() / account_name / "home"
    if not profile_home.exists():
        return ""
    for path in profile_home.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {"", ".json", ".txt", ".yaml", ".yml", ".toml", ".conf"}:
            continue
        try:
            if path.stat().st_size > 1024 * 1024:
                continue
            display = extract_account_display_name(path.read_text(encoding="utf-8", errors="ignore"))
            if display:
                return display
        except Exception:
            continue
    return ""


def jimeng_account_display_name(account_name: str, index: int = 0) -> str:
    meta = read_jimeng_account_meta(account_name)
    display = str(meta.get("display_name") or "").strip()
    if not display:
        display = discover_jimeng_account_display_name(account_name)
        if display:
            write_jimeng_account_meta(account_name, {"display_name": display, "profile_name": account_name})
    if display:
        return display[:30]
    if index:
        return f"账号 {index}"
    return "即梦账号"


def jimeng_accounts_text() -> str:
    accounts = saved_jimeng_accounts()
    if not accounts:
        return "(暂无)"
    lines = []
    for index, account in enumerate(accounts, start=1):
        lines.append(f"{index}. {jimeng_account_display_name(account, index)}")
    return "\n".join(lines)


def pending_login_path() -> Path:
    path = ROOT / "accounts" / "jimeng" / "pending_logins.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def read_pending_logins() -> dict:
    path = pending_login_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def write_pending_logins(data: dict) -> None:
    pending_login_path().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def next_jimeng_account_name(user_ctx: Optional[dict] = None) -> str:
    tenant = slug((user_ctx or {}).get("tenant_id") or "user", "user")
    existing = set(saved_jimeng_accounts()) | set(read_pending_logins().keys())
    for index in range(1, 100):
        candidate = f"{tenant}_account_{index:02d}"
        if candidate not in existing:
            return candidate
    return f"{tenant}_account_{int(time.time())}"


def next_jimeng_account_display_label(user_ctx: Optional[dict] = None) -> str:
    owner_name = str((user_ctx or {}).get("owner_name") or "").strip()
    base = owner_name if owner_name and not owner_name.startswith("ou_") else "即梦账号"
    return f"{base} {len(saved_jimeng_accounts()) + 1}"


def dreamina_command() -> str:
    for candidate in [
        str(Path.home() / ".local" / "bin" / "dreamina"),
        shutil.which("dreamina") or "",
    ]:
        if candidate and Path(candidate).exists():
            return candidate
    return "dreamina"


def start_jimeng_login(account_name: str, user_ctx: Optional[dict] = None) -> str:
    account_name = slug(account_name, "account")
    command = dreamina_command()
    proc = subprocess.run(
        [command, "login", "--headless"],
        cwd=str(ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=60,
    )
    output = proc.stdout.strip()
    if proc.returncode != 0:
        raise RuntimeError(output[-800:] or f"dreamina login exited {proc.returncode}")
    if "已复用当前本地 OAuth 登录态" in output or "Reuse the current local OAuth login state" in output:
        subprocess.run(
            [command, "logout"],
            cwd=str(ROOT),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=30,
        )
        proc = subprocess.run(
            [command, "login", "--headless"],
            cwd=str(ROOT),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=60,
        )
        output = proc.stdout.strip()
        if proc.returncode != 0:
            raise RuntimeError(output[-800:] or f"dreamina login after logout exited {proc.returncode}")
    link_match = re.search(r"verification_uri_complete:\s*(https?://\S+)", output)
    if not link_match:
        link_match = re.search(r"verification_uri:\s*(https?://\S+)", output)
    if not link_match:
        link_match = re.search(r"(https://jimeng\.jianying\.com/[^\s]+)", output)
    device_match = re.search(r"device_code:\s*([A-Za-z0-9._-]+)", output)
    user_match = re.search(r"user_code:\s*([A-Za-z0-9._-]+)", output)
    if not link_match:
        raise RuntimeError(f"未能从即梦 CLI 输出中识别授权链接。输出：{output[-500:]}")
    login_url = link_match.group(1).strip().rstrip("，,。.)]")
    pending = read_pending_logins()
    pending[account_name] = {
        "account_name": account_name,
        "display_name": next_jimeng_account_display_label(user_ctx),
        "device_code": device_match.group(1) if device_match else "",
        "user_code": user_match.group(1) if user_match else "",
        "login_url": login_url,
        "owner_open_id": (user_ctx or {}).get("owner_open_id", ""),
        "tenant_id": (user_ctx or {}).get("tenant_id", ""),
        "created_at": now(),
    }
    write_pending_logins(pending)
    return (
        f"即梦账号授权：{account_name}\n"
        f"点击链接完成授权：\n{login_url}\n\n"
        "授权完成后回到机器人点击“保存刚授权账号”。"
    )


def latest_pending_login_name(user_ctx: Optional[dict] = None) -> str:
    pending = read_pending_logins()
    if not pending:
        return ""
    owner_open_id = str((user_ctx or {}).get("owner_open_id") or "")
    candidates = []
    for name, info in pending.items():
        if owner_open_id and str(info.get("owner_open_id") or "") not in {"", owner_open_id}:
            continue
        candidates.append((str(info.get("created_at") or ""), name))
    if not candidates:
        return ""
    return sorted(candidates)[-1][1]


def save_jimeng_login(account_name: str) -> str:
    account_name = slug(account_name, "account")
    pending = read_pending_logins()
    info = pending.get(account_name, {})
    device_code = info.get("device_code", "")
    if device_code:
        check = run_jimeng_account_command(["checklogin", device_code])
        if "成功" not in check and "success" not in check.lower() and "当前登录账户信息" not in check:
            return f"授权还没有完成，请先打开授权链接。\n授权链接：{info.get('login_url', '(missing)')}"
    save_output = run_jimeng_account_command(["save", account_name])
    display_name = str(info.get("display_name") or "").strip()
    discovered_name = extract_account_display_name(save_output) or discover_jimeng_account_display_name(account_name)
    write_jimeng_account_meta(
        account_name,
        {
            "profile_name": account_name,
            "display_name": discovered_name or display_name or account_name,
            "last_saved_at": now(),
        },
    )
    pending.pop(account_name, None)
    write_pending_logins(pending)
    return f"已保存即梦账号：{jimeng_account_display_name(account_name)}\n{save_output}"


def save_pending_jimeng_login(user_ctx: Optional[dict] = None) -> str:
    account_name = latest_pending_login_name(user_ctx)
    if not account_name:
        return "没有待保存的授权账号。请先点击“新增账号”获取授权链接。"
    result = save_jimeng_login(account_name)
    owner_open_id = str((user_ctx or {}).get("owner_open_id") or "")
    if owner_open_id:
        update_user_config(owner_open_id, {"jimeng_account": account_name})
    return result + "\n已设为你的默认即梦账号。"


def current_account_text(user_ctx: Optional[dict] = None) -> str:
    ctx = user_ctx or {}
    current = str(ctx.get("jimeng_account") or "")
    return (
        "当前账号信息：\n"
        f"你的默认即梦账号：{jimeng_account_display_name(current) if current else '(未设置)'}\n"
        f"系统默认即梦账号：{setting('DEFAULT_JIMENG_ACCOUNT', '(empty)')}\n"
        f"当前激活 profile：{run_jimeng_account_command(['current'])}"
    )


def set_env_value(key: str, value: str) -> None:
    env_path = ROOT / ".env"
    lines = env_path.read_text(encoding="utf-8-sig").splitlines() if env_path.exists() else []
    replaced = False
    for idx, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[idx] = f"{key}={value}"
            replaced = True
            break
    if not replaced:
        lines.append(f"{key}={value}")
    env_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    ENV[key] = value


def find_task(task_id: str) -> Optional[Path]:
    for status in TASK_DIRS:
        for directory in iter_status_dirs(status):
            path = directory / f"{task_id}.json"
            if path.exists():
                return path
    return None


def validate_task(task: dict) -> List[str]:
    errors: List[str] = []
    normalize_task_images(task)
    prompt = Path(task.get("prompt_file", ""))
    if not prompt.exists():
        errors.append(f"Prompt file not found: {prompt}")
    else:
        try:
            if not prompt.read_text(encoding="utf-8").strip():
                errors.append(f"Prompt file is empty: {prompt}")
        except UnicodeDecodeError:
            errors.append(f"Prompt file is not UTF-8: {prompt}")
    images = task.get("images", [])
    image_source = str(task.get("image_source") or "")
    if not images and not task.get("review_bitable_record_id") and image_source != "manual_bitable":
        errors.append("At least one image is required.")
    if len(images) > 4:
        errors.append("At most 4 images are supported by this workflow.")
    for image in images:
        if not Path(image).exists():
            errors.append(f"Image file not found: {image}")
    return errors


def run_command(command: List[str], cwd: Path, log_file: Path) -> subprocess.CompletedProcess:
    log(f"Running: {' '.join(command)}")
    env = os.environ.copy()
    for key in ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"]:
        env.pop(key, None)
    extra_path = f"{Path.home() / '.local' / 'bin'}:/usr/local/bin:/usr/bin:/bin"
    env["PATH"] = f"{extra_path}:{env.get('PATH', '')}"
    with log_file.open("w", encoding="utf-8") as f:
        proc = subprocess.run(
            command,
            cwd=str(cwd),
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=int(setting("POLL_SECONDS", "300")) + 120,
        )
        f.write(proc.stdout)
        return proc


def dreamina_command() -> str:
    configured = setting("DREAMINA_BIN", "").strip()
    candidates = [
        configured,
        str(Path.home() / ".local" / "bin" / "dreamina"),
        "/usr/local/bin/dreamina",
        "/usr/bin/dreamina",
        shutil.which("dreamina") or "",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return configured or "dreamina"


def dreamina_subprocess_env() -> Dict[str, str]:
    env = os.environ.copy()
    for key in ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"]:
        env.pop(key, None)
    extra_path = f"{Path.home() / '.local' / 'bin'}:/usr/local/bin:/usr/bin:/bin"
    env["PATH"] = f"{extra_path}:{env.get('PATH', '')}"
    return env


def dreamina_login_valid() -> bool:
    proc = subprocess.run(
        [dreamina_command(), "user_credit"],
        cwd=str(ROOT),
        env=dreamina_subprocess_env(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=45,
    )
    if proc.returncode == 0:
        return True
    log(f"Dreamina login validation failed:\n{proc.stdout}")
    return False


def use_jimeng_profile(script: Path, account: str) -> None:
    proc = subprocess.run(
        ["bash", str(script), "use", account],
        cwd=str(ROOT),
        env=dreamina_subprocess_env(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=60,
    )
    log(f"Jimeng account switch output for {account}:\n{proc.stdout}")
    if proc.returncode != 0:
        raise RuntimeError(f"Failed to switch Jimeng account to {account}: {proc.stdout}")


def switch_jimeng_account(account: str) -> None:
    account = (account or setting("DEFAULT_JIMENG_ACCOUNT", "")).strip()
    if not account:
        return
    script = ROOT / "jimeng_account.sh"
    if not script.exists():
        raise RuntimeError(f"Jimeng account switch script not found: {script}")
    current_proc = subprocess.run(
        ["bash", str(script), "current"],
        cwd=str(ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=30,
    )
    active_paths = [
        Path.home() / ".dreamina_cli",
        Path.home() / ".config" / "dreamina",
        Path.home() / ".config" / "dreamina_cli",
    ]
    if current_proc.returncode == 0 and current_proc.stdout.strip() == account and any(path.exists() for path in active_paths):
        if dreamina_login_valid():
            log(f"Jimeng account already active; skip switch: {account}")
            return
        log(f"Jimeng account marked active but login is invalid; restoring profile: {account}")
    use_jimeng_profile(script, account)
    if dreamina_login_valid():
        return
    fallback = setting("DEFAULT_JIMENG_ACCOUNT", "").strip()
    if fallback and fallback != account and jimeng_profile_exists(fallback):
        log(f"Jimeng account {account} is invalid; falling back to {fallback}")
        use_jimeng_profile(script, fallback)
        if dreamina_login_valid():
            return
    raise RuntimeError(f"Jimeng account has no valid login state: {account}")


def extract_json(stdout: str) -> dict:
    start = stdout.find("{")
    end = stdout.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No JSON object found in dreamina output.")
    return json.loads(stdout[start : end + 1])


def download_video(url: str, output: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=120) as resp, output.open("wb") as f:
        shutil.copyfileobj(resp, f)


def find_downloaded_video(out_dir: Path) -> Optional[Path]:
    preferred = out_dir / "video.mp4"
    if preferred.exists() and preferred.stat().st_size > 0:
        return preferred
    candidates = sorted(
        (
            path
            for path in out_dir.glob("*.mp4")
            if path.is_file() and path.stat().st_size > 0
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def output_is_success(task_id: str) -> bool:
    task_path_found = find_task(task_id)
    task = read_task(task_path_found) if task_path_found else {"task_id": task_id}
    out_dir = output_dir_for_task(task)
    result_path = out_dir / "result.json"
    video_path = out_dir / "video.mp4"
    if not result_path.exists() or not video_path.exists():
        return False
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    return result.get("gen_status") == "success"


def should_retry_dreamina_result(result: dict) -> bool:
    fail_reason = str(result.get("fail_reason") or "").lower()
    return "upload phase, no file upload" in fail_reason or "upload image" in fail_reason


def is_dreamina_concurrency_error(message: str) -> bool:
    text = str(message or "").lower()
    return "exceedconcurrencylimit" in text or "exceed concurrency" in text


def dreamina_is_waiting(result: dict) -> bool:
    status = str(result.get("gen_status") or "").lower()
    queue_status = str(((result.get("queue_info") or {}).get("queue_status")) or "").lower()
    return status in {"querying", "running", "submitted"} or queue_status in {"queueing", "running"}


def query_dreamina_until_done(submit_id: str, out_dir: Path) -> dict:
    if not submit_id:
        raise RuntimeError("Dreamina is still querying but no submit_id was returned.")
    deadline = time.time() + int(setting("QUERY_RESULT_SECONDS", "1800"))
    interval = max(10, int(setting("QUERY_RESULT_INTERVAL", "60")))
    attempt = 0
    last_result: dict = {}
    while time.time() <= deadline:
        attempt += 1
        command = [
            dreamina_command(),
            "query_result",
            f"--submit_id={submit_id}",
            "--download_dir",
            str(out_dir),
        ]
        proc = run_command(command, ROOT, out_dir / f"query.{attempt}.log")
        if proc.returncode != 0:
            raise RuntimeError(f"dreamina query_result exited {proc.returncode}. See {out_dir / f'query.{attempt}.log'}")
        last_result = extract_json(proc.stdout)
        (out_dir / "result.json").write_text(json.dumps(last_result, ensure_ascii=False, indent=2), encoding="utf-8")
        if last_result.get("gen_status") == "success":
            return last_result
        if not dreamina_is_waiting(last_result):
            return last_result
        log(f"Dreamina task still waiting: submit_id={submit_id}; status={last_result.get('gen_status')}; next query in {interval}s")
        time.sleep(interval)
    raise RuntimeError(
        f"Dreamina still querying after {int(setting('QUERY_RESULT_SECONDS', '1800'))}s; submit_id={submit_id}"
    )


def run_generation(task: dict, api: FeishuApi) -> None:
    task_id = task["task_id"]
    normalize_task_images(task)
    out_dir = output_dir_for_task(task)
    out_dir.mkdir(parents=True, exist_ok=True)
    switch_jimeng_account(str(task.get("jimeng_account") or ""))
    task["duration"] = clamp_duration(task.get("duration"))
    task["model_version"] = normalize_model(task.get("model_version"))
    task["ratio"] = task.get("ratio") or setting("DEFAULT_RATIO", "9:16")
    task["video_resolution"] = task.get("video_resolution") or setting("DEFAULT_RESOLUTION", "720p")
    (out_dir / "task.json").write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
    result_path = out_dir / "result.json"
    result: dict = {}
    if result_path.exists():
        try:
            existing_result = json.loads(result_path.read_text(encoding="utf-8"))
        except Exception:
            existing_result = {}
        if dreamina_is_waiting(existing_result) and existing_result.get("submit_id"):
            task["submit_id"] = existing_result.get("submit_id")
            (out_dir / "task.json").write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
            notify_task_text(api, task, f"⏳ 继续查询已提交的即梦任务\n任务: {task_id}\nsubmit_id: {task.get('submit_id')}")
            result = query_dreamina_until_done(str(task.get("submit_id") or ""), out_dir)

    shutil.copyfile(task["prompt_file"], out_dir / "prompt.txt")
    staged_images: List[str] = []
    for idx, image in enumerate(task["images"], start=1):
        source = Path(image)
        suffix = source.suffix.lower() or ".png"
        staged = out_dir / f"image{idx}{suffix}"
        shutil.copyfile(source, staged)
        staged_images.append(str(staged))

    command = [dreamina_command(), "multimodal2video"]
    for image in staged_images:
        command += ["--image", image]
    command += [
        "--prompt",
        Path(task["prompt_file"]).read_text(encoding="utf-8"),
        "--model_version",
        task["model_version"],
        "--ratio",
        task["ratio"],
        "--duration",
        str(task["duration"]),
        "--video_resolution",
        task["video_resolution"],
        "--poll",
        setting("POLL_SECONDS", "300"),
    ]

    if result.get("gen_status") != "success":
        for attempt in range(2):
            log_name = "run.log" if attempt == 0 else f"run.retry{attempt}.log"
            proc = run_command(command, ROOT, out_dir / log_name)
            if proc.returncode != 0:
                raise RuntimeError(f"dreamina exited {proc.returncode}. See {out_dir / log_name}")
            result = extract_json(proc.stdout)
            (out_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            if result.get("gen_status") == "success":
                break
            if dreamina_is_waiting(result):
                task["submit_id"] = result.get("submit_id")
                (out_dir / "task.json").write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
                notify_task_text(api, task, f"⏳ 即梦任务已提交，正在排队/生成中\n任务: {task_id}\nsubmit_id: {task.get('submit_id')}")
                result = query_dreamina_until_done(str(task.get("submit_id") or ""), out_dir)
                if result.get("gen_status") == "success":
                    break
                raise RuntimeError(result.get("fail_reason") or f"Dreamina status: {result.get('gen_status')}")
            if attempt == 0 and should_retry_dreamina_result(result):
                log(f"Dreamina upload failed once; retrying task {task_id}")
                time.sleep(8)
                continue
            raise RuntimeError(result.get("fail_reason") or f"Dreamina status: {result.get('gen_status')}")
    videos = (result.get("result_json") or {}).get("videos") or []
    video_url = videos[0].get("video_url") if videos else ""
    canonical_video = out_dir / "video.mp4"
    if video_url:
        download_video(video_url, canonical_video)
    else:
        downloaded_video = find_downloaded_video(out_dir)
        if not downloaded_video:
            raise RuntimeError("Dreamina succeeded but no video_url was returned and no downloaded mp4 was found.")
        if downloaded_video != canonical_video:
            shutil.copyfile(downloaded_video, canonical_video)
    task["submit_id"] = result.get("submit_id")
    task["output_dir"] = str(out_dir)
    task["video_file"] = str(canonical_video)
    upload = api.upload_file_to_drive(canonical_video, f"{task_id}.mp4")
    task["feishu_video_file_token"] = upload["file_token"]
    task["video_url"] = upload["url"]
    write_task("done", task)
    if task.get("review_backend") == "bitable" or task.get("review_bitable_record_id") or task.get("script_bitable_record_id"):
        update_task_workflow_record(api, task, {"状态": "success", "视频链接": task["video_url"], "错误原因": ""}, "success")
    elif task.get("review_doc_id") and task.get("review_doc_video_cell_id"):
        try:
            api.append_video_link_to_doc(task["review_doc_id"], task["review_doc_video_cell_id"], video_url)
        except Exception as exc:
            log(f"Failed to write video link to review doc for {task_id}: {exc}")
            api.append_doc_text(task["review_doc_id"], None, f"视频链接：{video_url}")
    notify_task_text(api, task, f"✅ 即梦视频生成成功\n任务: {task_id}\nsubmit_id: {task.get('submit_id')}")
    notify_task_card(api, task, result_card(task))
    notify_task_card(api, task, result_status_card(str(task.get("owner_open_id") or "")))


class Worker:
    def __init__(self, api: FeishuApi) -> None:
        self.api = api
        self._running: set[str] = set()
        self._running_lanes: Dict[str, str] = {}
        self._active_account: str = ""
        self._queue_lock = threading.Lock()

    def account_key(self, task: dict) -> str:
        return str(task.get("jimeng_account") or setting("DEFAULT_JIMENG_ACCOUNT", "") or "__default__").strip()

    def lane_key(self, task: dict) -> str:
        account = self.account_key(task)
        model = normalize_model(task.get("model_version"))
        if model_allows_parallel(model):
            return f"{account}:{model}:{task['task_id']}"
        return f"{account}:serial"

    def account_lock_path(self, account: str) -> Path:
        lock_dir = LOCKS / "jimeng"
        lock_dir.mkdir(parents=True, exist_ok=True)
        return lock_dir / f"{slug(account, '__default__')}.lock"

    def acquire_account_lock(self, account: str, task_id: str) -> bool:
        payload = json.dumps(
            {"account": account, "task_id": task_id, "pid": os.getpid(), "started_at": now()},
            ensure_ascii=False,
            indent=2,
        )
        lock_path = self.account_lock_path(account)
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        return True

    def release_account_lock(self, account: str, task_id: str) -> None:
        lock_path = self.account_lock_path(account)
        if not lock_path.exists():
            return
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass

    def account_queue_size(self, account: str) -> int:
        count = 0
        for path in iter_task_paths("reviewing"):
            try:
                task = read_task(path)
            except Exception:
                continue
            if self.account_key(task) != account:
                continue
            if not task.get("review_auto_scan_enabled"):
                continue
            retry_after = float(task.get("retry_after_ts") or 0)
            if retry_after and time.time() < retry_after:
                continue
            count += 1
        return count

    def queue_status_text(self, user_ctx: Optional[dict] = None) -> str:
        user_ctx = user_ctx or {}
        owner_open_id = str(user_ctx.get("owner_open_id") or "")
        tenant_id = str(user_ctx.get("tenant_id") or "")

        status_counts = {
            status: len(iter_task_paths(status))
            for status in TASK_DIRS
        }

        reviewing = collect_tasks("reviewing", 200)
        running = collect_tasks("running", 50)
        pending = collect_tasks("pending", 50)
        failed = collect_tasks("failed", 5)

        def belongs(task: dict) -> bool:
            if not owner_open_id and not tenant_id:
                return True
            return (
                (owner_open_id and task.get("owner_open_id") == owner_open_id)
                or (tenant_id and task.get("tenant_id") == tenant_id)
                or (not task.get("owner_open_id") and not task.get("tenant_id"))
            )

        my_reviewing = [task for task in reviewing if belongs(task)]
        my_running = [task for task in running if belongs(task)]
        my_pending = [task for task in pending if belongs(task)]

        accounts: Dict[str, dict] = {}
        for task in reviewing + running:
            account = self.account_key(task)
            info = accounts.setdefault(account, {"reviewing": 0, "running": 0, "models": set()})
            status = "running" if task in running else "reviewing"
            info[status] += 1
            info["models"].add(normalize_model(task.get("model_version")))

        active_lanes = []
        with self._queue_lock:
            for lane, task_id in sorted(self._running_lanes.items()):
                active_lanes.append(f"{lane} -> {task_id}")
            active_account = self._active_account

        lines = [
            "OKIVIVI 队列状态",
            f"你的 tenant_id: {tenant_id or '(empty)'}",
            f"你的默认即梦账号: {user_ctx.get('jimeng_account') or '(empty)'}",
            "",
            "全局任务数量："
            f" pending={status_counts.get('pending', 0)},"
            f" reviewing={status_counts.get('reviewing', 0)},"
            f" running={status_counts.get('running', 0)},"
            f" done={status_counts.get('done', 0)},"
            f" failed={status_counts.get('failed', 0)}",
            "",
            f"你的待处理：pending={len(my_pending)}, reviewing={len(my_reviewing)}, running={len(my_running)}",
            f"当前激活即梦账号: {active_account or '(none)'}",
        ]

        if active_lanes:
            lines.append("")
            lines.append("当前运行 lane：")
            lines.extend(active_lanes[:8])
        else:
            lines.append("当前没有本进程内运行中的 lane。")

        if accounts:
            lines.append("")
            lines.append("账号队列：")
            for account, info in sorted(accounts.items()):
                models = ", ".join(sorted(info["models"])) or "(none)"
                lines.append(f"{account}: running={info['running']}, reviewing={info['reviewing']}, models={models}")

        if my_running:
            lines.append("")
            lines.append("你的运行中任务：")
            lines.extend(task_brief(task) for task in my_running[:5])
        if my_reviewing:
            lines.append("")
            lines.append("你的待审核/待生成任务：")
            lines.extend(task_brief(task) for task in my_reviewing[:8])
        if failed:
            lines.append("")
            lines.append("最近失败任务：")
            for task in failed:
                reason = str(task.get("fail_reason") or task.get("error") or "")[:80]
                lines.append(f"{task_brief(task)} | {reason}")

        return "\n".join(lines)

    def scan_pending_once(self) -> None:
        for path in iter_task_paths("pending"):
            claim_path = path.with_name(f"{path.name}.claiming")
            try:
                try:
                    path.rename(claim_path)
                except FileNotFoundError:
                    continue
                task = read_task(claim_path)
                task["duration"] = clamp_duration(task.get("duration"))
                errors = validate_task(task)
                if errors:
                    task["fail_reason"] = "\n".join(errors)
                    move_task(claim_path, "failed", task)
                    notify_task_text(self.api, task, f"❌ 任务校验失败: {task.get('task_id')}\n{task['fail_reason']}")
                    continue
                ensure_script_review_record(task, self.api)
                if task.get("deduped_script_record"):
                    task["status"] = "deduped"
                    task["dedupe_reason"] = "same task_id and prompt already exists in Feishu script bitable"
                    move_task(claim_path, "done", task)
                    log(f"Skipped duplicate script already in Feishu bitable: {task['task_id']}")
                    continue
                task["review_card_sent_at"] = now()
                move_task(claim_path, "reviewing", task)
                notify_task_card(self.api, task, script_review_card(task))
                log(f"Sent script review card for {task['task_id']}")
            except Exception as exc:
                if claim_path.exists():
                    try:
                        claim_path.rename(path)
                    except Exception:
                        pass
                log(f"Failed to scan task {path}: {exc}\n{traceback.format_exc()}")

    def scan_reviewing_once(self) -> None:
        for path in iter_task_paths("reviewing"):
            try:
                task_id = path.stem
                if task_id in self._running:
                    continue
                task = read_task(path)
                if not task.get("card_approved"):
                    continue
                retry_after = float(task.get("retry_after_ts") or 0)
                if retry_after and time.time() < retry_after:
                    continue
                if task.get("card_approved"):
                    ensure_task_images_from_suggestion(task)
                    self.start_generation(path, task)
                    continue
                if not task.get("review_bitable_record_id") and task.get("script_bitable_record_id"):
                    if promote_script_to_review(task, self.api):
                        write_task("reviewing", task)
                        if task.get("deduped_review_record"):
                            task["status"] = "deduped"
                            task["dedupe_reason"] = "same task_id and prompt already exists in Feishu review bitable"
                            move_task(path, "done", task)
                            log(f"Skipped duplicate review already in Feishu bitable: {task_id}")
                            continue
                        notify_task_card(self.api, task, review_card(task))
                        log(f"Sent review card after script confirmation for {task_id}")
                    else:
                        if task.pop("_script_record_missing", False):
                            move_task(path, "failed", task)
                            continue
                        write_task("reviewing", task)
                    continue
                if not task.get("review_auto_scan_enabled"):
                    continue
                if output_is_success(task_id):
                    task["output_dir"] = str(output_dir_for_task(task))
                    move_task(path, "done", task)
                    continue
                refresh_prompt_from_review_doc(task, self.api)
                if task.pop("_review_not_confirmed", False):
                    write_task("reviewing", task)
                    continue
                self.start_generation(path, task)
            except Exception as exc:
                log(f"Failed to scan reviewing task {path}: {exc}\n{traceback.format_exc()}")

    def start_generation(self, path: Path, task: dict) -> None:
        task_id = task["task_id"]
        account = self.account_key(task)
        model = normalize_model(task.get("model_version"))
        task["model_version"] = model
        lane = self.lane_key(task)
        with self._queue_lock:
            if task_id in self._running:
                return
            current_status = status_for_task(task_id)
            if current_status in {"running", "done"}:
                return
            if self._active_account and self._active_account != account:
                running_task_id = next(iter(self._running), "unknown")
                previous = task.get("queued_behind_task_id")
                task["queued_for_account"] = account
                task["queued_for_model"] = model
                task["queued_behind_task_id"] = running_task_id
                task["queued_at"] = task.get("queued_at") or now()
                write_task("reviewing", task)
                if previous != running_task_id:
                    log(f"Queued task by active Jimeng account: account={account}; active={self._active_account}; task={task_id}; running={running_task_id}")
                return
            running_task_id = self._running_lanes.get(lane)
            if running_task_id and running_task_id != task_id:
                previous = task.get("queued_behind_task_id")
                task["queued_for_account"] = account
                task["queued_for_model"] = model
                task["queued_behind_task_id"] = running_task_id
                task["queued_at"] = task.get("queued_at") or now()
                write_task("reviewing", task)
                if previous != running_task_id:
                    log(f"Queued task by Jimeng model lane: lane={lane}; task={task_id}; running={running_task_id}")
                return
            if not self._active_account and not self.acquire_account_lock(account, task_id):
                task["queued_for_account"] = account
                task["queued_for_model"] = model
                task["queued_behind_task_id"] = "external_worker_or_stale_lock"
                task["queued_at"] = task.get("queued_at") or now()
                write_task("reviewing", task)
                log(f"Queued task by Jimeng account file lock: account={account}; task={task_id}")
                return
            self._active_account = account
            self._running_lanes[lane] = task_id
            self._running.add(task_id)
        move_task(path, "running", task)
        if task.get("review_backend") == "bitable" or task.get("review_bitable_record_id") or task.get("script_bitable_record_id"):
            update_task_workflow_record(self.api, task, {"状态": "running", "错误原因": ""}, "running")
        mode = "VIP 并发通道" if model_allows_parallel(model) else "普通模型单并发队列"
        notify_task_text(self.api, task, f"▶️ 已通过审核，开始生成: {task_id}\n即梦账号: {account}\n模型: {model}\n队列: {mode}")
        thread = threading.Thread(target=self._run_generation_safe, args=(task,), daemon=True)
        thread.start()

    def approve(self, task_id: str, user_ctx: Optional[dict] = None, require_table_confirm: bool = True) -> None:
        reply_open_id = str((user_ctx or {}).get("owner_open_id") or "")
        if task_id in self._running:
            notify_text(self.api, f"⚠️ 任务正在生成中，已忽略重复通过: {task_id}", reply_open_id)
            return
        path = find_task(task_id)
        if not path:
            try:
                path = import_bitable_task(self.api, task_id, user_ctx)
            except Exception as exc:
                notify_text(self.api, f"❌ 导入多维表格任务失败，未开始生成\n任务: {task_id}\n原因: {exc}", reply_open_id)
                log(f"Failed to import bitable task {task_id}: {exc}\n{traceback.format_exc()}")
                return
            if not path:
                notify_text(self.api, f"⚠️ 未找到任务: {task_id}", reply_open_id)
                return
        current_status = status_for_task(task_id)
        if current_status in {"running", "done"}:
            notify_text(self.api, f"⚠️ 任务已处于 {current_status}，不会重复调用即梦: {task_id}", reply_open_id)
            return
        if output_is_success(task_id):
            task = read_task(path)
            task["output_dir"] = str(output_dir_for_task(task))
            move_task(path, "done", task)
            notify_task_text(self.api, task, f"⚠️ 任务已有生成结果，已标记 done，不会重复调用即梦: {task_id}")
            return
        task = read_task(path)
        if require_table_confirm:
            try:
                refresh_prompt_from_review_doc(task, self.api)
                if task.pop("_review_not_confirmed", False):
                    log(f"Task not confirmed in bitable, approve ignored silently: {task_id}")
                    write_task(current_status or "reviewing", task)
                    return
            except Exception as exc:
                notify_task_text(self.api, task, f"❌ 读取云文档失败，未开始生成\n任务: {task_id}\n原因: {exc}")
                log(f"Failed to refresh prompt from review doc for {task_id}: {exc}\n{traceback.format_exc()}")
                return
        else:
            ensure_task_images_from_suggestion(task)
            if not task.get("images"):
                notify_task_text(self.api, task, f"❌ 未找到可用于生成的图片\n任务: {task_id}\n图片选择: {task.get('image_suggestion') or '(empty)'}")
                write_task(current_status or "reviewing", task)
                return
            missing_images = [image for image in task.get("images", []) if not Path(image).exists()]
            if missing_images:
                notify_task_text(self.api, task, f"❌ 图片文件不存在，未开始生成\n任务: {task_id}\n" + "\n".join(missing_images))
                write_task(current_status or "reviewing", task)
                return
            task["review_backend"] = "bot_card"
            task["card_approved"] = True
            task["card_approved_at"] = now()
            write_task(current_status or "reviewing", task)
        self.start_generation(path, task)
        if status_for_task(task_id) == "reviewing":
            account = self.account_key(task)
            running_task_id = next((tid for lane, tid in self._running_lanes.items() if lane.startswith(f"{account}:")), "")
            if running_task_id:
                queue_size = self.account_queue_size(account)
                notify_task_text(
                    self.api,
                    task,
                    f"⏳ 任务已进入即梦账号队列\n"
                    f"任务: {task_id}\n"
                    f"即梦账号: {account}\n"
                    f"当前生成中: {running_task_id}\n"
                    f"该账号待生成数量: {queue_size}"
                )

    def reject(self, task_id: str, status: str = "failed", user_ctx: Optional[dict] = None) -> None:
        reply_open_id = str((user_ctx or {}).get("owner_open_id") or "").strip()
        path = find_task(task_id)
        if not path:
            notify_text(self.api, f"⚠️ 未找到任务: {task_id}", reply_open_id)
            return
        task = read_task(path)
        task["fail_reason"] = "Rejected by reviewer."
        move_task(path, status, task)
        notify_task_text(self.api, task, f"⏹️ 任务已驳回: {task_id}")

    def submit_manual_prompt(
        self,
        prompt: str,
        note: str = "",
        count: Optional[int] = None,
        duration: int = 15,
        character_mode: str = "single_vivi",
        model_version: str = "",
        user_ctx: Optional[dict] = None,
    ) -> List[dict]:
        prompt = str(prompt or "").strip()
        if not prompt:
            raise RuntimeError("文案输入为空。")
        prompts = [part.strip() for part in re.split(r"(?m)^\s*&\s*$", prompt) if part.strip()]
        count = len(prompts)
        if count < 1:
            raise RuntimeError("没有识别到有效文案。多条文案请用单独一行 & 分隔。")
        duration = clamp_duration(duration)
        model_version = normalize_model(model_version)
        character_mode = str(character_mode or "single_vivi").strip().lower()
        if character_mode not in {"single_vivi", "bree_sunny"}:
            character_mode = "single_vivi"
        image_suggestion = "all" if character_mode == "bree_sunny" else "blue"
        user_ctx = user_ctx or user_context("")
        if user_ctx.get("owner_open_id") and not user_workspace_available(self.api, user_ctx):
            raise RuntimeError("请先点击初始化工作区，再使用文案输入。")

        seed_task = {"tenant_id": user_ctx.get("tenant_id") or setting("DEFAULT_TENANT_ID", "default")}
        prompt_dir = prompt_dir_for_task(seed_task, "manual")
        prompt_dir.mkdir(parents=True, exist_ok=True)
        created: List[dict] = []
        batch_id = datetime.now().strftime("%Y%m%d-%H%M")
        for index, prompt_text in enumerate(prompts, start=1):
            base_task_id = f"{batch_id}-manual"
            task_id = base_task_id if count == 1 else f"{base_task_id}-{index:02d}"
            duplicate_index = 2
            while find_task(task_id) or (prompt_dir / f"{task_id}.txt").exists():
                task_id = f"{base_task_id}-{index:02d}-{duplicate_index}" if count > 1 else f"{base_task_id}-{duplicate_index}"
                duplicate_index += 1
            prompt_file = prompt_dir / f"{task_id}.txt"
            prompt_file.write_text(prompt_text + "\n", encoding="utf-8")
            task = {
                "task_id": task_id,
                "prompt_file": str(prompt_file),
                "images": [],
                "image_source": "manual_bitable",
                "image_suggestion": image_suggestion,
                "image_library": user_ctx.get("image_library") or setting("IMAGE_LIBRARY_DIR", str(ROOT / "vivi-image")),
                "duration": duration,
                "ratio": setting("DEFAULT_RATIO", "9:16"),
                "model_version": model_version,
                "video_resolution": setting("DEFAULT_RESOLUTION", "720p"),
                "jimeng_account": user_ctx.get("jimeng_account") or setting("DEFAULT_JIMENG_ACCOUNT", ""),
                "tenant_id": user_ctx.get("tenant_id") or setting("DEFAULT_TENANT_ID", "default"),
                "owner_open_id": user_ctx.get("owner_open_id", ""),
                "owner_name": user_ctx.get("owner_name", ""),
                "user_script_app_token": user_ctx.get("script_app_token", ""),
                "user_script_table_id": user_ctx.get("script_table_id", ""),
                "user_video_app_token": user_ctx.get("video_app_token", ""),
                "user_video_table_id": user_ctx.get("video_table_id", ""),
                "data_isolation_level": "physical",
                "manual_note": str(note or "").strip(),
                "character_mode": character_mode,
                "source": "feishu_manual_prompt",
                "created_at": now(),
            }
            ensure_script_review_record(task, self.api)
            task["review_card_sent_at"] = now()
            path = write_task("reviewing", task)
            notify_task_card(self.api, task, script_review_card(task))
            log(f"Manual prompt uploaded to script table: {task_id}; task={path}")
            created.append(task)
        return created

    def generate_scripts(
        self,
        count: int,
        duration: int,
        brief: str,
        script_duration: Optional[int] = None,
        character_mode: str = "single_vivi",
        model_version: str = "",
        source: str = "feishu_bot",
        user_ctx: Optional[dict] = None,
    ) -> None:
        thread = threading.Thread(
            target=self._generate_scripts_safe,
            args=(count, duration, brief, script_duration, character_mode, model_version, source, user_ctx or {}),
            daemon=True,
        )
        thread.start()

    def _generate_scripts_safe(
        self,
        count: int,
        duration: int,
        brief: str,
        script_duration: Optional[int],
        character_mode: str,
        model_version: str,
        source: str,
        user_ctx: dict,
    ) -> None:
        try:
            user_ctx = user_ctx or user_context("")
            count = max(1, min(20, int(count)))
            duration = clamp_duration(duration)
            script_duration = 30 if int(script_duration or duration) > 15 else 15
            duration = clamp_duration(min(script_duration, 15))
            character_mode = (character_mode or "single_vivi").strip().lower()
            if character_mode not in {"single_vivi", "bree_sunny"}:
                character_mode = "single_vivi"
            image_variant = "all" if character_mode == "bree_sunny" else "auto"
            model_version = normalize_model(model_version)
            tenant_id = str(user_ctx.get("tenant_id") or setting("DEFAULT_TENANT_ID", "default"))
            owner_open_id = str(user_ctx.get("owner_open_id") or "")
            jimeng_account = str(user_ctx.get("jimeng_account") or setting("DEFAULT_JIMENG_ACCOUNT", ""))
            image_library = str(user_ctx.get("image_library") or setting("IMAGE_LIBRARY_DIR", str(ROOT / "vivi-image")))
            script_app_token = str(user_ctx.get("script_app_token") or "")
            script_table_id = str(user_ctx.get("script_table_id") or "")
            video_app_token = str(user_ctx.get("video_app_token") or "")
            video_table_id = str(user_ctx.get("video_table_id") or "")
            if owner_open_id and not user_workspace_available(self.api, user_ctx):
                raise RuntimeError("请先点击初始化工作区，再使用文案生成。")
            notify_text(
                self.api,
                "📝 开始调用 DeepSeek 生成脚本\n"
                f"数量: {count}\n"
                f"文案时长: {script_duration}s\n"
                f"角色: {'bree,sunny' if character_mode == 'bree_sunny' else 'vivi'}\n"
                f"模型: {model_version}\n"
                f"租户: {tenant_id}\n"
                f"即梦账号: {jimeng_account or '(默认)'}\n"
                f"需求: {brief or '(默认)'}",
                owner_open_id,
            )
            command = [
                "python3",
                "worker/generate_scripts.py",
                "--agent-docs",
                setting("SCRIPT_AGENT_DOCS") or setting("SCRIPT_AGENT_DOC"),
                "--count",
                str(count),
                "--duration",
                str(duration),
                "--script-duration",
                str(script_duration),
                "--character-mode",
                character_mode,
                "--model-version",
                model_version,
                "--tenant-id",
                tenant_id,
                "--owner-open-id",
                owner_open_id,
                "--jimeng-account",
                jimeng_account,
                "--image-dir",
                image_library,
            ]
            if script_app_token and script_table_id and video_app_token and video_table_id:
                command += [
                    "--script-app-token",
                    script_app_token,
                    "--script-table-id",
                    script_table_id,
                    "--video-app-token",
                    video_app_token,
                    "--video-table-id",
                    video_table_id,
                ]
            if image_variant != "auto":
                command += ["--image-variant", image_variant]
            if brief:
                command += ["--brief", brief]
            proc = subprocess.run(
                command,
                cwd=str(ROOT),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=300,
            )
            (LOGS / "generate_scripts.log").write_text(proc.stdout, encoding="utf-8")
            if proc.returncode != 0:
                raise RuntimeError(f"generate_scripts exited {proc.returncode}: {proc.stdout[-1200:]}")
            created = [line.strip() for line in proc.stdout.splitlines() if line.strip().endswith(".json")]
            notify_text(
                self.api,
                f"✅ DeepSeek 脚本生成完成\n已创建任务: {len(created)}\n即将由队列发送飞书审核卡片。",
                owner_open_id,
            )
        except Exception as exc:
            notify_text(self.api, f"❌ DeepSeek 脚本生成失败\n原因: {exc}", str((user_ctx or {}).get("owner_open_id") or ""))
            log(f"Generate scripts failed: {exc}\n{traceback.format_exc()}")

    def _run_generation_safe(self, task: dict) -> None:
        task_id = task["task_id"]
        account = self.account_key(task)
        lane = self.lane_key(task)
        try:
            run_generation(task, self.api)
            log(f"Task done: {task_id}")
        except Exception as exc:
            if is_dreamina_concurrency_error(str(exc)):
                retry_delay = int(setting("CONCURRENCY_RETRY_SECONDS", "600"))
                task["status"] = "reviewing"
                task["retry_after_ts"] = time.time() + retry_delay
                task["last_retry_reason"] = str(exc)
                write_task("reviewing", task)
                running_path = task_path("running", task_id, task)
                if running_path.exists():
                    running_path.unlink()
                if task.get("review_backend") == "bitable" or task.get("review_bitable_record_id") or task.get("script_bitable_record_id"):
                    update_task_workflow_record(self.api, task, {"状态": "", "错误原因": ""}, "deferred")
                notify_task_text(
                    self.api,
                    task,
                    f"⏳ 即梦账号并发已满，任务将在约 {retry_delay // 60} 分钟后自动重试\n任务: {task_id}",
                )
                log(f"Task deferred by Dreamina concurrency limit {task_id}: {exc}")
                return
            task["fail_reason"] = str(exc)
            write_task("failed", task)
            running_path = task_path("running", task_id, task)
            if running_path.exists():
                running_path.unlink()
            if task.get("review_backend") == "bitable" or task.get("review_bitable_record_id") or task.get("script_bitable_record_id"):
                update_task_workflow_record(self.api, task, {"状态": "failed", "错误原因": str(exc)}, "failure")
            notify_task_text(self.api, task, f"❌ 即梦视频生成失败\n任务: {task_id}\n原因: {exc}")
            log(f"Task failed {task_id}: {exc}\n{traceback.format_exc()}")
        finally:
            with self._queue_lock:
                self._running.discard(task_id)
                if self._running_lanes.get(lane) == task_id:
                    self._running_lanes.pop(lane, None)
                if not any(item.startswith(f"{account}:") for item in self._running_lanes):
                    self.release_account_lock(account, task_id)
                    if self._active_account == account:
                        self._active_account = ""
            queue_size = self.account_queue_size(account)
            if queue_size:
                log(f"Jimeng account queue has pending tasks: account={account}; pending={queue_size}")


def parse_action(payload: Any) -> Optional[dict]:
    def read_attr(obj: Any, name: str) -> Any:
        if isinstance(obj, dict):
            return obj.get(name)
        return getattr(obj, name, None)

    def extract_open_id(obj: Any, depth: int = 0) -> str:
        if obj is None or depth > 5:
            return ""
        if isinstance(obj, str):
            return ""
        for container_name in ("operator", "user", "sender", "sender_id", "operator_id", "user_id"):
            container = read_attr(obj, container_name)
            if container is not None:
                found = extract_open_id(container, depth + 1)
                if found:
                    return found
        direct = read_attr(obj, "open_id")
        if isinstance(direct, str) and direct.strip():
            return direct.strip()
        if isinstance(obj, dict):
            for value in obj.values():
                found = extract_open_id(value, depth + 1)
                if found:
                    return found
        return ""

    actor_open_id = extract_open_id(payload)

    def read_value(action: Any) -> dict:
        merged: dict = {}
        form_value = getattr(action, "form_value", None)
        if isinstance(form_value, dict):
            merged.update(form_value)
        value = getattr(action, "value", None)
        if isinstance(value, str):
            merged.update(json.loads(value))
        elif isinstance(value, dict):
            merged.update(value)
        if merged and not merged.get("action"):
            if any(key in merged for key in ("count", "script_duration", "character_mode", "model_version", "brief")):
                merged["action"] = "prompt_form_submit"
            elif any(key in merged for key in ("manual_prompt", "manual_note")):
                merged["action"] = "manual_prompt_submit"
        if actor_open_id and not merged.get("owner_open_id"):
            merged["owner_open_id"] = actor_open_id
        if actor_open_id:
            merged["actor_open_id"] = actor_open_id
        return merged

    try:
        if hasattr(payload, "event"):
            event = getattr(payload, "event")
            action = getattr(event, "action", None)
            if action is not None:
                return read_value(action)
        if hasattr(payload, "action"):
            action = getattr(payload, "action")
            return read_value(action)
        if isinstance(payload, dict):
            action = payload.get("action") or payload.get("event", {}).get("action")
            if isinstance(action, dict):
                merged = {}
                if isinstance(action.get("form_value"), dict):
                    merged.update(action["form_value"])
                if isinstance(action.get("input_values"), dict):
                    merged.update(action["input_values"])
                value = action.get("value")
                if isinstance(value, str):
                    merged.update(json.loads(value))
                if isinstance(value, dict):
                    merged.update(value)
                if merged and not merged.get("action"):
                    if any(key in merged for key in ("count", "script_duration", "character_mode", "model_version", "brief")):
                        merged["action"] = "prompt_form_submit"
                    elif any(key in merged for key in ("manual_prompt", "manual_note")):
                        merged["action"] = "manual_prompt_submit"
                if actor_open_id and not merged.get("owner_open_id"):
                    merged["owner_open_id"] = actor_open_id
                if actor_open_id:
                    merged["actor_open_id"] = actor_open_id
                return merged
            value = payload.get("value")
            if isinstance(value, dict):
                if actor_open_id and not value.get("owner_open_id"):
                    value["owner_open_id"] = actor_open_id
                if actor_open_id:
                    value["actor_open_id"] = actor_open_id
                return value
    except Exception:
        return None
    return None


def normalize_feishu_model(value: str) -> str:
    token = value.strip().lower()
    aliases = {
        "fast_vip": "seedance2.0fast_vip",
        "fastvip": "seedance2.0fast_vip",
        "vip": "seedance2.0_vip",
        "fast": "seedance2.0fast",
        "2.0": "seedance2.0",
        "seedance2.0": "seedance2.0",
        "seedance2.0fast": "seedance2.0fast",
        "seedance2.0_vip": "seedance2.0_vip",
        "seedance2.0fast_vip": "seedance2.0fast_vip",
    }
    return aliases.get(token, "")


def parse_generation_request(text: str) -> Optional[dict]:
    normalized = text.strip()
    lowered = normalized.lower()
    if not any(token in lowered for token in ["generate", "生成", "脚本"]):
        return None

    parts = normalized.split()
    if normalized in {"文案生成", "生成文案", "生成脚本"}:
        return {"entry_only": True}

    if parts and parts[0].lower() in {"generate", "generate_scripts", "gen", "生成脚本", "生成文案", "文案生成"}:
        count = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else int(setting("DEFAULT_SCRIPT_COUNT", "3"))
        script_duration = int(parts[2]) if len(parts) >= 3 and parts[2].isdigit() else int(setting("DEFAULT_SCRIPT_DURATION", setting("DEFAULT_DURATION", "15")))
        character_mode = "single_vivi"
        model_version = setting("DEFAULT_MODEL", "seedance2.0fast_vip")
        brief_start = 3
        if len(parts) >= 4:
            role = parts[3].lower()
            if role in {"双", "双角色", "bree", "sunny", "bree_sunny", "bree,sunny", "2"}:
                character_mode = "bree_sunny"
                brief_start = 4
            elif role in {"vivi", "单", "单角色", "1"}:
                character_mode = "single_vivi"
                brief_start = 4
        if len(parts) > brief_start:
            model = normalize_feishu_model(parts[brief_start])
            if model:
                model_version = model
                brief_start += 1
        brief = " ".join(parts[brief_start:]) if len(parts) > brief_start else ""
        return {
            "entry_only": False,
            "count": count,
            "duration": min(script_duration, 15),
            "script_duration": 30 if script_duration > 15 else 15,
            "brief": brief,
            "character_mode": character_mode,
            "model_version": model_version,
        }

    count_match = re.search(r"生成\s*(\d+)\s*(?:条|个|篇)?", normalized)
    duration_match = re.search(r"(\d+)\s*s", lowered)
    count = int(count_match.group(1)) if count_match else int(setting("DEFAULT_SCRIPT_COUNT", "3"))
    script_duration = int(duration_match.group(1)) if duration_match else int(setting("DEFAULT_SCRIPT_DURATION", setting("DEFAULT_DURATION", "15")))
    character_mode = "bree_sunny" if any(token in lowered for token in ["双角色", "bree", "sunny"]) else "single_vivi"
    model_version = setting("DEFAULT_MODEL", "seedance2.0fast_vip")
    for part in parts:
        model = normalize_feishu_model(part)
        if model:
            model_version = model
            break
    return {
        "entry_only": False,
        "count": count,
        "duration": min(script_duration, 15),
        "script_duration": 30 if script_duration > 15 else 15,
        "brief": normalized,
        "character_mode": character_mode,
        "model_version": model_version,
    }


def start_feishu_ws(worker: Worker) -> None:
    try:
        import lark_oapi as lark
        from lark_oapi.api.im.v1 import P2ImMessageReceiveV1
    except ImportError as exc:
        raise SystemExit("Missing lark-oapi. Install with: pip install -r requirements.txt") from exc

    try:
        from lark_oapi.event.callback.model.p2_card_action_trigger import P2CardActionTriggerResponse
    except ImportError:
        P2CardActionTriggerResponse = None

    def card_response(payload: dict) -> Any:
        if P2CardActionTriggerResponse is None:
            return payload
        return P2CardActionTriggerResponse(payload)

    def on_message(data: P2ImMessageReceiveV1) -> None:
        try:
            message = data.event.message
            content = json.loads(message.content or "{}")
            text = content.get("text", "").strip()
            sender = getattr(data.event, "sender", None)
            sender_id = getattr(sender, "sender_id", None)
            sender_open_id = getattr(sender_id, "open_id", "") if sender_id else ""
            sender_user_id = getattr(sender_id, "user_id", "") if sender_id else ""
            sender_union_id = getattr(sender_id, "union_id", "") if sender_id else ""
            log(
                "Feishu message: "
                f"text={text}; open_id={sender_open_id}; user_id={sender_user_id}; union_id={sender_union_id}"
            )
            user_ctx = ensure_user_config(sender_open_id) if sender_open_id else user_context("")
            if not user_ctx.get("enabled", True):
                worker.api.text_to_open_id(sender_open_id, "你的机器人使用权限已停用，请联系管理员。")
                return
            def reply_text(message_text: str) -> None:
                if sender_open_id:
                    worker.api.text_to_open_id(sender_open_id, message_text)
                else:
                    worker.api.text(message_text)

            def reply_card(card: dict) -> None:
                if sender_open_id:
                    worker.api.card_to_open_id(sender_open_id, card)
                else:
                    worker.api.card(card)

            parts = text.split()
            if text.lower() in {"whoami", "id", "open_id"}:
                reply = (
                    "你的飞书用户标识：\n"
                    f"open_id: {sender_open_id or '(empty)'}\n"
                    f"user_id: {sender_user_id or '(empty)'}\n"
                    f"union_id: {sender_union_id or '(empty)'}\n"
                    f"tenant_id: {user_ctx.get('tenant_id') or '(empty)'}\n"
                    f"jimeng_account: {user_ctx.get('jimeng_account') or '(empty)'}"
                )
                if sender_open_id:
                    worker.api.text_to_open_id(sender_open_id, reply)
                else:
                    worker.api.text(reply)
            elif text in {"配置", "初始化", "工作区", "工作区配置", "初始化配置"}:
                reply_card(workspace_setup_card(user_ctx))
            elif len(parts) >= 2 and parts[0].lower() in {"approve", "通过"}:
                worker.approve(parts[1], user_ctx)
            elif len(parts) >= 2 and parts[0].lower() in {"reject", "驳回"}:
                worker.reject(parts[1], user_ctx=user_ctx)
            elif len(parts) >= 2 and parts[0].lower() in {"revise", "重写", "重做文案"}:
                worker.reject(parts[1], "needs_revision", user_ctx=user_ctx)
            elif text.lower() in {"scan", "扫描"}:
                worker.scan_pending_once()
            elif text in {"队列", "状态", "运行状态", "生成状态"}:
                reply_text(worker.queue_status_text(user_ctx))
            elif text in {"4", "账号管理", "多账号登录", "账号"}:
                reply_card(account_management_card(user_ctx))
            elif text in {"账号列表", "即梦账号列表"}:
                reply_text("已保存即梦账号：\n" + jimeng_accounts_text())
            elif text in {"当前账号", "即梦当前账号"}:
                reply_text(current_account_text(user_ctx))
            elif len(parts) >= 5 and parts[0] in {"表格配置", "配置表格", "多维表格配置"}:
                reply_text("现在不需要手动填写表格参数，点击配置按钮即可自动完成。")
                reply_card(workspace_setup_card(user_ctx))
            elif text in {"表格配置", "配置表格", "多维表格配置"}:
                reply_card(workspace_setup_card(user_ctx))
            elif len(parts) >= 2 and parts[0] in {"账号切换", "切换账号", "默认账号"}:
                account_name = parts[1].strip()
                if not jimeng_profile_exists(account_name):
                    reply_text(
                        f"本地还没有保存即梦账号：{account_name}\n\n"
                        + start_jimeng_login(account_name, user_ctx)
                    )
                    return
                if sender_open_id:
                    user_ctx = update_user_config(sender_open_id, {"jimeng_account": account_name})
                    reply_text(
                        f"已切换你的默认即梦账号：{jimeng_account_display_name(account_name)}\n"
                        "新任务会使用这个账号。"
                    )
                else:
                    set_env_value("DEFAULT_JIMENG_ACCOUNT", account_name)
                    reply_text(f"已切换系统默认即梦账号：{account_name}")
            elif len(parts) >= 2 and parts[0] in {"账号添加", "添加账号"}:
                account_name = parts[1].strip()
                reply_text(start_jimeng_login(account_name, user_ctx))
            elif len(parts) >= 2 and parts[0] in {"账号保存", "保存账号", "授权完成"}:
                account_name = parts[1].strip()
                reply_text(save_jimeng_login(account_name))
            elif text in {"2", "文案输入", "输入文案"}:
                if not user_workspace_available(worker.api, user_ctx):
                    reply_card(workspace_setup_card(user_ctx))
                    return
                reply_card(manual_prompt_entry_card(user_ctx))
            elif text in {"3", "动画生成", "生成动画"}:
                reply_text("动画生成模块已预留，等待后续开发接入。")
            elif text in {"帮助", "help", "菜单", "开始"}:
                reply_card(welcome_card(user_ctx))
                if not user_workspace_available(worker.api, user_ctx):
                    reply_card(workspace_setup_card(user_ctx))
            elif text in {"1", "文案生成", "生成文案", "生成脚本"}:
                if not user_workspace_available(worker.api, user_ctx):
                    reply_card(workspace_setup_card(user_ctx))
                    return
                reply_card(prompt_entry_card(user_ctx))
            elif text:
                reply_text("暂不识别该输入。请点击菜单按钮，或输入数字 1-4。")
                reply_card(welcome_card(user_ctx))
        except Exception as exc:
            log(f"Message handler failed: {exc}\n{traceback.format_exc()}")

    def on_card_action(data: Any) -> Any:
        value = parse_action(data)
        log(f"Feishu card action: {value}")
        if value and str(value.get("action") or "").startswith("menu_"):
            user_ctx = card_user_context(value)
            owner_open_id = str(user_ctx.get("owner_open_id") or "").strip()
            action = str(value.get("action") or "")
            if not owner_open_id:
                return card_response({
                    "toast": {"type": "error", "content": "无法识别当前点击用户，请在机器人私聊中输入 菜单"}
                })
            if action == "menu_prompt_generate":
                if owner_open_id and not user_workspace_ready(user_ctx):
                    notify_card(worker.api, workspace_setup_card(user_ctx), owner_open_id)
                    return card_response({
                        "toast": {"type": "error", "content": "请先初始化工作区"}
                    })
                notify_card(worker.api, prompt_entry_card(user_ctx), owner_open_id)
                return card_response({
                    "toast": {"type": "success", "content": "已打开文案生成"}
                })
            if action == "menu_manual_prompt":
                if owner_open_id and not user_workspace_ready(user_ctx):
                    notify_card(worker.api, workspace_setup_card(user_ctx), owner_open_id)
                    return card_response({
                        "toast": {"type": "error", "content": "请先初始化工作区"}
                    })
                notify_card(worker.api, manual_prompt_entry_card(user_ctx), owner_open_id)
                return card_response({
                    "toast": {"type": "success", "content": "已打开文案输入"}
                })
            if action == "menu_animation_todo":
                return card_response({
                    "toast": {"type": "info", "content": "DeepSeek动画生成待开发"}
                })
            if action == "menu_account_management":
                notify_card(worker.api, account_management_card(user_ctx), owner_open_id)
                return card_response({
                    "toast": {"type": "success", "content": "已打开账号管理"}
                })
        if value and str(value.get("action") or "").startswith("account_"):
            user_ctx = card_user_context(value)
            owner_open_id = str(user_ctx.get("owner_open_id") or "").strip()
            action = str(value.get("action") or "")
            if not owner_open_id:
                return card_response({
                    "toast": {"type": "error", "content": "无法识别当前点击用户，请在机器人私聊中重新打开账号管理"}
                })
            try:
                if action == "account_list":
                    message = "已保存即梦账号：\n" + jimeng_accounts_text()
                    if owner_open_id:
                        worker.api.text_to_open_id(owner_open_id, message)
                    else:
                        worker.api.text(message)
                    return card_response({"toast": {"type": "success", "content": "已发送账号列表"}})
                if action == "account_current":
                    message = current_account_text(user_ctx)
                    if owner_open_id:
                        worker.api.text_to_open_id(owner_open_id, message)
                    else:
                        worker.api.text(message)
                    return card_response({"toast": {"type": "success", "content": "已发送当前账号"}})
                if action == "account_add":
                    account_name = next_jimeng_account_name(user_ctx)
                    message = start_jimeng_login(account_name, user_ctx)
                    if owner_open_id:
                        worker.api.text_to_open_id(owner_open_id, message)
                    else:
                        worker.api.text(message)
                    return card_response({"toast": {"type": "success", "content": "已发送授权链接"}})
                if action == "account_save_pending":
                    message = save_pending_jimeng_login(user_ctx)
                    if owner_open_id:
                        worker.api.text_to_open_id(owner_open_id, message)
                    else:
                        worker.api.text(message)
                    return card_response({"toast": {"type": "success", "content": "保存账号完成"}})
                if action == "account_switch_menu":
                    if owner_open_id:
                        worker.api.card_to_open_id(owner_open_id, account_switch_card(user_ctx))
                    else:
                        worker.api.card(account_switch_card(user_ctx))
                    return card_response({"toast": {"type": "success", "content": "请选择账号"}})
                if action == "account_switch":
                    account_name = str(value.get("account_name") or "").strip()
                    if not account_name:
                        raise RuntimeError("缺少账号名称。")
                    if not jimeng_profile_exists(account_name):
                        raise RuntimeError(f"本地未找到账号：{account_name}")
                    if owner_open_id:
                        update_user_config(owner_open_id, {"jimeng_account": account_name})
                    else:
                        set_env_value("DEFAULT_JIMENG_ACCOUNT", account_name)
                    message = f"已切换默认即梦账号：{jimeng_account_display_name(account_name)}\n新任务会使用这个账号。"
                    if owner_open_id:
                        worker.api.text_to_open_id(owner_open_id, message)
                    else:
                        worker.api.text(message)
                    return card_response({"toast": {"type": "success", "content": "账号已切换"}})
            except Exception as exc:
                log(f"Account action failed: {exc}\n{traceback.format_exc()}")
                return card_response({
                    "toast": {
                        "type": "error",
                        "content": f"账号操作失败: {exc}",
                    }
                })
        if value and value.get("action") == "setup_workspace":
            user_ctx = card_user_context(value)
            owner_open_id = str(user_ctx.get("owner_open_id") or "").strip()
            if not owner_open_id:
                return card_response({
                    "toast": {"type": "error", "content": "无法识别当前点击用户，请在机器人私聊中输入 初始化"}
                })
            def setup_workspace_async() -> None:
                try:
                    initialized = initialize_user_workspace(worker.api, user_ctx)
                    state = initialized
                    title = "✅ 已复用你的 OKIVIVI 工作区。" if state.get("reused") else "✅ 你的 OKIVIVI 工作区已初始化完成。"
                    same_table = state.get("script_url") == state.get("url")
                    table_lines = (
                        f"工作流表：{state.get('url') or state.get('script_url') or '(empty)'}"
                        if same_table
                        else f"文案表：{state.get('script_url') or '(empty)'}\n视频表：{state.get('url') or '(empty)'}"
                    )
                    message = (
                        f"{title}\n\n"
                        f"{table_lines}\n\n"
                        "下面是功能菜单："
                    )
                    notify_text(worker.api, message, owner_open_id)
                    notify_card(worker.api, welcome_card(initialized.get("user_ctx") or user_ctx), owner_open_id)
                except Exception as exc:
                    log(f"Workspace setup failed: {exc}\n{traceback.format_exc()}")
                    notify_text(worker.api, f"❌ 初始化工作区失败\n原因: {exc}", owner_open_id)

            signature = request_signature("setup_workspace", value, ["tenant_id"])
            state = USER_REQUESTS.submit(owner_open_id, signature, "初始化工作区", setup_workspace_async)
            return card_response(request_toast(state, "已开始初始化，完成后会私聊通知你"))
        if value and value.get("action") in {"prompt_generate", "prompt_form_submit"}:
            user_ctx = card_user_context(value)
            owner_open_id = str(user_ctx.get("owner_open_id") or "").strip()
            if not owner_open_id:
                return card_response({
                    "toast": {"type": "error", "content": "无法识别当前点击用户，请重新输入 1 打开文案生成"}
                })
            if owner_open_id and not user_workspace_ready(user_ctx):
                notify_card(worker.api, workspace_setup_card(user_ctx), owner_open_id)
                return card_response({
                    "toast": {
                        "type": "error",
                        "content": "请先初始化单表工作区，再提交文案生成。",
                    }
                })
            count = int(value.get("count") or 1)
            script_duration = int(value.get("script_duration") or 15)
            character_mode = str(value.get("character_mode") or "single_vivi")
            model_version = str(value.get("model_version") or setting("DEFAULT_MODEL", "seedance2.0fast_vip"))
            brief = str(value.get("brief") or "").strip()

            def generate_scripts_async() -> None:
                worker._generate_scripts_safe(
                    count,
                    min(script_duration, 15),
                    brief,
                    script_duration,
                    character_mode,
                    model_version,
                    "feishu_bot",
                    user_ctx,
                )

            signature = request_signature(
                "prompt_generate",
                value,
                ["count", "script_duration", "character_mode", "model_version", "brief"],
            )
            state = USER_REQUESTS.submit(owner_open_id, signature, "DeepSeek文案生成", generate_scripts_async)
            return card_response(request_toast(state, f"已开始生成 {count} 条 {script_duration}s 文案"))
        if value and value.get("action") == "manual_prompt_submit":
            user_ctx = card_user_context(value)
            owner_open_id = str(user_ctx.get("owner_open_id") or "").strip()
            if not owner_open_id:
                return card_response({
                    "toast": {"type": "error", "content": "无法识别当前点击用户，请重新输入 2 打开文案输入"}
                })
            if owner_open_id and not user_workspace_ready(user_ctx):
                notify_card(worker.api, workspace_setup_card(user_ctx), owner_open_id)
                return card_response({
                    "toast": {
                        "type": "error",
                        "content": "请先初始化单表工作区，再上传文案。",
                    }
                })
            prompt = str(value.get("manual_prompt") or "").strip()
            note = str(value.get("manual_note") or "").strip()
            duration = int(value.get("manual_duration") or 15)
            character_mode = str(value.get("manual_character_mode") or "single_vivi")
            model_version = str(value.get("manual_model_version") or setting("DEFAULT_MODEL", "seedance2.0fast_vip"))

            def submit_manual_prompt_async() -> None:
                try:
                    tasks = worker.submit_manual_prompt(
                        prompt,
                        note,
                        duration=duration,
                        character_mode=character_mode,
                        model_version=model_version,
                        user_ctx=user_ctx,
                    )
                    notify_text(worker.api, f"✅ 文案已上传到工作流表: {len(tasks)} 条", owner_open_id)
                except Exception as exc:
                    log(f"Manual prompt submit failed: {exc}\n{traceback.format_exc()}")
                    notify_text(worker.api, f"❌ 文案上传失败\n原因: {exc}", owner_open_id)

            signature = request_signature(
                "manual_prompt_submit",
                value,
                ["manual_prompt", "manual_note", "manual_duration", "manual_character_mode", "manual_model_version"],
            )
            state = USER_REQUESTS.submit(owner_open_id, signature, "文案自行输入", submit_manual_prompt_async)
            return card_response(request_toast(state, "已收到，正在上传到工作流表"))
        if value and value.get("action") in {"toggle_script_prompt", "toggle_dialogue_cn", "card_approve_task"}:
            user_ctx = card_user_context(value)
            owner_open_id = str(user_ctx.get("owner_open_id") or "").strip()
            task_id = str(value.get("task_id") or "").strip()
            if not owner_open_id:
                return card_response({
                    "toast": {"type": "error", "content": "无法识别当前点击用户，请重新打开卡片"}
                })
            if not task_id:
                return card_response({
                    "toast": {"type": "error", "content": "缺少任务ID"}
                })
            path = find_task(task_id)
            if not path:
                return card_response({
                    "toast": {"type": "error", "content": f"未找到任务: {task_id}"}
                })
            task = read_task(path)
            if str(task.get("owner_open_id") or "") and str(task.get("owner_open_id") or "") != owner_open_id:
                return card_response({
                    "toast": {"type": "error", "content": "这个任务不属于当前点击用户"}
                })
            show_prompt = str(value.get("show_prompt")).lower() == "true" or value.get("show_prompt") is True
            show_dialogue = str(value.get("show_dialogue")).lower() == "true" or value.get("show_dialogue") is True
            action = str(value.get("action") or "")
            if action == "card_approve_task":
                task["card_approved"] = True
                task["card_approved_at"] = now()
                write_task(status_for_task(task_id) or "reviewing", task)

                def approve_from_card() -> None:
                    worker.approve(task_id, user_ctx, require_table_confirm=False)

                signature = request_signature("card_approve_task", value, ["task_id"])
                state = USER_REQUESTS.submit(owner_open_id, signature, "卡片通过生成", approve_from_card)
                response = request_toast(state, "已通过，正在提交即梦生成")
                response["card"] = script_review_card(task, show_prompt=show_prompt, show_dialogue=show_dialogue, approved=True)
                return card_response(response)
            return card_response({
                "toast": {"type": "success", "content": "已更新卡片"},
                "card": script_review_card(task, show_prompt=show_prompt, show_dialogue=show_dialogue),
            })
        if value and value.get("action") in {"result_download", "result_location", "result_jump_first"}:
            user_ctx = card_user_context(value)
            owner_open_id = str(user_ctx.get("owner_open_id") or "").strip()
            action = str(value.get("action") or "")
            task_id = str(value.get("task_id") or "").strip()
            if not owner_open_id:
                return card_response({
                    "toast": {"type": "error", "content": "无法识别当前点击用户，请重新打开结果卡片"}
                })
            if action == "result_jump_first":
                pending = undownloaded_done_tasks(owner_open_id, 1)
                if not pending:
                    notify_card(worker.api, result_status_card(owner_open_id), owner_open_id)
                    return card_response({"toast": {"type": "success", "content": "当前没有未下载视频"}})
                notify_card(worker.api, result_card(pending[0]), owner_open_id)
                return card_response({"toast": {"type": "success", "content": "已发送最上方未下载结果"}})
            if not task_id:
                return card_response({"toast": {"type": "error", "content": "缺少任务ID"}})
            path = find_task(task_id)
            if not path:
                return card_response({"toast": {"type": "error", "content": f"未找到任务: {task_id}"}})
            task = read_task(path)
            if str(task.get("owner_open_id") or "") and str(task.get("owner_open_id") or "") != owner_open_id:
                return card_response({"toast": {"type": "error", "content": "这个结果不属于当前点击用户"}})
            if action == "result_location":
                location = str(task.get("download_file") or result_download_path(task))
                notify_text(worker.api, f"📁 文件位置\n任务: {task_id}\n路径: {location}", owner_open_id)
                return card_response({"toast": {"type": "success", "content": "已发送文件位置"}})
            try:
                target = mark_result_downloaded(task)
            except Exception as exc:
                log(f"Result download failed: {exc}\n{traceback.format_exc()}")
                return card_response({"toast": {"type": "error", "content": f"下载失败: {exc}"}})
            notify_text(worker.api, f"✅ 已准备下载文件\n任务: {task_id}\n文件: {target}\n链接: {task.get('video_url') or '(empty)'}", owner_open_id)
            return card_response({
                "toast": {"type": "success", "content": "已下载"},
                "card": result_card(task, downloaded=True),
            })
        return card_response({
            "toast": {
                "type": "info",
                "content": "卡片按钮已停用，请在多维表格“确认”列选择“确认”。",
            }
        })

    builder = (
        lark.EventDispatcherHandler.builder(
            setting("FEISHU_VERIFICATION_TOKEN"),
            setting("FEISHU_ENCRYPT_KEY"),
        )
        .register_p2_im_message_receive_v1(on_message)
    )
    if hasattr(builder, "register_p2_card_action_trigger"):
        log("Card action trigger handler registered.")
        builder = builder.register_p2_card_action_trigger(on_card_action)
    else:
        log("This lark-oapi version has no register_p2_card_action_trigger; use message commands as fallback.")
    event_handler = builder.build()
    client = lark.ws.Client(
        app_id=setting("FEISHU_APP_ID"),
        app_secret=setting("FEISHU_APP_SECRET"),
        event_handler=event_handler,
        log_level=lark.LogLevel.INFO,
        auto_reconnect=True,
    )
    log("Starting Feishu long-connection client.")
    client.start()


def pending_scanner(worker: Worker) -> None:
    while True:
        worker.scan_pending_once()
        time.sleep(5)


def reviewing_scanner(worker: Worker) -> None:
    interval = int(setting("REVIEWING_SCAN_SECONDS", "15"))
    while True:
        worker.scan_reviewing_once()
        time.sleep(max(5, interval))


def main() -> int:
    ensure_dirs()
    recover_interrupted_running_tasks()
    api = FeishuApi()
    try:
        cleanup_duplicate_bitable_records(api)
    except Exception as exc:
        log(f"Failed to cleanup duplicate bitable records: {exc}\n{traceback.format_exc()}")
    worker = Worker(api)
    scanner = threading.Thread(target=pending_scanner, args=(worker,), daemon=True)
    scanner.start()
    review_scanner = threading.Thread(target=reviewing_scanner, args=(worker,), daemon=True)
    review_scanner.start()
    if setting("STARTUP_NOTIFY", "0").strip() == "1":
        try:
            api.text("即梦视频生成 worker 已启动。\n\n" + welcome_text())
        except Exception as exc:
            log(f"Startup outbound message skipped: {exc}")
    else:
        log("Startup outbound message disabled.")
    start_feishu_ws(worker)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

