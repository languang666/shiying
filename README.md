# 拾影 Shiying

多平台公开作品解析与下载网站。粘贴单条作品的分享链接，即可解析并下载其中的视频、图片或混合轮播；支持逐项下载，图集可打包成 ZIP 流式下载。

支持平台：抖音、TikTok、YouTube、Instagram、X / Twitter、微博、Facebook、Vimeo、Dailymotion。

> 平台页面、风控和媒体地址随时可能变化，公开样本测试通过不代表每条链接都可用。私密、已删除、需要登录或受 DRM 保护的作品无法下载。请只下载你有权保存的内容，并遵守各平台的服务条款与当地法律。

## 特性

- **单帖多媒体**：Instagram 轮播与 X 多视频推文可以逐项下载；X 链接带 `/video/N` 时只取第 N 个视频。
- **可恢复任务**：任务状态保存在 Redis，服务重启后会重新调度未完成的任务。
- **磁盘配额**：本地暂存有总配额和最小剩余空间限制，空间不够时先回收已完成且没人在读的缓存。
- **流式 ZIP**：图集与多视频作品均可一键打包下载（`/api/download/{job}/zip?kind=image|video`）；边读边打包，不在内存里拼出整个合集。
- **YouTube 稳定性**：上游请求串行并限速，相同视频共用元数据缓存；客户端会按顺序回退（默认 → mweb + PO Token → android_vr）；遇到 429 后进入冷却，冷却时间指数增长，最长 900 秒。
- **安全默认值**：所有出站请求都检查目标地址，拒绝访问内网；媒体域名有白名单；容器以非 root、只读根文件系统运行；日志里隐藏 URL 和凭据。
- **匿名会话**：访客用签名 Cookie 区分各自的任务，不需要注册。

## 架构

```
浏览器 ──HTTPS──> 反向代理 (Caddy / nginx)
                     │
                     ▼
              site (FastAPI, 单进程) ──> Redis（任务状态、租约、统计）
                │          │
                │          └─> media_worker.py / post_media.py（yt-dlp 子进程）
                │                 └─> youtube-pot（PO Token 服务，仅内部网络）
                ▼
   Douyin_TikTok_Download_API 后端（抖音 / TikTok）
```

- `app/app.py`：网站接口、按平台分流、下载响应。
- `app/runtime.py`：单实例 Redis 租约、可恢复的任务调度、存储配额、事件日志与统计。
- `app/state_store.py`：按记录持久化 Redis 状态。
- `app/media_worker.py`、`app/post_media.py`：基于 yt-dlp 的多平台媒体提取；Instagram 与 X 走单帖多媒体流程。
- `app/archive_stream.py`：带缓冲上限的 ZIP 流式输出。
- `app/youtube_control.py`：YouTube 请求间隔、冷却与并发控制。
- `app/static/`：带内容指纹的前端 CSS / JS。

抖音和 TikTok 依赖一个独立部署的 [Douyin_TikTok_Download_API](https://github.com/Evil0ctal/Douyin_TikTok_Download_API)（5.x）后端，网站通过 `BACKEND_URL` 和 `BACKEND_KEY` 调用它。不部署这个后端时，其他平台仍然可用。

## 部署

需要 Docker 与 Docker Compose v2。

```sh
git clone https://github.com/languang666/shiying.git
cd shiying
cp credentials/app.env.example credentials/app.env
chmod 600 credentials/app.env
# 编辑 credentials/app.env：填写 BACKEND_KEY 和随机生成的 SESSION_SECRET
mkdir -p data && sudo chown 10001:10001 data

# 构建镜像，并跑依赖检查和回归测试
python3 build-image.py            # 加上 --youtube-smoke 会额外实际下载一条公开 YouTube 视频做验证
docker compose up -d --wait site
```

网站默认只监听 `127.0.0.1:8080`（可以用 `SITE_PORT` 修改）。公网访问请在前面放一个负责 HTTPS 的反向代理，例如 Caddy：

```
parse.example.com {
    reverse_proxy 127.0.0.1:8080 {
        flush_interval -1
    }
}
```

如果抖音 / TikTok 后端和网站不在同一个 Compose 网络里，用环境变量 `BACKEND_URL` 指向它，例如 `BACKEND_URL=http://10.0.0.2:8000 docker compose up -d site`。

## 配置

| 变量 | 默认值 | 说明 |
|---|---|---|
| `BACKEND_URL` | `http://api:8000` | 抖音 / TikTok 后端地址 |
| `BACKEND_KEY` | （必填） | 后端 API Key |
| `SESSION_SECRET` | （必填） | 访客 Cookie 签名密钥 |
| `SITE_PARSE_CONCURRENCY` | `2` | 同时进行的解析任务数 |
| `SITE_DOWNLOAD_CONCURRENCY` | `2` | 同时进行的下载任务数 |
| `SITE_MEDIA_ITEM_WORKERS` | `4` | 一个图集内同时下载的条目数 |
| `SITE_DISK_QUOTA_BYTES` | 12 GiB | 本地暂存总配额 |
| `SITE_DISK_MIN_FREE_BYTES` | 1 GiB | 文件系统至少保留的空闲空间 |
| `SITE_JOB_RESERVE_BYTES` | 4 GiB | 每个运行中下载预留的空间（包括音视频合并用的临时空间） |
| `SITE_YOUTUBE_INTERVAL` | `5` | 两次 YouTube 提取请求之间的最短间隔（秒） |
| `SITE_QUEUE_TIMEOUT` | `300` | 排队等待上限（秒） |
| `SITE_YOUTUBE_POT_URL` | — | PO Token 服务地址 |

固定限制：单次下载最大 2 GiB，视频最长 60 分钟。任务文件在任务完成 1 小时后清理。

## 运行约束

- 网站只能运行一个 Web 进程：Redis 租约会阻止第二个进程管理同一个命名空间，**不要增加 Uvicorn workers**。
- 停止容器时，仍在执行的任务会被标记为可恢复；进程异常退出后，运行租约最多约 30 秒过期。
- 升级 yt-dlp 时，请成对更新 `yt-dlp` / `yt-dlp-ejs`，以及 PO Token 插件 / 服务（`bgutil-ytdlp-pot-provider`），然后重新执行 `build-image.py`。

## 运维

- `GET /healthz` 检查进程是否就绪以及 Redis 是否连通。
- 容器日志输出结构化事件（平台、任务、阶段、错误类别、耗时），日志里会隐藏 URL 和凭据。
- Redis 键 `media-site:v2:metrics:<platform>:<action>` 记录各平台的任务次数、结果、耗时分桶和错误类别，不通过公开接口暴露。

## 测试

```sh
docker compose up -d --wait redis
docker compose run --rm --no-deps -T --entrypoint python site - < tests/test_runtime.py
docker compose run --rm --no-deps -T --entrypoint python site - < tests/test_youtube.py
```

`test_runtime.py` 验证租约互斥、容量回收与读保护、任务恢复、ZIP CRC、流式缓冲和日志脱敏。它使用随机的 Redis 键前缀，结束时会删除。

## 致谢

- [yt-dlp](https://github.com/yt-dlp/yt-dlp)
- [Douyin_TikTok_Download_API](https://github.com/Evil0ctal/Douyin_TikTok_Download_API)
- [bgutil-ytdlp-pot-provider](https://github.com/Brainicism/bgutil-ytdlp-pot-provider)

## 许可证

[Apache License 2.0](LICENSE)
