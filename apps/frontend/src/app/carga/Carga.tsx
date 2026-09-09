/**
 * Carga — o módulo de contagem de carregamento. Desenho: `Carga.dc.html`
 * (abas Dashboard, Baias, Eventos, Validação).
 *
 * ─── O QUE ESTA TELA NÃO MOSTRA, E POR QUÊ ──────────────────────────────────
 *
 * O desenho promete um módulo inteiro; o servidor serve um pedaço dele. Tudo
 * abaixo foi MEDIDO no backend, não suposto — e cada item que não tem produtor
 * ficou de fora da tela, com a lacuna dita em voz alta em vez de preenchida
 * com número bonito.
 *
 *  1. **Ninguém grava contagem.** `CountingService.record_detection()` só tem
 *     chamador em `tests/` — nada no edge, na inferência ou no worker escreve
 *     `counting_events`. Logo `total_counts` fica `{}`. Onde o desenho mostra
 *     "86 itens contados", a tela diz **"sem contagem registrada"**: exibir
 *     `0` seria afirmar que a câmera contou zero, quando ninguém contou nada.
 *  2. **Não existe tabela de baias.** `bay_id` é um UUID solto em
 *     `counting_sessions`, sem FK e sem tabela (migrations 050 e 080; nenhum
 *     `CREATE TABLE ... bays` no repo). "BAIA-01" não tem de onde sair — e
 *     UUID cru na tela é proibido. As sessões em andamento aparecem
 *     identificadas pela **câmera**, que é o único rótulo legível que existe.
 *  3. **`/api/fueling/bays` e `/api/fueling/dashboard` não entram aqui.** Com a
 *     flag `fueling_use_mock` ligada devolvem dados SORTEADOS (e só para
 *     superadmin; cliente recebe `{no_data:true}`); com a flag desligada
 *     chamam `svc.get_loading_dashboard()` / `svc.list_loading_bays()`, que
 *     NÃO EXISTEM no `CountingService` — `AttributeError` → 500 engolido pelo
 *     `except`. Dado sorteado é dado inventado, e o caminho real nunca
 *     funcionou: os dois ficam fora.
 *  4. **Não há fila de validação.** `DELETE /counting/sessions/<id>` encerra e
 *     não seta `acceptance_status='pending'`; `GET /counting/sessions` filtra
 *     `status='running'`; o relatório filtra `manual_count IS NOT NULL`. Uma
 *     sessão encerrada e ainda sem conferência manual não cai em NENHUM dos
 *     dois — não existe caminho de UI para a primeira conferência. O contador
 *     "N PENDENTES" do desenho seria um número que ninguém sabe calcular.
 *  5. **Aceitar sistema ≠ aceitar manual, para o desenho; para o servidor é a
 *     mesma coisa.** `PATCH /sessions/<id>` grava `acceptance_status`
 *     ('pending'|'accepted'|'rejected') e nada mais: não existe coluna que
 *     diga QUAL contagem virou a oficial, nem `accepted_by`/`accepted_at`. Os
 *     dois botões ficam no lugar do desenho, **desabilitados e dizendo por
 *     quê** — mesmo tratamento de "Pausar" em Operações.
 *  6. **Não há agregação por hora nem por baia.** A única série do módulo é
 *     DIÁRIA (`daily` do relatório, `GROUP BY DATE(started_at)`); `bay_id` é
 *     filtro, nunca group-by. O gráfico mostra o dia — com o rótulo do dia.
 *  7. **Sem filtro de site.** `counting_sessions` não tem `site_id`, e
 *     `/api/v1/edge/sites` exige admin. O seletor fica desabilitado.
 *  8. **Duas placas concorrentes.** `truck_plate` (PATCH da sessão) e
 *     `plate_text` (LPR). O relatório de validação lê `truck_plate` — placa
 *     lida por OCR não aparece nele. Nos cartões usamos a que houver, dizendo
 *     qual é.
 *
 * A rota do estado de erro do desenho (`GET /api/carga/sessoes`) não existe em
 * lugar nenhum do backend. A real é `GET /api/counting/sessions`, e é ela que
 * a tela mostra quando falha.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  AlertTriangle, ArrowLeft, CheckCircle2, ClipboardCheck, LayoutGrid, List, Lock, Warehouse,
} from 'lucide-react'
import { Link } from 'react-router-dom'

import { useAuth } from '../../hooks/useAuth'
import { api } from '../../services/api'
import { confiancaInternaOuCliente } from '../../services/confidenceDisplay'
import { rotaHomeDoUsuario } from '../RotasNovas'
import { LogikosLoader } from '../shell/LogikosLoader'
import { lk } from '../tokens/lk.css'
import * as s from './Carga.css'
import { isInTenantContext } from '../../services/tenantContext'

// ── Rotas reais ─────────────────────────────────────────────────────────────

const ROTA_SESSOES = '/counting/sessions'
const ROTA_RELATORIO = '/counting/sessions/validation-report'
const ROTA_EVENTOS = '/fueling/events'
const ROTA_CAMERAS = '/cameras'

// ── Formas reais das respostas (envelope {success,message,data}) ─────────────

/** `counting_sessions` + `camera_name` do LEFT JOIN (counting_repository:39). */
interface Sessao {
  id: string
  camera_id: string
  camera_name?: string | null
  status?: string | null
  total_counts?: Record<string, number> | null
  started_at?: string | null
  truck_plate?: string | null
  plate_text?: string | null
  direction?: string | null
  manual_count?: number | null
  acceptance_status?: string | null
}

/** Linha de `sessions[]` do validation-report. Sem `camera_name` — só o UUID. */
interface SessaoConferida {
  id: string
  camera_id: string
  truck_plate?: string | null
  direction?: string | null
  started_at?: string | null
  ended_at?: string | null
  acceptance_status?: string | null
  manual_count: number
  system_count: number
  abs_error: number
  error_pct: number | null
  passed: boolean
}

interface Dia {
  day: string
  sessions: number
  system_total: number
  manual_total: number
  abs_error: number
  error_pct: number | null
  passed: boolean
}

interface Relatorio {
  threshold_pct: number
  sessions: SessaoConferida[]
  daily: Dia[]
  summary: {
    sessions_validated: number
    system_count: number
    manual_count: number
    abs_error: number
    error_pct: number | null
    passed: boolean
  }
}

/** `/fueling/events` lê `public.alerts` — detecção por classe, nada de sessão. */
interface EventoCarga {
  id: string
  camera_id: string
  class_name?: string | null
  confidence?: number | null
  created_at?: string | null
}

type Envelope<T> = { data?: T | null }

// ── Ajudantes ───────────────────────────────────────────────────────────────

const mensagemDe = (e: unknown) => (e instanceof Error ? e.message : 'falha desconhecida')

const iso = (d: Date) => d.toISOString().slice(0, 10)

function horaDe(quando?: string | null): string {
  if (!quando) return '—'
  const d = new Date(quando)
  return Number.isNaN(d.getTime())
    ? '—'
    : d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })
}

function dataHoraDe(quando?: string | null): string {
  if (!quando) return '—'
  const d = new Date(quando)
  return Number.isNaN(d.getTime())
    ? '—'
    : d.toLocaleString('pt-BR', {
        day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
      })
}

/** `day` vem como `YYYY-MM-DD`; `new Date()` nele desloca o dia em fuso oeste. */
function diaCurto(day: string): string {
  const [, mes, dia] = day.slice(0, 10).split('-')
  return dia && mes ? `${dia}/${mes}` : day
}

/**
 * Total contado pelo sistema. `null` quando o mapa está vazio — e ele está
 * SEMPRE vazio hoje (lacuna 1). "0" seria dizer que a contagem deu zero;
 * `null` diz que contagem nenhuma foi registrada, que é o que aconteceu.
 */
function totalContado(contagens?: Record<string, number> | null): number | null {
  const valores = Object.values(contagens ?? {})
  if (valores.length === 0) return null
  return valores.reduce((soma, v) => soma + (Number(v) || 0), 0)
}

/** Placa da sessão — dizendo de qual das duas colunas ela veio. */
function placaDe(sessao: Sessao): { texto: string; origem: string } | null {
  if (sessao.truck_plate) return { texto: sessao.truck_plate, origem: 'informada (truck_plate)' }
  if (sessao.plate_text) return { texto: sessao.plate_text, origem: 'lida por OCR (plate_text)' }
  return null
}

const DECISAO: Record<string, string> = {
  pending: 'PENDENTE',
  accepted: 'ACEITA',
  rejected: 'RECUSADA',
}

/**
 * Um recurso = uma rota, com os quatro estados que a casa exige.
 * `buscar === null` significa "esta aba não precisa deste dado agora".
 */
function useRecurso<T>(buscar: (() => Promise<T>) | null) {
  const [dados, setDados] = useState<T | null>(null)
  const [erro, setErro] = useState<string | null>(null)
  const [tentativa, setTentativa] = useState(0)

  useEffect(() => {
    if (!buscar) return
    let vivo = true
    setDados(null)
    setErro(null)
    buscar()
      .then((d) => { if (vivo) setDados(d) })
      .catch((e) => { if (vivo) setErro(mensagemDe(e)) })
    return () => { vivo = false }
  }, [buscar, tentativa])

  return {
    dados,
    erro,
    carregando: buscar !== null && dados === null && erro === null,
    recarregar: useCallback(() => setTentativa((t) => t + 1), []),
  }
}

type Aba = 'dashboard' | 'baias' | 'eventos' | 'validacao'

const ABAS: Array<{ id: Aba; rotulo: string; icone: typeof LayoutGrid }> = [
  { id: 'dashboard', rotulo: 'Dashboard', icone: LayoutGrid },
  { id: 'baias', rotulo: 'Baias', icone: Warehouse },
  { id: 'eventos', rotulo: 'Eventos', icone: List },
  { id: 'validacao', rotulo: 'Validação', icone: ClipboardCheck },
]

const TITULO: Record<Aba, string> = {
  dashboard: 'Dashboard',
  baias: 'Baias',
  eventos: 'Eventos',
  validacao: 'Validação de contagem',
}

type Periodo = 'hoje' | 'sete' | 'trinta'
const DIAS: Record<Periodo, number> = { hoje: 0, sete: 7, trinta: 30 }
const ROTULO_PERIODO: Record<Periodo, string> = {
  hoje: 'hoje',
  sete: 'últimos 7 dias',
  trinta: 'últimos 30 dias',
}

/** Estado = cor + ícone + PALAVRA. Cor sozinha não é estado. */
function Situacao({ dentro, meta }: { dentro: boolean; meta?: number }) {
  const Icone = dentro ? CheckCircle2 : AlertTriangle
  const cor = dentro ? lk.estado.ok : lk.estado.atencao
  return (
    <span className={s.selo} style={{ color: cor, borderColor: cor }}>
      <Icone size={12} strokeWidth={2.2} aria-hidden="true" />
      {dentro ? 'DENTRO DA META' : 'ACIMA DA META'}
      {meta !== undefined ? ` (${meta}%)` : ''}
    </span>
  )
}

export function Carga() {
  const { can, hasModule, isSuperAdmin } = useAuth()
  const podeLer = can('counting:read')
  const podeEscrever = can('counting:write')
  const temModuloCarga = hasModule('counting')

  const [aba, setAba] = useState<Aba>('dashboard')
  const [periodo, setPeriodo] = useState<Periodo>('sete')
  const [confirmando, setConfirmando] = useState<string | null>(null)
  const [encerrando, setEncerrando] = useState<string | null>(null)
  const [erroEncerrar, setErroEncerrar] = useState<string | null>(null)

  const consulta = useMemo(() => {
    const fim = new Date()
    const inicio = new Date(fim.getTime() - DIAS[periodo] * 864e5)
    return new URLSearchParams({ start: iso(inicio), end: iso(fim) }).toString()
  }, [periodo])

  const buscarSessoes = useCallback(
    () =>
      api
        .get<Envelope<{ sessions?: Sessao[] }>>(ROTA_SESSOES)
        .then((r) => r.data?.sessions ?? []),
    [],
  )
  const buscarRelatorio = useCallback(
    () =>
      api
        .get<Envelope<Relatorio>>(`${ROTA_RELATORIO}?${consulta}`)
        .then((r) => r.data ?? null),
    [consulta],
  )
  const buscarEventos = useCallback(
    () =>
      api
        .get<Envelope<{ events?: EventoCarga[] }>>(`${ROTA_EVENTOS}?limit=100`)
        .then((r) => r.data?.events ?? []),
    [],
  )
  /** Mapa id → nome: sem ele a tabela mostraria UUID, que é proibido. */
  const buscarCameras = useCallback(
    () =>
      api
        .get<Envelope<{ cameras?: Array<{ id: string; name?: string | null }> }>>(ROTA_CAMERAS)
        .then((r) =>
          Object.fromEntries(
            (r.data?.cameras ?? []).map((c) => [c.id, c.name ?? '']),
          ) as Record<string, string>,
        ),
    [],
  )

  const precisaSessoes = podeLer && temModuloCarga && (aba === 'dashboard' || aba === 'baias')
  const precisaRelatorio = podeLer && temModuloCarga && (aba === 'dashboard' || aba === 'validacao')
  const precisaEventos = podeLer && temModuloCarga && aba === 'eventos'
  const precisaCameras = podeLer && temModuloCarga && (aba === 'eventos' || aba === 'validacao')

  const sessoes = useRecurso(precisaSessoes ? buscarSessoes : null)
  const relatorio = useRecurso(precisaRelatorio ? buscarRelatorio : null)
  const eventos = useRecurso(precisaEventos ? buscarEventos : null)
  const cameras = useRecurso(precisaCameras ? buscarCameras : null)

  /** Nome da câmera, ou nada — nunca o UUID. */
  const nomeDaCamera = useCallback(
    (id: string) => cameras.dados?.[id]?.trim() || null,
    [cameras.dados],
  )

  const encerrar = useCallback(
    async (id: string) => {
      if (confirmando !== id) {
        setConfirmando(id)
        return
      }
      setEncerrando(id)
      setErroEncerrar(null)
      try {
        await api.delete(`${ROTA_SESSOES}/${id}`)
        setConfirmando(null)
        sessoes.recarregar()
      } catch (e) {
        setErroEncerrar(`DELETE /api${ROTA_SESSOES}/${id} · ${mensagemDe(e)}`)
      } finally {
        setEncerrando(null)
      }
    },
    [confirmando, sessoes],
  )

  // Sem lateral própria (SEM_BARRA_LATERAL): sem este link nenhum dos dois
  // ramos abaixo teria saída — mesma regra do cabeçalho do caminho feliz,
  // ver app/shell/becoSemSaida.test.tsx. `falhou`/`conteudo*` ficam de fora
  // porque só rodam DEPOIS do cabeçalho com Voltar já estar na árvore.
  const voltarCentro = (
    <Link to={rotaHomeDoUsuario(isSuperAdmin, isInTenantContext())} className={s.voltar}>
      <ArrowLeft size={16} strokeWidth={1.7} aria-hidden="true" />
      Voltar
    </Link>
  )

  if (!podeLer) {
    return (
      <div className={s.raiz}>
        <div className={s.cabecalho}>{voltarCentro}</div>
        <div className={s.centro}>
          <Lock size={36} strokeWidth={1.5} color={lk.cor.cinzaNevoa} aria-hidden="true" />
          <span className={s.centroTitulo}>Sem permissão</span>
          <span className={s.centroTexto}>
            A Carga exige a permissão <code>counting:read</code>. Peça ao administrador do
            seu tenant.
          </span>
        </div>
      </div>
    )
  }

  // Módulo desligado (nota do cético do flip): sem isto a tela chamaria as
  // rotas de qualquer jeito e tomaria 403 cru — mesmo tratamento do KPI
  // "sem fonte" de Retrabalhos em Qualidade.tsx. É o estado REAL do tenant da
  // demo (rvb só tem o módulo epi habilitado) — por isso não pode ser um
  // beco sem saída.
  if (!temModuloCarga) {
    return (
      <div className={s.raiz}>
        <div className={s.cabecalho}>{voltarCentro}</div>
        <div className={s.centro}>
          <Lock size={36} strokeWidth={1.5} color={lk.cor.cinzaNevoa} aria-hidden="true" />
          <span className={s.centroTitulo}>Módulo não habilitado</span>
          <span className={s.centroTexto}>
            O módulo Carga (<code>counting</code>) não está habilitado nesta sessão. Peça ao
            administrador do seu tenant.
          </span>
        </div>
      </div>
    )
  }

  const falhou = (rota: string, erro: string, recarregar: () => void) => (
    <div className={s.centro}>
      <AlertTriangle size={36} strokeWidth={1.5} color={lk.estado.nc} aria-hidden="true" />
      <span className={s.centroTitulo}>Não foi possível carregar</span>
      <span className={s.centroTecnico}>
        GET /api{rota} · {erro}
      </span>
      <button type="button" className={s.botaoPrimario} onClick={recarregar}>
        Tentar novamente
      </button>
    </div>
  )

  // ── Aba Dashboard ─────────────────────────────────────────────────────────

  function conteudoDashboard() {
    // "Tentar novamente" refaz a TELA, não só a rota que falhou: as duas
    // alimentam o mesmo painel, e retentar uma deixaria a outra em erro eterno.
    const retentar = () => {
      sessoes.recarregar()
      relatorio.recarregar()
    }
    if (sessoes.erro) return falhou(ROTA_SESSOES, sessoes.erro, retentar)
    if (relatorio.erro) return falhou(ROTA_RELATORIO, relatorio.erro, retentar)
    if (sessoes.carregando || relatorio.carregando) {
      return <LogikosLoader estado="waiting" variante="fullscreen" rotulo="CARREGANDO CARGA" />
    }

    const emAndamento = sessoes.dados ?? []
    const resumo = relatorio.dados?.summary
    const dias = relatorio.dados?.daily ?? []
    const meta = relatorio.dados?.threshold_pct

    if (emAndamento.length === 0 && dias.length === 0) {
      return (
        <div className={s.centro}>
          <Warehouse size={36} strokeWidth={1.5} color={lk.cor.cinzaNevoa} aria-hidden="true" />
          <span className={s.centroTitulo}>Nenhuma sessão de carga</span>
          <span className={s.centroTexto}>
            Não há sessão em andamento nem sessão conferida {ROTULO_PERIODO[periodo]}. A
            sessão nasce por chamada à API — não existe detecção de caminhão que a inicie.
          </span>
          <button type="button" className={s.botaoPrimario} onClick={() => setAba('baias')}>
            Ver sessões em andamento
          </button>
        </div>
      )
    }

    const maiorDia = Math.max(1, ...dias.map((d) => d.sessions))

    return (
      <>
        <div className={s.kpis}>
          <div className={s.kpi}>
            <span className={s.overline}>SESSÕES EM ANDAMENTO</span>
            <span className={s.kpiValor}>{emAndamento.length}</span>
            <span className={s.kpiSub}>o servidor lista só as em andamento</span>
          </div>
          <div className={s.kpi}>
            <span className={s.overline}>SESSÕES CONFERIDAS</span>
            <span className={s.kpiValor}>{resumo?.sessions_validated ?? 0}</span>
            <span className={s.kpiSub}>com contagem manual, {ROTULO_PERIODO[periodo]}</span>
          </div>
          <div className={s.kpi}>
            <span className={s.overline}>DIFERENÇA ABSOLUTA</span>
            <span className={s.kpiValor}>{resumo?.abs_error ?? 0}</span>
            <span className={s.kpiSub}>sistema vs romaneio, somada</span>
          </div>
          <div className={s.kpi}>
            <span className={s.overline}>ERRO</span>
            <span
              className={s.kpiValor}
              style={{
                color: resumo?.error_pct == null
                  ? lk.cor.cinzaNevoa
                  : resumo.passed
                    ? lk.estado.ok
                    : lk.estado.atencao,
              }}
            >
              {resumo?.error_pct == null ? '—' : `${resumo.error_pct}%`}
            </span>
            {resumo && resumo.error_pct != null ? (
              <Situacao dentro={resumo.passed} meta={meta} />
            ) : (
              <span className={s.kpiSub}>sem conferência manual no período</span>
            )}
          </div>
        </div>

        <div className={s.doisPaineis}>
          <div className={s.painel}>
            <span className={s.painelTitulo}>Sessões conferidas por dia</span>
            {dias.length === 0 ? (
              <span className={s.nota}>
                Nenhuma sessão conferida {ROTULO_PERIODO[periodo]}.
              </span>
            ) : (
              <div className={s.barras}>
                {dias.map((d) => (
                  <div key={d.day} className={s.colunaBarra}>
                    <div
                      className={s.barra}
                      style={{ height: `${Math.round((d.sessions / maiorDia) * 100)}%` }}
                      title={`${d.sessions} sessão(ões) conferida(s)`}
                    />
                    <span className={s.barraRotulo}>{diaCurto(d.day)}</span>
                  </div>
                ))}
              </div>
            )}
            <span className={s.nota}>
              A série é DIÁRIA porque é a única que existe: o backend agrupa por
              <code> DATE(started_at)</code>. Não há agregação por hora em lugar nenhum —
              o &quot;sessões por hora&quot; do desenho não tem fonte.
            </span>
          </div>

          <div className={s.painel}>
            <span className={s.painelTitulo}>Diferença por dia</span>
            {dias.map((d) => (
              <div key={d.day} className={s.linhaDia}>
                <span className={s.linhaDiaRotulo}>{diaCurto(d.day)}</span>
                <div className={s.trilho}>
                  <div
                    className={s.trilhoCheio}
                    style={{
                      width: `${Math.min(100, d.error_pct ?? 0)}%`,
                      background: d.passed ? lk.estado.ok : lk.estado.atencao,
                    }}
                  />
                </div>
                <span className={s.linhaDiaValor}>
                  {d.error_pct == null ? '—' : `${d.error_pct}%`}
                </span>
              </div>
            ))}
            <span className={s.nota}>
              Por DIA, não por baia: <code>bay_id</code> é filtro do relatório, nunca
              group-by — e não existe cadastro de baias para listar.
            </span>
          </div>
        </div>

        <div className={s.faixaFalta}>
          <AlertTriangle
            size={14}
            strokeWidth={2}
            color={lk.estado.atencao}
            style={{ flex: 'none', marginTop: '2px' }}
            aria-hidden="true"
          />
          <span>
            <strong>Três números do desenho não têm produtor.</strong> &quot;Itens
            contados&quot; depende de <code>counting_events</code>, e nada no edge, no
            reconhecimento ou no worker escreve nessa tabela. &quot;Divergências aguardando
            validação&quot; e &quot;baias ativas&quot; dependem, respectivamente, de uma
            fila de pendentes e de um cadastro de baias — nenhum dos dois existe. Estão
            registrados como pedido ao backend.
          </span>
        </div>
      </>
    )
  }

  // ── Aba Baias ─────────────────────────────────────────────────────────────

  function conteudoBaias() {
    if (sessoes.erro) return falhou(ROTA_SESSOES, sessoes.erro, sessoes.recarregar)
    if (sessoes.carregando) {
      return <LogikosLoader estado="waiting" variante="fullscreen" rotulo="CARREGANDO SESSÕES" />
    }
    const lista = sessoes.dados ?? []

    if (lista.length === 0) {
      return (
        <div className={s.centro}>
          <Warehouse size={36} strokeWidth={1.5} color={lk.cor.cinzaNevoa} aria-hidden="true" />
          <span className={s.centroTitulo}>Nenhuma sessão em andamento</span>
          <span className={s.centroTexto}>
            O desenho diz que a sessão aparece quando um caminhão entra na baia. Hoje ela
            só nasce por chamada à API: não há gatilho automático de detecção de caminhão
            em lugar nenhum do sistema.
          </span>
        </div>
      )
    }

    return (
      <>
        <div className={s.faixaFalta}>
          <AlertTriangle
            size={14}
            strokeWidth={2}
            color={lk.estado.atencao}
            style={{ flex: 'none', marginTop: '2px' }}
            aria-hidden="true"
          />
          <span>
            <strong>Cada cartão é uma sessão, identificada pela câmera.</strong> Não
            existe cadastro de baias: <code>bay_id</code> é um UUID sem tabela, sem nome e
            sem vínculo com câmera — então não há &quot;BAIA-01&quot; para mostrar, e UUID
            cru não vai para a tela.
          </span>
        </div>
        {erroEncerrar && <span className={s.centroTecnico}>{erroEncerrar}</span>}
        <div className={s.cartoes}>
          {lista.map((sessao) => {
            const contado = totalContado(sessao.total_counts)
            const placa = placaDe(sessao)
            const nome = sessao.camera_name?.trim()
            return (
              <div key={sessao.id} className={s.cartao}>
                <div className={s.cartaoTopo}>
                  <span className={s.cartaoNome}>
                    {nome || 'CÂMERA NÃO IDENTIFICADA'}
                  </span>
                  <span className={s.espacador} />
                  <span className={s.selo} style={{ color: lk.estado.ok, borderColor: lk.estado.ok }}>
                    <CheckCircle2 size={12} strokeWidth={2.2} aria-hidden="true" />
                    EM ANDAMENTO
                  </span>
                </div>

                <div className={s.contagem}>
                  {contado === null ? (
                    <span className={s.nota}>
                      Sem contagem registrada — nenhum processo grava em{' '}
                      <code>counting_events</code>.
                    </span>
                  ) : (
                    <>
                      <span className={s.contagemValor}>{contado}</span>
                      <span className={s.kpiSub}>itens contados</span>
                    </>
                  )}
                </div>

                <div className={s.cartaoRodape}>
                  <span className={s.kpiSub}>início {dataHoraDe(sessao.started_at)}</span>
                  {placa && (
                    <span className={s.cartaoNome} title={`Placa ${placa.origem}`}>
                      PLACA {placa.texto}
                    </span>
                  )}
                  <span className={s.espacador} />
                  <button
                    type="button"
                    className={s.botaoSecundario}
                    disabled={!podeEscrever || encerrando === sessao.id}
                    title={
                      podeEscrever
                        ? 'Encerra a sessão. Ela sai desta lista e, sem conferência manual, não aparece em nenhuma outra — não existe fila de validação.'
                        : 'Encerrar exige a permissão counting:write.'
                    }
                    onClick={() => void encerrar(sessao.id)}
                  >
                    {encerrando === sessao.id
                      ? 'Encerrando…'
                      : confirmando === sessao.id
                        ? 'Confirmar encerramento'
                        : 'Encerrar sessão'}
                  </button>
                </div>
              </div>
            )
          })}
        </div>
      </>
    )
  }

  // ── Aba Eventos ───────────────────────────────────────────────────────────

  function conteudoEventos() {
    if (eventos.erro) return falhou(ROTA_EVENTOS, eventos.erro, eventos.recarregar)
    if (eventos.carregando) {
      return <LogikosLoader estado="waiting" variante="fullscreen" rotulo="CARREGANDO EVENTOS" />
    }
    const lista = eventos.dados ?? []

    if (lista.length === 0) {
      return (
        <div className={s.centro}>
          <List size={36} strokeWidth={1.5} color={lk.cor.cinzaNevoa} aria-hidden="true" />
          <span className={s.centroTitulo}>Nenhum evento de carga</span>
          <span className={s.centroTexto}>
            Esta lista vem dos alertas do módulo <code>fueling</code>. Sem detecção
            registrada, não há o que mostrar.
          </span>
        </div>
      )
    }

    return (
      <>
        <table className={s.tabela}>
          <thead>
            <tr>
              <th className={s.th}>HORA</th>
              <th className={s.th}>CÂMERA</th>
              <th className={s.th}>CLASSE DETECTADA</th>
              <th className={s.th}>CONFIANÇA</th>
            </tr>
          </thead>
          <tbody>
            {lista.map((ev) => (
              <tr key={ev.id}>
                <td className={s.tdMono}>{horaDe(ev.created_at)}</td>
                <td className={s.td}>{nomeDaCamera(ev.camera_id) ?? '—'}</td>
                <td className={s.td}>{ev.class_name || '—'}</td>
                <td className={s.tdMono}>
                  {confiancaInternaOuCliente(ev.confidence, isSuperAdmin)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className={s.faixaFalta}>
          <AlertTriangle
            size={14}
            strokeWidth={2}
            color={lk.estado.atencao}
            style={{ flex: 'none', marginTop: '2px' }}
            aria-hidden="true"
          />
          <span>
            <strong>Sessão, baia, placa e status não entram nesta tabela.</strong>{' '}
            <code>GET /api/fueling/events</code> lê <code>public.alerts</code> e devolve
            id, câmera, classe, confiança e hora — nada mais. &quot;Início de sessão —
            caminhão detectado&quot; e &quot;fim de sessão — divergência&quot; são eventos
            de ciclo de vida que ninguém emite.
          </span>
        </div>
      </>
    )
  }

  // ── Aba Validação ─────────────────────────────────────────────────────────

  function conteudoValidacao() {
    if (relatorio.erro) return falhou(ROTA_RELATORIO, relatorio.erro, relatorio.recarregar)
    if (relatorio.carregando) {
      return <LogikosLoader estado="waiting" variante="fullscreen" rotulo="CARREGANDO VALIDAÇÃO" />
    }

    const conferidas = relatorio.dados?.sessions ?? []
    const meta = relatorio.dados?.threshold_pct

    const semFila = (
      <div className={s.faixaFalta}>
        <AlertTriangle
          size={14}
          strokeWidth={2}
          color={lk.estado.atencao}
          style={{ flex: 'none', marginTop: '2px' }}
          aria-hidden="true"
        />
        <span>
          <strong>Não existe fila de pendentes.</strong> O relatório só traz sessão que JÁ
          tem contagem manual (<code>manual_count IS NOT NULL</code>), e a listagem de
          sessões só traz as em andamento. Uma sessão encerrada e ainda não conferida —
          exatamente o &quot;AGUARDA VALIDAÇÃO&quot; do desenho — não aparece em nenhuma
          das duas, e não há caminho de tela para a primeira conferência.
        </span>
      </div>
    )

    if (conferidas.length === 0) {
      return (
        <>
          <div className={s.centro}>
            <ClipboardCheck
              size={36}
              strokeWidth={1.5}
              color={lk.cor.cinzaNevoa}
              aria-hidden="true"
            />
            <span className={s.centroTitulo}>Nenhuma sessão conferida</span>
            <span className={s.centroTexto}>
              Nenhuma sessão com contagem manual {ROTULO_PERIODO[periodo]}.
            </span>
          </div>
          {semFila}
        </>
      )
    }

    const [primeira, ...demais] = conferidas
    const diferenca = primeira.system_count - primeira.manual_count
    const nome = nomeDaCamera(primeira.camera_id)

    return (
      <>
        <div className={s.fichaValidacao}>
          <div className={s.cartaoTopo}>
            <span className={s.overline}>
              SESSÃO CONFERIDA MAIS RECENTE
              {nome ? ` · ${nome.toUpperCase()}` : ''} · {dataHoraDe(primeira.started_at)}
            </span>
            <span className={s.espacador} />
            {primeira.truck_plate && (
              <span className={s.selo}>PLACA {primeira.truck_plate}</span>
            )}
            <span className={s.selo}>
              {DECISAO[primeira.acceptance_status ?? ''] ?? 'SEM DECISÃO'}
            </span>
          </div>

          <div className={s.tresCaixas}>
            <div className={s.caixa}>
              <span className={s.overline}>CONTADO PELO SISTEMA</span>
              <span className={s.numeroGrande}>{primeira.system_count}</span>
              <span className={s.kpiSub}>soma de total_counts da sessão</span>
            </div>
            <div className={s.caixa}>
              <span className={s.overline}>CONTAGEM MANUAL</span>
              <span className={s.numeroGrande}>{primeira.manual_count}</span>
              <span className={s.kpiSub}>romaneio da expedição</span>
            </div>
            <div className={s.caixa}>
              <span className={s.overline}>DIFERENÇA</span>
              <span
                className={s.numeroGrande}
                style={{ color: diferenca === 0 ? lk.estado.ok : lk.estado.atencao }}
              >
                {diferenca > 0 ? `+${diferenca}` : diferenca}
              </span>
              <Situacao dentro={primeira.passed} meta={meta} />
            </div>
          </div>

          {/*
            Os dois botões do desenho distinguem QUAL contagem virou a oficial.
            O servidor não distingue: `acceptance_status` só aceita
            pending/accepted/rejected, e não há autor nem horário da decisão.
            Ficam no lugar, desabilitados e dizendo por quê.
          */}
          <div className={s.parDeBotoes}>
            <button
              type="button"
              className={s.botaoVeredito}
              disabled
              title="Sem rota: PATCH /sessions/<id> grava só acceptance_status (pending/accepted/rejected) — nada registra qual contagem virou a oficial, nem autor e horário."
            >
              Aceitar contagem do sistema ({primeira.system_count})
            </button>
            <button
              type="button"
              className={s.botaoVeredito}
              disabled
              title="Sem rota: PATCH /sessions/<id> grava só acceptance_status (pending/accepted/rejected) — nada registra qual contagem virou a oficial, nem autor e horário."
            >
              Aceitar contagem manual ({primeira.manual_count})
            </button>
          </div>
          <span className={s.nota}>
            O desenho diz que a decisão fica registrada com autor e horário. Não existem
            as colunas <code>accepted_by</code>/<code>accepted_at</code>, nem trilha de
            auditoria — e nenhum relatório de expedição consome esta decisão.
          </span>
        </div>

        {demais.length > 0 && (
          <>
            <span className={s.overline}>OUTRAS SESSÕES CONFERIDAS</span>
            <table className={s.tabela}>
              <thead>
                <tr>
                  <th className={s.th}>INÍCIO</th>
                  <th className={s.th}>CÂMERA</th>
                  <th className={s.th}>PLACA</th>
                  <th className={s.th}>SISTEMA</th>
                  <th className={s.th}>MANUAL</th>
                  <th className={s.th}>ERRO</th>
                  <th className={s.th}>SITUAÇÃO</th>
                </tr>
              </thead>
              <tbody>
                {demais.map((c) => (
                  <tr key={c.id}>
                    <td className={s.tdMono}>{dataHoraDe(c.started_at)}</td>
                    <td className={s.td}>{nomeDaCamera(c.camera_id) ?? '—'}</td>
                    <td className={s.tdMono}>{c.truck_plate || '—'}</td>
                    <td className={s.tdMono}>{c.system_count}</td>
                    <td className={s.tdMono}>{c.manual_count}</td>
                    <td className={s.tdMono}>
                      {c.error_pct == null ? '—' : `${c.error_pct}%`}
                    </td>
                    <td className={s.td}>
                      <Situacao dentro={c.passed} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}

        <span className={s.nota}>
          A placa aqui é a <code>truck_plate</code>, que é a que o relatório seleciona —
          placa lida por OCR fica em <code>plate_text</code> e não chega a esta tela.
        </span>
        {semFila}
      </>
    )
  }

  const periodoDesabilitado = aba === 'baias' || aba === 'eventos'

  return (
    <div className={s.raiz}>
      <div className={s.cabecalho}>
        {/* Sem barra lateral própria (SEM_BARRA_LATERAL): sem este link não há
            caminho de volta nenhum — regra global, ver
            app/shell/becoSemSaida.test.tsx. */}
        <Link to={rotaHomeDoUsuario(isSuperAdmin, isInTenantContext())} className={s.voltar}>
          <ArrowLeft size={16} strokeWidth={1.7} aria-hidden="true" />
          Voltar
        </Link>
        <div>
          <span className={s.overline}>CARGA</span>
          <h1 className={s.titulo}>{TITULO[aba]}</h1>
        </div>
        <span className={s.espacador} />
        <select
          className={s.seletor}
          disabled
          title="counting_sessions não tem site_id, e nenhuma rota de carga aceita filtro de site. Listar sites exige perfil admin."
          aria-label="Site"
        >
          <option>Todos os sites</option>
        </select>
        <select
          className={s.seletor}
          value={periodo}
          disabled={periodoDesabilitado}
          title={
            periodoDesabilitado
              ? aba === 'baias'
                ? 'A listagem de sessões em andamento não aceita período — o servidor devolve sempre as que estão rodando agora.'
                : 'GET /api/fueling/events aceita só limit — não há filtro por data.'
              : undefined
          }
          aria-label="Período"
          onChange={(e) => setPeriodo(e.target.value as Periodo)}
        >
          <option value="hoje">Hoje</option>
          <option value="sete">7 dias</option>
          <option value="trinta">30 dias</option>
        </select>
      </div>

      <div className={s.abas} role="tablist" aria-label="Seções da Carga">
        {ABAS.map(({ id, rotulo, icone: Icone }) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={aba === id}
            className={aba === id ? s.aba.ativa : s.aba.inativa}
            onClick={() => setAba(id)}
          >
            <Icone size={16} strokeWidth={1.8} aria-hidden="true" />
            {rotulo}
          </button>
        ))}
      </div>

      <div className={s.painelAba} role="tabpanel" aria-label={TITULO[aba]}>
        {aba === 'dashboard' && conteudoDashboard()}
        {aba === 'baias' && conteudoBaias()}
        {aba === 'eventos' && conteudoEventos()}
        {aba === 'validacao' && conteudoValidacao()}
      </div>
    </div>
  )
}
