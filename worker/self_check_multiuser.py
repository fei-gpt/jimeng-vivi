#!/usr/bin/env python3
"""Local regression checks for multi-user and multi-worker safety."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

try:
    from worker import feishu_worker as fw
except ModuleNotFoundError:
    import feishu_worker as fw


class FakeBitableApi:
    def __init__(self) -> None:
        self.records = {
            ("app", "tbl", "rec"): {
                "fields": {"状态": "", "运行节点": "", "认领ID": ""},
            }
        }

    def get_bitable_record(self, app_token: str, table_id: str, record_id: str) -> dict:
        return self.records[(app_token, table_id, record_id)]

    def update_bitable_record(self, app_token: str, table_id: str, record_id: str, fields: dict) -> None:
        self.records[(app_token, table_id, record_id)]["fields"].update(fields)


def ensure_account(name: str, owner: str = "") -> None:
    account_home = fw.ROOT / "accounts" / "jimeng" / name / "home"
    account_home.mkdir(parents=True, exist_ok=True)
    if owner:
        meta = {"owner_open_id": owner, "display_name": name}
        (account_home.parent / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")


def write_reviewing_task(tenant: str, task_id: str) -> None:
    directory = fw.ROOT / "tenants" / tenant / "tasks" / "reviewing"
    directory.mkdir(parents=True, exist_ok=True)
    task = {
        "task_id": task_id,
        "tenant_id": tenant,
        "owner_open_id": f"ou_{tenant}",
        "jimeng_account": f"acct_{tenant}",
        "status": "reviewing",
    }
    (directory / f"{task_id}.json").write_text(json.dumps(task, ensure_ascii=False), encoding="utf-8")


def main() -> int:
    ensure_account("codex_owner_a", "ou_codex_a")
    ensure_account("codex_owner_b", "ou_codex_b")
    ensure_account("codex_legacy")

    ctx_a = {"owner_open_id": "ou_codex_a", "tenant_id": "tenant_a", "jimeng_account": "codex_legacy"}
    ctx_b = {"owner_open_id": "ou_codex_b", "tenant_id": "tenant_b", "jimeng_account": ""}

    assert fw.account_owned_by_user("codex_owner_a", ctx_a)
    assert not fw.account_owned_by_user("codex_owner_b", ctx_a)
    assert fw.account_owned_by_user("codex_legacy", ctx_a)
    assert not fw.account_owned_by_user("codex_legacy", ctx_b)
    assert "codex_owner_b" not in fw.jimeng_accounts_text(ctx_a)
    assert fw.user_context("ou_user_without_config").get("jimeng_account") == ""

    write_reviewing_task("codex_tenant_a", "same-task")
    write_reviewing_task("codex_tenant_b", "same-task")
    for tenant in ["codex_tenant_a", "codex_tenant_b"]:
        ctx = {"tenant_id": tenant}
        path = fw.find_task("same-task", ctx)
        assert path and f"tenants/{tenant}/tasks/reviewing" in str(path)
        assert fw.status_for_task("same-task", ctx) == "reviewing"
        assert fw.task_instance_key(fw.read_task(path)) == f"{tenant}:same-task"

    api = FakeBitableApi()
    task = {
        "task_id": "claim-task",
        "review_bitable_app_token": "app",
        "review_bitable_table_id": "tbl",
        "review_bitable_record_id": "rec",
    }
    assert fw.claim_task_in_bitable(api, task)
    fields = api.records[("app", "tbl", "rec")]["fields"]
    assert fields["运行节点"] == fw.WORKER_INSTANCE_ID
    assert fields["认领ID"].startswith(f"{fw.WORKER_INSTANCE_ID}:")

    api.records[("app", "tbl", "rec")]["fields"].update({
        "状态": "running",
        "运行节点": "other-worker",
        "认领ID": "other-worker:abc",
    })
    assert not fw.claim_task_in_bitable(api, task)

    proc = subprocess.run(
        [
            sys.executable,
            "worker/create_task.py",
            "--prompt",
            "/tmp/no-such-prompt.txt",
            "--owner-open-id",
            "ou_no_account",
        ],
        cwd=fw.ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    assert proc.returncode != 0
    assert "Missing Jimeng account" in proc.stdout

    print("multi-user and multi-worker self-check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
