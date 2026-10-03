# 桥梁应变班交台

测量员上报跨段编号与微应变读数，后台工人用 `FOR UPDATE SKIP LOCKED` 认领待处理队列，按 **80～220 με** 判定 **合格** 或 **越界**。

## 风况联闸

江面大风时可联动关闭报送。顶栏「风况联闸」专页提供风速阈值与联闸开关，并展示联闸流水：

- 测量员打开联闸并设定阈值后，服务端在**风速超阈值期间一律挡回**报送（HTTP 409），关掉联闸立刻恢复；
- 挡回与流水写入在**同一次事务**中提交，不会出现挡回无记录；
- 风速由**服务端采样**（`backend/wind.py`），请求体夹带的 `wind_speed` 一律忽略，前端无法伪造风速蒙混；
- 复核员只读阈值与联闸记录，调整开关返回 403；
- 演练模拟大风：给接口进程设环境变量 `WIND_SPEED_MS=25`（固定风速），或不设时按 1.5～8.5 m/s 日常江面风速采样，把阈值调到极低即可模拟超阈值。

接口：`GET /api/wind-gate`、`PUT /api/wind-gate`（仅测量员）、`GET /api/wind-gate/events`。

## 技术栈

| 层 | 选型 |
|----|------|
| 接口 | Python Sanic + psycopg（异步连接池） |
| 工人 | `worker.py`（psycopg 同步，`FOR UPDATE SKIP LOCKED`） |
| 页面 | Mithril.js + Vite，nginx 反代 `/api` |
| 数据库 | PostgreSQL 16 |

## 端口

| 服务 | 地址 |
|------|------|
| 页面 | http://localhost:3198 |
| 接口 | http://localhost:8198 |
| PostgreSQL | localhost:54398（库名 `bridgestrain`） |

## 账号

| 用户 | 密码 | 权限 |
|------|------|------|
| surveyor | surv123456 | 测量员，可提交读数 |
| reviewer | rev123456 | 复核员，只读列表 |

## 启动

```bash
cd projects/19-bridge-strain-shift
docker compose up --build
```

健康检查：`GET http://localhost:8198/api/health` → `{"status":"ok","service":"bridge-strain-shift"}`

## 种子数据

| 跨段 | 微应变 | 结论 |
|------|--------|------|
| 跨中S1 | 150 με | 合格 |
| 支座S2 | 40 με | 越界 |

## 本地开发（可选）

```bash
cd backend && pip install -r requirements.txt
python -m sanic api.app --host=0.0.0.0 --port=8000 --single-process
python worker.py
cd frontend && npm install && npm run dev
```

接口进程默认监听容器内 **8000**，对外映射 **8198**。
