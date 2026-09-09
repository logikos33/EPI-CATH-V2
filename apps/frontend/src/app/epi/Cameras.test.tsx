/**
 * O que esta tela não pode perder — e por quê.
 *
 *  · **Dado real ou vazio honesto.** A lista vem da API; lista vazia diz
 *    "nenhuma câmera cadastrada" e falha diz que falhou. O que não pode
 *    acontecer é a tela afirmar "nenhuma câmera" quando na verdade a chamada
 *    quebrou — num tenant de 28 câmeras isso é uma mentira operacional.
 *  · **Estado = cor + ícone + palavra.** Câmera offline não pode ser só um
 *    ponto vermelho: quem não distingue verde de vermelho ainda tem de operar.
 *  · **Permissão gate a AÇÃO, não o texto.** Sem `cameras:write` não há
 *    "Adicionar câmera"; sem `cameras:test` não há "Testar conexão". As
 *    permissões são as reais de `core/permissions.py`.
 *  · **Aba Escopo (delta §2 item 8) em lote.** É a regressão mais cara desta
 *    família: a versão por câmera do `model-config` estourava o pool de
 *    conexões da API nas 28 câmeras da RVB. UMA chamada por MÓDULO — este
 *    teste conta as chamadas, não confia na intenção.
 *  · **Sites são de outro domínio.** A listagem de sites é restrita a
 *    administradores; um 403 lá não pode derrubar a lista de câmeras.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { Cameras, estadoDaCamera } from './Cameras'

// ── dublês ───────────────────────────────────────────────────────────────────

const listar = vi.fn()
const testar = vi.fn()
const arquivar = vi.fn()
const excluir = vi.fn()
const apiGet = vi.fn()
const apiPost = vi.fn()
const listarSites = vi.fn()
const saudeSites = vi.fn()
const getHealthContext = vi.fn()
const patchConfig = vi.fn()
const permissoes = new Set<string>()
let isSuperAdmin = false

vi.mock('../../services/cameraService', () => ({
  cameraService: {
    list: (...a: unknown[]) => listar(...a),
    test: (...a: unknown[]) => testar(...a),
    archive: (...a: unknown[]) => arquivar(...a),
    remove: (...a: unknown[]) => excluir(...a),
    restore: vi.fn(),
    start: vi.fn(),
    stop: vi.fn(),
    // A prévia usa o snapshot de triagem — nunca acende a câmera.
    getSnapshot: vi.fn().mockResolvedValue({ status: 'none', url: null, captured_at: null, error_reason: null }),
    refreshSnapshot: vi.fn().mockResolvedValue({ status: 'none', queued: false }),
    getHealthContext: (...a: unknown[]) => getHealthContext(...a),
    patchConfig: (...a: unknown[]) => patchConfig(...a),
  },
}))

vi.mock('../../services/edgeService', () => ({
  edgeService: {
    listSites: (...a: unknown[]) => listarSites(...a),
    getSitesHealth: (...a: unknown[]) => saudeSites(...a),
    updateSite: vi.fn(),
  },
}))

vi.mock('../../services/api', () => ({
  api: {
    get: (...a: unknown[]) => apiGet(...a),
    post: (...a: unknown[]) => apiPost(...a),
  },
}))

vi.mock('../../hooks/useAuth', () => ({
  useAuth: () => ({ can: (p: string) => permissoes.has(p), isSuperAdmin }),
}))

// ── dados no formato real do backend (RVB Isolantes — Blumenau) ──────────────

const SITE_ID = 'site-rvb-01'

const CAM_01 = {
  id: 'cam-01',
  name: 'CAM-01 Doca Norte',
  location: 'Doca Norte',
  manufacturer: 'intelbras',
  host: '10.0.3.11',
  port: 554,
  username: 'admin',
  channel: 1,
  is_active: true,
  stream_status: 'active',
  active_module: 'epi',
  fps_target: 5,
  last_seen: new Date().toISOString(),
  site_id: SITE_ID,
  created_at: '2026-08-01T10:00:00Z',
}

const CAM_04 = {
  ...CAM_01,
  id: 'cam-04',
  name: 'CAM-04 Expedição',
  location: 'Expedição',
  host: '10.0.3.14',
  stream_status: 'offline',
  fps_target: 4,
  last_seen: '2026-08-01T10:00:00Z',
}

const SITE = {
  id: SITE_ID,
  tenant_id: 't1',
  name: 'RVB Isolantes — Blumenau',
  description: null,
  location: 'Blumenau/SC',
  deployment_mode: 'edge' as const,
  status: 'active' as const,
  created_at: null,
  created_by: null,
}

const SAUDE = {
  site_id: SITE_ID,
  site_name: SITE.name,
  status: 'healthy' as const,
  last_heartbeat: new Date().toISOString(),
  fps: 12,
  cameras_online: 1,
  cameras_total: 2,
  gpu_temp_c: 61,
  decode_fps: 28,
}

const MODELO = { id: 'mod-1', name: 'RVB EPI v3', framework: 'yolox', r2_onnx_key: 'k.onnx', is_active: true, module_code: 'epi' }

// ── contexto de saúde (aba Desempenho, WS10) ─────────────────────────────────

const CTX_COM_TELEMETRIA = {
  has_telemetry: true,
  site_id: SITE_ID,
  derived_status: 'healthy' as const,
  received_at: new Date().toISOString(),
  metrics: {
    gpu_pct: 46, gpu_mem_pct: 52, cpu_pct: 31, inference_fps: 4.9,
    inference_latency_ms: 120, queue_depth: 2, cameras_online: 1,
    cameras_total: 2, gpu_temp_c: 61, decode_pct: 38,
  },
  fps_demand_total: 112,
  cameras_active_count: 2,
}

const CTX_SEM_TELEMETRIA = {
  has_telemetry: false,
  site_id: SITE_ID,
  derived_status: null,
  received_at: null,
  metrics: null,
  fps_demand_total: 112,
  cameras_active_count: 2,
}

/** `api.get` roteando por URL — é assim que a aba Escopo conversa com a API. */
function apiDeEscopo() {
  apiGet.mockImplementation((url: string) => {
    if (url === '/v1/models') return Promise.resolve({ success: true, data: { models: [MODELO] } })
    if (url.startsWith('/v1/models/')) {
      return Promise.resolve({
        success: true,
        data: {
          model: MODELO,
          lineage: { dataset_version: { class_distribution: { capacete: 12, colete: 9, __sem_suporte_treino__: ['colete'] } } },
        },
      })
    }
    if (url.startsWith('/cameras/model-config')) {
      return Promise.resolve({ success: true, data: { deployments: {} } })
    }
    return Promise.reject(new Error(`URL inesperada: ${url}`))
  })
}

function montar() {
  return render(<MemoryRouter><Cameras /></MemoryRouter>)
}

/** O nome da selecionada aparece na lista E no cabeçalho do detalhe — por isso
 * `findAllByText`; esperar por um só quebraria por ambiguidade, não por bug. */
const esperarCarregado = () => screen.findAllByText('CAM-01 Doca Norte')

beforeEach(() => {
  vi.clearAllMocks()
  permissoes.clear()
  isSuperAdmin = false
  ;['cameras:read', 'cameras:write', 'cameras:test', 'cameras:control', 'cameras:configure',
    'cameras:delete']
    .forEach((p) => permissoes.add(p))
  excluir.mockResolvedValue(undefined)
  listar.mockResolvedValue([CAM_01, CAM_04])
  listarSites.mockResolvedValue([SITE])
  saudeSites.mockResolvedValue([SAUDE])
  apiGet.mockRejectedValue(new Error('não deveria ser chamada'))
  getHealthContext.mockResolvedValue(CTX_COM_TELEMETRIA)
  patchConfig.mockResolvedValue({ ...CAM_01, propagation: { queued: true, reason: null } })
})

// ── dado real / vazio honesto / erro ─────────────────────────────────────────

describe('dado real, vazio honesto, erro que se assume', () => {
  it('mostra as câmeras que a API devolveu — nenhuma inventada', async () => {
    montar()
    expect(await esperarCarregado()).toBeTruthy()
    expect(screen.getByText('CAM-04 Expedição')).toBeTruthy()
  })

  it('lista vazia é vazio, com o convite do desenho', async () => {
    listar.mockResolvedValue([])
    montar()
    expect(await screen.findByText('Nenhuma câmera cadastrada')).toBeTruthy()
  })

  it('falha NÃO vira "nenhuma câmera" — diz que falhou e oferece tentar de novo', async () => {
    listar.mockRejectedValueOnce(new Error('TIMEOUT 10 S'))
    montar()
    expect(await screen.findByText('Não foi possível carregar')).toBeTruthy()
    expect(screen.queryByText('Nenhuma câmera cadastrada')).toBeNull()

    fireEvent.click(screen.getByText('Tentar novamente'))
    expect(await esperarCarregado()).toBeTruthy()
  })

  it('sites restritos (403) não derrubam a lista de câmeras', async () => {
    listarSites.mockRejectedValue(new Error('403 Forbidden'))
    saudeSites.mockRejectedValue(new Error('403 Forbidden'))
    montar()
    expect(await esperarCarregado()).toBeTruthy()

    fireEvent.click(screen.getByRole('tab', { name: 'Sites' }))
    expect(await screen.findByText(/403 Forbidden/)).toBeTruthy()
  })
})

// ── estado = cor + ícone + palavra ───────────────────────────────────────────

describe('estado nunca é só uma cor', () => {
  it('câmera offline traz a PALAVRA, não só um ponto vermelho', async () => {
    montar()
    await screen.findByText('CAM-04 Expedição')
    expect(screen.getAllByText('PARADA').length).toBeGreaterThan(0)
    expect(screen.getAllByText('ONLINE').length).toBeGreaterThan(0)
  })

  it('arquivada, falha e parada são estados distintos — e nomeados', () => {
    expect(estadoDaCamera({ is_active: false, stream_status: 'active' }).palavra).toBe('ARQUIVADA')
    expect(estadoDaCamera({ is_active: true, stream_status: 'error' }).palavra).toBe('FALHA')
    expect(estadoDaCamera({ is_active: true, stream_status: 'inactive' }).palavra).toBe('PARADA')
    expect(estadoDaCamera({ is_active: true, stream_status: 'online' }).palavra).toBe('ONLINE')
  })
})

// ── permissões reais ─────────────────────────────────────────────────────────

describe('permissões (as reais de core/permissions.py)', () => {
  it('sem cameras:write não há como adicionar nem editar', async () => {
    permissoes.delete('cameras:write')
    montar()
    await esperarCarregado()
    expect(screen.queryByText('Adicionar câmera')).toBeNull()
    expect(screen.queryByText('Editar')).toBeNull()
  })

  it('sem cameras:test não há "Testar conexão"', async () => {
    permissoes.delete('cameras:test')
    montar()
    await esperarCarregado()
    expect(screen.queryByText('Testar conexão')).toBeNull()
  })

  it('sem cameras:control não há iniciar/parar monitoramento', async () => {
    permissoes.delete('cameras:control')
    montar()
    await esperarCarregado()
    expect(screen.queryByText('Parar monitoramento')).toBeNull()
    expect(screen.queryByText('Iniciar monitoramento')).toBeNull()
  })
})

// ── teste de conectividade ───────────────────────────────────────────────────

describe('teste de conexão', () => {
  it('mostra o passo a passo com as mensagens que o backend devolveu', async () => {
    testar.mockResolvedValue({
      camera_id: CAM_01.id,
      success: false,
      error: 'Credenciais incorretas.',
      suggestion: 'Confira usuário e senha da câmera.',
      checks: {
        url_format: { status: 'ok', message: 'rtsp válido' },
        host_reachable: { status: 'ok', message: 'ping 4 ms' },
        port_open: { status: 'ok', message: '554 aberta' },
        rtsp_response: { status: 'error', message: '401 Unauthorized' },
        stream_available: { status: 'pending', message: 'não executado' },
      },
    })
    montar()
    await esperarCarregado()
    fireEvent.click(screen.getByText('Testar conexão'))

    expect(await screen.findByText('401 Unauthorized')).toBeTruthy()
    expect(screen.getByText('Handshake RTSP')).toBeTruthy()
    expect(screen.getByText('Credenciais incorretas.')).toBeTruthy()
    expect(screen.getByText('Confira usuário e senha da câmera.')).toBeTruthy()
  })

  it('erro de rede no próprio teste aparece na tela, não some', async () => {
    testar.mockRejectedValue(new Error('Failed to fetch'))
    montar()
    await esperarCarregado()
    fireEvent.click(screen.getByText('Testar conexão'))
    expect(await screen.findByText('Failed to fetch')).toBeTruthy()
  })
})

// ── endereço com senha oculta ────────────────────────────────────────────────

it('o endereço RTSP nunca exibe senha', async () => {
  montar()
  await esperarCarregado()
  expect(screen.getByText(/rtsp:\/\/admin:\*\*\*\*@10\.0\.3\.11:554/)).toBeTruthy()
})

// ── fabricante no painel de detalhe (§15 paridade) ───────────────────────────

it('mostra o fabricante da câmera sem precisar abrir a edição', async () => {
  montar()
  await esperarCarregado()
  expect(screen.getByText('Fabricante')).toBeTruthy()
  expect(screen.getByText('intelbras')).toBeTruthy()
})

// ── aba Saúde ────────────────────────────────────────────────────────────────

describe('aba Saúde', () => {
  it('não inventa uptime nem chama de "FPS" o que é alvo configurado', async () => {
    montar()
    await esperarCarregado()
    fireEvent.click(screen.getByRole('tab', { name: 'Saúde' }))

    expect(await screen.findByText('Uptime 7d')).toBeTruthy()
    expect(screen.getByText('FPS alvo')).toBeTruthy()
    const linha = screen.getByRole('cell', { name: 'CAM-01 Doca Norte' }).closest('tr')!
    // Uptime por câmera não existe na API — a célula fica vazia, não zerada.
    expect(within(linha).getAllByText('—').length).toBeGreaterThan(0)
  })
})

// ── aba Sites ────────────────────────────────────────────────────────────────

describe('aba Sites', () => {
  it('traz o site real e a telemetria que o edge mandou', async () => {
    montar()
    await esperarCarregado()
    fireEvent.click(screen.getByRole('tab', { name: 'Sites' }))

    expect(await screen.findByText('EDGE SAUDÁVEL')).toBeTruthy()
    expect(screen.getByText('1/2')).toBeTruthy()
    expect(screen.getByText('61')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Edge', pressed: true })).toBeTruthy()
  })

  it('sem cameras:configure o modo do site fica travado', async () => {
    permissoes.delete('cameras:configure')
    montar()
    await esperarCarregado()
    fireEvent.click(screen.getByRole('tab', { name: 'Sites' }))

    const cloud = await screen.findByRole('button', { name: 'Cloud-only' })
    expect((cloud as HTMLButtonElement).disabled).toBe(true)
  })
})

// ── aba Escopo — delta §2 item 8 ─────────────────────────────────────────────

describe('aba Escopo por câmera (delta §2 item 8)', () => {
  it('a aba existe — a funcionalidade não se perdeu na migração', async () => {
    apiDeEscopo()
    montar()
    await esperarCarregado()
    expect(screen.getByRole('tab', { name: 'Escopo' })).toBeTruthy()
  })

  it('pede o model-config EM LOTE: uma vez por MÓDULO, não uma por câmera', async () => {
    apiDeEscopo()
    montar()
    await esperarCarregado()
    fireEvent.click(screen.getByRole('tab', { name: 'Escopo' }))

    await waitFor(() => {
      const urls = apiGet.mock.calls.map((c) => String(c[0]))
      const emLote = urls.filter((url) => url.startsWith('/cameras/model-config'))
      // Duas câmeras, um módulo → UMA chamada. Uma por câmera estourava o pool
      // de conexões da API nas 28 da RVB.
      expect(emLote).toHaveLength(1)
      expect(emLote[0]).toBe('/cameras/model-config?module=epi')
    })
    // E nunca a variante por câmera.
    expect(apiGet.mock.calls.map((c) => String(c[0])).some((url) => /\/cameras\/[^/]+\/model-config/.test(url))).toBe(false)
  })

  it('só oferece as classes que o modelo declara — menos as sem suporte a treino', async () => {
    apiDeEscopo()
    montar()
    await esperarCarregado()
    fireEvent.click(screen.getByRole('tab', { name: 'Escopo' }))

    const seletor = await screen.findByLabelText('Modelo da câmera CAM-01 Doca Norte')
    fireEvent.change(seletor, { target: { value: MODELO.id } })

    expect(await screen.findByLabelText('Classe capacete em CAM-01 Doca Norte')).toBeTruthy()
    // 'colete' está em __sem_suporte_treino__ — o detector não a emite.
    expect(screen.queryByLabelText('Classe colete em CAM-01 Doca Norte')).toBeNull()
  })

  it('salvar grava module_code e config.classes — o contrato do worker', async () => {
    apiDeEscopo()
    apiPost.mockResolvedValue({
      success: true,
      data: { deployment: { id: 'd1', model_id: MODELO.id, camera_id: CAM_01.id, module_code: 'epi', config: { classes: ['capacete'] }, status: 'active', created_at: '2026-08-27T12:00:00Z' } },
    })
    montar()
    await esperarCarregado()
    fireEvent.click(screen.getByRole('tab', { name: 'Escopo' }))

    const seletor = await screen.findByLabelText('Modelo da câmera CAM-01 Doca Norte')
    fireEvent.change(seletor, { target: { value: MODELO.id } })
    fireEvent.click(await screen.findByLabelText('Salvar escopo de CAM-01 Doca Norte'))

    await waitFor(() => {
      expect(apiPost).toHaveBeenCalledWith(`/cameras/${CAM_01.id}/model-config`, {
        model_id: MODELO.id,
        module_code: 'epi',
        config: { classes: ['capacete'] },
      })
    })
  })

  it('sem cameras:configure a aba é somente leitura', async () => {
    permissoes.delete('cameras:configure')
    apiDeEscopo()
    montar()
    await esperarCarregado()
    fireEvent.click(screen.getByRole('tab', { name: 'Escopo' }))

    const seletor = await screen.findByLabelText('Modelo da câmera CAM-01 Doca Norte')
    expect((seletor as HTMLSelectElement).disabled).toBe(true)
  })

  it('falha ao carregar o escopo NÃO vira "nenhuma câmera ativa"', async () => {
    apiGet.mockRejectedValue(new Error('connection pool exhausted'))
    montar()
    await esperarCarregado()
    fireEvent.click(screen.getByRole('tab', { name: 'Escopo' }))

    expect(await screen.findByText('Não foi possível carregar o escopo')).toBeTruthy()
    expect(screen.queryByText('Nenhuma câmera ativa no tenant.')).toBeNull()
  })

  // Repro do achado na prova DEV: /novo/epi/cameras aba Escopo tem sua PRÓPRIA
  // cópia da UI de dropdown (AbaEscopo), separada de CameraModelScope — o
  // rebranding (política F5-LEVE, cliente NUNCA vê stack interno) cobriu o
  // componente antigo mas não este. Nome interno cru ("YOLO26 ...") aparecia
  // no <option> para papel NÃO-superadmin.
  it('VAZAMENTO: dropdown de modelo não mostra name/framework internos para não-superadmin', async () => {
    isSuperAdmin = false
    const modeloYolo = { id: 'mod-yolo', name: 'YOLO26 yolo26n - Job 0307e2b1', framework: 'yolox', r2_onnx_key: 'k1.onnx', is_active: true, module_code: 'epi' }
    const modeloRfdetr = { id: 'mod-rfdetr', name: 'YOLO26 rfdetr - Job 9f8e7d6c', framework: 'rfdetr', r2_onnx_key: 'k2.onnx', is_active: true, module_code: 'epi' }
    apiGet.mockImplementation((url: string) => {
      if (url === '/v1/models') return Promise.resolve({ success: true, data: { models: [modeloYolo, modeloRfdetr] } })
      if (url.startsWith('/v1/models/')) {
        return Promise.resolve({ success: true, data: { model: modeloYolo, lineage: { dataset_version: { class_distribution: {} } } } })
      }
      if (url.startsWith('/cameras/model-config')) return Promise.resolve({ success: true, data: { deployments: {} } })
      return Promise.reject(new Error(`URL inesperada: ${url}`))
    })
    montar()
    await esperarCarregado()
    fireEvent.click(screen.getByRole('tab', { name: 'Escopo' }))

    const seletor = await screen.findByLabelText('Modelo da câmera CAM-01 Doca Norte') as HTMLSelectElement
    const textos = Array.from(seletor.options).map((o) => o.textContent).join(' | ')
    expect(textos).not.toMatch(/yolo|rf-?detr|onnx/i)
  })

  it('superadmin: dropdown continua mostrando o nome interno do modelo — não regressão da engenharia', async () => {
    isSuperAdmin = true
    const modeloYolo = { id: 'mod-yolo', name: 'YOLO26 yolo26n - Job 0307e2b1', framework: 'yolox', r2_onnx_key: 'k1.onnx', is_active: true, module_code: 'epi' }
    apiGet.mockImplementation((url: string) => {
      if (url === '/v1/models') return Promise.resolve({ success: true, data: { models: [modeloYolo] } })
      if (url.startsWith('/v1/models/')) {
        return Promise.resolve({ success: true, data: { model: modeloYolo, lineage: { dataset_version: { class_distribution: {} } } } })
      }
      if (url.startsWith('/cameras/model-config')) return Promise.resolve({ success: true, data: { deployments: {} } })
      return Promise.reject(new Error(`URL inesperada: ${url}`))
    })
    montar()
    await esperarCarregado()
    fireEvent.click(screen.getByRole('tab', { name: 'Escopo' }))

    const seletor = await screen.findByLabelText('Modelo da câmera CAM-01 Doca Norte') as HTMLSelectElement
    const opt = Array.from(seletor.options).find((o) => o.value === 'mod-yolo')!
    expect(opt.textContent).toBe(modeloYolo.name)
  })
})

// ── aba Desempenho — 5ª aba (handoff-v2 Main.dc.html) ────────────────────────

describe('aba Desempenho (handoff-v2 Main.dc.html)', () => {
  it('renderiza com as métricas reais do health-context', async () => {
    montar()
    await esperarCarregado()
    fireEvent.click(screen.getByRole('tab', { name: 'Desempenho' }))

    expect(await screen.findByText('GPU')).toBeTruthy()
    expect(screen.getByText('46%')).toBeTruthy()
    expect(screen.getByText('FILA')).toBeTruthy()
    expect(screen.getByText('2')).toBeTruthy()
    expect(getHealthContext).toHaveBeenCalledWith(CAM_01.id)
  })

  it('sem telemetria é honesto — não inventa números', async () => {
    getHealthContext.mockResolvedValue(CTX_SEM_TELEMETRIA)
    montar()
    await esperarCarregado()
    fireEvent.click(screen.getByRole('tab', { name: 'Desempenho' }))

    expect(await screen.findByText('SEM TELEMETRIA')).toBeTruthy()
    expect(screen.queryByText('GPU')).toBeNull()
    // fps_demand_total não depende de telemetria — continua honesto mostrá-lo.
    expect(screen.getByText('112')).toBeTruthy()
  })

  it('o payload do patchConfig tem exatamente o que mudou', async () => {
    montar()
    await esperarCarregado()
    fireEvent.click(screen.getByRole('tab', { name: 'Desempenho' }))
    await screen.findByText('GPU')

    fireEvent.click(screen.getByRole('button', { name: '10 fps' }))
    fireEvent.click(screen.getByRole('button', { name: 'Salvar configuração' }))

    await waitFor(() => {
      expect(patchConfig).toHaveBeenCalledWith(CAM_01.id, { fps_target: 10, quality_preset: 'medium' })
    })
  })

  it('aviso âmbar aparece quando coleta está em alta e a operação não — e some ao mudar a coleta', async () => {
    montar()
    await esperarCarregado()
    fireEvent.click(screen.getByRole('tab', { name: 'Desempenho' }))

    // CAM_01: collection_subtype ausente (default 0=Principal) e
    // live_view_subtype ausente (default 1=substream) — desalinhado por padrão.
    expect(await screen.findByText(/Coleta em alta com operação em baixa/)).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: /Substream \(704×480\)/ }))
    expect(screen.queryByText(/Coleta em alta com operação em baixa/)).toBeNull()
  })

  it('sem cameras:configure os controles ficam travados', async () => {
    permissoes.delete('cameras:configure')
    montar()
    await esperarCarregado()
    fireEvent.click(screen.getByRole('tab', { name: 'Desempenho' }))
    await screen.findByText('GPU')

    expect((screen.getByRole('button', { name: '5 fps' }) as HTMLButtonElement).disabled).toBe(true)
    expect((screen.getByRole('button', { name: 'Salvar configuração' }) as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByText(/Somente leitura/)).toBeTruthy()
  })
})

// ── Excluir câmera ───────────────────────────────────────────────────────────
//
// A tela que faltava: só havia "Arquivar", e a arquivada continuava aparecendo
// no Ao Vivo para sempre. Excluir é DESTRUTIVO e sem desfazer pela UI — por
// isso todo o contrato aqui é sobre o pedido de confirmação e sobre o que a
// confirmação AFIRMA que acontece.

describe('excluir câmera', () => {
  it('o clique não exclui nada: abre a confirmação, que diz o que se perde e o que não', async () => {
    montar()
    await esperarCarregado()

    fireEvent.click(screen.getByRole('button', { name: /Excluir/ }))

    // Nada foi chamado só por abrir o diálogo.
    expect(excluir).not.toHaveBeenCalled()
    expect(screen.getByText('Excluir câmera')).toBeTruthy()

    const aviso = screen.getByText(/sai do sistema/)
    expect(aviso.textContent).toContain('CAM-01 Doca Norte')
    // O que NÃO é apagado — a promessa que o backend cumpre com exclusão
    // lógica (migration 138). Se um dia virar DELETE físico, esta frase é a
    // que passa a mentir.
    expect(aviso.textContent).toContain('evidências')
    expect(aviso.textContent).toContain('Não dá para desfazer')
  })

  it('confirmando, chama a exclusão da câmera selecionada e recarrega a lista', async () => {
    montar()
    await esperarCarregado()
    expect(listar).toHaveBeenCalledTimes(1)

    fireEvent.click(screen.getByRole('button', { name: /Excluir/ }))
    listar.mockResolvedValue([CAM_04])
    fireEvent.click(screen.getByRole('button', { name: 'Excluir para sempre' }))

    await waitFor(() => expect(excluir).toHaveBeenCalledWith('cam-01'))
    // Recarrega: a excluída some da lista e a seleção cai na que restou —
    // sem tela apontando para uma câmera que não existe mais.
    await waitFor(() => expect(listar).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(screen.queryByText('CAM-01 Doca Norte')).toBeNull())
  })

  it('cancelar fecha o diálogo sem excluir', async () => {
    montar()
    await esperarCarregado()

    fireEvent.click(screen.getByRole('button', { name: /Excluir/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Cancelar' }))

    await waitFor(() => expect(screen.queryByText('Excluir para sempre')).toBeNull())
    expect(excluir).not.toHaveBeenCalled()
  })

  it('sem cameras:delete o botão não existe — item que leva a 403 é pior que item ausente', async () => {
    permissoes.delete('cameras:delete')
    montar()
    await esperarCarregado()
    expect(screen.queryByRole('button', { name: /Excluir/ })).toBeNull()
    // Arquivar continua lá: são ações diferentes, com permissões diferentes.
    expect(screen.getByRole('button', { name: 'Arquivar' })).toBeTruthy()
  })

  it('falha da API vira erro na tela, não exclusão silenciosa', async () => {
    excluir.mockRejectedValue(new Error('Câmera não encontrada'))
    montar()
    await esperarCarregado()

    fireEvent.click(screen.getByRole('button', { name: /Excluir/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Excluir para sempre' }))

    expect(await screen.findByText('Câmera não encontrada')).toBeTruthy()
  })
})
