"""飞书自定义机器人推送（带签名校验）。

结果和「刷课」两件事分开写清楚：刷课失败不影响整体结论的判定口径，
跟原文里「猫猫那部分失败不要影响签到结论」是同一个处理方式。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import urllib.request


def _sign(secret: str) -> tuple[str, str]:
    """飞书官方签名算法：HMAC-SHA256(key=timestamp+"\\n"+secret, msg="") 后 base64。"""
    timestamp = str(int(time.time()))
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(
        string_to_sign.encode("utf-8"), b"", digestmod=hashlib.sha256
    ).digest()
    return timestamp, base64.b64encode(digest).decode("utf-8")


def send_text(webhook: str, secret: str, text: str) -> tuple[bool, str]:
    """发一条纯文本；返回 (是否成功, 原始返回/错误)。"""
    if not webhook:
        return False, "未配置 FEISHU_WEBHOOK，跳过推送"

    payload: dict = {"msg_type": "text", "content": {"text": text}}
    if secret:
        timestamp, sign = _sign(secret)
        payload["timestamp"] = timestamp
        payload["sign"] = sign

    request = urllib.request.Request(
        webhook,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read().decode("utf-8", "replace")
    except Exception as exc:  # 网络问题不该让主流程挂掉
        return False, f"{type(exc).__name__}: {exc}"

    compact = body.replace(" ", "")
    ok = '"code":0' in compact or '"StatusCode":0' in compact
    return ok, body
