"""Config poller: pulls scenario / model manifest from cloud, applies in memory.

State is held in a thread-safe ConfigState.  Callers read cameras/rules/scenario
via accessors.  When a new model is available, get_model_manifest() returns the
manifest; call ack_model_applied() after download to clear the pending entry.

No restart is needed to apply new config — the poller updates in-process state.
"""

import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Optional

from . import edge_config_cache

logger = logging.getLogger(__name__)

# F1: config muda pouco mas precisa chegar rápido quando muda. 45s + ETag/304
# (o 304 é barato) equilibra latência de aplicação e carga em frota grande.
_DEFAULT_INTERVAL = 45.0


@dataclass
class ModelManifest:
    sha256: str
    url: str
    engine_type: str


@dataclass
class _ConfigState:
    cameras: list[dict] = field(default_factory=list)
    rules: list[dict] = field(default_factory=list)
    scenario: dict = field(default_factory=dict)
    pending_model: Optional[ModelManifest] = None
    current_model_sha256: str = ""


class ConfigPoller:
    """Periodically fetches edge config from the cloud and applies it in memory."""

    def __init__(
        self,
        http_client: Any,
        cloud_url: str,
        device_id: str,
        token: str,
        poll_interval_s: float = _DEFAULT_INTERVAL,
        cache_path: Optional[str] = None,
    ) -> None:
        self._http = http_client
        self._url = f"{cloud_url.rstrip('/')}/api/v1/edge/config/poll"
        self._device_id = device_id
        self._token = token
        self._interval = poll_interval_s
        self._state = _ConfigState()
        self._lock = threading.RLock()
        self._etag = ""  # F1: última config aplicada (If-None-Match → 304)
        # ADR-0058: quando setado, toda config com "cameras" grava o mapa
        # camera_id->channel num arquivo local (edge_config_cache.py) — é o
        # que recorder_factory.py/collector_loop.py leem no PRÓXIMO restart
        # em vez de RECORDER_CHANNEL_MAP. None (default) preserva o
        # comportamento anterior a esta ADR para quem constrói um
        # ConfigPoller sem cache (todos os testes existentes).
        self._cache_path = cache_path

    # ── public accessors (thread-safe) ───────────────────────────────────────

    def get_cameras(self) -> list[dict]:
        with self._lock:
            return list(self._state.cameras)

    def get_rules(self) -> list[dict]:
        with self._lock:
            return list(self._state.rules)

    def get_scenario(self) -> dict:
        with self._lock:
            return dict(self._state.scenario)

    def get_model_manifest(self) -> Optional[ModelManifest]:
        """Returns the pending manifest if a new model is available, else None."""
        with self._lock:
            return self._state.pending_model

    def get_current_model_sha256(self) -> str:
        with self._lock:
            return self._state.current_model_sha256

    def ack_model_applied(self, sha256: str) -> None:
        """Call after model_manager swaps the model — clears pending entry."""
        with self._lock:
            self._state.current_model_sha256 = sha256
            self._state.pending_model = None

    def apply_camera_config(
        self,
        camera_id: Any,
        fps_target: Optional[int] = None,
        quality_preset: Optional[str] = None,
        collection_subtype: Optional[int] = None,
    ) -> bool:
        """Thread-safe partial update of a single camera in the in-memory state.

        Used by the CommandPoller when consuming 'update_camera_config'
        edge_commands (WS10). Only the provided fields are touched.
        Returns True when the camera was found and updated.

        collection_subtype (eixo COLETA, migration 114): aplicado in-memory
        aqui como os outros dois, mas o COLETOR (rtsp_timestamp_recorder_client)
        só lê o cache em disco no próximo restart do processo — mesma
        disciplina "structural change needs a controlled reload" da ADR-0054
        (D2), não hot-swap live.
        """
        if not camera_id:
            return False
        with self._lock:
            for cam in self._state.cameras:
                if str(cam.get("id")) == str(camera_id):
                    if fps_target is not None:
                        cam["fps_target"] = fps_target
                    if quality_preset is not None:
                        cam["quality_preset"] = quality_preset
                    if collection_subtype is not None:
                        cam["collection_subtype"] = collection_subtype
                    logger.info(
                        "camera_config_applied id=%s fps=%s quality=%s collection_subtype=%s",
                        camera_id, fps_target, quality_preset, collection_subtype,
                    )
                    return True
        logger.warning("camera_config_apply_miss id=%s (not in state yet)", camera_id)
        return False

    # ── internal ─────────────────────────────────────────────────────────────

    def _poll_once(self) -> bool:
        """Issue one config poll. Returns True if cloud responded OK (200 ou 304).

        F1: envia If-None-Match com o último ETag; 304 = nada mudou (mantém a
        última config boa, não reaplica). Config ruim/erro nunca derruba o site —
        o estado anterior é preservado (só substituímos em 200 bem-sucedido).
        """
        try:
            headers = {"Authorization": f"Bearer {self._token}"}
            if self._etag:
                headers["If-None-Match"] = self._etag
            resp = self._http.get(
                self._url,
                params={
                    "device_id": self._device_id,
                    "current_model_sha256": self._state.current_model_sha256,
                },
                headers=headers,
                timeout=15.0,
            )
            if resp.status_code == 304:
                return True  # nada mudou — última config boa preservada
            if resp.status_code != 200:
                logger.warning("config_poll_failed status=%d", resp.status_code)
                return False
            self._apply(resp.json())
            # Só grava o ETag após aplicar com sucesso (última config BOA).
            new_etag = resp.headers.get("ETag", "") if hasattr(resp, "headers") else ""
            if new_etag:
                self._etag = new_etag
            return True
        except Exception as exc:
            logger.warning("config_poll_error %s", exc)
            return False

    def _apply(self, data: dict) -> None:
        with self._lock:
            # Partial updates: only keys present in the response are applied.
            if "cameras" in data:
                self._state.cameras = list(data["cameras"])
                if self._cache_path:
                    self._write_channel_map_cache(data["cameras"], data.get("config_version", ""))
            if "rules" in data:
                self._state.rules = list(data["rules"])
            if "scenario" in data:
                self._state.scenario = dict(data["scenario"])

            model_data = data.get("model")
            if model_data and model_data.get("sha256") != self._state.current_model_sha256:
                self._state.pending_model = ModelManifest(
                    sha256=model_data["sha256"],
                    url=model_data["url"],
                    engine_type=model_data.get("engine_type", "pt"),
                )
                logger.info("new_model_available sha256=%s", self._state.pending_model.sha256)
            else:
                # No new model (null response or same sha256)
                self._state.pending_model = None

    def _write_channel_map_cache(self, cameras: list[dict], config_version: str) -> None:
        """ADR-0058: extrai {camera_id: channel} das câmeras ATIVAS e persiste.

        Só câmeras com `is_active` truthy e `channel` presente entram no mapa
        — mesmo espírito de RECORDER_CHANNEL_MAP nunca ter incluído câmera
        desligada. `is_active` ausente (payload futuro/parcial) é tratado
        como ativo (não trava a cache num campo que hoje é sempre enviado).

        collection_subtype_map (eixo COLETA, migration 114): mesmo filtro
        ativa+com-canal, mas só entram câmeras com `collection_subtype`
        explicitamente presente no payload — ausência não vira 0 aqui
        (o default fica a cargo de quem lê, RtspTimestampRecorderClient).
        """
        active_with_channel = [
            cam
            for cam in cameras
            if cam.get("channel") is not None and cam.get("is_active", True)
        ]
        channel_map = {str(cam["id"]): int(cam["channel"]) for cam in active_with_channel}
        collection_subtype_map = {
            str(cam["id"]): int(cam["collection_subtype"])
            for cam in active_with_channel
            if cam.get("collection_subtype") is not None
        }
        # Eixo OPERAÇÃO: fps_target por câmera. Mesmo filtro ativa+com-canal.
        # Ausente no payload NÃO vira default aqui — quem lê decide, e o
        # gerador do DeepStream RECUSA gerar sem valor em vez de assumir taxa
        # cheia, que satura a GPU em silêncio.
        fps_target_map = {
            str(cam["id"]): int(cam["fps_target"])
            for cam in active_with_channel
            if cam.get("fps_target") is not None
        }
        edge_config_cache.write_channel_map(
            self._cache_path, channel_map, config_version, collection_subtype_map,
            fps_target_map,
        )

    # ── main loop ────────────────────────────────────────────────────────────

    def run(self, stop_event: threading.Event) -> None:
        """Poll config continuously until *stop_event* is set."""
        while not stop_event.is_set():
            self._poll_once()
            stop_event.wait(timeout=self._interval)
