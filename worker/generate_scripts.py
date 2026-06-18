#!/usr/bin/env python3
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

try:
    from create_task import ROOT, clamp_duration, slug
except ModuleNotFoundError:
    from worker.create_task import ROOT, clamp_duration, slug

TASKS = ROOT / "tasks" / "pending"
PROMPTS = ROOT / "prompts" / "generated"
TENANTS = ROOT / "tenants"


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


def tenant_slug(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value or "")).strip("-")
    return cleaned[:80] or "default"


def tenant_root(tenant_id: str) -> Path:
    cleaned = tenant_slug(tenant_id)
    return TENANTS / cleaned if cleaned else ROOT


def tenant_tasks_dir(tenant_id: str) -> Path:
    base = tenant_root(tenant_id)
    return TASKS if base == ROOT else base / "tasks" / "pending"


def tenant_prompts_dir(tenant_id: str) -> Path:
    base = tenant_root(tenant_id)
    return PROMPTS if base == ROOT else base / "prompts" / "generated"


def task_id_exists(tenant_id: str, task_id: str) -> bool:
    base = tenant_root(tenant_id)
    roots = [ROOT / "tasks"] if base == ROOT else [base / "tasks"]
    for root in roots:
        for status in ["pending", "reviewing", "running", "done", "failed", "needs_revision"]:
            if (root / status / f"{task_id}.json").exists():
                return True
    return False


def next_numbered_task_id(tenant_id: str, batch_id: str, prompt_dir: Path, start_index: int) -> str:
    index = max(1, start_index)
    while True:
        task_id = f"{batch_id}-{index:02d}"
        if not task_id_exists(tenant_id, task_id) and not (prompt_dir / f"{task_id}.txt").exists():
            return task_id
        index += 1


def split_doc_paths(value: str) -> List[str]:
    if not value.strip():
        return []
    parts = re.split(r"[;\n]+", value)
    return [part.strip().strip('"').strip("'") for part in parts if part.strip()]


def load_agent_docs(single_doc: str, doc_list: str) -> str:
    paths = split_doc_paths(doc_list)
    if not paths and single_doc:
        paths = [single_doc]
    if not paths:
        raise SystemExit("SCRIPT_AGENT_DOCS or SCRIPT_AGENT_DOC is required in .env or pass --agent-doc.")

    chunks: List[str] = []
    for idx, raw_path in enumerate(paths, start=1):
        path = Path(raw_path).expanduser()
        if not path.exists():
            raise SystemExit(f"Agent doc does not exist: {path}")
        text = path.read_text(encoding="utf-8-sig").strip()
        if not text:
            raise SystemExit(f"Agent doc is empty: {path}")
        chunks.append(f"===== Agent Doc {idx}: {path.name} =====\n{text}")
    return "\n\n".join(chunks)


def post_json(url: str, payload: dict, headers: dict, timeout: int = 180) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json; charset=utf-8")
    for key, value in headers.items():
        req.add_header(key, value)
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"DeepSeek HTTP {exc.code}: {detail}") from exc


def extract_json_array(text: str) -> List[dict]:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("[")
        end = cleaned.rfind("]")
        if start == -1 or end == -1 or end <= start:
            raise
        data = json.loads(cleaned[start : end + 1])
    if not isinstance(data, list):
        raise ValueError("DeepSeek response must be a JSON array.")
    return data


def normalize_script(text: str) -> str:
    script = text.strip()
    script = normalize_character_description_format(script)
    return script


def normalize_character_description_format(script: str) -> str:
    match = re.search(r"人物描述：(.*?)(?=\n环境描述：|\n环境音：|\n\d+\s*-\s*\d+s：|$)", script, flags=re.S)
    if not match:
        return script
    desc = match.group(1)
    fixed = desc
    if not re.search(r"(黑色瞳孔|瞳孔为黑色|black pupils?)", fixed, flags=re.I):
        if re.search(r"(瞳孔|pupils?)", fixed, flags=re.I):
            fixed = re.sub(r"(瞳孔|pupils?)", r"黑色\1", fixed, count=1, flags=re.I)
        else:
            fixed = fixed.rstrip("，,。 ") + "，黑色瞳孔"
    if not re.search(
        r"黑发|黑色(?:短发|长发|卷发|直发|波浪发|马尾|低马尾|丸子头|刘海|头发)|金发|金色(?:短发|长发|卷发|直发|波浪发|马尾|低马尾|丸子头|刘海|头发)|blonde\s+hair|blond\s+hair|black\s+hair|dark\s+black\s+hair",
        fixed,
        flags=re.I,
    ):
        fixed = re.sub(
            r"(短发|长发|卷发|直发|波浪发|马尾|低马尾|丸子头|刘海|头发)",
            r"黑色\1",
            fixed,
            count=1,
        )
    if fixed == desc:
        return script
    return script[: match.start(1)] + fixed + script[match.end(1) :]


def validate_script(script: str, index: int) -> None:
    lowered = script.lower()
    has_vivi = re.search(r"\bvivi\b", lowered) is not None
    has_bree = re.search(r"\bbree\b", lowered) is not None
    has_sunny = re.search(r"\bsunny\b", lowered) is not None
    if not has_vivi:
        raise ValueError(f"Script {index} must mention vivi.")
    if has_bree or has_sunny:
        raise ValueError(f"Script {index} cannot mention bree or sunny; only single-vivi scripts are allowed.")
    missing = [token for token in ["=vivi", "人物描述", "环境描述"] if token not in script]
    if missing:
        raise ValueError(f"Script {index} is missing required tokens: {', '.join(missing)}.")
    if not re.search(r"\b0\s*-\s*\d+s", lowered):
        raise ValueError(f"Script {index} must include timestamped shot segments.")
    if re.search(r"caption|cta|总结|表格|tiktok caption", lowered):
        raise ValueError(f"Script {index} contains forbidden non-shot-script content.")


def validate_dialogue_cn(dialogue_cn: str, index: int) -> None:
    if not dialogue_cn.strip():
        raise ValueError(f"Script {index} must include non-empty dialogue_cn.")
    if not re.search(r"[\u4e00-\u9fff]", dialogue_cn):
        raise ValueError(f"Script {index} dialogue_cn must be Chinese.")
    if not re.search(r"[:：]", dialogue_cn):
        raise ValueError(f"Script {index} dialogue_cn must preserve speaker labels.")


def normalize_items(data: List[dict]) -> List[dict]:
    normalized: List[dict] = []
    for idx, item in enumerate(data, start=1):
        if isinstance(item, str):
            item = {"name": f"script-{idx}", "script": item}
        if not isinstance(item, dict):
            raise ValueError(f"Script item {idx} is not an object.")
        script = normalize_script(str(item.get("script") or item.get("content") or ""))
        if not script:
            raise ValueError(f"Script item {idx} has empty script.")
        validate_script(script, idx)
        dialogue_cn = str(
            item.get("dialogue_cn")
            or item.get("dialogue_translation")
            or item.get("dialogue_zh")
            or item.get("对话中文")
            or ""
        ).strip()
        validate_dialogue_cn(dialogue_cn, idx)
        normalized.append(
            {
                "name": str(item.get("name") or item.get("title") or f"script-{idx}").strip(),
                "script": script,
                "dialogue_cn": dialogue_cn,
            }
        )
    return normalized


FEW_SHOT_EXAMPLES = ""


def build_user_prompt(count: int, duration: int, brief: str, character_mode: str = "") -> str:
    extra = f"\n用户额外要求：{brief.strip()}\n" if brief.strip() else ""
    mode = (character_mode or "").strip().lower()
    if mode == "single_vivi":
        role_rule = (
            "\n当前角色选择：1.vivi。每条文案只写一个角色 Vivi；正文不得出现 Bree 或 Sunny；"
            "不要在正文中用 blue/pink 作为角色名。图片颜色由系统后续匹配，不写进文案。\n"
        )
    else:
        role_rule = ""
    return f"""
请严格读取并执行 system message 中的 md 文档规则。
请生成 {count} 条可直接用于即梦视频生成的 {duration}s 分镜脚本。
{role_rule}
只输出 JSON 数组，不要 Markdown，不要解释。数组中每个元素格式：
{{
  "name": "short_task_name",
  "script": "完整分镜脚本",
  "dialogue_cn": "只提取 script 中英文对白并翻译为中文；保留说话人；不要翻译人物描述/环境描述/动作描述"
}}

生成前自检：
- 每条 script 必须遵守 system message 中的当前 md 文档。
- 每条 script 是否包含 `=vivi`、`人物描述：`、`环境描述：`、时间段分镜。
- 不要输出编号短口播，不要输出广告口播，不要输出“Caption/CTA/总结/表格”。
- dialogue_cn 只能包含对白翻译，格式如 “Girl：……\\nVivi：……”，不要写总结。
- dialogue_cn 必须保留 script 中全部英文对白的中文翻译；不要把对白压缩成梗概，也不要漏掉人物或 vivi 的任何一句台词。
{extra}
""".strip()


def build_short_script_prompt(count: int, duration: int, brief: str, character_mode: str = "") -> str:
    extra = f"\n用户额外要求：{brief.strip()}\n" if brief.strip() else ""
    role_rule = ""
    if (character_mode or "").strip().lower() == "single_vivi":
        role_rule = "\n当前角色选择：1.vivi。每条文案只写一个角色 Vivi。\n"
    return f"""
请只执行 system message 中的 5-8s 短文案专用规则文档，不要调用、引用或混用 15s/30s 提示链规则。
请生成 {count} 条可直接用于即梦视频生成的 {duration}s 短文案。
{role_rule}
输出必须是 JSON 数组，不要 Markdown，不要解释。数组中每个元素格式：
{{
  "name": "short_task_name",
  "script": "完整短文案分镜脚本",
  "dialogue_cn": "只提取 script 中英文对白并翻译为中文；保留说话人；不要翻译人物描述/环境描述/动作描述"
}}

生成前自检：
- 每条 script 必须完整保留短文案规则文档要求的字段结构。
- 时间段必须匹配本次选择的 {duration}s，不要写成 15s 或 30s。
- 每条 script 只写单个 vivi，不写 Bree、Sunny 或双角色结构。
- dialogue_cn 必须保留全部英文对白的中文翻译，不能写总结。
{extra}
""".strip()


def deepseek_once(
    agent_doc: str,
    count: int,
    duration: int,
    brief: str,
    character_mode: str = "",
    feedback: str = "",
    script_kind: str = "default",
) -> List[dict]:
    api_key = setting("DEEPSEEK_API_KEY")
    if not api_key:
        raise SystemExit("DEEPSEEK_API_KEY is required in .env")
    base_url = setting("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    model = setting("DEEPSEEK_MODEL", "deepseek-chat")
    if str(script_kind or "").strip().lower() == "short_6s":
        user_prompt = build_short_script_prompt(count, duration, brief, character_mode)
    else:
        user_prompt = build_user_prompt(count, duration, brief, character_mode)
    if setting("DEEPSEEK_FEW_SHOT", "0") != "0":
        user_prompt = f"{FEW_SHOT_EXAMPLES}\n\n{user_prompt}"
    if feedback:
        user_prompt += f"\n\n上一次输出没有解析成可用 JSON，请只修正问题并重新输出完整 JSON 数组。解析问题：\n{feedback}"
    payload = {
        "model": model,
        "temperature": float(setting("DEEPSEEK_TEMPERATURE", "0.9")),
        "messages": [
            {"role": "system", "content": agent_doc},
            {"role": "user", "content": user_prompt},
        ],
    }
    response = post_json(
        f"{base_url}/chat/completions",
        payload,
        {"Authorization": f"Bearer {api_key}"},
    )
    try:
        content = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Unexpected DeepSeek response: {response}") from exc
    return normalize_items(extract_json_array(content))


def call_deepseek(
    agent_doc: str,
    count: int,
    duration: int,
    brief: str,
    character_mode: str = "",
    script_kind: str = "default",
) -> List[dict]:
    max_rounds = max(1, int(setting("DEEPSEEK_REWRITE_ATTEMPTS", "2")) + 1)
    batch_size = max(1, min(10, int(float(setting("DEEPSEEK_SCRIPT_BATCH_SIZE", "5")))))
    last_error: Optional[Exception] = None
    passed_items: List[dict] = []
    attempts_without_progress = 0
    max_total_attempts = max_rounds * max(1, (count + batch_size - 1) // batch_size)
    while len(passed_items) < count and attempts_without_progress < max_total_attempts:
        remaining = count - len(passed_items)
        request_count = min(remaining, batch_size)
        try:
            scripts = deepseek_once(agent_doc, request_count, duration, brief, character_mode, "", script_kind)
            if scripts:
                before = len(passed_items)
                passed_items.extend(scripts[:remaining])
                attempts_without_progress = 0 if len(passed_items) > before else attempts_without_progress + 1
                continue
            last_error = RuntimeError(
                f"DeepSeek returned only {len(scripts)} parseable scripts; requested {request_count}."
            )
        except Exception as exc:
            last_error = exc
        attempts_without_progress += 1
    if len(passed_items) >= count:
        return passed_items[:count]
    if passed_items:
        print(
            f"WARNING: DeepSeek produced {len(passed_items)} parseable scripts; requested {count}. "
            "Proceeding with parseable scripts instead of failing the whole batch.",
            file=sys.stderr,
        )
        return passed_items[:count]
    raise RuntimeError(
        f"DeepSeek output produced only {len(passed_items)} parseable scripts; requested {count}. Last error: {last_error}"
    ) from last_error


def requested_variant(brief: str) -> str:
    text = brief.lower()
    if "blue" in text and "pink" in text:
        return "all"
    if "pink" in text:
        return "pink"
    if "blue" in text:
        return "blue"
    return ""


def assigned_variant(script_item: dict, index: int, brief: str, image_variant: str = "", character_mode: str = "") -> str:
    mode = (character_mode or "").strip().lower()
    override = (image_variant or "").strip().lower()
    if override in {"blue", "pink", "all"}:
        return override
    forced = requested_variant(brief)
    if forced:
        return forced
    text = str(script_item.get("script") or "").lower()
    if "blue" in text and "pink" in text:
        return "all"
    if "pink" in text:
        return "pink"
    if "blue" in text:
        return "blue"
    return "blue" if index % 2 == 1 else "pink"


def create_task(
    script_item: dict,
    index: int,
    batch_id: str,
    duration: int,
    image_dir: Path,
    brief: str,
    image_variant: str = "",
    character_mode: str = "",
    script_duration: int = 15,
    model_version: str = "",
    tenant_id: str = "",
    owner_open_id: str = "",
    jimeng_account: str = "",
    script_app_token: str = "",
    script_table_id: str = "",
    video_app_token: str = "",
    video_table_id: str = "",
    drive_video_folder_token: str = "",
    drive_tables_folder_token: str = "",
    generation_mode: str = "direct_generate",
    script_kind: str = "default",
) -> Path:
    prompt_dir = tenant_prompts_dir(tenant_id)
    task_dir = tenant_tasks_dir(tenant_id)
    prompt_dir.mkdir(parents=True, exist_ok=True)
    task_dir.mkdir(parents=True, exist_ok=True)

    task_id = next_numbered_task_id(tenant_id, batch_id, prompt_dir, index)
    prompt_file = prompt_dir / f"{task_id}.txt"
    prompt_file.write_text(script_item["script"].strip() + "\n", encoding="utf-8")

    variant = assigned_variant(script_item, index, brief, image_variant, character_mode)

    generation_mode = str(generation_mode or "direct_generate").strip().lower()
    if generation_mode not in {"direct_generate", "write_table"}:
        generation_mode = "direct_generate"
    auto_approve = generation_mode == "direct_generate"
    is_short_script = str(script_kind or "").strip().lower() == "short_6s"

    task = {
        "task_id": task_id,
        "prompt_file": str(prompt_file),
        "images": [],
        "image_source": "manual_bitable",
        "image_suggestion": variant,
        "image_library": str(image_dir),
        "script_source": "deepseek_short_6s" if is_short_script else "deepseek",
        "script_kind": "short_6s" if is_short_script else "default",
        "generation_mode": generation_mode,
        "dialogue_translation": str(script_item.get("dialogue_cn") or ""),
        "script_batch_id": batch_id,
        "script_duration": script_duration,
        "segment_count": 2 if script_duration == 30 else 1,
        "segment_index": 1 if script_duration == 30 else 0,
        "segment_duration": 15 if script_duration == 30 else duration,
        "parent_task_id": task_id if script_duration == 30 else "",
        "generation_state": "pending_segment_1" if script_duration == 30 else "",
        "character_mode": character_mode,
        "duration": duration,
        "ratio": setting("DEFAULT_RATIO", "9:16"),
        "model_version": model_version or setting("DEFAULT_MODEL", "seedance2.0fast_vip"),
        "video_resolution": setting("DEFAULT_RESOLUTION", "720p"),
        "jimeng_account": jimeng_account or ("" if owner_open_id else setting("DEFAULT_JIMENG_ACCOUNT", "")),
        "tenant_id": tenant_id,
        "owner_open_id": owner_open_id,
        "user_script_app_token": script_app_token,
        "user_script_table_id": script_table_id,
        "user_video_app_token": video_app_token,
        "user_video_table_id": video_table_id,
        "drive_video_folder_token": drive_video_folder_token,
        "drive_tables_folder_token": drive_tables_folder_token,
        "data_isolation_level": "physical",
        "status": "pending",
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    if auto_approve:
        task["card_approved"] = True
        task["card_approved_at"] = datetime.now().isoformat(timespec="seconds")
        task["auto_approved_by"] = "direct_generate"
    path = task_dir / f"{task_id}.json"
    path.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate multiple OKIVIVI scripts with DeepSeek and create video tasks.")
    parser.add_argument("--agent-doc", default=setting("SCRIPT_AGENT_DOC"), help="Markdown rule document path.")
    parser.add_argument(
        "--agent-docs",
        default=setting("SCRIPT_AGENT_DOCS"),
        help="Semicolon-separated markdown rule document paths. Takes priority over --agent-doc.",
    )
    parser.add_argument("--count", default=setting("DEFAULT_SCRIPT_COUNT", "3"), help="Number of scripts/tasks to generate.")
    parser.add_argument("--duration", default=setting("DEFAULT_DURATION", "15"), help="Video duration, clamped to 4-15 for Jimeng.")
    parser.add_argument("--script-duration", default="", help="Script/story duration for DeepSeek, usually 15 or 30 seconds.")
    parser.add_argument("--brief", default="", help="Extra generation requirement.")
    parser.add_argument("--image-variant", default="", choices=["", "auto", "blue", "pink", "all"], help="Override image group selection.")
    parser.add_argument("--character-mode", default="", choices=["", "single_vivi"], help="Control scripts to use Vivi only.")
    parser.add_argument(
        "--model-version",
        default=setting("DEFAULT_MODEL", "seedance2.0fast_vip"),
        choices=["seedance2.0", "seedance2.0fast", "seedance2.0_vip", "seedance2.0fast_vip"],
        help="Dreamina multimodal model version.",
    )
    parser.add_argument("--image-dir", default=setting("IMAGE_LIBRARY_DIR", str(ROOT / "vivi-image")), help="Reference image library.")
    parser.add_argument("--tenant-id", default=setting("DEFAULT_TENANT_ID", "default"), help="Tenant/user workspace id.")
    parser.add_argument("--owner-open-id", default="", help="Feishu sender open_id that owns this batch.")
    parser.add_argument("--jimeng-account", default=setting("DEFAULT_JIMENG_ACCOUNT", ""), help="Jimeng account profile for generated tasks.")
    parser.add_argument("--script-app-token", default="", help="Tenant script library bitable app_token.")
    parser.add_argument("--script-table-id", default="", help="Tenant script library bitable table_id.")
    parser.add_argument("--video-app-token", default="", help="Tenant video review bitable app_token.")
    parser.add_argument("--video-table-id", default="", help="Tenant video review bitable table_id.")
    parser.add_argument("--drive-video-folder-token", default="", help="Tenant video Drive folder token.")
    parser.add_argument("--drive-tables-folder-token", default="", help="Tenant table Drive folder token.")
    parser.add_argument(
        "--generation-mode",
        default="direct_generate",
        choices=["direct_generate", "write_table"],
        help="direct_generate auto-approves scripts; write_table only writes them to Feishu for manual generation.",
    )
    parser.add_argument(
        "--script-kind",
        default="default",
        choices=["default", "short_6s", "share_15s"],
        help="Generation profile for the produced scripts.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print generated scripts without writing tasks.")
    args = parser.parse_args()

    agent_doc = load_agent_docs(args.agent_doc, args.agent_docs)
    count = max(1, min(20, int(float(args.count))))
    duration = clamp_duration(args.duration)
    script_duration_raw = int(float(args.script_duration or args.duration or 15))
    script_duration = 30 if script_duration_raw > 15 else clamp_duration(str(script_duration_raw))
    image_dir = Path(args.image_dir).expanduser()
    batch_id = datetime.now().strftime("vivi-%m%d-%H")
    if args.owner_open_id and not args.jimeng_account.strip():
        raise SystemExit("Missing Jimeng account. Configure SHARED_JIMENG_ACCOUNT or save a personal Jimeng profile.")

    scripts = call_deepseek(
        agent_doc,
        count,
        script_duration,
        args.brief,
        args.character_mode,
        args.script_kind,
    )
    if len(scripts) < count:
        print(f"WARNING: requested {count}, DeepSeek returned {len(scripts)}", file=sys.stderr)

    if args.dry_run:
        print(json.dumps(scripts, ensure_ascii=False, indent=2))
        return 0

    created: List[str] = []
    for idx, item in enumerate(scripts, start=1):
        path = create_task(
            item,
            idx,
            batch_id,
            duration,
            image_dir,
            args.brief,
            args.image_variant,
            args.character_mode,
            script_duration,
            args.model_version,
            args.tenant_id,
            args.owner_open_id,
            args.jimeng_account,
            args.script_app_token,
            args.script_table_id,
            args.video_app_token,
            args.video_table_id,
            args.drive_video_folder_token,
            args.drive_tables_folder_token,
            args.generation_mode,
            args.script_kind,
        )
        created.append(str(path))
        time.sleep(0.1)
    print("\n".join(created))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
