"""
DOMAIN camera_service.py — Camera CRUD, RTSP/HTTP URL construction, and stream control.

Layer: domain
Pattern: Service (framework-agnostic)

Key exports:
  - CameraService.create_camera: validates required fields, Fernet-encrypts password, persists via CameraRepository
  - CameraService.list_cameras: admin sees all cameras, operators see only their own
  - CameraService.get_camera: fetches by UUID, strips password_encrypted from response
  - CameraService.build_rtsp_url: constructs manufacturer-specific RTSP URL (Hikvision/Intelbras/Dahua/generic),
    runs RTSPUrlValidator before returning
  - CameraService.build_stream_url: selects HTTP/ISAPI for Hikvision on non-554 ports, falls back to build_rtsp_url
  - CameraService.update_camera / delete_camera: enforce user ownership or admin bypass
  - CameraService.record_test_result: best-effort persistence of connectivity test outcome

Constraints:
  - CAMERA_SECRET_KEY env var must be set; Fernet key must be 32 url-safe base64 bytes
  - Passwords are never returned in any response — pop password_encrypted before returning dicts
  - All URL construction passes through RTSPUrlValidator.validate before being used

Related: app/core/validators.py, app/infrastructure/database/repositories/camera_repository.py
"""
import logging
from typing import Optional
from uuid import UUID

from cryptography.fernet import Fernet

from app.core.exceptions import (
    NotFoundError,
    ValidationError,
)
from app.core.validators import RTSPUrlValidator
from app.infrastructure.database.repositories.camera_repository import CameraRepository

logger = logging.getLogger(__name__)


class CameraService:
    """Use cases de câmeras IP."""

    def __init__(
        self,
        camera_repo: CameraRepository,
        fernet_key: str,
    ) -> None:
        self._camera_repo = camera_repo
        self._fernet = Fernet(fernet_key.encode()) if fernet_key else None

    def _encrypt_password(self, password: str) -> str:
        """Criptografa senha com Fernet."""
        if not self._fernet:
            raise ValidationError("CAMERA_SECRET_KEY não configurada")
        return self._fernet.encrypt(password.encode()).decode()

    _VALID_CODECS: frozenset[str] = frozenset({"h264", "h265"})
    # 0 = stream principal; 1 = substream (ver manufacturer_profiles.py e
    # migration 092_camera_live_view_subtype.sql) — nenhum outro valor
    # corresponde a stream real de nenhum fabricante suportado.
    _VALID_SUBTYPES: frozenset[int] = frozenset({0, 1})

    def _validate_hardening_fields(self, data: dict) -> None:
        """Valida faixa dos campos numéricos/enum da câmera (mutirão 2.6).

        Cobre: video_codec, max_auth_failures, detection_stream_url
        (hardening, task-041) e port/channel/subtype/live_view_subtype
        (streaming). Nenhum destes últimos tinha validação de faixa: a coluna
        é INTEGER e aceita qualquer valor, então port=0, channel=-1 ou
        subtype=7 eram gravados sem erro — a URL RTSP resultante era inválida
        (ou apontava para stream inexistente), o FFmpeg conectava a lugar
        nenhum e o coletor não via erro algum (zero frame, zero erro).
        """
        video_codec = data.get("video_codec")
        if video_codec is not None and video_codec not in self._VALID_CODECS:
            raise ValidationError(
                f"video_codec '{video_codec}' inválido — aceitos: h264, h265 (ou null)"
            )

        max_auth_failures = data.get("max_auth_failures")
        if max_auth_failures is not None:
            if not isinstance(max_auth_failures, int) or max_auth_failures < 1:
                raise ValidationError(
                    "max_auth_failures deve ser inteiro >= 1"
                )

        detection_stream_url = data.get("detection_stream_url")
        if detection_stream_url:
            RTSPUrlValidator.validate(detection_stream_url)

        port = data.get("port")
        if port is not None:
            if isinstance(port, bool) or not isinstance(port, int) or not (1 <= port <= 65535):
                raise ValidationError("port deve ser inteiro entre 1 e 65535")

        channel = data.get("channel")
        if channel is not None:
            if isinstance(channel, bool) or not isinstance(channel, int) or not (1 <= channel <= 64):
                raise ValidationError("channel deve ser inteiro entre 1 e 64")

        subtype = data.get("subtype")
        if subtype is not None:
            if (
                isinstance(subtype, bool)
                or not isinstance(subtype, int)
                or subtype not in self._VALID_SUBTYPES
            ):
                raise ValidationError(
                    f"subtype inválido. Valores aceitos: {sorted(self._VALID_SUBTYPES)} "
                    "(0 = principal, 1 = substream)"
                )

        live_view_subtype = data.get("live_view_subtype")
        if live_view_subtype is not None:
            if (
                isinstance(live_view_subtype, bool)
                or not isinstance(live_view_subtype, int)
                or live_view_subtype not in self._VALID_SUBTYPES
            ):
                raise ValidationError(
                    f"live_view_subtype inválido. Valores aceitos: "
                    f"{sorted(self._VALID_SUBTYPES)} (0 = principal, 1 = substream)"
                )

    def _decrypt_password(self, encrypted: str) -> str:
        """Descriptografa senha com Fernet."""
        if not self._fernet or not encrypted:
            return ""
        try:
            return self._fernet.decrypt(encrypted.encode()).decode()
        except Exception:
            return ""

    def create_camera(
        self, user_id: UUID, data: dict, created_by: Optional[UUID] = None
    ) -> dict:
        """Cria câmera IP. Criptografa senha antes de salvar.

        user_id: escopo do tenant (tenant_id do JWT) — grava em tenant_id.
        created_by: usuário autenticado — grava em public.cameras.user_id (NOT NULL).
        """
        if not data.get("name") or not data.get("host"):
            raise ValidationError("name e host são obrigatórios")

        self._validate_hardening_fields(data)

        camera_data = {
            "tenant_id": user_id,
            "user_id": created_by or user_id,
            "name": data["name"],
            "location": data.get("location"),
            "description": data.get("description"),
            "manufacturer": data.get("manufacturer", "generic"),
            "host": data["host"],
            "port": data.get("port", 554),
            "username": data.get("username", "admin"),
            "channel": data.get("channel", 1),
            "subtype": data.get("subtype", 0),
            # task-067: campo independente do `subtype` de detecção/inferência —
            # câmera nova nasce com o live view apontando pro substream (baixa
            # latência). NÃO reaproveitar `subtype` aqui (ver migration 092).
            "live_view_subtype": data.get("live_view_subtype", 1),
            "detection_stream_url": data.get("detection_stream_url"),
            "video_codec": data.get("video_codec"),
            "max_auth_failures": data.get("max_auth_failures", 5),
            # Sem isto a câmera nasce órfã de site e fica INVISÍVEL pro edge:
            # config_poll (list_for_site_config), sum_fps_demand, stream_info
            # (deployment_mode) e live-view/wanted filtram todos por site_id.
            # A coluna existia e era lida em vários caminhos, mas nenhuma rota
            # sabia gravá-la — encontrado ao ligar o live view na RVB.
            "site_id": data.get("site_id"),
        }

        if data.get("password"):
            camera_data["password_encrypted"] = self._encrypt_password(
                data["password"]
            )

        camera = self._camera_repo.create(camera_data)
        camera["id"] = str(camera["id"])
        return camera

    def list_cameras(self, user_id: UUID, is_admin: bool = False) -> list[dict]:
        """Lista câmeras. Admin vê todas, operator vê as suas."""
        if is_admin:
            cameras = self._camera_repo.get_all()
        else:
            cameras = self._camera_repo.get_by_user(user_id)

        for cam in cameras:
            cam["id"] = str(cam["id"])
        return cameras

    def get_camera(self, camera_id: UUID) -> dict:
        """Busca câmera por ID (sem senha)."""
        camera = self._camera_repo.get_by_id(camera_id)
        if not camera:
            raise NotFoundError("Câmera", str(camera_id))
        camera["id"] = str(camera["id"])
        camera.pop("password_encrypted", None)
        return camera

    def build_rtsp_url(
        self,
        camera_id: UUID,
        user_id: UUID,
        is_admin: bool = False,
        subtype_override: Optional[int] = None,
    ) -> str:
        """Constrói URL RTSP da câmera. Valida permissão.

        subtype_override: usado internamente por build_stream_url (live view /
        fallback) para forçar um subtype específico sem tocar no `subtype`
        bruto da câmera. None (default) preserva o comportamento histórico —
        callers diretos (ex.: testes de conectividade) continuam usando o
        subtype configurado pelo operador.
        """
        camera = self._camera_repo.get_by_id(camera_id)
        if not camera:
            raise NotFoundError("Câmera", str(camera_id))

        # `user_id` é, de fato, o tenant_id do contexto — todos os handlers
        # passam get_tenant_id(). O nome do parâmetro mentia e a comparação
        # tratava os dois como a mesma coisa, negando SEMPRE para não-admin.
        # Mesmo defeito corrigido em delete_camera (#392); estes ficaram.
        # Cross-tenant → 404, nunca 403 (C-01 — não vazar existência).
        if str(camera["tenant_id"]) != str(user_id) and not is_admin:
            raise NotFoundError("Câmera", str(camera_id))

        if camera.get("rtsp_url_override"):
            url = camera["rtsp_url_override"]
        else:
            from urllib.parse import quote as _quote  # noqa: PLC0415
            password = self._decrypt_password(camera.get("password_encrypted", ""))
            safe_user = _quote(str(camera.get("username", "")), safe="")
            safe_pass = _quote(password, safe="")
            base = f"rtsp://{safe_user}:{safe_pass}@{camera['host']}:{camera['port']}"

            manufacturer = (camera.get("manufacturer") or "generic").lower()
            channel = camera.get("channel", 1)
            # 1 = substream (H.264, lower res); 0 = main 1080p
            subtype = subtype_override if subtype_override is not None else camera.get("subtype", 1)

            if manufacturer == "hikvision":
                # Hikvision: /Streaming/Channels/{channel}0{subtype+1}
                stream_id = f"{channel}0{subtype + 1}"
                url = f"{base}/Streaming/Channels/{stream_id}"
            elif manufacturer in ("intelbras", "dahua"):
                url = f"{base}/cam/realmonitor?channel={channel}&subtype={subtype}"
            else:
                url = f"{base}/stream1"

        RTSPUrlValidator.validate(url)
        return url

    @staticmethod
    def _resolve_live_view_subtype(camera: dict) -> int:
        """Resolve o subtype a usar no LIVE VIEW (task-067).

        Prefere `live_view_subtype` (campo independente, migration 092,
        default 1 = substream). Se a linha não tiver o campo (None — legado ou
        mock antigo), cai pro `subtype` histórico como fallback defensivo.

        NUNCA usado pelo teste de conectividade (/api/cameras/test) nem por
        qualquer caminho de detecção/inferência — apenas por build_stream_url
        quando for_live_view=True e por build_stream_url_for_lazy_start.
        """
        live_view_subtype = camera.get("live_view_subtype")
        if live_view_subtype is None:
            return camera.get("subtype", 0)
        return live_view_subtype

    def build_stream_url(
        self,
        camera_id: UUID,
        user_id: UUID,
        is_admin: bool = False,
        for_live_view: bool = False,
        subtype_override: Optional[int] = None,
    ) -> str:
        """Build best available stream URL. RTSP for port 554, HTTP/ISAPI for Hikvision on other ports.

        for_live_view: quando True, resolve o subtype via `live_view_subtype`
        (task-067) em vez do `subtype` bruto. Usado pelo entry point explícito
        do live view (POST /stream/start). O teste de conectividade
        (/api/cameras/test) NÃO passa esse argumento — continua testando o
        subtype configurado pelo operador, comportamento inalterado.
        subtype_override: força um subtype explícito (ex.: 0 para computar a
        URL do stream principal como fallback), tem precedência sobre
        for_live_view quando ambos são passados.
        """
        camera = self._camera_repo.get_by_id(camera_id)
        if not camera:
            raise NotFoundError("Câmera", str(camera_id))

        # `user_id` é, de fato, o tenant_id do contexto — todos os handlers
        # passam get_tenant_id(). O nome do parâmetro mentia e a comparação
        # tratava os dois como a mesma coisa, negando SEMPRE para não-admin.
        # Mesmo defeito corrigido em delete_camera (#392); estes ficaram.
        # Cross-tenant → 404, nunca 403 (C-01 — não vazar existência).
        if str(camera["tenant_id"]) != str(user_id) and not is_admin:
            raise NotFoundError("Câmera", str(camera_id))

        # Override takes priority (supports any validated scheme)
        if camera.get("rtsp_url_override"):
            url = camera["rtsp_url_override"]
            RTSPUrlValidator.validate(url)
            return url

        if subtype_override is None and for_live_view:
            subtype_override = self._resolve_live_view_subtype(camera)

        port = camera.get("port", 554)
        manufacturer = (camera.get("manufacturer") or "generic").lower()

        # Hikvision on non-554 port → HTTP/ISAPI
        if port != 554 and manufacturer == "hikvision":
            from urllib.parse import quote as _quote  # noqa: PLC0415
            password = self._decrypt_password(camera.get("password_encrypted", ""))
            safe_user = _quote(str(camera.get("username", "")), safe="")
            safe_pass = _quote(password, safe="")
            channel = camera.get("channel", 1)
            # 1 = substream (H.264, lower res); 0 = main 1080p
            subtype = subtype_override if subtype_override is not None else camera.get("subtype", 1)
            stream_id = f"{channel}0{subtype + 1}"
            url = (
                f"http://{safe_user}:{safe_pass}@{camera['host']}:{port}"
                f"/ISAPI/Streaming/channels/{stream_id}/httpPreview"
            )
            RTSPUrlValidator.validate(url)
            return url

        # Default: existing RTSP logic
        return self.build_rtsp_url(camera_id, user_id, is_admin, subtype_override=subtype_override)

    def build_stream_url_for_lazy_start(
        self, camera_id: UUID, subtype_override: Optional[int] = None
    ) -> str:
        """Return RTSP/HTTP URL for a camera WITHOUT ownership check.

        Intentionally unauthenticated: serve_hls is already unauthenticated
        (hls.js cannot send Authorization headers). The camera_id UUID was
        originally obtained by the frontend via a JWT-authenticated request,
        so the caller has already proved access. Only the re-check is skipped.

        task-067: this is the real lazy-start path used by serve_hls (GET
        .m3u8). It always resolves the LIVE VIEW subtype (`live_view_subtype`,
        default 1 = substream), never the raw `subtype` used for
        detection/recording. Pass subtype_override=0 to cheaply build the
        "main stream" URL (string-only, no I/O) for runtime fallback.
        """
        camera = self._camera_repo.get_by_id(camera_id)
        if not camera:
            raise NotFoundError("Câmera", str(camera_id))

        if camera.get("rtsp_url_override"):
            url = camera["rtsp_url_override"]
            RTSPUrlValidator.validate(url)
            return url

        from urllib.parse import quote as _quote  # noqa: PLC0415
        password = self._decrypt_password(camera.get("password_encrypted", ""))
        safe_user = _quote(str(camera.get("username", "")), safe="")
        safe_pass = _quote(password, safe="")
        port = camera.get("port", 554)
        manufacturer = (camera.get("manufacturer") or "generic").lower()
        channel = camera.get("channel", 1)
        subtype = (
            subtype_override
            if subtype_override is not None
            else self._resolve_live_view_subtype(camera)
        )

        if port != 554 and manufacturer == "hikvision":
            stream_id = f"{channel}0{subtype + 1}"
            url = (
                f"http://{safe_user}:{safe_pass}@{camera['host']}:{port}"
                f"/ISAPI/Streaming/channels/{stream_id}/httpPreview"
            )
        elif manufacturer == "hikvision":
            stream_id = f"{channel}0{subtype + 1}"
            url = f"rtsp://{safe_user}:{safe_pass}@{camera['host']}:{port}/Streaming/Channels/{stream_id}"
        elif manufacturer in ("intelbras", "dahua"):
            url = (
                f"rtsp://{safe_user}:{safe_pass}@{camera['host']}:{port}"
                f"/cam/realmonitor?channel={channel}&subtype={subtype}"
            )
        else:
            url = f"rtsp://{safe_user}:{safe_pass}@{camera['host']}:{port}/stream1"

        RTSPUrlValidator.validate(url)
        return url

    def update_camera(self, camera_id: UUID, user_id: UUID, data: dict, is_admin: bool = False) -> dict:
        """Atualiza câmera. Valida permissão e re-encripta senha se fornecida."""
        camera = self._camera_repo.get_by_id(camera_id)
        if not camera:
            raise NotFoundError("Câmera", str(camera_id))

        # `user_id` é, de fato, o tenant_id do contexto — todos os handlers
        # passam get_tenant_id(). O nome do parâmetro mentia e a comparação
        # tratava os dois como a mesma coisa, negando SEMPRE para não-admin.
        # Mesmo defeito corrigido em delete_camera (#392); estes ficaram.
        # Cross-tenant → 404, nunca 403 (C-01 — não vazar existência).
        if str(camera["tenant_id"]) != str(user_id) and not is_admin:
            raise NotFoundError("Câmera", str(camera_id))

        self._validate_hardening_fields(data)

        update_data: dict = {}
        for field in (
            "name", "location", "description", "manufacturer",
            "host", "port", "username", "channel", "subtype", "live_view_subtype",
            "rtsp_url_override", "is_active",
            "detection_stream_url", "video_codec", "max_auth_failures",
            # site_id também no update: permite associar/mover câmera já
            # cadastrada para um site sem recriá-la (ver create_camera).
            "site_id",
            # position_confirmed (migration 113/D-85): vira TRUE só por ação
            # humana, depois de conferir na fábrica que o canal mostra o lugar
            # que o nome diz. Precisa existir AQUI e na allow-list do
            # CameraRepository.update — campo só num dos dois é descartado em
            # silêncio (cicatriz do site_id, comentário lá).
            "position_confirmed",
            # collection_subtype: eixo COLETA (frame de treino), independente
            # do eixo OPERAÇÃO acima (fps_target/quality_preset/
            # live_view_subtype) — migration 114.
            "collection_subtype",
        ):
            if field in data:
                update_data[field] = data[field]

        if data.get("password"):
            update_data["password_encrypted"] = self._encrypt_password(data["password"])

        if not update_data:
            raise ValidationError("Nenhum campo para atualizar")

        updated = self._camera_repo.update(camera_id, update_data)
        if updated:
            updated["id"] = str(updated["id"])
            updated.pop("password_encrypted", None)
        return updated  # type: ignore[return-value]

    _VALID_FPS = {1, 5, 10, 15, 30}
    _VALID_QUALITY = {"low", "medium", "high"}
    # Eixo COLETA (frame de treino) — independente de _VALID_SUBTYPES (que
    # hoje só é usado por live_view_subtype/subtype, eixo OPERAÇÃO). Mesmos
    # dois valores possíveis (0=principal, 1=substream) por coincidência de
    # domínio, não porque são o mesmo campo.
    _VALID_COLLECTION_SUBTYPE = {0, 1}

    def patch_config(
        self,
        camera_id: UUID,
        tenant_id: UUID,
        fps_target: Optional[int] = None,
        quality_preset: Optional[str] = None,
        collection_subtype: Optional[int] = None,
        is_admin: bool = False,
    ) -> dict:
        """Atualiza fps_target, quality_preset e/ou collection_subtype da
        câmera (PATCH parcial).

        Permissão escopada pelo tenant do JWT (não pelo user_id — fix da mesma
        classe do commit f6df666): a câmera deve pertencer ao tenant do token,
        com override para admin/superadmin.
        Pelo menos um dos três campos deve ser fornecido.
        """
        if fps_target is None and quality_preset is None and collection_subtype is None:
            raise ValidationError(
                "Informe fps_target, quality_preset e/ou collection_subtype"
            )
        if fps_target is not None and fps_target not in self._VALID_FPS:
            raise ValidationError(
                f"fps_target inválido. Valores aceitos: {sorted(self._VALID_FPS)}"
            )
        if quality_preset is not None and quality_preset not in self._VALID_QUALITY:
            raise ValidationError(
                f"quality_preset inválido. Valores aceitos: {sorted(self._VALID_QUALITY)}"
            )
        if (
            collection_subtype is not None
            and collection_subtype not in self._VALID_COLLECTION_SUBTYPE
        ):
            raise ValidationError(
                "collection_subtype inválido. Valores aceitos: "
                f"{sorted(self._VALID_COLLECTION_SUBTYPE)}"
            )

        camera = self._camera_repo.get_by_id(camera_id)
        if not camera:
            raise NotFoundError("Câmera", str(camera_id))

        # Cross-tenant responde 404, nunca 403 (C-01 — não vazar existência).
        # Irmão de delete_camera, corrigido no #392; este ficou para trás.
        if str(camera["tenant_id"]) != str(tenant_id) and not is_admin:
            raise NotFoundError("Câmera", str(camera_id))

        updated = self._camera_repo.update_config(
            camera_id,
            str(camera["tenant_id"]),
            fps_target,
            quality_preset,
            collection_subtype,
        )
        if not updated:
            raise NotFoundError("Câmera", str(camera_id))
        updated["id"] = str(updated["id"])
        updated.pop("password_encrypted", None)
        return updated

    def record_test_result(self, camera_id: UUID, error: str | None) -> None:
        """Persiste resultado do último teste de conectividade (best-effort)."""
        try:
            self._camera_repo.update_last_tested(camera_id, error)
        except Exception:
            pass  # Não bloquear resposta por falha no registro

    def delete_camera(self, camera_id: UUID, tenant_id: UUID, is_admin: bool = False) -> None:
        """Exclui a câmera do sistema. Valida posse por TENANT.

        Exclusão LÓGICA (`cameras.deleted_at`, migration 138), não DELETE
        físico — ver `CameraRepository.soft_delete` para o porquê. O efeito
        para quem usa o produto é o pedido: a câmera some da lista, do Ao
        Vivo, do grid, dos seletores e do config que o edge baixa. O que
        NÃO some: alerta, evidência gravada e frame de treino já anotado.
        Não há desfazer pela tela — quem quiser a câmera de volta cadastra
        de novo (é por isso que `archive_camera` continua existindo, para
        quem quer o reversível).

        O parâmetro sempre foi o tenant_id do contexto (ver o handler em
        cameras/crud_handlers.py), mas o nome antigo dizia `user_id` e a
        comparação era `camera["tenant_id"] != user_id` — dois identificadores
        de entidades diferentes, então para qualquer não-admin dava sempre
        "Sem permissão". Era esse o erro ao tentar remover câmera.

        Câmera de outro tenant responde 404, nunca 403 (C-01 — não vazar
        existência). O override por `is_admin` é preservado como estava.
        """
        camera = self._camera_repo.get_by_id(camera_id)
        if not camera:
            raise NotFoundError("Câmera", str(camera_id))

        if str(camera["tenant_id"]) != str(tenant_id) and not is_admin:
            raise NotFoundError("Câmera", str(camera_id))

        self._camera_repo.soft_delete(camera_id)

    def archive_camera(self, camera_id: UUID, tenant_id: UUID) -> dict:
        """Arquiva câmera (is_active=False) — reversível, nunca apaga linha.

        É o caminho correto para tirar do reconhecimento uma câmera que não
        faz parte dele: o DELETE real leva junto alertas, eventos, sessões de
        contagem e operações por CASCADE, e trava por FK quando a câmera já
        tem frames de treino.

        NÃO apaga nem tira frame algum do treino (task B4, corrige claim
        falsa deste docstring): a fila de Classificar, a galeria de Anotar e
        o export de dataset (versioning_v2._snapshot_labeled_frames/
        _fetch_annotations) servem e exportam normalmente o material já
        minerado/anotado de câmera arquivada — descartar veredito humano já
        dado (ou impedir que ele aconteça) não protege o modelo de nada, só
        desperdiça o trabalho. Arquivar só impede CAPTURA NOVA dessa câmera
        (ela para de aparecer como fonte ativa de coleta) — nenhuma das três
        superfícies acima filtra mais por `is_active` (rodada 3, mesma causa
        raiz do `is_active` sobrecarregado entre arquivada e rascunho de
        import: ver frame_repository.list_images_filtered).
        """
        camera = self._camera_repo.get_by_id(camera_id)
        if not camera or str(camera["tenant_id"]) != str(tenant_id):
            raise NotFoundError("Câmera", str(camera_id))

        return self._camera_repo.set_active(camera_id, is_active=False)

    def restore_camera(self, camera_id: UUID, tenant_id: UUID) -> dict:
        """Desarquiva câmera (is_active=True) — o inverso de archive_camera."""
        camera = self._camera_repo.get_by_id(camera_id)
        if not camera or str(camera["tenant_id"]) != str(tenant_id):
            raise NotFoundError("Câmera", str(camera_id))

        return self._camera_repo.set_active(camera_id, is_active=True)
