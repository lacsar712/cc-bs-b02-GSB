# 桥梁应变班交台

测量员上报跨段编号与微应变读数，后台工人用 `FOR UPDATE SKIP LOCKED` 认领待处理队列，按 **80～220 με** 判定 **合格** 或 **越界**。

江面大风时可**联动关闭报送（风况联闸）**：测量员在「风况联闸」专页开启联闸并设定风速阈值；服务端在**超阈值期间一律挡回**读数上报，**关闭联闸或风力回落后立刻恢复**。挡回动作与联闸流水在**同一数据库事务**内提交。


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

## 风况联闸

| 接口 | 方法 | 权限 | 说明 |
|------|------|------|------|
| `/api/wind-gate` | GET | 已登录 | 读取开关、阈值、服务端最新风速、当前是否挡回 |
| `/api/wind-gate` | PUT | 测量员 | 切换 `enabled` 与/或 `threshold_ms`，写入流水（同事务） |
| `/api/wind-gate/events` | GET | 已登录 | 联闸流水（开启/关闭/调阈值/挡回），复核员只读 |
| `/api/wind-gate/samples` | POST | 测量员 | **服务端**风速采集（岸基风速仪／演练），写入 `wind_samples` |

规则要点：

- 风速只能由服务端提供：`POST /api/readings` 请求体若携带 `wind_ms`/`windSpeed` 等风速字段一律 `400` 拒绝，前端无法伪造风速蒙混。
- 联闸开启且服务端最新风速 **＞** 阈值时，上报在**同一事务**内被挡回（`409`）并写一条 `blocked` 流水；读数不入库。关闭联闸或风速回落到阈值内立即恢复（`201`）。
- 复核员可只读阈值、开关状态与流水，不能切换开关、调阈值或录入风速（写操作 `403`）。

页面入口：登录后顶栏「风况联闸」胶囊（挡回中红色脉冲）→ 风况联闸专页（开关、阈值、服务端风速录入/模拟、联闸流水）。

演练：阈值调到极低（如 `0.1`）→ 录入/模拟超阈值风速（如 `8.5 m/s`）→ 再报应 **409 失败**且流水多一条「挡回上报」→ 关闭联闸后再报应 **201 成功**。


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
