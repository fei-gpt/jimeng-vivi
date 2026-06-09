#!/usr/bin/env python3
import json
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "worker"))

import feishu_worker as fw
from backfill_dialogue_cn import plain_text


DIALOGUE = {
    "20260529-0956-farmer_market_negotiation": "Girl：八美元一盒草莓。这都够吃一顿午饭了。\nVivi：你上周花了十美元买蜡烛。你甚至都不点。\nGirl：你真是个昂贵的习惯。但你说得对。",
    "20260529-0956-coffee_run_stalling": "Girl：我应该回工位了。但也可以……不回。\nVivi：你已经说“再五分钟”说了一个小时了。\nGirl：这是我唯一的目击者，结果它还告我状。\nGirl：行吧。我们去负责任。",
    "20260529-0956-closet_meltdown": "Girl：我没衣服穿，可我明明有一整个衣柜。\nVivi：你昨天也这么说。后来穿了那件黑色的，还被夸了两次。\nGirl：行。但我只是因为你记得才听你的。",
    "20260529-0956-park_bench_dare": "Girl：你觉得它看不透我？看好了。\nVivi：你真的在要求一个巴掌大的东西评价你的选择。\nGirl：好吧，它说得有道理。\nGirl：是我自己撞上来的。",
    "20260529-0956-grocery_decision_loser": "Girl：我已经在这里站了四分钟。这真是新低。\nVivi：你上次也这么说。然后你两瓶都买了。\nGirl：这件事只有你和我知道。",
}


def main() -> None:
    owner_open_id = "ou_402611cd232d4982f37708939a575452"
    ctx = fw.user_context(owner_open_id)
    api = fw.FeishuApi()
    records = api.list_bitable_records(ctx["script_app_token"], ctx["script_table_id"])
    updated = []
    for record in records:
        fields = record.get("fields") or {}
        task_id = plain_text(fields.get("任务ID")).strip()
        if task_id not in DIALOGUE:
            continue
        record_id = fw.record_id_of(record)
        if not record_id:
            continue
        api.update_bitable_record(ctx["script_app_token"], ctx["script_table_id"], record_id, {"对话中文": DIALOGUE[task_id]})
        task_path = fw.find_task(task_id, fw.task_context_from_user(ctx))
        if task_path:
            task = fw.read_task(task_path)
            task["dialogue_translation"] = DIALOGUE[task_id]
            fw.write_task(fw.status_for_task(task_id, task) or "done", task)
        updated.append(task_id)
    print(json.dumps({"updated": updated}, ensure_ascii=False))


if __name__ == "__main__":
    main()
