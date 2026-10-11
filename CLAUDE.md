# TTS 语音插件（xuqian13.tts-voice-plugin）

## 官方上游与版本溯源
- 官方仓库（Upstream Repository）：https://github.com/xuqian13/tts_voice_plugin
- 官方作者（Author / Maintainer）：xuqian13
- 功能定位：多后端语音合成（TTS）发语音插件
- 本地演进定位：基于官方上游最新版本，融入本地生产环境兼容性定制与稳定性加固。
- 本地专属定制：本地对齐 MiniMax 官方 t2a_v2 接口与 Sweet_Lady 甜美音色配置。


源: GitHub `xuqian13`  
插件 ID: `xuqian13.tts-voice-plugin` v3.3.0

## 主人改过的关键文件

- `config.toml`：
  - `default_backend = "minimax"`
  - `model = "speech-2.8-hd"`
  - `default_voice = "Chinese (Mandarin)_Sweet_Lady"`（甜心女士；旧字段名 `voice_id`）
  - `api_url = "https://api.minimaxi.com/v1/t2a_v2"`（主站；bj 为备用）
  - 音频：mp3 / 32000 / 128000 / channel=1 / speed=1 / volume=1 / pitch=0 / timeout=60
  - Docker：`audio_output_dir = "data"`、`use_base64_audio = false`、`send_error_messages = false`

## 字段映射（重要）

新版本字段名 ← 旧字段名：

- `default_voice` ← `voice_id`
- `volume` ← `vol`
- `audio_format` ← `format`

旧版本 0.x 插件路径是 `legacy-0x-backup/from-server/.../plugins/tts_voice_plugin`（已归档）。

## 接入 & 依赖

- API：`t2a_v2`（minimaxi 主站）。API 冒烟：服务器直调 → HTTP 200，`status_code=0 success`，有 audio hex。
- 输出音频 → Docker `/MaiMBot/data/MaiMBot/plugins/xuqian13_tts-voice-plugin/data/`，NapCat 发到 QQ。

## 关键日志

- `TTS 插件已加载，默认后端=minimax`，注册含 `minimax`。

## 用法

- 群/私聊让 bot 发语音；或常见 `/minimax 文本`（以插件文档为准）。

## 历史脚本

- 本机：`legacy-0x-backup/from-server/patch_tts_minimax.py`、`smoke_minimax_tts.py`。

## 历史脚注

- 2026-07-28: 从服务器同步到本地仓库（baseline head=`27981af`）；补充 `.gitignore` 主人段与本 CLAUDE.md。
