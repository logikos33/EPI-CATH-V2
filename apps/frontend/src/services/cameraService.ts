/**
 * cameraService — camada de acesso à API de câmeras.
 *
 * Mantém um único ponto de chamada HTTP para todas as operações de câmeras.
 * Mapeamento form → API: 'ip' → 'host', 'path' → 'rtsp_url_override'.
 */
import { api } from './api'
import type { Camera } from '../types'
import type { CameraHealthContext } from '../types/edge'

export interface CameraFormData {
  name: string
  ip: string
  port: number
  username: string
  password: string
  path: string
  manufacturer: string
  location?: string
  /** Ativar/arquivar (nunca apaga — só troca a flag). */
  is_active?: boolean
  /** Alguém conferiu presencialmente na fábrica que o canal mostra este lugar (D-85). */
  position_confirmed?: boolean
}

export interface StreamStartResult {
  camera_id: string
  /** URL HLS autorizada pelo backend — tokenizada quando o enforcement está
   * ligado. Nunca montar esta URL no frontend. */
  hls_url: string
  status: string
  dispatch_mode?: string
  rtsp_url_validated?: boolean
}

export interface TestCheck {
  status: 'ok' | 'error' | 'warning' | 'pending'
  message: string
}

export interface TestResult {
  camera_id: string
  success: boolean
  error: string | null
  suggestion: string | null
  checks: {
    url_format: TestCheck
    host_reachable: TestCheck
    port_open: TestCheck
    rtsp_response: TestCheck
    stream_available: TestCheck
  }
}

function formToApiPayload(data: Partial<CameraFormData>): Record<string, unknown> {
  const payload: Record<string, unknown> = {}
  if (data.name !== undefined) payload.name = data.name
  if (data.ip !== undefined) payload.host = data.ip
  if (data.port !== undefined) payload.port = data.port
  if (data.username !== undefined) payload.username = data.username
  if (data.password !== undefined) payload.password = data.password
  if (data.path !== undefined) payload.rtsp_url_override = data.path || null
  if (data.manufacturer !== undefined) payload.manufacturer = data.manufacturer
  if (data.location !== undefined) payload.location = data.location
  if (data.is_active !== undefined) payload.is_active = data.is_active
  if (data.position_confirmed !== undefined) payload.position_confirmed = data.position_confirmed
  return payload
}

/**
 * Constrói preview da URL RTSP para exibir ao usuário (sem fazer requisição).
 * Senha sempre mascarada com ****.
 */
export function buildRtspPreview(data: Partial<CameraFormData>): string {
  if (data.path) return data.path

  const ip = data.ip || '...'
  const port = data.port || 554
  const user = data.username || ''
  const path = getDefaultPath(data.manufacturer || '')

  if (user) {
    return `rtsp://${user}:****@${ip}:${port}${path}`
  }
  return `rtsp://${ip}:${port}${path}`
}

export function getDefaultPath(manufacturer: string): string {
  const paths: Record<string, string> = {
    hikvision: '/Streaming/Channels/101',
    dahua: '/cam/realmonitor?channel=1&subtype=0',
    intelbras: '/cam/realmonitor?channel=1&subtype=1',
    axis: '/axis-media/media.amp',
    samsung: '/profile1/media.smp',
    generic: '/stream',
  }
  return paths[manufacturer?.toLowerCase()] || '/stream'
}

/** Envelope padrão do backend: { status: "success"|"error", data: T } */
type ApiEnvelope<T> = { status: string; data: T }

/** PATCH parcial de config — pelo menos um campo (validado no backend). */
export interface CameraConfigPatch {
  fps_target?: number
  quality_preset?: string
  /** Eixo COLETA (frame de treino, migration 114): 0=principal, 1=substream.
   * Independente de fps_target/quality_preset (eixo OPERAÇÃO). */
  collection_subtype?: number
}

/** Resultado da propagação cloud→edge enfileirada no PATCH /config (aditivo). */
export interface CameraPropagation {
  queued: boolean
  reason: 'no_site' | 'error' | null
}

export type CameraWithPropagation = Camera & { propagation?: CameraPropagation }
type ApiListData = { cameras: Camera[]; gateway_status?: unknown; inference_status?: unknown }

export interface ProbeInput {
  manufacturer: string
  ip_or_host: string
  port?: number
  username?: string
  password?: string
  channel?: number
  is_behind_nat?: boolean
}

export interface ProbeResult {
  ok: boolean | null
  method?: string
  codec?: string | null
  resolution?: string | null
  fps?: number | null
  substream_url_sugerida?: string | null
  gateway_available?: boolean
  warning?: string | null
  error?: string | null
  message?: string
}

/** Estado do último snapshot de uma câmera (Bloco A: miniatura de triagem).
 * "none" = nunca capturado. "pending" = captura em andamento (edge_command
 * disparado, aguardando o box). "ready"/"failed" = resultado final da
 * última tentativa. */
export type CameraSnapshotStatus = 'none' | 'pending' | 'ready' | 'failed'

export interface CameraSnapshotState {
  status: CameraSnapshotStatus
  /** URL presignada (só quando status === 'ready'). */
  url: string | null
  captured_at: string | null
  error_reason: string | null
}

/** Resposta de POST /snapshot/refresh — pode ser um no-op (cache fresco). */
export interface CameraSnapshotRefreshResult {
  status: CameraSnapshotStatus
  queued: boolean
  reason?: 'fresh' | 'already_pending'
  captured_at?: string | null
}

export const cameraService = {
  async list(): Promise<Camera[]> {
    const res = await api.get<ApiEnvelope<ApiListData>>('/cameras')
    return res.data?.cameras ?? []
  },

  async get(id: string): Promise<Camera> {
    const res = await api.get<ApiEnvelope<Camera>>(`/cameras/${id}`)
    return res.data
  },

  async create(data: CameraFormData): Promise<Camera> {
    const res = await api.post<ApiEnvelope<Camera>>('/cameras', formToApiPayload(data))
    return res.data
  },

  async update(id: string, data: Partial<CameraFormData>): Promise<Camera> {
    const res = await api.put<ApiEnvelope<Camera>>(`/cameras/${id}`, formToApiPayload(data))
    return res.data
  },

  /** Arquiva a câmera (is_active=false). Reversível — ⛔ não apaga nada.
   *
   * ARQUIVAR ≠ EXCLUIR (`remove` abaixo). Arquivar tira do reconhecimento e
   * volta com `restore`; excluir tira do sistema e não volta.
   */
  async archive(id: string): Promise<Camera> {
    const res = await api.post<ApiEnvelope<{ camera: Camera }>>(`/cameras/${id}/archive`)
    return res.data.camera
  },

  async restore(id: string): Promise<Camera> {
    const res = await api.post<ApiEnvelope<{ camera: Camera }>>(`/cameras/${id}/restore`)
    return res.data.camera
  },

  /** Exclui a câmera do sistema. IRREVERSÍVEL pela UI — não há `restore`.
   *
   * O que o backend faz NÃO é `DELETE FROM cameras` (isso apagaria alerta e
   * evidência por CASCATA, e travaria por FK em câmera com frame de treino):
   * é exclusão lógica (`cameras.deleted_at`, migration 138). A câmera some da
   * lista, do Ao Vivo, do grid e do config do edge; alerta, evidência e frame
   * anotado permanecem. Quem quiser a câmera de volta cadastra de novo.
   *
   * Quem chama TEM de pedir confirmação explícita antes (Cameras.tsx).
   */
  async remove(id: string): Promise<void> {
    await api.delete<ApiEnvelope<{ deleted: boolean }>>(`/cameras/${id}`)
  },

  async test(id: string): Promise<TestResult> {
    const res = await api.post<ApiEnvelope<TestResult>>(`/cameras/${id}/test`)
    return res.data
  },

  /** Inicia o stream e devolve a URL HLS que o backend autorizou.
   *
   * A URL TEM que vir do backend: com HLS_REQUIRE_PLAYBACK_TOKEN ligado ela
   * carrega um token de playback no path, e a URL legada (montada no front)
   * recebe 404. O token é o portão de tenant do serve_hls, que é público por
   * design — hls.js não envia header de auth.
   */
  async start(id: string): Promise<StreamStartResult> {
    const res = await api.post<ApiEnvelope<StreamStartResult>>(`/cameras/${id}/stream/start`)
    return res.data
  },

  async stop(id: string): Promise<void> {
    await api.post(`/cameras/${id}/stream/stop`)
  },

  async patchConfig(
    id: string,
    patch: CameraConfigPatch,
  ): Promise<CameraWithPropagation> {
    const res = await api.patch<ApiEnvelope<CameraWithPropagation>>(
      `/cameras/${id}/config`,
      patch,
    )
    return res.data
  },

  /** Telemetria real do site da câmera para o aviso health-aware (WS10). */
  async getHealthContext(id: string): Promise<CameraHealthContext> {
    const res = await api.get<ApiEnvelope<CameraHealthContext>>(
      `/cameras/${id}/health-context`,
    )
    return res.data
  },

  async probe(data: ProbeInput): Promise<ProbeResult> {
    const res = await api.post<ApiEnvelope<ProbeResult>>('/cameras/probe', data)
    return res.data
  },

  /** Estado do último snapshot (Bloco A) — nunca ativa a câmera, nunca toca
   * no channel_map/HLS. Leitura pura do cache do servidor. */
  async getSnapshot(id: string): Promise<CameraSnapshotState> {
    const res = await api.get<ApiEnvelope<CameraSnapshotState>>(`/cameras/${id}/snapshot`)
    return res.data
  },

  /** Dispara uma nova captura (edge_command capture_snapshot) — o backend
   * decide se é no-op (cache ainda fresco) ou se despacha de fato; nunca
   * bate no gravador a cada chamada. */
  async refreshSnapshot(id: string): Promise<CameraSnapshotRefreshResult> {
    const res = await api.post<ApiEnvelope<CameraSnapshotRefreshResult>>(
      `/cameras/${id}/snapshot/refresh`,
    )
    return res.data
  },
}

export default cameraService
