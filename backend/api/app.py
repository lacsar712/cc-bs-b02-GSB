import math
import os
from datetime import datetime, timedelta, timezone

import jwt
from passlib.context import CryptContext
from sanic import Sanic
from sanic.response import json as sanic_json

from db import create_pool, ensure_schema, seed_if_empty
from wind import sample_wind_speed

SECRET = os.environ.get("JWT_SECRET", "bridge-strain-dev-secret")
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

USERS = {
    "surveyor": {"role": "writer", "password_hash": pwd.hash("surv123456")},
    "reviewer": {"role": "reader", "password_hash": pwd.hash("rev123456")},
}

app = Sanic("bridge-strain-shift")


def _auth_header(request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return None


def _decode_user(token: str | None) -> dict | None:
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET, algorithms=["HS256"])
    except jwt.InvalidTokenError:
        return None
    sub = payload.get("sub")
    if sub not in USERS:
        return None
    return {"username": sub, "role": payload.get("role")}


def _require_user(request) -> dict:
    user = _decode_user(_auth_header(request))
    if not user:
        return None
    return user


def _iso(dt) -> str | None:
    if dt is None:
        return None
    return dt.isoformat()


def _gate_dict(row: dict, wind_speed: float | None = None) -> dict:
    return {
        "enabled": row["enabled"],
        "threshold_ms": row["threshold_ms"],
        "updated_by": row["updated_by"],
        "updated_at": _iso(row["updated_at"]),
        "wind_speed": wind_speed,
    }


@app.before_server_start
async def setup(_app, _loop):
    pool = await create_pool()
    _app.ctx.pool = pool
    await ensure_schema(pool)
    await seed_if_empty(pool)


@app.after_server_stop
async def teardown(_app, _loop):
    pool = _app.ctx.pool
    if pool:
        await pool.close()


@app.get("/api/health")
async def health(_request):
    return sanic_json({"status": "ok", "service": "bridge-strain-shift"})


@app.post("/api/auth/login")
async def login(request):
    body = request.json or {}
    username = str(body.get("username", "")).strip()
    password = str(body.get("password", ""))
    user = USERS.get(username)
    if not user or not pwd.verify(password, user["password_hash"]):
        return sanic_json({"detail": "用户名或密码错误"}, status=401)
    exp = datetime.now(timezone.utc) + timedelta(hours=8)
    token = jwt.encode(
        {"sub": username, "role": user["role"], "exp": exp},
        SECRET,
        algorithm="HS256",
    )
    return sanic_json(
        {"access_token": token, "username": username, "role": user["role"]}
    )


@app.get("/api/wind-gate")
async def get_wind_gate(request):
    if not _require_user(request):
        return sanic_json({"detail": "未登录"}, status=401)
    pool = request.app.ctx.pool
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT enabled, threshold_ms, updated_by, updated_at
                FROM wind_gate_settings
                WHERE id = 1
                """
            )
            row = await cur.fetchone()
    return sanic_json(_gate_dict(row, sample_wind_speed()))


@app.put("/api/wind-gate")
async def update_wind_gate(request):
    user = _require_user(request)
    if not user:
        return sanic_json({"detail": "未登录"}, status=401)
    if user["role"] != "writer":
        return sanic_json({"detail": "复核员只读，仅测量员可调整风况联闸"}, status=403)
    body = request.json or {}
    enabled = body.get("enabled")
    if not isinstance(enabled, bool):
        return sanic_json({"detail": "enabled 必须是布尔值"}, status=400)
    try:
        threshold = float(body.get("threshold_ms"))
    except (TypeError, ValueError):
        return sanic_json({"detail": "风速阈值必须是数字"}, status=400)
    if not math.isfinite(threshold) or not 0 < threshold <= 100:
        return sanic_json({"detail": "风速阈值需在 0～100 m/s 之间"}, status=400)

    pool = request.app.ctx.pool
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT enabled, threshold_ms FROM wind_gate_settings WHERE id = 1 FOR UPDATE"
            )
            prev = await cur.fetchone()
            await cur.execute(
                """
                UPDATE wind_gate_settings
                SET enabled = %s, threshold_ms = %s, updated_by = %s, updated_at = now()
                WHERE id = 1
                """,
                (enabled, threshold, user["username"]),
            )
            # 设置变更与流水写入同一次事务
            if enabled != prev["enabled"]:
                if enabled:
                    event = "open"
                    detail = f"打开联闸，阈值 {threshold} m/s，超阈值期间报送一律挡回"
                else:
                    event = "close"
                    detail = "关闭联闸，报送立即恢复"
                await cur.execute(
                    """
                    INSERT INTO wind_gate_events
                        (event_type, wind_speed, threshold_ms, actor, detail)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (event, sample_wind_speed(), threshold, user["username"], detail),
                )
            if threshold != prev["threshold_ms"]:
                await cur.execute(
                    """
                    INSERT INTO wind_gate_events
                        (event_type, wind_speed, threshold_ms, actor, detail)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        "threshold",
                        sample_wind_speed(),
                        threshold,
                        user["username"],
                        f"阈值由 {prev['threshold_ms']} 调整为 {threshold} m/s",
                    ),
                )
            await cur.execute(
                """
                SELECT enabled, threshold_ms, updated_by, updated_at
                FROM wind_gate_settings
                WHERE id = 1
                """
            )
            row = await cur.fetchone()
        await conn.commit()
    return sanic_json(_gate_dict(row, sample_wind_speed()))


@app.get("/api/wind-gate/events")
async def list_wind_gate_events(request):
    if not _require_user(request):
        return sanic_json({"detail": "未登录"}, status=401)
    pool = request.app.ctx.pool
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT id, event_type, wind_speed, threshold_ms, actor, detail, created_at
                FROM wind_gate_events
                ORDER BY id DESC
                LIMIT 50
                """
            )
            rows = await cur.fetchall()
    out = []
    for r in rows:
        out.append(
            {
                "id": r["id"],
                "event_type": r["event_type"],
                "wind_speed": r["wind_speed"],
                "threshold_ms": r["threshold_ms"],
                "actor": r["actor"],
                "detail": r["detail"],
                "created_at": _iso(r["created_at"]),
            }
        )
    return sanic_json(out)


@app.get("/api/readings")
async def list_readings(request):
    if not _require_user(request):
        return sanic_json({"detail": "未登录"}, status=401)
    pool = request.app.ctx.pool
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT id, span_code, microstrain, verdict, reason, status,
                       created_by, created_at, processed_at
                FROM strain_readings
                ORDER BY id DESC
                """
            )
            rows = await cur.fetchall()
    out = []
    for r in rows:
        out.append(
            {
                "id": r["id"],
                "span_code": r["span_code"],
                "microstrain": r["microstrain"],
                "verdict": r["verdict"],
                "reason": r["reason"],
                "status": r["status"],
                "created_by": r["created_by"],
                "created_at": _iso(r["created_at"]),
                "processed_at": _iso(r["processed_at"]),
            }
        )
    return sanic_json(out)


@app.post("/api/readings")
async def create_reading(request):
    user = _require_user(request)
    if not user:
        return sanic_json({"detail": "未登录"}, status=401)
    if user["role"] != "writer":
        return sanic_json({"detail": "仅测量员可提交应变读数"}, status=403)
    body = request.json or {}
    span_code = str(body.get("span_code", "")).strip()
    if not span_code:
        return sanic_json({"detail": "跨段编号不能为空"}, status=400)
    try:
        microstrain = float(body.get("microstrain"))
    except (TypeError, ValueError):
        return sanic_json({"detail": "微应变必须是数字"}, status=400)

    pool = request.app.ctx.pool
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT enabled, threshold_ms FROM wind_gate_settings WHERE id = 1 FOR UPDATE"
            )
            gate = await cur.fetchone()
            if gate and gate["enabled"]:
                # 风速只以服务端采样为准，请求体夹带的 wind_speed 一律忽略
                wind = sample_wind_speed()
                if wind > gate["threshold_ms"]:
                    # 挡回与流水同一次事务：先写流水再提交，随后才返回挡回响应
                    await cur.execute(
                        """
                        INSERT INTO wind_gate_events
                            (event_type, wind_speed, threshold_ms, actor, detail)
                        VALUES ('blocked', %s, %s, %s, %s)
                        """,
                        (
                            wind,
                            gate["threshold_ms"],
                            user["username"],
                            f"跨段 {span_code} 读数 {microstrain} με 报送被风况联闸挡回",
                        ),
                    )
                    await conn.commit()
                    return sanic_json(
                        {
                            "detail": (
                                f"风况联闸生效：当前风速 {wind} m/s 超过阈值 "
                                f"{gate['threshold_ms']} m/s，本次报送已挡回"
                            ),
                            "blocked": True,
                            "wind_speed": wind,
                            "threshold_ms": gate["threshold_ms"],
                        },
                        status=409,
                    )
            await cur.execute(
                """
                INSERT INTO strain_readings (span_code, microstrain, status, created_by, created_at)
                VALUES (%s, %s, 'pending', %s, now())
                RETURNING id, span_code, microstrain, verdict, reason, status,
                          created_by, created_at, processed_at
                """,
                (span_code, microstrain, user["username"]),
            )
            row = await cur.fetchone()
        await conn.commit()

    return sanic_json(
        {
            "id": row["id"],
            "span_code": row["span_code"],
            "microstrain": row["microstrain"],
            "verdict": row["verdict"],
            "reason": row["reason"],
            "status": row["status"],
            "created_by": row["created_by"],
            "created_at": _iso(row["created_at"]),
            "processed_at": None,
            "message": "已入队，后台工人将认领并判定",
        },
        status=201,
    )
