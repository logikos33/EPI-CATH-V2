/**
 * Módulo Qualidade — `Qualidade.dc.html`.
 *
 * ── O QUE ESTE ARQUIVO É, E O QUE NÃO É ─────────────────────────────────────
 *
 * O desenho é um SHELL com sete itens de menu. Cinco deles (Dashboard,
 * Inspeções, Peças, Relatórios, Config) não desenham nada: eles apenas embutem,
 * via `dc-import`, as telas "Gestão Qualidade", "Revisão Qualidade" e
 * "Configuração Qualidade" — outros arquivos, fora do escopo deste. O conteúdo
 * PRÓPRIO do desenho são duas abas: **Retrabalho** e **Câmeras das estações**.
 *
 * Por isso os sete itens aparecem como uma barra de abas e não como uma segunda
 * barra lateral: o `Shell` do front novo já ocupa a lateral, e as cinco telas
 * embutidas ainda não foram migradas. Elas ficam no lugar do desenho,
 * DESABILITADAS e dizendo por quê — apontá-las para `/quality` levaria o
 * usuário ao front ANTIGO, calado (é o bug que `coexistencia.test.tsx` cobra).
 *
 * ── ONDE O DESENHO PEDE DADO QUE O SERVIDOR NÃO TEM ─────────────────────────
 *
 * Tudo abaixo foi MEDIDO em `services/api/app/api/v1/quality/routes.py` e
 * `gate_repository.py`. Nenhum número desta tela é inventado:
 *
 *  1. **"PONTO" (P4, P2, P6, P1)** não existe em tabela nenhuma do módulo. O
 *     que há é `validation_type ∈ {v1,v2,v3}` — as três validações do gate RVB.
 *     A coluna virou **VALIDAÇÃO** e mostra V1/V2/V3. Renomear é o tratamento
 *     que a tela de Câmeras já deu a "FPS" → "FPS ALVO": o dado é outro, então
 *     o rótulo é outro.
 *  2. **PEÇA e OP** não vêm em `/gate/reworks` (é `SELECT *` de
 *     `quality_reworks`, sem JOIN): só sai `piece_id`, um UUID. `piece_number`
 *     e `work_order` moram em `quality_pieces` e são servidos por
 *     `/gate/pieces` — então resolvemos por lá, como a tela de Operações faz
 *     com o módulo. Peça que não resolve vira "—". **UUID cru na tela, nunca**:
 *     a tela antiga mostra `piece_id.slice(-8)`, que é um pedaço de UUID.
 *  3. **Status "AGUARDANDO"** não existe: `quality_reworks` não tem coluna de
 *     status, só `completed_at`. São DOIS estados, e é o que a tela mostra.
 *  4. **"Marcar recapturada"** prometia o ciclo NC → recaptura → CONFORME.
 *     `PATCH /gate/reworks/<id>/complete` só grava `completed_at` e a duração —
 *     não re-inspeciona nem aprova nada. O botão existe (a rota é real) com o
 *     rótulo do que ele de fato faz: **"Concluir retrabalho"**.
 *  5. **KPIs "AGUARDANDO" e "RECUPERADAS HOJE"** não têm fonte alguma. Os
 *     quatro cartões do desenho foram preenchidos com o que É servido:
 *     `rework_active` e `nok_count` (dashboard/summary) e `by_validation` +
 *     `avg_rework_duration_seconds` (gate/stats/rework).
 *  6. **"meta 2:30"** e o realce âmbar por tempo: não há campo de meta/SLA em
 *     `quality_camera_config`, `quality_stations` nem na config do tenant. Sem
 *     meta, sem realce.
 *  7. **Tempo médio** é ALL-TIME (a query não tem WHERE de data) — o cartão diz
 *     isso, em vez de deixar parecer "hoje".
 *  8. **Filtros "Hoje / 7 dias" e "Estação"**: `gate_list_reworks` só lê
 *     `piece_id` e `validation_type`. `date_from`/`date_to` existem no
 *     repository e a ROTA nunca os passa; estação não existe em camada nenhuma.
 *     Os dois seletores ficam desabilitados, dizendo por quê.
 *  9. **Paginação**: `total` da rota é `len(reworks)` — o tamanho da PÁGINA,
 *     não a contagem. "Página X de Y" construído sobre isso mentiria; a tela
 *     avisa quando a página encheu.
 * 10. **"Latência P95 · 1,4 s"** e **"Papel: captura dos pontos"**: nenhuma
 *     rota do projeto expõe latência por câmera, e `camera_type`/
 *     `validation_types` existem no banco mas não são retornados. Fora.
 * 11. **"ZONA DE CAPTURA DEMARCADA"**: não há ROI/zona em nenhuma tabela do
 *     módulo, e `/quality/cameras` não devolve snapshot nem `rtsp_url`. A
 *     moldura 16:9 fica; o retângulo tracejado não.
 * 12. **ONLINE / INSTÁVEL**: o campo servido é `cameras.status`, estado de
 *     CADASTRO (`VARCHAR(50) DEFAULT 'inactive'`), não liveness. A tela diz
 *     ATIVA/INATIVA e o `title` explica. "Instável" não existe.
 *
 * ── PERMISSÕES ──────────────────────────────────────────────────────────────
 *
 * `quality:read` e `quality:write` EXISTEM no registry desde 29/08 — foram
 * criadas justamente porque esta tela apareceu sem gate. Antes disso o botão
 * ficava sem chave de propósito: inventar uma faria `can()` devolver `false`
 * para sempre e sumiria o controle de todo mundo MENOS do superadmin, que passa
 * por cima de tudo e por isso nunca veria o problema.
 *
 * "Concluir retrabalho" pede `quality:write` — quem opera a bancada (operator,
 * admin) tem; analyst e viewer leem e não concluem. "Testar conexão" segue com
 * `cameras:test`, a mesma chave que a tela de Câmeras usa para o mesmo botão.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  ArrowLeft,
  CircleCheck,
  CircleSlash,
  FileText,
  LayoutDashboard,
  Package,
  Plug,
  RefreshCw,
  Search,
  SlidersHorizontal,
  TriangleAlert,
  Video,
  type LucideIcon,
} from 'lucide-react'

import { useAuth } from '../../hooks/useAuth'
import { api } from '../../services/api'
import { cameraService, type TestResult } from '../../services/cameraService'
import { LogikosLoader } from '../shell/LogikosLoader'
import { lk } from '../tokens/lk.css'
import * as s from './Qualidade.css'
import { Link, useNavigate } from 'react-router-dom'
import { rotaHomeDoUsuario, rotaNova } from '../RotasNovas'
import { isInTenantContext } from '../../services/tenantContext'

// ── Formas REAIS do servidor ────────────────────────────────────────────────

/** `SELECT *` de `quality_reworks` — sem JOIN, sem status, sem estação. */
interface Retrabalho {
  id: string
  piece_id: string
  inspection_id?: string | null
  validation_type?: string | null
  defect_type?: string | null
  defect_description?: string | null
  operator_id?: string | null
  started_at?: string | null
  completed_at?: string | null
  duration_seconds?: number | null
  attempt_number?: number | null
  notes?: string | null
}

/** `quality_pieces` — é aqui, e só aqui, que moram `piece_number` e `work_order`. */
interface Peca {
  id: string
  piece_number?: string | null
  work_order?: string | null
}

/** `data.stats` de `/gate/stats/rework`. `by_validation` é DICT, não array. */
interface EstatisticasRetrabalho {
  by_validation?: Record<string, number> | null
  avg_rework_duration_seconds?: number | null
  most_common_defect?: string | null
}

/** `data.summary` de `/dashboard/summary` — exige o módulo `quality` no JWT. */
interface ResumoQualidade {
  rework_active?: number | null
  nok_count?: number | null
}

/** `/quality/cameras`. Sem `rtsp_url`, sem snapshot, sem `station`. */
interface CameraQualidade {
  id: string
  name?: string | null
  location?: string | null
  status?: string | null
  product_type?: string | null
  production_order?: string | null
  is_setup_mode?: boolean | null
  ok_confidence_threshold?: number | null
  nok_confidence_threshold?: number | null
  last_inspection_at?: string | null
  last_result?: string | null
}

/** `quality_stations` — único lugar que liga câmera ↔ bancada (`camera_ids`). */
interface Estacao {
  id: string
  station_code?: string | null
  name?: string | null
  camera_ids?: string[] | null
}

type Envelope<T> = { data?: T | null }

// ── Abas ────────────────────────────────────────────────────────────────────

type Aba = 'retrabalho' | 'cameras'

interface ItemAba {
  chave: string
  rotulo: string
  Icone: LucideIcon
  /** `null` = a aba é desta tela. Texto = por que não dá para ir. */
  motivoIndisponivel: string | null
  /** Rota do front novo, quando a aba mora em outra tela. */
  destino?: string
}

/**
 * As abas Dashboard/Inspeções/Peças/Relatórios/Config moram em telas
 * IRMÃS — no desenho elas entram por `dc-import`, aqui têm rota própria e
 * deep-link. Ficaram desabilitadas na primeira versão porque, quando esta tela
 * foi escrita, as irmãs ainda não existiam; agora existem e a aba navega.
 */
const ABAS: ItemAba[] = [
  { chave: 'dashboard', rotulo: 'Dashboard', Icone: LayoutDashboard, motivoIndisponivel: null, destino: '/quality/gestao' },
  { chave: 'inspecoes', rotulo: 'Inspeções', Icone: Search, motivoIndisponivel: null, destino: '/quality/revisao' },
  { chave: 'pecas', rotulo: 'Peças', Icone: Package, motivoIndisponivel: null, destino: '/quality/gestao' },
  { chave: 'retrabalho', rotulo: 'Retrabalho', Icone: RefreshCw, motivoIndisponivel: null },
  { chave: 'cameras', rotulo: 'Câmeras', Icone: Video, motivoIndisponivel: null },
  { chave: 'relatorios', rotulo: 'Relatórios', Icone: FileText, motivoIndisponivel: null, destino: '/quality/gestao' },
  { chave: 'config', rotulo: 'Config', Icone: SlidersHorizontal, motivoIndisponivel: null, destino: '/quality/configuracao' },
]

// ── Formatação ──────────────────────────────────────────────────────────────

const SEM_DADO = '—'

function hora(iso: string | null | undefined): string {
  if (!iso) return SEM_DADO
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return SEM_DADO
  return d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })
}

/** Segundos → "m:ss". O desenho usa esse formato ("2:48"). */
function duracao(segundos: number | null | undefined): string {
  if (segundos === null || segundos === undefined) return SEM_DADO
  const n = Number(segundos)
  if (!Number.isFinite(n) || n < 0) return SEM_DADO
  const total = Math.floor(n)
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`
}

/**
 * Tempo da coluna TEMPO.
 *
 * `duration_seconds` só é gravado no `complete_rework`; enquanto o retrabalho
 * está aberto ele vem NULL, e o desenho mostra um relógio correndo. O decorrido
 * é calculado a partir de `started_at`, que é dado real do servidor — mas vem
 * marcado com "há", para não se passar pela duração medida de um ciclo fechado.
 * Ele NÃO tica sozinho: recalcula quando a tela recarrega.
 */
function tempoDe(r: Retrabalho): string {
  if (r.completed_at) return duracao(r.duration_seconds)
  if (!r.started_at) return SEM_DADO
  const inicio = new Date(r.started_at).getTime()
  if (Number.isNaN(inicio)) return SEM_DADO
  const seg = Math.floor((Date.now() - inicio) / 1000)
  return seg < 0 ? SEM_DADO : `há ${duracao(seg)}`
}

/** `active` → ATIVA. É estado de CADASTRO — o `title` da tela diz isso. */
function cadastroDaCamera(status: string | null | undefined): { palavra: string; tom: keyof typeof s.tom } {
  const v = (status ?? '').toLowerCase()
  if (v === 'active') return { palavra: 'ATIVA', tom: 'ok' }
  if (v === 'inactive' || v === '') return { palavra: 'INATIVA', tom: 'neutro' }
  return { palavra: v.toUpperCase(), tom: 'neutro' }
}

const MARCA_PASSO: Record<string, string> = { ok: '✓', error: '✗', warning: '!', pending: '·' }
const TOM_PASSO: Record<string, keyof typeof s.tom> = {
  ok: 'ok', error: 'nc', warning: 'atencao', pending: 'neutro',
}

/** Os 5 checks que `POST /api/cameras/<id>/test` executa, nessa ordem. */
const PASSOS: Array<[keyof TestResult['checks'], string]> = [
  ['url_format', 'Formato do endereço'],
  ['host_reachable', 'Equipamento respondeu'],
  ['port_open', 'Porta aberta'],
  ['rtsp_response', 'Handshake RTSP'],
  ['stream_available', 'Vídeo disponível'],
]

// ── Estados da tela ─────────────────────────────────────────────────────────

function Erro({ rota, detalhe, aoTentar }: { rota: string; detalhe: string; aoTentar: () => void }) {
  return (
    <div className={s.centro}>
      <TriangleAlert size={36} strokeWidth={1.5} color={lk.estado.nc} aria-hidden="true" />
      <span className={s.centroTitulo}>Não foi possível carregar</span>
      <span className={s.centroTecnico}>
        {rota} · {detalhe}
      </span>
      <button className={s.botaoPrimario} onClick={aoTentar}>
        Tentar novamente
      </button>
    </div>
  )
}

// ── Aba Retrabalho ──────────────────────────────────────────────────────────

const ROTA_RETRABALHOS = 'GET /api/v1/quality/gate/reworks'
/** `total` da rota é o tamanho da página; pedimos 50 e avisamos se encher. */
const POR_PAGINA = 50

function Retrabalhos() {
  const { can, hasModule } = useAuth()
  const temModuloQualidade = hasModule('quality')
  const podeConcluir = can('quality:write')

  const [lista, setLista] = useState<Retrabalho[] | null>(null)
  const [pecas, setPecas] = useState<Record<string, Peca>>({})
  const [stats, setStats] = useState<EstatisticasRetrabalho | null>(null)
  const [resumo, setResumo] = useState<ResumoQualidade | null>(null)
  const [erro, setErro] = useState<string | null>(null)
  const [concluindo, setConcluindo] = useState<string | null>(null)

  const carregar = useCallback(() => {
    setErro(null)
    setLista(null)
    api
      .get<Envelope<{ reworks?: Retrabalho[] }>>(`/v1/quality/gate/reworks?page=1&per_page=${POR_PAGINA}`)
      .then((r) => setLista(r.data?.reworks ?? []))
      .catch((e) => setErro(e instanceof Error ? e.message : 'Erro ao carregar'))

    // Resolver PEÇA e OP: sem isto a coluna mostraria UUID. Se falhar, as duas
    // colunas viram "—" e a tela segue de pé — é enfeite útil, não requisito.
    api
      .get<Envelope<{ pieces?: Peca[] }>>('/v1/quality/gate/pieces?page=1&per_page=200')
      .then((r) =>
        setPecas(Object.fromEntries((r.data?.pieces ?? []).map((p) => [String(p.id), p]))),
      )
      .catch(() => undefined)

    api
      .get<Envelope<{ stats?: EstatisticasRetrabalho }>>('/v1/quality/gate/stats/rework')
      .then((r) => setStats(r.data?.stats ?? {}))
      .catch(() => setStats({}))

    // `/dashboard/summary` devolve 403 sem o módulo `quality` no claim. Não
    // chamamos para tomar 403: os dois cartões dizem que não têm fonte.
    if (temModuloQualidade) {
      api
        .get<Envelope<{ summary?: ResumoQualidade }>>('/v1/quality/dashboard/summary')
        .then((r) => setResumo(r.data?.summary ?? {}))
        .catch(() => setResumo({}))
    }
  }, [temModuloQualidade])

  useEffect(carregar, [carregar])

  async function concluir(id: string) {
    setConcluindo(id)
    try {
      await api.patch(`/v1/quality/gate/reworks/${id}/complete`)
      carregar()
    } finally {
      setConcluindo(null)
    }
  }

  const porValidacao = useMemo(() => {
    const dict = stats?.by_validation ?? {}
    const pares = Object.entries(dict).filter(([k, v]) => k && k !== 'null' && Number.isFinite(Number(v)))
    const total = pares.reduce((acc, [, v]) => acc + Number(v), 0)
    const detalhe = pares
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([k, v]) => `${k.toUpperCase()} ${v}`)
      .join(' · ')
    return { total, detalhe }
  }, [stats])

  if (erro) return <Erro rota={ROTA_RETRABALHOS} detalhe={erro} aoTentar={carregar} />
  if (lista === null) {
    return <LogikosLoader estado="waiting" variante="fullscreen" rotulo="CARREGANDO RETRABALHO" />
  }

  const semFonteHoje = temModuloQualidade
    ? 'sem número no servidor'
    : 'o módulo Qualidade não está habilitado nesta sessão'

  return (
    <>
      <div className={s.cabecalho}>
        <h1 className={s.titulo}>Retrabalho</h1>
        <span className={s.espacador} />
        {/* Os dois filtros do desenho ficam no lugar, desabilitados: a rota
            não lê data nem estação. A opção declara o recorte REAL da lista. */}
        <select
          className={s.seletor}
          disabled
          title="A rota de retrabalhos não lê filtro de data: date_from/date_to existem no repository, mas gate_list_reworks nunca os passa."
          aria-label="Período"
        >
          <option>Todo o período</option>
        </select>
        <select
          className={s.seletor}
          disabled
          title="Não há filtro por estação em camada nenhuma: quality_reworks não tem coluna de bancada e a rota não faz JOIN com quality_stations."
          aria-label="Estação"
        >
          <option>Todas as estações</option>
        </select>
      </div>

      <div className={s.kpis}>
        <div className={`${s.kpi} ${s.kpiDestaque}`}>
          <span className={s.kpiLabel}>EM RETRABALHO</span>
          <span className={`${s.kpiValor} ${s.kpiValorAtencao}`}>
            {resumo?.rework_active ?? SEM_DADO}
          </span>
          <span className={s.kpiSub}>
            {resumo?.rework_active === undefined || resumo?.rework_active === null
              ? semFonteHoje
              : 'peças em retrabalho agora'}
          </span>
        </div>
        <div className={s.kpi}>
          <span className={s.kpiLabel}>NC HOJE</span>
          <span className={s.kpiValor}>{resumo?.nok_count ?? SEM_DADO}</span>
          <span className={s.kpiSub}>
            {resumo?.nok_count === undefined || resumo?.nok_count === null
              ? semFonteHoje
              : 'peças reprovadas hoje — a entrada da fila'}
          </span>
        </div>
        <div className={s.kpi}>
          <span className={s.kpiLabel}>RETRABALHOS REGISTRADOS</span>
          <span className={s.kpiValor}>{stats === null ? SEM_DADO : porValidacao.total}</span>
          <span className={s.kpiSub}>
            {porValidacao.detalhe ? `${porValidacao.detalhe} · todo o período` : 'todo o período'}
          </span>
        </div>
        <div className={s.kpi}>
          <span className={s.kpiLabel}>TEMPO MÉDIO</span>
          <span className={s.kpiValor}>{duracao(stats?.avg_rework_duration_seconds)}</span>
          {/* O desenho traz "meta 2:30". Não existe campo de meta em lugar
              nenhum do módulo — e a média do servidor não recorta por data. */}
          <span className={s.kpiSub}>todo o período · só ciclos concluídos</span>
        </div>
      </div>

      {lista.length === 0 ? (
        <div className={s.centro}>
          <CircleCheck size={36} strokeWidth={1.5} color={lk.estado.ok} aria-hidden="true" />
          <span className={s.centroTitulo}>Nada em retrabalho</span>
          <span className={s.centroTexto}>
            Nenhum retrabalho registrado. O ciclo abre na estação, quando uma validação dá
            não conforme.
          </span>
          <button className={s.botaoPrimario} onClick={carregar}>
            Atualizar
          </button>
        </div>
      ) : (
        <div className={s.rolagem}>
          <div className={s.tabela} role="table" aria-label="Fila de retrabalho">
            <div className={s.cabecalhoCelula}>PEÇA</div>
            <div className={s.cabecalhoCelula}>OP</div>
            {/* "PONTO" no desenho. Ponto de inspeção não existe no banco; o que
                existe é a validação v1/v2/v3 do gate. */}
            <div className={s.cabecalhoCelula}>VALIDAÇÃO</div>
            <div className={s.cabecalhoCelula}>MOTIVO</div>
            <div className={`${s.cabecalhoCelula} ${s.alinhaDireita}`}>INÍCIO</div>
            <div className={`${s.cabecalhoCelula} ${s.alinhaDireita}`}>TEMPO</div>
            <div className={s.cabecalhoCelula}>STATUS</div>
            <div className={s.cabecalhoCelula} />

            {lista.map((r) => {
              const peca = pecas[String(r.piece_id)]
              const aberto = !r.completed_at
              return (
                <div key={r.id} style={{ display: 'contents' }}>
                  <span className={`${s.celula} ${s.celulaMono}`}>
                    {peca?.piece_number || SEM_DADO}
                  </span>
                  <span className={`${s.celula} ${s.celulaSecundaria}`}>
                    {peca?.work_order || SEM_DADO}
                  </span>
                  <span className={`${s.celula} ${s.celulaMono}`}>
                    {r.validation_type ? r.validation_type.toUpperCase() : SEM_DADO}
                  </span>
                  <span className={s.celula}>
                    {r.defect_description || r.defect_type || SEM_DADO}
                  </span>
                  <span className={`${s.celula} ${s.celulaMono} ${s.celulaSecundaria} ${s.alinhaDireita}`}>
                    {hora(r.started_at)}
                  </span>
                  <span className={`${s.celula} ${s.celulaMono} ${s.alinhaDireita}`}>
                    {tempoDe(r)}
                  </span>
                  <span className={s.celula}>
                    <span className={`${s.estado} ${aberto ? s.tom.atencao : s.tom.ok}`}>
                      {aberto
                        ? <RefreshCw size={13} strokeWidth={2} aria-hidden="true" />
                        : <CircleCheck size={13} strokeWidth={2} aria-hidden="true" />}
                      {aberto ? 'EM RETRABALHO' : 'CONCLUÍDO'}
                    </span>
                  </span>
                  <span className={`${s.celula} ${s.celulaAcao}`}>
                    {aberto && (
                      <button
                        className={s.acao}
                        disabled={concluindo === r.id || !podeConcluir}
                        title={
                          podeConcluir
                            ? 'PATCH /gate/reworks/<id>/complete grava a hora de fim e soma a duração na peça. Não re-inspeciona nem aprova: a recaptura acontece na estação.'
                            : 'Sem permissão para operar qualidade (quality:write).'
                        }
                        onClick={() => { void concluir(r.id) }}
                      >
                        {concluindo === r.id ? 'Concluindo…' : 'Concluir retrabalho'}
                      </button>
                    )}
                  </span>
                </div>
              )
            })}
          </div>
        </div>
      )}

      <p className={s.nota}>
        A fila acompanha o ciclo NC → retrabalho → recaptura na estação. Concluir aqui fecha
        o registro de tempo; quem devolve a peça a “conforme” é a nova inspeção no kiosk —
        não existe rota que faça as duas coisas.
        {lista.length >= POR_PAGINA && (
          <> Mostrando os {POR_PAGINA} mais recentes: o servidor não devolve a contagem total.</>
        )}
      </p>
    </>
  )
}

// ── Aba Câmeras das estações ────────────────────────────────────────────────

const ROTA_CAMERAS = 'GET /api/v1/quality/cameras'

function CamerasDasEstacoes() {
  const navegar = useNavigate()
  const { can } = useAuth()
  const podeTestar = can('cameras:test')

  const [cameras, setCameras] = useState<CameraQualidade[] | null>(null)
  const [estacoes, setEstacoes] = useState<Estacao[]>([])
  const [erro, setErro] = useState<string | null>(null)
  const [selecionadaId, setSelecionadaId] = useState<string | null>(null)
  const [teste, setTeste] = useState<TestResult | null>(null)
  const [erroTeste, setErroTeste] = useState<string | null>(null)
  const [testando, setTestando] = useState(false)

  const carregar = useCallback(() => {
    setErro(null)
    setCameras(null)
    api
      .get<Envelope<{ cameras?: CameraQualidade[] }>>('/v1/quality/cameras')
      .then((r) => {
        const lista = r.data?.cameras ?? []
        setCameras(lista)
        setSelecionadaId((atual) => atual ?? lista[0]?.id ?? null)
      })
      .catch((e) => setErro(e instanceof Error ? e.message : 'Erro ao carregar'))

    // A ÚNICA fonte real do vínculo câmera ↔ bancada é `camera_ids` de
    // `quality_stations`. `/quality/cameras` não devolve `station`.
    api
      .get<Envelope<{ stations?: Estacao[] }>>('/v1/quality/gate/stations')
      .then((r) => setEstacoes(r.data?.stations ?? []))
      .catch(() => undefined)
  }, [])

  useEffect(carregar, [carregar])

  const estacaoDa = useCallback(
    (cameraId: string): string | null => {
      const e = estacoes.find((est) => (est.camera_ids ?? []).some((c) => String(c) === String(cameraId)))
      if (!e) return null
      return e.name || e.station_code || null
    },
    [estacoes],
  )

  const selecionada = cameras?.find((c) => c.id === selecionadaId) ?? null

  async function testarConexao() {
    if (!selecionada || !podeTestar) return
    setTestando(true)
    setTeste(null)
    setErroTeste(null)
    try {
      setTeste(await cameraService.test(selecionada.id))
    } catch (e) {
      setErroTeste(e instanceof Error ? e.message : 'Erro ao testar a conexão')
    } finally {
      setTestando(false)
    }
  }

  if (erro) return <Erro rota={ROTA_CAMERAS} detalhe={erro} aoTentar={carregar} />
  if (cameras === null) {
    return <LogikosLoader estado="waiting" variante="fullscreen" rotulo="CARREGANDO CÂMERAS" />
  }

  return (
    <>
      <div className={s.cabecalho}>
        <h1 className={s.titulo}>Câmeras das estações</h1>
        <span className={s.espacador} />
        {/* A Configuração de Qualidade agora existe no front novo — vai por
            `rotaNova`, porque `/quality` também é rota do front ANTIGO. */}
        <button
          className={s.botaoSecundario}
          onClick={() => navegar(rotaNova('/quality/configuracao'))}
        >
          <SlidersHorizontal size={15} strokeWidth={1.8} aria-hidden="true" />
          Config de estações
        </button>
      </div>

      {cameras.length === 0 ? (
        <div className={s.centro}>
          <CircleSlash size={36} strokeWidth={1.5} color={lk.cor.cinzaNevoa} aria-hidden="true" />
          <span className={s.centroTitulo}>Nenhuma câmera de estação</span>
          <span className={s.centroTexto}>
            Nenhuma câmera está atribuída ao módulo Qualidade. A atribuição é feita na
            configuração do módulo, que ainda não foi migrada para o front novo.
          </span>
          <span className={s.centroTecnico}>{ROTA_CAMERAS} · 0 câmeras</span>
          <button className={s.botaoPrimario} onClick={carregar}>
            Atualizar
          </button>
        </div>
      ) : (
        <div className={s.split}>
          <div className={s.listaCameras}>
            {cameras.map((c) => {
              const cad = cadastroDaCamera(c.status)
              const estacao = estacaoDa(c.id)
              return (
                <button
                  key={c.id}
                  className={`${s.cartaoCamera} ${c.id === selecionadaId ? s.cartaoCameraAtivo : ''}`}
                  onClick={() => {
                    setSelecionadaId(c.id)
                    setTeste(null)
                    setErroTeste(null)
                  }}
                >
                  <span
                    className={`${s.bolinha} ${s.tom[cad.tom]}`}
                    style={{ background: 'currentColor' }}
                    aria-hidden="true"
                  />
                  <span className={s.cartaoTextos}>
                    <span className={s.cartaoNome}>{c.name || c.id}</span>
                    <span className={s.cartaoSub}>{estacao ?? 'Sem bancada vinculada'}</span>
                  </span>
                  <span className={`${s.cartaoEstado} ${s.tom[cad.tom]}`}>{cad.palavra}</span>
                </button>
              )
            })}
          </div>

          {selecionada && (
            <div className={s.detalhe}>
              <div className={s.detalheTopo}>
                <span className={s.detalheNome}>{selecionada.name || selecionada.id}</span>
                {(() => {
                  const cad = cadastroDaCamera(selecionada.status)
                  return (
                    <span
                      className={`${s.estado} ${s.tom[cad.tom]}`}
                      title="Estado de CADASTRO da câmera (cameras.status). Não é medida de liveness: o produto não serve estado ao vivo por câmera do módulo Qualidade."
                    >
                      {cad.tom === 'ok'
                        ? <CircleCheck size={13} strokeWidth={2} aria-hidden="true" />
                        : <CircleSlash size={13} strokeWidth={2} aria-hidden="true" />}
                      {cad.palavra}
                    </span>
                  )
                })()}
                <span className={s.espacador} />
                {podeTestar && (
                  <button
                    className={s.botaoPrimario}
                    disabled={testando}
                    onClick={() => { void testarConexao() }}
                  >
                    {testando
                      ? <LogikosLoader variante="spinner" estado="waiting" tamanho={16} />
                      : <Plug size={14} strokeWidth={1.7} aria-hidden="true" />}
                    Testar conexão
                  </button>
                )}
              </div>

              <div className={s.corpoDetalhe}>
                <div className={s.previa}>
                  <Video size={22} strokeWidth={1.5} color={lk.cor.cinzaNevoa} aria-hidden="true" />
                  <span className={s.previaTexto}>
                    SEM PRÉVIA — esta rota não serve snapshot nem zona de captura
                  </span>
                </div>
                <dl className={s.campos}>
                  <dt className={s.rotulo}>Estação</dt>
                  <dd className={s.valor}>{estacaoDa(selecionada.id) ?? 'Sem bancada vinculada'}</dd>
                  <dt className={s.rotulo}>Área</dt>
                  <dd className={s.valor}>{selecionada.location || SEM_DADO}</dd>
                  <dt className={s.rotulo}>Produto</dt>
                  <dd className={s.valor}>{selecionada.product_type || SEM_DADO}</dd>
                  <dt className={s.rotulo}>OP</dt>
                  <dd className={s.valor}>{selecionada.production_order || SEM_DADO}</dd>
                  <dt className={s.rotulo}>Último veredito</dt>
                  <dd className={s.valorMono}>
                    {selecionada.last_result
                      ? `${selecionada.last_result.toUpperCase()} · ${hora(selecionada.last_inspection_at)}`
                      : 'SEM INSPEÇÃO REGISTRADA'}
                  </dd>
                  <dt className={s.rotulo}>Modo setup</dt>
                  <dd className={s.valor}>{selecionada.is_setup_mode ? 'Ligado' : 'Desligado'}</dd>
                </dl>
              </div>

              {(teste || erroTeste) && (
                <div className={s.painelTeste}>
                  <span className={s.overline}>Teste de conexão · passo a passo</span>
                  {teste &&
                    PASSOS.map(([chave, texto]) => {
                      const check = teste.checks?.[chave]
                      const tomPasso = TOM_PASSO[check?.status ?? 'pending'] ?? 'neutro'
                      return (
                        <div key={chave} className={s.passo}>
                          <span className={`${s.passoMarca} ${s.tom[tomPasso]}`} aria-hidden="true">
                            {MARCA_PASSO[check?.status ?? 'pending'] ?? '·'}
                          </span>
                          <span className={s.passoTexto}>{texto}</span>
                          {/* O desenho põe um tempo por passo ("31 MS") e FPS.
                              `TestResult.checks` traz {status, message} e nada
                              mais — sem cronometragem, sem leitura de FPS. */}
                          <span className={`${s.passoMensagem} ${s.tom[tomPasso]}`}>
                            {check?.message ?? 'não executado'}
                          </span>
                        </div>
                      )
                    })}
                  {teste && (
                    <span className={`${s.estado} ${teste.success ? s.tom.ok : s.tom.nc}`} role="status">
                      {teste.success
                        ? 'Conexão estabelecida'
                        : teste.error || 'Falha na conexão'}
                    </span>
                  )}
                  {erroTeste && (
                    <span className={`${s.estado} ${s.tom.nc}`} role="status">{erroTeste}</span>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </>
  )
}

// ── Tela ────────────────────────────────────────────────────────────────────

export function Qualidade() {
  const { isSuperAdmin } = useAuth()
  const navegar = useNavigate()
  const [aba, setAba] = useState<Aba>('retrabalho')

  return (
    <div className={s.raiz}>
      <nav className={s.abas} aria-label="Módulo Qualidade">
        {/* Sem barra lateral própria (SEM_BARRA_LATERAL): sem este link não há
            caminho de volta nenhum — regra global, ver
            app/shell/becoSemSaida.test.tsx. */}
        <Link to={rotaHomeDoUsuario(isSuperAdmin, isInTenantContext())} className={s.voltar}>
          <ArrowLeft size={16} strokeWidth={1.7} aria-hidden="true" />
          Voltar
        </Link>
        {ABAS.map((item) => {
          // Aba com `destino` mora noutra tela: ela nunca fica "ativa" aqui.
          const ativa = !item.destino && item.motivoIndisponivel === null && item.chave === aba
          return (
            <button
              key={item.chave}
              className={ativa ? s.aba.ativa : s.aba.inativa}
              disabled={item.motivoIndisponivel !== null}
              title={item.motivoIndisponivel ?? undefined}
              aria-current={ativa ? 'page' : undefined}
              onClick={() => {
                if (item.motivoIndisponivel !== null) return
                // `rotaNova` e não caminho absoluto: `/quality` existe no front
                // ANTIGO, e um link cru levaria para lá calado.
                if (item.destino) navegar(rotaNova(item.destino))
                else setAba(item.chave as Aba)
              }}
            >
              <item.Icone size={16} strokeWidth={1.7} aria-hidden="true" />
              {item.rotulo}
            </button>
          )
        })}
      </nav>

      {aba === 'retrabalho' ? <Retrabalhos /> : <CamerasDasEstacoes />}
    </div>
  )
}
