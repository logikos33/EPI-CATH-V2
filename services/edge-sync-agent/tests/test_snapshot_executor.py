"""Tests for SnapshotExecutor: recorder capture + multipart upload, and the
anti-lockout circuit breaker (a 401/403 from the recorder must suspend ALL
future capture_snapshot attempts until process restart — CLAUDE.md, the
gravador applies anti-brute-force lockout to repeated failed-auth attempts).
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock

from app.recorder_client import RecorderAuthError, RecorderChannelError, RecorderError
from app.snapshot_executor import SnapshotExecutor


def _http_ok():
    resp = MagicMock()
    resp.status_code = 201
    return resp


def _http_rejected(status=500):
    resp = MagicMock()
    resp.status_code = status
    resp.text = "upstream rejected"
    return resp


def _make_executor(recorder, http=None):
    return SnapshotExecutor(
        recorder_client=recorder,
        http_client=http or MagicMock(),
        cloud_url="http://cloud.test",
        token="tok",
    )


# ── happy path ───────────────────────────────────────────────────────────────

def test_capture_and_upload_success():
    recorder = MagicMock()
    recorder.get_snapshot.return_value = b"jpeg-bytes"
    http = MagicMock()
    http.post.return_value = _http_ok()
    executor = _make_executor(recorder, http)

    result = executor.capture_and_upload("cam-1", channel=3)

    assert result == {"ok": True}
    # O channel do payload segue adiante como HINT — é o que permite
    # fotografar câmera draft (fora do channel_map por desenho).
    recorder.get_snapshot.assert_called_once_with("cam-1", channel_hint=3)
    url, kwargs = http.post.call_args
    assert url[0] == "http://cloud.test/api/v1/edge/cameras/cam-1/snapshot"
    assert kwargs["headers"]["Authorization"] == "Bearer tok"
    assert kwargs["files"]["file"][0] == "snapshot.jpg"
    assert kwargs["files"]["file"][1] == b"jpeg-bytes"


def test_capture_and_upload_strips_trailing_slash_from_cloud_url():
    recorder = MagicMock()
    recorder.get_snapshot.return_value = b"x"
    http = MagicMock()
    http.post.return_value = _http_ok()
    executor = SnapshotExecutor(recorder, http, cloud_url="http://cloud.test/", token="t")

    executor.capture_and_upload("cam-1")

    url = http.post.call_args[0][0]
    assert url == "http://cloud.test/api/v1/edge/cameras/cam-1/snapshot"


# ── non-auth capture failures: continue, never trip the breaker ────────────

def test_capture_timeout_returns_reason_timeout_and_does_not_trip_breaker():
    recorder = MagicMock()
    recorder.get_snapshot.side_effect = RecorderError("ffmpeg não respondeu em 10.0s")
    executor = _make_executor(recorder)

    result = executor.capture_and_upload("cam-1")

    assert result["ok"] is False
    assert result["reason"] == "timeout"
    assert executor.circuit_open is False


def test_capture_no_signal_returns_generic_reason():
    recorder = MagicMock()
    recorder.get_snapshot.side_effect = RecorderError("canal sem sinal")
    executor = _make_executor(recorder)

    result = executor.capture_and_upload("cam-1")

    assert result["ok"] is False
    assert result["reason"] == "sem sinal no canal"
    assert executor.circuit_open is False


def test_capture_unexpected_exception_never_raises_out():
    recorder = MagicMock()
    recorder.get_snapshot.side_effect = RuntimeError("boom")
    executor = _make_executor(recorder)

    result = executor.capture_and_upload("cam-1")

    assert result["ok"] is False
    assert result["reason"] == "capture_failed"


# ── upload failures ──────────────────────────────────────────────────────────

def test_upload_rejected_returns_upload_failed_reason():
    recorder = MagicMock()
    recorder.get_snapshot.return_value = b"jpeg"
    http = MagicMock()
    http.post.return_value = _http_rejected(500)
    executor = _make_executor(recorder, http)

    result = executor.capture_and_upload("cam-1")

    assert result["ok"] is False
    assert result["reason"] == "upload_failed"


def test_upload_network_error_returns_upload_failed_reason():
    recorder = MagicMock()
    recorder.get_snapshot.return_value = b"jpeg"
    http = MagicMock()
    http.post.side_effect = ConnectionError("offline")
    executor = _make_executor(recorder, http)

    result = executor.capture_and_upload("cam-1")

    assert result["ok"] is False
    assert result["reason"] == "upload_failed"


# ── anti-lockout circuit breaker ─────────────────────────────────────────────

def test_auth_failure_trips_circuit_and_reports_reason_auth():
    recorder = MagicMock()
    recorder.get_snapshot.side_effect = RecorderAuthError("onvif_soap_auth_failed status=401")
    executor = _make_executor(recorder)

    result = executor.capture_and_upload("cam-1")

    assert result == {"ok": False, "reason": "auth", "detail": "onvif_soap_auth_failed status=401"}
    assert executor.circuit_open is True
    assert "401" in executor.circuit_reason


def test_circuit_open_short_circuits_without_touching_recorder_again():
    recorder = MagicMock()
    recorder.get_snapshot.side_effect = RecorderAuthError("status=401")
    executor = _make_executor(recorder)

    executor.capture_and_upload("cam-1")  # trips the breaker
    recorder.get_snapshot.reset_mock()

    result = executor.capture_and_upload("cam-2")  # a DIFFERENT camera

    assert result["ok"] is False
    assert result["reason"] == "auth"
    recorder.get_snapshot.assert_not_called()  # never touches the recorder again


def test_circuit_survives_across_many_cameras_until_reset():
    recorder = MagicMock()
    recorder.get_snapshot.side_effect = RecorderAuthError("status=403")
    executor = _make_executor(recorder)

    executor.capture_and_upload("cam-1")
    for cam in ("cam-2", "cam-3", "cam-4"):
        result = executor.capture_and_upload(cam)
        assert result["reason"] == "auth"

    assert recorder.get_snapshot.call_count == 1  # only the FIRST attempt ever hit the recorder


def test_reset_circuit_allows_capture_again():
    recorder = MagicMock()
    recorder.get_snapshot.side_effect = [RecorderAuthError("status=401"), b"jpeg-bytes"]
    http = MagicMock()
    http.post.return_value = _http_ok()
    executor = _make_executor(recorder, http)

    executor.capture_and_upload("cam-1")
    assert executor.circuit_open is True

    executor.reset_circuit()
    result = executor.capture_and_upload("cam-1")

    assert executor.circuit_open is False
    assert result == {"ok": True}


# ── channel hint (bug de campo RVB: draft fora do channel_map) ───────────────

def test_missing_channel_forwards_hint_none():
    recorder = MagicMock()
    recorder.get_snapshot.return_value = b"jpeg"
    http = MagicMock()
    http.post.return_value = _http_ok()
    executor = _make_executor(recorder, http)

    executor.capture_and_upload("cam-1")  # sem channel no payload

    recorder.get_snapshot.assert_called_once_with("cam-1", channel_hint=None)


def test_invalid_channel_hint_is_sanitized_to_none():
    """Hint inválido (tipo errado, bool, fora de 1..64) vira None — nunca
    derruba o comando nem chega cru ao RecorderClient."""
    recorder = MagicMock()
    recorder.get_snapshot.return_value = b"jpeg"
    http = MagicMock()
    http.post.return_value = _http_ok()
    executor = _make_executor(recorder, http)

    for bad in ("9", 0, 65, -1, True, 3.5, {"ch": 9}):
        recorder.get_snapshot.reset_mock()
        executor.capture_and_upload("cam-1", channel=bad)
        recorder.get_snapshot.assert_called_once_with("cam-1", channel_hint=None)


def test_channel_hint_boundaries_1_and_64_are_valid():
    recorder = MagicMock()
    recorder.get_snapshot.return_value = b"jpeg"
    http = MagicMock()
    http.post.return_value = _http_ok()
    executor = _make_executor(recorder, http)

    executor.capture_and_upload("cam-1", channel=1)
    recorder.get_snapshot.assert_called_with("cam-1", channel_hint=1)
    executor.capture_and_upload("cam-1", channel=64)
    recorder.get_snapshot.assert_called_with("cam-1", channel_hint=64)


def test_channel_resolution_failure_reports_specific_no_channel_reason():
    """Fora do mapa E sem hint -> reason='no_channel' (específico), NUNCA o
    genérico 'sem sinal no canal' — que mentiria sobre um canal que nem foi
    contatado (o sintoma exato do bug de campo)."""
    recorder = MagicMock()
    recorder.get_snapshot.side_effect = RecorderChannelError(
        "camera_id='cam-draft' fora do channel_map e sem canal no comando"
    )
    executor = _make_executor(recorder)

    result = executor.capture_and_upload("cam-draft")

    assert result["ok"] is False
    assert result["reason"] == "no_channel"
    assert "fora do channel_map" in result["detail"]
    assert executor.circuit_open is False  # não é auth — breaker intacto


# ── evidência de alerta (capture_evidence) ──────────────────────────────────
#
# O caminho que faz o alerta do box chegar COM frame: mesma captura, mesmo
# breaker, rota /evidence, e o retorno é a chave R2 (que só a nuvem sabe — o
# box não tem, nem terá, credencial de R2).

def _http_created(r2_key="evidence/cam-1/20260909T041800000000.jpg"):
    resp = MagicMock()
    resp.status_code = 201
    resp.json.return_value = {"success": True, "data": {"r2_key": r2_key}}
    return resp


def test_capture_evidence_devolve_r2_key_da_nuvem():
    recorder = MagicMock()
    recorder.get_snapshot.return_value = b"jpeg-bytes"
    http = MagicMock()
    http.post.return_value = _http_created()
    executor = _make_executor(recorder, http)

    assert executor.capture_evidence("cam-1") == (
        "evidence/cam-1/20260909T041800000000.jpg", b"jpeg-bytes", "ao_vivo",
    )
    url, _kwargs = http.post.call_args
    assert url[0] == "http://cloud.test/api/v1/edge/cameras/cam-1/evidence"


def test_capture_evidence_sem_sinal_devolve_none_sem_levantar():
    """Evidência é desejável, não pode bloquear o alerta."""
    recorder = MagicMock()
    recorder.get_snapshot.side_effect = RecorderError("sem sinal no canal")
    executor = _make_executor(recorder)

    assert executor.capture_evidence("cam-1") == (None, None, None)


def test_capture_evidence_auth_abre_o_mesmo_breaker_do_snapshot():
    """Anti-lockout: credencial rejeitada na evidência para TAMBÉM o snapshot."""
    recorder = MagicMock()
    recorder.get_snapshot.side_effect = RecorderAuthError("401 Unauthorized")
    executor = _make_executor(recorder)

    assert executor.capture_evidence("cam-1") == (None, None, None)
    assert executor.circuit_open is True
    assert executor.capture_and_upload("cam-2") == {
        "ok": False, "reason": "auth", "detail": "401 Unauthorized",
    }
    recorder.get_snapshot.assert_called_once()  # nunca tocou o gravador de novo


def test_capture_evidence_com_breaker_aberto_nao_toca_o_gravador():
    recorder = MagicMock()
    executor = _make_executor(recorder)
    executor._trip_circuit("credencial rejeitada")

    assert executor.capture_evidence("cam-1") == (None, None, None)
    recorder.get_snapshot.assert_not_called()


def test_capture_evidence_upload_rejeitado_devolve_o_frame_sem_chave():
    """Upload falho não perde o pixel: o frame é bom e o GuardaPessoa ainda o
    julga — só o alerta é que nasce sem imagem."""
    recorder = MagicMock()
    recorder.get_snapshot.return_value = b"jpeg-bytes"
    http = MagicMock()
    http.post.return_value = _http_rejected(502)  # R2 fora do ar
    executor = _make_executor(recorder, http)

    assert executor.capture_evidence("cam-1") == (None, b"jpeg-bytes", "ao_vivo")


# ── evidência ANCORADA no instante da detecção ─────────────────────────────
#
# O quadro tem de ser o do momento que gerou a caixa, não o "agora" que o
# gravador devolve segundos depois. Contrato confirmado em hardware real
# (Intelbras iNVD 3032 da RVB, 09/09/2026) — ver
# rtsp_timestamp_recorder_client.capture_frame_at.

_INSTANTE = datetime(2026, 9, 9, 4, 9, 1, tzinfo=timezone.utc)


def test_capture_evidence_pede_o_quadro_do_instante_da_deteccao():
    """Falha antes do conserto: `capture_evidence` só sabia pedir o ao vivo."""
    recorder = MagicMock()
    recorder.capture_frame_at.return_value = b"quadro-do-instante"
    http = MagicMock()
    http.post.return_value = _http_created()
    executor = _make_executor(recorder, http)

    chave, jpeg, origem = executor.capture_evidence("cam-1", _INSTANTE)

    recorder.capture_frame_at.assert_called_once_with("cam-1", _INSTANTE)
    recorder.get_snapshot.assert_not_called()  # nenhuma ida extra ao gravador
    assert (jpeg, origem) == (b"quadro-do-instante", "gravador_no_instante")
    assert chave == "evidence/cam-1/20260909T041800000000.jpg"


def test_sem_instante_continua_no_ao_vivo():
    """Chamada antiga (sem instante) segue funcionando: nada de alerta perdido
    porque um publicador velho não manda timestamp."""
    recorder = MagicMock()
    recorder.get_snapshot.return_value = b"agora"
    http = MagicMock()
    http.post.return_value = _http_created()

    _, jpeg, origem = _make_executor(recorder, http).capture_evidence("cam-1")

    assert (jpeg, origem) == (b"agora", "ao_vivo")
    recorder.capture_frame_at.assert_not_called()


def test_gravador_sem_o_trecho_degrada_para_o_ao_vivo():
    """O iNVD grava por movimento/agendamento: nem todo instante existe (404).
    Evidência ruim é ruim; alerta que não chega é pior."""
    recorder = MagicMock()
    recorder.capture_frame_at.side_effect = RecorderError("404: sem gravação na janela")
    recorder.get_snapshot.return_value = b"agora"
    http = MagicMock()
    http.post.return_value = _http_created()

    _, jpeg, origem = _make_executor(recorder, http).capture_evidence("cam-1", _INSTANTE)

    assert (jpeg, origem) == (b"agora", "ao_vivo")


def test_playback_falho_pausa_e_nao_dobra_as_conexoes_no_gravador():
    """A TRAVA do desenho: cada evento gasta UMA ida ao gravador. Se o playback
    falhasse e o ao vivo entrasse a cada evento, a taxa contra um aparelho com
    lockout anti-brute-force DOBRARIA. Depois da primeira falha, os eventos
    seguintes vão direto ao vivo."""
    recorder = MagicMock()
    recorder.capture_frame_at.side_effect = RecorderError("CGI fora")
    recorder.get_snapshot.return_value = b"agora"
    http = MagicMock()
    http.post.return_value = _http_created()
    executor = _make_executor(recorder, http)
    relogio = [1000.0]
    executor._clock = lambda: relogio[0]

    for _ in range(5):
        relogio[0] += 1.0
        executor.capture_evidence("cam-1", _INSTANTE)

    assert recorder.capture_frame_at.call_count == 1, "playback insistiu dentro da pausa"
    assert recorder.get_snapshot.call_count == 5

    relogio[0] += 61.0  # passada a pausa, volta a tentar o instante certo
    executor.capture_evidence("cam-1", _INSTANTE)
    assert recorder.capture_frame_at.call_count == 2


def test_auth_no_playback_abre_o_breaker_e_nao_tenta_o_ao_vivo():
    """Credencial rejeitada nunca vira fallback: reusar credencial recusada
    contra outro transporte é martelar o mesmo aparelho (anti-lockout)."""
    recorder = MagicMock()
    recorder.capture_frame_at.side_effect = RecorderAuthError("401")
    executor = _make_executor(recorder, MagicMock())

    assert executor.capture_evidence("cam-1", _INSTANTE) == (None, None, None)
    recorder.get_snapshot.assert_not_called()
    assert executor.circuit_open is True
