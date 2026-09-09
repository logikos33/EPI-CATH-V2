/**
 * EPI Ao Vivo — `/epi/live` (de-para: `/epi/monitoring` do front atual).
 *
 * Desenho: `EPI Ao Vivo.dc.html`. Presets 2×2/3×3/4×3 + colunas 2–6 + modo
 * DESTAQUE (1 grande + trilho), overlay de detecção com toggle, gaveta da
 * câmera. Em ≥5 colunas o estado vira só o ícone e os rótulos de bbox somem.
 *
 * ─────────────────────────────────────────────────────────────────────────
 * O QUE ESTA TELA HERDA E NÃO PODE REGREDIR (história cara)
 * ─────────────────────────────────────────────────────────────────────────
 *
 * 1. **A URL do HLS NUNCA é montada aqui.** Desde os PRs 255/256, `m3u8`
 *    sem token de playback responde 404 — o MESMO 404 de stream inexistente
 *    (C-01: não vaze existência). A URL vem de `POST /stream/start` via
 *    `useLiveView`, com o token no PATH (`/stream/s/<token>/stream.m3u8`),
 *    porque os `.ts` da playlist são relativos e herdam o token sozinhos.
 *
 * 2. **Nada de player novo.** `CameraPlayer` carrega o watchdog de stall, o
 *    backoff, a recuperação por URL nova no 404/410 e o teardown de aba
 *    oculta. `useLiveView` carrega a renovação ancorada no `exp` REAL do
 *    token — a cadeia que causou o congelamento cíclico de 04/08 e foi
 *    fechada nos PRs 306-308. Reescrever qualquer um dos dois é reabrir
 *    aquele bug.
 *
 * 3. **Sessão única de playback por câmera.** Ladrilho e gaveta cada um monta
 *    seu `useLiveView` + `CameraPlayer`. Com os dois vivos para a MESMA
 *    câmera, cada um minta um token próprio e dois tokens baixam o mesmo
 *    `.ts` (visto em produção). `suprimido` desliga o player do ladrilho
 *    enquanto a gaveta da mesma câmera está aberta.
 *
 * 4. **Só monta player de câmera visível** (IntersectionObserver). Um video
 *    wall de 28 câmeras montando 28 players fora da viewport derruba o
 *    bucket de vídeo do rate-limit.
 *
 * ─────────────────────────────────────────────────────────────────────────
 * DADO REAL, OU VAZIO HONESTO — o que esta tela NÃO inventa
 * ─────────────────────────────────────────────────────────────────────────
 *
 * · **Estado da câmera.** `cameras.stream_status` NÃO EXISTE mais no backend
 *   (só sobrou no type do front) e `cameras.last_seen` não é escrito por
 *   ninguém. O único sinal honesto de "o vídeo está chegando" é o próprio
 *   caminho de playback: arquivada → OFFLINE; `/stream/start` falhou →
 *   OFFLINE; URL ainda não resolveu → CONECTANDO; URL na mão → ONLINE.
 *   O desenho diz "INSTÁVEL" no estado do meio — trocado por "CONECTANDO",
 *   que é o que o sinal disponível de fato afirma.
 *
 * · **Contagem do cabeçalho.** "N CÂMERAS · M ATIVAS" (de `is_active`), não
 *   "M ONLINE": online é por ladrilho e só existe depois do playback.
 *
 * · **Preset ≠ paginação.** No protótipo o preset 2×2 corta a lista em 4 e
 *   não há como ver a 5ª câmera. Aqui o preset define COLUNAS e a grade
 *   mostra todas — 28 câmeras não podem sumir por causa de um preset.
 */
import { useCallback, useEffect, useMemo, useRef, useState, type RefObject } from 'react'
import { Link } from 'react-router-dom'
import {
  DndContext,
  PointerSensor,
  closestCenter,
  useDraggable,
  useDroppable,
  useSensor,
  useSensors,
  type DragEndEvent,
} from '@dnd-kit/core'
import { LayoutGrid, Maximize2, MoreVertical, Plus, Video, X } from 'lucide-react'

import { api, getToken } from '../../services/api'
import { confiancaBruta } from '../../services/confidenceDisplay'
import { useAuth } from '../../hooks/useAuth'
import { useLiveView } from '../../hooks/useLiveView'
import { useMonitoringSocket, type Detection } from '../../hooks/useMonitoringSocket'
/**
 * REUSO DELIBERADO, não dívida de migração (triagem de 05/09).
 *
 * `CameraPlayer` mora em `components/monitoring/` e é `INFRA` no
 * MANIFESTO-FRONT-ANTIGO ("não é tela"): a Fase 3 só apaga `MIGRADO`, então a
 * demolição do front antigo NÃO o leva junto. Movê-lo para `app/` quebraria os
 * seis consumidores do front antigo que ainda servem rota viva —
 * `CameraCell`, `ScenarioEditor`, `LiveVideoWithOperations`, `CamerasPage`,
 * `CameraTriagePage`, `FuelingPage` — e duplicá-lo criaria um segundo player
 * sem o watchdog de stall, o backoff e a recuperação por URL nova (item 2 do
 * cabeçalho: era exatamente o congelamento cíclico dos PRs 306-308).
 *
 * A regra está travada em `src/test/migracao/fronteira-front-novo.test.ts`.
 */
import { CameraPlayer } from '../../components/monitoring/CameraPlayer'
import { labelForModule } from '../../utils/labels'
import type { Camera } from '../../types'
import { LogikosLoader } from '../shell/LogikosLoader'
import * as s from './AoVivo.css'
import { rotaNova } from '../RotasNovas'

const WS_URL = (import.meta.env.VITE_WS_URL as string | undefined)
  || (import.meta.env.VITE_API_URL as string | undefined)
  || ''

/**
 * Dimensão do frame que as bbox do WebSocket usam como referência. É a MESMA
 * suposição que `DetectionOverlay` do front atual carrega desde sempre (seus
 * callers passam 640×360 fixo) — o payload de `detection` não traz as
 * dimensões do frame. Convertido para % aqui, então o ladrilho pode ter
 * qualquer tamanho sem recalcular nada.
 */
const FRAME = { largura: 640, altura: 360 }

/** Acima disto o ladrilho fica pequeno: estado vira ícone, rótulo de bbox some. */
const COLUNAS_COMPACTAS = 5

type EstadoCamera = 'online' | 'conectando' | 'offline'

const TARJA: Record<EstadoCamera, { icone: string; palavra: string }> = {
  online: { icone: '●', palavra: 'ONLINE' },
  conectando: { icone: '▲', palavra: 'CONECTANDO' },
  offline: { icone: '✕', palavra: 'OFFLINE' },
}

type Preset = '2x2' | '3x3' | '4x3' | 'destaque' | 'custom'

const COLUNAS_DO_PRESET: Partial<Record<Preset, number>> = { '2x2': 2, '3x3': 3, '4x3': 4 }

const PRESETS: Array<[Preset, string]> = [
  ['2x2', '2×2'],
  ['3x3', '3×3'],
  ['4x3', '4×3'],
  ['destaque', 'DESTAQUE'],
]

interface AlertaRecente {
  id: string
  class_name?: string | null
  violations?: Array<{ class?: string }> | null
  created_at?: string | null
  captured_at?: string | null
}

// ---------------------------------------------------------------------------
// Site (parede POR SITE) — agrupamento client-side pelo `site_id` que já vem
// em cada câmera de GET /cameras. `GET /v1/edge/sites` existe e teria o nome
// real, mas é admin/superadmin-only (`routes.py:1117`) e esta tela é aberta
// por qualquer papel com `cameras:read` — chamá-la quebraria a tela para
// operador comum. Sem nome real, o rótulo é honesto: o id curto, não um nome
// inventado.
// ---------------------------------------------------------------------------
const SEM_SITE = 'sem-site'

function chaveDeSite(c: Camera): string {
  return c.site_id ?? SEM_SITE
}

function rotuloDeSite(id: string): string {
  return id === SEM_SITE ? 'SEM SITE' : `SITE ${id.slice(0, 8).toUpperCase()}`
}

// ---------------------------------------------------------------------------
// Layouts nomeados — localStorage chaveado por site (decisão do Vitor
// 30/08/2026: client-side, sem endpoint). Tolerante a quota cheia / modo
// privado / conteúdo corrompido: layout salvo é conveniência, nunca dado.
// ---------------------------------------------------------------------------
interface LayoutSalvo {
  nome: string
  colunas: number
  /** ids de câmera na ordem dos quadros; `null` = quadro vazio. */
  slots: Array<string | null>
}

const MAX_LAYOUTS = 10

function chaveLayouts(siteId: string): string {
  return `lk-parede:${siteId}`
}

function ehLayoutSalvo(v: unknown): v is LayoutSalvo {
  return (
    v != null &&
    typeof v === 'object' &&
    typeof (v as LayoutSalvo).nome === 'string' &&
    typeof (v as LayoutSalvo).colunas === 'number' &&
    Array.isArray((v as LayoutSalvo).slots)
  )
}

function lerLayouts(siteId: string): LayoutSalvo[] {
  try {
    const cru: unknown = JSON.parse(localStorage.getItem(chaveLayouts(siteId)) ?? 'null')
    return Array.isArray(cru) ? cru.filter(ehLayoutSalvo).slice(0, MAX_LAYOUTS) : []
  } catch {
    return []
  }
}

function gravarLayouts(siteId: string, layouts: LayoutSalvo[]) {
  try {
    localStorage.setItem(chaveLayouts(siteId), JSON.stringify(layouts.slice(0, MAX_LAYOUTS)))
  } catch {
    // Modo privado / cota cheia: o layout é conveniência, não dado.
  }
}

// ---------------------------------------------------------------------------
// useNaTela — só monta player do que está na viewport (ver ponto 4 do topo).
// ---------------------------------------------------------------------------
function useNaTela(ref: RefObject<HTMLElement | null>): boolean {
  // jsdom não tem IntersectionObserver: sem observer, tudo é visível — senão a
  // tela nasceria vazia em teste por um detalhe de ambiente.
  const [visivel, setVisivel] = useState(typeof IntersectionObserver === 'undefined')
  useEffect(() => {
    const el = ref.current
    if (!el || typeof IntersectionObserver === 'undefined') return
    const obs = new IntersectionObserver(
      (entradas) => {
        const e = entradas[0]
        if (e) setVisivel(e.isIntersecting)
      },
      { rootMargin: '150px', threshold: 0 },
    )
    obs.observe(el)
    return () => obs.disconnect()
  }, [ref])
  return visivel
}

// ---------------------------------------------------------------------------
// Overlay de detecção — ⛔ pointerEvents none, ZERO onClick (CLAUDE.md).
// ---------------------------------------------------------------------------
function CamadaCaixas({ deteccoes, comRotulo }: { deteccoes: Detection[]; comRotulo: boolean }) {
  const { isSuperAdmin } = useAuth()
  return (
    // pointerEvents também inline: é regra de segurança de interação, não
    // enfeite — tem de valer mesmo se alguém trocar a folha de estilo.
    <div className={s.camadaCaixas} style={{ pointerEvents: 'none' }} data-testid="camada-caixas">
      {deteccoes.map((d, i) => {
        const [x, y, w, h] = d.bbox
        const tom = d.is_violation === true ? 'nc' : 'ok'
        return (
          <div
            key={`${d.class}-${i}`}
            className={s.caixa[tom]}
            data-testid="caixa-deteccao"
            style={{
              left: `${(x / FRAME.largura) * 100}%`,
              top: `${(y / FRAME.altura) * 100}%`,
              width: `${(w / FRAME.largura) * 100}%`,
              height: `${(h / FRAME.altura) * 100}%`,
            }}
          >
            {comRotulo && (
              <span className={s.rotuloCaixa[tom]}>
                {/* Contrato A1c: número cru não prevê acerto — a etiqueta sobre
                    o vídeo ao vivo não tem espaço para a leitura honesta
                    (whiteSpace nowrap), então só superadmin vê o %; ver
                    services/confidenceDisplay.ts. */}
                {d.class}{isSuperAdmin ? ` ${confiancaBruta(d.confidence)}` : ''}
              </span>
            )}
          </div>
        )
      })}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Ladrilho — uma câmera. Dono do seu próprio ciclo de playback.
// ---------------------------------------------------------------------------
interface LadrilhoProps {
  camera: Camera
  deteccoes: Detection[]
  compacto: boolean
  selecionado: boolean
  /** Gaveta desta MESMA câmera aberta — não manter dois players (ponto 3). */
  suprimido: boolean
  /** WebSocket de detecções caiu (§11) — dado pode estar velho, não confiável. */
  semSinal: boolean
  grande?: boolean
  onSelecionar: () => void
  onDestacar?: () => void
}

function Ladrilho({
  camera,
  deteccoes,
  compacto,
  selecionado,
  suprimido,
  semSinal,
  grande = false,
  onSelecionar,
  onDestacar,
}: LadrilhoProps) {
  const ref = useRef<HTMLDivElement>(null)
  const naTela = useNaTela(ref)
  // Mesma regra da parede: só `is_active === false` é inativa. Campo ausente
  // é câmera viva — esconder vídeo por causa de um campo que não veio seria
  // a tela decidindo por conta própria que a câmera está fora.
  const inativa = camera.is_active === false
  const ativo = naTela && !suprimido && !inativa
  const { hlsUrl, error } = useLiveView(camera.id, ativo)

  const estado: EstadoCamera = inativa || error != null
    ? 'offline'
    : hlsUrl != null
      ? 'online'
      : 'conectando'

  const tarja = TARJA[estado]
  const descricao = `${tarja.icone} ${tarja.palavra}`

  return (
    <div
      ref={ref}
      className={`${grande ? s.foco : s.ladrilho[selecionado ? 'selecionado' : 'normal']}${semSinal ? ` ${s.ladrilhoSemSinal}` : ''}`}
      role="button"
      tabIndex={0}
      aria-label={`Abrir ${camera.name}`}
      onClick={onSelecionar}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          onSelecionar()
        }
      }}
    >
      {ativo && hlsUrl != null && (
        <div className={s.moldura}>
          <CameraPlayer cameraId={camera.id} hlsUrl={hlsUrl} />
        </div>
      )}

      {/* Reconectando é só quando de fato há o que reconectar. Câmera
          arquivada não está "tentando" nada — dizer que está seria mentir com
          animação. */}
      {estado === 'offline' && !inativa && (
        <LogikosLoader
          variante={compacto ? 'spinner' : 'tile'}
          estado="retry"
          rotulo={`RECONECTANDO · ${camera.name}`}
          tamanho={grande ? 52 : 44}
        />
      )}
      {/* Guarda, não caminho normal: desde o filtro `camerasVivas` a parede
          não recebe mais câmera inativa. Se uma chegar aqui por outro
          caminho, o certo é dizer o que ela é — não girar um "RECONECTANDO"
          eterno para algo que não está tentando conectar. */}
      {estado === 'offline' && inativa && (
        <span className={s.centradoDetalhe}>Câmera arquivada</span>
      )}

      <span className={s.tarjaNome}>{camera.name}</span>
      {/* §13 — local e módulo já vêm em GET /cameras; só não eram desenhados.
          Some no quadrinho compacto (mesmo critério do rótulo de bbox). */}
      {!compacto && (
        <span className={s.tarjaLocalModulo}>
          {camera.location ?? 'SEM LOCAL'}
          {camera.module_code != null && ` · ${labelForModule(camera.module_code)}`}
        </span>
      )}
      <span className={s.tarjaEstado[estado]} title={descricao}>
        {compacto ? tarja.icone : descricao}
      </span>
      {semSinal && (
        <span
          className={s.avisoSemSinal}
          title="Conexão de detecções caída — pode haver violação não sinalizada"
        >
          SEM SINAL
        </span>
      )}

      {deteccoes.length > 0 && <CamadaCaixas deteccoes={deteccoes} comRotulo={!compacto} />}

      {onDestacar != null && (
        <button
          type="button"
          className={s.botaoDestacar}
          title="Destacar esta câmera"
          aria-label={`Destacar ${camera.name}`}
          onClick={(e) => {
            e.stopPropagation()
            onDestacar()
          }}
        >
          <Maximize2 size={14} strokeWidth={1.8} />
        </button>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Gaveta da câmera
// ---------------------------------------------------------------------------
function Gaveta({
  camera,
  deteccoes,
  onFechar,
  onDestacar,
}: {
  camera: Camera
  deteccoes: Detection[]
  onFechar: () => void
  onDestacar: () => void
}) {
  // `is_active !== false` (e não a veracidade do campo): câmera cujo payload
  // não traz is_active é tratada como VIVA — o mesmo critério do filtro da
  // parede. Com a checagem antiga, campo ausente derrubava o playback.
  const { hlsUrl } = useLiveView(camera.id, camera.is_active !== false)
  const [eventos, setEventos] = useState<AlertaRecente[] | null>(null)

  useEffect(() => {
    let vivo = true
    setEventos(null)
    api
      .get<{ data?: { alerts?: AlertaRecente[] } }>(
        `/alerts?camera_id=${encodeURIComponent(camera.id)}&per_page=5`,
      )
      .then((res) => {
        if (vivo) setEventos(res.data?.alerts ?? [])
      })
      .catch(() => {
        if (vivo) setEventos([])
      })
    return () => {
      vivo = false
    }
  }, [camera.id])

  const estado: EstadoCamera =
    camera.is_active === false ? 'offline' : hlsUrl != null ? 'online' : 'conectando'

  return (
    <aside className={s.gaveta} aria-label={`Detalhes de ${camera.name}`}>
      <div className={s.gavetaTopo}>
        <span className={s.gavetaNome}>{camera.name}</span>
        <span className={s.tarjaEstado[estado]} style={{ position: 'static' }}>
          {TARJA[estado].icone} {TARJA[estado].palavra}
        </span>
        <button type="button" className={s.gavetaFechar} onClick={onFechar} aria-label="Fechar painel">
          <X size={13} strokeWidth={1.8} />
        </button>
      </div>

      <div className={s.gavetaVideo}>
        {hlsUrl != null && (
          <div className={s.moldura}>
            <CameraPlayer cameraId={camera.id} hlsUrl={hlsUrl} />
          </div>
        )}
        {deteccoes.length > 0 && <CamadaCaixas deteccoes={deteccoes} comRotulo />}
      </div>

      {/* Só o que o backend de fato devolve nesta rota. Resolução e origem do
          feed (o "1080P · HLS · EDGE" do desenho) não vêm de GET /cameras. */}
      <div className={s.gavetaDados}>
        <span>{camera.fps_target != null ? `${camera.fps_target} FPS` : 'FPS —'}</span>
        <span>HLS</span>
        <span>{camera.location ?? 'SEM LOCAL'}</span>
      </div>

      <button type="button" className={s.acaoSecundaria} onClick={onDestacar}>
        <Maximize2 size={14} strokeWidth={1.8} />
        Destacar no grid
      </button>

      <div className={s.blocoLista}>
        <span className={s.overline}>Eventos recentes</span>
        {eventos == null ? (
          <LogikosLoader variante="spinner" estado="waiting" />
        ) : eventos.length === 0 ? (
          <span className={s.centradoDetalhe}>Nenhum evento nesta câmera</span>
        ) : (
          eventos.map((ev) => (
            <Link key={ev.id} to={rotaNova(`/epi/eventos/${ev.id}`)} className={s.itemEvento}>
              <span className={s.pontoEvento} />
              <span className={s.eventoTitulo}>
                {ev.class_name ?? ev.violations?.map((v) => v.class).filter(Boolean).join(', ') ?? 'Evento'}
              </span>
              <span className={s.eventoHora}>{horaDe(ev.captured_at ?? ev.created_at)}</span>
            </Link>
          ))
        )}
      </div>

      <Link to={rotaNova('/epi/cameras')} className={s.acaoSecundaria}>
        Ir para gestão da câmera
      </Link>
    </aside>
  )
}

function horaDe(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return Number.isNaN(d.getTime())
    ? '—'
    : d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })
}

// ---------------------------------------------------------------------------
// CelulaSlot — um quadro da grade de montagem (câmera OU vago), arrastável.
// Fora do modo Montar é só leitura: sem alça, sem seletor, sem menu.
// ---------------------------------------------------------------------------
interface CelulaSlotProps {
  indice: number
  camera: Camera | null
  camerasDisponiveis: Camera[]
  montando: boolean
  compacto: boolean
  selecionado: boolean
  suprimido: boolean
  semSinal: boolean
  deteccoes: Detection[]
  arrastando: string | null
  onEscolher: (cameraId: string) => void
  onRemover: () => void
  onSelecionar: () => void
  onDestacar: () => void
}

function CelulaSlot({
  indice,
  camera,
  camerasDisponiveis,
  montando,
  compacto,
  selecionado,
  suprimido,
  semSinal,
  deteccoes,
  arrastando,
  onEscolher,
  onRemover,
  onSelecionar,
  onDestacar,
}: CelulaSlotProps) {
  const idSlot = `slot-${indice}`
  const { attributes, listeners, setNodeRef: setArrastoRef } = useDraggable({
    id: idSlot,
    disabled: !montando || camera == null,
  })
  const { setNodeRef: setSolturaRef, isOver } = useDroppable({ id: idSlot, disabled: !montando })
  const refs = (el: HTMLDivElement | null) => {
    setArrastoRef(el)
    setSolturaRef(el)
  }
  const soltarAqui = montando && isOver && arrastando != null && arrastando !== idSlot
  const arrasto = montando ? { ...attributes, ...listeners } : {}

  if (camera == null) {
    return (
      <div ref={refs} {...arrasto}>
        {soltarAqui ? (
          <div className={s.ladrilhoVago}>
            <span className={s.soltePraTrocar}>SOLTE PARA TROCAR</span>
          </div>
        ) : montando ? (
          <label className={s.ladrilhoVago}>
            <Plus size={22} strokeWidth={1.7} aria-hidden />
            Escolher câmera
            <select
              className={s.selectSobreposto}
              aria-label={`Escolher câmera para o quadro ${indice + 1}`}
              value=""
              onChange={(e) => {
                if (e.target.value) onEscolher(e.target.value)
              }}
            >
              <option value="" disabled>
                Escolher câmera
              </option>
              {camerasDisponiveis.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </label>
        ) : (
          <div className={s.ladrilhoVago}>
            <span>Vazio</span>
          </div>
        )}
      </div>
    )
  }

  return (
    <div ref={refs} className={s.celulaOcupada} {...arrasto}>
      <Ladrilho
        camera={camera}
        deteccoes={deteccoes}
        compacto={compacto}
        selecionado={selecionado}
        suprimido={suprimido}
        semSinal={semSinal}
        onSelecionar={onSelecionar}
        onDestacar={onDestacar}
      />
      {montando &&
        (soltarAqui ? (
          <span className={s.soltePraTrocarSobre}>SOLTE PARA TROCAR</span>
        ) : (
          <>
          {/* Tirar do quadro ≠ excluir a câmera. Isto mexe SÓ nesta parede:
              a câmera continua cadastrada, continua no Ao Vivo de quem não
              usa este layout e volta pelo "Escolher câmera" do quadro vago.
              Excluir de verdade é em Câmeras. */}
          <button
            type="button"
            className={s.botaoTirar}
            title={`Tirar ${camera.name} do quadro (não exclui a câmera)`}
            aria-label={`Tirar ${camera.name} do quadro ${indice + 1}`}
            onClick={(e) => {
              e.stopPropagation()
              onRemover()
            }}
          >
            <X size={14} strokeWidth={1.9} aria-hidden />
          </button>
          <label
            className={s.menuCelula}
            title="Trocar câmera desta posição"
            aria-label={`Trocar câmera do quadro ${indice + 1}`}
          >
            <MoreVertical size={14} strokeWidth={1.8} aria-hidden />
            <select
              className={s.selectSobreposto}
              aria-label={`Trocar câmera do quadro ${indice + 1}`}
              value={camera.id}
              onChange={(e) => {
                if (e.target.value && e.target.value !== camera.id) onEscolher(e.target.value)
              }}
            >
              {camerasDisponiveis.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </label>
          </>
        ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// AoVivo
// ---------------------------------------------------------------------------
export function AoVivo() {
  const { can } = useAuth()
  const podeVer = can('cameras:read')

  const [cameras, setCameras] = useState<Camera[] | null>(null)
  const [erro, setErro] = useState<string | null>(null)
  const [carregando, setCarregando] = useState(true)

  const [preset, setPreset] = useState<Preset>('2x2')
  const [colunas, setColunas] = useState(4)
  const [overlay, setOverlay] = useState(true)
  const [selecionada, setSelecionada] = useState<string | null>(null)
  const [focada, setFocada] = useState<string | null>(null)

  const [siteEscolhido, setSiteEscolhido] = useState<string | null>(null)
  const [montando, setMontando] = useState(false)
  const [slots, setSlots] = useState<Array<string | null> | null>(null)
  const [layouts, setLayouts] = useState<LayoutSalvo[]>([])
  const [layoutAtivo, setLayoutAtivo] = useState<string | null>(null)
  const [arrastando, setArrastando] = useState<string | null>(null)
  const sensores = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 4 } }))

  const carregar = useCallback(async () => {
    setCarregando(true)
    setErro(null)
    try {
      const res = await api.get<{ data?: { cameras?: Camera[] } | Camera[] }>('/cameras')
      const bruto = res.data
      const lista = Array.isArray(bruto)
        ? bruto
        : Array.isArray(bruto?.cameras)
          ? bruto.cameras
          : []
      setCameras(lista)
    } catch (e) {
      setCameras(null)
      setErro(e instanceof Error ? e.message : 'Conexão recusada')
    } finally {
      setCarregando(false)
    }
  }, [])

  useEffect(() => {
    if (podeVer) void carregar()
    else setCarregando(false)
  }, [carregar, podeVer])

  // WebSocket de detecções — JWT vai no handshake (`auth`), nunca na query.
  const token = getToken()
  const { detections, subscribeCamera, connected } = useMonitoringSocket({
    wsUrl: WS_URL,
    token: token ?? '',
    enabled: !!token && podeVer,
  })
  // §11 — o único sinal honesto de "detecção parou de chegar" que a tela tem
  // hoje é o próprio socket. Sem ele, os ladrilhos seguem ONLINE sem avisar.
  const semSinal = !connected

  /**
   * A PAREDE SÓ RECEBE CÂMERA ATIVA (`is_active !== false`).
   *
   * Antes, câmera arquivada entrava na grade e virava um quadrado morto
   * escrito "Câmera arquivada" — para sempre, porque não havia como tirá-la
   * dali. O Ao Vivo existe para ver vídeo chegando; câmera inativa não tem
   * vídeo por definição, então o ladrilho dela é um quadro que nunca vai
   * acender ocupando espaço de um que ia.
   *
   * Some, mas NÃO em silêncio: `inativas` vira uma contagem clicável no
   * cabeçalho. Sumir sem dizer para onde foi seria a tela mentindo sobre
   * quantas câmeras o site tem.
   *
   * `!== false` e não `=== true`: se a API um dia parar de mandar o campo,
   * o certo é mostrar a câmera, não esconder a parede inteira por um campo
   * ausente.
   *
   * ⚠️ `is_active === false` é ARQUIVADA **ou** rascunho de importação em
   * lote — a coluna serve aos dois (ver camera_repository.create_draft). Por
   * isso o rótulo diz "INATIVAS", que é o que o dado afirma, e não
   * "ARQUIVADAS", que seria afirmar mais do que se sabe.
   */
  const camerasVivas = useMemo(
    () => (cameras ?? []).filter((c) => c.is_active !== false),
    [cameras],
  )

  useEffect(() => {
    if (!connected) return
    camerasVivas.forEach((c) => subscribeCamera(c.id))
  }, [camerasVivas, connected, subscribeCamera])

  const colunasEfetivas = COLUNAS_DO_PRESET[preset] ?? colunas
  const compacto = colunasEfetivas >= COLUNAS_COMPACTAS
  const emDestaque = preset === 'destaque'

  // Grupos de site — na ORDEM em que aparecem em `cameras` (estável, não
  // alfabética: pula de assunto e o operador se perde). 1 grupo só = a tela
  // inteira já é 1 site, o seletor não tem o que separar.
  const gruposSite = useMemo(() => {
    const vistos = new Set<string>()
    const grupos: string[] = []
    for (const c of camerasVivas) {
      const chave = chaveDeSite(c)
      if (!vistos.has(chave)) {
        vistos.add(chave)
        grupos.push(chave)
      }
    }
    return grupos
  }, [camerasVivas])

  const siteAtivo =
    gruposSite.length <= 1
      ? (gruposSite[0] ?? null)
      : (siteEscolhido ?? gruposSite[0] ?? null)

  const camerasDoSite = useMemo(
    () =>
      gruposSite.length <= 1
        ? camerasVivas
        : camerasVivas.filter((c) => chaveDeSite(c) === siteAtivo),
    [camerasVivas, gruposSite, siteAtivo],
  )

  /** Inativas do site em foco — o número que o cabeçalho mostra e linka. */
  const inativasDoSite = useMemo(
    () =>
      (cameras ?? []).filter(
        (c) =>
          c.is_active === false &&
          (gruposSite.length <= 1 || chaveDeSite(c) === siteAtivo),
      ).length,
    [cameras, gruposSite, siteAtivo],
  )

  // Trocar de site invalida qualquer montagem em curso — os slots guardam
  // ids de câmera do site anterior, e os layouts salvos são por site.
  useEffect(() => {
    if (siteAtivo == null) return
    setLayouts(lerLayouts(siteAtivo))
    setLayoutAtivo(null)
    setSlots(null)
    setMontando(false)
  }, [siteAtivo])

  const camDe = useCallback(
    (id: string | null) => (id == null ? null : (camerasDoSite.find((c) => c.id === id) ?? null)),
    [camerasDoSite],
  )
  const cameraSelecionada = camDe(selecionada)
  const cameraFocada = camDe(focada) ?? camerasDoSite[0] ?? null

  const deteccoesDe = useCallback(
    (id: string): Detection[] => (overlay ? (detections[id] ?? []) : []),
    [detections, overlay],
  )

  const destacar = useCallback((id: string) => {
    setPreset('destaque')
    setFocada(id)
    setSelecionada(null)
    // "Destacar" só é visível na grade cheia ou na de destaque — sair da
    // montagem, senão o clique não teria efeito nenhum na tela.
    setSlots(null)
    setLayoutAtivo(null)
    setMontando(false)
  }, [])

  // ── Modo Montar + layouts salvos ──────────────────────────────────────────

  const idsNosSlots = useMemo(
    () => new Set((slots ?? []).filter((id): id is string => id != null)),
    [slots],
  )
  const disponiveisPara = useCallback(
    (atualId: string | null) =>
      camerasDoSite.filter((c) => c.id === atualId || !idsNosSlots.has(c.id)),
    [camerasDoSite, idsNosSlots],
  )

  const alternarMontar = useCallback(() => {
    setMontando((atual) => {
      const proximo = !atual
      setSlots((cur) => cur ?? (proximo ? camerasDoSite.map((c) => c.id) : cur))
      return proximo
    })
  }, [camerasDoSite])

  const verGradeCompleta = useCallback(() => {
    setSlots(null)
    setLayoutAtivo(null)
    setMontando(false)
  }, [])

  const aplicarLayout = useCallback((l: LayoutSalvo) => {
    setLayoutAtivo((atual) => {
      if (atual === l.nome) {
        setSlots(null)
        return null
      }
      setSlots(l.slots)
      setColunas(l.colunas)
      setPreset('custom')
      return l.nome
    })
  }, [])

  const removerLayout = useCallback(
    (nome: string) => {
      if (siteAtivo == null) return
      setLayouts((atual) => {
        const proximos = atual.filter((l) => l.nome !== nome)
        gravarLayouts(siteAtivo, proximos)
        return proximos
      })
      setLayoutAtivo((atual) => {
        if (atual !== nome) return atual
        setSlots(null)
        return null
      })
    },
    [siteAtivo],
  )

  const salvarLayoutAtual = useCallback(() => {
    if (siteAtivo == null || layouts.length >= MAX_LAYOUTS) return
    const nome = window.prompt('Nome deste layout:')?.trim()
    if (!nome) return
    const novo: LayoutSalvo = {
      nome,
      colunas: colunasEfetivas,
      slots: slots ?? camerasDoSite.map((c) => c.id),
    }
    setLayouts((atual) => {
      const proximos = [...atual.filter((l) => l.nome !== nome), novo].slice(0, MAX_LAYOUTS)
      gravarLayouts(siteAtivo, proximos)
      return proximos
    })
    setSlots(novo.slots)
    setLayoutAtivo(nome)
  }, [siteAtivo, layouts.length, colunasEfetivas, slots, camerasDoSite])

  const aoSoltarSlot = useCallback((e: DragEndEvent) => {
    setArrastando(null)
    if (!e.over || e.active.id === e.over.id) return
    const de = Number(String(e.active.id).replace('slot-', ''))
    const para = Number(String(e.over.id).replace('slot-', ''))
    setSlots((atual) => {
      if (atual == null) return atual
      const proximo = [...atual]
      ;[proximo[de], proximo[para]] = [proximo[para], proximo[de]]
      return proximo
    })
  }, [])

  if (!podeVer) {
    return (
      <div className={s.centrado}>
        <Video size={36} strokeWidth={1.5} aria-hidden />
        <span className={s.centradoTitulo}>Sem permissão para ver câmeras</span>
        <span className={s.centradoTexto}>
          Peça a quem administra o seu acesso a permissão de ver as câmeras, para acompanhar o ao vivo.
        </span>
      </div>
    )
  }

  if (carregando) {
    return <LogikosLoader variante="fullscreen" estado="waiting" rotulo="CONECTANDO ÀS CÂMERAS" />
  }

  if (erro != null) {
    return (
      <div className={s.centrado}>
        <span className={s.centradoTitulo}>Falha ao conectar ao gateway de vídeo</span>
        <span className={s.centradoDetalhe}>{erro}</span>
        <button type="button" className={s.acaoPrimaria} onClick={() => void carregar()}>
          Tentar novamente
        </button>
      </div>
    )
  }

  if (camerasDoSite.length === 0) {
    // Duas causas diferentes, dois textos diferentes: mandar "cadastre a
    // primeira câmera" para quem tem 12 câmeras arquivadas seria a tela
    // afirmando algo falso sobre o cadastro dele.
    const soInativas = (cameras?.length ?? 0) > 0
    return (
      <div className={s.centrado}>
        <Video size={36} strokeWidth={1.5} aria-hidden />
        <span className={s.centradoTitulo}>
          {soInativas ? 'Nenhuma câmera ativa neste site' : 'Nenhuma câmera neste site'}
        </span>
        <span className={s.centradoTexto}>
          {soInativas
            ? `As ${cameras?.length} câmeras cadastradas estão inativas (arquivadas ou rascunho de importação) e não entram na parede. Desarquive em Câmeras para voltarem ao Ao Vivo.`
            : 'Cadastre a primeira câmera para começar o monitoramento ao vivo.'}
        </span>
        <Link to={rotaNova('/epi/cameras')} className={s.acaoPrimaria}>
          {soInativas ? 'Ir para Câmeras' : 'Adicionar câmera'}
        </Link>
      </div>
    )
  }

  return (
    <div className={s.pagina}>
      <div className={s.barra}>
        <h1 className={s.titulo}>Ao Vivo</h1>
        <span className={s.resumo}>{camerasDoSite.length} CÂMERAS</span>
        {inativasDoSite > 0 && (
          <Link
            to={rotaNova('/epi/cameras')}
            className={s.resumoInativas}
            title="Câmera inativa (arquivada ou rascunho de importação) não entra na parede. Ver em Câmeras."
          >
            · {inativasDoSite} INATIVAS, FORA DA PAREDE
          </Link>
        )}

        {gruposSite.length > 1 && (
          <div className={s.colunas.inativo}>
            <span className={s.rotuloColunas}>SITE</span>
            <select
              className={s.seletorSiteControle}
              aria-label="Site"
              value={siteAtivo ?? ''}
              onChange={(e) => setSiteEscolhido(e.target.value)}
            >
              {gruposSite.map((id) => (
                <option key={id} value={id}>
                  {rotuloDeSite(id)}
                </option>
              ))}
            </select>
          </div>
        )}

        <span className={s.espacador} />

        <div className={s.grupoPresets} role="group" aria-label="Layout da grade">
          {PRESETS.map(([chave, rotulo]) => (
            <button
              key={chave}
              type="button"
              className={s.preset[preset === chave ? 'ativo' : 'inativo']}
              aria-pressed={preset === chave}
              onClick={() => setPreset(chave)}
            >
              {rotulo}
            </button>
          ))}
        </div>

        <div className={s.colunas[preset === 'custom' ? 'ativo' : 'inativo']}>
          <span className={s.rotuloColunas}>COLUNAS</span>
          <button
            type="button"
            className={s.passo}
            aria-label="Menos colunas"
            disabled={colunas <= 2}
            onClick={() => {
              setPreset('custom')
              setColunas((c) => Math.max(2, c - 1))
            }}
          >
            −
          </button>
          <span
            className={s.valorColunas[preset === 'custom' ? 'ativo' : 'inativo']}
            data-testid="colunas-valor"
          >
            {colunasEfetivas}
          </span>
          <button
            type="button"
            className={s.passo}
            aria-label="Mais colunas"
            disabled={colunas >= 6}
            onClick={() => {
              setPreset('custom')
              setColunas((c) => Math.min(6, c + 1))
            }}
          >
            +
          </button>
        </div>

        <button
          type="button"
          className={s.alternador[overlay ? 'ligado' : 'desligado']}
          aria-pressed={overlay}
          onClick={() => setOverlay((v) => !v)}
        >
          <span className={s.trilho[overlay ? 'ligado' : 'desligado']} aria-hidden>
            <span className={s.botaoTrilho[overlay ? 'ligado' : 'desligado']} />
          </span>
          Overlay de detecção
        </button>

        <button
          type="button"
          className={s.botaoMontar[montando ? 'ativo' : 'inativo']}
          aria-pressed={montando}
          onClick={alternarMontar}
        >
          <LayoutGrid size={15} strokeWidth={1.9} aria-hidden />
          Montar
        </button>
      </div>

      <div className={s.barraLayouts}>
        <span className={s.rotuloLayouts}>MEUS LAYOUTS</span>
        {layouts.map((l) => (
          <span key={l.nome} className={s.chipLayout[layoutAtivo === l.nome ? 'ativo' : 'inativo']}>
            <button type="button" onClick={() => aplicarLayout(l)} className={s.botaoChip}>
              {l.nome}
            </button>
            <button
              type="button"
              className={s.botaoChip}
              aria-label={`Remover layout ${l.nome}`}
              onClick={() => removerLayout(l.nome)}
            >
              <X size={12} strokeWidth={2} aria-hidden />
            </button>
          </span>
        ))}
        <button
          type="button"
          className={s.chipSalvar}
          disabled={layouts.length >= MAX_LAYOUTS}
          onClick={salvarLayoutAtual}
        >
          <Plus size={13} strokeWidth={2} aria-hidden />
          Salvar layout atual
        </button>
        {slots != null && (
          <button type="button" className={s.linkGradeCompleta} onClick={verGradeCompleta}>
            Ver grade completa
          </button>
        )}
        <span className={s.contagemLayouts}>{layouts.length} de {MAX_LAYOUTS} layouts</span>
      </div>

      <div className={s.area}>
        <div className={s.coluna}>
          {slots != null ? (
            <DndContext
              sensors={sensores}
              collisionDetection={closestCenter}
              onDragStart={(e) => setArrastando(String(e.active.id))}
              onDragEnd={aoSoltarSlot}
              onDragCancel={() => setArrastando(null)}
            >
              <div
                className={s.grade}
                data-testid="grade-montagem"
                style={{ gridTemplateColumns: `repeat(${colunasEfetivas}, 1fr)` }}
              >
                {slots.map((camId, indice) => {
                  const camera = camId == null ? null : (camerasDoSite.find((c) => c.id === camId) ?? null)
                  return (
                    <CelulaSlot
                      key={indice}
                      indice={indice}
                      camera={camera}
                      camerasDisponiveis={disponiveisPara(camId)}
                      montando={montando}
                      compacto={compacto}
                      selecionado={camera != null && selecionada === camera.id}
                      suprimido={camera != null && selecionada === camera.id}
                      semSinal={semSinal}
                      deteccoes={camera != null ? deteccoesDe(camera.id) : []}
                      arrastando={arrastando}
                      onEscolher={(novoId) =>
                        setSlots((cur) => cur?.map((v, i) => (i === indice ? novoId : v)) ?? cur)
                      }
                      onRemover={() =>
                        setSlots((cur) => cur?.map((v, i) => (i === indice ? null : v)) ?? cur)
                      }
                      onSelecionar={() => camera != null && setSelecionada(camera.id)}
                      onDestacar={() => camera != null && destacar(camera.id)}
                    />
                  )
                })}
              </div>
            </DndContext>
          ) : emDestaque && cameraFocada != null ? (
            <div className={s.destaque}>
              <div className={s.focoColuna}>
                <Ladrilho
                  key={cameraFocada.id}
                  camera={cameraFocada}
                  deteccoes={deteccoesDe(cameraFocada.id)}
                  compacto={false}
                  selecionado={false}
                  suprimido={selecionada === cameraFocada.id}
                  semSinal={semSinal}
                  grande
                  onSelecionar={() => setSelecionada(cameraFocada.id)}
                />
              </div>
              <div className={s.trilhoLateral}>
                {camerasDoSite
                  .filter((c) => c.id !== cameraFocada.id)
                  .map((c) => (
                    <Ladrilho
                      key={c.id}
                      camera={c}
                      deteccoes={deteccoesDe(c.id)}
                      compacto
                      selecionado={selecionada === c.id}
                      suprimido={selecionada === c.id}
                      semSinal={semSinal}
                      onSelecionar={() => setFocada(c.id)}
                    />
                  ))}
              </div>
            </div>
          ) : (
            <div
              className={s.grade}
              data-testid="grade"
              style={{ gridTemplateColumns: `repeat(${colunasEfetivas}, 1fr)` }}
            >
              {camerasDoSite.map((c) => (
                <Ladrilho
                  key={c.id}
                  camera={c}
                  deteccoes={deteccoesDe(c.id)}
                  compacto={compacto}
                  selecionado={selecionada === c.id}
                  suprimido={selecionada === c.id}
                  semSinal={semSinal}
                  onSelecionar={() => setSelecionada(c.id)}
                  onDestacar={() => destacar(c.id)}
                />
              ))}
              <Link to={rotaNova('/epi/cameras')} className={s.ladrilhoVago}>
                <Plus size={22} strokeWidth={1.7} aria-hidden />
                Adicionar câmera
              </Link>
            </div>
          )}
          {montando && (
            <span className={s.rodapeMontagem}>
              Arraste um quadro sobre outro para trocar de posição. O layout salvo guarda qual
              câmera fica em qual quadro — o operador decora posição, não nome.
            </span>
          )}
        </div>

        {cameraSelecionada != null && (
          <Gaveta
            camera={cameraSelecionada}
            deteccoes={deteccoesDe(cameraSelecionada.id)}
            onFechar={() => setSelecionada(null)}
            onDestacar={() => destacar(cameraSelecionada.id)}
          />
        )}
      </div>
    </div>
  )
}
