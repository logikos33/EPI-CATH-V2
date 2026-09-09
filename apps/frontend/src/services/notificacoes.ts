/**
 * Notificações — contrato ÚNICO entre o sino, o aviso (pop-up) e a central.
 *
 * ⚠️ NÃO existe caixa de entrada por usuário no backend. `/api/v1/notifications/*`
 * é só CANAL (para onde mandar WhatsApp/e-mail/webhook), não "o que eu já li".
 * O que existe é `alerts.acknowledged`, um booleano por ALERTA e por TENANT —
 * e é ele que este módulo trata como "lida":
 *
 *     não lida = `acknowledged = false`   ·   lida = `acknowledged = true`
 *
 * Isso é uma DECISÃO, não um detalhe: significa que marcar como lida é um ato
 * do TENANT, não de cada pessoa. Dois operadores da RVB compartilham a mesma
 * caixa — quando um lê, sai da fila do outro. É o que a tela de Eventos já faz
 * desde sempre com a coluna "Novo/Reconhecido", e inventar aqui uma segunda
 * noção de "lida" (por usuário) exigiria tabela nova, migration e um segundo
 * número de pendências que discordaria do da tela de Eventos no primeiro dia.
 * Se o dono quiser caixa POR USUÁRIO, é tabela nova — está registrado.
 *
 * Um único lugar decide o recorte (violações, ADR-0065), o rótulo da classe e o
 * formato da data. Três telas liam isso de três jeitos antes.
 */
import { api } from './api'

export interface ViolacaoNotificada {
  class: string
  confidence?: number
}

export interface Notificacao {
  id: string
  camera_id: string
  camera_name?: string
  violations: ViolacaoNotificada[]
  acknowledged: boolean
  /** Quando a linha foi GRAVADA. */
  created_at: string
  /** Hora REAL da captura no edge — pode divergir de `created_at` por dias. */
  timestamp?: string
}

export interface PaginaNotificacoes {
  alerts: Notificacao[]
  total: number
  /** Rajadas (câmera+classe em <60s) do MESMO recorte — ux2/dedup. */
  total_situacoes?: number
  page?: number
  per_page?: number
  pages?: number
}

/**
 * Chave de cache do que o sino busca. O aviso (pop-up) usa a MESMA — react-query
 * dedupa, então montar os dois não dobra a requisição nem cria duas verdades
 * sobre "quantas estão pendentes".
 */
export const CHAVE_PENDENTES = ['alerts-unack', 'violation'] as const

/**
 * O recorte do sino: ADR-0065 — EPI presente é telemetria, não alerta. `per_page=30`
 * é da ux2/dedup: uma rajada isolada não pode preencher o painel inteiro e
 * esconder situações mais antigas.
 */
export const CONSULTA_PENDENTES =
  '/alerts?acknowledged=false&per_page=30&page=1&kind=violation'

export const ROTULO_CLASSE: Record<string, string> = {
  no_helmet: 'Sem capacete',
  no_vest: 'Sem colete',
  no_gloves: 'Sem luvas',
  no_safety_glasses: 'Sem óculos',
  no_glasses: 'Sem óculos',
}

/** "Sem capacete, Sem luvas". Classe sem rótulo sai com o nome cru — nunca em
 *  branco: apagar o que o modelo disse é pior que mostrar um nome feio. */
export function rotuloDasClasses(violacoes: ViolacaoNotificada[] | undefined): string {
  const nomes = (violacoes ?? []).map((v) => ROTULO_CLASSE[v.class] ?? v.class).filter(Boolean)
  return nomes.length > 0 ? nomes.join(', ') : 'Evento sem classe declarada'
}

/**
 * O instante do FATO: a captura no edge (`timestamp`), com queda para a hora da
 * gravação. É o mesmo eixo que a lista de Eventos e o CSV exibem — mostrar
 * `created_at` no pop-up faria o mesmo evento ter duas horas diferentes em duas
 * telas (medido no DEV: 334 de 5.174 alertas divergem, até 3,5 dias).
 */
export function quandoAconteceu(n: Pick<Notificacao, 'created_at' | 'timestamp'>): string {
  return n.timestamp ?? n.created_at
}

/** Data e hora LOCAIS de quem está lendo — o backend manda ISO 8601 UTC (Z). */
export function dataHoraLocal(iso: string | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleString('pt-BR', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function haQuantoTempo(iso: string | undefined): string {
  if (!iso) return ''
  const t = new Date(iso).getTime()
  if (Number.isNaN(t)) return ''
  const mins = Math.floor((Date.now() - t) / 60000)
  if (mins < 1) return 'agora'
  if (mins < 60) return `há ${mins}min`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `há ${hrs}h`
  return `há ${Math.floor(hrs / 24)}d`
}

/** Envelope do backend: `{success, message, data}`. Chamadores antigos que
 *  devolviam o corpo cru continuam funcionando pelo segundo ramo. */
function conteudo<T>(resposta: unknown): T | undefined {
  const r = resposta as { data?: T } | undefined
  return r?.data ?? (resposta as T | undefined)
}

export async function buscarPendentes(): Promise<PaginaNotificacoes | undefined> {
  return conteudo<PaginaNotificacoes>(await api.get(CONSULTA_PENDENTES))
}

/**
 * Marca N alertas como lidos em UMA requisição.
 *
 * Antes disto o cliente fazia o laço: 235 pendentes = 235 POSTs (é literalmente
 * o que `Eventos.tsx` ainda faz para a seleção manual). Devolve quantas
 * MUDARAM de estado — não quantas foram pedidas.
 */
export async function marcarLidas(ids: string[]): Promise<number> {
  if (ids.length === 0) return 0
  const r = conteudo<{ acknowledged?: number }>(await api.post('/alerts/acknowledge', { ids }))
  return r?.acknowledged ?? 0
}

/**
 * Marca TODAS as pendentes do recorte. `all: true` vai explícito porque o
 * backend recusa corpo vazio — "marque tudo" nunca pode ser o default de um
 * cliente com bug.
 */
export async function marcarTodasLidas(
  opcoes: { kind?: string; cameraId?: string } = {},
): Promise<number> {
  const corpo: Record<string, unknown> = { all: true, kind: opcoes.kind ?? 'violation' }
  if (opcoes.cameraId) corpo.camera_id = opcoes.cameraId
  const r = conteudo<{ acknowledged?: number }>(await api.post('/alerts/acknowledge', corpo))
  return r?.acknowledged ?? 0
}

/**
 * URL assinada da evidência, ou `null`.
 *
 * `null` é resposta legítima e frequente: alerta sem frame guardado, storage
 * local (que não assina URL) ou R2 fora do ar. Quem chama mostra o ícone de
 * aviso — NUNCA uma imagem de mentira no lugar da evidência que não existe.
 */
export async function miniaturaDaEvidencia(alertaId: string): Promise<string | null> {
  try {
    const r = conteudo<{ snapshot_url?: string }>(await api.get(`/alerts/${alertaId}/snapshot`))
    return r?.snapshot_url ?? null
  } catch {
    return null
  }
}

/**
 * Deep-link do evento a partir de uma notificação.
 *
 * ⚠️ SEM `acknowledged=false`. O clique numa notificação MARCA COMO LIDA — se o
 * destino continuasse filtrando por não-lidas, o usuário clicaria e cairia numa
 * lista onde o evento que ele acabou de abrir não está mais. Beco sem saída
 * construído com as próprias mãos.
 */
export function rotaDoEvento(base: string, n: Pick<Notificacao, 'id' | 'camera_id'>): string {
  const p = new URLSearchParams({
    camera_id: n.camera_id ?? '',
    kind: 'violation',
    highlight: n.id,
  })
  return `${base}?${p}`
}
