/**
 * O que esta tela não pode errar: mostrar UUID no lugar do código da peça,
 * inventar o "ponto de inspeção" que não existe no banco, e prometer no botão
 * um ciclo ("recapturada · conforme") que nenhuma rota executa.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const auth = vi.hoisted(() => ({
  can: vi.fn((_p: string) => true),
  hasModule: vi.fn((_m: string) => true),
  isSuperAdmin: false,
}))
vi.mock('../../hooks/useAuth', () => ({ useAuth: () => auth }))
vi.mock('../../services/tenantContext', () => ({
  isInTenantContext: () => false,
  getSessionTokenExpMs: () => null,
}))

const get = vi.fn()
const patch = vi.fn()
vi.mock('../../services/api', () => ({
  api: {
    get: (...a: unknown[]) => get(...a),
    patch: (...a: unknown[]) => patch(...a),
  },
}))

const testarCamera = vi.fn()
vi.mock('../../services/cameraService', () => ({
  cameraService: { test: (...a: unknown[]) => testarCamera(...a) },
}))

import { Qualidade } from './Qualidade'
import { MemoryRouter } from 'react-router-dom'

const navegar = vi.hoisted(() => vi.fn())
vi.mock('react-router-dom', async (orig) => ({
  ...(await orig<typeof import('react-router-dom')>()),
  useNavigate: () => navegar,
}))

/** UUID de peça — o que `/gate/reworks` devolve, e o que NÃO pode ir à tela. */
const PECA_UUID = '7b3f2a10-8c41-4e0b-9f22-1d5a6c7e8b90'

/** Linha REAL de `quality_reworks`: sem status, sem estação, sem piece_number. */
const retrabalho = (extra: Record<string, unknown> = {}) => ({
  id: 'rw-1',
  piece_id: PECA_UUID,
  validation_type: 'v1',
  defect_type: 'distancia',
  defect_description: 'Distância 13,4 mm — fora da tolerância',
  started_at: '2026-08-29T14:12:00Z',
  completed_at: null,
  duration_seconds: null,
  ...extra,
})

const camera = (extra: Record<string, unknown> = {}) => ({
  id: 'cam-est-01',
  name: 'CAM-EST-01',
  location: 'Bancada A',
  status: 'active',
  product_type: 'Anel plano',
  production_order: 'OP 2024-1186',
  last_result: 'ok',
  last_inspection_at: '2026-08-29T14:38:00Z',
  ...extra,
})

interface Respostas {
  reworks?: unknown[]
  pieces?: unknown[]
  stats?: unknown
  summary?: unknown
  cameras?: unknown[]
  stations?: unknown[]
}

function responde(r: Respostas = {}) {
  get.mockImplementation((rota: string) => {
    const p = String(rota)
    if (p.includes('/gate/reworks')) return Promise.resolve({ data: { reworks: r.reworks ?? [] } })
    if (p.includes('/gate/pieces')) return Promise.resolve({ data: { pieces: r.pieces ?? [] } })
    if (p.includes('/gate/stats/rework')) return Promise.resolve({ data: { stats: r.stats ?? {} } })
    if (p.includes('/dashboard/summary')) return Promise.resolve({ data: { summary: r.summary ?? {} } })
    if (p.includes('/gate/stations')) return Promise.resolve({ data: { stations: r.stations ?? [] } })
    if (p.includes('/quality/cameras')) return Promise.resolve({ data: { cameras: r.cameras ?? [] } })
    return Promise.resolve({ data: {} })
  })
}

/** Troca para a aba "Câmeras" — as duas abas vivem no mesmo componente. */
async function abraCameras() {
  fireEvent.click(await screen.findByRole('button', { name: /câmeras/i }))
}

beforeEach(() => {
  get.mockReset()
  patch.mockReset().mockResolvedValue({ data: {} })
  testarCamera.mockReset()
  auth.can.mockReset().mockReturnValue(true)
  auth.hasModule.mockReset().mockReturnValue(true)
})

/**
 * Com Router: desde que as telas irmãs (Gestão/Revisão/Configuração) passaram a
 * existir, as abas NAVEGAM em vez de ficar desabilitadas.
 */
const montar = () => render(<MemoryRouter><Qualidade /></MemoryRouter>)

describe('Qualidade · abas do módulo', () => {
  it('as abas das telas IRMÃS navegam — elas existem agora', async () => {
    // Quando esta tela foi escrita, Gestão/Revisão/Configuração ainda não
    // existiam e as abas ficavam desabilitadas. Existem: a aba tem de levar
    // até lá, e nenhuma pode estar travada.
    responde()
    montar()
    await screen.findByText('Retrabalho', { selector: 'h1' })
    for (const rotulo of ['Dashboard', 'Inspeções', 'Peças', 'Relatórios', 'Config']) {
      const b = screen.getByRole('button', { name: rotulo }) as HTMLButtonElement
      expect(b.disabled, `${rotulo} não deveria estar travada`).toBe(false)
    }
  })

  it('a aba irmã vai para DENTRO do prefixo — /quality existe no front antigo', async () => {
    responde()
    montar()
    await screen.findByText('Retrabalho', { selector: 'h1' })
    fireEvent.click(screen.getByRole('button', { name: 'Config' }))
    expect(navegar).toHaveBeenCalledWith('/novo/quality/configuracao')
  })

  it('"Voltar" é o primeiro link da barra e leva à home do usuário', async () => {
    // Sem barra lateral própria (SEM_BARRA_LATERAL), este é o único jeito de
    // sair do módulo — regra global, ver app/shell/becoSemSaida.test.tsx.
    responde()
    montar()
    const primeiro = (await screen.findAllByRole('link'))[0]
    expect(primeiro.textContent?.trim()).toBe('Voltar')
    // isSuperAdmin: false no dublê → home é a escolha de módulo.
    expect(primeiro.getAttribute('href')).toBe('/novo/modules')
  })
})

describe('Qualidade · Retrabalho', () => {
  it('resolve PEÇA e OP por /gate/pieces — UUID nunca chega à tela', async () => {
    // `/gate/reworks` é SELECT * sem JOIN: só sai piece_id. A tela antiga
    // mostra `piece_id.slice(-8)`, que é um pedaço de UUID.
    responde({
      reworks: [retrabalho()],
      pieces: [{ id: PECA_UUID, piece_number: 'AN-24-0781', work_order: 'OP 2024-1186' }],
    })
    montar()
    expect(await screen.findByText('AN-24-0781')).toBeTruthy()
    expect(screen.getByText('OP 2024-1186')).toBeTruthy()
    expect(screen.queryByText(new RegExp(PECA_UUID.slice(-8), 'i'))).toBeNull()
  })

  it('peça que não resolve vira "—", nunca o UUID', async () => {
    responde({ reworks: [retrabalho()], pieces: [] })
    montar()
    await screen.findByText(/fora da tolerância/)
    expect(screen.queryByText(new RegExp(PECA_UUID, 'i'))).toBeNull()
    expect(screen.getAllByText('—').length).toBeGreaterThan(0)
  })

  it('a coluna do desenho "PONTO" vira VALIDAÇÃO — P4 não existe no banco', async () => {
    responde({ reworks: [retrabalho({ validation_type: 'v2' })] })
    montar()
    expect(await screen.findByText('VALIDAÇÃO')).toBeTruthy()
    expect(screen.getByText('V2')).toBeTruthy()
    expect(screen.queryByText('PONTO')).toBeNull()
  })

  it('o botão diz o que a rota FAZ — não promete "recapturada · conforme"', async () => {
    responde({ reworks: [retrabalho()] })
    montar()
    const b = (await screen.findByRole('button', { name: /concluir retrabalho/i })) as HTMLButtonElement
    expect(b.disabled).toBe(false)
    expect(b.title).toMatch(/não re-inspeciona|recaptura acontece na estação/i)
    expect(screen.queryByRole('button', { name: /marcar recapturada/i })).toBeNull()
    expect(screen.queryByText(/RECAPTURADA · CONFORME/i)).toBeNull()
  })

  it('concluir chama PATCH .../complete e recarrega a fila', async () => {
    responde({ reworks: [retrabalho()] })
    montar()
    fireEvent.click(await screen.findByRole('button', { name: /concluir retrabalho/i }))
    await waitFor(() =>
      expect(patch).toHaveBeenCalledWith('/v1/quality/gate/reworks/rw-1/complete'),
    )
  })

  it('retrabalho concluído mostra CONCLUÍDO e não oferece a ação de novo', async () => {
    // `quality_reworks` não tem coluna de status: os dois estados derivam de
    // completed_at. "AGUARDANDO" do desenho não tem representação nenhuma.
    responde({
      reworks: [retrabalho({ completed_at: '2026-08-29T14:20:00Z', duration_seconds: 132 })],
    })
    montar()
    expect(await screen.findByText('CONCLUÍDO')).toBeTruthy()
    expect(screen.getByText('2:12')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /concluir retrabalho/i })).toBeNull()
    expect(screen.queryByText('AGUARDANDO')).toBeNull()
  })

  it('os filtros do desenho ficam desabilitados — a rota não lê data nem estação', async () => {
    responde({ reworks: [retrabalho()] })
    montar()
    const periodo = (await screen.findByLabelText('Período')) as HTMLSelectElement
    const estacao = screen.getByLabelText('Estação') as HTMLSelectElement
    expect(periodo.disabled).toBe(true)
    expect(estacao.disabled).toBe(true)
    expect(periodo.title).toMatch(/date_from|filtro de data/i)
    // A opção declara o recorte REAL da lista, não "Hoje".
    expect(screen.getByText('Todo o período')).toBeTruthy()
  })

  it('tempo médio é do período inteiro, e não inventa a "meta 2:30" do desenho', async () => {
    responde({
      reworks: [retrabalho()],
      stats: { by_validation: { v1: 3, v2: 1 }, avg_rework_duration_seconds: 132.4 },
    })
    montar()
    expect(await screen.findByText('2:12')).toBeTruthy()
    expect(screen.getByText(/todo o período · só ciclos concluídos/i)).toBeTruthy()
    expect(screen.queryByText(/meta 2:30/i)).toBeNull()
    // by_validation é DICT — a tela antiga chamava .slice()/.map() e não
    // renderizava cartão nenhum.
    expect(screen.getByText(/V1 3 · V2 1/)).toBeTruthy()
  })

  it('sem o módulo quality, os cartões do dia dizem que não têm fonte — e não tomam 403', async () => {
    auth.hasModule.mockReturnValue(false)
    responde({ reworks: [retrabalho()] })
    montar()
    expect((await screen.findAllByText('EM RETRABALHO')).length).toBeGreaterThan(0)
    expect(get.mock.calls.some(([r]) => String(r).includes('/dashboard/summary'))).toBe(false)
    expect(screen.getAllByText(/módulo Qualidade não está habilitado/i).length).toBeGreaterThan(0)
  })

  it('vazio é honesto e não promete abertura de retrabalho pela tela', async () => {
    responde({ reworks: [] })
    montar()
    expect(await screen.findByText('Nada em retrabalho')).toBeTruthy()
    expect(screen.queryByRole('table')).toBeNull()
  })

  it('erro mostra a rota e o retry refaz a chamada', async () => {
    get.mockRejectedValue(new Error('timeout'))
    montar()
    expect(await screen.findByText(/GET \/api\/v1\/quality\/gate\/reworks · timeout/)).toBeTruthy()
    responde({ reworks: [retrabalho()] })
    fireEvent.click(screen.getByRole('button', { name: /tentar novamente/i }))
    expect(await screen.findByText(/fora da tolerância/)).toBeTruthy()
  })
})

describe('Qualidade · Câmeras das estações', () => {
  it('resolve a estação por quality_stations.camera_ids — a rota de câmeras não serve station', async () => {
    responde({
      cameras: [camera()],
      stations: [{ id: 'st-1', station_code: 'bench_a', name: 'Estação 1', camera_ids: ['cam-est-01'] }],
    })
    montar()
    await abraCameras()
    expect((await screen.findAllByText('CAM-EST-01')).length).toBeGreaterThan(0)
    expect(screen.getAllByText('Estação 1').length).toBeGreaterThan(0)
  })

  it('câmera sem bancada diz isso — não inventa "Estação 1"', async () => {
    responde({ cameras: [camera()], stations: [] })
    montar()
    await abraCameras()
    expect((await screen.findAllByText('Sem bancada vinculada')).length).toBeGreaterThan(0)
  })

  it('o estado é de CADASTRO, não liveness: nada de ONLINE/INSTÁVEL', async () => {
    responde({ cameras: [camera({ status: 'active' })] })
    montar()
    await abraCameras()
    expect((await screen.findAllByText('ATIVA')).length).toBeGreaterThan(0)
    expect(screen.queryByText(/INSTÁVEL/i)).toBeNull()
    expect(screen.queryByText(/^ONLINE$/i)).toBeNull()
  })

  it('não mostra latência P95, papel nem zona de captura — nada disso tem fonte', async () => {
    responde({ cameras: [camera()] })
    montar()
    await abraCameras()
    await screen.findByText('Área')
    expect(screen.queryByText(/Latência P95/i)).toBeNull()
    expect(screen.queryByText(/ZONA DE CAPTURA DEMARCADA/i)).toBeNull()
    expect(screen.queryByText(/box edge/i)).toBeNull()
  })

  it('"Config de estações" leva à Configuração, dentro do prefixo', async () => {
    // Ficava desabilitado porque a Configuração de Qualidade não existia no
    // front novo. Existe: o botão navega — e por `rotaNova`, porque `/quality`
    // também é rota do front ANTIGO e um caminho cru levaria para lá calado.
    responde()
    montar()
    fireEvent.click(await screen.findByRole('button', { name: /câmeras/i }))
    fireEvent.click(await screen.findByRole('button', { name: /config de estações/i }))
    expect(navegar).toHaveBeenCalledWith('/novo/quality/configuracao')
  })

  it('Testar conexão exibe os 5 checks reais — sem ms e sem FPS inventados', async () => {
    const check = (status: string, message: string) => ({ status, message })
    testarCamera.mockResolvedValue({
      camera_id: 'cam-est-01',
      success: true,
      error: null,
      suggestion: null,
      checks: {
        url_format: check('ok', 'formato válido'),
        host_reachable: check('ok', 'host respondeu'),
        port_open: check('ok', 'porta 554 aberta'),
        rtsp_response: check('ok', 'handshake ok'),
        stream_available: check('ok', 'stream disponível'),
      },
    })
    responde({ cameras: [camera()] })
    montar()
    await abraCameras()
    fireEvent.click(await screen.findByRole('button', { name: /testar conexão/i }))
    expect(await screen.findByText('Handshake RTSP')).toBeTruthy()
    expect(screen.getByText('Conexão estabelecida')).toBeTruthy()
    expect(testarCamera).toHaveBeenCalledWith('cam-est-01')
    expect(screen.queryByText(/MS$/)).toBeNull()
    expect(screen.queryByText(/FPS/)).toBeNull()
  })

  it('sem cameras:test o botão não aparece', async () => {
    auth.can.mockImplementation((p: string) => p !== 'cameras:test')
    responde({ cameras: [camera()] })
    montar()
    await abraCameras()
    await screen.findAllByText('CAM-EST-01')
    expect(screen.queryByRole('button', { name: /testar conexão/i })).toBeNull()
  })

  it('lista vazia nomeia a rota — é a lacuna conhecida do módulo', async () => {
    responde({ cameras: [] })
    montar()
    await abraCameras()
    expect(await screen.findByText('Nenhuma câmera de estação')).toBeTruthy()
    expect(screen.getByText(/GET \/api\/v1\/quality\/cameras · 0 câmeras/)).toBeTruthy()
  })

  it('erro na aba de câmeras mostra a rota e tenta de novo', async () => {
    responde({ cameras: [camera()] })
    montar()
    await screen.findByText('Retrabalho', { selector: 'h1' })
    get.mockRejectedValue(new Error('timeout'))
    await abraCameras()
    expect(await screen.findByText(/GET \/api\/v1\/quality\/cameras · timeout/)).toBeTruthy()
  })
})

describe('Qualidade · gate de permissão', () => {
  it('sem quality:write, "Concluir retrabalho" fica travado e diz por quê', async () => {
    // A chave nasceu em 29/08 justamente porque esta tela apareceu sem gate.
    auth.can.mockImplementation((p: string) => p !== 'quality:write')
    responde({ reworks: [retrabalho()] })
    montar()
    const b = (await screen.findByRole('button', { name: /concluir retrabalho/i })) as HTMLButtonElement
    expect(b.disabled).toBe(true)
    expect(b.title).toMatch(/quality:write/)
  })

  it('com quality:write o botão funciona', async () => {
    auth.can.mockReturnValue(true)
    responde({ reworks: [retrabalho()] })
    montar()
    const b = (await screen.findByRole('button', { name: /concluir retrabalho/i })) as HTMLButtonElement
    expect(b.disabled).toBe(false)
  })
})
