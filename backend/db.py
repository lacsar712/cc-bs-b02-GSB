import os

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from rules import judge_microstrain

DSN = os.environ.get(
    "DATABASE_URL", "postgresql://app:app@localhost:54398/bridgestrain"
)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS strain_readings (
    id serial PRIMARY KEY,
    span_code text NOT NULL,
    microstrain double precision NOT NULL,
    verdict text,
    reason text,
    status text NOT NULL DEFAULT 'pending',
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    processed_at timestamptz
);
CREATE INDEX IF NOT EXISTS idx_strain_readings_status ON strain_readings (status, id);

-- 风况联闸：单行设置（id 恒为 1），测量员可切换开关与阈值
CREATE TABLE IF NOT EXISTS wind_gate_settings (
    id integer PRIMARY KEY DEFAULT 1,
    enabled boolean NOT NULL DEFAULT false,
    threshold_ms double precision NOT NULL DEFAULT 13.8,
    updated_by text,
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT wind_gate_settings_singleton CHECK (id = 1)
);

-- 风速采样：只能由服务端写入（岸基风速仪/服务端模拟接口），
-- 上报接口绝不采信请求体中的风速，前端无法伪造风速蒙混过关。
CREATE TABLE IF NOT EXISTS wind_samples (
    id bigserial PRIMARY KEY,
    wind_ms double precision NOT NULL,
    source text NOT NULL DEFAULT 'server-anemometer',
    created_by text,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_wind_samples_latest ON wind_samples (id DESC);

-- 联闸流水：开关/阈值变更与挡回均落表；挡回与流水在同一事务内提交
CREATE TABLE IF NOT EXISTS wind_gate_events (
    id bigserial PRIMARY KEY,
    event_type text NOT NULL,
    wind_ms double precision,
    threshold_ms double precision,
    detail text,
    reading_id integer,
    span_code text,
    operator text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_wind_gate_events_latest ON wind_gate_events (id DESC);
"""

SETTINGS_SEED_SQL = """
INSERT INTO wind_gate_settings (id, enabled, threshold_ms, updated_by, updated_at)
VALUES (1, false, 13.8, NULL, now())
ON CONFLICT (id) DO NOTHING
"""


async def create_pool() -> AsyncConnectionPool:
    pool = AsyncConnectionPool(
        conninfo=DSN,
        min_size=1,
        max_size=5,
        kwargs={"row_factory": dict_row},
        open=False,
    )
    await pool.open()
    return pool


async def ensure_schema(pool: AsyncConnectionPool) -> None:
    async with pool.connection() as conn:
        await conn.execute(SCHEMA_SQL)
        await conn.execute(SETTINGS_SEED_SQL)
        await conn.commit()


async def seed_if_empty(pool: AsyncConnectionPool) -> None:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT COUNT(*) AS n FROM strain_readings")
            row = await cur.fetchone()
            if row["n"] > 0:
                return
            samples = [
                ("跨中S1", 150.0),
                ("支座S2", 40.0),
            ]
            for span_code, microstrain in samples:
                verdict, reason = judge_microstrain(microstrain)
                await cur.execute(
                    """
                    INSERT INTO strain_readings
                        (span_code, microstrain, verdict, reason, status, created_by, processed_at)
                    VALUES (%s, %s, %s, %s, 'done', 'surveyor', now())
                    """,
                    (span_code, microstrain, verdict, reason),
                )
        await conn.commit()


def connect_sync():
    import psycopg

    return psycopg.connect(DSN, row_factory=dict_row)


def ensure_schema_sync(conn) -> None:
    conn.execute(SCHEMA_SQL)
    conn.execute(SETTINGS_SEED_SQL)


def seed_if_empty_sync(conn) -> None:
    row = conn.execute("SELECT COUNT(*) AS n FROM strain_readings").fetchone()
    if row["n"] > 0:
        return
    samples = [
        ("跨中S1", 150.0),
        ("支座S2", 40.0),
    ]
    for span_code, microstrain in samples:
        verdict, reason = judge_microstrain(microstrain)
        conn.execute(
            """
            INSERT INTO strain_readings
                (span_code, microstrain, verdict, reason, status, created_by, processed_at)
            VALUES (%s, %s, %s, %s, 'done', 'surveyor', now())
            """,
            (span_code, microstrain, verdict, reason),
        )
    conn.commit()
