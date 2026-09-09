"""Tests for capture_still_frame: subprocess wiring, timeout, and error paths.

Uses a fake Popen (no real ffmpeg invoked) — proves the plumbing, not
ffmpeg's actual behavior against a real RTSP source (no hardware available,
documented in the module docstring).
"""

import subprocess

import pytest

from app.recorder_client import RecorderError
from app.rtsp_frame_capture import capture_still_frame, extract_still_frame

_VALID_URL = "rtsp://10.0.0.5:554/cam/realmonitor?channel=1&subtype=0"


class _FakeProc:
    def __init__(
        self,
        stdout: bytes = b"",
        stderr: bytes = b"",
        raise_timeout: bool = False,
    ) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self._raise_timeout = raise_timeout
        self.killed = False
        self.communicate_calls = 0

    def communicate(self, timeout=None):
        self.communicate_calls += 1
        if self._raise_timeout and self.communicate_calls == 1:
            raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=timeout)
        return self._stdout, self._stderr

    def kill(self):
        self.killed = True


def _fake_popen_factory(**kwargs):
    proc = _FakeProc(**kwargs)

    def _popen(cmd, **_):
        return proc

    return _popen, proc


def test_returns_stdout_bytes_on_success():
    popen, _ = _fake_popen_factory(stdout=b"\xff\xd8jpeg-bytes")
    result = capture_still_frame(_VALID_URL, popen=popen)
    assert result == b"\xff\xd8jpeg-bytes"


def test_invalid_url_raises_before_spawning_process():
    calls = []

    def _popen(cmd, **kwargs):
        calls.append(cmd)
        return _FakeProc(stdout=b"x")

    with pytest.raises(RecorderError):
        capture_still_frame("ftp://bad-scheme/cam", popen=_popen)
    assert calls == []


def test_ffmpeg_missing_binary_raises_recorder_error():
    def _popen(cmd, **kwargs):
        raise OSError("No such file or directory: 'ffmpeg'")

    with pytest.raises(RecorderError):
        capture_still_frame(_VALID_URL, popen=_popen)


def test_empty_output_raises_recorder_error_with_stderr_tail():
    popen, _ = _fake_popen_factory(stdout=b"", stderr=b"Connection refused")
    with pytest.raises(RecorderError, match="Connection refused"):
        capture_still_frame(_VALID_URL, popen=popen)


def test_timeout_kills_process_and_raises_recorder_error():
    popen, proc = _fake_popen_factory(raise_timeout=True)
    with pytest.raises(RecorderError, match="não respondeu"):
        capture_still_frame(_VALID_URL, popen=popen, timeout_seconds=1.0)
    assert proc.killed is True
    assert proc.communicate_calls == 2  # first raises, second drains after kill()


def test_credentials_never_appear_in_raised_error_message():
    url = "rtsp://admin:s3cr3t@10.0.0.5:554/cam/realmonitor?channel=1&subtype=0"

    def _popen(cmd, **kwargs):
        raise OSError("boom")

    with pytest.raises(RecorderError) as exc_info:
        capture_still_frame(url, popen=_popen)
    assert "s3cr3t" not in str(exc_info.value)


def test_command_requests_single_frame_and_pipes_to_stdout():
    captured = {}

    def _popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return _FakeProc(stdout=b"jpeg-bytes")

    capture_still_frame(_VALID_URL, popen=_popen)

    cmd = captured["cmd"]
    assert cmd[0] == "ffmpeg"
    assert "-frames:v" in cmd and cmd[cmd.index("-frames:v") + 1] == "1"
    assert cmd[-1] == "pipe:1"
    assert captured["kwargs"]["stdout"] == subprocess.PIPE


def test_stderr_do_ffmpeg_nunca_vaza_a_senha_no_erro():
    """Vazamento real no DEV: o ffmpeg ecoa a URL de entrada inteira ao falhar,
    e o stderr ia cru pro log/RecorderError com a senha do gravador."""
    SENHA = "S3nh4Sup3rS3cr3t4"
    stderr = (
        f"Error opening input file rtsp://Admin:{SENHA}@192.168.35.18:554/"
        "cam/realmonitor?channel=1&subtype=0\n"
    ).encode()
    popen, _ = _fake_popen_factory(stdout=b"", stderr=stderr)

    with pytest.raises(RecorderError) as exc_info:
        capture_still_frame(_VALID_URL, popen=popen)

    assert SENHA not in str(exc_info.value)
    assert "***" in str(exc_info.value)
    assert "192.168.35.18" in str(exc_info.value)  # host segue visível p/ diagnóstico


# ── extract_still_frame: quadro de um trecho JÁ baixado ────────────────────
#
# É o que permite a evidência do INSTANTE da detecção: o trecho vem do
# gravador por HTTP (capture_frame_at) e o quadro sai daqui, sem abrir
# conexão nova e sem a senha passar pelo argv do ffmpeg.

def test_extract_still_frame_le_de_arquivo_e_nao_de_pipe():
    """Arquivo, não `pipe:0`: o MP4 do iNVD 3032 faz o ffmpeg parar em
    'partial file' quando lido de um pipe — o demuxer precisa de seek."""
    capturado = {}

    def _popen(cmd, **_kw):
        capturado["cmd"] = cmd
        return _FakeProc(stdout=b"\xff\xd8jpeg")

    assert extract_still_frame(b"mp4-bytes", popen=_popen) == b"\xff\xd8jpeg"
    cmd = capturado["cmd"]
    assert "pipe:0" not in cmd
    assert cmd[cmd.index("-i") + 1].endswith(".mp4")
    assert cmd[-1] == "pipe:1"


def test_extract_still_frame_trecho_vazio_levanta():
    with pytest.raises(RecorderError, match="vazio"):
        extract_still_frame(b"")


def test_extract_still_frame_sem_saida_levanta_com_stderr_redigido():
    """Mesma disciplina do caminho RTSP: o stderr do ffmpeg pode ecoar URL com
    credencial do gravador."""
    proc = _FakeProc(stdout=b"", stderr=b"erro em rtsp://admin:s3cr3t@10.0.0.5/x")
    with pytest.raises(RecorderError) as exc:
        extract_still_frame(b"mp4", popen=lambda *a, **k: proc)
    assert "s3cr3t" not in str(exc.value)


def test_extract_still_frame_timeout_mata_o_processo():
    proc = _FakeProc(raise_timeout=True)
    with pytest.raises(RecorderError, match="não respondeu"):
        extract_still_frame(b"mp4", popen=lambda *a, **k: proc, timeout_seconds=0.1)
    assert proc.killed is True
