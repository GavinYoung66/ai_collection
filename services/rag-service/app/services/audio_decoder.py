"""AMR/WMA 音频解码：ffmpeg 转标准 WAV 字节（V5，design D6）。

- Client 侧解码（对齐 D6 决议）：ffmpeg 将 AMR/WMA 解码为 16kHz 单声道 PCM WAV，
  交给既有 audio_parser → asr_client VAD/≤60s 分片/ASR Provider 管线，Provider 契约零改动；
- 仅由 AudioParser 对 .amr/.wma 调用，不拦截已支持格式（mp3/wav/m4a 直通原路径）；
- 临时文件用后即删（TemporaryDirectory）；ffmpeg 缺失/解码失败/超时 → AudioDecodeError，
  上层转 A3001 或降级占位，worker 不卡死、不污染进程级事件循环。
"""
import asyncio
import subprocess
import tempfile
import uuid
from pathlib import Path

from common.config.settings import settings


class AudioDecodeError(Exception):
    """AMR/WMA 解码失败（上层转 A3001 或降级占位）。"""


async def decode_to_wav(file_path: Path) -> bytes:
    """ffmpeg 将 AMR/WMA 解码为 WAV 字节（subprocess，超时受 KB_PARSE_TIMEOUT 约束）。"""
    timeout = settings.KB_PARSE_TIMEOUT
    with tempfile.TemporaryDirectory(prefix="rag_audio_decode_") as tmp:
        tmp_path = Path(tmp)
        src = tmp_path / f"in_{uuid.uuid4().hex[:8]}{file_path.suffix}"
        dst = tmp_path / f"out_{uuid.uuid4().hex[:8]}.wav"
        src.write_bytes(file_path.read_bytes())
        cmd = [
            "ffmpeg", "-y", "-i", str(src),
            "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
            str(dst),
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                raise AudioDecodeError(f"ffmpeg 解码超时（>{timeout}s）")
            if proc.returncode != 0:
                detail = (stderr or b"").decode(errors="ignore")[-200:]
                raise AudioDecodeError(f"ffmpeg 解码失败: {detail}")
            return dst.read_bytes()
        except subprocess.SubprocessError as e:
            raise AudioDecodeError(f"ffmpeg 进程异常: {e}") from e
        except FileNotFoundError as e:
            raise AudioDecodeError("ffmpeg 未安装（系统 PATH 缺失）") from e
