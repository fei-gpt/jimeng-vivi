from pathlib import Path
import json
import urllib.error
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
FILE_TOKEN = "FWkebPGdCoRl2Ex9IZFcCfd6nJd"
OUTPUT = ROOT / "deepseek" / "OKIVIVI-feishu-current.md"


def load_env():
    env = {}
    for raw in (ROOT / ".env").read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip("\"").strip("'")
    return env


def request_json(method, url, payload=None, headers=None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    if payload is not None:
        req.add_header("Content-Type", "application/json; charset=utf-8")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    env = load_env()
    app_id = env.get("FEISHU_APP_ID")
    app_secret = env.get("FEISHU_APP_SECRET")
    if not app_id or not app_secret:
        raise SystemExit("FEISHU_APP_ID/FEISHU_APP_SECRET missing in .env")

    base = env.get("FEISHU_OPENAPI_BASE", "https://open.feishu.cn").rstrip("/")
    token_resp = request_json(
        "POST",
        base + "/open-apis/auth/v3/tenant_access_token/internal",
        {"app_id": app_id, "app_secret": app_secret},
    )
    if token_resp.get("code") != 0:
        raise SystemExit("tenant token failed: " + json.dumps(token_resp, ensure_ascii=False))

    headers = {"Authorization": "Bearer " + token_resp["tenant_access_token"]}
    endpoints = [
        base + f"/open-apis/drive/v1/medias/{FILE_TOKEN}/download",
        base + f"/open-apis/drive/v1/files/{FILE_TOKEN}/download",
    ]
    errors = []
    for url in endpoints:
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=90) as resp:
                data = resp.read()
                content_type = resp.headers.get("Content-Type", "")
            if data[:300].lstrip().startswith(b"{"):
                try:
                    errors.append((url, "json", json.loads(data.decode("utf-8"))))
                    continue
                except Exception:
                    pass
            try:
                text = data.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise SystemExit("downloaded file is not UTF-8 markdown text") from exc
            if len(text.strip()) < 50:
                raise SystemExit("downloaded file is too short; check link permission/content")
            OUTPUT.parent.mkdir(parents=True, exist_ok=True)
            OUTPUT.write_text(text, encoding="utf-8")
            print(json.dumps({
                "ok": True,
                "bytes": len(data),
                "chars": len(text),
                "content_type": content_type,
                "output": str(OUTPUT),
            }, ensure_ascii=False))
            print(text[:300].replace("\r", ""))
            return
        except urllib.error.HTTPError as exc:
            errors.append((url, "http", exc.code, exc.read().decode("utf-8", errors="replace")[:1000]))

    raise SystemExit("all download endpoints failed: " + json.dumps(errors, ensure_ascii=False))


if __name__ == "__main__":
    main()
