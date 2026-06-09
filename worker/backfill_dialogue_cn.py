#!/usr/bin/env python3
import json
import re
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "worker"))

import feishu_worker as fw
import generate_scripts as gs


def plain_text(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
            else:
                parts.append(str(item))
        return "".join(parts)
    return str(value or "")


def translate_dialogue(script: str) -> str:
    api_key = gs.setting("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is empty")
    payload = {
        "model": gs.setting("DEEPSEEK_MODEL", "deepseek-chat"),
        "temperature": 0.1,
        "messages": [
            {
                "role": "system",
                "content": "你只负责从分镜脚本中提取英文对白并翻译为中文。只输出对话中文，不要解释。",
            },
            {
                "role": "user",
                "content": (
                    "从下面脚本中只提取英文对白，翻译为中文，保留说话人标签。"
                    "格式示例：\nGirl：……\nVivi：……\n不要翻译人物描述、环境描述、动作描述。\n\n"
                    + script
                ),
            },
        ],
    }
    response = gs.post_json(
        f"{gs.setting('DEEPSEEK_BASE_URL', 'https://api.deepseek.com').rstrip('/')}/chat/completions",
        payload,
        {"Authorization": f"Bearer {api_key}"},
    )
    text = response["choices"][0]["message"]["content"].strip()
    text = re.sub(r"^```(?:text|markdown)?\s*|\s*```$", "", text, flags=re.I).strip()
    if not re.search(r"[\u4e00-\u9fff]", text):
        raise RuntimeError("translated dialogue is not Chinese")
    return text


def main() -> None:
    owner_open_id = sys.argv[1] if len(sys.argv) > 1 else "ou_402611cd232d4982f37708939a575452"
    ctx = fw.user_context(owner_open_id)
    app_token = ctx.get("script_app_token")
    table_id = ctx.get("script_table_id")
    if not app_token or not table_id:
        raise RuntimeError("script table is not configured for this user")
    api = fw.FeishuApi()
    updated = 0
    skipped = 0
    for record in api.list_bitable_records(app_token, table_id):
        record_id = fw.record_id_of(record)
        fields = record.get("fields") or {}
        task_id = plain_text(fields.get("任务ID")).strip()
        script = plain_text(fields.get("文案")).strip()
        existing = plain_text(fields.get("对话中文")).strip()
        if not record_id or not task_id or existing or not script:
            skipped += 1
            continue
        try:
            dialogue_cn = translate_dialogue(script)
            api.update_bitable_record(app_token, table_id, record_id, {"对话中文": dialogue_cn})
            task_path = fw.find_task(task_id, fw.task_context_from_user(ctx))
            if task_path:
                task = fw.read_task(task_path)
                task["dialogue_translation"] = dialogue_cn
                fw.write_task(fw.status_for_task(task_id, task) or "reviewing", task)
            print(f"updated {task_id}")
            updated += 1
        except Exception as exc:
            print(f"skipped {task_id}: {exc}")
            skipped += 1
    print(json.dumps({"updated": updated, "skipped": skipped}, ensure_ascii=False))


if __name__ == "__main__":
    main()
