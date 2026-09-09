/**
 * EPI Evento Detalhe — `/epi/eventos/:id` (front novo).
 *
 * Migração de `pages/epi/AlertDetailPage.tsx` (`/epi/alerts/:alertId`) para o
 * desenho `EPI Evento Detalhe.dc.html`. É a tela que LEVA AO ACONTECIDO: frame
 * inteiro da evidência, caixa no lugar exato, hora REAL da captura, classe e
 * confiança — e o veredito humano com o MOTIVO.
 *
 * ─────────────────────────────────────────────────────────────────────────
 * O QUE NÃO PODE SE PERDER (docs/migration/DELTA-PRE-MIGRACAO.md §2)
 *
 * · **Badge de procedência** (item 2, ADR-0066 — "a caixa diz quem a
 *   desenhou"): DUAS fontes, nesta ordem.
 *   1. `violations[].origem`, DECLARADA por quem gravou o evento
 *      (`procedenciaDeclarada()`, em `components/shared/ProcedenciaEvento.tsx`,
 *      o mesmo módulo que a lista e os widgets usam). É afirmação de primeira mão.
 *   2. só na ausência dela, `classificarLatencia()` de
 *      `components/shared/ProcedenciaBadge.tsx` — o atraso entre captura e
 *      gravação, importado e não recopiado (se o limiar mudar lá, muda aqui).
 *   Por que a ordem importa (medido em 05/09): 4.609 dos 5.174 eventos do DEV
 *   têm caixa desenhada por PESSOA e `created_at == timestamp`, então o
 *   critério TEMPORAL nunca acendia — a tela ficava muda justamente onde tinha
 *   mais o que dizer. Indício não vence declaração.
 *   Sem nenhuma das duas: sem badge = sem afirmação (`alerts.timestamp` ainda
 *   nasce com DEFAULT NOW(), carimbar "AO VIVO" trocaria uma mentira por outra).
 *
 * · **Motivo do veredito** (item 5): campo `reason` no
 *   `POST /verification/:id/review`. É ele que desambigua o `reject` — "a
 *   pessoa estava de máscara", "a caixa pegou a pessoa errada" e "não dava
 *   para ver" pedem correções OPOSTAS. Sem ele o falso positivo não ensina
 *   nada além de "erramos". Mesmo `aria-label`, mesmo `maxLength`, mesma
 *   ida ao backend.
 *
 * · **Lupa da evidência** (item 3): a matemática é `pages/epi/lupaEvidencia.ts`
 *   — módulo PURO e testado, reusado inteiro. A cola com o DOM (roda
 *   não-passiva, pinça, teclas) é `useLupa()`, vizinho deste arquivo, e a
 *   gaveta de evidência da LISTA chama exatamente o mesmo hook.
 *   ⚠️ O módulo puro mora em `pages/epi/` mas NÃO é do front antigo: marcar
 *   como INFRA no `MANIFESTO-FRONT-ANTIGO.md`, senão some com a página velha.
 *
 * ─────────────────────────────────────────────────────────────────────────
 * ADR-0065 / 0066 / 0067 — o que esta tela NÃO afirma
 *
 * O desenho carimba "VIOLAÇÃO CONFIRMADA" no chip. Esta tela carimba
 * "CONFIRMADO", sem a palavra violação, porque `GET /api/alerts/:id` **não
 * devolve a polaridade**: a lista deriva `event_kind` ('violation' |
 * 'compliance') no SQL, o detalhe tem projeção explícita e não a carrega.
 * Afirmar violação sem esse campo é exatamente o que a ADR-0067 proíbe —
 * violação nasce de julgamento POSITIVO de ausência, nunca do silêncio.
 *
 * Pelo mesmo motivo a tela não diz QUEM julgou: a projeção do detalhe omite
 * `verified_by` de propósito, e sem o prefixo `user:` não dá para separar o
 * veredito humano do que a task Celery de pré-análise grava com
 * `verified_by='claude-haiku'` (ver `components/shared/VereditoHumano.tsx`).
 * O chip diz o que está REGISTRADO, e o `title` diz que a autoria não vem
 * neste endpoint. Os dois gaps estão no bloco PARA O BACKEND, no fim.
 */
import { useEffect, useRef, useState } from 'react'
import {
  AlertTriangle, ArrowLeft, Check, Clock, Cpu, ImageOff, Maximize2, Minus, Pencil, Plus, SearchX, X,
} from 'lucide-react'
import { Link, useParams } from 'react-router-dom'

import { HANDLES, boxFromDrag, clamp, moveBox, resizeBox, type HandleId } from '../../components/annotation/boxGeometry'
import type { Box } from '../../components/annotation/studioTypes'
import { classificarLatencia } from '../../components/shared/ProcedenciaBadge'
import { ORIGEM_HUMANA, procedenciaDeclarada } from '../../components/shared/ProcedenciaEvento'
import { useAuth } from '../../hooks/useAuth'
import { confiancaBruta, confiancaInternaOuCliente } from '../../services/confidenceDisplay'
import { ESCALA_MAX, ESCALA_MIN } from '../../pages/epi/lupaEvidencia'
import { ApiError, api } from '../../services/api'
import { labelForClass } from '../../utils/labels'
import { rotaNova } from '../RotasNovas'
import { LogikosLoader } from '../shell/LogikosLoader'
import * as s from './EventoDetalhe.css'
import { useLupa } from './useLupa'

/**
 * Rota da lista NO FRONT NOVO (de-para do DELTA: `/epi/alerts` → `/epi/eventos`).
 *
 * `rotaNova()` não é preciosismo: `'/epi/eventos'` cru NÃO é rota do front
 * antigo — cai no catch-all do `App.tsx`, o `RootRedirect` manda para
 * `/modules`, e a pessoa que clicou em "Eventos" no cabeçalho desta tela sai
 * do produto novo inteiro. Ficou assim porque a varredura de coexistência só
 * enxergava LITERAL, e o caminho estava guardado nesta constante (corrigido
 * junto, em `coexistencia.test.tsx`).
 */
const ROTA_EVENTOS = rotaNova('/epi/eventos')

type Bbox = [number, number, number, number]

/** Única unidade de bbox que esta tela sabe projetar (contrato de `domain/detectors/base.py`). */
export const BBOX_PIXELS = 'pixels_xywh_frame_original'

export interface Violacao {
  class: string
  confidence: number
  bbox?: Bbox
  bbox_unidade?: string
  /** Quem desenhou ESTA caixa, declarado por quem gravou o evento. Chega
   *  intacto: `GET /api/alerts/:id` devolve `violations` cru
   *  (`alerts/routes.py`, projeção do detalhe). Evento vindo do edge não
   *  declara nada — e aí a tela não afirma nada. */
  origem?: string
  /** Ferramenta da anotação humana ('manual', proposta aceita…). */
  anotacao_source?: string
  /** Marca da carga em lote do acervo de demonstração
   *  (`scripts/ops/eventos_acervo_rvb.py`). */
  lote?: string
}

/**
 * Procedência DECLARADA — a regra mora em
 * `components/shared/ProcedenciaEvento.tsx` desde a issue #670: a LISTA de
 * eventos, os dois widgets do dashboard e o histórico antigo liam SÓ o
 * critério temporal e apresentavam 4.609 anotações humanas como detecção do
 * modelo. Reexportada aqui porque esta tela foi onde a função nasceu (PR #669)
 * e é por este módulo que os testes dela entram.
 */
export {
  ORIGEM_HUMANA, ORIGEM_MODELO, procedenciaDeclarada, type ProcedenciaDeclarada,
} from '../../components/shared/ProcedenciaEvento'

/** Última correção de caixa registrada no ledger append-only do alerta
 *  (`violations_historico` no backend, ver `_ultima_correcao`). */
interface Correcao { por: string | null; por_nome?: string | null; em: string | null }

/** Projeção de `GET /api/alerts/:id` — só o que a rota realmente devolve. */
export interface Evento {
  id: string
  camera_id: string | null
  camera_name?: string | null
  violations: Violacao[]
  acknowledged: boolean
  captured_at: string | null
  created_at?: string | null
  evidence_url: string | null
  verification_verdict?: string | null
  verified_at?: string | null
  correcao_ultima?: Correcao | null
}

type Fase = 'carregando' | 'carregado' | 'vazio' | 'erro'
type Veredito = 'approve' | 'reject'

/** Modo do arrasto em curso sobre a caixa de correção — desenhar do zero,
 *  mover a caixa inteira, ou redimensionar por uma das 8 alças. */
type InteracaoCaixa =
  | { modo: 'desenhar'; x0: number; y0: number }
  | { modo: 'mover'; offX: number; offY: number }
  | { modo: 'redimensionar'; alca: HandleId; inicio: Box }

/**
 * bbox = [x, y, w, h] em PIXELS do frame ORIGINAL (canto superior-esquerdo,
 * NÃO centro) → caixa em % sobre a imagem renderizada. O alerta não carrega as
 * dimensões do frame; elas saem de `naturalWidth`/`naturalHeight` da <img>.
 *
 * Exportada porque é a única lógica não-trivial de posicionamento da tela.
 * (Mesma matemática de `AlertDetailPage.boxStyle`; copiada de propósito para o
 * front novo não importar um módulo do front antigo, que tem de poder sair.)
 */
export function caixaEmPorcento([x, y, w, h]: Bbox, natW: number, natH: number) {
  // toFixed(4) só apara o ruído binário; 4 casas em % é sub-pixel em qualquer frame.
  const pct = (n: number, total: number) => `${+((n / total) * 100).toFixed(4)}%`
  return { left: pct(x, natW), top: pct(y, natH), width: pct(w, natW), height: pct(h, natH) }
}

/**
 * bbox px (frame original) → caixa normalizada de `boxGeometry.ts` (mesma
 * matemática — centro + dimensões, 0–1 — do Estúdio de Anotação). Mover e
 * redimensionar por alça não são reescritos aqui, só convertidos na
 * fronteira do arrasto.
 */
function bboxParaBox([x, y, w, h]: Bbox, natW: number, natH: number): Box {
  return {
    id: 'correcao', classId: 0,
    xCenter: (x + w / 2) / natW, yCenter: (y + h / 2) / natH,
    width: w / natW, height: h / natH,
  }
}

/** Caminho de volta: caixa normalizada → bbox px (frame original), arredondado. */
function boxParaBbox(b: Box, natW: number, natH: number): Bbox {
  return [
    Math.round((b.xCenter - b.width / 2) * natW),
    Math.round((b.yCenter - b.height / 2) * natH),
    Math.round(b.width * natW),
    Math.round(b.height * natH),
  ]
}

/** Estado do veredito REGISTRADO. Cor + ícone + palavra — nunca só cor. */
const VEREDITO_CHIP = {
  approve: { variante: 'confirmado', palavra: 'CONFIRMADO', Icone: AlertTriangle },
  reject: { variante: 'descartado', palavra: 'DESCARTADO', Icone: X },
  nenhum: { variante: 'aguarda', palavra: 'AGUARDA VEREDITO', Icone: Clock },
} as const

function chipDoVeredito(verdict?: string | null) {
  if (verdict === 'approve') return VEREDITO_CHIP.approve
  if (verdict === 'reject') return VEREDITO_CHIP.reject
  return VEREDITO_CHIP.nenhum
}

const dataHora = (iso?: string | null) =>
  iso ? new Date(iso).toLocaleString('pt-BR') : '—'

/** Posição de cada alça — offset −6px, 11px de lado, conforme o handoff
 *  (`CorrigirCaixa.dc.html`). Tamanho real vem inline (contra-escala do zoom). */
const ALCA_POS: Record<HandleId, React.CSSProperties> = {
  nw: { left: '-6px', top: '-6px', cursor: 'nwse-resize' },
  n: { left: 'calc(50% - 5.5px)', top: '-6px', cursor: 'ns-resize' },
  ne: { right: '-6px', top: '-6px', cursor: 'nesw-resize' },
  e: { right: '-6px', top: 'calc(50% - 5.5px)', cursor: 'ew-resize' },
  se: { right: '-6px', bottom: '-6px', cursor: 'nwse-resize' },
  s: { left: 'calc(50% - 5.5px)', bottom: '-6px', cursor: 'ns-resize' },
  sw: { left: '-6px', bottom: '-6px', cursor: 'nesw-resize' },
  w: { left: '-6px', top: 'calc(50% - 5.5px)', cursor: 'ew-resize' },
}

export function EventoDetalhe() {
  const { id } = useParams<{ id: string }>()
  const { can, isSuperAdmin } = useAuth()

  const [evento, setEvento] = useState<Evento | null>(null)
  const [fase, setFase] = useState<Fase>('carregando')
  const [tentativa, setTentativa] = useState(0)
  /** Dimensões do frame ORIGINAL: só existem depois que a <img> carrega. */
  const [natural, setNatural] = useState<{ w: number; h: number } | null>(null)

  /** A URL assinada do R2 vale 1h (`ttl=3600`) e depois responde 403
   *  ExpiredRequest. Sem isto a <img> falhava calada e o palco ficava preto —
   *  o operador lia "evento sem evidência", que é mentira. */
  const [evidenciaFalhou, setEvidenciaFalhou] = useState(false)

  /** Motivo do veredito — opcional, mas é ele que desambigua o `reject`. */
  const [motivo, setMotivo] = useState('')
  const [salvando, setSalvando] = useState(false)
  const [erroAcao, setErroAcao] = useState<string | null>(null)
  /** 409: alguém julgou antes — informação, não falha (nunca `erroAcao`). */
  const [avisoAcao, setAvisoAcao] = useState<string | null>(null)

  // ── correção de caixa ────────────────────────────────────────────────────
  // Migrada de `pages/epi/AlertDetailPage.tsx` (PATCH /alerts/:id/violations)
  // — ver "PARA O DESIGN" no fim do arquivo.
  // `selecionada` é o índice da violação em edição; `null` = fora do modo.
  const imgRef = useRef<HTMLImageElement>(null)
  const [selecionada, setSelecionada] = useState<number | null>(null)
  const [rascunho, setRascunho] = useState<Bbox | null>(null)
  const [salvandoCaixa, setSalvandoCaixa] = useState(false)
  const [erroCaixa, setErroCaixa] = useState<string | null>(null)
  /** Arrasto EM CURSO — não precisa de re-render, só de leitura no próximo pointermove. */
  const interacaoCaixa = useRef<InteracaoCaixa | null>(null)

  useEffect(() => {
    let vivo = true
    setFase('carregando')
    setErroAcao(null)
    // Frame novo = dimensões novas. Sem isto a caixa do próximo evento cairia
    // projetada nas dimensões do frame anterior até a <img> carregar.
    setNatural(null)
    setEvidenciaFalhou(false)
    api.get<{ data?: { alert: Evento } }>(`/alerts/${id}`)
      .then((res) => {
        if (!vivo) return
        const a = res.data?.alert ?? null
        setEvento(a)
        setFase(a ? 'carregado' : 'vazio')
      })
      .catch((e: unknown) => {
        if (!vivo) return
        setEvento(null)
        // 404 é "não existe OU é de outro tenant" (C-01) — os dois são o mesmo
        // estado vazio para quem olha. Qualquer outro status é falha de carga.
        setFase(e instanceof ApiError && e.status === 404 ? 'vazio' : 'erro')
      })
    return () => { vivo = false }
  }, [id, tentativa])

  // ── lupa ──────────────────────────────────────────────────────────────────
  // A evidência é uma caixa de orelha num frame 1920x1080: sem ampliar, o dono
  // não julga. Zoom/pan vivem numa ÚNICA camada transformada que envolve a
  // <img> E as caixas — por isso a caixa escala junto, ancorada nos mesmos
  // pixels. Tirar as caixas de dentro dessa camada dessincroniza em silêncio.
  //
  // A cola com o DOM (roda não-passiva, pinça, teclas) mora em `useLupa` desde
  // que o painel de julgamento da LISTA passou a precisar da mesma lupa: dois
  // zooms copiados divergiriam no primeiro ajuste. Aqui só sobrou o que é
  // desta tela — o modo de CORREÇÃO de caixa, que sequestra o mesmo ponteiro.
  const {
    lupa, refPalco, aoDuploClique, ampliar, reenquadrar,
    aoDescerPonteiro: lupaDesce,
    aoMoverPonteiro: lupaMove,
    aoSoltarPonteiro: lupaSolta,
    aoTeclar: lupaTecla,
  } = useLupa()

  // Evento novo = enquadramento novo — e a correção de um evento não deve
  // sobreviver para o próximo.
  useEffect(() => {
    reenquadrar()
    setSelecionada(null)
    setRascunho(null)
    setErroCaixa(null)
  }, [id, reenquadrar])

  const evidencia = evento?.evidence_url ?? null

  /**
   * Ponto do cursor → coordenadas NORMALIZADAS (0–1) do frame, para a
   * matemática de `boxGeometry.ts`. O rect vem da <img>, não do palco: a
   * imagem está dentro da camada com `transform` da lupa — só o rect medido
   * reflete escala e pan (mesma razão de `pontoNoFrame` no front antigo).
   */
  const pontoNormalizado = (clientX: number, clientY: number) => {
    const img = imgRef.current
    if (!img) return null
    const r = img.getBoundingClientRect()
    if (!r.width || !r.height) return null
    return { x: clamp((clientX - r.left) / r.width, 0, 1), y: clamp((clientY - r.top) / r.height, 0, 1) }
  }

  const aoDescerPonteiro = (e: React.PointerEvent<HTMLDivElement>) => {
    if (selecionada !== null) {
      // Alças e a caixa de correção têm o próprio onPointerDown (com
      // stopPropagation) — só chega aqui quem começou fora delas, e isso
      // sempre é DESENHAR uma caixa nova.
      const pos = pontoNormalizado(e.clientX, e.clientY)
      if (pos) interacaoCaixa.current = { modo: 'desenhar', x0: pos.x, y0: pos.y }
      return
    }
    lupaDesce(e)
  }

  const aoMoverPonteiro = (e: React.PointerEvent<HTMLDivElement>) => {
    if (selecionada !== null) {
      const interacao = interacaoCaixa.current
      if (!interacao || !natural) return
      const pos = pontoNormalizado(e.clientX, e.clientY)
      if (!pos) return
      if (interacao.modo === 'desenhar') {
        const caixa = boxFromDrag(interacao.x0, interacao.y0, pos.x, pos.y, 0, 'correcao')
        // Arrasto pequeno demais (clique acidental) não sobrescreve o rascunho.
        if (caixa) setRascunho(boxParaBbox(caixa, natural.w, natural.h))
      } else if (interacao.modo === 'mover') {
        setRascunho((atual) => {
          if (!atual) return atual
          const caixa = moveBox(bboxParaBox(atual, natural.w, natural.h), pos.x - interacao.offX, pos.y - interacao.offY)
          return boxParaBbox(caixa, natural.w, natural.h)
        })
      } else {
        setRascunho(boxParaBbox(resizeBox(interacao.inicio, interacao.alca, pos.x, pos.y), natural.w, natural.h))
      }
      return
    }
    lupaMove(e)
  }

  const aoSoltarPonteiro = (e: React.PointerEvent<HTMLDivElement>) => {
    if (selecionada !== null) { interacaoCaixa.current = null; return }
    lupaSolta(e)
  }

  /** Teclado: zoom só na roda é inutilizável sem mouse. Não é enfeite. */
  const aoTeclar = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (selecionada !== null && e.key === 'Escape') { cancelarCorrecao(); e.preventDefault(); return }
    lupaTecla(e)
  }

  // ── veredito ──────────────────────────────────────────────────────────────

  /** Relê `GET /alerts/:id` sem derrubar o que já está na tela. */
  const recarregar = () =>
    api.get<{ data?: { alert: Evento } }>(`/alerts/${id}`)
      .then((res) => setEvento(res.data?.alert ?? null))
      .catch(() => { /* a tela segue mostrando o estado anterior */ })

  /**
   * `POST /verification/:id/review`. NÃO toca `/acknowledge`: reconhecer é
   * ciência do operador, veredito é verdade sobre a detecção. `reason` vai
   * junto sempre que houver — a rota e o service sempre aceitaram.
   */
  const darVeredito = async (verdict: Veredito) => {
    setSalvando(true)
    setErroAcao(null)
    setAvisoAcao(null)
    try {
      await api.post(`/verification/${id}/review`, {
        verdict,
        ...(motivo.trim() ? { reason: motivo.trim() } : {}),
      })
      setMotivo('')
      await recarregar()
    } catch (e) {
      // 409 = OUTRA PESSOA julgou este alerta primeiro (guarda
      // `verification_verdict IS NULL OR verified_by = <eu>` do UPDATE, em
      // verification_service.py). Não é falha do operador e não é erro desta
      // tela: a mensagem do servidor já diz QUEM julgou e QUANDO. Recarrega o
      // alerta para o chip do cabeçalho passar a mostrar o veredito que EXISTE
      // — a tela continua viva e informada, nunca vermelha.
      // ⛔ Mesma regra do bloco 4 de `Verificacao.tsx`: não transforme o 409 em
      // "Não foi possível registrar", que faz o operador clicar de novo.
      if (e instanceof ApiError && e.status === 409) {
        setAvisoAcao(e.message)
        setMotivo('')
        await recarregar()
      } else {
        setErroAcao('Não foi possível registrar o veredito.')
      }
    } finally {
      setSalvando(false)
    }
  }

  // ── correção de caixa ────────────────────────────────────────────────────

  /** bbox GRAVADO da violação `i` — ponto de partida do rascunho, e valor ao
   *  qual um arrasto degenerado (clique sem mover) simplesmente volta, por
   *  nunca ter sido sobrescrito (ver `aoMoverPonteiro`, modo 'desenhar'). */
  const bboxDe = (i: number | null): Bbox | null =>
    i === null ? null : evento?.violations[i]?.bbox ?? null

  const iniciarCorrecao = (i: number) => {
    setSelecionada(i)
    setRascunho(bboxDe(i))
    setErroCaixa(null)
  }

  const cancelarCorrecao = () => {
    setSelecionada(null)
    setRascunho(null)
    setErroCaixa(null)
  }

  /** Alça (redimensionar). `stopPropagation`: sem ele o pointerdown também
   *  cairia no palco e reiniciaria um DESENHO por cima. */
  const aoDescerAlca = (e: React.PointerEvent<HTMLSpanElement>, alca: HandleId) => {
    e.stopPropagation()
    if (!rascunho || !natural) return
    interacaoCaixa.current = { modo: 'redimensionar', alca, inicio: bboxParaBox(rascunho, natural.w, natural.h) }
  }

  /** Corpo da caixa de correção: arrastar MOVE, não redesenha. */
  const aoDescerCaixa = (e: React.PointerEvent<HTMLDivElement>) => {
    e.stopPropagation()
    if (!rascunho || !natural) return
    const pos = pontoNormalizado(e.clientX, e.clientY)
    if (!pos) return
    const caixa = bboxParaBox(rascunho, natural.w, natural.h)
    interacaoCaixa.current = { modo: 'mover', offX: pos.x - caixa.xCenter, offY: pos.y - caixa.yCenter }
  }

  /**
   * `PATCH /alerts/:id/violations` — mesmo contrato de
   * `pages/epi/AlertDetailPage.tsx:198-201`. O servidor carimba a unidade
   * (BBOX_PIXELS) e guarda o array anterior INTEIRO em `violations_historico`
   * — nada se perde, só se acrescenta.
   */
  const salvarCaixa = async () => {
    if (selecionada === null || !rascunho) return
    setSalvandoCaixa(true)
    setErroCaixa(null)
    try {
      const res = await api.patch<{ data?: { violations: Violacao[]; correcao_ultima: Correcao | null } }>(
        `/alerts/${id}/violations`,
        { correcoes: [{ index: selecionada, bbox: rascunho }] },
      )
      const d = res.data
      if (d) {
        setEvento((ev) => (ev ? { ...ev, violations: d.violations, correcao_ultima: d.correcao_ultima } : ev))
      }
      cancelarCorrecao()
    } catch {
      setErroCaixa('Não foi possível salvar a caixa.')
    } finally {
      setSalvandoCaixa(false)
    }
  }

  // ── estados de tela inteira ───────────────────────────────────────────────

  if (fase === 'carregando') {
    return <LogikosLoader estado="waiting" variante="fullscreen" rotulo="CARREGANDO EVENTO" tamanho={96} />
  }

  // ERRO antes de VAZIO, e a ordem importa: em `erro` o evento também é null,
  // e a ordem invertida mostraria "não encontrado" para uma falha de rede —
  // um 500 vira "esse evento não existe", que é mentira e mata o retry.
  if (fase === 'erro') {
    return (
      <div className={s.estadoCentral}>
        <AlertTriangle className={s.estadoIcone.falha} strokeWidth={1.5} aria-hidden />
        <span className={s.estadoTitulo}>Falha ao carregar o evento</span>
        <button type="button" className={s.botaoPrimario} onClick={() => setTentativa((t) => t + 1)}>
          Tentar novamente
        </button>
      </div>
    )
  }

  if (fase === 'vazio' || !evento) {
    return (
      <div className={s.estadoCentral}>
        <SearchX className={s.estadoIcone.neutro} strokeWidth={1.5} aria-hidden />
        <span className={s.estadoTitulo}>Evento não encontrado</span>
        <p className={s.estadoTexto}>
          O evento pode ter sido removido pela política de retenção (90 dias).
        </p>
        <Link to={ROTA_EVENTOS} className={s.botaoPrimario}>Voltar aos eventos</Link>
      </div>
    )
  }

  // ── carregado ─────────────────────────────────────────────────────────────

  const chip = chipDoVeredito(evento.verification_verdict)
  const classes = evento.violations ?? []
  const primeira = classes[0]
  // Só desenha o que sabemos projetar. Unidade ausente/estranha = origem
  // desconhecida: melhor nenhuma caixa que caixa mentirosa.
  const desenhaveis = classes.filter((v) => v.bbox && v.bbox_unidade === BBOX_PIXELS)
  const unidadeDesconhecida = classes.filter((v) => v.bbox && v.bbox_unidade !== BBOX_PIXELS)
  // Origem DECLARADA manda; o atraso entre captura e gravação (ADR-0066) só
  // fala quando não há declaração nenhuma — indício não vence afirmação.
  const procedencia = procedenciaDeclarada(evento.violations)
  const retroativa = !procedencia
    && classificarLatencia(evento.captured_at, evento.created_at) === 'retroativa'
  const podeJulgar = can('verification:write')
  const podeCorrigir = can('alerts:feedback')

  return (
    <div className={s.pagina}>
      <div className={s.cabecalho}>
        <Link to={ROTA_EVENTOS} className={s.voltar}>
          <ArrowLeft size={14} aria-hidden /> Eventos
        </Link>
        <h1 className={s.titulo}>
          Evento <span className={s.tituloId} title={evento.id}>#{evento.id.slice(0, 8)}</span>
        </h1>
        <span
          className={s.chip[chip.variante]}
          title="Veredito registrado sobre a detecção. Este endpoint não devolve quem julgou nem a polaridade do evento."
        >
          <chip.Icone className={s.chipIcone} aria-hidden /> {chip.palavra}
        </span>
      </div>

      <div className={s.corpo}>
        <div className={s.colunaEvidencia}>
          <div
            ref={refPalco}
            className={s.palco}
            tabIndex={0}
            role="group"
            aria-label="Frame da evidência. Roda do mouse ou + e − para ampliar, setas para deslocar, 0 para voltar ao enquadramento inteiro."
            onPointerDown={aoDescerPonteiro}
            onPointerMove={aoMoverPonteiro}
            onPointerUp={aoSoltarPonteiro}
            onPointerCancel={aoSoltarPonteiro}
            onDoubleClick={aoDuploClique}
            onKeyDown={aoTeclar}
            style={{ cursor: lupa.escala > ESCALA_MIN ? 'grab' : 'zoom-in' }}
          >
            <span className={s.selo.esquerda}>
              {(evento.camera_name ?? evento.camera_id?.slice(0, 8) ?? '—').toUpperCase()}
            </span>
            <span className={s.selo.direita}>{dataHora(evento.captured_at)}</span>

            {evidencia && !evidenciaFalhou ? (
              <div
                className={s.camada}
                style={{ transform: `translate(${lupa.x}px, ${lupa.y}px) scale(${lupa.escala})` }}
              >
                <div className={s.quadro}>
                  <img
                    ref={imgRef}
                    className={s.imagem}
                    src={evidencia}
                    alt="Frame da evidência"
                    draggable={false}   // o drag nativo da imagem atropela o pan
                    onError={() => setEvidenciaFalhou(true)}
                    onLoad={(e) => {
                      const img = e.currentTarget
                      if (img.naturalWidth > 0 && img.naturalHeight > 0) {
                        setNatural({ w: img.naturalWidth, h: img.naturalHeight })
                      }
                    }}
                  />
                  {natural && selecionada === null && desenhaveis.map((v, i) => (
                    <div
                      key={i}
                      data-testid="caixa-violacao"
                      className={s.caixa}
                      style={{
                        ...caixaEmPorcento(v.bbox as Bbox, natural.w, natural.h),
                        // contra-escala: a 8× uma borda de 2,5px come a evidência
                        borderWidth: `${2.5 / lupa.escala}px`,
                        borderRadius: `${4 / lupa.escala}px`,
                      }}
                    >
                      <span
                        className={s.caixaRotulo}
                        style={{ transform: `scale(${1 / lupa.escala})` }}
                      >
                        {/* Contrato A1c: número cru não prevê acerto — a etiqueta
                            sobre a evidência não tem espaço para a leitura honesta
                            (whiteSpace nowrap), então só superadmin vê o %; ver
                            services/confidenceDisplay.ts. */}
                        {labelForClass(v.class)}{isSuperAdmin ? ` · ${confiancaBruta(v.confidence)}` : ''}
                      </span>
                    </div>
                  ))}

                  {natural && selecionada !== null && (() => {
                    const original = classes[selecionada]
                    // Mesma regra do resto da tela: unidade ausente/estranha
                    // = origem desconhecida, sem caixa tracejada — nunca uma
                    // "onde a IA marcou" mentirosa.
                    const iaBbox = original?.bbox && original.bbox_unidade === BBOX_PIXELS ? original.bbox : null
                    // Quem desenhou a caixa ORIGINAL. Em 4.609 dos 5.174
                    // eventos do DEV foi uma PESSOA — carimbar "a IA marcou"
                    // por cima é atribuir ao modelo o trabalho de alguém, e
                    // ensina o operador a corrigir o que ninguém errou.
                    const rotuloOriginal = original?.origem === ORIGEM_HUMANA
                      ? 'ONDE A PESSOA MARCOU'
                      : 'ONDE A IA MARCOU'
                    return (
                      <>
                        {iaBbox && (
                          <div
                            className={s.caixaIA}
                            style={{
                              ...caixaEmPorcento(iaBbox, natural.w, natural.h),
                              borderWidth: `${2 / lupa.escala}px`,
                            }}
                          >
                            <span className={s.rotuloCaixaIA} style={{ transform: `scale(${1 / lupa.escala})` }}>
                              {rotuloOriginal}
                            </span>
                          </div>
                        )}
                        {rascunho && (
                          <div
                            data-testid="caixa-correcao"
                            className={s.caixaCorrecao}
                            style={{
                              ...caixaEmPorcento(rascunho, natural.w, natural.h),
                              borderWidth: `${2 / lupa.escala}px`,
                            }}
                            onPointerDown={aoDescerCaixa}
                          >
                            <span className={s.rotuloCaixaCorrecao} style={{ transform: `scale(${1 / lupa.escala})` }}>
                              SUA CORREÇÃO
                            </span>
                            {HANDLES.map((alca) => (
                              <span
                                key={alca}
                                className={s.alca}
                                style={{
                                  ...ALCA_POS[alca],
                                  width: `${11 / lupa.escala}px`,
                                  height: `${11 / lupa.escala}px`,
                                }}
                                onPointerDown={(e) => aoDescerAlca(e, alca)}
                              />
                            ))}
                          </div>
                        )}
                      </>
                    )
                  })()}
                </div>
              </div>
            ) : evidencia ? (
              // A evidência EXISTE no servidor; foi o link que não abriu.
              // Dizer "sem imagem" aqui apagaria a prova do acontecido.
              <div className={s.semImagem} role="alert">
                <ImageOff className={s.estadoIcone.falha} strokeWidth={1.5} aria-hidden />
                Não foi possível carregar a imagem desta evidência.
                <span className={s.estadoMono}>
                  O link assinado vale 1 hora — o deste evento pode ter expirado.
                </span>
                <button
                  type="button"
                  className={s.linkTentarNovamente}
                  onClick={() => setTentativa((t) => t + 1)}
                >
                  Gerar novo link e tentar de novo
                </button>
              </div>
            ) : (
              <div className={s.semImagem}>
                <SearchX className={s.estadoIcone.neutro} strokeWidth={1.5} aria-hidden />
                Sem imagem de evidência para este evento
              </div>
            )}
          </div>

          {selecionada !== null && (
            <p className={s.dicaCorrecao}>ARRASTE PARA DESENHAR · ALÇAS REDIMENSIONAM · ESC CANCELA</p>
          )}

          <div className={s.barraLupa}>
            <button type="button" className={s.botaoLupa} aria-label="Ampliar"
                    onClick={() => ampliar(1.5)} disabled={lupa.escala >= ESCALA_MAX}>
              <Plus size={15} aria-hidden />
            </button>
            <button type="button" className={s.botaoLupa} aria-label="Reduzir"
                    onClick={() => ampliar(1 / 1.5)} disabled={lupa.escala <= ESCALA_MIN}>
              <Minus size={15} aria-hidden />
            </button>
            <button type="button" className={s.botaoLupa} disabled={lupa.escala === ESCALA_MIN}
                    onClick={reenquadrar}>
              <Maximize2 size={14} aria-hidden /> Frame inteiro
            </button>
            <span className={s.dicaLupa}>
              {lupa.escala.toFixed(1).replace('.', ',')}× · roda amplia · arrastar desloca
            </span>
          </div>

          {evidencia && classes.length > 0 && classes.every((v) => !v.bbox) && (
            <p className={s.aviso}>Evento sem coordenadas gravadas — frame exibido sem marcação.</p>
          )}
          {evidencia && unidadeDesconhecida.length > 0 && (
            <p className={s.aviso}>
              Coordenadas de origem desconhecida — caixa não desenhada para{' '}
              {unidadeDesconhecida.length === 1 ? '1 detecção' : `${unidadeDesconhecida.length} detecções`}.
            </p>
          )}
        </div>

        <aside className={s.painel}>
          {primeira && (
            <span className={s.classeChip}>
              <AlertTriangle className={s.classeIcone} aria-hidden />
              {labelForClass(primeira.class).toUpperCase()}
            </span>
          )}

          <div className={s.grade}>
            <span className={s.rotulo}>Câmera</span>
            <span>{evento.camera_name ?? evento.camera_id ?? '—'}</span>

            <span className={s.rotulo}>Captura</span>
            <span className={s.valorMono}>
              {dataHora(evento.captured_at)}{' '}
              {/* Badge de procedência (ADR-0066). Só a afirmação NEGATIVA: sem
                  badge = sem afirmação. A tela do ACONTECIDO é justamente onde
                  a distância entre captura e gravação precisa aparecer. */}
              {procedencia && (
                <span
                  className={s.procedencia[procedencia.origem]}
                  title={procedencia.titulo}
                  data-testid="procedencia"
                >
                  {procedencia.origem === 'humana'
                    ? <Pencil className={s.procedenciaIcone} aria-hidden />
                    : <Cpu className={s.procedenciaIcone} aria-hidden />}
                  {' '}{procedencia.rotulo}
                </span>
              )}
              {retroativa && (
                <span
                  className={s.procedencia.retroativa}
                  title={`Capturado: ${evento.captured_at} · gravado: ${evento.created_at}`}
                >
                  <Clock className={s.procedenciaIcone} aria-hidden /> coleta retroativa
                </span>
              )}
            </span>

            <span className={s.rotulo}>Reconhecimento</span>
            <span>{evento.acknowledged ? 'Reconhecido' : 'Pendente'}</span>

            <span className={s.rotulo}>Veredito em</span>
            <span className={s.valorMono}>{dataHora(evento.verified_at)}</span>
          </div>

          <div className={s.bloco}>
            <span className={s.overline}>Detecções</span>
            {classes.length === 0 ? (
              <span className={s.rotulo}>Nenhuma detecção gravada neste evento.</span>
            ) : (
              <ul className={s.listaDeteccoes}>
                {classes.map((v, i) => (
                  <li
                    key={i}
                    className={selecionada === i ? `${s.deteccao} ${s.deteccaoSelecionada}` : s.deteccao}
                  >
                    <span className={s.deteccaoInfo}>
                      <span>{labelForClass(v.class)}</span>
                      <span className={s.confianca}>{confiancaInternaOuCliente(v.confidence, isSuperAdmin)}</span>
                    </span>
                    {evidencia && podeCorrigir && selecionada === null && (
                      <button type="button" className={s.botaoCorrigir} onClick={() => iniciarCorrecao(i)}>
                        Corrigir caixa
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>

          {evento.correcao_ultima && (
            <div className={s.badgeAutoria}>
              <Clock className={s.procedenciaIcone} aria-hidden />
              <p className={s.badgeAutoriaTexto} data-testid="badge-autoria">
                Caixa corrigida por <strong>{evento.correcao_ultima.por_nome ?? '—'}</strong>
                {evento.correcao_ultima.em && <><br />{dataHora(evento.correcao_ultima.em)}</>}
              </p>
            </div>
          )}

          {selecionada === null ? (
            <div className={s.vereditoBloco}>
              <span className={s.overline}>Veredito</span>
              <p className={s.ajuda}>
                O veredito é sobre a DETECÇÃO: ela é procedente ou o modelo errou.
                Não confundir com <strong>Reconhecer</strong>, que só registra que
                alguém viu o evento.
              </p>

              {podeJulgar ? (
                <>
                  <input
                    type="text"
                    className={s.campoMotivo}
                    value={motivo}
                    onChange={(e) => setMotivo(e.target.value)}
                    placeholder="Por quê? (opcional — ex.: a caixa pegou a luva do outro)"
                    aria-label="Motivo do veredito"
                    maxLength={280}
                  />
                  <p className={s.ajuda}>
                    O motivo é o que separa “a pessoa estava de máscara” de “a caixa
                    pegou a pessoa errada”. As duas coisas pedem correções opostas.
                  </p>
                  <div className={s.botoesVeredito}>
                    <button type="button" className={s.botaoVeredito.confirmar}
                            onClick={() => darVeredito('approve')} disabled={salvando}
                            title="A detecção está correta (procedente)">
                      <Check className={s.iconeVeredito} aria-hidden /> Confirmar
                    </button>
                    <button type="button" className={s.botaoVeredito.descartar}
                            onClick={() => darVeredito('reject')} disabled={salvando}
                            title="A detecção está errada (falso positivo)">
                      <X className={s.iconeVeredito} aria-hidden /> Descartar
                    </button>
                  </div>
                </>
              ) : (
                <p className={s.aviso}>
                  Você não tem permissão para julgar detecções. Peça a quem administra o seu acesso.
                </p>
              )}

              {erroAcao && <p role="alert" className={s.erro}>{erroAcao}</p>}
              {avisoAcao && <p role="status" className={s.aviso}>{avisoAcao}</p>}
            </div>
          ) : (
            <div className={s.vereditoBloco}>
              <span className={s.overline}>Coordenadas</span>
              <p className={s.ajuda}>
                Pixels do frame original, a partir do canto superior esquerdo.
                Este é o caminho para quem não usa o mouse com precisão — e ele
                faz tudo o que o arrasto faz.
              </p>
              <div className={s.gradeCoordenadas}>
                {(['X', 'Y', 'LARGURA', 'ALTURA'] as const).map((rotulo, eixo) => (
                  <label key={rotulo} className={s.campoCoordenada}>
                    <span className={s.rotuloCoordenada}>{rotulo}</span>
                    <input
                      type="number"
                      min={0}
                      className={s.inputCoordenada}
                      value={rascunho ? rascunho[eixo] : ''}
                      disabled={salvandoCaixa}
                      onChange={(e) => {
                        const n = Math.round(Number(e.target.value))
                        if (!Number.isFinite(n)) return
                        const base: Bbox = rascunho ?? [0, 0, 0, 0]
                        const nova: Bbox = [base[0], base[1], base[2], base[3]]
                        nova[eixo] = Math.max(0, n)
                        setRascunho(nova)
                      }}
                    />
                  </label>
                ))}
              </div>
              {natural && <span className={s.rotulo}>Frame: {natural.w} × {natural.h} px</span>}

              <div className={s.botoesVeredito}>
                <button
                  type="button"
                  className={s.botaoCorrecao.salvar}
                  onClick={salvarCaixa}
                  disabled={salvandoCaixa || !rascunho || rascunho[2] <= 0 || rascunho[3] <= 0}
                >
                  <Check className={s.iconeVeredito} aria-hidden /> Salvar caixa
                </button>
                <button type="button" className={s.botaoCorrecao.cancelar} onClick={cancelarCorrecao} disabled={salvandoCaixa}>
                  Cancelar
                </button>
              </div>

              {erroCaixa && (
                <p role="alert" className={s.erro}>
                  {erroCaixa}
                  <button type="button" className={s.linkTentarNovamente} onClick={salvarCaixa}>
                    Tentar novamente
                  </button>
                </p>
              )}
            </div>
          )}
        </aside>
      </div>
    </div>
  )
}

export default EventoDetalhe

/* ───────────────────────────────────────────────────────────────────────────
 * PARA O DESIGN — o desenho pede, o produto ainda não tem
 *
 * 1. **Player do evento** (▶ / 1× / faixa 24h clicável com scrubbing). Não há
 *    clipe: `alerts.evidence_key` é UM JPEG. Os clipes de 20-30s da ADR-0033
 *    não estão ligados ao alerta por endpoint nenhum. Botões de play sobre um
 *    still seriam controles falsos — omitidos.
 * 2. **Faixa "ÚLTIMAS 24H" com legenda OK / EVENTO / VIOLAÇÃO.**
 *    `GET /api/v1/events/timeline` devolve `{bucket, count}` — CONTAGEM, não
 *    severidade, e sem polaridade. Pintar contagem com a legenda do desenho
 *    trocaria o significado da faixa. Precisa de severidade por bucket.
 * 3. **← CAM-03 / CAM-05 →** (câmera vizinha). Não existe noção de vizinhança
 *    (nem ordem física, nem grupo/doca) em `ip_cameras`.
 * 4. **EVIDÊNCIAS · FRAMES T−2S / T / T+2S.** A API entrega um frame só.
 * 5. **Compartilhar** (expiração 1h/24h/7d, ver / ver+baixar). Nenhum endpoint
 *    de share link para evento no contrato do handoff — só o admin tem.
 * 6. **Criar ação corretiva** (sugestões, responsável, prazo). O módulo de
 *    ações (`EPI Ações`) não tem UMA rota mapeada no `contrato-dados.js`.
 * 7. **Zona** ("Zona de capacete obrigatório") e **Pessoas** ("2 na cena · 1 em
 *    violação"): o detalhe do alerta não devolve zona nem contagem de pessoas.
 * 8. **Largura**: o desenho desta tela usa 1360px; o token do shell é 1280.
 *    Usei o token. Se 1360 for regra desta tela, vira token.
 *
 * PARA O BACKEND — dois campos que a tela precisa e a projeção do detalhe omite
 *
 * A. `event_kind` ('violation' | 'compliance'). A LISTA deriva no SQL
 *    (`alert_repository.list_with_filters`), o DETALHE não. Sem ele o chip não
 *    pode dizer "VIOLAÇÃO CONFIRMADA" como o desenho quer — e ADR-0067 proíbe
 *    inferir. Um campo na projeção de `get_alert` resolve.
 * B. `verified_by` (ou um booleano `veredito_humano`). Sem o prefixo `user:`
 *    não dá para separar veredito de gente do que a task Celery grava com
 *    `verified_by='claude-haiku'`. Com ele, esta tela reusa
 *    `vereditoHumano()` e passa a dizer quem julgou.
 *
 * Migrada do front antigo (item que faltava nesta lista): correção de caixa
 * (`PATCH /alerts/:id/violations`, entrada por arrasto + 8 alças + teclado
 * das coordenadas, "Caixa corrigida por…"). Implementada a partir do desenho
 * `CorrigirCaixa.dc.html` — gate `alerts:feedback`, mover/redimensionar
 * reaproveitam a matemática de `components/annotation/boxGeometry.ts` (mesma
 * do Estúdio de Anotação), só convertida na fronteira px↔normalizado.
 * ─────────────────────────────────────────────────────────────────────────── */
