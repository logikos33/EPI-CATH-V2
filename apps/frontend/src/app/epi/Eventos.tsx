/**
 * EPI Eventos — a lista de eventos do módulo EPI (rota nova `/epi/eventos`).
 *
 * Migração da `pages/AlertsHistoryPage.tsx` (rota antiga `/epi/alerts`) para o
 * front novo, contra o desenho `EPI Eventos.dc.html`. O de-para está em
 * `docs/migration/DELTA-PRE-MIGRACAO.md` §3.1.
 *
 * TRÊS EIXOS DISTINTOS, três colunas, três paletas — é o que o delta §2 manda
 * preservar e o que esta tela existe para não confundir:
 *
 *   EVENTO   = POLARIDADE — o que o evento É (classe do modelo, ADR-0065).
 *              Três estados: violação · conformidade · NÃO DEFINIDA. Verde e
 *              vermelho moram aqui e só aqui.
 *   VEREDITO = o que uma PESSOA julgou. Branco/âmbar/cinza, nunca verde nem
 *              vermelho — se "falso positivo" fosse vermelho, veredito e
 *              violação virariam a mesma cor na mesma linha.
 *   STATUS   = fluxo de trabalho (alguém deu ciência). Reconhecer é sempre um
 *              clique explícito, nunca hover.
 *
 * O QUE ESTA TELA NÃO FAZ, e por quê (para o design / backend):
 *
 * · **Miniatura da detecção.** O desenho tem uma coluna DETECÇÃO com o frame e
 *   a caixa. `GET /api/alerts` devolve `evidence_key`, mas NÃO devolve URL
 *   assinada — só o detalhe (`GET /api/alerts/<id>`) e `/snapshot` assinam, um
 *   alerta por requisição. Desenhar um retângulo cinza no lugar seria dado
 *   inventado; 20 requisições por página, um N+1 por rolagem. Falta
 *   `evidence_url` na listagem (ou uma rota em lote).
 * · **Faixa "eventos por hora".** A única fonte agregada é
 *   `GET /api/v1/events/timeline`, que ignora `kind` e `acknowledged` e soma
 *   `demo_events`: as barras discordariam da tabela logo abaixo sob o filtro
 *   padrão da própria tela. E a segunda série do desenho ("violação
 *   confirmada") não tem fonte nenhuma. Falta um agregado por hora que aceite
 *   os mesmos filtros da lista e separe confirmadas.
 * · **Status "descartado".** O desenho traz três estados numa coluna só;
 *   `alerts.acknowledged` é booleano. Descartar, na prática, é o veredito
 *   "falso positivo" — que é OUTRO eixo e tem coluna própria.
 *
 * ⚠️ **SITUAÇÕES: uma palavra, dois eixos de tempo.** Medido no DEV em
 * 2026-09-05 (tenant RVB, 30 dias, todos os tipos, 5.122 linhas), a MESMA
 * regra "mesma câmera+classe em <60s" dá três números diferentes conforme
 * onde e sobre qual coluna é aplicada:
 *
 *   3.308  `total_situacoes` do backend — gap encadeado sobre `created_at`
 *          (GRAVAÇÃO). É o número que esta tela imprime.
 *   3.385  a mesma regra sobre `timestamp` (CAPTURA) no conjunto inteiro.
 *   3.658  a regra aplicada página a página, somando as 257 páginas — rajada
 *          cortada na virada da página vira duas.
 *
 * A verdade é 3.385. "Situação" é um fato do CHÃO DE FÁBRICA, e o eixo do
 * chão de fábrica é a captura — é a regra que o resto do produto já segue
 * (`capture_profile`, `review_situation`, o Dashboard inteiro). Medidas as
 * linhas que os dois eixos discordam: 175 alertas foram GRAVADOS a menos de
 * 60s do anterior mas CAPTURADOS a mais de 60s — o `created_at` funde
 * situações distintas só porque a carga em lote as escreveu no mesmo
 * segundo, e some com 77 situações reais.
 *
 * Esta tela não pode consertar o número: `total_situacoes` nasce em
 * `AlertRepository.list_with_filters`. O que ela faz, e é o que está
 * travado em teste aqui:
 *   1. agrupa a página pela CAPTURA (`timestamp ?? created_at`) — o eixo
 *      certo. Trocar para `created_at` para "bater com o badge" esconderia
 *      aquelas 175 linhas atrás de um "+N repetições";
 *   2. NOMEIA o eixo de cada número na tela, em vez de escrever "situações"
 *      como se os dois fossem a mesma coisa.
 * PEDIDO-AO-BACKEND: `total_situacoes` sobre `timestamp` (uma palavra na
 * CTE) — aí os dois viram um só.
 */
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { NavLink, useSearchParams } from 'react-router-dom'
import {
  AlertTriangle, Check, CheckCircle, ChevronDown, ChevronLeft, ChevronRight,
  Circle, CheckCircle2, Clock, Download, Eye, HelpCircle, Inbox, RefreshCw,
  ShieldCheck, XCircle,
  type LucideIcon,
} from 'lucide-react'

import { useAuth } from '../../hooks/useAuth'
import { useModuleClasses } from '../../hooks/useModuleClasses'
import { useToast } from '../../components/ui/Toast/useToast'
import { api, ApiError } from '../../services/api'
import { cameraService } from '../../services/cameraService'
import { confiancaInternaOuCliente } from '../../services/confidenceDisplay'
import { marcarLidas } from '../../services/notificacoes'
import { classificarLatencia } from '../../components/shared/ProcedenciaBadge'
import { procedenciaDeclarada } from '../../components/shared/ProcedenciaEvento'
import { vereditoHumano, type Veredito } from '../../components/shared/VereditoHumano'
import {
  EXPLICACAO_POLARIDADE, ROTULO_POLARIDADE, type Polaridade,
} from '../../components/shared/PolaridadeClasse'
import { labelForVerificationReason } from '../../utils/labels'
import { rangeForPeriod } from '../../utils/timeBuckets'
import { agruparPorRajada } from '../../utils/rajadas'
import type { Camera } from '../../types'
import { LogikosLoader } from '../shell/LogikosLoader'
import * as s from './Eventos.css'
import { rotaNova } from '../RotasNovas'
import { PainelEvidencia, type ResultadoVeredito } from './PainelEvidencia'

const MODULO = 'epi'
const POR_PAGINA = 20

interface Violacao {
  class: string
  confidence?: number
  /** Quem desenhou ESTA caixa, declarado por quem gravou o evento. Chega na
   *  LISTA porque `AlertRepository.list_with_filters` faz `SELECT a.*` — o
   *  JSONB `violations` vem cru, com `origem` e `lote` dentro. */
  origem?: string
  /** Marca da carga em lote do acervo de demonstração. */
  lote?: string
}

interface Evento {
  id: string
  camera_id?: string
  camera_name?: string
  violations: Violacao[]
  acknowledged: boolean
  /** Quando o evento foi GRAVADO. */
  created_at: string
  /** Hora REAL da captura no edge — pode divergir de `created_at`. */
  timestamp?: string
  /** ADR-0065 (contrato A1, três estados): 'compliance' = EPI em uso
   *  (telemetria); 'violation' = classe de violação de verdade; 'observacao'
   *  = classe indecidida (não decide `polaridadeDoEvento` sozinho — quem
   *  desempata é o catálogo, ver abaixo). */
  event_kind?: 'violation' | 'compliance' | 'observacao'
  /** Veredito bruto — a IA grava o MESMO 'approve'/'reject'; sozinho não prova gente. */
  verification_verdict?: string | null
  /** 'user:<id>' (gente) ou 'claude-haiku' (IA) — a prova de procedência do veredito. */
  verified_by?: string | null
  /** Justificativa que a pessoa deu ao julgar (`alerts.verification_reason`). */
  verification_reason?: string | null
}

interface Pagina {
  alerts: Evento[]
  total: number
  /** Rajadas (câmera+classe repetida em <60s) desta MESMA página filtrada —
   *  ux2/dedup, `AlertRepository.list_with_filters`. Ausente em backend/mock
   *  antigo: quem lê cai para `total` (linhas). */
  total_situacoes?: number
  page: number
  per_page: number
  pages: number
}

type Periodo = 'hoje' | '7d' | '30d'

interface Filtros {
  periodo: Periodo
  /** Intervalo cru vindo de deep-link — vence o período até alguém trocá-lo. */
  intervalo: { from: string; to: string } | null
  cameraId: string
  classe: string
  /** '' = todos · 'false' = novo · 'true' = reconhecido (`?acknowledged=`). */
  status: string
  /** ADR-0065 — a tela abre em VIOLAÇÕES: EPI presente é telemetria, não alerta.
   *
   *  DECISÃO (rodada de correção UX, contrato A1): o default CONTINUA
   *  'violation' e NÃO passa a incluir 'observacao' (a classe indecidida).
   *  Não é omissão — é escolha registrada, com dois motivos:
   *
   *  1. O backend não tem um `kind` que signifique "tudo que não é
   *     conformidade" — só os quatro valores testados (violation· compliance
   *     ·observacao·todos). Abrir em '' (todos) para pegar observação junto
   *     traria de volta a CONFORMIDADE (telemetria de EPI em uso) misturada
   *     na lista — exatamente o ruído que a ADR-0065 existe para tirar da
   *     tela padrão. Trocar "indecidido escondido" por "conformidade
   *     poluindo a fila" não é ganho.
   *  2. 'observacao' não é beco sem saída: tem opção PRÓPRIA e visível no
   *     MESMO seletor desta tela ("Não definida (ninguém classificou)"), ao
   *     lado de "Todos os tipos" — quem abre a tela vê as quatro opções.
   *     Some da ABERTURA padrão, não da tela.
   *
   *  Se um dia o backend ganhar um `kind` combinado ("tudo que não é
   *  conformidade"), a revisão certa é trocar o default para ele — não para
   *  '' (todos). Ver teste "abertura padrão pede kind=violation, não
   *  observação" abaixo, que trava esta decisão. */
  kind: string
  pagina: number
}

const ROTULO_VEREDITO: Record<Veredito, string> = {
  procedente: 'Procedente',
  'falso-positivo': 'Falso positivo',
  'nao-revisado': 'Não revisado',
}

const EXPLICACAO_VEREDITO: Record<Veredito, string> = {
  procedente: 'Uma pessoa revisou e considerou o evento correto.',
  'falso-positivo': 'Uma pessoa revisou e considerou a detecção incorreta.',
  'nao-revisado': 'Ninguém julgou este evento ainda. Não é o mesmo que "falso".',
}

const ICONE_POLARIDADE: Record<Polaridade, LucideIcon> = {
  violacao: AlertTriangle,
  conformidade: CheckCircle,
  indefinida: HelpCircle,
}

/** Ícones próprios: estado é cor + ÍCONE + palavra, e os dois eixos não se imitam. */
const ICONE_VEREDITO: Record<Veredito, LucideIcon> = {
  procedente: ShieldCheck,
  'falso-positivo': XCircle,
  'nao-revisado': Clock,
}

// ── STATUS = QUÃO LONGE o evento andou no fluxo ─────────────────────────────
//
// Defeito relatado pelo dono em 09/09/2026, com a tela em operação: linha com
// veredito humano PROCEDENTE e a coluna STATUS ainda dizendo "NOVO".
//
// Causa: a célula lia SÓ `ev.acknowledged` (booleano) — e julgar não mexe em
// `acknowledged`; mexe em `verification_verdict`/`verified_by`. Duas colunas,
// dois atos, e a tela só olhava um deles. Nada estava errado no banco: o
// evento julgado É `acknowledged=false`. Errado era a tela chamar isso de
// "Novo", que é a palavra que manda o operador ir olhar.
//
// Conserto SEM misturar os eixos que o cabeçalho deste arquivo separa: STATUS
// continua sendo fluxo de trabalho, só que com o degrau que faltava. O que
// entra aqui é o FATO de ter sido julgado, nunca o CONTEÚDO do julgamento —
// "Procedente" e "Falso positivo" seguem morando só na coluna VEREDITO, com a
// paleta delas. Um evento julgado não é novo, seja qual for o veredito.
//
//   novo → reconhecido → avaliado
//
// Derivar na tela (em vez de gravar `acknowledged=true` ao julgar) conserta de
// graça as centenas de linhas JÁ julgadas hoje pelo dono: elas passam a ler
// certo sem UPDATE em massa nem migration de dados.
type StatusFluxo = 'novo' | 'reconhecido' | 'avaliado'

const ROTULO_STATUS: Record<StatusFluxo, string> = {
  novo: 'Novo',
  reconhecido: 'Reconhecido',
  avaliado: 'Avaliado',
}

const EXPLICACAO_STATUS: Record<StatusFluxo, string> = {
  novo: 'Ninguém deu ciência nem julgou este evento.',
  reconhecido: 'Alguém deu ciência. Ainda sem veredito de procedência.',
  avaliado: 'Uma pessoa abriu a evidência e julgou. O veredito está na coluna ao lado.',
}

const ICONE_STATUS: Record<StatusFluxo, LucideIcon> = {
  novo: Circle,
  reconhecido: Check,
  avaliado: CheckCircle2,
}

/** Função PURA — a MESMA regra que o servidor usa para mandar julgado para o
 *  fim da fila (`AlertRepository._JULGADO_POR_HUMANO_SQL`). */
function statusDoFluxo(ev: Evento): StatusFluxo {
  if (vereditoHumano(ev.verification_verdict, ev.verified_by) !== 'nao-revisado') {
    return 'avaliado'
  }
  return ev.acknowledged ? 'reconhecido' : 'novo'
}

/** Julgado por GENTE — o critério da ordem da fila e do degrau "Avaliado". */
const julgadoPorHumano = (ev: Evento) =>
  vereditoHumano(ev.verification_verdict, ev.verified_by) !== 'nao-revisado'

/** Ainda cabe "Reconhecer"? Só o que ninguém deu ciência NEM julgou. */
const podeReconhecer = (ev: Evento) => !ev.acknowledged && !julgadoPorHumano(ev)

/** Intervalo do período. "Hoje" é o dia corrente de verdade, não 24h rolantes. */
function intervaloDoPeriodo(periodo: Periodo): { from: string; to: string } {
  if (periodo === 'hoje') {
    const inicio = new Date()
    inicio.setHours(0, 0, 0, 0)
    return { from: inicio.toISOString(), to: new Date().toISOString() }
  }
  const r = rangeForPeriod(periodo)
  return { from: r.from, to: r.to }
}

export function Eventos() {
  const { can, isSuperAdmin } = useAuth()
  const toast = useToast()
  const [parametros] = useSearchParams()
  const { classes, classLabel } = useModuleClasses(MODULO)

  const [filtros, setFiltros] = useState<Filtros>(() => {
    const de = parametros.get('start_date')
    const ate = parametros.get('end_date')
    return {
      periodo: 'hoje',
      intervalo: de && ate ? { from: de, to: ate } : null,
      cameraId: parametros.get('camera_id') ?? '',
      classe: parametros.get('violation_type') ?? '',
      status: parametros.get('acknowledged') ?? '',
      kind: parametros.get('kind') ?? 'violation',
      pagina: 1,
    }
  })
  const [dados, setDados] = useState<Pagina | null>(null)
  const [carregando, setCarregando] = useState(true)
  const [erro, setErro] = useState<string | null>(null)
  const [cameras, setCameras] = useState<Camera[]>([])
  const [selecionados, setSelecionados] = useState<string[]>([])
  const [ocupado, setOcupado] = useState<string | null>(null)
  const [exportando, setExportando] = useState(false)

  // Deep-link do sino: realça a linha e rola até ela, depois solta o realce.
  const [destaque, setDestaque] = useState<string | null>(() => parametros.get('highlight'))
  const refDestaque = useRef<HTMLTableRowElement | null>(null)

  /** Evento aberto na gaveta de evidência — julgar SEM sair da lista. Guarda o
   *  ID, não o objeto: depois de cada veredito a lista é relida e os objetos
   *  são outros; o ID é o que sobrevive à releitura e mantém a posição. */
  const [abertoId, setAbertoId] = useState<string | null>(null)
  const refAberto = useRef<HTMLTableRowElement | null>(null)

  // Rajadas expandidas (id do representante) — ux2/dedup: por padrão o
  // representante fica sozinho na tela; expandir mostra as N repetições.
  const [expandidos, setExpandidos] = useState<Set<string>>(new Set())
  const alternarExpandido = (id: string) =>
    setExpandidos((atual) => {
      const novo = new Set(atual)
      if (novo.has(id)) novo.delete(id)
      else novo.add(id)
      return novo
    })

  const podeLer = can('alerts:read')
  // `alerts:feedback` NÃO é lido aqui: desde que o veredito saiu da linha,
  // quem gateia julgar é a gaveta (`PainelEvidencia`, que chama o mesmo
  // `can('alerts:feedback')`). Um gate a mais nesta tela seria só uma segunda
  // cópia da mesma regra, livre para divergir da primeira.
  const podeExportar = can('alerts:export')

  /** Querystring da listagem — a MESMA base do CSV, para o export sair no recorte da tela. */
  const consulta = useCallback(() => {
    const { from, to } = filtros.intervalo ?? intervaloDoPeriodo(filtros.periodo)
    const p = new URLSearchParams()
    if (filtros.cameraId) p.set('camera_id', filtros.cameraId)
    p.set('start_date', from)
    p.set('end_date', to)
    if (filtros.classe) p.set('violation_type', filtros.classe)
    if (filtros.status !== '') p.set('acknowledged', filtros.status)
    if (filtros.kind) p.set('kind', filtros.kind)
    return p
  }, [filtros])

  /**
   * `silencioso` NÃO é enfeite: sem ele, todo veredito trocava a tabela pelo
   * loader de tela inteira — a gaveta desmontava, a rolagem voltava ao topo e
   * quem estava na linha 14 de 20 perdia o lugar a cada decisão. É exatamente
   * o "sem sair da lista" que esta rodada existe para entregar. A releitura
   * continua vindo do SERVIDOR (nada de carimbar o veredito na mão no objeto
   * local, que seria afirmar autoria sem ter lido a resposta) — só não pisca.
   */
  const carregar = useCallback(async (silencioso = false) => {
    if (!silencioso) setCarregando(true)
    setErro(null)
    try {
      const p = consulta()
      // PAGINAÇÃO POR PÁGINA, como a tela antiga e como o backend calcula o
      // OFFSET (`(page-1)*per_page`, alerts/routes.py). Não trocar por cursor
      // nem por offset cru: a ordenação é determinística no SERVIDOR (julgado
      // por último, depois hora DESC, `a.id` de desempate) e qualquer outro
      // mecanismo aqui reabre o buraco de linhas puladas entre páginas.
      p.set('page', String(filtros.pagina))
      p.set('per_page', String(POR_PAGINA))
      const res = await api.get<{ data?: Pagina }>(`/alerts?${p}`)
      const d = res?.data
      setDados({
        alerts: d?.alerts ?? [],
        total: d?.total ?? 0,
        total_situacoes: d?.total_situacoes,
        page: d?.page ?? filtros.pagina,
        per_page: d?.per_page ?? POR_PAGINA,
        pages: d?.pages ?? 1,
      })
    } catch (e) {
      setDados(null)
      // A rota crua não ajuda quem opera a fábrica — e a régua de jargão não vê
      // string fora de JSX, então esta linha passou meses servida. O código
      // numérico fica: é o que o suporte pede.
      setErro(
        e instanceof ApiError
          ? `A lista de eventos não respondeu (código ${e.status}).`
          : 'A lista de eventos não respondeu.',
      )
    } finally {
      setCarregando(false)
    }
  }, [consulta, filtros.pagina])

  useEffect(() => {
    if (podeLer) void carregar()
  }, [carregar, podeLer])

  // Avançar na gaveta tem de mover a LISTA junto — senão a linha aberta some
  // atrás do painel e "sem perder a posição" vira só uma frase.
  useEffect(() => {
    if (abertoId) refAberto.current?.scrollIntoView?.({ block: 'nearest' })
  }, [abertoId])

  // Nomes de câmera para o filtro do desenho ("CAM-04 Expedição"). Degrada em
  // silêncio: sem a lista, o filtro some — nunca vira campo de digitar UUID.
  useEffect(() => {
    if (!podeLer) return
    let vivo = true
    cameraService
      .list()
      .then((cs) => { if (vivo) setCameras(cs) })
      .catch(() => { if (vivo) setCameras([]) })
    return () => { vivo = false }
  }, [podeLer])

  useEffect(() => {
    if (!destaque || !dados) return
    refDestaque.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    const t = setTimeout(() => setDestaque(null), 4000)
    return () => clearTimeout(t)
  }, [dados, destaque])

  /**
   * POLARIDADE em três estados. `event_kind='compliance'` é afirmação POSITIVA
   * do backend (`is_violation IS FALSE`) e vale sozinha. Já `'violation'`
   * colapsa TRUE **e NULL** no mesmo balde — para exibir, isso mentiria
   * ("ninguém decidiu" apareceria como violação), então quem desempata é o
   * catálogo (`GET /api/modules/epi/classes`, campo `polaridade`).
   *
   * Sem catálogo carregado, ou classe fora dele, não há afirmação a fazer:
   * devolve `null` e a célula mostra só o nome da classe. Ausência de selo =
   * ausência de afirmação (mesma regra do badge de procedência).
   */
  const polaridadePorClasse = useMemo(() => {
    const m = new Map<string, Polaridade>()
    const lista: Array<{ class_name: string; polaridade?: Polaridade }> = classes
    for (const c of lista) if (c.polaridade) m.set(c.class_name, c.polaridade)
    return m
  }, [classes])

  const polaridadeDoEvento = useCallback(
    (ev: Evento): Polaridade | null => {
      if (ev.event_kind === 'compliance') return 'conformidade'
      const classe = ev.violations?.[0]?.class
      return (classe && polaridadePorClasse.get(classe)) || null
    },
    [polaridadePorClasse],
  )

  /** Todo filtro volta para a página 1 — paginar sobre outro recorte é linha pulada. */
  const trocarFiltro = (mudanca: Partial<Filtros>) =>
    setFiltros((f) => ({ ...f, ...mudanca, pagina: 1 }))

  /**
   * AMPLIAR — issue #795. Isto era `limparFiltros`, e resetava para
   * `periodo:'hoje' + kind:'violation'`: exatamente o estado em que a tela
   * NASCE. Ou seja, no primeiro acesso — que é o caso do vazio — o único botão
   * do painel vazio era um NO-OP garantido: o usuário clicava, nada mudava, e
   * a tela continuava sugerindo "filtro demais".
   *
   * Agora ele afrouxa de verdade: 30 dias, TODO tipo de evento, sem câmera,
   * classe nem status. É a saída que o painel promete. Se depois disso ainda
   * vier vazio, aí sim é vazio de verdade — e o texto abaixo diz isso.
   *
   * Não mexe no DEFAULT da tela (violação primeiro segue valendo, ADR-0065 e a
   * decisão registrada acima); mexe só na SAÍDA oferecida no vazio.
   */
  const ampliarBusca = () =>
    setFiltros({
      periodo: '30d', intervalo: null, cameraId: '', classe: '',
      status: '', kind: '', pagina: 1,
    })

  /** O recorte já está no máximo que o botão alcança — não há o que ampliar. */
  const recorteJaAmplo =
    filtros.periodo === '30d' && !filtros.intervalo && filtros.kind === '' &&
    !filtros.cameraId && !filtros.classe && !filtros.status

  /** Reconhecer: ato explícito. Não existe chave de permissão para "dar
   *  ciência" no registry (`core/permissions.py` só tem read/feedback/export),
   *  então o botão segue visível para quem lê — igual à tela antiga. Registrado
   *  para o backend. */
  const reconhecer = async (id: string) => {
    setOcupado(id)
    try {
      await api.post(`/alerts/${id}/acknowledge`)
      setSelecionados((sel) => sel.filter((i) => i !== id))
      await carregar()
    } catch {
      toast.error('Não foi possível reconhecer o evento')
    } finally {
      setOcupado(null)
    }
  }

  /**
   * Reconhecer a SELEÇÃO — UMA requisição, não uma por linha.
   *
   * Era `Promise.all` sobre `POST /alerts/<id>/acknowledge`: com 100 linhas
   * marcadas, 100 requisições em paralelo, cada uma com JWT e UPDATE próprios.
   * Além do custo, o modo de falha era ruim de explicar — "37 de 100 não
   * puderam ser reconhecidos", sem dizer QUAIS, e com a lista já recarregada
   * por baixo. A rota em lote decide tudo numa transação e devolve quantas
   * MUDARAM de estado.
   */
  const reconhecerSelecionados = async () => {
    setOcupado('lote')
    const alvos = [...selecionados]
    try {
      const quantas = await marcarLidas(alvos)
      // O número vem do backend. Se ele vier menor que o pedido, alguma linha
      // já estava reconhecida (outro operador chegou antes) — dizer isso é
      // mais honesto que anunciar sucesso sobre trabalho que não aconteceu.
      if (quantas < alvos.length) {
        toast.info(`${quantas} de ${alvos.length} reconhecidos (o restante já estava)`)
      }
    } catch {
      toast.error('Não foi possível reconhecer os eventos selecionados')
    }
    setSelecionados([])
    setOcupado(null)
    await carregar()
  }

  /**
   * Veredito humano — reusa `POST /api/verification/<id>/review`, que carimba
   * `verified_by='user:<id>'` (a prova que a coluna VEREDITO lê).
   *
   * A assinatura AINDA aceita 'reject' porque é o contrato da rota, mas desta
   * tela só sai 'approve': a linha não rejeita mais (ver o comentário do grupo
   * de botões, abaixo).
   *
   * O MOTIVO (`reason`) não vem daqui. Quem abre a evidência para julgar
   * escolhe um motivo da lista fechada, igual à tela de Verificação: lá a
   * pessoa está OLHANDO o frame, e é dali que sai a informação que recalibra o
   * modelo. Da linha o veredito vai SEM motivo — nunca com motivo vazio, que
   * gravaria "justificado" sobre uma justificativa que ninguém deu. O motivo
   * já registrado aparece abaixo do selo.
   *
   * Devolve o desfecho em vez de engoli-lo: a gaveta precisa saber se avança
   * (veredito registrado, ou 409 de quem julgou primeiro) ou se fica onde
   * está (falha de verdade).
   */
  const julgar = async (
    id: string,
    verdict: 'approve' | 'reject',
    reason?: string,
  ): Promise<ResultadoVeredito> => {
    setOcupado(id)
    try {
      await api.post(`/verification/${id}/review`, { verdict, ...(reason ? { reason } : {}) })
      await carregar(true)
      return 'ok'
    } catch (e) {
      // 409 = OUTRA PESSOA julgou este alerta primeiro (guarda
      // `verification_verdict IS NULL OR verified_by = <eu>` do UPDATE, em
      // verification_service.py). É INFORMAÇÃO, não falha do operador: a
      // mensagem do servidor já diz QUEM julgou e QUANDO, e a lista recarrega
      // para a coluna VEREDITO mostrar a decisão que existe.
      // ⛔ Mesma regra do bloco 4 de `Verificacao.tsx`: um "Não foi possível
      // registrar" genérico faz o operador clicar de novo no que já resolveu.
      if (e instanceof ApiError && e.status === 409) {
        toast.info('Alerta já revisado', e.message)
        await carregar(true)
        return 'conflito'
      }
      toast.error('Não foi possível registrar o veredito')
      return 'erro'
    } finally {
      setOcupado(null)
    }
  }

  const exportar = async () => {
    setExportando(true)
    try {
      const blob = await api.downloadBlob(`/alerts/export?${consulta()}`)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = 'eventos.csv'
      a.click()
      URL.revokeObjectURL(url)
    } catch {
      toast.error('Erro ao exportar')
    } finally {
      setExportando(false)
    }
  }

  const eventos = dados?.alerts ?? []
  const selecionaveis = eventos.filter(podeReconhecer).map((e) => e.id)

  /**
   * ux2/dedup: agrupa a PÁGINA carregada por câmera+classe+60s — mesma janela
   * do backend (VerificationService). "Situações" no badge do cabeçalho/rodapé
   * vem de `total_situacoes` (o filtro INTEIRO), não deste agrupamento local.
   *
   * ⚠️ **A ORDEM DA FILA PRECISA DAS DUAS METADES.** O servidor já manda
   * julgado por último (`AlertRepository._JULGADO_POR_HUMANO_SQL`, ANTES do
   * LIMIT/OFFSET — é ele quem decide QUAIS 20 linhas a página traz). Mas
   * `agruparPorRajada` **descarta a ordem de entrada**: ele reagrupa por chave
   * e reordena os grupos por hora do representante (ver `rajadas.ts`). Sem o
   * `sort` abaixo, o trabalho do servidor chegava na tela e era jogado fora —
   * o operador continuaria vendo julgado no meio da lista.
   *
   * O `sort` é ESTÁVEL (garantia da spec desde ES2019), então dentro de cada
   * balde a cronologia que veio do servidor sobrevive intacta: não-julgado
   * mais recente primeiro, e só depois os julgados, também do mais recente.
   *
   * Grupo afunda só quando TODAS as suas linhas foram julgadas. Uma rajada de
   * 30 repetições com 29 julgadas ainda tem trabalho, e trabalho não se
   * esconde no rodapé por causa do representante.
   */
  const grupos = useMemo(
    () =>
      agruparPorRajada(eventos, {
        cameraId: (ev) => ev.camera_id ?? '',
        classe: (ev) => ev.violations?.[0]?.class ?? '',
        criadoEm: (ev) => ev.timestamp ?? ev.created_at,
      }).sort(
        (a, b) =>
          Number(a.repeticoes.every(julgadoPorHumano)) -
          Number(b.repeticoes.every(julgadoPorHumano)),
      ),
    [eventos],
  )

  /**
   * A ordem em que as linhas APARECEM — representante e, se a rajada estiver
   * expandida, as repetições reveladas. É por ela que a gaveta anda.
   *
   * Andar pela página crua (`eventos`) levaria a evento recolhido dentro de
   * uma rajada: a gaveta trocaria de frame e NENHUMA linha se acenderia atrás
   * dela. Quem opera perderia a referência de onde está — que é justamente o
   * que esta rodada veio consertar.
   */
  const ordemVisivel = useMemo(() => {
    const linhas: Evento[] = []
    for (const g of grupos) {
      linhas.push(g.representante)
      if (expandidos.has(g.representante.id)) {
        for (const rep of g.repeticoes) if (rep.id !== g.representante.id) linhas.push(rep)
      }
    }
    return linhas
  }, [grupos, expandidos])

  const indiceAberto = abertoId ? ordemVisivel.findIndex((ev) => ev.id === abertoId) : -1
  const eventoAberto = indiceAberto >= 0 ? ordemVisivel[indiceAberto] : null

  /** Anda na ordem visível; no fim da página FICA onde está (não fecha nem
   *  pula para outra página — a paginação é escolha explícita de quem opera). */
  const irParaVisivel = (i: number) => {
    const alvo = ordemVisivel[i]
    if (alvo) setAbertoId(alvo.id)
  }

  /**
   * Trabalho que sobra NESTA PÁGINA. O eixo vai no rótulo, e não é
   * preciosismo: `total`/`total_situacoes` falam do filtro INTEIRO (todas as
   * páginas) e este número fala das 20 linhas carregadas — misturar os dois
   * escopos num único badge é o defeito que o cabeçalho deste arquivo
   * documenta. É ele que cai a cada veredito, sem recarregar a tela.
   */
  const semVeredito = eventos.filter(
    (ev) => vereditoHumano(ev.verification_verdict, ev.verified_by) === 'nao-revisado',
  ).length

  if (!podeLer) {
    return (
      <div className={s.painelCentral}>
        <AlertTriangle size={36} strokeWidth={1.5} aria-hidden="true" />
        <span className={s.painelTitulo}>Sem permissão</span>
        <span className={s.painelTexto}>
          Ver eventos exige permissão para consultar eventos. Peça a quem administra
          o seu acesso.
        </span>
      </div>
    )
  }

  /**
   * UMA linha de evento — usada tanto para o representante de uma rajada
   * quanto para cada repetição revelada ao expandir (ux2/dedup). `atenuada`
   * é a ÚNICA diferença visual entre as duas: mesma estrutura, mesmas ações
   * (Reconhecer/Julgar/Abrir), porque uma repetição é um alerta de verdade
   * — só não é uma SITUAÇÃO nova.
   */
  const renderLinha = (ev: Evento, atenuada = false) => {
    const polaridade = polaridadeDoEvento(ev)
    const IconePol = polaridade ? ICONE_POLARIDADE[polaridade] : null
    const veredito = vereditoHumano(ev.verification_verdict, ev.verified_by)
    const IconeVer = ICONE_VEREDITO[veredito]
    const capturadoEm = ev.timestamp ?? ev.created_at
    // PROCEDÊNCIA (issue #670): origem DECLARADA em `violations[].origem`
    // manda; o atraso entre captura e gravação é fallback. Sem esta ordem a
    // lista ficava muda em 4.609 dos 5.174 eventos do DEV — todos com caixa
    // desenhada por PESSOA e `created_at == timestamp`, que o critério
    // temporal nunca acende. Indício não vence declaração.
    const procedencia = procedenciaDeclarada(ev.violations)
    const retroativo = !procedencia
      && classificarLatencia(ev.timestamp, ev.created_at) === 'retroativa'
    const marcado = selecionados.includes(ev.id)
    const realcada = ev.id === destaque
    const aberta = ev.id === abertoId
    return (
      <tr
        key={ev.id}
        ref={realcada ? refDestaque : aberta ? refAberto : undefined}
        className={[
          realcada ? s.linhaDestacada : '',
          aberta ? s.linhaAberta : '',
          atenuada ? s.linhaRepeticao : '',
        ]
          .filter(Boolean)
          .join(' ') || undefined}
      >
        <td className={s.celula}>
          {podeReconhecer(ev) && (
            <input
              type="checkbox"
              className={s.caixaSelecao}
              aria-label={`Selecionar evento de ${ev.camera_name ?? 'câmera'}`}
              checked={marcado}
              onChange={() =>
                setSelecionados((sel) =>
                  marcado ? sel.filter((i) => i !== ev.id) : [...sel, ev.id],
                )
              }
            />
          )}
        </td>

        {/* POLARIDADE + classe. Cor + ícone + palavra, sempre. */}
        <td className={s.celulaEvento}>
          {polaridade && IconePol && (
            <span
              className={`${s.selo} ${s.corPolaridade[polaridade]}`}
              title={EXPLICACAO_POLARIDADE[polaridade]}
            >
              <IconePol size={13} strokeWidth={1.7} aria-hidden="true" />
              {ROTULO_POLARIDADE[polaridade]}
            </span>
          )}
          <span className={s.nomeClasse}>
            {ev.violations?.length
              ? ev.violations.map((v) => classLabel(v.class)).join(', ')
              : '—'}
          </span>
        </td>

        <td className={s.celula}>{ev.camera_name || '—'}</td>

        <td className={s.celulaMono}>
          {new Date(capturadoEm).toLocaleString('pt-BR')}
          {/* PROCEDÊNCIA: só a afirmação negativa. Sem badge =
              sem afirmação — não existe carimbo de "ao vivo"
              enquanto o shadow roda sobre frames já coletados. */}
          {procedencia && (
            <>
              {' '}
              <span
                className={s.seloProcedencia[procedencia.origem]}
                title={procedencia.titulo}
                data-testid="procedencia"
              >
                {procedencia.rotulo}
              </span>
            </>
          )}
          {retroativo && (
            <>
              {' '}
              <span
                className={s.seloRetroativo}
                title={`Capturado: ${ev.timestamp} · gravado: ${ev.created_at}`}
              >
                coleta retroativa
              </span>
            </>
          )}
        </td>

        <td className={s.celula}>
          {(() => {
            const st = statusDoFluxo(ev)
            const IconeSt = ICONE_STATUS[st]
            return (
              <span
                className={`${s.selo} ${s.corStatus[st]}`}
                title={EXPLICACAO_STATUS[st]}
              >
                <IconeSt size={13} strokeWidth={1.7} aria-hidden="true" />
                {ROTULO_STATUS[st]}
              </span>
            )
          })()}
        </td>

        {/* VEREDITO — coluna e paleta próprias. O SELO aparece
            sempre, inclusive em "Não revisado": ausência de
            veredito é um estado, não um espaço em branco. */}
        <td className={s.celula}>
          <span
            className={`${s.selo} ${s.corVeredito[veredito]}`}
            title={EXPLICACAO_VEREDITO[veredito]}
          >
            <IconeVer size={13} strokeWidth={1.7} aria-hidden="true" />
            {ROTULO_VEREDITO[veredito]}
          </span>
          {/* MOTIVO do veredito: o que separa "estava de máscara" de "a
              caixa pegou a luva do outro".

              ⛔ SÓ de gente, e o gate é `veredito !== 'nao-revisado'` (que já
              exige `verified_by` com prefixo `user:`). `verification_reason`
              é coluna COMPARTILHADA: a task Celery de triagem escreve nela o
              que aconteceu com ELA, e em falha de infraestrutura isso era
              texto de erro. Era daí que saía "API key não configurada"
              impressa embaixo do selo, em centenas de linhas reais da RVB —
              erro de infraestrutura servido como se fosse a justificativa de
              alguém (reportado pelo dono em 09/09/2026).

              A raiz foi fechada no backend (`tasks/verification.py` grava
              NULL em falha), mas as linhas JÁ gravadas continuam no banco: é
              este gate que as tira da tela sem migration de UPDATE. E ele
              vale para a FAMÍLIA inteira ("Erro IA: …", "Erro ao parsear
              resposta IA"), não só para a frase que o dono viu — o campo diz
              "justificativa que a PESSOA deu ao julgar", e só isso passa.

              A ficha da fila de verificação (`Verificacao.tsx`) mostra o
              mesmo campo sob o rótulo "Motivo da IA" — lá está NOMEADO como
              da máquina, e por isso não é mentira; fica como está. */}
          {veredito !== 'nao-revisado' && ev.verification_reason && (
            <span className={s.motivo} title={labelForVerificationReason(ev.verification_reason)}>
              {labelForVerificationReason(ev.verification_reason)}
            </span>
          )}
          {/* JULGAR NÃO ACONTECE NA LINHA — nenhum dos dois sentidos.

              HISTÓRICO, porque a regra andou em UMA direção e não convém
              desandar: antes havia aqui DOIS botões, "Procedente" e "Falso
              positivo", ambos mandando veredito com um clique seco. O de
              REJEITAR saiu primeiro (assimetria deliberada): rejeitar é dizer
              "a máquina errou", vira dado de treino, e sem motivo estruturado
              não se consegue reler depois ("errou por quê?"). Confirmar
              ficou, com o argumento de que concordar com o detector não
              acrescenta afirmação nova ao acervo.

              O argumento não se sustentou no uso real. Decisão do dono
              (09/09/2026, com a tela em operação e centenas de eventos por
              dia): "procede e não procede tem que sair dali, só deve aparecer
              de quem evidencia a foto". CONFIRMAR TAMBÉM É AFIRMAÇÃO —
              `approve` carimba `verified_by='user:<id>'` e entra no acervo
              como julgamento de gente, exatamente com o mesmo peso do outro.
              E a linha não mostra o frame: mostra câmera, classe e horário.
              Concordar sem ver a foto é afirmar sobre o que não se olhou —
              ainda mais num acervo em que 15% dos alertas são cena SEM
              NINGUÉM, coisa que só o frame revela.

              A assimetria virou simetria: os DOIS vereditos passam pela
              evidência. O caminho continua a um clique — "Ver evidência"
              abre a gaveta na própria lista (com a foto, a lupa e os dois
              botões), "Abrir →" leva à tela inteira. Sumiu o CONTROLE, não o
              caminho: nenhum veredito ficou inalcançável, só deixou de ser
              dado às cegas.

              ⛔ Não devolva botão de veredito a esta célula "para agilizar".
              Se agilizar for o pedido, o lugar é a gaveta (que já avança
              sozinha para o próximo depois de cada decisão). */}
        </td>

        {/* Confiança da detecção (§9 paridade) — o dado já vinha,
            só não era desenhado. Primeira violação, como no legado.
            Contrato A1c: o número cru não prevê acerto (medido no
            DEV, ~58-65% plano em toda faixa) — leitura honesta para
            quem não é superadmin, ver confidenceDisplay.ts. */}
        <td className={s.celula}>
          <span className={s.confianca}>
            {confiancaInternaOuCliente(ev.violations?.[0]?.confidence, isSuperAdmin)}
          </span>
        </td>

        <td className={s.celulaAcoes}>
          {/* "Reconhecer" é dar CIÊNCIA. Quem julgou já fez mais do que isso —
              abriu a evidência, olhou o frame e decidiu. Oferecer ciência
              depois do veredito é trabalho que não muda nada na tela (o
              STATUS já diz "Avaliado") e convida ao clique inútil. */}
          {podeReconhecer(ev) && (
            <button
              className={s.botao}
              disabled={ocupado === ev.id}
              onClick={() => void reconhecer(ev.id)}
            >
              Reconhecer
            </button>
          )}
          {/* O caminho curto: frame inteiro + lupa + veredito SEM sair daqui.
              "Abrir →" continua ao lado porque a tela inteira faz o que a
              gaveta não faz (corrigir a marcação, ver o histórico). */}
          <button
            className={s.botao}
            aria-label={`Ver evidência do evento de ${ev.camera_name ?? 'câmera'}`}
            onClick={() => setAbertoId(ev.id)}
          >
            <Eye size={15} strokeWidth={1.7} aria-hidden="true" />
            Ver evidência
          </button>
          <NavLink className={s.botao} to={rotaNova(`/epi/eventos/${ev.id}`)}>
            Abrir →
          </NavLink>
        </td>
      </tr>
    )
  }

  return (
    <div className={s.pagina}>
      <div className={s.cabecalho}>
        <h1 className={s.titulo}>Eventos</h1>
        {dados && (
          <span className={s.meta}>
            {/* ux2/dedup: badge conta SITUAÇÕES (rajadas), não linhas — "1/66
                reconhecidas" media repetição, não trabalho. `total_situacoes`
                vem do filtro INTEIRO (todas as páginas), não só a carregada.
                O eixo vai no rótulo: o backend agrupa por hora de GRAVAÇÃO e
                as linhas abaixo agrupam por hora de CAPTURA — dois números
                da mesma regra que não fecham (ver cabeçalho do arquivo). */}
            {dados.total_situacoes != null && dados.total_situacoes !== dados.total
              ? `${dados.total_situacoes} SITUAÇÕES (POR HORA DE GRAVAÇÃO) · ${dados.total} EVENTOS`
              : `${dados.total} NO PERÍODO`}
          </span>
        )}
        <span className={s.espacador} />

        <select
          className={s.filtro}
          aria-label="Período"
          value={filtros.intervalo ? '' : filtros.periodo}
          onChange={(e) => {
            // '' é só o rótulo do intervalo que veio no link; não é período.
            if (!e.target.value) return
            // Escolher um período descarta o intervalo cru do deep-link.
            trocarFiltro({ periodo: e.target.value as Periodo, intervalo: null })
          }}
        >
          {filtros.intervalo && <option value="">Período do link</option>}
          <option value="hoje">Hoje</option>
          <option value="7d">7 dias</option>
          <option value="30d">30 dias</option>
        </select>

        {cameras.length > 0 && (
          <select
            className={s.filtro}
            aria-label="Câmera"
            value={filtros.cameraId}
            onChange={(e) => trocarFiltro({ cameraId: e.target.value })}
          >
            <option value="">Todas as câmeras</option>
            {cameras.map((c) => (
              <option key={c.id} value={c.id}>
                {c.location ? `${c.name} · ${c.location}` : c.name}
              </option>
            ))}
          </select>
        )}

        {classes.length > 0 && (
          <select
            className={s.filtro}
            aria-label="Classe"
            value={filtros.classe}
            onChange={(e) => trocarFiltro({ classe: e.target.value })}
          >
            <option value="">Todas as classes</option>
            {classes.map((c) => (
              <option key={c.class_name} value={c.class_name}>
                {classLabel(c.class_name)}
              </option>
            ))}
          </select>
        )}

        <select
          className={s.filtro}
          aria-label="Status"
          value={filtros.status}
          onChange={(e) => trocarFiltro({ status: e.target.value })}
        >
          <option value="">Todos os status</option>
          <option value="false">Novo</option>
          <option value="true">Reconhecido</option>
        </select>

        {/* Fora do desenho, preservado da tela antiga (ADR-0065): sem isto a
            lista abriria misturando telemetria de EPI em uso com violação.
            Contrato A1: 'observacao' é o TERCEIRO balde do backend — classe
            que ninguém classificou ainda (nem presença, nem violação). Sem
            esta opção, quem quisesse ACHAR essas detecções só teria
            "Todos os tipos" (misturado com violação/conformidade de
            verdade) — o filtro dedicado é o que torna o indecidido
            achável. */}
        <select
          className={s.filtro}
          aria-label="Tipo de evento"
          value={filtros.kind}
          onChange={(e) => trocarFiltro({ kind: e.target.value })}
        >
          <option value="violation">Violações</option>
          <option value="compliance">Conformidade (EPI em uso)</option>
          <option value="observacao">Não definida (ninguém classificou)</option>
          <option value="">Todos os tipos</option>
        </select>

        {podeExportar && (
          <button className={s.botao} onClick={() => void exportar()} disabled={exportando}>
            <Download size={15} strokeWidth={1.7} aria-hidden="true" />
            {exportando ? 'Exportando…' : 'Exportar CSV'}
          </button>
        )}
      </div>

      {selecionados.length > 0 && (
        <div className={s.barraSelecao}>
          <span className={s.contagemSelecao}>{selecionados.length} selecionados</span>
          <span className={s.espacador} />
          <button
            className={s.botaoPrimario}
            onClick={() => void reconhecerSelecionados()}
            disabled={ocupado === 'lote'}
          >
            Reconhecer selecionados
          </button>
          <button className={s.botao} onClick={() => setSelecionados([])}>
            Limpar
          </button>
        </div>
      )}

      {carregando ? (
        <LogikosLoader estado="waiting" variante="fullscreen" rotulo="CARREGANDO EVENTOS" />
      ) : erro ? (
        <div className={s.painelCentral} role="alert">
          <AlertTriangle size={36} strokeWidth={1.5} aria-hidden="true" />
          <span className={s.painelTitulo}>Não foi possível carregar</span>
          <span className={s.painelDetalhe}>{erro}</span>
          <button className={s.botaoPainel} onClick={() => void carregar()}>
            <RefreshCw size={16} strokeWidth={1.7} aria-hidden="true" />
            Tentar novamente
          </button>
        </div>
      ) : eventos.length === 0 ? (
        <div className={s.painelCentral}>
          <Inbox size={36} strokeWidth={1.5} aria-hidden="true" />
          <span className={s.painelTitulo}>Nenhum evento no período</span>
          <span className={s.painelTexto}>
            {recorteJaAmplo
              ? 'Nenhum evento em 30 dias, com todos os tipos e câmeras. O vazio é do acervo, não do filtro.'
              : 'Nenhuma detecção com os filtros atuais — e a tela abre mostrando só violações. Eventos de conformidade e de outros dias ficam de fora deste recorte.'}
          </span>
          {!recorteJaAmplo && (
            <button className={s.botaoPainel} onClick={ampliarBusca}>
              Ver 30 dias, todos os tipos
            </button>
          )}
        </div>
      ) : (
        <>
          <div className={s.cartao}>
            <table className={s.tabela}>
              <thead>
                <tr>
                  <th scope="col" className={s.cabecalhoCelula}>
                    <input
                      type="checkbox"
                      className={s.caixaSelecao}
                      aria-label="Selecionar todos os eventos novos"
                      checked={
                        selecionaveis.length > 0 && selecionados.length === selecionaveis.length
                      }
                      onChange={(e) => setSelecionados(e.target.checked ? selecionaveis : [])}
                    />
                  </th>
                  <th scope="col" className={s.cabecalhoCelula}>Evento</th>
                  <th scope="col" className={s.cabecalhoCelula}>Câmera</th>
                  <th scope="col" className={s.cabecalhoCelula}>Hora</th>
                  <th scope="col" className={s.cabecalhoCelula}>Status</th>
                  <th scope="col" className={s.cabecalhoCelula}>Veredito humano</th>
                  <th scope="col" className={s.cabecalhoCelula}>Confiança</th>
                  <th scope="col" className={s.cabecalhoCelula} />
                </tr>
              </thead>
              <tbody>
                {grupos.map((grupo) => {
                  const ev = grupo.representante
                  const expandido = expandidos.has(ev.id)
                  return (
                    <Fragment key={ev.id}>
                      {renderLinha(ev)}
                      {/* ux2/dedup: rajada (câmera+classe repetida em <60s) —
                          nunca esconde, só recolhe. Expandir revela as N
                          linhas originais, cada uma com as MESMAS ações. */}
                      {grupo.tamanho > 1 && (
                        <tr>
                          <td
                            colSpan={8}
                            className={s.linhaRajadaToggle}
                            role="button"
                            tabIndex={0}
                            onClick={() => alternarExpandido(ev.id)}
                            onKeyDown={(e) => {
                              if (e.key === 'Enter' || e.key === ' ') {
                                e.preventDefault()
                                alternarExpandido(ev.id)
                              }
                            }}
                          >
                            {expandido
                              ? <ChevronDown size={12} strokeWidth={1.7} aria-hidden="true" />
                              : <ChevronRight size={12} strokeWidth={1.7} aria-hidden="true" />}
                            {' '}+{grupo.tamanho - 1} repetiç{grupo.tamanho - 1 === 1 ? 'ão' : 'ões'} da mesma câmera+classe em &lt;60s de captura
                          </td>
                        </tr>
                      )}
                      {expandido &&
                        grupo.repeticoes
                          .filter((rep) => rep.id !== ev.id)
                          .map((rep) => <Fragment key={rep.id}>{renderLinha(rep, true)}</Fragment>)}
                    </Fragment>
                  )
                })}
              </tbody>
            </table>
          </div>

          <div className={s.rodape}>
            <span>
              {dados?.total_situacoes != null && dados.total_situacoes !== dados.total
                ? `${dados.total_situacoes} SITUAÇÕES (POR HORA DE GRAVAÇÃO) · ${dados.total} EVENTOS`
                : `${dados?.total ?? 0} EVENTOS`}
            </span>
            {/* Escopo DIFERENTE do número ao lado, e por isso separado e
                nomeado: aquele fala do filtro inteiro, este das linhas
                carregadas. É o que anda a cada veredito dado na gaveta. */}
            <span className={s.overlineLegenda}>
              {semVeredito} SEM VEREDITO NESTA PÁGINA
            </span>
            <span className={s.espacador} />
            <button
              className={s.botao}
              aria-label="Página anterior"
              disabled={filtros.pagina <= 1}
              onClick={() => setFiltros((f) => ({ ...f, pagina: f.pagina - 1 }))}
            >
              <ChevronLeft size={16} strokeWidth={1.7} aria-hidden="true" />
            </button>
            <span className={s.overlineLegenda}>
              {dados?.page ?? 1} / {dados?.pages ?? 1}
            </span>
            <button
              className={s.botao}
              aria-label="Próxima página"
              disabled={!dados || filtros.pagina >= dados.pages}
              onClick={() => setFiltros((f) => ({ ...f, pagina: f.pagina + 1 }))}
            >
              <ChevronRight size={16} strokeWidth={1.7} aria-hidden="true" />
            </button>
          </div>

          <span className={s.nota}>
            Reconhecer é sempre um clique explícito — nunca hover. Do evento dá para agir
            em ≤2 cliques: Abrir → Confirmar / Criar ação.
          </span>
        </>
      )}

      {/* Só renderiza quando o evento aberto AINDA existe na página relida —
          gaveta sobre um evento que sumiu do recorte mostraria dado de um
          estado que não é mais o da lista atrás dela. */}
      {eventoAberto && (
        <PainelEvidencia
          key={eventoAberto.id}
          evento={eventoAberto}
          posicao={indiceAberto + 1}
          totalVisivel={ordemVisivel.length}
          aoAnterior={() => irParaVisivel(indiceAberto - 1)}
          aoProximo={() => irParaVisivel(indiceAberto + 1)}
          aoFechar={() => setAbertoId(null)}
          aoJulgar={julgar}
        />
      )}
    </div>
  )
}
