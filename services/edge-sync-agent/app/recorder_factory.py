"""Selects and configures the real RecorderClient for this edge process.

Config source decision (task-091, revisited ADR-0058): RECORDER_PROTOCOL/
HOST/PORT/USERNAME/PASSWORD (the gravador's connection secret) stay in
environment variables local to the edge device — NOT pulled from the
cloud's `public.recorders` table (infra/migrations/099_recorders.sql, a
different consumer: the WS-B1 cloud-side training-frame extraction flow,
ADR-0034) and NEVER placed on GET /api/v1/edge/config/poll (ADR-0057:
segredo não entra na config/poll).

The camera_id→channel MAP is different: it's not a secret (just which
integer channel on the gravador each camera_id corresponds to — the SAME
`cameras.channel` column config/poll already returns for RTSP-URL
construction, infra/migrations/002_cameras.sql), and task-091 explicitly
deferred wiring it through config/poll as "a larger cross-cutting change
out of scope for this task". ADR-0058 (fatia mínima) closes that gap:
`resolve_channel_map()` below prefers the map cached locally by
`ConfigPoller` from the cloud (`edge_config_cache.py` — written on every
successful poll) and falls back to RECORDER_CHANNEL_MAP in .env only
when the cache is absent (cold start / cloud hasn't ever delivered a
config yet — the compat/transition path). Whichever source wins is logged
so a technician reading device logs can tell which one was used.

One recorder per site is assumed (ADR-0045: the gravador IS the evidence
source for the site) — no support here for multiple recorders per edge
process.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from . import edge_config_cache
from .onvif_recorder_client import OnvifRecorderClient
from .recorder_client import RecorderClient, RecorderError
from .rtsp_timestamp_recorder_client import RtspTimestampRecorderClient

logger = logging.getLogger(__name__)

# Cascata da ADR-0034 restrita ao que este processo sabe falar hoje: ONVIF
# Profile G (caminho aberto/preferido) e o fallback RTSP-com-timestamp
# (Dahua/Intelbras). Hikvision ISAPI existe no monolito (WS-B1) mas não foi
# portado aqui — nenhum cliente RVB usa Hikvision (CLAUDE.md); adicionar
# quando houver necessidade real, não especulativamente.
_RTSP_FALLBACK_PROTOCOLS = frozenset({"dahua", "intelbras", "rtsp"})
_SUPPORTED_PROTOCOLS = frozenset({"onvif"} | _RTSP_FALLBACK_PROTOCOLS)


def build_recorder_client(
    protocol: str,
    host: str,
    port: int,
    username: str,
    password: str,
    channel_map: dict[str, int],
    http_client: Any = None,
    stream_subtype: int = 0,
    collection_subtype_overrides: dict[str, int] | None = None,
    cgi_port: int = 80,
) -> RecorderClient:
    """Resolves a concrete RecorderClient for *protocol*.

    Raises RecorderError for an unsupported/unmapped protocol — no silent
    fallback to a default client (ADR-0017 discipline: a misconfigured
    protocol string must fail loud, same as NotConfiguredRecorderClient does
    for "no client at all").

    RECORDER_PROTOCOL=onvif additionally REQUIRES username/password: unlike
    the RTSP fallback clients (some gravadores accept anonymous RTSP),
    OnvifRecorderClient's every SOAP request carries a WS-Security
    UsernameToken/PasswordDigest — there is no anonymous mode. Turning the
    flag on without credentials used to build a client that silently sent
    zero auth and failed obscurely at first real request; this now fails at
    boot instead, with a message that says exactly what to configure.

    collection_subtype_overrides (eixo COLETA, migration 114): repassado SÓ
    ao RtspTimestampRecorderClient — ONVIF (OnvifRecorderClient) não tem um
    conceito equivalente de subtype aqui, protocolo em produção no box da
    RVB é 'intelbras' (RTSP fallback).

    cgi_port (eixo EVIDÊNCIA): idem, só o RtspTimestampRecorderClient — é a
    porta do CGI HTTP pela qual a evidência é ancorada no instante da
    detecção (`capture_frame_at`). ONVIF ancoraria por outro caminho
    (Range: clock= no PLAY do replay), não implementado: nenhum cliente usa
    ONVIF hoje, e escrever sem hardware para validar é o que já produziu a
    lacuna que este PR fechou.
    """
    normalized = (protocol or "").strip().lower()
    if normalized not in _SUPPORTED_PROTOCOLS:
        raise RecorderError(
            f"protocolo de gravador não suportado: {protocol!r}. "
            f"Suportados: {', '.join(sorted(_SUPPORTED_PROTOCOLS))}."
        )

    if normalized == "onvif":
        if not username or not password:
            raise RecorderError(
                "RECORDER_PROTOCOL=onvif exige RECORDER_USERNAME e RECORDER_PASSWORD "
                "não vazios — ONVIF Profile G autentica todo request via WS-Security "
                "UsernameToken/PasswordDigest, sem modo anônimo. Configure ambos antes "
                "de ligar a flag (sem fallback silencioso, ADR-0017)."
            )
        return OnvifRecorderClient(
            host=host,
            port=port,
            username=username,
            password=password,
            channel_map=channel_map,
            http_client=http_client,
        )

    return RtspTimestampRecorderClient(
        host=host,
        port=port,
        username=username,
        password=password,
        channel_map=channel_map,
        stream_subtype=stream_subtype,
        collection_subtype_overrides=collection_subtype_overrides,
        cgi_port=cgi_port,
    )


def validate_onvif_boot_or_raise(client: RecorderClient) -> None:
    """ADR-0052 hardware-validation follow-up: a SINGLE liveness+auth check at
    boot for OnvifRecorderClient, no retry.

    Why single-shot, no retry: the gravador is a real device (Intelbras iNVD
    3032 at RVB) with anti-brute-force lockout on repeated failed-auth
    attempts (CLAUDE.md's edge network section — cameras "travam por lockout
    anti-brute-force" if hammered). A retry loop here would be exactly the
    kind of hammering that trips it. One call — `client.health()`, which
    itself issues exactly one GetSystemDateAndTime SOAP request — either
    succeeds or boot aborts with a clear message; a human fixes the config
    and restarts, nothing here auto-retries into a lockout.

    No-op for any RecorderClient that isn't OnvifRecorderClient — the RTSP
    fallback clients have no equivalent one-shot auth probe worth gating boot
    on here (their errors already surface per-request, unchanged by this).
    """
    if not isinstance(client, OnvifRecorderClient):
        return
    health = client.health()
    if not health.reachable:
        raise RecorderError(
            "onvif_boot_auth_check_failed — a validação única de autenticação ONVIF "
            f"no boot falhou (detail={health.detail!r}). Verifique RECORDER_HOST/"
            "RECORDER_PORT/RECORDER_USERNAME/RECORDER_PASSWORD e a conectividade com "
            "o gravador antes de religar RECORDER_PROTOCOL=onvif. Sem retentativa "
            "automática por design — o gravador pode aplicar lockout anti-brute-force "
            "a tentativas repetidas de autenticação (CLAUDE.md)."
        )


def resolve_channel_map(source: dict[str, str]) -> tuple[dict[str, int], str, str]:
    """ADR-0058: resolves camera_id->channel, preferring the cloud-polled cache
    over RECORDER_CHANNEL_MAP in .env — the box's compat/transition path.

    Returns (channel_map, source_label, config_version):
      - source_label == "cloud_config": `EDGE_CONFIG_CACHE_PATH` (or the
        default) exists and parsed OK — cloud is authoritative, even if the
        map inside happens to be empty (a real "site has zero cameras" is
        NOT the same as "cloud hasn't answered yet", and silently falling
        back to a stale .env in that case would reintroduce exactly the
        divergence this ADR closes). `config_version` is the hash the cloud
        sent with that config (empty string for pre-F1 cloud responses).
      - source_label == "env": no usable cache file — RECORDER_CHANNEL_MAP
        parsed from *source* (raises RecorderError if present but malformed:
        that's a real misconfiguration, fail loud per ADR-0017). Blank/absent
        RECORDER_CHANNEL_MAP is NOT an error here — it's simply empty.
      - source_label == "none": neither source had anything — callers decide
        whether that's fatal (it is, today, for both consumers).
    """
    cache_path = source.get("EDGE_CONFIG_CACHE_PATH") or edge_config_cache.DEFAULT_CACHE_PATH
    cached = edge_config_cache.read_channel_map(cache_path)
    if cached is not None:
        logger.info(
            "recorder_channel_map_source=cloud_config path=%s config_version=%s cameras=%d",
            cache_path, cached.config_version, len(cached.channel_map),
        )
        return cached.channel_map, "cloud_config", cached.config_version

    raw = source.get("RECORDER_CHANNEL_MAP", "")
    if not raw.strip():
        return {}, "none", ""

    channel_map = _parse_channel_map(raw)
    logger.info(
        "recorder_channel_map_source=env cameras=%d "
        "(config polled da nuvem ainda não disponível em %s)",
        len(channel_map), cache_path,
    )
    return channel_map, "env", ""


def resolve_collection_subtype_overrides(source: dict[str, str]) -> dict[str, int]:
    """ADR-0058-style: eixo COLETA (migration 114) — lê o MESMO cache local
    (`EDGE_CONFIG_CACHE_PATH` ou o default) que já entrega o channel_map, e
    devolve `collection_subtype_map` (camera_id -> 0=principal, 1=substream).

    Sem cache disponível (cold start / nuvem nunca respondeu) -> {} — não é
    um erro aqui (diferente de channel_map, que é obrigatório): o coletor
    cai para RECORDER_STREAM_SUBTYPE global (RtspTimestampRecorderClient),
    o mesmo comportamento pré-114.
    """
    cache_path = source.get("EDGE_CONFIG_CACHE_PATH") or edge_config_cache.DEFAULT_CACHE_PATH
    cached = edge_config_cache.read_channel_map(cache_path)
    if cached is None:
        return {}
    return cached.collection_subtype_map


def build_recorder_client_from_env(env: dict[str, str] | None = None) -> RecorderClient:
    """Reads RECORDER_* env vars and builds the configured RecorderClient.

    Required: RECORDER_PROTOCOL, RECORDER_HOST, RECORDER_PORT.
    Optional: RECORDER_USERNAME, RECORDER_PASSWORD (default ""),
    RECORDER_STREAM_SUBTYPE (default "0" — Dahua/Intelbras main stream;
    "1" selects the sub stream, lighter for training-frame collection than
    the main/high-res stream; RtspTimestampRecorderClient only, ONVIF has no
    equivalent concept here).
    channel_map (camera_id -> canal do gravador): resolvido por
    `resolve_channel_map()` — cloud-polled config quando disponível
    (ADR-0058), senão RECORDER_CHANNEL_MAP (JSON '{"cam-id": 1, ...}') no
    .env. Nenhuma fonte disponível -> RecorderError (no silent empty map:
    every camera_id lookup would then fail anyway, but failing here gives a
    clearer error at startup instead of at first request).

    collection_subtype_overrides (camera_id -> 0=principal/1=substream, eixo
    COLETA, migration 114): resolvido por `resolve_collection_subtype_overrides()`
    do MESMO cache local do channel_map. Câmera sem override cai para
    RECORDER_STREAM_SUBTYPE (o `stream_subtype` acima, global). Diferente do
    channel_map, ausência de cache aqui NÃO é fatal — {} preserva o
    comportamento pré-114 (subtype global para todas as câmeras).
    """
    source = env if env is not None else os.environ

    protocol = source.get("RECORDER_PROTOCOL", "")
    host = source.get("RECORDER_HOST", "")
    port_raw = source.get("RECORDER_PORT", "")
    if not protocol or not host or not port_raw:
        raise RecorderError(
            "RECORDER_PROTOCOL, RECORDER_HOST e RECORDER_PORT são obrigatórios "
            "para configurar o RecorderClient real (task-091) — sem fallback silencioso."
        )
    try:
        port = int(port_raw)
    except ValueError as exc:
        raise RecorderError(f"RECORDER_PORT inválido: {port_raw!r}") from exc

    username = source.get("RECORDER_USERNAME", "")
    password = source.get("RECORDER_PASSWORD", "")

    channel_map, channel_map_source, _config_version = resolve_channel_map(source)
    if channel_map_source == "none":
        raise RecorderError(
            "Nenhum mapa canal→câmera disponível: nem config polled da nuvem "
            "(GET /api/v1/edge/config/poll, ADR-0058) nem RECORDER_CHANNEL_MAP "
            "no .env — sem fallback silencioso."
        )

    stream_subtype_raw = source.get("RECORDER_STREAM_SUBTYPE", "0")
    try:
        stream_subtype = int(stream_subtype_raw)
    except ValueError as exc:
        raise RecorderError(f"RECORDER_STREAM_SUBTYPE inválido: {stream_subtype_raw!r}") from exc

    # Porta do CGI HTTP (eixo EVIDÊNCIA): é por ela que a evidência é ancorada
    # no INSTANTE da detecção em vez de ser "o agora". 0 desliga e toda captura
    # volta ao vivo — o botão de rollback sem OTA, num box em produção.
    cgi_port_raw = source.get("RECORDER_CGI_PORT", "80")
    try:
        cgi_port = int(cgi_port_raw)
    except ValueError as exc:
        raise RecorderError(f"RECORDER_CGI_PORT inválido: {cgi_port_raw!r}") from exc

    collection_subtype_overrides = resolve_collection_subtype_overrides(source)

    return build_recorder_client(
        protocol=protocol,
        host=host,
        port=port,
        username=username,
        password=password,
        channel_map=channel_map,
        stream_subtype=stream_subtype,
        collection_subtype_overrides=collection_subtype_overrides,
        cgi_port=cgi_port,
    )


def _parse_channel_map(raw: str) -> dict[str, int]:
    """Parses a non-blank RECORDER_CHANNEL_MAP string. Caller (resolve_channel_map)
    handles the blank case — this only ever sees non-blank input, so any
    failure here is a genuine misconfiguration (fail loud, ADR-0017)."""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RecorderError(f"RECORDER_CHANNEL_MAP não é JSON válido: {exc}") from exc
    if not isinstance(parsed, dict):
        raise RecorderError("RECORDER_CHANNEL_MAP deve ser um objeto JSON {camera_id: canal}")
    try:
        return {str(k): int(v) for k, v in parsed.items()}
    except (TypeError, ValueError) as exc:
        raise RecorderError(f"RECORDER_CHANNEL_MAP tem valor de canal inválido: {exc}") from exc
