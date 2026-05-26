# OKIVIVI Docker 部署

这个部署方式只启动一个飞书长连接 worker，不暴露任何端口，不会占用宿主机已有的 Web 服务端口。

## 1. 拉取代码

```bash
mkdir -p ~/apps
cd ~/apps
git clone https://github.com/fei-gpt/jimeng-vivi.git okivivi
cd okivivi
```

## 2. 准备本地配置和数据

```bash
cp .env.example .env
mkdir -p accounts tenants tasks/{pending,reviewing,running,done,failed} outputs logs script_requests prompts vivi-image
touch users.json bitable_state.json
```

把本地正在使用的这些文件/目录同步到服务器同名位置：

- `.env`
- `users.json`
- `bitable_state.json`
- `vivi-image/`
- `deepseek/` 下的文案规则文件

注意：`.env`、`users.json`、账号登录态和输出文件不会上传到 GitHub。

## 3. 启动

```bash
docker compose up -d --build
docker compose logs -f okivivi-worker
```

第一次启动时，容器会自动安装即梦 CLI。

## 4. 登录/切换即梦账号

如果需要在容器里手动登录即梦：

```bash
docker compose exec okivivi-worker bash
dreamina login --headless
```

按输出链接完成授权后，再通过机器人账号管理保存账号。

## 5. 常用命令

```bash
docker compose ps
docker compose logs -f okivivi-worker
docker compose restart okivivi-worker
docker compose down
```

`docker compose down` 只停止 OKIVIVI 这个 compose 项目，不会停止服务器上其他 Docker 应用。
