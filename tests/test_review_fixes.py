"""
review-fixes TDD 测试集。

针对 6 个 HIGH 修点 + 1 个 MEDIUM 修点。
每个测试在修复前应 FAIL，修复后应 PASS。
"""

import asyncio
import inspect
import re
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

# 通过 conftest 注入的 namespace package 导入被测模块
from _tts_plugin_test.backends import base as base_mod
from _tts_plugin_test.backends.base import TTSBackendBase, TTSResult
from _tts_plugin_test.backends import gpt_sovits as gsv_mod
from _tts_plugin_test.backends.gpt_sovits import GPTSoVITSBackend
from _tts_plugin_test.backends import doubao as doubao_mod
from _tts_plugin_test.utils import file as file_mod
from _tts_plugin_test.utils.file import TTSFileManager
from _tts_plugin_test.utils import session as session_mod
from _tts_plugin_test.utils.session import TTSSessionManager


class _StubBackend(TTSBackendBase):
    """最小 stub：用于直接测 TTSBackendBase 的 send_audio / cleanup 跟踪行为。"""
    backend_name = "stub"
    async def execute(self, text, voice=None, **kwargs):
        return TTSResult(False, "stub", backend_name=self.backend_name)


# ─────────────────────────────────────────────────────────────────────────
# HIGH 1: backends/base.py:125-126 create_task 无引用（cleanup fire-and-forget）
# 修复目标：模块级 _pending_cleanups 集合 + done_callback discard + 取消函数
# ─────────────────────────────────────────────────────────────────────────
def test_high1_pending_cleanups_set_exists():
    """模块级必须有一个 _pending_cleanups 集合用于跟踪未完成的 cleanup 任务。"""
    assert hasattr(base_mod, "_pending_cleanups"), "backends/base.py 缺少模块级 _pending_cleanups 集合"
    assert isinstance(base_mod._pending_cleanups, set)


def test_high1_send_audio_registers_cleanup_task():
    """send_audio 走文件路径时，asyncio.create_task 出来的 task 必须被登记到 _pending_cleanups。"""
    config_getter = MagicMock(side_effect=lambda key, default=None: {
        "general.use_base64_audio": False,
        "general.audio_output_dir": "",
    }.get(key, default))

    backend = _StubBackend(config_getter)
    backend.set_send_custom(AsyncMock(return_value=True))

    # 让 cleanup_file_async 永远 sleep，这样 task 不会立刻 done 被 discard
    async def forever_cleanup(*args, **kwargs):
        await asyncio.sleep(9999)

    async def run():
        with patch.object(TTSFileManager, "write_audio_async", AsyncMock(return_value=True)), \
             patch.object(TTSFileManager, "cleanup_file_async", forever_cleanup):
            result = await backend.send_audio(
                audio_data=b"x" * 1024, audio_format="mp3", prefix="tts_high1"
            )
            # 关键断言：在 loop 还活着、task 还没 done 时，_pending_cleanups 必须有登记
            pending_count = len(base_mod._pending_cleanups)
            # 清理：取消掉我们刚登记的 task，避免 pytest 警告
            base_mod._cancel_pending_cleanups()
            return result, pending_count

    result, pending_count = asyncio.run(run())

    assert result.success is True
    assert pending_count >= 1, \
        "create_task 创建的清理任务未被追踪，unload 时无法取消"


def test_high1_cancel_helper_clears_set():
    """必须提供一个取消函数（_cancel_pending_cleanups）让 on_unload 调用。"""
    assert hasattr(base_mod, "_cancel_pending_cleanups"), \
        "backends/base.py 缺少 _cancel_pending_cleanups 取消函数，unload 时无法取消进行中的 cleanup"

    # 调用必须不抛异常
    base_mod._cancel_pending_cleanups()
    assert len(base_mod._pending_cleanups) == 0


# ─────────────────────────────────────────────────────────────────────────
# HIGH 2: utils/session.py:57-66 TCPConnector 参数
# 修复目标：force_close=False, trust_env=False, limit=50, limit_per_host=10,
#          ttl_dns_cache=300, enable_cleanup_closed=True
# ─────────────────────────────────────────────────────────────────────────
def test_high2_connector_params():
    """get_session 创建的 TCPConnector / ClientSession 必须使用修复指定的参数。"""
    # 重置单例，保证走"创建新 session"分支
    TTSSessionManager._instance = None

    mgr = asyncio.run(TTSSessionManager.get_instance())
    session = asyncio.run(mgr.get_session("test_high2", timeout=60))

    connector = session.connector
    assert connector is not None, "get_session 未创建 TCPConnector"
    assert connector.force_close is False, f"force_close 应为 False，实际 {connector.force_close}"
    # trust_env 是 ClientSession 参数，不是 TCPConnector 参数
    assert session.trust_env is False, f"ClientSession.trust_env 应为 False，实际 {session.trust_env}"
    assert connector.limit == 50, f"limit 应为 50，实际 {connector.limit}"
    assert connector.limit_per_host == 10, f"limit_per_host 应为 10，实际 {connector.limit_per_host}"
    # ttl_dns_cache 在 aiohttp 3.x 里落在 _cached_hosts._ttl 上
    dns_ttl = getattr(connector._cached_hosts, "_ttl", None)
    assert dns_ttl == 300, f"ttl_dns_cache 应为 300，实际 {dns_ttl}"
    # enable_cleanup_closed：Python 3.12+ 会强制 disabled（cpython PR #118960 已修 FD 泄漏），
    # 构造参数仍被接受；这里只断言 limit / force_close / trust_env / ttl 是我们真正可控的。
    # 若 Python 版本 < 3.12，则 _cleanup_closed_disabled 应为 False。
    import sys
    if sys.version_info < (3, 12):
        assert connector._cleanup_closed_disabled is False, \
            f"enable_cleanup_closed 应为 True（_cleanup_closed_disabled=False），实际 disabled={connector._cleanup_closed_disabled}"

    asyncio.run(mgr.close_session())


# ─────────────────────────────────────────────────────────────────────────
# HIGH 3: utils/session.py + backends/doubao.py 缺 auto_decompress=True
# 修复目标：ClientSession(auto_decompress=True)，删除 doubao.py 自定义 Accept-Encoding 头
# ─────────────────────────────────────────────────────────────────────────
def test_high3_client_session_auto_decompress():
    """ClientSession 创建时必须传 auto_decompress=True，否则 gzip body 会让 response.json() 抛 JSONDecodeError。"""
    TTSSessionManager._instance = None
    mgr = asyncio.run(TTSSessionManager.get_instance())
    session = asyncio.run(mgr.get_session("test_high3", timeout=60))

    assert session.auto_decompress is True, \
        f"ClientSession.auto_decompress 应为 True，实际 {session.auto_decompress}"

    asyncio.run(mgr.close_session())


def test_high3_doubao_no_manual_accept_encoding():
    """doubao.py 不再手动设置 Accept-Encoding 头（否则 aiohttp 不解压 gzip，response.json() 会失败）。"""
    src = inspect.getsource(doubao_mod)
    assert '"Accept-Encoding"' not in src, \
        "backends/doubao.py 仍包含自定义 Accept-Encoding 头，应删除以让 aiohttp auto_decompress 处理"
    assert "'Accept-Encoding'" not in src, "backends/doubao.py 仍包含自定义 Accept-Encoding 头"


# ─────────────────────────────────────────────────────────────────────────
# HIGH 4: backends/gpt_sovits.py ClassVar _current_* 多 stream 并发切换竞态
# 修复目标：asyncio.Lock (ClassVar) 保护 _switch_model + urllib.parse.urlencode
# ─────────────────────────────────────────────────────────────────────────
def test_high4_switch_model_lock_exists():
    """GPTSoVITSBackend 必须有 ClassVar asyncio.Lock 用于串行化模型切换。"""
    assert hasattr(GPTSoVITSBackend, "_switch_model_lock"), \
        "GPTSoVITSBackend 缺少 _switch_model_lock 锁，多 stream 并发切换模型会竞态"
    assert isinstance(GPTSoVITSBackend._switch_model_lock, asyncio.Lock), \
        f"_switch_model_lock 应为 asyncio.Lock 实例，实际 {type(GPTSoVITSBackend._switch_model_lock)}"


def test_high4_switch_model_uses_urlencode():
    """_switch_model 必须用 urllib.parse.urlencode 拼 weights_path，不能直接 f-string 拼接。"""
    src = inspect.getsource(gsv_mod)
    # 接受两种写法：from urllib.parse import urlencode / urllib.parse.urlencode(...)
    has_urlencode = (
        "urlencode" in src
        and ("from urllib.parse" in src or "urllib.parse.urlencode" in src)
    )
    assert has_urlencode, \
        "_switch_model 应使用 urllib.parse.urlencode 编码 weights_path，避免特殊字符破坏 URL"
    assert "weights_path={gpt_weights}" not in src, \
        "_switch_model 仍在用 f-string 拼接 weights_path，存在注入/转义风险"
    assert "weights_path={sovits_weights}" not in src, \
        "_switch_model 仍在用 f-string 拼接 sovits_weights"


def test_high4_switch_model_url_decodes_correctly():
    """特殊字符（? & 空格）通过 urlencode 后仍能被后端正确解析。"""
    from urllib.parse import parse_qs, urlparse

    config_getter = MagicMock(return_value=None)
    backend = GPTSoVITSBackend(config_getter)

    # 关键：让 _switch_model 不真正发请求，但能"调出去"测出 URL
    captured_urls = []

    @asynccontextmanager_dummy
    async def fake_get(url, **kwargs):
        captured_urls.append(url)
        # 立刻返回成功的响应
        resp = MagicMock()
        resp.status = 200
        resp.text = AsyncMock(return_value="")
        yield resp

    # mock TTSSessionManager.get_instance 拿到的对象
    sm = MagicMock()
    sm.get = fake_get

    # 含特殊字符的路径，URL 编码后必须仍能解析回原值
    gpt_path = "/tmp/model with space/weight?v=1&x=2"

    # 重置类变量
    GPTSoVITSBackend._current_gpt_weights = None
    GPTSoVITSBackend._current_sovits_weights = None

    async def run():
        # 直接 patch 后端模块里的 TTSSessionManager（它是 from ..utils.session import 进来的）
        with patch.object(gsv_mod, "TTSSessionManager") as cls:
            cls.get_instance = AsyncMock(return_value=sm)
            ok, err = await backend._switch_model(
                server="http://127.0.0.1:9880",
                gpt_weights=gpt_path,
                sovits_weights=None,
                timeout=10,
            )
        return ok, err

    ok, err = asyncio.run(run())

    assert ok is True, f"_switch_model 应成功，实际 err={err}"
    assert len(captured_urls) == 1
    parsed = urlparse(captured_urls[0])
    qs = parse_qs(parsed.query)
    assert qs.get("weights_path") == [gpt_path], \
        f"URL 编码后无法还原原 weights_path={gpt_path!r}，实际 {qs!r}"


def asynccontextmanager_dummy(func):
    """极简 asynccontextmanager 装饰器（测试用，避免 import 顺序问题）。"""
    from contextlib import asynccontextmanager
    return asynccontextmanager(func)


# ─────────────────────────────────────────────────────────────────────────
# HIGH 5: backends/base.py:96-106 send_custom 返回值被吞
# 修复目标：if not ok: return TTSResult(False, "QQ消息发送失败", ...)
# ─────────────────────────────────────────────────────────────────────────
def test_high5_send_custom_failure_returns_error():
    """_send_custom 返 False 时，send_audio 必须返回 success=False 的 TTSResult，而不是吞掉。"""
    config_getter = MagicMock(side_effect=lambda key, default=None: {
        "general.use_base64_audio": True,  # 走 base64 路径，正好在 96-106 行
    }.get(key, default))

    backend = _StubBackend(config_getter)
    backend.set_send_custom(AsyncMock(return_value=False))  # 模拟 QQ 发送失败

    result = asyncio.run(
        backend.send_audio(audio_data=b"x" * 1024, audio_format="mp3", prefix="tts_high5")
    )

    assert result.success is False, \
        f"_send_custom 返回 False 时 TTSResult.success 应为 False，实际 {result.success}"
    assert "QQ" in result.message or "发送失败" in result.message or "失败" in result.message, \
        f"失败消息应明确表达发送失败，实际 {result.message!r}"


# ─────────────────────────────────────────────────────────────────────────
# HIGH 6: utils/file.py:118-146 临时文件路径无 path traversal 校验
# 修复目标：realpath 后校验在 plugin_dir 子树下，否则抛 ValueError
# ─────────────────────────────────────────────────────────────────────────
def test_high6_path_traversal_rejected(tmp_path):
    """output_dir 含 ../ 跳出 plugin 子树时必须抛 ValueError。"""
    with pytest.raises(ValueError):
        TTSFileManager.generate_temp_path(
            prefix="tts", suffix=".mp3", output_dir="../../etc/cron.daily"
        )


def test_high6_absolute_path_outside_plugin_rejected():
    """output_dir 是绝对路径且不在 plugin 子树时必须抛 ValueError。"""
    # Windows 下用绝对盘符路径；跨平台用 os.path 拼出一个明显在 plugin 之外的目录
    with pytest.raises(ValueError):
        TTSFileManager.generate_temp_path(
            prefix="tts", suffix=".mp3", output_dir="C:\\Windows\\System32"
        )


def test_high6_empty_output_dir_still_works(tmp_path):
    """空 output_dir（默认行为）继续走 project_root，不抛异常。"""
    p = TTSFileManager.generate_temp_path(prefix="tts", suffix=".mp3", output_dir="")
    assert p.endswith(".mp3")
    assert "tts_" in p


# ─────────────────────────────────────────────────────────────────────────
# MEDIUM 1: backends/cosyvoice.py fallback 路径改 assets/test.wav
# 修复目标：默认 fallback 路径是 plugin_dir/assets/test.wav
# ─────────────────────────────────────────────────────────────────────────
def test_medium_cosyvoice_fallback_uses_assets_test_wav():
    """cosyvoice.py 默认 fallback 路径必须是 assets/test.wav（仓库真实存在的资源）。"""
    from _tts_plugin_test.backends import cosyvoice as cv_mod

    src = inspect.getsource(cv_mod)
    # 必须出现 assets/test.wav 的拼接方式
    assert re.search(r"os\.path\.join\([^)]*assets[^)]*test\.wav", src), \
        "cosyvoice.py 应使用 os.path.join(plugin_dir, 'assets', 'test.wav') 作为 fallback 路径"
    # 不应该再回退到 plugin_dir/test.wav（旧错误路径）
    assert "os.path.join(plugin_dir, \"test.wav\")" not in src, \
        "cosyvoice.py 仍在用旧的 plugin_dir/test.wav 路径，应改为 assets/test.wav"
    assert "os.path.join(plugin_dir, 'test.wav')" not in src, \
        "cosyvoice.py 仍在用旧的 plugin_dir/test.wav 路径"
