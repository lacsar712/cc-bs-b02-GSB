import os
from datetime import datetime, timedelta, timezone
from math import isfinite

import jwt
from passlib.context import CryptContext
from sanic import Sanic
from sanic.response import json as sanic_json

from db import create_pool, ensure_schema, seed_if_empty

# 客户端请求体若携带风速字段一律拒绝：风速只能由服务端采集，防止前端伪造蒙混
FORBIDDEN_WIND_FIELDS = {"wind_ms", "wind", "wind_speed", "windSpeed"}
WIND_LIMIT_MS = 100.0

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


async def _fetch_gate_state(cur) -> dict | None:
    """读取联闸设置与服务端最新风速样本（最新样本由服务端采集，不采信前端）。"""
    await cur.execute(
        """
        SELECT s.enabled, s.threshold_ms, s.updated_by, s.updated_at,
               w.wind_ms, w.created_at AS wind_at, w.source AS wind_source
        FROM wind_gate_settings s
        LEFT JOIN LATERAL (
            SELECT wind_ms, created_at, source
            FROM wind_samples
            ORDER BY id DESC
            LIMIT 1
        ) w ON true
        WHERE s.id = 1
        """
    )
    return await cur.fetchone()


def _latest_wind(row) -> dict | None:
    if row is None or row["wind_ms"] is None:
        return None
    return {
        "wind_ms": row["wind_ms"],
        "recorded_at": _iso(row["wind_at"]),
        "source": row["wind_source"],
        "over_threshold": row["wind_ms"] > row["threshold_ms"],
    }


def _gate_open(row) -> bool:
    """联闸开关已开且服务端最新风速超过阈值 → 挡回（返回 True）。"""
    if not row or not row["enabled"]:
        return False
    if row["wind_ms"] is None:
        return False
    return row["wind_ms"] > row["threshold_ms"]


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
    if FORBIDDEN_WIND_FIELDS.intersection(body.keys()):
        return sanic_json(
            {"detail": "风速由服务端采集，禁止在上报中携带风速"}, status=400
        )
    span_code = str(body.get("span_code", "")).strip()
    if not span_code:
        return sanic_json({"detail": "跨段编号不能为空"}, status=400)
    try:
        microstrain = float(body.get("microstrain"))
    except (TypeError, ValueError):
        return sanic_json({"detail": "微应变必须是数字"}, status=400)
    if not isfinite(microstrain):
        return sanic_json({"detail": "微应变必须是有限数字"}, status=400)

    pool = request.app.ctx.pool
    async with pool.connection() as conn:
        # 整个判定 + 落库在同一事务内：要么读到超阈值风速后挡回并写流水，
        # 要么放行进队；事务随 return 正常退出而提交，异常则整体回滚。
        async with conn.transaction():
            async with conn.cursor() as cur:
                gate_row = await _fetch_gate_state(cur)
                if _gate_open(gate_row):
                    wind_ms = float(gate_row["wind_ms"])
                    threshold = float(gate_row["threshold_ms"])
                    await cur.execute(
                        """
                        INSERT INTO wind_gate_events
                            (event_type, wind_ms, threshold_ms, detail,
                             span_code, operator, created_at)
                        VALUES ('blocked', %s, %s, %s, %s, %s, now())
                        RETURNING id, created_at
                        """,
                        (
                            wind_ms,
                            threshold,
                            f"江面大风（{wind_ms:.1f} m/s ＞ 阈值 "
                            f"{threshold:.1f} m/s），联闸挡回读数上报：{span_code}",
                            span_code,
                            user["username"],
                        ),
                    )
                    blocked = await cur.fetchone()
                    return sanic_json(
                        {
                            "detail": (
                                f"江面大风，联闸已挡回上报"
                                f"（{wind_ms:.1f} m/s ＞ {threshold:.1f} m/s）。"
                                "关闭联闸或风力回落后再报。"
                            ),
                            "blocked": True,
                            "wind_ms": wind_ms,
                            "threshold_ms": threshold,
                            "event_id": blocked["id"],
                        },
                        status=409,
                    )

                await cur.execute(
                    """
                    INSERT INTO strain_readings
                        (span_code, microstrain, status, created_by, created_at)
                    VALUES (%s, %s, 'pending', %s, now())
                    RETURNING id, span_code, microstrain, verdict, reason, status,
                              created_by, created_at, processed_at
                    """,
                    (span_code, microstrain, user["username"]),
                )
                row = await cur.fetchone()

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


def _gate_payload(gate_row) -> dict:
    return {
        "enabled": gate_row["enabled"],
        "threshold_ms": gate_row["threshold_ms"],
        "updated_by": gate_row["updated_by"],
        "updated_at": _iso(gate_row["updated_at"]),
        "latest_wind": _latest_wind(gate_row),
        "gate_blocking": _gate_open(gate_row),
    }


@app.get("/api/wind-gate")
async def get_wind_gate(request):
    user = _require_user(request)
    if not user:
        return sanic_json({"detail": "未登录"}, status=401)
    pool = request.app.ctx.pool
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            gate_row = await _fetch_gate_state(cur)
    return sanic_json(_gate_payload(gate_row))


@app.put("/api/wind-gate")
async def update_wind_gate(request):
    user = _require_user(request)
    if not user:
        return sanic_json({"detail": "未登录"}, status=401)
    if user["role"] != "writer":
        return sanic_json(
            {"detail": "仅测量员可切换联闸开关或调整阈值，复核员只读"}, status=403
        )
    body = request.json or {}
    if "enabled" not in body and "threshold_ms" not in body:
        return sanic_json(
            {"detail": "请提供 enabled 或 threshold_ms"}, status=400
        )

    enabled = None
    if "enabled" in body:
        if not isinstance(body["enabled"], bool):
            return sanic_json({"detail": "enabled 必须是布尔值"}, status=400)
        enabled = body["enabled"]

    threshold = None
    if "threshold_ms" in body:
        try:
            threshold = float(body["threshold_ms"])
        except (TypeError, ValueError):
            return sanic_json({"detail": "阈值必须是数字（m/s）"}, status=400)
        if not isfinite(threshold) or threshold < 0 or threshold > WIND_LIMIT_MS:
            return sanic_json(
                {"detail": f"阈值须在 0～{WIND_LIMIT_MS:g} m/s 之间"}, status=400
            )

    pool = request.app.ctx.pool
    async with pool.connection() as conn:
        # 设置更新与联闸流水在同一事务内提交
        async with conn.transaction():
            async with conn.cursor() as cur:
                if enabled is not None and threshold is not None:
                    await cur.execute(
                        """
                        UPDATE wind_gate_settings
                        SET enabled = %s, threshold_ms = %s,
                            updated_by = %s, updated_at = now()
                        WHERE id = 1
                        RETURNING enabled, threshold_ms, updated_by, updated_at
                        """,
                        (enabled, threshold, user["username"]),
                    )
                elif enabled is not None:
                    await cur.execute(
                        """
                        UPDATE wind_gate_settings
                        SET enabled = %s, updated_by = %s, updated_at = now()
                        WHERE id = 1
                        RETURNING enabled, threshold_ms, updated_by, updated_at
                        """,
                        (enabled, user["username"]),
                    )
                else:
                    await cur.execute(
                        """
                        UPDATE wind_gate_settings
                        SET threshold_ms = %s, updated_by = %s, updated_at = now()
                        WHERE id = 1
                        RETURNING enabled, threshold_ms, updated_by, updated_at
                        """,
                        (threshold, user["username"]),
                    )
                settings = await cur.fetchone()

                events: list[tuple] = []
                if enabled is not None:
                    events.append(
                        (
                            "enabled" if enabled else "disabled",
                            None,
                            float(settings["threshold_ms"]),
                            f"联闸已{'开启' if enabled else '关闭'}"
                            + (f"（阈值 {threshold:.1f} m/s）" if threshold is not None else ""),
                            user["username"],
                        )
                    )
                if threshold is not None:
                    events.append(
                        (
                            "threshold_changed",
                            None,
                            threshold,
                            f"风速阈值调整为 {threshold:.1f} m/s"
                            + ("（本次同时开启联闸）" if enabled is True else ""),
                            user["username"],
                        )
                    )
                for etype, wind_ms, thr, detail, operator in events:
                    await cur.execute(
                        """
                        INSERT INTO wind_gate_events
                            (event_type, wind_ms, threshold_ms, detail, operator, created_at)
                        VALUES (%s, %s, %s, %s, %s, now())
                        """,
                        (etype, wind_ms, thr, detail, operator),
                    )

                gate_row = await _fetch_gate_state(cur)
    return sanic_json(_gate_payload(gate_row))


@app.get("/api/wind-gate/events")
async def list_wind_gate_events(request):
    user = _require_user(request)
    if not user:
        return sanic_json({"detail": "未登录"}, status=401)
    pool = request.app.ctx.pool
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT id, event_type, wind_ms, threshold_ms, detail,
                       reading_id, span_code, operator, created_at
                FROM wind_gate_events
                ORDER BY id DESC
                LIMIT 200
                """
            )
            rows = await cur.fetchall()
    return sanic_json(
        [
            {
                "id": r["id"],
                "event_type": r["event_type"],
                "wind_ms": r["wind_ms"],
                "threshold_ms": r["threshold_ms"],
                "detail": r["detail"],
                "reading_id": r["reading_id"],
                "span_code": r["span_code"],
                "operator": r["operator"],
                "created_at": _iso(r["created_at"]),
            }
            for r in rows
        ]
    )


@app.post("/api/wind-gate/samples")
async def ingest_wind_sample(request):
    """服务端风速采集入口（岸基风速仪 / 服务端模拟）。

    仅测量员可写入用于演练；复核员只读。该接口代表“服务端提供”的采样，
    读数上报接口本身绝不接受请求体里的风速字段。
    """
    user = _require_user(request)
    if not user:
        return sanic_json({"detail": "未登录"}, status=401)
    if user["role"] != "writer":
        return sanic_json(
            {"detail": "复核员只读，不可录入风速采样"}, status=403
        )
    body = request.json or {}
    try:
        wind_ms = float(body.get("wind_ms"))
    except (TypeError, ValueError):
        return sanic_json({"detail": "wind_ms 必须是数字（m/s）"}, status=400)
    if not isfinite(wind_ms) or wind_ms < 0 or wind_ms > WIND_LIMIT_MS:
        return sanic_json(
            {"detail": f"风速须在 0～{WIND_LIMIT_MS:g} m/s 之间"}, status=400
        )
    source = str(body.get("source", "server-anemometer")).strip() or "server-anemometer"

    pool = request.app.ctx.pool
    async with pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO wind_samples (wind_ms, source, created_by, created_at)
                    VALUES (%s, %s, %s, now())
                    RETURNING id, wind_ms, source, created_at
                    """,
                    (wind_ms, source, user["username"]),
                )
                sample = await cur.fetchone()
                gate_row = await _fetch_gate_state(cur)
    blocking = _gate_open(gate_row)
    over = float(sample["wind_ms"]) > float(gate_row["threshold_ms"])
    return sanic_json(
        {
            "id": sample["id"],
            "wind_ms": sample["wind_ms"],
            "source": sample["source"],
            "recorded_at": _iso(sample["created_at"]),
            "gate_enabled": gate_row["enabled"],
            "threshold_ms": gate_row["threshold_ms"],
            "over_threshold": over,
            "gate_blocking": blocking,
            "message": (
                "当前风速超过阈值，联闸挡回中"
                if blocking
                else ("当前风速超过阈值，但联闸未开启" if over else "风速样本已由服务端记录")
            ),
        },
        status=201,
    )
