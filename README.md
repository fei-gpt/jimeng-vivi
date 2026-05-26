# OKIVIVI Jimeng Video Worker

本项目把 OKIVIVI 的视频生成流程固化为一套可部署服务：

```text
飞书机器人菜单 -> DeepSeek 生成/人工输入文案 -> 飞书多维表格审核 -> 即梦 CLI 生成视频 -> 飞书通知结果
```

核心能力：

- 飞书机器人长连接，无需公网 callback URL
- 新用户一键初始化个人工作区
- 文案库和视频审核表分离
- 支持 DeepSeek 自动生成分镜脚本
- 支持手动文案输入
- 支持多即梦账号保存、切换和队列调度
- 支持按用户写入 `tenant_id`、`owner_open_id`、`jimeng_account`
- 支持即梦多图参考 `multimodal2video`

## 目录结构

```text
worker/
  feishu_worker.py          # 飞书长连接 worker、审核、队列、即梦调用
  generate_scripts.py       # DeepSeek 文案生成
  create_task.py            # 本地任务创建
  prompt_ui_server.py       # 本地简易 UI，可选
deepseek/
  OKIVIVI-text-zong.md      # 主文案规则
  OKIVIVI-text-assistive.md # 辅助文案规则
vivi-image/
  okivivi-blue.jpg
  okivivi-blue1.jpg
  okivivi-pink.jpg
  okivivi-pink1.jpg
tasks/
outputs/
logs/
script_requests/
```

运行时会生成 `.env`、`users.json`、`bitable_state.json`、`accounts/`、`tenants/`、`logs/`、`outputs/` 等本机状态文件，这些不会提交到 Git。

## 服务器部署

推荐使用 Ubuntu 22.04。

```bash
git clone https://github.com/fei-gpt/jimeng-vivi.git
cd jimeng-vivi

sudo apt update
sudo apt install -y python3.10-venv python3-pip curl

python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
```

安装即梦 CLI：

```bash
curl -s https://jimeng.jianying.com/cli | bash
```

如果安装后当前 shell 找不到 `dreamina`，执行：

```bash
export PATH="$HOME/.local/bin:$PATH"
```

## 配置 `.env`

至少填写：

```text
FEISHU_APP_ID=
FEISHU_APP_SECRET=
FEISHU_ENCRYPT_KEY=
FEISHU_VERIFICATION_TOKEN=

FEISHU_BOT_OPEN_ID=
FEISHU_OPENAPI_BASE=https://open.feishu.cn

DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat

DEFAULT_MODEL=seedance2.0fast_vip
DEFAULT_RATIO=9:16
DEFAULT_DURATION=15
DEFAULT_RESOLUTION=720p
POLL_SECONDS=300

IMAGE_LIBRARY_DIR=/home/vivi/okivivi/vivi-image
DEFAULT_IMAGE_VARIANT=blue
DEFAULT_IMAGE_COUNT=2
```

飞书开放平台需要启用：

- 机器人能力
- 长连接事件订阅
- `im.message.receive_v1`
- `card.action.trigger`
- 发送消息权限
- 多维表格、云文档相关读写权限

## 启动 worker

开发调试：

```bash
. .venv/bin/activate
python3 -u worker/feishu_worker.py
```

后台启动：

```bash
. .venv/bin/activate
mkdir -p logs
setsid -f python3 -u worker/feishu_worker.py > logs/feishu_worker_bg.log 2>&1
```

查看日志：

```bash
tail -f logs/feishu_worker_bg.log
```

## 飞书机器人使用

用户只需要输入数字：

```text
1  文案生成
2  文案输入
3  动画生成
4  账号管理
```

新用户首次使用时，机器人会发送“初始化我的工作区”按钮。点击后会自动创建并绑定：

- 个人文案库
- 个人视频审核表
- 个人任务身份配置

用户不需要手动发送 app token、table id 或长命令。

## 即梦账号管理

输入 `4` 后通过按钮操作：

- `1 账号列表`
- `2 当前账号`
- `3 新增账号`
- `4 保存刚授权账号`
- `5 切换账号`

新增账号会自动生成账号名并返回授权链接。授权完成后点击“保存刚授权账号”。

## 文案和图片规则

当前图片库只使用：

- `okivivi-blue.jpg`
- `okivivi-blue1.jpg`
- `okivivi-pink.jpg`
- `okivivi-pink1.jpg`

图片匹配规则在 worker 中执行：

- `blue` 使用 `okivivi-blue.jpg` + `okivivi-blue1.jpg`
- `pink` 使用 `okivivi-pink.jpg` + `okivivi-pink1.jpg`
- `all` 同时使用四张图片
- `animation*` 图片不会用于当前文案流程

## 生成流程

1. 用户通过机器人生成或输入文案
2. 文案写入“文案库”
3. 用户在文案库中选择图片、模型，并把“确认”列设为“确认”
4. worker 将记录导入视频审核表
5. 用户在视频审核表中确认
6. worker 调用 `dreamina multimodal2video`
7. 成功后写入视频链接，失败后写入状态和原因

默认即梦参数：

```text
model_version=seedance2.0fast_vip
ratio=9:16
duration=15
video_resolution=720p
poll=300
```

## 注意事项

- `.env`、`users.json`、`accounts/`、`tenants/` 不要提交到 Git。
- 服务器迁移时，需要重新配置飞书应用密钥、DeepSeek API Key 和即梦账号授权。
- 普通 `seedance2.0`、`seedance2.0fast` 走单并发；VIP 模型支持并发提交。
