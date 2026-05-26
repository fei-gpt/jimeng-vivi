#!/usr/bin/env python3
import base64
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request


def main() -> int:
    owner = os.environ.get("GITHUB_OWNER", "fei-gpt")
    repo = os.environ.get("GITHUB_REPO", "jimeng-vivi")
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise SystemExit("GITHUB_TOKEN is required")

    api = f"https://api.github.com/repos/{owner}/{repo}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "okivivi-uploader",
    }

    def request(method: str, url: str, data=None, allow_404: bool = False, allow_statuses=None):
        allow_statuses = set(allow_statuses or [])
        body = None
        request_headers = dict(headers)
        if data is not None:
            body = json.dumps(data).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=body, headers=request_headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                text = resp.read().decode("utf-8")
                return json.loads(text) if text else {}
        except urllib.error.HTTPError as exc:
            if allow_404 and exc.code == 404:
                return None
            if exc.code in allow_statuses:
                return {"_allowed_status": exc.code, "_body": exc.read().decode("utf-8", errors="ignore")}
            detail = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"{method} {url} failed {exc.code}: {detail}") from exc

    ref = request("GET", f"{api}/git/ref/heads/main", allow_404=True, allow_statuses={409})
    if not ref or ref.get("_allowed_status") == 409:
        readme = subprocess.check_output(["git", "show", "HEAD:README.md"])
        request(
            "PUT",
            f"{api}/contents/README.md",
            {
                "message": "Initialize repository",
                "content": base64.b64encode(readme).decode("ascii"),
                "branch": "main",
            },
            allow_statuses={422},
        )
        ref = request("GET", f"{api}/git/ref/heads/main", allow_404=True)

    files = subprocess.check_output(
        ["git", "ls-tree", "-r", "--name-only", "HEAD"],
        text=True,
    ).splitlines()
    print(f"Uploading {len(files)} files via GitHub API...", flush=True)

    tree_items = []
    for index, path in enumerate(files, 1):
        with open(path, "rb") as f:
            content = base64.b64encode(f.read()).decode("ascii")
        blob = request("POST", f"{api}/git/blobs", {"content": content, "encoding": "base64"})
        tree_items.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        if index % 10 == 0 or index == len(files):
            print(f"  {index}/{len(files)}", flush=True)

    parents = []
    base_tree = None
    if ref:
        parent_sha = ref["object"]["sha"]
        parents = [parent_sha]
        parent_commit = request("GET", f"{api}/git/commits/{parent_sha}")
        base_tree = parent_commit.get("tree", {}).get("sha")

    tree_payload = {"tree": tree_items}
    if base_tree:
        tree_payload["base_tree"] = base_tree
    tree = request("POST", f"{api}/git/trees", tree_payload)

    local_sha = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
    commit = request(
        "POST",
        f"{api}/git/commits",
        {
            "message": f"Sync OKIVIVI workflow ({local_sha})",
            "tree": tree["sha"],
            "parents": parents,
        },
    )

    if ref:
        request("PATCH", f"{api}/git/refs/heads/main", {"sha": commit["sha"], "force": True})
    else:
        request("POST", f"{api}/git/refs", {"ref": "refs/heads/main", "sha": commit["sha"]})

    print(f"Uploaded commit: {commit['sha']}")
    print(f"URL: https://github.com/{owner}/{repo}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
