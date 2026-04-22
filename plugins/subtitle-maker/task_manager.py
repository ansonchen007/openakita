"""subtitle-maker — task manager subclass."""

from __future__ import annotations

from openakita_plugin_sdk.contrib import BaseTaskManager


class SubtitleTaskManager(BaseTaskManager):
    def extra_task_columns(self):
        return [
            ("source_path", "TEXT"),
            ("srt_path", "TEXT"),
            ("vtt_path", "TEXT"),
            ("burned_video_path", "TEXT"),
            ("language", "TEXT"),
        ]

    def default_config(self):
        return {
            "asr_provider": "auto",
            "asr_region": "cn",
            "asr_model": "base",
            "asr_language": "auto",
            "asr_binary": "whisper-cli",
            "dashscope_api_key": "",
            "default_format": "srt",   # srt | vtt | both
            "burn_into_video": "false",
            "ffmpeg_path": "ffmpeg",
        }
