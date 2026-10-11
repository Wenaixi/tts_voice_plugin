"""Pytest 配置：用 sys.modules 注入把 plugin root 当成 namespace package，
绕过 directory 名带 hyphen 的问题，使 backends/、utils/、config_keys.py 内部
的 ``from ..config_keys`` 等相对导入能正确解析。
"""

import importlib.util
import sys
import types
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_PKG = "_tts_plugin_test"

# 1. 注册 namespace package 容器
ns_pkg = types.ModuleType(PLUGIN_PKG)
ns_pkg.__path__ = [str(PLUGIN_ROOT)]
sys.modules[PLUGIN_PKG] = ns_pkg


def _load_module(rel_name: str):
    """从 plugin root 按相对路径加载 .py 模块。"""
    full = f"{PLUGIN_PKG}.{rel_name}"
    path = PLUGIN_ROOT / (rel_name.replace(".", "/") + ".py")
    spec = importlib.util.spec_from_file_location(full, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[full] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_package(rel_name: str):
    """从 plugin root 按相对路径加载包（含 __init__.py）。"""
    full = f"{PLUGIN_PKG}.{rel_name}"
    pkg_dir = PLUGIN_ROOT / rel_name.replace(".", "/")
    spec = importlib.util.spec_from_file_location(
        full,
        pkg_dir / "__init__.py",
        submodule_search_locations=[str(pkg_dir)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[full] = mod
    spec.loader.exec_module(mod)
    return mod


# 2. 先注入无依赖的 config_keys（backends 和 utils 都依赖它）
_load_module("config_keys")

# 3. 加载 utils 包，让它 __init__ 自动拉起 text/file/session
_load_package("utils")

# 4. 加载 backends 包，让它 __init__ 自动注册全部后端
_load_package("backends")
