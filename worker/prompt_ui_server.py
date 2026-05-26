#!/usr/bin/env python3
import argparse
import json
import subprocess
import sys
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict

from create_task import ROOT, clamp_duration


PAYLOADS = ROOT / "script_requests" / "ui_payloads"
STATE_FILE = ROOT / "bitable_state.json"


HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>OKIVIVI 文案生成</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f5f7fb;
      --panel: #ffffff;
      --ink: #172033;
      --muted: #667085;
      --line: #d9e0ea;
      --blue: #2563eb;
      --blue-soft: #dbeafe;
      --shadow: 0 18px 50px rgba(30, 41, 59, 0.12);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Microsoft YaHei", "Segoe UI", Arial, sans-serif;
      background: var(--bg);
      color: var(--ink);
    }
    main {
      width: min(1040px, calc(100vw - 32px));
      margin: 28px auto;
      display: grid;
      grid-template-columns: minmax(0, 1fr) 320px;
      gap: 20px;
    }
    .workspace, .side {
      background: var(--panel);
      border: 1px solid var(--line);
      box-shadow: var(--shadow);
    }
    .workspace { padding: 24px; }
    .side { padding: 20px; align-self: start; }
    h1 { margin: 0 0 6px; font-size: 24px; font-weight: 750; }
    .sub { color: var(--muted); font-size: 14px; margin-bottom: 22px; }
    .grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 16px;
      margin-bottom: 16px;
    }
    label { display: block; font-size: 13px; color: #344054; margin-bottom: 8px; }
    select, input, textarea {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      color: var(--ink);
      font: inherit;
      padding: 11px 12px;
      outline: none;
    }
    select:focus, input:focus, textarea:focus { border-color: var(--blue); box-shadow: 0 0 0 3px var(--blue-soft); }
    textarea { min-height: 112px; resize: vertical; line-height: 1.6; }
    .actions { display: flex; align-items: center; gap: 12px; margin-top: 18px; flex-wrap: wrap; }
    .model-tabs {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 8px;
      margin: 4px 0 16px;
    }
    .model-tabs input { position: absolute; opacity: 0; pointer-events: none; }
    .model-tabs label {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px 8px;
      text-align: center;
      cursor: pointer;
      margin: 0;
      color: #344054;
      background: #fff;
      min-height: 44px;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .model-tabs input:checked + label {
      border-color: var(--blue);
      background: var(--blue-soft);
      color: var(--blue);
      font-weight: 700;
    }
    button {
      border: 0;
      border-radius: 8px;
      padding: 12px 18px;
      font: inherit;
      cursor: pointer;
    }
    .primary { background: var(--blue); color: white; font-weight: 700; }
    .ghost { background: #eef2f7; color: #344054; }
    button:disabled { opacity: .55; cursor: wait; }
    .hint { color: var(--muted); font-size: 13px; }
    .side h2 { margin: 0 0 14px; font-size: 16px; }
    .kv { border-top: 1px solid var(--line); padding-top: 12px; margin-top: 12px; font-size: 13px; color: var(--muted); }
    .kv b { display: block; color: var(--ink); margin-bottom: 4px; }
    .result {
      margin-top: 18px;
      border: 1px solid var(--line);
      background: #f8fafc;
      padding: 14px;
      min-height: 78px;
      white-space: pre-wrap;
      font-family: Consolas, "Microsoft YaHei", monospace;
      font-size: 13px;
      line-height: 1.55;
    }
    .ok { border-color: #86efac; background: #f0fdf4; }
    .bad { border-color: #fecaca; background: #fef2f2; }
    a { color: var(--blue); text-decoration: none; }
    @media (max-width: 820px) {
      main { grid-template-columns: 1fr; }
      .grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <main>
    <section class="workspace">
      <h1>OKIVIVI 文案生成</h1>
      <div class="sub">选择文案长度和角色后提交，系统会调用 DeepSeek，写入文案库，并进入飞书确认流程。</div>

      <form id="form">
        <div class="grid">
          <div>
            <label for="count">生成条数</label>
            <select id="count" name="count">
              <option value="1" selected>1 条</option>
              <option value="2">2 条</option>
              <option value="3">3 条</option>
              <option value="5">5 条</option>
              <option value="10">10 条</option>
            </select>
          </div>
          <div>
            <label for="script_duration">视频文案时长</label>
            <select id="script_duration" name="script_duration">
              <option value="15" selected>15s</option>
              <option value="30">30s</option>
            </select>
          </div>
          <div>
            <label for="character_mode">角色选择</label>
            <select id="character_mode" name="character_mode">
              <option value="single_vivi" selected>1. vivi</option>
              <option value="bree_sunny">2. bree, sunny</option>
            </select>
          </div>
          <div>
            <label for="source">来源</label>
            <select id="source" name="source">
              <option value="prompt_ui" selected>本地 UI</option>
              <option value="server_product_reserved">服务器商品预留</option>
            </select>
          </div>
        </div>

        <div>
          <label>调用模型</label>
          <div class="model-tabs">
            <div>
              <input id="model_seedance20" type="radio" name="model_version" value="seedance2.0" />
              <label for="model_seedance20">Seedance 2.0</label>
            </div>
            <div>
              <input id="model_seedance20fast" type="radio" name="model_version" value="seedance2.0fast" />
              <label for="model_seedance20fast">2.0 Fast</label>
            </div>
            <div>
              <input id="model_seedance20vip" type="radio" name="model_version" value="seedance2.0_vip" />
              <label for="model_seedance20vip">2.0 VIP</label>
            </div>
            <div>
              <input id="model_seedance20fastvip" type="radio" name="model_version" value="seedance2.0fast_vip" checked />
              <label for="model_seedance20fastvip">Fast VIP</label>
            </div>
          </div>
        </div>

        <div>
          <label for="brief">备注内容</label>
          <textarea id="brief" name="brief" placeholder="填写用户的文案大意，例如：宿舍里刚结束 group project，疲惫但好笑；厨房出门前，咖啡和购物清单。"></textarea>
        </div>

        <div class="grid" style="margin-top:16px">
          <div>
            <label for="product_id">商品 ID（预留）</label>
            <input id="product_id" name="product_id" placeholder="后续接服务器商品视频生成" />
          </div>
          <div>
            <label for="product_url">商品链接（预留）</label>
            <input id="product_url" name="product_url" placeholder="https://..." />
          </div>
        </div>

        <div class="actions">
          <button class="primary" id="submit" type="submit">生成文案</button>
          <button class="ghost" type="button" id="clear">清空</button>
          <span class="hint">提交后等待飞书机器人推送审核记录。</span>
        </div>
      </form>

      <div id="result" class="result">等待提交。</div>
    </section>

    <aside class="side">
      <h2>当前链路</h2>
      <div class="kv"><b>文案长度</b>15s 或 30s，只影响 DeepSeek 生成的分镜脚本长度</div>
      <div class="kv"><b>角色选择</b>1.vivi 生成单角色；2.bree,sunny 生成双角色并匹配四张图</div>
      <div class="kv"><b>视频参数</b>即梦生成使用当前选中的模型 / 9:16 / 720p</div>
      <div class="kv"><b>人工确认</b>文案写入文案库，飞书表格确认后再生成视频</div>
      <div class="kv"><b>表格入口</b><span id="links">读取中...</span></div>
    </aside>
  </main>

  <script>
    const form = document.getElementById('form');
    const result = document.getElementById('result');
    const submit = document.getElementById('submit');
    document.getElementById('clear').addEventListener('click', () => {
      document.getElementById('brief').value = '';
      document.getElementById('product_id').value = '';
      document.getElementById('product_url').value = '';
      result.className = 'result';
      result.textContent = '等待提交。';
    });

    async function loadHealth() {
      try {
        const res = await fetch('/api/health');
        const data = await res.json();
        const links = [];
        if (data.review_url) links.push(`<a href="${data.review_url}" target="_blank">审核表</a>`);
        if (data.script_url) links.push(`<a href="${data.script_url}" target="_blank">文案库</a>`);
        document.getElementById('links').innerHTML = links.join(' / ') || '未配置';
      } catch {
        document.getElementById('links').textContent = '服务未就绪';
      }
    }

    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      submit.disabled = true;
      result.className = 'result';
      result.textContent = '正在调用 DeepSeek 生成文案...';
      const payload = Object.fromEntries(new FormData(form).entries());
      payload.count = Number(payload.count || 1);
      payload.script_duration = Number(payload.script_duration || 15);
      try {
        const res = await fetch('/api/generate', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (!res.ok || !data.ok) throw new Error(data.error || '生成失败');
        result.className = 'result ok';
        result.textContent =
          `已提交：${data.request_id}\n` +
          `生成任务数：${data.created_tasks.length}\n\n` +
          data.created_tasks.join('\n');
      } catch (err) {
        result.className = 'result bad';
        result.textContent = `生成失败：${err.message}`;
      } finally {
        submit.disabled = false;
      }
    });
    loadHealth();
  </script>
</body>
</html>
"""


def load_state() -> Dict[str, Any]:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def clean_text(value: Any) -> str:
    return str(value or "").strip()


def parse_submit_output(text: str) -> Dict[str, Any]:
    request_id = ""
    created = []
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("request_id="):
            request_id = line.split("=", 1)[1].strip()
        elif line.endswith(".json") and "/tasks/pending/" in line:
            created.append(line)
    return {"request_id": request_id, "created_tasks": created}


def normalize_script_duration(value: Any) -> int:
    try:
        duration = int(float(value or 15))
    except (TypeError, ValueError):
        duration = 15
    return 30 if duration > 15 else 15


def normalize_model_version(value: Any) -> str:
    allowed = {"seedance2.0", "seedance2.0fast", "seedance2.0_vip", "seedance2.0fast_vip"}
    model = clean_text(value) or "seedance2.0fast_vip"
    return model if model in allowed else "seedance2.0fast_vip"


def build_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    count = max(1, min(20, int(float(data.get("count") or 1))))
    script_duration = normalize_script_duration(data.get("script_duration"))
    character_mode = clean_text(data.get("character_mode")).lower() or "single_vivi"
    if character_mode not in {"single_vivi", "bree_sunny"}:
        character_mode = "single_vivi"
    image_variant = "all" if character_mode == "bree_sunny" else "auto"
    return {
        "count": count,
        "duration": clamp_duration(str(min(script_duration, 15))),
        "script_duration": script_duration,
        "brief": clean_text(data.get("brief")),
        "source": clean_text(data.get("source")) or "prompt_ui",
        "character_mode": character_mode,
        "image_variant": image_variant,
        "model_version": normalize_model_version(data.get("model_version")),
        "product_id": clean_text(data.get("product_id")),
        "product_url": clean_text(data.get("product_url")),
        "product_payload": {},
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "OKIVIVIPromptUI/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[{datetime.now().isoformat(timespec='seconds')}] {self.address_string()} {fmt % args}", flush=True)

    def send_json(self, status: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/" or self.path.startswith("/?"):
            body = HTML.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/api/health":
            state = load_state()
            self.send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "root": str(ROOT),
                    "review_url": state.get("url") or state.get("review_url") or "https://ncnrqomkm3wb.feishu.cn/base/V4w1b2NKcalN05sxJQJcyMNenef",
                    "script_url": state.get("script_url") or "https://ncnrqomkm3wb.feishu.cn/base/LdViboZxnaGsmLsuAmOc3E9FnTd",
                },
            )
            return
        self.send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:
        if self.path != "/api/generate":
            self.send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            payload = build_payload(data)
            payload_name = datetime.now().strftime("ui-entry-%Y%m%d-%H%M%S-%f.json")
            payload_path = PAYLOADS / payload_name
            write_json(payload_path, payload)
            proc = subprocess.run(
                [sys.executable, "worker/submit_script_request.py", "--payload-file", str(payload_path)],
                cwd=str(ROOT),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=420,
            )
            parsed = parse_submit_output(proc.stdout)
            if proc.returncode != 0:
                self.send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"ok": False, "error": proc.stdout[-3000:], "payload_file": str(payload_path)},
                )
                return
            self.send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "request_id": parsed.get("request_id"),
                    "created_tasks": parsed.get("created_tasks", []),
                    "payload_file": str(payload_path),
                    "output": proc.stdout[-2000:],
                },
            )
        except Exception as exc:
            self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8765, type=int)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"OKIVIVI prompt UI running at http://{args.host}:{args.port}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
