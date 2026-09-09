/**
 * EPI Verificação — `/epi/verificacao` (de-para: `/epi/verification`).
 *
 * Desenho: `EPI Verificação.dc.html`. Lógica real: `pages/VerificationQueuePage`.
 * Endpoints (contrato do design, `contrato-dados.js`, categoria
 * `events-alerts-media`):
 *
 *   GET  /api/verification/queue              → fila sem veredito do tenant (verdict IS NULL)
 *   POST /api/verification/<alert_id>/review  → veredito humano
 *   GET  /api/alerts/<alert_id>/snapshot      → URL assinada da evidência
 *   PATCH /api/alerts/<alert_id>/violations   → correção de caixa (ver bloco abaixo)
 *
 * ─── A FILA — leia antes de mexer ────────────────────────────────────────────
 *
 * Já custou caro aqui (PRs 496, 500, 487 e a fila que mentia sobre "N
 * restantes" com dois revisores ao mesmo tempo — paridade §3). As regras que
 * sobraram:
 *
 *  1. **Nunca reordena o que já está na tela; remove só quem já não é meu
 *     trabalho.** Item que EU decidi nesta sessão nunca sai (fica marcado —
 *     regra do "carimba" abaixo). Item que sumiu do servidor e não fui eu quem
 *     decidiu SAI — foi outra pessoa que revisou por fora, e deixá-lo na tela é
 *     apresentar para julgar (de novo) o que já foi julgado, sobrescrevendo em
 *     silêncio o veredito alheio. `carregar` só remove sob essa certeza (ver
 *     regra 2). O índice nunca é um número cru sobre o array: `carregar`
 *     recalcula a posição pelo ID do item que estava na tela — se um item ANTES
 *     dele sai, o índice não pode ficar apontando pra o vizinho errado, que era
 *     exatamente o bug do PR 496.
 *  2. **Reabastecimento por dedup de id, jamais por OFFSET — e só remove sob
 *     lote INTEIRO.** Decidir um alerta o tira do filtro (`verdict IS NULL`) no
 *     servidor: a próxima leitura da MESMA "página" já é outro conjunto. É a
 *     família exata do OFFSET que perdia 50% das linhas no PR 500. Quem separa
 *     "já está na fila" de "trabalho novo" é `anexarSemRepetir` — o mesmo
 *     mecanismo do estúdio de anotação, importado daqui e não reescrito. O
 *     endpoint corta em `limit` e não pagina: um lote CHEIO não prova que quem
 *     faltou foi revisado — pode estar só além do corte. Por isso a regra 1 só
 *     remove quando o lote veio ABAIXO do limite; lote cheio não tira ninguém.
 *  3. **Listener sem closure velha.** O `keydown` é re-registrado quando o item
 *     corrente muda. Um handler preso ao render antigo é o "ref atrasado" do
 *     PR 496: a tecla C carimba veredito no item que já não está na tela.
 *  4. **409 = alguém julgou primeiro; nunca um erro do operador.** O poll de
 *     15s reduz a janela, não a fecha: entre o último `carregar` e o clique,
 *     outra pessoa pode ter decidido o MESMO alerta. O backend recusa o
 *     segundo veredito (`verification_verdict IS NULL OR verified_by = <eu>`
 *     no UPDATE, `verification_service.py`) e responde 409 dizendo QUEM
 *     julgou e QUANDO. Aqui isso vira: carimbo `'outro'` (resolvido, mas não
 *     é meu veredito — `Marca`), toast com a frase do servidor, e AVANÇA.
 *     Nada do que o operador já fez se perde, e a tela nunca escreve
 *     "Confirmado" por uma decisão que não foi dele. ⛔ Não transforme o 409
 *     em `toast.error` genérico: "Erro ao registrar o veredito" faz o
 *     operador clicar de novo no que já foi resolvido.
 *
 *     A colisão também é reduzida na ORIGEM: `GET /queue` manda o `user_id` e
 *     o servidor gira a fila por trilha (`_trilha`), então três operadores
 *     abrem alertas DIFERENTES em vez do mesmo. A fila continua sendo a
 *     mesma para todos (nada é filtrado, `total` não muda) — só o ponto de
 *     partida difere. Por isso a ordem entregue a UM operador não é mais a
 *     ordem canônica pura, e continua valendo a regra: **o cliente não
 *     reordena** (ver bloco "FILA POR INCERTEZA").
 *
 * ⚠️ Este endpoint NÃO pagina (sem cursor, sem offset — `limit` ≤100 e pronto).
 * Por isso o reabastecimento é o MESMO polling de 15s do front antigo, e não
 * `precisaDeReabastecimento`: pedir "página 2" a um endpoint que não tem
 * páginas seria inventar paginação, e inventar paginação é como o PR 500 começou.
 *
 * ⚠️ **"N RESTANTES" e o gate de "Fila zerada" usam `total` (verdade do
 * servidor), não `fila.length`.** A tela já escreveu "Fila zerada" com
 * centenas de alertas pendentes ainda no banco — `fila.length` é só os
 * `LIMITE` (50) mais incertos carregados agora, nunca "quanto falta de
 * verdade". Ver `restantes` e o comentário do endpoint em `carregar`.
 *
 * ─── FILA POR INCERTEZA (delta §2 item 9 — não podia se perder) ──────────────
 *
 * O que ordena a fila é o quanto o modelo está EM DÚVIDA, não a hora do
 * alerta. **Quem ordena é o SERVIDOR** (`get_human_queue`,
 * verification_service.py), em DUAS camadas: 1) `rank_na_rajada` — dentro de
 * cada rajada (câmera+classe repetida em <60s), o mais incerto vira rank 1,
 * e um representante de CADA rajada aparece antes de qualquer rank 2 (dedup
 * NÃO filtra ninguém, rodada 3 — só reordena: irmãos de rajada continuam
 * contados e voltam depois); 2) incerteza (`ABS(confidence - 0.5)`) desempata
 * dentro da camada. Antes ordenava por `created_at DESC`, e como o endpoint
 * não pagina (`LIMIT` corta DE VERDADE), a ordem decidia QUAIS itens
 * apareciam: medido no DEV, os 50 mais recentes tinham confiança 0,90-1,00
 * (o modelo já tinha certeza) e o operador nunca alcançava os casos
 * ambíguos.
 *
 * ⚠️ **O CLIENTE NÃO REORDENA o lote (rodada 4).** Chegou a reordenar por
 * `ordenarPorIncerteza` (só incerteza, sem noção de rajada) — isso DESFAZIA
 * a camada 1 do servidor (um irmão de rajada com incerteza baixa intercalava
 * antes do representante de OUTRA rajada, devolvendo a fila à ordem que a
 * rodada 1 já tinha reprovado). `carregar` agora passa `itens` direto pra
 * `anexarSemRepetir` — a ordem renderizada É a ordem que o servidor mandou,
 * ponto. `incertezaDe`/`ordenarPorIncerteza` continuam exportadas (testadas
 * como funções puras, documentam a fórmula — mesma de
 * `FrameRepository._INCERTEZA_SQL`) mas NINGUÉM as chama no caminho de
 * render; não reintroduza a chamada em `carregar` (teste de mutação:
 * "a ordem renderizada é a ordem que o SERVIDOR devolveu"). **A ordenação é
 * aplicada UMA VEZ, no servidor** — nunca de novo no cliente, nunca à fila
 * que já está na tela (regra 1).
 *
 * ─── LUPA (pan+zoom) E CORREÇÃO DE CAIXA (contrato B1) ───────────────────────
 *
 * Duas peças reusadas, não reescritas:
 *  · `pages/epi/lupaEvidencia.ts` — `proximoEstado()`, estado puro de
 *    zoom+pan com clamp (roda, arrasto, pinça). Mesma origem de
 *    `EventoDetalhe.tsx`.
 *  · `components/annotation/boxGeometry.ts` — mover/redimensionar por 8
 *    alças, mesma matemática do Estúdio de Anotação.
 *
 * `PATCH /alerts/<id>/violations` é o MESMO endpoint de `EventoDetalhe.tsx`:
 * só o `bbox` (pixels do frame original) vai ao servidor — ele carimba
 * `bbox_unidade` e preserva `class`/`confidence` (corrigir POSIÇÃO não é
 * porta para reescrever CLASSE). A autoria (ADR-0066: "a caixa diz quem a
 * desenhou") volta em `correcao_ultima` e é mostrada com NOME, nunca UUID cru
 * — mesmo tratamento de `EventoDetalhe.tsx`.
 *
 * Enquanto uma caixa está em edição (`selecionada !== null`), os atalhos de
 * fila (← → C R) ficam mudos — só Escape cancela. Editar é modo EXPLÍCITO
 * (botão "Corrigir caixa"); um "C" digitado no meio de um arrasto não pode
 * carimbar veredito e abandonar a correção em curso.
 *
 * ponytail: a cola de pan+zoom+correção-de-caixa (handlers de ponteiro, wheel,
 * conversão px↔normalizado) é uma cópia physical de `EventoDetalhe.tsx` — as
 * duas telas repetem ~80 linhas de glue porque cada uma tem seu próprio JSX/
 * CSS de palco. Se uma TERCEIRA tela precisar do mesmo par lupa+caixa, vale
 * extrair um hook (`useLupaECaixa`); com duas, extrair agora seria abstração
 * para um caso que ainda não existe.
 *
 * ─── PARA O DESIGN / PARA O BACKEND ─────────────────────────────────────────
 *
 *  · **"Enviar para anotação · Estúdio" (tecla A) não tem endpoint.** Varri as
 *    421 linhas do contrato: nenhuma rota leva um ALERTA para a fila de
 *    anotação (`/api/training/*` parte de `training_frames`, não de `alerts`;
 *    `POST /api/v1/feedback` é GAP-DE-PRODUTO de outra tela e ainda 500). O
 *    botão fica no lugar do desenho, **desabilitado e dizendo por quê** — e o
 *    atalho A sai da barra de atalhos, porque anunciar tecla que não faz nada é
 *    pior que não anunciar. O contador "N ENVIADOS P/ ANOTAÇÃO" do cabeçalho
 *    cai junto: sem a ação, seria um zero eterno fingindo métrica.
 *  · **"Zona"** não existe em `alerts` (nem coluna, nem no payload da fila). No
 *    lugar, a ficha mostra **Confiança** e o **motivo da IA** — que são o dado
 *    real e o que justifica a posição do item na fila por incerteza.
 *  · A evidência precisa de uma segunda chamada (`/alerts/<id>/snapshot`): a
 *    fila devolve `evidence_key`, não URL assinada. Se o design quiser uma
 *    chamada só, o pedido ao backend é `evidence_url` no item da fila.
 *  · **Correção de caixa** só cobre a detecção PRINCIPAL (`desenhaveis[0]`) —
 *    o desenho desta tela não lista múltiplas detecções por item (diferente
 *    de `EventoDetalhe.tsx`); se um alerta com várias violações precisar
 *    corrigir mais de uma, o pedido ao design é uma lista igual à do detalhe.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  AlertTriangle, ArrowLeft, ArrowRight, Check, ImageOff, Lock, Minus, PenLine, Plus,
  ShieldCheck, SquarePen, X,
} from 'lucide-react'
import { Link } from 'react-router-dom'

import { anexarSemRepetir } from '../../components/annotation/studioQueue'
import { HANDLES, boxFromDrag, clamp, moveBox, resizeBox, type HandleId } from '../../components/annotation/boxGeometry'
import type { Box } from '../../components/annotation/studioTypes'
import { procedenciaDeclarada } from '../../components/shared/ProcedenciaEvento'
import { useAuth } from '../../hooks/useAuth'
import {
  ESCALA_MAX, ESCALA_MIN, LUPA_INICIAL, distanciaEntre, proximoEstado,
  type EventoLupa, type Palco,
} from '../../pages/epi/lupaEvidencia'
import { api, ApiError } from '../../services/api'
import { confiancaInternaOuCliente } from '../../services/confidenceDisplay'
import { useToast } from '../../components/ui/Toast/useToast'
import { labelForClass, MOTIVOS_VERIFICACAO, type MotivoVerificacao } from '../../utils/labels'
import { agruparPorRajada } from '../../utils/rajadas'
import { LogikosLoader } from '../shell/LogikosLoader'
import * as s from './Verificacao.css'
import { rotaNova } from '../RotasNovas'
import { estiloDaCaixa, referenciaDaCaixa } from '../../services/bboxProjecao'

/** Única unidade de bbox projetável (contrato de `domain/detectors/base.py`). */

type Veredito = 'approve' | 'reject'

/** O que ficou carimbado no item nesta sessão. `'outro'` = OUTRA pessoa
 *  julgou antes de mim (409 do backend) — resolvido, mas não é MEU veredito,
 *  e a tela não pode escrever "Confirmado" por uma decisão que não foi minha. */
type Marca = Veredito | 'outro' 
type Bbox = [number, number, number, number]

interface Violacao {
  class: string
  confidence?: number
  bbox?: Bbox
  bbox_unidade?: string
  /** Quem desenhou ESTA caixa, declarado por quem gravou o evento. Chega na
   *  fila porque `get_human_queue` faz `SELECT a.*` — o JSONB `violations`
   *  vem cru do banco, com `origem` e `lote` dentro. */
  origem?: string
  /** Marca da carga em lote do acervo de demonstração. */
  lote?: string
}

/** Última correção de caixa registrada no ledger append-only do alerta
 *  (`violations_historico` no backend, ver `_ultima_correcao`) — mesmo
 *  formato de `EventoDetalhe.tsx`. */
interface Correcao { por: string | null; por_nome?: string | null; em: string | null }

export interface ItemVerificacao {
  id: string
  camera_id?: string | null
  camera_name?: string | null
  class_name?: string | null
  confidence?: number
  violations?: Violacao[]
  verification_reason?: string | null
  evidence_key?: string | null
  created_at?: string | null
  timestamp?: string | null
  correcao_ultima?: Correcao | null
}

/** Envelope de `GET /verification/queue`. `total` é a contagem REAL do
 *  servidor (mesmo WHERE do `items` — verdict nulo + exclusão de
 *  conformidade + dedup, ver `get_queue_count`); `count` é só `len(items)`,
 *  capado no `limit`. Backends antigos (ou mocks de teste) podem não mandar
 *  `total` — nesse caso a tela cai para a contagem local (ver `restantes`). */
interface RespostaFila {
  items?: ItemVerificacao[]
  count?: number
  total?: number
}

/** Traço de aprendizado ativo do backend: `MIN(ABS(confidence - 0.5))`.
 *
 * A confiança do próprio alerta entra junto com a das propostas: o payload da
 * fila traz as duas, e `violations` vazio é comum em alerta de classe única —
 * ignorá-la mandaria esses itens para o fim como se não houvesse sinal.
 *
 * `1` (o `COALESCE(..., 1.0)` do SQL) quando não há confiança nenhuma para
 * ordenar: vai para o FIM, explicitamente. Pôr no começo o que não se sabe
 * medir seria ordem arbitrária vestida de prioridade. */
export function incertezaDe(item: ItemVerificacao): number {
  const confiancas = (item.violations ?? [])
    .map((v) => v.confidence)
    .concat(item.confidence)
    .filter((c): c is number => typeof c === 'number' && Number.isFinite(c))
  if (confiancas.length === 0) return 1
  return Math.min(...confiancas.map((c) => Math.abs(c - 0.5)))
}

/** Mais duvidoso primeiro. Empate mantém a ordem do servidor (sort estável) —
 *  reordenar empate seria mexer na fila sem motivo. */
export function ordenarPorIncerteza(itens: readonly ItemVerificacao[]): ItemVerificacao[] {
  return [...itens].sort((a, b) => incertezaDe(a) - incertezaDe(b))
}

/** bbox = [x, y, w, h] em pixels do frame ORIGINAL → % sobre a <img>. */
function caixaEmPorcento(
  [x, y, w, h]: Bbox,
  natW: number,
  natH: number,
) {
  const pct = (n: number, total: number) => `${+((n / total) * 100).toFixed(4)}%`
  return { left: pct(x, natW), top: pct(y, natH), width: pct(w, natW), height: pct(h, natH) }
}

/** Recorte da detecção: a MESMA imagem, enquadrada na caixa por background.
 *  ⛔ NÃO MEXER na aparência — o cliente elogiou este bloco explicitamente. */
function estiloRecorte(
  [x, y, w, h]: Bbox,
  natW: number,
  natH: number,
  url: string,
) {
  const sobraX = natW - w
  const sobraY = natH - h
  return {
    backgroundImage: `url(${url})`,
    backgroundSize: `${(natW / w) * 100}% ${(natH / h) * 100}%`,
    backgroundPosition: `${sobraX > 0 ? (x / sobraX) * 100 : 0}% ${sobraY > 0 ? (y / sobraY) * 100 : 0}%`,
  }
}

function horaDe(item: ItemVerificacao): string {
  const bruto = item.created_at ?? item.timestamp
  if (!bruto) return '—'
  const d = new Date(bruto)
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString('pt-BR')
}

function classeDe(item: ItemVerificacao): string {
  return item.class_name ?? item.violations?.[0]?.class ?? '—'
}

const dataHora = (iso?: string | null) => (iso ? new Date(iso).toLocaleString('pt-BR') : '—')

/** bbox px (frame original) → caixa normalizada de `boxGeometry.ts` (mesma
 *  matemática — centro + dimensões, 0–1 — do Estúdio de Anotação e de
 *  `EventoDetalhe.tsx`). Mover e redimensionar por alça não são reescritos
 *  aqui, só convertidos na fronteira do arrasto. */
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

/** Modo do arrasto em curso sobre a caixa de correção — desenhar do zero,
 *  mover a caixa inteira, ou redimensionar por uma das 8 alças. */
type InteracaoCaixa =
  | { modo: 'desenhar'; x0: number; y0: number }
  | { modo: 'mover'; offX: number; offY: number }
  | { modo: 'redimensionar'; alca: HandleId; inicio: Box }

/** Posição de cada alça — mesmo layout de `EventoDetalhe.tsx`. Tamanho real
 *  vem inline (contra-escala do zoom). */
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

/** Mesmo intervalo do front antigo — é assim que alerta novo entra na fila. */
const POLL_MS = 15_000
const LIMITE = 50

export function Verificacao() {
  const { can, isSuperAdmin } = useAuth()
  const toast = useToast()
  const podeLer = can('verification:read')
  const podeEscrever = can('verification:write')
  const podeCorrigir = can('alerts:feedback')

  // Ver regra 1 do cabeçalho: item meu não sai; item de outro que sumiu do
  // servidor sai (sob a certeza da regra 2).
  const [fila, setFila] = useState<ItemVerificacao[]>([])
  const [indice, setIndice] = useState(0)
  const [decididos, setDecididos] = useState<Record<string, Marca>>({})
  // Verdade do servidor para "N RESTANTES" — ver `restantes` abaixo e o
  // comentário do endpoint em `carregar`. `null` até o primeiro sync OK.
  const [totalServidor, setTotalServidor] = useState<number | null>(null)
  // Quantos itens já estavam em `decididos` no momento do ÚLTIMO sync bem
  // sucedido — a diferença para `decididos` atual é "decisão feita DEPOIS do
  // último sync", que ainda não pode estar refletida em `totalServidor`.
  const decididosNoUltimoSyncRef = useRef(0)
  const [carregando, setCarregando] = useState(true)
  const [erro, setErro] = useState<string | null>(null)
  const [enviando, setEnviando] = useState(false)
  // Motivo estruturado do veredito (contrato B2) — obrigatório pra rejeitar,
  // opcional pra confirmar. Reseta a cada item novo (ver efeito abaixo):
  // motivo escolhido para o item anterior não pode vazar pro próximo.
  const [motivo, setMotivo] = useState<MotivoVerificacao | ''>('')
  const [motivoFaltando, setMotivoFaltando] = useState(false)
  const [urlEvidencia, setUrlEvidencia] = useState<string | null>(null)
  const [natural, setNatural] = useState<{ w: number; h: number } | null>(null)
  const cacheUrl = useRef(new Map<string, string | null>())
  // Trava síncrona: `enviando` só vale no próximo render, e duas teclas C no
  // MESMO tick leem `false` nas duas — dois POST, dois carimbos. Veredito é
  // dado de treino; duplicar aqui suja o dataset em silêncio.
  const enviandoRef = useRef(false)

  // ── lupa (pan + zoom) ────────────────────────────────────────────────────
  // Estado puro em `lupaEvidencia.ts` — REUSADO, não reescrito (contrato B1).
  const palcoRef = useRef<HTMLDivElement>(null)
  const imgRef = useRef<HTMLImageElement>(null)
  const [lupa, setLupa] = useState(LUPA_INICIAL)
  // O listener de wheel é registrado uma vez (precisa ser não-passivo); o ref
  // dá a ele o estado atual sem re-registrar.
  const lupaRef = useRef(lupa)
  lupaRef.current = lupa
  const ponteiros = useRef(new Map<number, { x: number; y: number }>())
  const distPinca = useRef(0)

  // ── correção de caixa ────────────────────────────────────────────────────
  // Mesmo contrato de `EventoDetalhe.tsx` (`PATCH /alerts/:id/violations`),
  // migrado aqui para o alerta CORRENTE da fila.
  const [selecionada, setSelecionada] = useState<number | null>(null)
  const [rascunho, setRascunho] = useState<Bbox | null>(null)
  const [salvandoCaixa, setSalvandoCaixa] = useState(false)
  const [erroCaixa, setErroCaixa] = useState<string | null>(null)
  /** Arrasto EM CURSO — não precisa de re-render, só de leitura no próximo pointermove. */
  const interacaoCaixa = useRef<InteracaoCaixa | null>(null)

  // Espelhos para leitura fora do render, de dentro de `carregar`: é um
  // useCallback estável (deps `[]`, como sempre foi) e não pode fechar sobre
  // fila/índice/decididos/toast do render em que foi criado. `toast` também
  // precisa de espelho — não é seguro assumir que ele é estável: entrar como
  // dep recriaria `carregar` a cada valor novo, e o efeito de polling abaixo
  // (que depende de `carregar`) desmontaria e chamaria `carregar()` nesse
  // instante — se a própria chamada gerar um `toast` novo (como em qualquer
  // dublê de teste que devolve `{ ...vi.fn() }` a cada render), isso realimenta
  // o próprio gatilho e derruba o processo em OOM.
  const filaRef = useRef<ItemVerificacao[]>([])
  const indiceRef = useRef(0)
  const decididosRef = useRef<Record<string, Marca>>({})
  const toastRef = useRef(toast)
  useEffect(() => {
    filaRef.current = fila
    indiceRef.current = indice
    decididosRef.current = decididos
    toastRef.current = toast
  }, [fila, indice, decididos, toast])

  const carregar = useCallback(async () => {
    try {
      const res = await api.get<{ data?: RespostaFila }>(`/verification/queue?limit=${LIMITE}`)
      const itens = res?.data?.items ?? []
      const idsServidor = new Set(itens.map((i) => i.id))

      // `total` é a verdade do servidor no INSTANTE deste request. Decisões
      // feitas DEPOIS (ver `restantes`) descontam por cima até o próximo
      // sync realinhar tudo — é o que corrige o "Fila zerada" com centenas
      // no banco (docblock do topo): antes, "quanto falta" só existia como
      // `fila.length`, o array LOCAL de no máximo `LIMITE` itens.
      const totalNovo = res?.data?.total
      if (typeof totalNovo === 'number') {
        setTotalServidor(totalNovo)
        decididosNoUltimoSyncRef.current = Object.keys(decididosRef.current).length
      }

      // Lote CHEIO não prova nada sobre quem faltou (regra 2) — só remove
      // abaixo do limite, e nunca remove o que EU decidi (regra 1).
      const truncado = itens.length >= LIMITE
      const idAntes = filaRef.current[indiceRef.current]?.id
      const continuam = filaRef.current.filter(
        (i) => truncado || idsServidor.has(i.id) || Boolean(decididosRef.current[i.id]),
      )
      // Anexa sem repetir, na ORDEM QUE O SERVIDOR MANDOU — nunca reordena
      // quem já ficou, e não reordena o lote novo por conta própria (ver
      // docblock do topo: o servidor já rankeia por rajada + incerteza; um
      // resort aqui embaralharia esse trabalho de volta pra ordem errada).
      const nova = anexarSemRepetir(continuam, itens)

      // O item na tela sumiu e não fui eu quem decidiu: outra pessoa revisou
      // por fora enquanto o operador olhava para ele. Índice pelo ID, nunca
      // pela posição crua — item removido ANTES dele encolheria o array.
      const novoIndice = idAntes === undefined ? indiceRef.current : nova.findIndex((i) => i.id === idAntes)
      if (idAntes !== undefined && novoIndice === -1) {
        toastRef.current.info(
          'Alerta já revisado',
          'Outra pessoa decidiu este alerta enquanto ele estava aberto aqui.',
        )
      }
      setFila(nova)
      setIndice(novoIndice === -1 ? Math.min(indiceRef.current, Math.max(0, nova.length - 1)) : novoIndice)
      setErro(null)
    } catch (e) {
      setErro(e instanceof Error ? e.message : 'Erro ao carregar a fila')
    } finally {
      setCarregando(false)
    }
  }, [])

  useEffect(() => {
    if (!podeLer) {
      setCarregando(false)
      return
    }
    void carregar()
    const id = setInterval(() => void carregar(), POLL_MS)
    return () => clearInterval(id)
  }, [carregar, podeLer])

  const atual = fila[indice]
  // Progresso do LOTE carregado (feedback do "quanto já venci do que estou
  // olhando agora") — continua local de propósito, decisão é instantânea.
  const pendentesLocal = useMemo(
    () => fila.filter((i) => !decididos[i.id]).length,
    [fila, decididos],
  )
  const total = fila.length
  const progresso = total === 0 ? 0 : Math.round(((total - pendentesLocal) / total) * 100)

  // "N RESTANTES" e o gate de fila-vazia usam a VERDADE DO SERVIDOR
  // (`totalServidor`, de `get_queue_count` — ver `carregar`), não
  // `fila.length`: o array local é só os `LIMITE` (50) mais incertos, e a
  // tela já escreveu "Fila zerada" com centenas ainda no banco (docblock do
  // topo do arquivo — exatamente o bug que este contador existe pra matar).
  // Decisões feitas DEPOIS do último sync descontam na hora
  // (`decididosDesdeSync`), pro operador ver o número cair no clique — sem
  // isso "2 RESTANTES" só apareceria no próximo poll, até 15s depois.
  const decididosDesdeSync = Object.keys(decididos).length - decididosNoUltimoSyncRef.current
  const restantes = totalServidor === null
    ? pendentesLocal
    : Math.max(0, totalServidor - decididosDesdeSync)

  // Evidência do item corrente. A fila entrega `evidence_key`; a URL assinada
  // vem de /alerts/<id>/snapshot. Cache por id: voltar com ← não repede.
  // Item novo também reseta lupa e correção de caixa em curso — nenhum dos
  // dois pode sobreviver para o próximo alerta da fila.
  //
  // ⚠️ Dep é `atual?.id`, NUNCA o objeto `atual` inteiro: `salvarCaixa` chama
  // `setFila` com um NOVO objeto para o item corrigido (mesmo id, violations
  // atualizadas) — se a dep fosse `atual`, esse `setNatural(null)` disparava
  // de novo aqui, e como a <img> não recarrega (mesmo `src`), `onLoad` nunca
  // mais dispara e a caixa recém-salva ficava invisível para sempre.
  useEffect(() => {
    setLupa(LUPA_INICIAL)
    setNatural(null)
    setSelecionada(null)
    setRascunho(null)
    setErroCaixa(null)
    if (!atual) {
      setUrlEvidencia(null)
      return
    }
    if (!atual.evidence_key) {
      setUrlEvidencia(null)
      return
    }
    const cacheado = cacheUrl.current.get(atual.id)
    if (cacheado !== undefined) {
      setUrlEvidencia(cacheado)
      return
    }
    let vivo = true
    setUrlEvidencia(null)
    api
      .get<{ data?: { snapshot_url?: string } }>(`/alerts/${atual.id}/snapshot`)
      .then((r) => {
        const url = r?.data?.snapshot_url ?? null
        cacheUrl.current.set(atual.id, url)
        if (vivo) setUrlEvidencia(url)
      })
      .catch(() => {
        // Sem evidência a tela ainda serve (classe, câmera, hora, motivo).
        // Marcar como null no cache evita re-pedir a cada ← →.
        cacheUrl.current.set(atual.id, null)
        if (vivo) setUrlEvidencia(null)
      })
    return () => {
      vivo = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- ver comentário acima: dep é a IDENTIDADE, não o objeto.
  }, [atual?.id])

  // Item novo na tela → motivo do item anterior não pode vazar (change-of-mind
  // num item já decidido é a única razão legítima de o motivo persistir, e aí
  // `atual.id` não muda).
  useEffect(() => {
    setMotivo('')
    setMotivoFaltando(false)
  }, [atual?.id])

  const avancar = useCallback(() => {
    setIndice((i) => Math.min(i + 1, Math.max(0, fila.length - 1)))
  }, [fila.length])

  const voltar = useCallback(() => {
    setIndice((i) => Math.max(0, i - 1))
  }, [])

  const decidir = useCallback(
    async (verdict: Veredito) => {
      // Item revisado por outro já saiu de `fila` em `carregar` — não há mais
      // botão para clicar nele. O que resta aqui é honrar o 409 do backend
      // quando a revisão alheia chegou ENTRE o último poll e este clique
      // (regra 4 do cabeçalho) — nunca engolir em silêncio, nunca
      // sobrescrever.
      if (!atual || !podeEscrever || enviandoRef.current) return
      // Motivo é obrigatório pra REJEITAR — é o que ensina a calibração (o
      // "EPI está presente" mais que qualquer outro). Pra confirmar é
      // opcional: não bloqueia, só viaja se foi escolhido.
      if (verdict === 'reject' && !motivo) {
        setMotivoFaltando(true)
        toast.error(
          'Selecione um motivo para rejeitar',
          'O motivo é o que alimenta a recalibração do modelo.',
        )
        return
      }
      enviandoRef.current = true
      setEnviando(true)
      try {
        await api.post(`/verification/${atual.id}/review`, {
          verdict,
          ...(motivo ? { reason: motivo } : {}),
        })
        // Carimba NA POSIÇÃO. Não remove: ver regra 1 do cabeçalho.
        setDecididos((d) => ({ ...d, [atual.id]: verdict }))
        toast.success(verdict === 'approve' ? 'Alerta confirmado' : 'Alerta rejeitado')
        setMotivo('')
        setMotivoFaltando(false)
        avancar()
      } catch (e) {
        // 409 = outra pessoa julgou este alerta antes de mim (guarda
        // `verification_verdict IS NULL` do backend, bloco 4). NÃO é erro do
        // operador e o trabalho dele não pode evaporar: o item é carimbado
        // como resolvido POR OUTRO (nunca como veredito meu — seria mentir
        // sobre a autoria), o motivo escolhido é limpo e a fila AVANÇA. A
        // mensagem vem pronta do servidor ("Maria Silva já avaliou este
        // alerta há 2 minutos") — quem julgou e quando, que é o que o
        // operador precisa pra não achar que perdeu o clique.
        if (e instanceof ApiError && e.status === 409) {
          setDecididos((d) => ({ ...d, [atual.id]: 'outro' }))
          toast.info('Alerta já revisado', e.message)
          setMotivo('')
          setMotivoFaltando(false)
          avancar()
          return
        }
        toast.error(e instanceof Error ? e.message : 'Erro ao registrar o veredito')
      } finally {
        enviandoRef.current = false
        setEnviando(false)
      }
    },
    [atual, avancar, podeEscrever, toast, motivo],
  )

  // ── correção de caixa: handlers ──────────────────────────────────────────

  const bboxDe = useCallback(
    (i: number | null): Bbox | null => (i === null ? null : atual?.violations?.[i]?.bbox ?? null),
    [atual],
  )

  const iniciarCorrecao = useCallback(
    (i: number) => {
      setSelecionada(i)
      setRascunho(bboxDe(i))
      setErroCaixa(null)
    },
    [bboxDe],
  )

  const cancelarCorrecao = useCallback(() => {
    setSelecionada(null)
    setRascunho(null)
    setErroCaixa(null)
  }, [])

  // ⚠️ Deps completas de propósito: o listener acompanha o item corrente. Um
  // handler preso ao primeiro render é o "ref atrasado" do PR 496 — a tecla C
  // carimbaria veredito num alerta que já saiu da tela. Em modo de correção
  // de caixa (`selecionada !== null`) os atalhos de fila ficam mudos — só
  // Escape cancela (ver docblock do topo: editar é modo explícito).
  useEffect(() => {
    if (!atual) return
    const aoTeclar = (e: KeyboardEvent) => {
      const alvo = e.target as HTMLElement | null
      if (alvo && /^(INPUT|TEXTAREA|SELECT)$/.test(alvo.tagName)) return
      if (e.metaKey || e.ctrlKey || e.altKey) return
      if (selecionada !== null) {
        if (e.key === 'Escape') {
          cancelarCorrecao()
          e.preventDefault()
        }
        return
      }
      const k = e.key.toLowerCase()
      if (e.key === 'ArrowRight') avancar()
      else if (e.key === 'ArrowLeft') voltar()
      else if (k === 'c') void decidir('approve')
      else if (k === 'r') void decidir('reject')
      else return
      e.preventDefault()
    }
    window.addEventListener('keydown', aoTeclar)
    return () => window.removeEventListener('keydown', aoTeclar)
  }, [atual, avancar, voltar, decidir, selecionada, cancelarCorrecao])

  // ── lupa: medir, despachar, âncora ───────────────────────────────────────

  const medir = useCallback((): { rect: DOMRect; palco: Palco } | null => {
    const el = palcoRef.current
    if (!el) return null
    const rect = el.getBoundingClientRect()
    return { rect, palco: { largura: rect.width, altura: rect.height } }
  }, [])

  const despachar = useCallback((ev: EventoLupa, palco: Palco) => {
    setLupa((prev) => proximoEstado(prev, ev, palco))
  }, [])

  /** Âncora relativa ao CENTRO do palco — `transformOrigin: center` assume isso. */
  const ancorar = (rect: DOMRect, clientX: number, clientY: number) => ({
    ancoraX: clientX - (rect.left + rect.width / 2),
    ancoraY: clientY - (rect.top + rect.height / 2),
  })

  useEffect(() => {
    const el = palcoRef.current
    if (!el) return
    const onWheel = (ev: WheelEvent) => {
      // Já no piso e afastando: NÃO sequestra a roda — a página rola normal.
      if (lupaRef.current.escala === ESCALA_MIN && ev.deltaY > 0) return
      ev.preventDefault() // exige passive:false; o onWheel do React é passivo.
      const m = medir()
      if (!m) return
      despachar(
        { tipo: 'zoom', fator: ev.deltaY < 0 ? 1.15 : 1 / 1.15, ...ancorar(m.rect, ev.clientX, ev.clientY) },
        m.palco,
      )
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [despachar, medir, urlEvidencia])

  /**
   * Ponto do cursor → coordenadas NORMALIZADAS (0–1) do frame, para a
   * matemática de `boxGeometry.ts`. O rect vem da <img>, não do palco: a
   * imagem está dentro da camada com `transform` da lupa — só o rect medido
   * reflete escala e pan.
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
    ponteiros.current.set(e.pointerId, { x: e.clientX, y: e.clientY })
    if (ponteiros.current.size === 2) distPinca.current = distanciaEntre([...ponteiros.current.values()])
    // Opcional: jsdom (testes) não implementa `setPointerCapture` — sem a
    // chamada, o navegador real perde só o "seguir o ponteiro fora do
    // elemento" em arrastos muito rápidos, não a funcionalidade de pan.
    e.currentTarget.setPointerCapture?.(e.pointerId)
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
        setRascunho((atualR) => {
          if (!atualR) return atualR
          const caixa = moveBox(bboxParaBox(atualR, natural.w, natural.h), pos.x - interacao.offX, pos.y - interacao.offY)
          return boxParaBbox(caixa, natural.w, natural.h)
        })
      } else {
        setRascunho(boxParaBbox(resizeBox(interacao.inicio, interacao.alca, pos.x, pos.y), natural.w, natural.h))
      }
      return
    }
    const anterior = ponteiros.current.get(e.pointerId)
    if (!anterior) return
    ponteiros.current.set(e.pointerId, { x: e.clientX, y: e.clientY })
    const m = medir()
    if (!m) return
    const pontos = [...ponteiros.current.values()]
    if (pontos.length >= 2) {
      // Pinça: fator = variação da distância, âncora no ponto médio.
      const nova = distanciaEntre(pontos)
      if (distPinca.current > 0 && nova > 0) {
        const meio = { x: (pontos[0].x + pontos[1].x) / 2, y: (pontos[0].y + pontos[1].y) / 2 }
        despachar({ tipo: 'zoom', fator: nova / distPinca.current, ...ancorar(m.rect, meio.x, meio.y) }, m.palco)
      }
      distPinca.current = nova
      return
    }
    // Em escala 1 o limite de pan é 0: arrastar já é inócuo, sem guarda extra.
    despachar({ tipo: 'arrastar', dx: e.clientX - anterior.x, dy: e.clientY - anterior.y }, m.palco)
  }

  const aoSoltarPonteiro = (e: React.PointerEvent<HTMLDivElement>) => {
    if (selecionada !== null) {
      interacaoCaixa.current = null
      return
    }
    ponteiros.current.delete(e.pointerId)
    distPinca.current = 0
  }

  const aoDuploClique = (e: React.MouseEvent<HTMLDivElement>) => {
    if (selecionada !== null) return
    const m = medir()
    if (!m) return
    if (lupaRef.current.escala >= ESCALA_MAX) {
      despachar({ tipo: 'reset' }, m.palco)
      return
    }
    despachar({ tipo: 'zoom', fator: 2, ...ancorar(m.rect, e.clientX, e.clientY) }, m.palco)
  }

  const zoomBotao = (fator: number) => {
    const m = medir()
    if (m) despachar({ tipo: 'zoom', fator, ancoraX: 0, ancoraY: 0 }, m.palco)
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
   * `PATCH /alerts/:id/violations` — mesmo contrato de `EventoDetalhe.tsx`. O
   * servidor carimba a unidade (BBOX_PIXELS) e guarda o array anterior
   * INTEIRO em `violations_historico` — nada se perde, só se acrescenta.
   */
  const salvarCaixa = async () => {
    if (selecionada === null || !rascunho || !atual) return
    setSalvandoCaixa(true)
    setErroCaixa(null)
    const alertaId = atual.id
    try {
      const res = await api.patch<{ data?: { violations: Violacao[]; correcao_ultima: Correcao | null } }>(
        `/alerts/${alertaId}/violations`,
        { correcoes: [{ index: selecionada, bbox: rascunho }] },
      )
      const d = res.data
      if (d) {
        // Por ID, nunca por posição crua: a fila pode reordenar (poll de 15s,
        // outro revisor decidindo em paralelo) enquanto este PATCH está em
        // voo — carimbar por índice congelado antes do await escreve a
        // correção e a autoria no alerta ERRADO (achado do revisor cético,
        // contrato B1 — mesma família de bug do PR 496, agora em `salvarCaixa`).
        setFila((f) =>
          f.map((it) =>
            it.id === alertaId ? { ...it, violations: d.violations, correcao_ultima: d.correcao_ultima } : it,
          ),
        )
      }
      cancelarCorrecao()
    } catch {
      setErroCaixa('Não foi possível salvar a caixa.')
    } finally {
      setSalvandoCaixa(false)
    }
  }

  // ux2/dedup — INFORMATIVO, não filtra nem reordena (regras 1/2/3 do
  // cabeçalho do arquivo continuam intactas: a fila renderiza `fila` na
  // ordem exata que o servidor mandou, `restantes`/`total` continuam
  // "trabalho real" — decisão de NÃO propagar veredito entre irmãos de
  // rajada está pendente, ver docblock do módulo `verification_service.py`).
  // Só avisa: "este item é 1 de N detecções da mesma câmera+classe em <60s",
  // pra o operador não achar que são N situações distintas.
  const gruposRajada = useMemo(
    () =>
      agruparPorRajada(fila, {
        cameraId: (i) => i.camera_id ?? '',
        classe: (i) => classeDe(i),
        criadoEm: (i) => i.created_at ?? i.timestamp ?? '',
      }),
    [fila],
  )
  const rajadaDoAtual = useMemo(
    () => gruposRajada.find((g) => g.repeticoes.some((i) => i.id === atual?.id)),
    [gruposRajada, atual?.id],
  )

  if (!podeLer) {
    return (
      <div className={s.centro}>
        <Lock size={36} strokeWidth={1.5} aria-hidden />
        <span className={s.centroTitulo}>Sem permissão</span>
        <span className={s.centroTexto}>
          A fila de verificação exige a permissão de ver as detecções para revisar. Peça a
          quem administra o seu acesso.
        </span>
      </div>
    )
  }

  if (carregando) {
    return <LogikosLoader estado="waiting" variante="fullscreen" rotulo="CARREGANDO FILA" />
  }

  // Erro só toma a tela quando não há nada para trabalhar. Falha de um poll
  // com fila na mão não pode apagar o que o operador está julgando.
  if (erro && fila.length === 0) {
    return (
      <div className={s.centro}>
        <AlertTriangle size={36} strokeWidth={1.5} className={s.iconeNc} aria-hidden />
        <span className={s.centroTitulo}>Não foi possível carregar a fila</span>
        <span className={s.centroTecnico}>{erro}</span>
        <button
          type="button"
          className={s.acaoPrimaria}
          onClick={() => {
            setCarregando(true)
            void carregar()
          }}
        >
          Tentar novamente
        </button>
      </div>
    )
  }

  if (restantes === 0 || !atual) {
    return (
      <div className={s.centro}>
        <ShieldCheck size={36} strokeWidth={1.5} className={s.iconeOk} aria-hidden />
        <span className={s.centroTitulo}>Fila zerada</span>
        <span className={s.centroTexto}>
          Nenhuma detecção aguarda verificação. As decisões de hoje já alimentam o próximo
          treino.
        </span>
        <Link className={s.acaoPrimaria} to={rotaNova('/epi/dashboard')}>
          Voltar ao dashboard
        </Link>
      </div>
    )
  }

  const classe = classeDe(atual)
  const rotuloClasse = labelForClass(classe)
  const vereditoAtual = decididos[atual.id]
  const violacoesTodas = atual.violations ?? []
  // Projetável = sabemos contra QUE quadro a caixa foi medida. Comparar com
  // uma unidade só deixava a tela cega para tudo que vem do edge.
  const projetavel = (v: { bbox?: Bbox; bbox_unidade?: string; frame_wh?: number[] }) =>
    referenciaDaCaixa(v, { w: 1, h: 1 }) !== null
  const desenhaveis = violacoesTodas.filter(projetavel)
  const semUnidade = violacoesTodas.filter((v) => v.bbox && !projetavel(v)).length
  const indexPrincipal = violacoesTodas.findIndex(projetavel)
  const principal = desenhaveis[0]?.bbox
  const confianca = atual.confidence ?? atual.violations?.[0]?.confidence
  /** Origem DECLARADA das caixas (issue #670) — regra compartilhada,
   *  não recopiada. `null` quando ninguém declarou nada. */
  const procedencia = procedenciaDeclarada(atual.violations)

  // Fora do modo de correção: caixas de leitura, `pointerEvents: none` (lei
  // da casa) — nunca alvo de clique. Em modo de correção: a caixa "ONDE A IA
  // MARCOU" (tracejada) + a caixa editável (sólida, com alças).
  const emCorrecao = selecionada !== null
  const iaBbox = emCorrecao && indexPrincipal >= 0 ? violacoesTodas[indexPrincipal]?.bbox ?? null : null

  return (
    <div className={s.pagina}>
      <div className={s.cabecalho}>
        <h1 className={s.titulo}>Verificação</h1>
        <span className={s.restantes}>{restantes} RESTANTES</span>
        <div
          className={s.trilho}
          role="progressbar"
          aria-valuenow={progresso}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label="Progresso da fila"
        >
          <span className={s.trilhoCheio} style={{ width: `${progresso}%`, display: 'block' }} />
        </div>
        <span className={s.espacador} />
        <span className={s.atalhos}>
          <span>
            <span className={s.tecla}>←</span> <span className={s.tecla}>→</span> NAVEGAR
          </span>
          <span>
            <span className={s.tecla}>C</span> CONFIRMAR
          </span>
          <span>
            <span className={s.tecla}>R</span> REJEITAR
          </span>
        </span>
      </div>

      <div className={s.palco}>
        <div
          className={s.evidencia}
          ref={palcoRef}
          role="group"
          aria-label="Frame da evidência. Roda do mouse para ampliar, arrastar para deslocar."
          onPointerDown={aoDescerPonteiro}
          onPointerMove={aoMoverPonteiro}
          onPointerUp={aoSoltarPonteiro}
          onPointerCancel={aoSoltarPonteiro}
          onDoubleClick={aoDuploClique}
          style={urlEvidencia ? { cursor: lupa.escala > ESCALA_MIN ? 'grab' : 'zoom-in' } : undefined}
        >
          {urlEvidencia ? (
            <div
              className={s.camadaZoom}
              data-testid="camada-zoom"
              style={{ transform: `translate(${lupa.x}px, ${lupa.y}px) scale(${lupa.escala})` }}
            >
              <span className={s.quadro}>
                <img
                  ref={imgRef}
                  className={s.imagem}
                  src={urlEvidencia}
                  alt={`Evidência de ${rotuloClasse}`}
                  draggable={false}
                  onLoad={(e) => {
                    const img = e.currentTarget
                    if (img.naturalWidth > 0 && img.naturalHeight > 0) {
                      setNatural({ w: img.naturalWidth, h: img.naturalHeight })
                    }
                  }}
                />
                {natural && !emCorrecao &&
                  desenhaveis.map((v, i) => (
                    <span
                      key={i}
                      data-testid="caixa-violacao"
                      className={s.caixa}
                      // Quadro CERTO: a caixa do edge é medida no streammux,
                      // não na imagem exibida. Correção e rascunho, abaixo,
                      // nascem na imagem e seguem com `natural`.
                      style={estiloDaCaixa(v, natural) ?? undefined}
                    >
                      <span className={s.caixaRotulo}>
                        {labelForClass(v.class).toUpperCase()}
                      </span>
                    </span>
                  ))}

                {natural && emCorrecao && (
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
                          ONDE A IA MARCOU
                        </span>
                      </div>
                    )}
                    {rascunho && (
                      <div
                        data-testid="caixa-correcao"
                        className={s.caixaCorrecao}
                        style={{
                          ...caixaEmPorcento(rascunho, natural.w, natural.h),
                          borderWidth: `${2.5 / lupa.escala}px`,
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
                )}
              </span>
            </div>
          ) : (
            <span className={s.semImagem}>
              <ImageOff size={28} strokeWidth={1.5} aria-hidden />
              Sem imagem de evidência para esta detecção
            </span>
          )}

          <span className={s.selo}>{(atual.camera_name ?? atual.camera_id ?? '—').toUpperCase()}</span>

          {emCorrecao && (
            <p className={s.dicaCorrecao}>ARRASTE PARA DESENHAR · ALÇAS REDIMENSIONAM · ESC CANCELA</p>
          )}

          <div className={s.zoomBarra}>
            <button
              type="button"
              className={s.zoomBotao}
              aria-label="Diminuir zoom"
              disabled={!urlEvidencia || lupa.escala <= ESCALA_MIN}
              onClick={() => zoomBotao(1 / 1.5)}
            >
              <Minus size={15} strokeWidth={1.7} aria-hidden />
            </button>
            <span className={s.zoomValor}>{lupa.escala.toFixed(1)}×</span>
            <button
              type="button"
              className={s.zoomBotao}
              aria-label="Aumentar zoom"
              disabled={!urlEvidencia || lupa.escala >= ESCALA_MAX}
              onClick={() => zoomBotao(1.5)}
            >
              <Plus size={15} strokeWidth={1.7} aria-hidden />
            </button>
          </div>
        </div>

        <div className={s.painel}>
          <span className={s.overline}>Detecção proposta</span>

          <div className={s.classeLinha}>
            <AlertTriangle size={20} strokeWidth={1.7} aria-hidden />
            <span className={s.classeNome}>{rotuloClasse}</span>
          </div>

          {/* ux2/dedup — informativo, não decide nada por conta própria (ver
              docblock do módulo verification_service.py): a fila continua
              julgando item por item, só avisa que este item é UM de N
              detecções da mesma câmera+classe redetectadas em <60s. */}
          {rajadaDoAtual && rajadaDoAtual.tamanho > 1 && (
            <details className={s.rajadaAviso}>
              <summary>
                Rajada de {rajadaDoAtual.tamanho} · mesma câmera+classe em &lt;60s
              </summary>
              <ul className={s.rajadaListaHorarios}>
                {rajadaDoAtual.repeticoes.map((i) => (
                  <li key={i.id}>
                    {horaDe(i)}
                    {i.id === atual.id ? ' (este)' : ''}
                  </li>
                ))}
              </ul>
            </details>
          )}

          <div className={s.ficha}>
            <span className={s.fichaRotulo}>Câmera</span>
            <span className={s.fichaDado}>{atual.camera_name ?? atual.camera_id ?? '—'}</span>
            <span className={s.fichaRotulo}>Horário</span>
            <span className={s.fichaDado}>{horaDe(atual)}</span>
            {/* PROCEDÊNCIA (issue #670). Esta é a tela onde o operador JULGA, e
                era a única das cinco superfícies que não dizia quem desenhou a
                caixa: 4.609 dos 5.174 eventos do DEV são acervo semeado com
                caixa de PESSOA (`origem = 'anotacao_humana'`, lote de
                demonstração) e chegavam aqui indistinguíveis de uma detecção do
                modelo em produção — pedindo veredito sobre encenação sem avisar.
                A regra é a MESMA de `ProcedenciaEvento` (dono único), não uma
                cópia. Sem declaração, sem linha: ausência de badge continua
                sendo ausência de afirmação. */}
            {procedencia && (
              <>
                <span className={s.fichaRotulo}>Procedência</span>
                <span
                  className={s.fichaDado}
                  title={procedencia.titulo}
                  data-testid="procedencia"
                >
                  {procedencia.rotulo}
                </span>
              </>
            )}
            <span className={s.fichaRotulo}>Confiança</span>
            <span className={s.fichaDado}>
              {confiancaInternaOuCliente(confianca, isSuperAdmin)}
            </span>
            {atual.verification_reason && (
              <>
                <span className={s.fichaRotulo}>Motivo da IA</span>
                <span className={s.fichaDado}>{atual.verification_reason}</span>
              </>
            )}
          </div>

          {podeCorrigir && principal && urlEvidencia && !emCorrecao && (
            <button
              type="button"
              className={s.botaoCorrigir}
              onClick={() => iniciarCorrecao(indexPrincipal)}
            >
              <SquarePen size={13} strokeWidth={1.8} aria-hidden /> Corrigir caixa
            </button>
          )}

          {atual.correcao_ultima && (
            <div className={s.badgeAutoria}>
              <SquarePen size={14} strokeWidth={1.7} aria-hidden />
              <p className={s.badgeAutoriaTexto} data-testid="badge-autoria">
                Caixa corrigida por <strong>{atual.correcao_ultima.por_nome ?? '—'}</strong>
                {atual.correcao_ultima.em && <><br />{dataHora(atual.correcao_ultima.em)}</>}
              </p>
            </div>
          )}

          {/* Recorte da detecção — ⛔ NÃO MEXER na aparência (elogiado pelo cliente). */}
          {principal && natural && urlEvidencia && (
            <div
              className={s.recorte}
              style={estiloRecorte(principal, natural.w, natural.h, urlEvidencia)}
              role="img"
              aria-label={`Recorte da detecção de ${rotuloClasse}`}
            >
              <span className={s.recorteRotulo}>RECORTE DA DETECÇÃO</span>
            </div>
          )}

          {semUnidade > 0 && (
            <span className={s.nota}>
              {semUnidade === 1 ? '1 violação' : `${semUnidade} violações`} sem unidade de caixa
              conhecida — não projetadas sobre o frame.
            </span>
          )}

          <div className={s.navegacao}>
            <button
              type="button"
              className={s.navBotao}
              onClick={voltar}
              disabled={indice === 0 || emCorrecao}
              aria-label="Item anterior"
            >
              <ArrowLeft size={14} strokeWidth={1.7} aria-hidden /> Anterior
            </button>
            <span className={s.espacador} />
            <button
              type="button"
              className={s.navBotao}
              onClick={avancar}
              disabled={indice >= fila.length - 1 || emCorrecao}
              aria-label="Próximo item"
            >
              Próximo <ArrowRight size={14} strokeWidth={1.7} aria-hidden />
            </button>
          </div>

          {emCorrecao ? (
            <div className={s.veredito}>
              <span className={s.overline}>Coordenadas</span>
              <p className={s.nota}>
                Pixels do frame original, a partir do canto superior esquerdo — o mesmo caminho
                para quem não usa o mouse com precisão.
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

              <div className={s.navegacao}>
                <button
                  type="button"
                  className={s.botaoSalvarCaixa}
                  onClick={() => void salvarCaixa()}
                  disabled={salvandoCaixa || !rascunho || rascunho[2] <= 0 || rascunho[3] <= 0}
                >
                  <Check size={16} strokeWidth={2.2} aria-hidden /> Salvar caixa
                </button>
                <button
                  type="button"
                  className={s.botaoCancelarCaixa}
                  onClick={cancelarCorrecao}
                  disabled={salvandoCaixa}
                >
                  Cancelar
                </button>
              </div>

              {erroCaixa && <span className={s.nota}>{erroCaixa}</span>}
            </div>
          ) : (
            <div className={s.veredito}>
              <div className={s.motivoLinha}>
                <label htmlFor="motivo-verificacao" className={s.motivoRotulo}>
                  Motivo (obrigatório para rejeitar)
                </label>
                <select
                  id="motivo-verificacao"
                  className={
                    motivoFaltando ? `${s.motivoSelect} ${s.motivoSelectErro}` : s.motivoSelect
                  }
                  value={motivo}
                  disabled={!podeEscrever || enviando}
                  onChange={(e) => {
                    setMotivo(e.target.value as MotivoVerificacao | '')
                    setMotivoFaltando(false)
                  }}
                >
                  <option value="">Selecione um motivo…</option>
                  {MOTIVOS_VERIFICACAO.map((m) => (
                    <option key={m.valor} value={m.valor}>
                      {m.rotulo}
                    </option>
                  ))}
                </select>
                {motivoFaltando && (
                  <span className={s.motivoErro}>Selecione um motivo para rejeitar.</span>
                )}
              </div>

              {vereditoAtual ? (
                <span
                  className={`${s.decidido} ${
                    vereditoAtual === 'outro'
                      ? ''
                      : vereditoAtual === 'approve'
                        ? s.decididoOk
                        : s.decididoNc
                  }`}
                >
                  {vereditoAtual === 'approve' ? (
                    <Check size={14} strokeWidth={2.4} aria-hidden />
                  ) : (
                    <X size={14} strokeWidth={2.4} aria-hidden />
                  )}
                  {vereditoAtual === 'outro'
                    ? 'Revisado por outro'
                    : vereditoAtual === 'approve'
                      ? 'Confirmado'
                      : 'Rejeitado'}
                </span>
              ) : null}

              <button
                type="button"
                className={s.confirmar}
                onClick={() => void decidir('approve')}
                disabled={!podeEscrever || enviando}
                title={podeEscrever ? undefined : 'Exige a permissão de julgar detecções'}
              >
                <Check size={17} strokeWidth={2.4} aria-hidden />
                Confirmar <span className={s.teclaBotao}>C</span>
              </button>

              <button
                type="button"
                className={s.rejeitar}
                onClick={() => void decidir('reject')}
                disabled={!podeEscrever || enviando}
                title={podeEscrever ? undefined : 'Exige a permissão de julgar detecções'}
              >
                <X size={16} strokeWidth={2.4} aria-hidden />
                Rejeitar <span className={s.teclaBotao}>R</span>
              </button>

              <button
                type="button"
                className={s.anotar}
                disabled
                title="Sem endpoint que leve um alerta para a fila de anotação do Estúdio — registrado para o backend"
              >
                <PenLine size={14} strokeWidth={1.8} aria-hidden />
                Enviar para anotação · Estúdio
              </button>

              <span className={s.nota}>
                Cada decisão vira dado de treino. O envio para a fila de anotação do Estúdio
                ainda não tem endpoint — registrado, e a verificação não trava por isso.
              </span>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
