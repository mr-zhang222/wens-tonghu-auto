"""配置集中入口。

所有配置都从环境变量读：
- 本机跑：直接 export / 或先跑 local/export_login.py 生成 storage_state.json
- 云端跑：GitHub Repository secrets / variables 注入，明文 token 不落仓库、不进日志
"""

from __future__ import annotations

import base64
import binascii
import gzip
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

# GitHub secret 硬上限 48 KB（官方限制）。而 Playwright 导出的 storage_state
# 里绝大部分是前端页面缓存（`irep-m-*`，占 95%+），原始 base64 轻松上 130 KB
# → 会被 GitHub 直接拒收。所以 export_login.py 导出的是 **gzip + base64**，
# 体积约 1/8，无损。这里两种都认：看字节魔数决定要不要解压。
GZIP_MAGIC = b"\x1f\x8b"

# 默认入口 = 你给的同呼移动端页面。
# 如果学习平台在同呼里有独立入口（比如点进去之后是另一个页面），
# 把 WENS_LEARN_URL 换成那个地址即可，脚本不用改。
DEFAULT_ENTRY_URL = (
    "https://cq.wens.com.cn/mobile.html?form=nbj_user_mob_home"
    "&appid=501136478&client_id=501136478&msgShowStyle=11"
    "#/page/root6639a8ccffd34ce5abfbca50b8e424c3"
)


def _s(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    return default if value is None or not value.strip() else value.strip()


def _i(name: str, default: int) -> int:
    try:
        return int(_s(name, str(default)))
    except ValueError:
        return default


def _f(name: str, default: float) -> float:
    try:
        return float(_s(name, str(default)))
    except ValueError:
        return default


def _b(name: str, default: bool = False) -> bool:
    return _s(name, "1" if default else "0").lower() in {"1", "true", "yes", "y", "on"}


def decode_storage_state(raw_b64: str) -> bytes:
    """把 secret 里的那串还原成 storage_state.json 的字节。

    兼容两种格式：
    - gzip + base64（export_login.py 现在导出的，体积小，能过 48 KB 限制）
    - 纯 base64（早期导出 / 手动构造的，照样支持）
    粘贴时夹带的换行、空格会被 base64 解码器自动忽略，不用手工清。
    """
    try:
        raw = base64.b64decode(raw_b64)
    except (binascii.Error, ValueError) as exc:
        raise SystemExit(
            "WENS_STORAGE_STATE 不是合法 base64 —— 请重新执行 local/export_login.py，"
            "并把 login_state/storage_state.gz.b64 的内容整段复制过去"
        ) from exc

    if raw[:2] == GZIP_MAGIC:
        try:
            raw = gzip.decompress(raw)
        except OSError as exc:
            raise SystemExit(
                "WENS_STORAGE_STATE 看起来是 gzip 但解压失败（内容可能被截断）——"
                "请重新复制 login_state/storage_state.gz.b64 的完整内容"
            ) from exc

    if raw.lstrip()[:1] != b"{":
        raise SystemExit(
            "WENS_STORAGE_STATE 解出来的不是 JSON —— 请重新执行 local/export_login.py"
        )
    return raw


@dataclass
class Settings:
    entry_url: str
    artifact_dir: Path
    storage_state_path: Path
    cookie_header: str
    feishu_webhook: str
    feishu_secret: str
    playback_rate: float
    max_minutes: int
    headless: bool
    dry_run: bool
    inspect: bool
    # 快速模式相关
    fast_mode: bool
    fast_method: str  # seek（默认，拽进度条）| replay（实验，重放请求）
    seek_step_seconds: float
    seek_interval_ms: float
    final_tail_seconds: float
    heartbeat_step_seconds: float
    heartbeat_delay_ms: float
    fast_rate: float
    max_video_minutes: int

    @property
    def budget_seconds(self) -> float:
        """单次运行的硬时间预算，超了就把剩下的留到下一次补做。"""
        return float(max(1, self.max_minutes) * 60)


def load_settings() -> Settings:
    workspace = Path(os.environ.get("GITHUB_WORKSPACE", ".")).resolve()
    artifact_dir = workspace / _s("ARTIFACT_DIR", "artifacts")
    artifact_dir.mkdir(parents=True, exist_ok=True)

    # 优先用 storageState（Playwright 全量登录态，最完整）；
    # 没有就用 WENS_COOKIE（手动从 DevTools 复制的 Cookie 字符串，最省事）。
    #
    # ⚠️ 从 secret 解出来的登录态**绝不能落在 artifact_dir**：流水线最后会把
    #    artifacts/ 整个上传成产物，落在那儿就等于把登录态上传到仓库。
    #    所以写到系统临时目录（runner 用完即弃）。
    state_dir = Path(tempfile.gettempdir()) / "wens-tonghu-auto"
    state_dir.mkdir(parents=True, exist_ok=True)
    storage_state_path = state_dir / "storage_state.json"

    raw_b64 = _s("WENS_STORAGE_STATE")
    if raw_b64:
        storage_state_path.write_bytes(decode_storage_state(raw_b64))
    else:
        # 本机跑的时候，直接复用 export_login.py 导出的那份，省得手动复制
        local_state = workspace / "login_state" / "storage_state.json"
        if local_state.exists():
            storage_state_path = local_state

    return Settings(
        entry_url=_s("WENS_LEARN_URL", DEFAULT_ENTRY_URL),
        artifact_dir=artifact_dir,
        storage_state_path=storage_state_path,
        cookie_header=_s("WENS_COOKIE"),
        feishu_webhook=_s("FEISHU_WEBHOOK"),
        feishu_secret=_s("FEISHU_SECRET"),
        playback_rate=_f("PLAYBACK_RATE", 2.0),
        max_minutes=_i("MAX_MINUTES", 180),
        headless=_b("HEADLESS", True),
        dry_run=_b("DRY_RUN", False),
        inspect=_b("INSPECT", False),
        fast_mode=_b("FAST_MODE", False),
        fast_method=_s("FAST_METHOD", "seek").lower(),
        seek_step_seconds=_f("SEEK_STEP_SECONDS", 10.0),
        seek_interval_ms=_f("SEEK_INTERVAL_MS", 1200.0),
        final_tail_seconds=_f("FINAL_TAIL_SECONDS", 8.0),
        heartbeat_step_seconds=_f("HEARTBEAT_STEP_SECONDS", 15.0),
        heartbeat_delay_ms=_f("HEARTBEAT_DELAY_MS", 80.0),
        fast_rate=_f("FAST_RATE", 10.0),
        max_video_minutes=_i("MAX_VIDEO_MINUTES", 45),
    )
