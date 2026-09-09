/**
 * EPI Dashboard — `/epi/dashboard` (F3 da migração).
 *
 * Desenho: `EPI Dashboard.dc.html`. Layout, medidas e copy são do handoff.
 *
 * ⛔ ZERO DADO INVENTADO — e neste dashboard isso custa caro, porque o desenho
 * pede coisas que o backend HOJE não tem. Cada uma aparece como vazio
 * honesto, nunca como número plausível:
 *
 *  1. **Delta e curva de 7 dias do score.** `/api/modules/epi/stats` devolve o
 *     score do momento e nada de histórico. "+5 vs ontem (82)" e a sparkline
 *     exigiriam uma série de score por dia que não existe em endpoint nenhum.
 *     → o rodapé do cartão diz SEM HISTÓRICO em vez de desenhar uma linha.
 *
 *  2. **Plano de ação com responsável e prazo.** Continua sem existir: não há
 *     tabela, endpoint nem permissão `actions:*` para "ação corretiva
 *     atribuída a fulano com vencimento". O que EXISTE — e é o que a tela
 *     mostra agora — é a TRATATIVA do evento: `alerts.acknowledged` ("alguém
 *     viu") e `alerts.verification_status` ("a detecção procede"), as duas
 *     colunas que `/epi/acoes` já lê e escreve. O cartão morto que dizia "—"
 *     e "Indisponível" saiu: era espaço de tela ocupado por um travessão.
 *
 *  2b. **Eixo do tempo = CAPTURA, não ingestão.** Todo painel temporal desta
 *     tela lê `alerts.timestamp` (quando o frame foi capturado), nunca
 *     `created_at` (quando a linha entrou no banco). Os dois só coincidem com
 *     ingestão ao vivo. Medido no DEV: a captura se espalha das 10h às 19h —
 *     o dia da fábrica — enquanto o `created_at` empilha tudo em 3 horários,
 *     que são os momentos em que a carga rodou. "Em que horário a fábrica
 *     gera violação" respondido sobre `created_at` é "a que horas o servidor
 *     gravou", e isso não é informação de segurança nenhuma.
 *
 *  3. **Seletor de site.** Nenhum dos endpoints desta tela aceita escopo de
 *     site (`/modules/epi/stats` é do tenant; `/v1/events/*` filtra por câmera).
 *     → o seletor não entra. Um filtro que não filtra é pior que filtro nenhum:
 *     o operador acha que está olhando a doca e está olhando a fábrica toda.
 *
 *  4. **Conectividade por câmera ("online").** `public.cameras.last_seen`
 *     existe na tabela mas nunca recebe UPDATE em lugar nenhum do código —
 *     o sistema não sabe se uma câmera está transmitindo, só sabe se ela
 *     está cadastrada como ativa (não arquivada). O cartão dizia "Câmeras
 *     online" e derivava "N fora do ar" de `total − ativas`, mas `total`
 *     incluía câmeras ARQUIVADAS (29 = 19 ativas + 10 arquivadas na RVB) —
 *     dois erros empilhados fabricando uma métrica de disponibilidade que
 *     não existe. → cartão agora diz "Câmeras ativas", um número só, com a
 *     legenda admitindo a falta de telemetria. PEDIDO-AO-BACKEND: heartbeat
 *     real por câmera (writer de `last_seen`) para um "online" que signifique
 *     algo.
 *
 * O seletor de TURNO entra porque é real: recorta `from`/`to` dos dois painéis
 * de evento. Os KPIs do topo NÃO são recortáveis por turno (o backend fixa
 * "hoje" e "últimas 24 h"), e o cartão diz isso na legenda em vez de deixar o
 * leitor supor que o filtro alcançou tudo.
 */
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import {
  DndContext,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
  type DragEndEvent,
} from '@dnd-kit/core'
import { SortableContext, rectSortingStrategy, useSortable } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { useQuery } from '@tanstack/react-query'
import {
  ArrowDown,
  ArrowUp,
  BarChart3,
  CheckCircle2,
  GripVertical,
  LayoutGrid,
  ShieldAlert,
  SlidersHorizontal,
  AlertTriangle,
} from 'lucide-react'
import { Link } from 'react-router-dom'

import { useAuth } from '../../hooks/useAuth'
import { api } from '../../services/api'
import { scoreImpresso } from './scoreConformidade'
import { moduleService, type ModuleStats } from '../../services/moduleService'
import { eventsService } from '../../services/eventsService'
import { violationLabel } from '../../components/dashboard/widgets/violationLabels'
import { fillBuckets, formatBucketLabel } from '../../utils/timeBuckets'
import { agregarPerfil, picoDeViolacao, rotuloDia } from '../../utils/perfilEventos'
import { LogikosLoader } from '../shell/LogikosLoader'
import { lk } from '../tokens/lk.css'
import * as s from './Dashboard.css'
import { rotaNova } from '../RotasNovas'

/**
 * `ModuleStats` (serviço compartilhado com o front antigo) só declara as 4
 * chaves originais; `module_service.get_stats` devolve também os KPIs de BI.
 * Declarado aqui, opcional, para não mexer num arquivo que outras telas usam.
 */
type EstatisticasEpi = ModuleStats & {
  compliance_rate?: number | null
  /** Por que o score veio `null` — ver `_com_score_honesto` em
   *  `app/api/v1/modules/routes.py`. A tela NUNCA deduz a razão do valor. */
  compliance_reason?: string | null
}

/**
 * Frase do vazio do score. Chave desconhecida (backend mais novo que a tela)
 * cai na frase genérica — nunca some, nunca vira 100.
 */
const RAZAO_SEM_SCORE: Record<string, string> = {
  sem_cameras_ativas: 'sem câmera ativa para calcular',
  sem_sinal_no_periodo: 'sem dado no período — nada chegou nas últimas 24 h',
  nao_foi_possivel_apurar: 'não foi possível apurar agora',
}

// ── Turno ───────────────────────────────────────────────────────────────────

type IdTurno = 'primeiro' | 'segundo' | 'dia'

/**
 * ⚠️ Janelas fixas: o backend não expõe os turnos do cliente em lugar nenhum.
 * As horas vão no rótulo de propósito — ninguém precisa adivinhar o que
 * "1º turno" recorta, e no dia em que a janela real do RVB for outra o texto
 * está errado na cara do operador, não escondido no código.
 */
const TURNOS: Array<{ id: IdTurno; rotulo: string; inicio: number; fim: number }> = [
  { id: 'primeiro', rotulo: '1º turno · 06h–14h', inicio: 6, fim: 14 },
  { id: 'segundo', rotulo: '2º turno · 14h–22h', inicio: 14, fim: 22 },
  { id: 'dia', rotulo: 'Dia inteiro · 00h–24h', inicio: 0, fim: 24 },
]

function janelaDoTurno(turno: IdTurno, agora = new Date()) {
  const t = TURNOS.find((x) => x.id === turno) ?? TURNOS[2]
  const inicio = new Date(agora)
  inicio.setHours(t.inicio, 0, 0, 0)
  const fim = new Date(agora)
  // setHours(24) rola para a meia-noite seguinte — é o fim exclusivo do dia.
  fim.setHours(t.fim, 0, 0, 0)
  return { de: inicio.toISOString(), ate: fim.toISOString(), faixa: t }
}

// ── Preferência de widgets (ordem + visibilidade) ───────────────────────────

type IdWidget =
  | 'violacoes-horario'
  | 'volume-dia'
  | 'composicao'
  | 'eventos-hora'
  | 'violacoes-classe'
  | 'cameras-eventos'

const WIDGETS: Array<{ id: IdWidget; rotulo: string }> = [
  { id: 'violacoes-horario', rotulo: 'Violações por horário do dia' },
  { id: 'volume-dia', rotulo: 'Volume por dia' },
  { id: 'composicao', rotulo: 'Composição dos eventos' },
  { id: 'eventos-hora', rotulo: 'Eventos por hora' },
  { id: 'violacoes-classe', rotulo: 'Violações por classe' },
  { id: 'cameras-eventos', rotulo: 'Câmeras com mais eventos' },
]

/**
 * Janela fixa de 30 dias para o ranking — não segue o seletor de TURNO (o
 * ranking lê o acumulado do mês, não o recorte do turno escolhido).
 * `_MAX_SUMMARY_DAYS` em `services/api/app/api/v1/events/routes.py` é 92:
 * 30 cabe com folga, sem precisar reduzir a janela nem o rótulo.
 */
const JANELA_RANKING_DIAS = 30
const ROTULO_JANELA_RANKING = `ÚLTIMOS ${JANELA_RANKING_DIAS} DIAS`

/**
 * Janela do perfil temporal. 90 dias porque o acervo do cliente tem MESES, não
 * dias: uma curva de horário montada sobre dois dias é ruído, e a mesma curva
 * sobre um trimestre é padrão de turno. 90 e não 92 (`_MAX_SUMMARY_DAYS`) para
 * a borda do arredondamento nunca cair do lado do 400.
 *
 * O rótulo do painel NÃO promete 90 dias de operação — quem diz o alcance real
 * é a legenda, que sai de `primeira_captura`/`ultima_captura`. Prometer no
 * título uma janela que o dado não preenche é a mesma mentira de desenhar
 * série longa em cima de série curta.
 */
const JANELA_PERFIL_DIAS = 90

/**
 * Janela do cartão "Aguardando tratativa" — 30 dias porque é a janela de
 * `Acoes.tsx` (`const DIAS = 30`), a tela para onde o cartão manda ("tratar
 * eventos →"). Cartão e fila de trabalho contando períodos diferentes é a
 * primeira metade da issue #802; a segunda é o `kind` (ver `tratativa`).
 */
const JANELA_TRATATIVA_DIAS = 30

const PADRAO: IdWidget[] = WIDGETS.map((w) => w.id)
const CHAVE_PREF = 'lk-epi-dashboard-widgets'

interface Preferencia {
  ordem: IdWidget[]
  ocultos: IdWidget[]
}

/**
 * Tolerante de propósito: storage corrompido, id que já não existe e widget
 * novo que ainda não estava salvo não podem quebrar a tela nem sumir. O que
 * está salvo manda na ordem; o que é novo entra no fim.
 */
function lerPreferencia(): Preferencia {
  const conhecido = (id: unknown): id is IdWidget => PADRAO.includes(id as IdWidget)
  try {
    const cru = JSON.parse(localStorage.getItem(CHAVE_PREF) ?? 'null') as Partial<Preferencia> | null
    const salvos = (cru?.ordem ?? []).filter(conhecido)
    return {
      ordem: [...salvos, ...PADRAO.filter((id) => !salvos.includes(id))],
      ocultos: (cru?.ocultos ?? []).filter(conhecido),
    }
  } catch {
    return { ordem: PADRAO, ocultos: [] }
  }
}

function gravarPreferencia(pref: Preferencia) {
  try {
    localStorage.setItem(CHAVE_PREF, JSON.stringify(pref))
  } catch {
    // Modo privado / cota cheia: a preferência é conveniência, não dado.
  }
}

// ── Peças visuais ───────────────────────────────────────────────────────────

type Severidade = 'ok' | 'atencao' | 'nc' | 'neutro'

const COR: Record<Severidade, string> = {
  ok: lk.estado.ok,
  atencao: lk.estado.atencao,
  nc: lk.estado.nc,
  neutro: lk.cor.cinzaNevoa,
}

/** Estado = cor + ícone + palavra. Os três juntos, sempre — nunca só a cor. */
function Estado({ nivel, palavra }: { nivel: Severidade; palavra: string }) {
  const Icone = nivel === 'ok' ? CheckCircle2 : AlertTriangle
  return (
    <span className={s.estadoLinha} style={{ color: COR[nivel] }}>
      <Icone size={12} strokeWidth={2} aria-hidden="true" />
      {palavra}
    </span>
  )
}

function numero(n: number) {
  return n.toLocaleString('pt-BR')
}

// `scoreImpresso` mora em `./scoreConformidade` porque `Relatorios.tsx` mostra
// O MESMO número (issue #789): quando ele vivia aqui, o conserto pegou só esta
// tela e a mesma semana saía "99" no cartão e "100" no relatório que vira PDF.
// Reexportado para quem já o importava deste módulo.
export { scoreImpresso } from './scoreConformidade'

function horaCurta(ms: number) {
  return new Date(ms).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })
}

/**
 * Tipo de evento que um painel CONTOU. É o `kind` do backend, e a única razão
 * de existir é obrigar cada link a declará-lo: sem isso o número do cartão e o
 * número da tela de destino falam de conjuntos diferentes.
 *
 * `'todos'` vira `kind=` (vazio) na URL — que é exatamente a opção "Todos os
 * tipos" do seletor de `Eventos.tsx`. Não é omissão: omitir o parâmetro faz o
 * destino cair no default 'violation'.
 */
type TipoContado = 'todos' | 'violation'

/**
 * Monta o link para `/epi/eventos` na querystring que aquela tela já lê
 * (`start_date`/`end_date`/`camera_id`/`violation_type`/`acknowledged`/`kind`
 * — ver `Eventos.tsx`). Não inventa parâmetro novo: só usa o que o destino já
 * suporta.
 *
 * ⚠️ `kind` é OBRIGATÓRIO, e é o conserto de um defeito medido no DEV
 * (2026-09-05, tenant RVB, janela de 30 dias):
 *
 *   painel "Câmeras com mais eventos"  (GET /v1/events/summary) → 4.629 eventos
 *   destino do clique, como estava     (GET /api/alerts, sem kind) →   495
 *
 * O link não mandava `kind`; `Eventos.tsx` assume `'violation'` quando o
 * parâmetro está AUSENTE, então a lista mostrava só as violações de um cartão
 * que tinha contado tudo — 10× menos, sem uma palavra dizendo por quê. Um
 * cartão que se desmente ao ser clicado é pior que cartão nenhum: quem clica
 * conclui que um dos dois números está quebrado, e não sabe qual.
 *
 * Regra: o link carrega o MESMO recorte que produziu o número. Painel que
 * contou todo evento manda `'todos'`; painel de violação manda `'violation'`.
 *
 * O recorte tem TRÊS eixos, e o link declara os três:
 *
 *  · **tipo** (`kind`) — acima;
 *  · **tempo** — `start_date`/`end_date` são hora de CAPTURA (todo painel
 *    desta tela lê `alerts.timestamp`, item 2b do cabeçalho). `GET
 *    /api/alerts` passou a ler a janela nesse mesmo eixo por DEFAULT
 *    (issue #676), então não há parâmetro a mandar: mandar `time_field`
 *    aqui seria repetir o default;
 *  · **módulo** (`module_code=epi`) — os painéis saem de
 *    `/v1/events/*?module_code=epi`, que aplica a coluna `module_code` E o
 *    escopo de câmera do módulo. Sem ele a lista contava alerta de câmera
 *    fora do EPI: 82 linhas a mais que o cartão, medidas no DEV.
 *    ⚠️ `Eventos.tsx` ainda NÃO relê este parâmetro da URL (arquivo de outra
 *    frente nesta rodada — issue #701). Até lá o link já sai correto e o
 *    destino ignora; quando aquela linha entrar, o número fecha sozinho.
 */
function linkParaEventos(params: {
  kind: TipoContado
  start_date?: string
  end_date?: string
  camera_id?: string
  violation_type?: string
  acknowledged?: string
}) {
  const q = new URLSearchParams()
  for (const [chave, valor] of Object.entries(params)) {
    if (chave === 'kind') continue
    if (valor) q.set(chave, valor)
  }
  // Escopo de módulo: TODO painel desta tela conta com `module_code=epi`.
  q.set('module_code', 'epi')
  // Sempre presente, inclusive vazio: `?kind=` é "todos os tipos" para o
  // destino, e a AUSÊNCIA é que reativaria o default 'violation'.
  q.set('kind', params.kind === 'todos' ? '' : params.kind)
  return rotaNova(`/epi/eventos?${q.toString()}`)
}

interface PainelProps {
  id: IdWidget
  titulo: string
  nota?: string
  acao?: ReactNode
  children: ReactNode
}

/** Painel arrastável — a alça é a única área que inicia o arrasto. */
function Painel({ id, titulo, nota, acao, children }: PainelProps) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id })
  return (
    <section
      ref={setNodeRef}
      className={isDragging ? `${s.painel} ${s.arrastando}` : s.painel}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      aria-label={titulo}
    >
      <div className={s.painelCabecalho}>
        <button
          type="button"
          className={s.alca}
          aria-label={`Mover ${titulo}`}
          {...attributes}
          {...listeners}
        >
          <GripVertical size={14} strokeWidth={2} aria-hidden="true" />
        </button>
        <span className={s.painelTitulo}>{titulo}</span>
        {nota && <span className={s.painelNota}>{nota}</span>}
        {acao}
      </div>
      {children}
    </section>
  )
}

/**
 * Vazio/erro de painel: nunca derruba a tela, sempre diz o que houve. `cta`
 * é o caminho de saída de um vazio de JANELA (turno sem evento) — sem ele o
 * operador fica preso olhando um painel em branco sem saber que 30 dias atrás
 * tem dado.
 */
function VazioPainel({
  texto,
  aoRetentar,
  cta,
}: {
  texto: string
  aoRetentar?: () => void
  cta?: { texto: string; to: string }
}) {
  return (
    <div className={s.painelVazio}>
      <span>{texto}</span>
      {aoRetentar && (
        <button type="button" className={s.botaoRetentar} onClick={aoRetentar}>
          Tentar novamente
        </button>
      )}
      {cta && (
        <Link to={cta.to} className={s.botaoRetentar}>
          {cta.texto}
        </Link>
      )}
    </div>
  )
}

// ── Tela ────────────────────────────────────────────────────────────────────

export function Dashboard() {
  const { can } = useAuth()
  const [turno, setTurno] = useState<IdTurno>('dia')
  const [dicaAberta, setDicaAberta] = useState(false)
  const [personalizar, setPersonalizar] = useState(false)
  const [pref, setPref] = useState<Preferencia>(lerPreferencia)
  const refPersonalizar = useRef<HTMLDivElement>(null)

  // Clique fora fecha. Sem isto o popover só some clicando de volta no botão —
  // e quem abriu por engano fica com ele plantado sobre o primeiro cartão.
  useEffect(() => {
    if (!personalizar) return
    const fora = (e: PointerEvent) => {
      if (!refPersonalizar.current?.contains(e.target as Node)) setPersonalizar(false)
    }
    document.addEventListener('pointerdown', fora)
    return () => document.removeEventListener('pointerdown', fora)
  }, [personalizar])

  const janela = useMemo(() => janelaDoTurno(turno), [turno])

  const estatisticas = useQuery({
    queryKey: ['epi', 'stats'],
    queryFn: () => moduleService.getStats('epi') as Promise<EstatisticasEpi>,
    staleTime: 30_000,
    refetchInterval: 60_000,
  })

  const linhaDoTempo = useQuery({
    queryKey: ['epi', 'timeline', janela.de, janela.ate],
    queryFn: () =>
      eventsService.getTimeline({
        from: janela.de,
        to: janela.ate,
        bucket: 'hour',
        moduleCode: 'epi',
        // Captura, não ingestão (ver item 2b do cabeçalho). Sem isto o painel
        // de hoje desenha o horário em que a carga rodou.
        timeField: 'captured',
      }),
    staleTime: 30_000,
    refetchInterval: 60_000,
  })

  const resumo = useQuery({
    queryKey: ['epi', 'summary', janela.de, janela.ate],
    queryFn: () => eventsService.getSummary({ from: janela.de, to: janela.ate, moduleCode: 'epi' }),
    staleTime: 30_000,
    refetchInterval: 60_000,
  })

  const janela30d = useMemo(() => {
    const agora = new Date()
    const inicio = new Date(agora)
    inicio.setDate(inicio.getDate() - JANELA_RANKING_DIAS)
    return { de: inicio.toISOString(), ate: agora.toISOString() }
  }, [])

  // Mesma definição de "hoje" que `Eventos.tsx` usa (00h local até agora) —
  // é a janela que o backend fixa para `alerts_today`, não a do turno.
  const janelaHoje = useMemo(() => {
    const agora = new Date()
    const inicio = new Date(agora)
    inicio.setHours(0, 0, 0, 0)
    return { de: inicio.toISOString(), ate: agora.toISOString() }
  }, [])

  const rankingCameras = useQuery({
    queryKey: ['epi', 'summary-cameras', janela30d.de, janela30d.ate],
    queryFn: () => eventsService.getSummary({ from: janela30d.de, to: janela30d.ate, moduleCode: 'epi' }),
    staleTime: 30_000,
    refetchInterval: 60_000,
  })

  const janelaPerfil = useMemo(() => {
    const agora = new Date()
    const inicio = new Date(agora)
    inicio.setDate(inicio.getDate() - JANELA_PERFIL_DIAS)
    return { de: inicio.toISOString(), ate: agora.toISOString() }
  }, [])

  // Um pedido só alimenta três painéis (horário do dia, volume por dia,
  // composição) e o cartão de tratativa — todos falam do MESMO conjunto de
  // eventos, e buscá-los em quatro chamadas abriria a porta para quatro
  // números que não fecham na mesma tela.
  const perfil = useQuery({
    queryKey: ['epi', 'profile', janelaPerfil.de, janelaPerfil.ate],
    queryFn: () =>
      eventsService.getProfile({ from: janelaPerfil.de, to: janelaPerfil.ate, moduleCode: 'epi' }),
    staleTime: 60_000,
    refetchInterval: 120_000,
  })

  // ⚠️ Aritmética IDÊNTICA à de `Acoes.tsx` (`desde()`: `Date.now() − dias ×
  // 86_400_000`), e não `setDate(getDate() − 30)` como as outras janelas desta
  // tela. As duas só coincidem quando não há mudança de fuso no meio: a
  // segunda preserva a HORA LOCAL e escorrega uma hora atravessando um
  // horário de verão. Uma hora de diferença entre o cartão e a fila é
  // pequena, e é exatamente do tamanho de "por que este número não fecha".
  const janelaTratativa = useMemo(() => {
    const agora = Date.now()
    return {
      de: new Date(agora - JANELA_TRATATIVA_DIAS * 86_400_000).toISOString(),
      ate: new Date(agora).toISOString(),
    }
  }, [])

  /**
   * O cartão "Aguardando tratativa" — issue #802.
   *
   * ⛔ Vinha de `perfil.situacao.nao_reconhecidos`, que é "todo evento do
   * período ainda sem ciência" — TODO evento, de TODO tipo, em 90 dias.
   * Medido no DEV (RVB, 05/09): o cartão dizia **5.062** e a tela para onde
   * ele manda, `/epi/acoes`, dizia **368**. 14× de diferença, e o motivo
   * estava dois blocos abaixo no próprio Dashboard: a Composição do período
   * é Violação 504 + **Conformidade (EPI em uso) 3.881** + Não definida 707.
   * Ninguém "trata" 3.881 registros de EPI EM USO — e `/epi/acoes` sabe
   * disso (ADR-0065: uma tela de AGIR que listasse conformidade estaria
   * pedindo ação sobre quem está certo).
   *
   * A conta passa a ser a MESMA chamada que `Acoes.tsx` faz para encher a
   * coluna "Aguardando" — `kind=violation` + `acknowledged=false` + os
   * mesmos 30 dias, `per_page=1` porque só o envelope importa. Não é uma
   * segunda leitura do mesmo fato: é a leitura do destino, trazida para cá.
   *
   * ⚠️ Duas diferenças declaradas em relação a `Acoes.tsx`, ambas do lado do
   * cartão ser MAIS estrito, e nenhuma delas move o número num tenant só-EPI
   * como a RVB:
   *
   *  · `module_code=epi` — o cartão vive no dashboard do EPI e todo outro
   *    número desta tela é escopado assim. `Acoes.tsx` ainda não manda: num
   *    tenant com mais de um módulo a FILA é que contaria a mais. Issue #825.
   *  · `end_date=agora` — `Acoes.tsx` deixa a janela aberta no topo. Só
   *    diverge se existir captura datada no futuro; o link precisa dos dois
   *    limites (`Eventos.tsx` só adota o intervalo da URL quando recebe
   *    `start_date` E `end_date`), e o pedido usa o mesmo par para o cartão
   *    não contar numa janela e mandar para outra.
   */
  const tratativa = useQuery({
    queryKey: ['epi', 'tratativa', janelaTratativa.de],
    queryFn: () =>
      api.get<{ data?: { total?: number; total_situacoes?: number } }>(
        `/alerts?kind=violation&acknowledged=false&module_code=epi` +
          `&start_date=${encodeURIComponent(janelaTratativa.de)}` +
          `&end_date=${encodeURIComponent(janelaTratativa.ate)}&per_page=1`,
      ),
    staleTime: 30_000,
    refetchInterval: 60_000,
  })

  const atualizar = useCallback(() => {
    void estatisticas.refetch()
    void linhaDoTempo.refetch()
    void resumo.refetch()
    void rankingCameras.refetch()
    void perfil.refetch()
    void tratativa.refetch()
  }, [estatisticas, linhaDoTempo, resumo, rankingCameras, perfil, tratativa])

  const alterarPref = useCallback((proxima: Preferencia) => {
    setPref(proxima)
    gravarPreferencia(proxima)
  }, [])

  const sensores = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 4 } }))

  const visiveis = useMemo(
    () => pref.ordem.filter((id) => !pref.ocultos.includes(id)),
    [pref],
  )

  const aoSoltar = useCallback(
    (evento: DragEndEvent) => {
      const { active, over } = evento
      if (!over || active.id === over.id) return
      const de = pref.ordem.indexOf(active.id as IdWidget)
      const para = pref.ordem.indexOf(over.id as IdWidget)
      if (de < 0 || para < 0) return
      const ordem = [...pref.ordem]
      const [movido] = ordem.splice(de, 1)
      ordem.splice(para, 0, movido)
      alterarPref({ ...pref, ordem })
    },
    [pref, alterarPref],
  )

  // ── Estados de tela inteira ───────────────────────────────────────────────

  if (estatisticas.isPending) {
    return (
      <LogikosLoader
        estado="waiting"
        variante="fullscreen"
        rotulo="CARREGANDO DASHBOARD"
        tamanho={96}
      />
    )
  }

  if (estatisticas.isError) {
    return (
      <div className={s.telaCentral}>
        <AlertTriangle size={36} strokeWidth={1.5} color={lk.estado.nc} aria-hidden="true" />
        <span className={s.telaTitulo}>Não foi possível carregar</span>
        <span className={s.telaDetalhe}>Os indicadores deste módulo não responderam.</span>
        <button type="button" className={s.botaoPrimario} onClick={atualizar}>
          Tentar novamente
        </button>
      </div>
    )
  }

  const dados = estatisticas.data
  const camerasTotal = dados.cameras_total ?? 0
  const camerasAtivas = dados.cameras_active ?? 0
  const eventosHoje = dados.alerts_today ?? 0
  const eventosSemana = dados.alerts_week ?? 0

  // Vazio de verdade = não há de onde vir dado nenhum. Zero evento com câmera
  // rodando NÃO é vazio: é o resultado que o cliente quer ver, e esconder isso
  // atrás de "sem dados" apagaria justamente o dia bom.
  if (camerasTotal === 0 && eventosHoje === 0) {
    return (
      <div className={s.telaCentral}>
        <LayoutGrid size={36} strokeWidth={1.5} color={lk.cor.cinzaNevoa} aria-hidden="true" />
        <span className={s.telaTitulo}>Sem dados para este módulo</span>
        <span className={s.telaTexto}>
          Nenhuma câmera está atribuída ao EPI ainda. O score aparece após as primeiras detecções.
        </span>
        {can('cameras:read') && (
          <Link to={rotaNova('/epi/cameras')} className={s.botaoPrimario}>
            Ver câmeras
          </Link>
        )}
      </div>
    )
  }

  // ── Derivações (todas a partir de dado real) ──────────────────────────────

  const score = dados.compliance_rate
  const razaoSemScore =
    RAZAO_SEM_SCORE[dados.compliance_reason ?? ''] ?? 'não foi possível apurar agora'
  const nivelScore: Severidade =
    score == null ? 'atencao' : score >= 90 ? 'ok' : score >= 70 ? 'atencao' : 'nc'
  const palavraScore = score == null ? 'Indisponível' : score >= 90 ? 'Conforme' : score >= 70 ? 'Atenção' : 'Crítico'

  const media7d = eventosSemana / 7
  const deltaEventos = media7d > 0 ? Math.round(((eventosHoje - media7d) / media7d) * 100) : null


  const pontos = fillBuckets(janela.de, janela.ate, 'hour', linhaDoTempo.data?.timeline ?? []).map(
    (p) => ({ ...p, rotulo: formatBucketLabel(p.bucket, 'hour') }),
  )
  const picoEventos = pontos.reduce<(typeof pontos)[number] | null>(
    (maior, p) => (maior === null || p.count > maior.count ? p : maior),
    null,
  )
  const maxHora = picoEventos?.count ?? 0
  const totalHoras = pontos.reduce((soma, p) => soma + p.count, 0)

  const porClasse = [...(resumo.data?.by_class ?? [])].sort((a, b) => b.count - a.count)
  const maxClasse = porClasse[0]?.count ?? 0
  const totalClasses = porClasse.reduce((soma, c) => soma + c.count, 0)

  // Backend já devolve top-10 ordenado por count DESC (top_cameras_by_alerts).
  const porCamera = rankingCameras.data?.by_camera ?? []
  const maxCamera = porCamera[0]?.count ?? 0
  const totalEventosCameras = porCamera.reduce((soma, c) => soma + c.count, 0)
  const concentracaoTop3 =
    totalEventosCameras > 0
      ? Math.round(
          (porCamera.slice(0, 3).reduce((soma, c) => soma + c.count, 0) / totalEventosCameras) * 100,
        )
      : 0

  const faixaTurno = `HOJE · ${String(janela.faixa.inicio).padStart(2, '0')}H–${String(
    janela.faixa.fim,
  ).padStart(2, '0')}H`

  // ── Perfil temporal (horário do dia · dia · composição) ───────────────────

  const situacao = perfil.data?.situacao

  // Issue #802 — o cartão de tratativa lê o envelope da MESMA chamada que
  // enche a coluna "Aguardando" de `/epi/acoes`. `total_situacoes` é a
  // contagem de RAJADAS do recorte inteiro (não da página); sem ele (backend
  // antigo/mock) cai para linhas, igual ao sino e à lista de eventos.
  const eventosAguardando = tratativa.data?.data?.total ?? 0
  const situacoesAguardando = tratativa.data?.data?.total_situacoes ?? eventosAguardando
  const dadosPerfil = agregarPerfil(perfil.data?.rows ?? [])
  const picoHora = picoDeViolacao(dadosPerfil.porHora)
  const maxHoraDia = dadosPerfil.porHora.reduce((m, p) => (p.total > m ? p.total : m), 0)
  const maxDia = dadosPerfil.porDia.reduce((m, p) => (p.total > m ? p.total : m), 0)
  const perfilVazio = dadosPerfil.total === 0

  /**
   * Alcance REAL do dado, não a janela pedida. Sem isto o painel diz "últimos
   * 90 dias" e o leitor supõe 90 dias de operação — a mesma suposição que
   * `SCORE SEM SÉRIE` existe para impedir do outro lado da tela.
   */
  const alcancePerfil = (() => {
    if (!situacao?.primeira_captura || !situacao.ultima_captura) return null
    const de = new Date(situacao.primeira_captura.replace(/Z?$/, 'Z'))
    const ate = new Date(situacao.ultima_captura.replace(/Z?$/, 'Z'))
    if (Number.isNaN(de.getTime()) || Number.isNaN(ate.getTime())) return null
    const dia = (d: Date) => d.toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit' })
    return { texto: `${dia(de)} a ${dia(ate)}`, dias: dadosPerfil.diasComRegistro }
  })()

  const legendaAlcance = alcancePerfil
    ? `${alcancePerfil.texto} · ${numero(alcancePerfil.dias)} dia(s) com registro`
    : null

  /** Rótulo de dia só quando cabe — 60 barras com data embaixo vira borrão. */
  const mostrarRotuloDia = dadosPerfil.porDia.length <= 16

  const taxaViolacao =
    dadosPerfil.total > 0 ? Math.round((dadosPerfil.violacoes / dadosPerfil.total) * 100) : null

  /**
   * Dia de maior volume. A série real é irregular (a coleta é por campanha, não
   * contínua), então uma barra alta esmaga as outras e o painel se lê como
   * vazio. A frase põe o número na tela mesmo quando a barra do vizinho tem
   * dois pixels — e ela é o mesmo dado do gráfico, não um resumo à parte.
   */
  const diaPico = dadosPerfil.porDia.reduce<(typeof dadosPerfil.porDia)[number] | null>(
    (maior, p) => (p.total > 0 && (maior === null || p.total > maior.total) ? p : maior),
    null,
  )

  const revisados = (situacao?.procedentes ?? 0) + (situacao?.improcedentes ?? 0)

  const COMPOSICAO: Array<{ chave: 'violacao' | 'conformidade' | 'indefinido'; rotulo: string }> = [
    { chave: 'violacao', rotulo: 'Violação' },
    { chave: 'conformidade', rotulo: 'Conformidade (EPI em uso)' },
    { chave: 'indefinido', rotulo: 'Não definida' },
  ]

  /** Vazio comum aos três painéis do perfil — mesma causa, mesma frase. */
  const vazioPerfil = (
    <VazioPainel
      texto="Nenhum evento capturado no período."
      cta={{ texto: 'Ver eventos →', to: rotaNova('/epi/eventos') }}
    />
  )

  const painelPorId: Record<IdWidget, ReactNode> = {
    'violacoes-horario': (
      <Painel
        key="violacoes-horario"
        id="violacoes-horario"
        titulo="Violações por horário do dia"
        nota={legendaAlcance ? undefined : 'HORÁRIO DE CAPTURA'}
      >
        {perfil.isError ? (
          <VazioPainel
            texto="Não foi possível carregar o perfil por horário."
            aoRetentar={() => void perfil.refetch()}
          />
        ) : perfil.isPending ? (
          <VazioPainel texto="Carregando…" />
        ) : perfilVazio ? (
          vazioPerfil
        ) : (
          <>
            <div className={s.destaqueLinha}>
              {picoHora ? (
                <>
                  A fábrica concentra violação às{' '}
                  <span className={s.destaqueNumero}>
                    {String(picoHora.hora).padStart(2, '0')}h
                  </span>{' '}
                  — <span className={s.destaqueNumero}>{numero(picoHora.violacoes)}</span> de{' '}
                  {numero(picoHora.total)} evento(s) daquele horário.
                </>
              ) : (
                <>
                  Nenhuma violação neste período — {numero(dadosPerfil.total)} evento(s)
                  registrados, todos fora do balde de violação.
                </>
              )}
            </div>
            <div
              className={s.barras}
              role="group"
              aria-label="Violações por horário do dia, últimos 90 dias"
            >
              {dadosPerfil.porHora.map((p) => {
                const alturaTotal = maxHoraDia > 0 ? (p.total / maxHoraDia) * 100 : 0
                const fatiaViolacao = p.total > 0 ? (p.violacoes / p.total) * 100 : 0
                return (
                  <div
                    key={p.hora}
                    className={s.colunaEmpilhada}
                    title={`${String(p.hora).padStart(2, '0')}h · ${numero(p.violacoes)} violação(ões) de ${numero(p.total)} evento(s)`}
                    aria-label={`${String(p.hora).padStart(2, '0')} horas: ${numero(p.violacoes)} violações de ${numero(p.total)} eventos`}
                  >
                    <div className={s.pilha} style={{ height: `${alturaTotal}%` }}>
                      <div
                        className={s.segmentoViolacao}
                        style={{ height: `${fatiaViolacao}%` }}
                      />
                      <div
                        className={s.segmentoRestante}
                        style={{ height: `${100 - fatiaViolacao}%` }}
                      />
                    </div>
                    {/*
                      Rótulo a cada 6h. Com 3h os "18h"/"21h" encavalavam: o
                      painel tem ~300px para 24 colunas (12px cada) e "18h" em
                      mono de 9,5px pede o dobro disso. A cada 6h cada rótulo
                      tem 72px só seus, e o eixo continua legível.
                    */}
                    <span className={s.rotuloEixo}>
                      {p.hora % 6 === 0 ? `${String(p.hora).padStart(2, '0')}h` : ''}
                    </span>
                  </div>
                )
              })}
            </div>
            <div className={s.chaveCores}>
              <span className={s.chaveItem}>
                <span className={s.chaveMarca} style={{ background: lk.estado.nc }} />
                Violação
              </span>
              <span className={s.chaveItem}>
                <span className={s.chaveMarca} style={{ background: lk.cor.borda }} />
                Demais eventos
              </span>
            </div>
            {legendaAlcance && <span className={s.legenda}>{legendaAlcance}</span>}
          </>
        )}
      </Painel>
    ),

    'volume-dia': (
      <Painel key="volume-dia" id="volume-dia" titulo="Volume por dia">
        {perfil.isError ? (
          <VazioPainel
            texto="Não foi possível carregar o volume por dia."
            aoRetentar={() => void perfil.refetch()}
          />
        ) : perfil.isPending ? (
          <VazioPainel texto="Carregando…" />
        ) : perfilVazio ? (
          vazioPerfil
        ) : (
          <>
            {diaPico && (
              <div className={s.destaqueLinha}>
                Maior volume em{' '}
                <span className={s.destaqueNumero}>{rotuloDia(diaPico.dia)}</span> —{' '}
                <span className={s.destaqueNumero}>{numero(diaPico.total)}</span> evento(s),{' '}
                {numero(diaPico.violacoes)} violação(ões).
              </div>
            )}
            <div className={s.barras} role="group" aria-label="Volume de eventos por dia">
              {dadosPerfil.porDia.map((p) => {
                const alturaTotal = maxDia > 0 ? (p.total / maxDia) * 100 : 0
                const fatiaViolacao = p.total > 0 ? (p.violacoes / p.total) * 100 : 0
                return (
                  <div
                    key={p.dia}
                    className={s.colunaEmpilhada}
                    title={`${rotuloDia(p.dia)} · ${numero(p.violacoes)} violação(ões) de ${numero(p.total)} evento(s)`}
                    aria-label={`${rotuloDia(p.dia)}: ${numero(p.violacoes)} violações de ${numero(p.total)} eventos`}
                  >
                    <div className={s.pilha} style={{ height: `${alturaTotal}%` }}>
                      <div
                        className={s.segmentoViolacao}
                        style={{ height: `${fatiaViolacao}%` }}
                      />
                      <div
                        className={s.segmentoRestante}
                        style={{ height: `${100 - fatiaViolacao}%` }}
                      />
                    </div>
                    {mostrarRotuloDia && (
                      <span className={s.rotuloEixo}>{rotuloDia(p.dia)}</span>
                    )}
                  </div>
                )
              })}
            </div>
            <div className={s.chaveCores}>
              <span className={s.chaveItem}>
                <span className={s.chaveMarca} style={{ background: lk.estado.nc }} />
                Violação
              </span>
              <span className={s.chaveItem}>
                <span className={s.chaveMarca} style={{ background: lk.cor.borda }} />
                Demais eventos
              </span>
            </div>
            <span className={s.legenda}>
              {legendaAlcance ?? `${numero(dadosPerfil.porDia.length)} dia(s)`}
            </span>
          </>
        )}
      </Painel>
    ),

    composicao: (
      <Painel key="composicao" id="composicao" titulo="Composição dos eventos">
        {perfil.isError ? (
          <VazioPainel
            texto="Não foi possível carregar a composição."
            aoRetentar={() => void perfil.refetch()}
          />
        ) : perfil.isPending ? (
          <VazioPainel texto="Carregando…" />
        ) : perfilVazio ? (
          vazioPerfil
        ) : (
          <>
            {COMPOSICAO.map(({ chave, rotulo }) => {
              const valor = dadosPerfil.porTipo[chave]
              return (
                <div key={chave} className={s.classeLinha}>
                  <span className={s.classeNome} title={rotulo}>
                    {rotulo}
                  </span>
                  <div className={s.classeTrilho}>
                    <div
                      className={s.polaridadePreenchimento[chave]}
                      style={{
                        width: `${dadosPerfil.total > 0 ? Math.round((valor / dadosPerfil.total) * 100) : 0}%`,
                      }}
                    />
                  </div>
                  <span className={s.classeValor}>{numero(valor)}</span>
                </div>
              )
            })}
            <span className={s.legenda}>
              {taxaViolacao}% dos {numero(dadosPerfil.total)} eventos do período são violação. Um
              evento de conformidade é EPI registrado EM USO — soma ao total, não ao risco.
            </span>
            <div className={s.rankingDivisor} />
            <div className={s.linhaResumo}>
              <span>Revisados por pessoa</span>
              <span className={s.linhaResumoValor}>
                {numero(revisados)}
                {revisados > 0 && (
                  <>
                    {' '}
                    <span className={s.legenda}>
                      ({numero(situacao?.procedentes ?? 0)} procedentes ·{' '}
                      {numero(situacao?.improcedentes ?? 0)} descartados)
                    </span>
                  </>
                )}
              </span>
            </div>
            {situacao?.confianca_media != null && (
              <div className={s.linhaResumo}>
                <span>Confiança média do detector</span>
                <span className={s.linhaResumoValor}>
                  {Math.round(situacao.confianca_media * 100)}%
                </span>
              </div>
            )}
            <div className={s.linhaResumo}>
              <span>Câmeras com evento no período</span>
              <span className={s.linhaResumoValor}>{numero(situacao?.cameras ?? 0)}</span>
            </div>
          </>
        )}
      </Painel>
    ),

    'eventos-hora': (
      <Painel key="eventos-hora" id="eventos-hora" titulo="Eventos por hora" nota={faixaTurno}>
        {linhaDoTempo.isError ? (
          <VazioPainel
            texto="Não foi possível carregar a linha do tempo."
            aoRetentar={() => void linhaDoTempo.refetch()}
          />
        ) : linhaDoTempo.isPending ? (
          <VazioPainel texto="Carregando…" />
        ) : totalHoras === 0 ? (
          <VazioPainel
            texto="Sem eventos no período."
            cta={{
              texto: 'Ver últimos 30 dias →',
              // `/v1/events/timeline` não filtra kind: este painel conta TODO
              // evento do módulo, e o link tem de pedir o mesmo.
              to: linkParaEventos({
                kind: 'todos',
                start_date: janela30d.de,
                end_date: janela30d.ate,
              }),
            }}
          />
        ) : (
          <>
            <div
              className={s.barrasRolagem}
              role="group"
              aria-label={`Eventos por hora, ${faixaTurno}`}
            >
              {pontos.map((p) => {
                const nivel: Severidade =
                  p.count === 0
                    ? 'neutro'
                    : p.count === maxHora
                      ? 'nc'
                      : p.count >= maxHora * 0.6
                        ? 'atencao'
                        : 'neutro'
                const fimHora = new Date(new Date(p.bucket).getTime() + 3_600_000).toISOString()
                return (
                  <Link
                    key={p.bucket}
                    to={linkParaEventos({
                      kind: 'todos',
                      start_date: p.bucket,
                      end_date: fimHora,
                    })}
                    className={`${s.colunaBarra} ${s.linkLimpo}`}
                    title={`${p.rotulo} · ${numero(p.count)} evento(s)`}
                    aria-label={`${p.rotulo} · ${numero(p.count)} evento(s) · ver eventos`}
                  >
                    <div
                      className={s.barra}
                      style={{
                        height: maxHora > 0 ? `${Math.round((p.count / maxHora) * 100)}%` : '0%',
                        background: p.count === 0 ? lk.cor.borda : COR[nivel],
                      }}
                    />
                    <span className={s.barraRotulo}>{p.rotulo}</span>
                  </Link>
                )
              })}
            </div>
            <span className={s.legenda}>
              Pico às {picoEventos?.rotulo} · {numero(maxHora)} evento(s) · {numero(totalHoras)} no
              período.
            </span>
          </>
        )}
      </Painel>
    ),

    'violacoes-classe': (
      <Painel key="violacoes-classe" id="violacoes-classe" titulo="Violações por classe">
        {resumo.isError ? (
          <VazioPainel
            texto="Não foi possível carregar as classes."
            aoRetentar={() => void resumo.refetch()}
          />
        ) : resumo.isPending ? (
          <VazioPainel texto="Carregando…" />
        ) : totalClasses === 0 ? (
          <VazioPainel
            texto="Sem violações no período."
            cta={{
              texto: 'Ver últimos 30 dias →',
              // `by_class` do resumo já é só violação (mesmo predicado de
              // `list_with_filters(kind='violation')`) — o link mantém o corte.
              to: linkParaEventos({
                kind: 'violation',
                start_date: janela30d.de,
                end_date: janela30d.ate,
              }),
            }}
          />
        ) : (
          <>
            {porClasse.slice(0, 6).map((c) => (
              <Link
                key={c.class}
                to={linkParaEventos({
                  kind: 'violation',
                  start_date: janela.de,
                  end_date: janela.ate,
                  violation_type: c.class,
                })}
                className={`${s.classeLinha} ${s.linkLimpo}`}
                aria-label={`${violationLabel(c.class)} · ${numero(c.count)} evento(s) · ver eventos`}
              >
                <ShieldAlert
                  size={16}
                  strokeWidth={1.7}
                  color={lk.cor.cinzaNevoa}
                  aria-hidden="true"
                />
                <span className={s.classeNome}>{violationLabel(c.class)}</span>
                <div className={s.classeTrilho}>
                  <div
                    className={s.classePreenchimento}
                    style={{ width: `${Math.round((c.count / maxClasse) * 100)}%` }}
                  />
                </div>
                <span className={s.classeValor}>{numero(c.count)}</span>
              </Link>
            ))}
            <span className={s.legenda}>
              {violationLabel(porClasse[0].class)} concentra{' '}
              {Math.round((porClasse[0].count / totalClasses) * 100)}% das violações.
            </span>
          </>
        )}
      </Painel>
    ),

    'cameras-eventos': (
      <Painel
        key="cameras-eventos"
        id="cameras-eventos"
        titulo="Câmeras com mais eventos"
        nota={ROTULO_JANELA_RANKING}
      >
        {rankingCameras.isError ? (
          <VazioPainel
            texto="Não foi possível carregar o ranking de câmeras."
            aoRetentar={() => void rankingCameras.refetch()}
          />
        ) : rankingCameras.isPending ? (
          <VazioPainel texto="Carregando…" />
        ) : totalEventosCameras === 0 ? (
          <div className={s.rankingVazio}>
            <BarChart3 size={30} strokeWidth={1.5} color={lk.cor.cinzaNevoa} aria-hidden="true" />
            <span className={s.rankingVazioTitulo}>Sem eventos no período</span>
            <span className={s.rankingVazioTexto}>
              Nenhuma câmera registrou evento. O ranking aparece assim que houver o primeiro.
            </span>
          </div>
        ) : (
          <>
            <div className={s.rankingLista}>
              {porCamera.map((c, i) => {
                // Regra do ciano: só as 3 primeiras posições em destaque (barra e nome).
                const destaque = i < 3 ? 'top' : 'resto'
                const nome = c.camera_name ?? 'Sem nome'
                const conteudo = (
                  <>
                    <span className={s.rankingPos}>{String(i + 1).padStart(2, '0')}</span>
                    <span className={`${s.rankingNome} ${s.rankingDestaque[destaque]}`} title={nome}>
                      {nome}
                    </span>
                    <div className={s.rankingTrilho}>
                      <div
                        className={s.rankingPreenchimento[destaque]}
                        style={{ width: maxCamera > 0 ? `${Math.round((c.count / maxCamera) * 100)}%` : '0%' }}
                      />
                    </div>
                    <span className={`${s.rankingValor} ${s.rankingDestaque[destaque]}`}>
                      {numero(c.count)}
                    </span>
                  </>
                )
                // Sem camera_id (evento sem câmera atribuída) não tem para onde
                // filtrar — 'camera_id=' vazio mostraria TODAS, o oposto do que
                // a linha representa. Linha fica de pé, só não clicável.
                return c.camera_id ? (
                  <Link
                    key={c.camera_id}
                    to={linkParaEventos({
                      // `top_cameras_by_alerts` conta TODO alerta da câmera,
                      // não só violação.
                      kind: 'todos',
                      start_date: janela30d.de,
                      end_date: janela30d.ate,
                      camera_id: c.camera_id,
                    })}
                    className={`${s.rankingLinha} ${s.linkLimpo}`}
                    aria-label={`${nome} · ${numero(c.count)} evento(s) · ver eventos`}
                  >
                    {conteudo}
                  </Link>
                ) : (
                  <div key={`${nome}-${i}`} className={s.rankingLinha}>
                    {conteudo}
                  </div>
                )
              })}
            </div>
            <div className={s.rankingDivisor} />
            <div className={s.rankingRodape}>
              <span className={s.legenda}>
                As três primeiras concentram{' '}
                <strong className={s.rankingEnfase}>{concentracaoTop3}%</strong> dos eventos do
                período.
              </span>
              {can('alerts:read') && (
                <Link to={rotaNova('/epi/eventos')} className={s.atalhoInline}>
                  ver eventos →
                </Link>
              )}
            </div>
          </>
        )}
      </Painel>
    ),
  }

  return (
    <div className={s.raiz}>
      <div className={s.cabecalho}>
        <h1 className={s.titulo}>Dashboard</h1>
        <span className={s.espacador} />

        <select
          className={s.seletor}
          aria-label="Turno"
          value={turno}
          onChange={(e) => setTurno(e.target.value as IdTurno)}
        >
          {TURNOS.map((t) => (
            <option key={t.id} value={t.id}>
              {t.rotulo}
            </option>
          ))}
        </select>

        <div
          className={s.envoltorioPopover}
          ref={refPersonalizar}
          onKeyDown={(e) => e.key === 'Escape' && setPersonalizar(false)}
        >
          <button
            type="button"
            className={s.botaoFantasma}
            aria-expanded={personalizar}
            onClick={() => setPersonalizar((v) => !v)}
          >
            <SlidersHorizontal size={14} strokeWidth={1.7} aria-hidden="true" />
            Personalizar widgets
          </button>
          {personalizar && (
            <div className={s.popover} role="group" aria-label="Personalizar widgets">
              <span className={s.overline}>Widgets visíveis</span>
              {WIDGETS.map((w) => (
                <label key={w.id} className={s.popoverLinha}>
                  <input
                    type="checkbox"
                    className={s.popoverCheck}
                    checked={!pref.ocultos.includes(w.id)}
                    onChange={() =>
                      alterarPref({
                        ...pref,
                        ocultos: pref.ocultos.includes(w.id)
                          ? pref.ocultos.filter((id) => id !== w.id)
                          : [...pref.ocultos, w.id],
                      })
                    }
                  />
                  <span>{w.rotulo}</span>
                </label>
              ))}
              <button
                type="button"
                className={s.botaoRetentar}
                onClick={() => alterarPref({ ordem: PADRAO, ocultos: [] })}
              >
                Restaurar padrão
              </button>
            </div>
          )}
        </div>
      </div>

      <div className={s.gridKpi}>
        {/* Score de conformidade */}
        <section className={s.cartaoScore} aria-label="Score de conformidade">
          <div className={s.linhaOverline}>
            <span className={s.overline}>Score de conformidade</span>
            <button
              type="button"
              className={s.botaoAjuda}
              aria-label="Como o score é calculado"
              aria-expanded={dicaAberta}
              onClick={() => setDicaAberta((v) => !v)}
              onMouseEnter={() => setDicaAberta(true)}
              onMouseLeave={() => setDicaAberta(false)}
            >
              i
            </button>
          </div>
          {dicaAberta && (
            <div className={s.dica} role="note">
              Como é calculado: 100 × (1 − horas-câmera com violação ÷ (câmeras ativas × 24)) nas
              últimas 24 h. O score só é calculado quando ALGUM evento chegou nessas 24 h — sem
              câmera ativa, sem nada chegando, ou com a consulta falhando ele aparece como
              indisponível, nunca como 100. Zero violação em zero hora observada é ausência de
              medição, não conformidade.
            </div>
          )}
          <div className={s.scoreLinha}>
            <span className={s.scoreNumero}>{score == null ? '—' : scoreImpresso(score)}</span>
            <div className={s.scoreLado}>
              <Estado nivel={nivelScore} palavra={palavraScore} />
              <span className={s.legenda}>
                {/* issue #789: a legenda diz o EIXO, não só a janela. "Score de
                    conformidade" sobre "% das horas-câmera sem violação" é o
                    que o número mede — e sem isso a única explicação do que
                    ele afirma vivia atrás do botão "i". */}
                {score == null
                  ? razaoSemScore
                  : '% das horas-câmera sem violação · últimas 24 h · todas as câmeras do módulo'}
              </span>
            </div>
          </div>
          <div className={s.rodapeMono}>
            {/*
              "SEM HISTÓRICO" sozinho contradizia o cartão ao lado, que mostra
              "% vs média 7d" a 40px daqui: um diz que não há histórico, o outro
              calcula em cima de uma média de 7 dias. São coisas diferentes — não
              existe SÉRIE DO SCORE em endpoint nenhum, enquanto a contagem de
              eventos da semana existe (`alerts_week`) — mas quem lê a tela não
              tem como saber disso. O rótulo passa a dizer de que histórico fala.
            */}
            <span>SCORE SEM SÉRIE · 7 DIAS</span>
            <span>ATUALIZADO {horaCurta(estatisticas.dataUpdatedAt)}</span>
          </div>
        </section>

        {/* Eventos hoje */}
        <section className={`${s.cartaoKpi} ${s.acento.neutro}`} aria-label="Eventos hoje">
          <span className={s.overline}>Eventos hoje</span>
          <Link
            // `alerts_today` conta TODO alerta do módulo, não só violação.
            to={linkParaEventos({
              kind: 'todos',
              start_date: janelaHoje.de,
              end_date: janelaHoje.ate,
            })}
            className={`${s.kpiValor} ${s.linkLimpo}`}
            aria-label={`Eventos hoje: ${numero(eventosHoje)} · ver eventos`}
          >
            {numero(eventosHoje)}
          </Link>
          <span className={s.legenda}>
            {deltaEventos === null ? (
              'sem média de 7 dias ainda'
            ) : (
              <>
                {deltaEventos >= 0 ? (
                  <ArrowUp size={12} strokeWidth={2} aria-hidden="true" />
                ) : (
                  <ArrowDown size={12} strokeWidth={2} aria-hidden="true" />
                )}{' '}
                {deltaEventos >= 0 ? '+' : '−'}
                {Math.abs(deltaEventos)}% vs média 7d
              </>
            )}
          </span>
          {can('alerts:read') && (
            <Link to={rotaNova('/epi/eventos')} className={s.atalho}>
              triar eventos →
            </Link>
          )}
        </section>

        {/* Aguardando tratativa — `alerts.acknowledged`, a mesma coluna que
            `/epi/acoes` escreve. Não é "plano de ação com prazo" (isso não
            existe, ver cabeçalho): é quanta VIOLAÇÃO ninguém abriu ainda.
            Issue #802: o número é o da fila de destino, e a legenda diz o
            recorte inteiro (tipo + tempo) em vez de deixar supor. */}
        {tratativa.isSuccess && (
          <section
            className={`${s.cartaoKpi} ${
              situacoesAguardando > 0 ? s.acento.atencao : s.acento.ok
            }`}
            aria-label="Aguardando tratativa"
          >
            <span className={s.overline}>Aguardando tratativa</span>
            <Link
              // Os TRÊS eixos do recorte que produziu o número, declarados:
              // tipo (`violation`), tratativa (`acknowledged=false`) e tempo
              // (os mesmos 30 dias). Sem o `kind` o destino mostrava também
              // conformidade; sem `acknowledged` mostrava o que já foi tratado.
              to={linkParaEventos({
                kind: 'violation',
                acknowledged: 'false',
                start_date: janelaTratativa.de,
                end_date: janelaTratativa.ate,
              })}
              className={`${s.kpiValor} ${s.linkLimpo}`}
              aria-label={`Aguardando tratativa: ${numero(situacoesAguardando)} · ver eventos`}
            >
              {numero(situacoesAguardando)}
            </Link>
            <Estado
              nivel={situacoesAguardando > 0 ? 'atencao' : 'ok'}
              palavra={situacoesAguardando > 0 ? 'Sem reconhecimento' : 'Tudo reconhecido'}
            />
            <span className={s.legenda}>
              {/* SITUAÇÕES no número grande (é o que `/epi/acoes` conta na
                  coluna "Aguardando"), EVENTOS na legenda (é o que a lista de
                  `/epi/eventos` conta). As duas unidades aparecem porque as
                  duas telas de destino usam uma cada — esconder uma delas é
                  reabrir a divergência por outro caminho.

                  ⚠️ E a legenda tem de dizer QUAL é qual. Ela dizia só "1.096
                  evento(s)" embaixo de um 735 sem unidade nenhuma: dois
                  números diferentes para o que parece a mesma coisa, e nada na
                  tela ligando um ao outro — quem lê desconfia do cartão ou
                  escolhe um dos dois no chute. As DUAS unidades passam a sair
                  nomeadas, na mesma ordem e com as mesmas palavras do contador
                  de `/epi/eventos` ("N SITUAÇÕES · M EVENTOS"), que é a tela
                  para onde o número grande leva. */}
              {situacoesAguardando !== eventosAguardando
                ? `${numero(situacoesAguardando)} situações · ${numero(eventosAguardando)} eventos · violação sem reconhecimento · ${JANELA_TRATATIVA_DIAS}d`
                : `violação sem reconhecimento · últimos ${JANELA_TRATATIVA_DIAS} dias`}
            </span>
            {can('alerts:read') && (
              <Link to={rotaNova('/epi/acoes')} className={s.atalho}>
                tratar eventos →
              </Link>
            )}
          </section>
        )}

        {/* Câmeras ativas — não existe telemetria de conectividade por câmera
            (ver cabeçalho, item 4): "online" prometeria um dado que o
            sistema não mede. O que existe é status de cadastro.

            ⚠️ E o status de cadastro é DO MÓDULO, não do tenant:
            `count_by_status` (camera_repository.py) filtra por
            `escopo_camera_sql(module_code='epi')`. Medido no DEV (RVB,
            09/09): 18 câmeras ativas no tenant, 17 delas atribuídas ao EPI —
            "Espaço de convivência" (canal 10) está ativa e NÃO é câmera de
            EPI. O 17 é o número certo para esta tela; o que faltava era o
            cartão dizer sobre qual conjunto ele fala, porque "Cadastradas e
            ativas" sozinho não fecha com a lista de câmeras do tenant. */}
        <section
          className={`${s.cartaoKpi} ${camerasAtivas > 0 ? s.acento.ok : s.acento.atencao}`}
          aria-label="Câmeras ativas"
        >
          <span className={s.overline}>Câmeras ativas</span>
          <span className={s.kpiValor}>{numero(camerasAtivas)}</span>
          <Estado
            nivel={camerasAtivas > 0 ? 'ok' : 'atencao'}
            palavra={
              camerasAtivas > 0 ? 'Atribuídas ao EPI e ativas' : 'Nenhuma câmera ativa no EPI'
            }
          />
          <span className={s.legenda}>sem telemetria de conectividade por câmera ainda</span>
          {can('cameras:read') && (
            <Link to={rotaNova('/epi/cameras')} className={s.atalho}>
              ver câmeras →
            </Link>
          )}
        </section>
      </div>

      <DndContext sensors={sensores} collisionDetection={closestCenter} onDragEnd={aoSoltar}>
        <SortableContext items={visiveis} strategy={rectSortingStrategy}>
          <div className={s.gridPaineis}>{visiveis.map((id) => painelPorId[id])}</div>
        </SortableContext>
      </DndContext>
    </div>
  )
}

export default Dashboard
