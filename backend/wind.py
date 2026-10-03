"""江面风速采样：完全由服务端产生，前端不得也不需提交风速。

报送接口一律以本模块采样值判定是否触发风况联闸，
请求体中即便夹带 wind_speed 字段也会被忽略，前端无法伪造风速蒙混。
"""

import math
import os
import random
import time


def sample_wind_speed() -> float:
    """返回当前江面风速（m/s）。

    设定了环境变量 WIND_SPEED_MS 时返回该固定值，用于演练时模拟大风；
    否则按时间正弦叠加随机抖动，生成约 1.5～8.5 m/s 的日常江面风速。
    """
    override = os.environ.get("WIND_SPEED_MS")
    if override is not None:
        try:
            return round(float(override), 1)
        except ValueError:
            pass
    base = 5.0 + 2.5 * math.sin(time.time() / 30.0)
    return round(max(0.0, base + random.uniform(-1.0, 1.0)), 1)
