/**
 * O que este dashboard não pode fazer, e é o que se trava aqui:
 *
 *  · **inventar número.** Score sem cálculo possível vira "—", nunca 100; ação
 *    sem endpoint vira "Indisponível", nunca "6 abertas". Um dashboard de
 *    segurança que preenche lacuna com número plausível é pior que um vazio:
 *    o gestor age em cima do que leu.
 *  · **mostrar cor sozinha.** Todo estado sai com palavra junto — quem não
 *    distingue verde de vermelho ainda tem de conseguir operar.
 *  · **oferecer link que dá 403.** "triar eventos" e "ver câmeras" só aparecem
 *    para quem tem a permissão da tela de destino (`navPorPerfil` já provou
 *    que item que leva a porta fechada é pior que item ausente).
 *  · **chamar "sem dados" um dia bom.** Zero evento com câmera rodando é o
 *    resultado que o cliente quer — esconder isso atrás do estado vazio
 *    apagaria justamente o dia em que ninguém se machucou.
 *  · **mentir sobre o alcance do filtro.** O turno recorta os painéis de
 *    evento; os KPIs do topo o backend fixa em "hoje"/"24 h", e a tela diz.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { Dashboard, scoreImpresso } from './Dashboard'
import { Eventos } from './Eventos'
import { agruparPorRajada } from '../../utils/rajadas'

const getStats = vi.fn()
const getTimeline = vi.fn()
const getSummary = vi.fn()
const getProfile = vi.fn()

vi.mock('../../services/moduleService', () => ({
  moduleService: { getStats: (...a: unknown[]) => getStats(...a) },
}))
vi.mock('../../services/eventsService', () => ({
  eventsService: {
    getTimeline: (...a: unknown[]) => getTimeline(...a),
    getSummary: (...a: unknown[]) => getSummary(...a),
    getProfile: (...a: unknown[]) => getProfile(...a),
  },
}))

/**
 * A tela de DESTINO (`Eventos`) roda de verdade neste arquivo — é a única
 * forma de provar que o número do cartão e o número da lista são o MESMO
 * número, e não duas leituras que ninguém confrontou. Os mocks abaixo
 * existem só para ela.
 *
 * `api.get('/alerts?…')` não devolve um total fixo: ele APLICA o recorte que
 * a querystring pediu sobre `ACERVO`, exatamente como o backend faz
 * (`kind` ausente = 'violation' é decisão da TELA, não do backend — o
 * backend sem `kind` devolve tudo). Um mock de total fixo passaria com o
 * link quebrado.
 */
/**
 * RELÓGIO CONGELADO em `beforeEach` (`vi.setSystemTime`). Desde que o mock
 * de `/alerts` passou a aplicar a JANELA (issue #676), o resultado depende
 * de "hoje": com o relógio real, o acervo de agosto sairia do recorte de 30
 * dias sozinho, e a suíte ficaria vermelha por causa da data em que rodou.
 */
const AGORA = new Date('2026-08-25T14:30:00.000Z')
/** O bucket de uma hora que a barra do Dashboard representa (hora de CAPTURA). */
const HORA_BARRA = '2026-08-25T13:00:00.000Z'
/**
 * A carga em lote GRAVOU tudo num instante só, depois do turno — a forma
 * real do acervo do RVB. É o que separa os dois eixos: nenhuma linha tem
 * `created_at` dentro da hora capturada.
 */
const GRAVACAO_LOTE = '2026-08-25T14:05:00.000Z'

/**
 * `i === 8` cai 30 s depois de `i === 4` (mesma câmera, mesma classe): é UMA
 * RAJADA, dois eventos. Existe de propósito — é o que faz `total_situacoes`
 * divergir de `total` neste acervo, e sem essa divergência o cartão de
 * tratativa e a lista poderiam concordar por acaso, contando a mesma unidade.
 */
const capturaDe = (i: number) =>
  i < 3
    ? new Date(Date.parse(HORA_BARRA) + (i + 1) * 10 * 60_000).toISOString()
    : i === 8
      ? new Date(AGORA.getTime() - 4 * 3 * 3_600_000 + 30_000).toISOString()
      : new Date(AGORA.getTime() - i * 3 * 3_600_000).toISOString()

const ACERVO = Array.from({ length: 24 }, (_, i) => ({
  id: `e${i}`,
  camera_id: 'c1',
  camera_name: 'Entrada Expedição',
  // 1 em cada 4 é violação de verdade; o resto é conformidade (EPI em uso),
  // que é a maioria do acervo real do RVB.
  violations: [{ class: i % 4 === 0 ? 'no_helmet' : 'helmet', confidence: 0.9 }],
  event_kind: (i % 4 === 0 ? 'violation' : 'compliance') as 'violation' | 'compliance',
  // 1 em cada 3 já foi reconhecido. Com `i % 2` (como era) TODA violação
  // — que é `i % 4` — caía em índice par e vinha reconhecida: a fila de
  // "aguardando violação" era zero, e um cartão que conta zero concorda com
  // qualquer lista.
  acknowledged: i % 3 === 0,
  // Os DOIS eixos, e eles DIVERGEM (é o defeito das issues #674/#676):
  // gravação num instante só, captura espalhada pelo turno.
  created_at: GRAVACAO_LOTE,
  timestamp: capturaDe(i),
  verification_verdict: null,
  verified_by: null,
}))

function servirAlerts(rota: string) {
  const q = new URLSearchParams(rota.split('?')[1] ?? '')
  const kind = q.get('kind')
  const ack = q.get('acknowledged')
  // EIXO DO TEMPO — espelha o contrato de `GET /api/alerts` depois da issue
  // #676: a janela `start_date`/`end_date` é lida na hora de CAPTURA
  // (`timestamp`), que é a coluna que a lista EXIBE; `?time_field=created`
  // volta ao eixo da gravação. Um mock cego a datas passaria com o link
  // apontando para a janela errada.
  const eixo = q.get('time_field') === 'created' ? 'created_at' : 'timestamp'
  const de = q.get('start_date')
  const ate = q.get('end_date')
  // ⚠️ `module_code` NÃO é aplicado aqui de propósito: `Eventos.tsx` ainda
  // não relê o parâmetro da URL (issue #701), então o mock não pode fingir
  // um escopo que a tela não manda.
  const alerts = ACERVO.filter(
    (e) =>
      (kind === null || kind === '' || e.event_kind === kind) &&
      (ack === null || e.acknowledged === (ack === 'true')) &&
      (!de || Date.parse(e[eixo]) >= Date.parse(de)) &&
      (!ate || Date.parse(e[eixo]) <= Date.parse(ate)),
  )
  // `total_situacoes` — RAJADAS do recorte inteiro, com o MESMO critério do
  // backend (câmera+classe, gap ≤60 s, eixo de CAPTURA: `alert_repository
  // .list_with_filters`). Sem isto o mock não distinguiria as duas unidades
  // que o produto imprime, e o cartão poderia trocar uma pela outra sem que
  // nenhum teste percebesse.
  const total_situacoes = agruparPorRajada(alerts, {
    cameraId: (e) => e.camera_id,
    classe: (e) => e.violations?.[0]?.class ?? '',
    criadoEm: (e) => e.timestamp,
  }).length
  return {
    success: true,
    data: { alerts, total: alerts.length, total_situacoes, page: 1, per_page: 20, pages: 1 },
  }
}

vi.mock('../../services/api', () => ({
  ApiError: class extends Error { status = 500 },
  getToken: () => 't',
  api: {
    get: vi.fn((rota: string) => Promise.resolve(servirAlerts(rota))),
    post: vi.fn(() => Promise.resolve({ success: true })),
    downloadBlob: vi.fn(() => Promise.resolve(new Blob(['a']))),
  },
}))
vi.mock('../../services/cameraService', () => ({ cameraService: { list: () => Promise.resolve([]) } }))
vi.mock('../../hooks/useModuleClasses', () => ({
  useModuleClasses: () => ({
    classes: [
      { class_name: 'no_helmet', display_name: 'Sem capacete', polaridade: 'violacao' },
      { class_name: 'helmet', display_name: 'Capacete', polaridade: 'conformidade' },
    ],
    loading: false,
    classLabel: (c: string) => (c === 'no_helmet' ? 'Sem capacete' : 'Capacete'),
  }),
}))
vi.mock('../../components/ui/Toast/useToast', () => ({
  useToast: () => ({ error: vi.fn(), success: vi.fn(), info: vi.fn() }),
}))

/**
 * Node 25 traz Web Storage nativo e ele colide com o do jsdom neste ambiente
 * (mesmo achado já registrado em `tenantContextRenewal.test.ts`) — o global
 * real perde métodos. Storage in-memory resolve, e de quebra isola a
 * preferência de widgets entre casos.
 */
class MemoriaStorage implements Storage {
  private mapa = new Map<string, string>()
  get length(): number { return this.mapa.size }
  clear(): void { this.mapa.clear() }
  getItem(k: string): string | null { return this.mapa.get(k) ?? null }
  key(i: number): string | null { return Array.from(this.mapa.keys())[i] ?? null }
  removeItem(k: string): void { this.mapa.delete(k) }
  setItem(k: string, v: string): void { this.mapa.set(k, String(v)) }
}

/** Formato real de `module_service.get_stats` (RVB — 3 câmeras, 1 fora). */
const STATS_RVB = {
  cameras_active: 2,
  cameras_total: 3,
  alerts_today: 23,
  alerts_week: 196, // média 7d = 28 → −18%, o número do desenho
  compliance_rate: 87,
}

function montar({
  permissoes = ['alerts:read', 'cameras:read'],
}: { permissoes?: string[] } = {}) {
  sessionStorage.setItem(
    'user',
    JSON.stringify({ id: 'u1', email: 'a@b.c', name: 'Ana', role: 'operator', permissions: permissoes }),
  )
  const cliente = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={cliente}>
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

/** Vazio de `/events/profile` — nenhum evento capturado na janela. */
const PERFIL_VAZIO = {
  rows: [],
  situacao: {
    total: 0,
    nao_reconhecidos: 0,
    procedentes: 0,
    improcedentes: 0,
    cameras: 0,
    primeira_captura: null,
    ultima_captura: null,
    confianca_media: null,
  },
}

/**
 * Perfil com os números REAIS medidos no DEV (RVB, 423 alertas): 302
 * conformidade, 66 violação, 55 não definida — a proporção importa, porque é
 * ela que prova que "423 alertas" não é "423 violações".
 */
const PERFIL_RVB = {
  rows: [
    { bucket: '2026-08-21T13:00:00', kind: 'violacao', count: 40 },
    { bucket: '2026-08-21T13:00:00', kind: 'conformidade', count: 97 },
    { bucket: '2026-08-21T17:00:00', kind: 'violacao', count: 26 },
    { bucket: '2026-08-21T17:00:00', kind: 'conformidade', count: 205 },
    { bucket: '2026-08-23T13:00:00', kind: 'indefinido', count: 55 },
  ],
  situacao: {
    total: 423,
    nao_reconhecidos: 396,
    procedentes: 60,
    improcedentes: 62,
    cameras: 14,
    primeira_captura: '2026-08-21T10:30:00',
    ultima_captura: '2026-08-23T13:41:30',
    confianca_media: 0.5622104018912525,
  },
}

beforeEach(() => {
  // Só `Date` é falsificado: `setTimeout`/`setInterval` reais mantêm o
  // react-query e o `waitFor` do Testing Library funcionando.
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(AGORA)
  // Dois armazenamentos, de propósito: a SESSÃO ('user') vive na aba
  // (services/sessao.ts) e a PREFERÊNCIA de widget vive no navegador.
  vi.stubGlobal('sessionStorage', new MemoriaStorage())
  vi.stubGlobal('localStorage', new MemoriaStorage())
  getStats.mockResolvedValue({ ...STATS_RVB })
  getTimeline.mockResolvedValue({ bucket: 'hour', timeline: [] })
  getSummary.mockResolvedValue({ total: 0, by_class: [], by_camera: [] })
  getProfile.mockResolvedValue(PERFIL_VAZIO)
})

afterEach(() => {
  vi.clearAllMocks()
  vi.useRealTimers()
})

describe('EPI Dashboard — números', () => {
  it('mostra o score da API e não um número de exemplo', async () => {
    montar()
    expect(await screen.findByText('87')).toBeTruthy()
    // O 82 do protótipo ("+5 vs ontem") não pode vazar para a tela.
    expect(screen.queryByText(/vs ontem/i)).toBeNull()
  })

  it('score sem cálculo possível vira "—" e a palavra Indisponível, nunca 100', async () => {
    getStats.mockResolvedValue({ ...STATS_RVB, cameras_active: 0, compliance_rate: null })
    montar()
    const cartao = await screen.findByLabelText('Score de conformidade')
    expect(within(cartao).getByText('—')).toBeTruthy()
    expect(within(cartao).getByText('Indisponível')).toBeTruthy()
    expect(within(cartao).queryByText('100')).toBeNull()
  })

  it('declara que não tem histórico de 7 dias em vez de desenhar uma curva', async () => {
    montar()
    expect(await screen.findByText(/SCORE SEM SÉRIE · 7 DIAS/)).toBeTruthy()
  })

  it('deriva o delta de eventos da média de 7 dias que a API devolveu', async () => {
    montar()
    const cartao = await screen.findByLabelText('Eventos hoje')
    expect(within(cartao).getByText('23')).toBeTruthy()
    // 23 vs média 28 (196/7) = −18%
    expect(within(cartao).getByText(/−18% vs média 7d/)).toBeTruthy()
  })

  it('sem semana nenhuma, admite que não há média — não escreve 0%', async () => {
    getStats.mockResolvedValue({ ...STATS_RVB, alerts_week: 0, alerts_today: 0 })
    montar()
    const cartao = await screen.findByLabelText('Eventos hoje')
    expect(within(cartao).getByText(/sem média de 7 dias ainda/)).toBeTruthy()
  })
})

describe('EPI Dashboard — o que o backend não tem', () => {
  it('não sobra cartão morto na tela: o travessão de "Ações abertas" saiu', async () => {
    getProfile.mockResolvedValue(PERFIL_RVB)
    montar()
    await screen.findByText('87')
    // O cartão inventava um domínio (ação corretiva com prazo e responsável)
    // que não existe em tabela nenhuma, e ocupava um quarto da faixa de KPI
    // com um "—". Espaço de tela não se gasta com placeholder.
    expect(screen.queryByLabelText('Ações abertas')).toBeNull()
    expect(screen.queryByText('Indisponível')).toBeNull()
    // Os exemplos do protótipo continuam banidos.
    expect(screen.queryByText(/Reforçar DDS na doca/i)).toBeNull()
    expect(screen.queryByText(/CARLOS M\./i)).toBeNull()
  })

  it('não oferece seletor de site — nenhum endpoint desta tela aceita site', async () => {
    montar()
    await screen.findByText('87')
    expect(screen.queryByLabelText(/site/i)).toBeNull()
  })
})

describe('EPI Dashboard — câmeras não fingem telemetria que não existe (D1)', () => {
  it('mostra só o número de câmeras ativas — nunca "online", que prometeria conectividade não medida', async () => {
    montar()
    const cartao = await screen.findByLabelText('Câmeras ativas')
    const valor = within(cartao).getByText('2')
    expect(valor).toBeTruthy()
    // getByText('2') casa igual com "2/3": por padrão o testing-library só
    // junta os text nodes DIRETOS do elemento, então um <span> aninhado com
    // o "/3" fica fora do match e passaria despercebido. .textContent pega
    // a árvore inteira — é o que de fato trava a fração fora da tela.
    expect(valor.textContent).toBe('2')
    expect(within(cartao).getByText('Cadastradas e ativas')).toBeTruthy()
    expect(within(cartao).queryByText(/online/i)).toBeNull()
    expect(within(cartao).queryByText(/fora do ar/i)).toBeNull()
  })

  it('zero câmera ativa admite isso em vez de inventar contagem', async () => {
    getStats.mockResolvedValue({ ...STATS_RVB, cameras_active: 0, cameras_total: 0 })
    montar()
    const cartao = await screen.findByLabelText('Câmeras ativas')
    const valor = within(cartao).getByText('0')
    expect(valor.textContent).toBe('0')
    expect(within(cartao).getByText('Nenhuma câmera ativa')).toBeTruthy()
  })
})

describe('EPI Dashboard — permissão', () => {
  it('esconde os atalhos das telas que o perfil não pode abrir', async () => {
    montar({ permissoes: [] })
    await screen.findByText('87')
    expect(screen.queryByText(/triar eventos/)).toBeNull()
    expect(screen.queryByText(/ver câmeras/)).toBeNull()
  })

  it('mostra os atalhos para quem pode', async () => {
    montar()
    expect(await screen.findByText(/triar eventos/)).toBeTruthy()
    expect(screen.getByText(/ver câmeras/)).toBeTruthy()
  })
})

describe('EPI Dashboard — vazio, erro e turno', () => {
  it('dia sem violação NÃO é tela vazia: mostra o score e zero eventos', async () => {
    getStats.mockResolvedValue({
      cameras_active: 3, cameras_total: 3, alerts_today: 0, alerts_week: 0, compliance_rate: 100,
    })
    montar()
    expect(await screen.findByText('100')).toBeTruthy()
    expect(screen.queryByText(/Sem dados para este módulo/)).toBeNull()
  })

  it('sem câmera e sem evento, aí sim é vazio — com caminho de saída', async () => {
    getStats.mockResolvedValue({
      cameras_active: 0, cameras_total: 0, alerts_today: 0, alerts_week: 0, compliance_rate: null,
    })
    montar()
    expect(await screen.findByText(/Sem dados para este módulo/)).toBeTruthy()
    expect(screen.getByText('Ver câmeras')).toBeTruthy()
  })

  it('falha de carga diz o que falhou (sem a rota crua) e um retry que refaz a chamada', async () => {
    getStats.mockRejectedValue(new Error('timeout'))
    montar()
    expect(await screen.findByText(/Não foi possível carregar/)).toBeTruthy()
    // Rodada V1 do jargão: o detalhe era `GET /API/MODULES/EPI/STATS` — a rota
    // da API como única explicação, servida no DEV. O estado de erro continua
    // DIZENDO o que falhou; o que saiu foi o endereço.
    const detalhe = screen.getByText(/não responderam/)
    expect(detalhe.textContent, 'a rota da API voltou para a tela').not.toMatch(/GET |\/api\//i)

    getStats.mockResolvedValue({ ...STATS_RVB })
    fireEvent.click(screen.getByText('Tentar novamente'))
    expect(await screen.findByText('87')).toBeTruthy()
  })

  it('trocar o turno reconsulta os eventos na janela nova', async () => {
    montar()
    await screen.findByText('87')
    await waitFor(() => expect(getTimeline).toHaveBeenCalled())

    const primeira = getTimeline.mock.calls.at(-1)?.[0] as { from: string; to: string }
    fireEvent.change(screen.getByLabelText('Turno'), { target: { value: 'primeiro' } })

    await waitFor(() => {
      const ultima = getTimeline.mock.calls.at(-1)?.[0] as { from: string; to: string }
      expect(ultima.from).not.toBe(primeira.from)
    })
    const ultima = getTimeline.mock.calls.at(-1)?.[0] as { from: string; to: string }
    expect(new Date(ultima.from).getHours()).toBe(6)
    expect(new Date(ultima.to).getHours()).toBe(14)
  })
})

describe('EPI Dashboard — painéis', () => {
  it('período sem evento diz que está sem evento, não desenha barra', async () => {
    montar()
    const painel = await screen.findByLabelText('Eventos por hora')
    expect(within(painel).getByText('Sem eventos no período.')).toBeTruthy()
  })

  it('violações por classe saem do resumo da API, com o percentual real', async () => {
    getSummary.mockResolvedValue({
      total: 23,
      by_class: [
        { class: 'no_helmet', count: 12 },
        { class: 'no_vest', count: 6 },
        { class: 'no_gloves', count: 5 },
      ],
      by_camera: [],
    })
    montar()
    const painel = await screen.findByLabelText('Violações por classe')
    expect(within(painel).getByText('Sem capacete')).toBeTruthy()
    expect(within(painel).getByText('12')).toBeTruthy()
    // 12 de 23 = 52%, o mesmo número do desenho — só que calculado.
    expect(within(painel).getByText(/concentra 52% das violações/)).toBeTruthy()
  })

  it('ranking de câmeras: só as 3 primeiras saem em destaque, resto neutro', async () => {
    getSummary.mockResolvedValue({
      total: 0,
      by_class: [],
      by_camera: [
        { camera_id: 'c1', camera_name: 'Entrada Expedição', count: 108 },
        { camera_id: 'c2', camera_name: 'Guarita', count: 63 },
        { camera_id: 'c3', camera_name: 'Porta Pallets', count: 41 },
        { camera_id: 'c4', camera_name: 'Manutenção', count: 28 },
      ],
    })
    montar()
    const painel = await screen.findByLabelText('Câmeras com mais eventos')
    // Posições são sempre zero-padded ("01".."04") — só elas batem este padrão,
    // nunca as contagens (108/63/41/28), que não começam com zero.
    const posicoes = within(painel).getAllByText(/^0[1-9]$/)
    expect(posicoes).toHaveLength(4)

    const nomeTop = within(painel).getByText('Entrada Expedição')
    const nomeResto = within(painel).getByText('Manutenção')
    expect(nomeTop.className).not.toBe(nomeResto.className)

    // Backend já devolve top-10 ordenado por count DESC (top_cameras_by_alerts).
    // 108 + 63 + 41 = 212 de 240 no total = 88%.
    expect(within(painel).getByText('88%')).toBeTruthy()
  })

  it('ranking de câmeras vazio diz que não há eventos, não desenha barra nenhuma', async () => {
    getSummary.mockResolvedValue({ total: 0, by_class: [], by_camera: [] })
    montar()
    const painel = await screen.findByLabelText('Câmeras com mais eventos')
    expect(within(painel).getByText('Sem eventos no período')).toBeTruthy()
  })

  it('câmera sem nome não vaza UUID cru na tela', async () => {
    getSummary.mockResolvedValue({
      total: 0,
      by_class: [],
      by_camera: [{ camera_id: 'c1', camera_name: null, count: 5 }],
    })
    montar()
    const painel = await screen.findByLabelText('Câmeras com mais eventos')
    expect(within(painel).getByText('Sem nome')).toBeTruthy()
  })

  it('barra de eventos por hora leva pra lista filtrada naquela hora exata', async () => {
    // Hora corrente truncada em UTC: sempre cai dentro do turno padrão
    // ("dia", 00h–24h local), sem depender de fuso ou de quando o teste roda.
    const inicioHora = new Date()
    inicioHora.setUTCMinutes(0, 0, 0)
    const bucketIso = inicioHora.toISOString()
    getTimeline.mockResolvedValue({ bucket: 'hour', timeline: [{ bucket: bucketIso, count: 4 }] })
    montar()
    const painel = await screen.findByLabelText('Eventos por hora')
    const barra = within(painel).getByTitle(/4 evento/)
    expect(barra.tagName).toBe('A')
    const href = barra.getAttribute('href') ?? ''
    expect(href).toContain('/novo/epi/eventos?')
    const params = new URLSearchParams(href.split('?')[1])
    expect(params.get('start_date')).toBe(bucketIso)
    expect(new Date(params.get('end_date') ?? '').getTime() - inicioHora.getTime()).toBe(3_600_000)
  })

  it('linha de violações por classe leva pra lista filtrada pela classe', async () => {
    getSummary.mockResolvedValue({
      total: 12,
      by_class: [{ class: 'no_helmet', count: 12 }],
      by_camera: [],
    })
    montar()
    const painel = await screen.findByLabelText('Violações por classe')
    const linha = within(painel).getByRole('link', { name: /Sem capacete/ })
    const href = linha.getAttribute('href') ?? ''
    expect(href).toContain('/novo/epi/eventos?')
    expect(href).toContain('violation_type=no_helmet')
  })

  it('linha do ranking de câmeras leva pra lista filtrada por câmera, na janela de 30 dias', async () => {
    getSummary.mockResolvedValue({
      total: 5,
      by_class: [],
      by_camera: [{ camera_id: 'c1', camera_name: 'Entrada Expedição', count: 5 }],
    })
    montar()
    const painel = await screen.findByLabelText('Câmeras com mais eventos')
    const linha = within(painel).getByRole('link', { name: /Entrada Expedição/ })
    const href = linha.getAttribute('href') ?? ''
    expect(href).toContain('/novo/epi/eventos?')
    expect(href).toContain('camera_id=c1')
    // Ranking é sempre 30 dias — não é o recorte do turno selecionado.
    expect(href).toContain('start_date=')
  })

  it('câmera sem camera_id fica de pé mas sem link — filtro vazio mostraria tudo', async () => {
    getSummary.mockResolvedValue({
      total: 5,
      by_class: [],
      by_camera: [{ camera_id: null, camera_name: 'Sem nome', count: 5 }],
    })
    montar()
    const painel = await screen.findByLabelText('Câmeras com mais eventos')
    expect(within(painel).getByText('Sem nome')).toBeTruthy()
    expect(within(painel).queryByRole('link', { name: /Sem nome/ })).toBeNull()
  })

  it('KPI de eventos hoje leva pra lista da janela de hoje', async () => {
    montar()
    const cartao = await screen.findByLabelText('Eventos hoje')
    const link = within(cartao).getByRole('link', { name: /Eventos hoje: 23/ })
    const href = link.getAttribute('href') ?? ''
    expect(href).toContain('/novo/epi/eventos?')
    expect(href).toContain('start_date=')
    expect(href).toContain('end_date=')
  })

  it('vazio de eventos por hora oferece o caminho pros últimos 30 dias', async () => {
    montar()
    const painel = await screen.findByLabelText('Eventos por hora')
    expect(within(painel).getByText('Sem eventos no período.')).toBeTruthy()
    const cta = within(painel).getByRole('link', { name: /últimos 30 dias/ })
    expect((cta.getAttribute('href') ?? '')).toContain('/novo/epi/eventos?')
  })

  it('vazio de violações por classe oferece o caminho pros últimos 30 dias', async () => {
    montar()
    const painel = await screen.findByLabelText('Violações por classe')
    expect(within(painel).getByText('Sem violações no período.')).toBeTruthy()
    const cta = within(painel).getByRole('link', { name: /últimos 30 dias/ })
    expect((cta.getAttribute('href') ?? '')).toContain('/novo/epi/eventos?')
  })

  it('esconder um widget o tira da tela e a preferência sobrevive à remontagem', async () => {
    // `region` = o <section> do painel. O checkbox do popover carrega o mesmo
    // rótulo, e por nome só a busca pegaria os dois.
    const painel = () => screen.queryByRole('region', { name: 'Composição dos eventos' })

    const tela = montar()
    await screen.findByText('87')
    expect(painel()).toBeTruthy()

    fireEvent.click(screen.getByText('Personalizar widgets'))
    fireEvent.click(await screen.findByLabelText('Composição dos eventos', { selector: 'input' }))
    await waitFor(() => expect(painel()).toBeNull())

    tela.unmount()
    montar()
    await screen.findByText('87')
    expect(painel()).toBeNull()
  })

  it('os painéis novos entram no "Personalizar widgets", como os vizinhos', async () => {
    montar()
    await screen.findByText('87')
    fireEvent.click(screen.getByText('Personalizar widgets'))
    for (const rotulo of ['Violações por horário do dia', 'Volume por dia', 'Composição dos eventos']) {
      expect(await screen.findByLabelText(rotulo, { selector: 'input' })).toBeTruthy()
    }
  })
})

/**
 * O bloco que impede o painel de mentir. Cada widget novo tem DOIS casos: um
 * com dado (mostra o número que veio do banco) e um SEM dado (diz que não há,
 * em vez de desenhar barra de enfeite ou escrever zero como se fosse medição).
 */
describe('EPI Dashboard — perfil temporal (violações por horário, volume por dia, composição)', () => {
  it('lê o perfil pelo horário de CAPTURA, não pelo de ingestão', async () => {
    montar()
    await screen.findByText('87')
    // A linha do tempo de hoje também: sem `timeField: 'captured'` o painel
    // desenha a hora em que a carga rodou, não a hora da fábrica.
    await waitFor(() => expect(getTimeline).toHaveBeenCalled())
    const params = getTimeline.mock.calls.at(-1)?.[0] as { timeField?: string }
    expect(params.timeField).toBe('captured')
  })

  it('aponta a hora de PICO DE VIOLAÇÃO, não a hora de maior volume', async () => {
    getProfile.mockResolvedValue(PERFIL_RVB)
    montar()
    const painel = await screen.findByLabelText('Violações por horário do dia')
    // 13h UTC tem 40 violações em 137 eventos; 17h UTC tem 26 em 231. O pico
    // de VIOLAÇÃO é o das 13h — o de volume seria o das 17h.
    const horaLocal = String(new Date('2026-08-21T13:00:00Z').getHours()).padStart(2, '0')
    await waitFor(() =>
      expect(within(painel).getByText(`${horaLocal}h`, { selector: 'span' })).toBeTruthy(),
    )
    expect(within(painel).getByText('40')).toBeTruthy()
  })

  it('sem evento capturado, os três painéis dizem isso em vez de desenhar barra', async () => {
    montar()
    for (const nome of ['Violações por horário do dia', 'Volume por dia', 'Composição dos eventos']) {
      const painel = await screen.findByLabelText(nome)
      expect(within(painel).getByText('Nenhum evento capturado no período.')).toBeTruthy()
      // Nada de "0" ou "—" ocupando o lugar de um número que não existe.
      expect(within(painel).queryByText('—')).toBeNull()
      expect(within(painel).queryByRole('group')).toBeNull()
    }
  })

  it('a composição separa violação de conformidade — o total não é "tudo violação"', async () => {
    getProfile.mockResolvedValue(PERFIL_RVB)
    montar()
    const painel = await screen.findByLabelText('Composição dos eventos')
    await waitFor(() => expect(within(painel).getByText('66')).toBeTruthy())
    expect(within(painel).getByText('302')).toBeTruthy()
    expect(within(painel).getByText('55')).toBeTruthy()
    expect(within(painel).getByText('Conformidade (EPI em uso)')).toBeTruthy()
    expect(within(painel).getByText('Não definida')).toBeTruthy()
    // 66 de 423 = 16%, e a frase tem de dizer isso e não "423 violações".
    expect(within(painel).getByText(/16% dos 423 eventos do período são violação/)).toBeTruthy()
  })

  it('mostra a revisão humana e a confiança média que vieram da API', async () => {
    getProfile.mockResolvedValue(PERFIL_RVB)
    montar()
    const painel = await screen.findByLabelText('Composição dos eventos')
    await waitFor(() => expect(within(painel).getByText(/122/)).toBeTruthy())
    expect(within(painel).getByText(/60 procedentes · 62 descartados/)).toBeTruthy()
    expect(within(painel).getByText('56%')).toBeTruthy()
    expect(within(painel).getByText('14')).toBeTruthy()
  })

  it('declara o alcance REAL do dado em vez de deixar supor 90 dias de operação', async () => {
    getProfile.mockResolvedValue(PERFIL_RVB)
    montar()
    const painel = await screen.findByLabelText('Violações por horário do dia')
    // 2 dias com registro (21 e 23/08) numa série de 3 dias — o buraco de 22
    // aparece no gráfico e não conta como dia operado.
    await waitFor(() =>
      expect(within(painel).getByText(/21\/08 a 23\/08 · 2 dia\(s\) com registro/)).toBeTruthy(),
    )
  })

  /**
   * ISSUE #802 — "AGUARDANDO TRATATIVA 5.062" abria uma tela que dizia 368.
   *
   * O cartão vinha de `perfil.situacao.nao_reconhecidos`: TODO evento, de
   * TODO tipo, em 90 dias. No DEV (RVB, 05/09) isso somava os **3.881
   * eventos de CONFORMIDADE (EPI em uso)** que o próprio Dashboard decompõe
   * dois blocos abaixo — e ninguém trata EPI em uso. 14× de diferença entre
   * o número e o destino.
   *
   * Estes casos travam o recorte NOVO (violação · sem reconhecimento · 30 d,
   * a janela de `Acoes.tsx`). Volte o cartão para `situacao.nao_reconhecidos`
   * e o primeiro fica vermelho: 15 (todo tipo, todo estado do perfil) no
   * lugar de 3.
   */
  it('#802: o cartão conta VIOLAÇÃO sem reconhecimento, não o acervo inteiro', async () => {
    getProfile.mockResolvedValue(PERFIL_RVB)
    montar()
    const cartao = await screen.findByLabelText('Aguardando tratativa')
    // 4 violações sem reconhecimento no acervo (i = 4, 8, 16, 20), e duas
    // delas (4 e 8) são a MESMA rajada → 3 situações.
    await waitFor(() => expect(within(cartao).getByText('3')).toBeTruthy())
    expect(within(cartao).getByText('Sem reconhecimento')).toBeTruthy()
    // A legenda declara a outra unidade e o recorte inteiro — o número grande
    // conta situações (como `/epi/acoes`), a legenda conta eventos (como a
    // lista de `/epi/eventos`).
    expect(within(cartao).getByText(/4 evento\(s\) · violação sem reconhecimento · 30d/)).toBeTruthy()
    // ⛔ o número do perfil (396 sem reconhecimento de 423) não pode reaparecer.
    expect(within(cartao).queryByText('396')).toBeNull()
  })

  it('#802: sem a contagem da fila o cartão não entra — não inventa um zero', async () => {
    const { api } = await import('../../services/api')
    const get = api.get as unknown as ReturnType<typeof vi.fn>
    get.mockImplementation((rota: string) =>
      rota.includes('acknowledged=false')
        ? Promise.reject(new Error('timeout'))
        : Promise.resolve(servirAlerts(rota)),
    )
    montar()
    await screen.findByText('87')
    await waitFor(() => expect(screen.queryByLabelText('Aguardando tratativa')).toBeNull())
    get.mockImplementation((rota: string) => Promise.resolve(servirAlerts(rota)))
  })
})

/**
 * O DEEP-LINK QUE SE DESMENTE (bloco 2, achado 2).
 *
 * Medido no DEV em 2026-09-05 (tenant RVB, janela de 30 dias):
 *
 *   painel "Câmeras com mais eventos"  GET /v1/events/summary   → 4.629
 *   destino do clique, como estava     GET /api/alerts (s/ kind) →   495
 *
 * O link não mandava `kind`; `Eventos.tsx` assume 'violation' quando o
 * parâmetro está AUSENTE. O cartão contava tudo, a lista mostrava só as
 * violações — 10× menos, sem uma palavra explicando.
 *
 * Estes casos rodam as DUAS telas: leem o href que o Dashboard escreveu e
 * montam `Eventos` nele, contra o mesmo acervo. Se os dois números
 * divergirem, o teste falha — que é a única maneira de o link não voltar a
 * mentir por omissão.
 */
describe('EPI Dashboard — o link carrega o filtro que produziu o número', () => {
  const kindDe = (href: string) => new URLSearchParams(href.split('?')[1]).get('kind')

  it('painel que contou TODO evento manda kind explícito de "todos"', async () => {
    getSummary.mockResolvedValue({
      total: 24,
      by_class: [],
      by_camera: [{ camera_id: 'c1', camera_name: 'Entrada Expedição', count: 24 }],
    })
    montar()
    const painel = await screen.findByLabelText('Câmeras com mais eventos')
    const href = within(painel).getByRole('link', { name: /Entrada Expedição/ }).getAttribute('href') ?? ''
    // `''` (presente e vazio) = "Todos os tipos". `null` (ausente) é o que
    // fazia o destino cair no default 'violation'.
    expect(kindDe(href)).toBe('')
  })

  it('painel de VIOLAÇÃO por classe mantém o corte de violação no link', async () => {
    getSummary.mockResolvedValue({
      total: 12,
      by_class: [{ class: 'no_helmet', count: 12 }],
      by_camera: [],
    })
    montar()
    const painel = await screen.findByLabelText('Violações por classe')
    const href = within(painel).getByRole('link', { name: /Sem capacete/ }).getAttribute('href') ?? ''
    expect(kindDe(href)).toBe('violation')
    expect(href).toContain('violation_type=no_helmet')
  })

  it('"Aguardando tratativa" leva os TRÊS eixos que produziram o número', async () => {
    getProfile.mockResolvedValue(PERFIL_RVB)
    montar()
    const cartao = await screen.findByLabelText('Aguardando tratativa')
    const href = cartao.querySelector('a')?.getAttribute('href') ?? ''
    // #802: `kind` era `''` (todos os tipos) num cartão que a tela chama de
    // fila de tratativa — e a fila de tratativa (`/epi/acoes`) é de violação.
    expect(kindDe(href)).toBe('violation')
    expect(href).toContain('acknowledged=false')
    const q = new URLSearchParams(href.split('?')[1])
    const dias = (Date.parse(q.get('end_date')!) - Date.parse(q.get('start_date')!)) / 86_400_000
    expect(Math.round(dias)).toBe(30)
  })

  /**
   * PROVA FIM-A-FIM da issue #802: o número que o usuário LÊ no cartão é o
   * número que ele ENCONTRA quando clica, nas duas unidades que o produto
   * imprime (situações e eventos). Dashboard e `Eventos` rodam de verdade,
   * contra o mesmo acervo, ligados só pelo href que o cartão escreveu.
   */
  it('PROVA FIM-A-FIM #802: cartão de tratativa == lista de destino (situações E eventos)', async () => {
    getProfile.mockResolvedValue(PERFIL_RVB)
    const dashboard = montar()
    const cartao = await screen.findByLabelText('Aguardando tratativa')
    await waitFor(() => expect(within(cartao).getByText('3')).toBeTruthy())
    const doCartao = Number(cartao.querySelector('a')?.textContent?.replace(/\D/g, ''))
    const eventosDoCartao = Number(
      (within(cartao).getByText(/evento\(s\)/).textContent ?? '').match(/^\d+/)?.[0],
    )
    const href = cartao.querySelector('a')?.getAttribute('href') ?? ''
    dashboard.unmount()

    render(
      <MemoryRouter initialEntries={[href.replace('/novo', '')]}>
        <Routes>
          <Route path="/epi/eventos" element={<Eventos />} />
        </Routes>
      </MemoryRouter>,
    )
    await screen.findAllByText('Entrada Expedição')
    // "N SITUAÇÕES (…) · M EVENTOS" aparece no cabeçalho E no rodapé da tela.
    const [contador] = await screen.findAllByText(/EVENTOS$/)
    const [situacoesDaLista, eventosDaLista] = (contador.textContent ?? '')
      .match(/\d+/g)!
      .map(Number)

    expect(situacoesDaLista).toBe(doCartao)
    expect(eventosDaLista).toBe(eventosDoCartao)
  })

  it('PROVA FIM-A-FIM: o número do cartão é o número que a lista de destino mostra', async () => {
    getSummary.mockResolvedValue({
      total: 24,
      by_class: [],
      by_camera: [{ camera_id: 'c1', camera_name: 'Entrada Expedição', count: 24 }],
    })
    const dashboard = montar()
    const painel = await screen.findByLabelText('Câmeras com mais eventos')
    const linha = within(painel).getByRole('link', { name: /Entrada Expedição/ })
    // O número que o cartão AFIRMA, lido da tela — não da fixture.
    const doCartao = Number(within(painel).getByText('24').textContent)
    const href = linha.getAttribute('href') ?? ''
    dashboard.unmount()

    // A tela de destino, no MESMO link, contra o MESMO acervo.
    render(
      <MemoryRouter initialEntries={[href.replace('/novo', '')]}>
        <Routes>
          <Route path="/epi/eventos" element={<Eventos />} />
        </Routes>
      </MemoryRouter>,
    )
    // Esperar a LISTA chegar: o rodapé existe desde o primeiro render com
    // "0 EVENTOS", e ler antes da carga daria um zero de loading.
    await screen.findAllByText('Entrada Expedição')
    // O contador é "N SITUAÇÕES · M EVENTOS" quando as duas unidades
    // divergem (o acervo tem uma rajada) e só "M EVENTOS" quando não —
    // o número de EVENTOS é sempre o ÚLTIMO. `replace(/\D/g,'')`, como era,
    // colava os dois num "2324".
    const [contador] = await screen.findAllByText(/EVENTOS$/)
    const daLista = Number((contador.textContent ?? '').match(/\d+/g)!.at(-1))

    expect(daLista).toBe(doCartao)
  })

  it('o link declara o ESCOPO DE MÓDULO que produziu o número (module_code=epi)', async () => {
    getSummary.mockResolvedValue({
      total: 24,
      by_class: [],
      by_camera: [{ camera_id: 'c1', camera_name: 'Entrada Expedição', count: 24 }],
    })
    montar()
    const painel = await screen.findByLabelText('Câmeras com mais eventos')
    const href =
      within(painel).getByRole('link', { name: /Entrada Expedição/ }).getAttribute('href') ?? ''
    // Todo painel desta tela conta com `/v1/events/*?module_code=epi`, que
    // aplica a coluna `module_code` E o escopo de câmera do módulo. Sem
    // declarar o mesmo no link, a lista conta alerta de câmera fora do EPI —
    // 82 linhas a mais que o cartão, medidas no DEV.
    expect(new URLSearchParams(href.split('?')[1]).get('module_code')).toBe('epi')
  })

  it('PROVA FIM-A-FIM: a barra de uma HORA DE CAPTURA abre a lista daquela hora, não a da gravação', async () => {
    // 3 das 24 linhas foram CAPTURADAS dentro da hora que a barra representa;
    // as 24 foram GRAVADAS no mesmo instante, FORA dela (carga em lote).
    getTimeline.mockResolvedValue({
      bucket: 'hour',
      timeline: [{ bucket: HORA_BARRA, count: 3 }],
    })
    const dashboard = montar()
    const painel = await screen.findByLabelText('Eventos por hora')
    const barra = within(painel).getByRole('link', {
      name: /3 evento\(s\) · ver eventos/,
    })
    const href = barra.getAttribute('href') ?? ''
    dashboard.unmount()

    render(
      <MemoryRouter initialEntries={[href.replace('/novo', '')]}>
        <Routes>
          <Route path="/epi/eventos" element={<Eventos />} />
        </Routes>
      </MemoryRouter>,
    )
    await screen.findAllByText('Entrada Expedição')
    // O contador é "N SITUAÇÕES · M EVENTOS" quando as duas unidades
    // divergem (o acervo tem uma rajada) e só "M EVENTOS" quando não —
    // o número de EVENTOS é sempre o ÚLTIMO. `replace(/\D/g,'')`, como era,
    // colava os dois num "2324".
    const [contador] = await screen.findAllByText(/EVENTOS$/)
    const daLista = Number((contador.textContent ?? '').match(/\d+/g)!.at(-1))

    // Pelo eixo da GRAVAÇÃO esta janela de uma hora não alcança linha nenhuma
    // (tudo foi gravado às 14:05Z) — a lista viria vazia embaixo de uma barra
    // que afirma 3. Pelo eixo da CAPTURA, que é o que a barra desenha, são 3.
    expect(daLista).toBe(3)
  })
})

/**
 * SCORE 100 SOBRE O VAZIO (bloco 2, achado 1) — lado da tela.
 *
 * O backend passou a devolver `compliance_rate: null` + `compliance_reason`
 * quando nada chegou na janela (ver
 * `tests/unit/api/test_modules_stats_score_honesto.py`). Aqui trava que a
 * tela DIZ a razão em vez de deixar o "—" mudo, e que não existe caminho
 * em que ela pinte 100 · Conforme sobre 24 h sem dado.
 */
describe('EPI Dashboard — score não afirma mais do que sabe', () => {
  it('sem sinal de ingestão nas 24 h: "—" com a razão, nunca 100 em verde', async () => {
    // A resposta REAL do DEV, já com o conserto do backend aplicado.
    getStats.mockResolvedValue({
      ...STATS_RVB,
      cameras_active: 17,
      cameras_total: 17,
      alerts_today: 0,
      alerts_week: 127,
      compliance_rate: null,
      compliance_reason: 'sem_sinal_no_periodo',
    })
    montar()
    const cartao = await screen.findByLabelText('Score de conformidade')
    expect(within(cartao).getByText('—')).toBeTruthy()
    expect(within(cartao).getByText('Indisponível')).toBeTruthy()
    expect(within(cartao).getByText(/sem dado no período/)).toBeTruthy()
    expect(within(cartao).queryByText('100')).toBeNull()
    expect(within(cartao).queryByText('Conforme')).toBeNull()
  })

  it('sem câmera ativa continua dizendo a razão antiga, não a nova', async () => {
    getStats.mockResolvedValue({
      ...STATS_RVB,
      cameras_active: 0,
      compliance_rate: null,
      compliance_reason: 'sem_cameras_ativas',
    })
    montar()
    const cartao = await screen.findByLabelText('Score de conformidade')
    expect(within(cartao).getByText(/sem câmera ativa para calcular/)).toBeTruthy()
  })

  it('razão desconhecida (backend novo, tela velha) não vira número nem some', async () => {
    getStats.mockResolvedValue({
      ...STATS_RVB,
      compliance_rate: null,
      compliance_reason: 'motivo_que_a_tela_nao_conhece',
    })
    montar()
    const cartao = await screen.findByLabelText('Score de conformidade')
    expect(within(cartao).getByText('—')).toBeTruthy()
    expect(within(cartao).getByText(/não foi possível apurar agora/)).toBeTruthy()
  })

  it('a dica explica que zero em zero hora observada não é conformidade', async () => {
    montar()
    await screen.findByText('87')
    fireEvent.click(screen.getByLabelText('Como o score é calculado'))
    expect(
      await screen.findByText(/ausência de medição, não conformidade/),
    ).toBeTruthy()
  })
})

/**
 * ISSUE #789 — "100 · Conforme" no dia de 66 violações.
 *
 * O score é `100 × (1 − horas-câmera com violação ÷ (câmeras ativas × 24))`.
 * Com as 17 câmeras ativas da RVB o denominador é 408 horas-câmera/dia, então
 * uma hora-câmera com violação vale 0,245 % — e `Math.round` levava qualquer
 * taxa ≥ 99,5 para o inteiro **100**. Medido no acervo do DEV:
 *
 *   25/08 · 66 violações · 1 hora-câmera → 99,8 → a tela imprimia **100**
 *   31/07 · 13 violações · 2 horas-câmera → 99,5 → a tela imprimia **100**
 *
 * E a dica do próprio cartão promete, com estas palavras, que o score aparece
 * "nunca como 100" quando não pôde ser afirmado.
 *
 * Troque `scoreImpresso` de volta por `Math.round` e os dois primeiros casos
 * ficam vermelhos.
 */
describe('#789 — o score nunca AFIRMA 100 num dia que teve violação', () => {
  it('99,8 (66 violações reais em 1 hora-câmera) imprime 99, não 100', async () => {
    getStats.mockResolvedValue({ ...STATS_RVB, compliance_rate: 99.8 })
    montar()
    const cartao = await screen.findByLabelText('Score de conformidade')
    await waitFor(() => expect(within(cartao).getByText('99')).toBeTruthy())
    expect(within(cartao).queryByText('100')).toBeNull()
  })

  it('99,5 — a borda exata do arredondamento — também imprime 99', async () => {
    getStats.mockResolvedValue({ ...STATS_RVB, compliance_rate: 99.5 })
    montar()
    const cartao = await screen.findByLabelText('Score de conformidade')
    await waitFor(() => expect(within(cartao).getByText('99')).toBeTruthy())
    expect(within(cartao).queryByText('100')).toBeNull()
  })

  it('100 exato (zero hora-câmera com violação) continua 100 — o dia bom não vira 99', async () => {
    getStats.mockResolvedValue({ ...STATS_RVB, compliance_rate: 100 })
    montar()
    const cartao = await screen.findByLabelText('Score de conformidade')
    await waitFor(() => expect(within(cartao).getByText('100')).toBeTruthy())
  })

  it('a legenda diz o EIXO do número, não só a janela', async () => {
    getStats.mockResolvedValue({ ...STATS_RVB, compliance_rate: 92.4 })
    montar()
    const cartao = await screen.findByLabelText('Score de conformidade')
    await waitFor(() =>
      expect(within(cartao).getByText(/% das horas-câmera sem violação/)).toBeTruthy(),
    )
  })

  it('a tabela inteira do #789: nenhuma taxa < 100 imprime 100', () => {
    // dia | taxa medida no DEV | o que a tela tem de imprimir
    const medido: [number, number][] = [
      [89.2, 89], // 10/08 · 159 violações
      [92.4, 92], // 07/08 · 152 violações
      [98.0, 98], // 18/08 ·  44 violações
      [98.8, 98], // 12/08 ·  37 violações  (era 99 por arredondamento)
      [99.5, 99], // 31/07 ·  13 violações  (era 100)
      [99.8, 99], // 25/08 ·  66 violações  (era 100)
      [100, 100], // zero hora-câmera com violação
    ]
    for (const [taxa, impresso] of medido) expect(scoreImpresso(taxa)).toBe(impresso)
  })
})
