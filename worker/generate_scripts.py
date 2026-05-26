#!/usr/bin/env python3
import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from create_task import ROOT, clamp_duration, slug


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


ENV = load_env(ROOT / ".env")


def setting(name: str, default: str = "") -> str:
    return ENV.get(name, default)


def tenant_slug(value: str) -> str:
    return slug(str(value or ""), "")


def tenant_root(tenant_id: str) -> Path:
    cleaned = tenant_slug(tenant_id)
    return TENANTS / cleaned if cleaned else ROOT


def tenant_tasks_dir(tenant_id: str) -> Path:
    base = tenant_root(tenant_id)
    return TASKS if base == ROOT else base / "tasks" / "pending"


def tenant_prompts_dir(tenant_id: str) -> Path:
    base = tenant_root(tenant_id)
    return PROMPTS if base == ROOT else base / "prompts" / "generated"


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
        with urllib.request.urlopen(req, timeout=timeout) as resp:
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
    return text.strip()


def validate_script(script: str, index: int) -> None:
    lowered = script.lower()
    has_vivi = re.search(r"\bvivi\b", lowered) is not None
    has_bree = re.search(r"\bbree\b", lowered) is not None
    has_sunny = re.search(r"\bsunny\b", lowered) is not None
    if not (has_vivi or has_bree or has_sunny):
        raise ValueError(f"Script {index} must mention vivi, bree, or sunny.")
    if has_bree != has_sunny:
        raise ValueError(f"Script {index} cannot mention only one of bree/sunny; use vivi for single-character scripts.")
    missing = [token for token in ["=vivi", "人物描述", "环境描述"] if token not in script]
    if missing:
        raise ValueError(f"Script {index} is missing required tokens: {', '.join(missing)}.")
    if not re.search(r"\b0\s*-\s*\d+s", lowered):
        raise ValueError(f"Script {index} must include timestamped shot segments.")
    if re.search(r"caption|cta|总结|表格|tiktok caption", lowered):
        raise ValueError(f"Script {index} contains forbidden non-shot-script content.")


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
        normalized.append(
            {
                "name": str(item.get("name") or item.get("title") or f"script-{idx}").strip(),
                "script": script,
            }
        )
    return normalized


FEW_SHOT_EXAMPLES = """
以下是格式示例，只学习结构，不要复写内容：
[
  {
    "name": "dorm_after_class_reset",
    "script": "=vivi。vivi大小只有巴掌大小 8cm*5cm，vivi眼睛会左右张望和眨眼（眼睛表情十分丰富，vivi 头部下方的尾巴非常灵动，轻轻摇晃，vivi 以画外音发声（九岁女童），无口鼻等面部特征，无字幕。镜头轻微晃动，画面带有日常生活的粗糙感。\\n\\n人物描述：22岁美国白人大学女生，棕色头发松松扎起，穿oversized college hoodie、straight jeans和sneakers，像是刚从一节很累的课回来。\\n\\n环境描述：美国宿舍书桌旁，laptop半开着，notebook旁放着荧光笔和半杯iced coffee，椅背上搭着hoodie。\\n\\n0-3s：她把Vivi捧到镜头前，像展示一个刚救她一命的小证据：“Before anyone asks, this is my emotional support after class.”\\n\\n3-8s：她低头看Vivi，指尖轻轻拢住它，Vivi眼睛左右张望并眨了一下，尾巴轻轻晃动。Vivi以画外音发声：“You said you were fine three times today. My logs disagree.”\\n\\n8-12s：她愣住，慢慢看回镜头：“That is a very aggressive wellness check.”\\n\\n12-15s：她把Vivi抱近胸口，靠回椅背，小声说：“Fine. The wellness check can stay.”"
  }
]
""".strip()


def build_user_prompt(count: int, duration: int, brief: str, character_mode: str = "") -> str:
    extra = f"\n用户额外要求：{brief.strip()}\n" if brief.strip() else ""
    mode = (character_mode or "").strip().lower()
    if mode == "single_vivi":
        role_rule = (
            "\n当前角色选择：1.vivi。每条文案只写一个角色 Vivi；正文不得出现 Bree 或 Sunny；"
            "不要在正文中用 blue/pink 作为角色名。图片颜色由系统后续匹配，不写进文案。\n"
        )
    elif mode == "bree_sunny":
        role_rule = (
            "\n当前角色选择：2.bree,sunny。每条文案必须同时出现 Bree 和 Sunny，表示 blue 与 pink 两个形态同场；"
            "不能只出现其中一个。允许保留固定开头 =vivi，但分镜内容必须清楚写出 Bree 和 Sunny 两个角色。\n"
        )
    else:
        role_rule = ""
    return f"""
请严格读取并执行 system message 中的 md 文档规则。
硬性要求：
1. “文案”在这里等于文档中的“分镜脚本”，不是普通广告口播。
2. 每条只输出「分镜」模块正文，不要标题、摘要、TikTok Caption、CTA、总结、表格。
3. 每条必须包含固定 text、人物描述、环境描述、时间段分镜。
4. 每条脚本必须以固定一致性句开头，且包含“=vivi”“人物描述”“环境描述”。
5. 单角色脚本只能写 Vivi，不要只写 Bree 或只写 Sunny。
6. 只有当脚本同时表现 blue 和 pink 两个形态时，才允许同时出现 Bree 和 Sunny；不能只出现其中一个。
7. pink/blue 是外部图片组选择；用户只要求 pink 或只要求 blue 时，正文仍然按单角色写 Vivi。
8. 如果输出格式不符合 md 文档，必须在输出前自行重写。

请生成 {count} 条可直接用于即梦视频生成的 {duration}s 分镜脚本。
{role_rule}
只输出 JSON 数组，不要 Markdown，不要解释。数组中每个元素格式：
{{
  "name": "short_task_name",
  "script": "完整分镜脚本"
}}

生成前自检：
- 如果任意一条只出现 Sunny 或只出现 Bree，请改写为 Vivi。
- 只有 blue+pink 双形态脚本才允许 Bree 和 Sunny 同时出现。
- 不要输出编号短口播，不要输出广告口播，不要输出“Caption/CTA/总结/表格”。
{extra}
""".strip()


def deepseek_once(agent_doc: str, count: int, duration: int, brief: str, character_mode: str = "", feedback: str = "") -> List[dict]:
    api_key = setting("DEEPSEEK_API_KEY")
    if not api_key:
        raise SystemExit("DEEPSEEK_API_KEY is required in .env")
    base_url = setting("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    model = setting("DEEPSEEK_MODEL", "deepseek-chat")
    user_prompt = build_user_prompt(count, duration, brief, character_mode)
    if setting("DEEPSEEK_FEW_SHOT", "1") != "0":
        user_prompt = f"{FEW_SHOT_EXAMPLES}\n\n{user_prompt}"
    if feedback:
        user_prompt += f"\n\n上一次输出未通过校验，请只修正问题并重新输出完整 JSON 数组。校验错误：\n{feedback}"
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


def call_deepseek(agent_doc: str, count: int, duration: int, brief: str, character_mode: str = "") -> List[dict]:
    attempts = max(1, int(setting("DEEPSEEK_REWRITE_ATTEMPTS", "2")))
    feedback = ""
    last_error: Optional[Exception] = None
    for _ in range(attempts + 1):
        try:
            return deepseek_once(agent_doc, count, duration, brief, character_mode, feedback)
        except Exception as exc:
            last_error = exc
            feedback = str(exc)
    raise RuntimeError(f"DeepSeek output failed validation after retries: {last_error}") from last_error


def requested_variant(brief: str) -> str:
    text = brief.lower()
    if ("bree" in text and "sunny" in text) or ("blue" in text and "pink" in text):
        return "all"
    if "pink" in text or "sunny" in text:
        return "pink"
    if "blue" in text or "bree" in text:
        return "blue"
    return ""


def assigned_variant(script_item: dict, index: int, brief: str, image_variant: str = "", character_mode: str = "") -> str:
    mode = (character_mode or "").strip().lower()
    if mode == "bree_sunny":
        return "all"
    override = (image_variant or "").strip().lower()
    if override in {"blue", "pink", "all"}:
        return override
    forced = requested_variant(brief)
    if forced:
        return forced
    text = str(script_item.get("script") or "").lower()
    if ("bree" in text and "sunny" in text) or ("blue" in text and "pink" in text):
        return "all"
    if "sunny" in text or "pink" in text:
        return "pink"
    if "bree" in text or "blue" in text:
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
) -> Path:
    prompt_dir = tenant_prompts_dir(tenant_id)
    task_dir = tenant_tasks_dir(tenant_id)
    prompt_dir.mkdir(parents=True, exist_ok=True)
    task_dir.mkdir(parents=True, exist_ok=True)

    name = slug(script_item.get("name") or f"script-{index}")
    task_id = f"{batch_id}-{index:02d}-{name}"
    prompt_file = prompt_dir / f"{task_id}.txt"
    prompt_file.write_text(script_item["script"].strip() + "\n", encoding="utf-8")

    variant = assigned_variant(script_item, index, brief, image_variant, character_mode)

    task = {
        "task_id": task_id,
        "prompt_file": str(prompt_file),
        "images": [],
        "image_source": "manual_bitable",
        "image_suggestion": variant,
        "image_library": str(image_dir),
        "script_source": "deepseek",
        "script_batch_id": batch_id,
        "script_duration": script_duration,
        "character_mode": character_mode,
        "duration": duration,
        "ratio": setting("DEFAULT_RATIO", "9:16"),
        "model_version": model_version or setting("DEFAULT_MODEL", "seedance2.0fast_vip"),
        "video_resolution": setting("DEFAULT_RESOLUTION", "720p"),
        "jimeng_account": jimeng_account or setting("DEFAULT_JIMENG_ACCOUNT", ""),
        "tenant_id": tenant_id,
        "owner_open_id": owner_open_id,
        "user_script_app_token": script_app_token,
        "user_script_table_id": script_table_id,
        "user_video_app_token": video_app_token,
        "user_video_table_id": video_table_id,
        "data_isolation_level": "physical",
        "status": "pending",
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
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
    parser.add_argument("--character-mode", default="", choices=["", "single_vivi", "bree_sunny"], help="Control whether scripts use Vivi only or Bree+Sunny.")
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
    parser.add_argument("--dry-run", action="store_true", help="Print generated scripts without writing tasks.")
    args = parser.parse_args()

    agent_doc = load_agent_docs(args.agent_doc, args.agent_docs)
    count = max(1, min(20, int(float(args.count))))
    duration = clamp_duration(args.duration)
    script_duration = int(float(args.script_duration or args.duration or 15))
    script_duration = 30 if script_duration > 15 else 15
    image_dir = Path(args.image_dir).expanduser()
    batch_id = datetime.now().strftime("okivivi-%Y%m%d-%H%M%S")

    scripts = call_deepseek(agent_doc, count, script_duration, args.brief, args.character_mode)
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
        )
        created.append(str(path))
        time.sleep(0.1)
    print("\n".join(created))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
