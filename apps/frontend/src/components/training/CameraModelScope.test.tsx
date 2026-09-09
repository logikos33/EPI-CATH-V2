/**
 * CameraModelScope — aba "Modelos por câmera".
 *
 * Garante: (1) câmeras + classes que o modelo DE FATO prevê (class_distribution
 * sem `__*` e sem as listadas em `__sem_suporte_treino__`) renderizam com o
 * escopo do deployment — lido de GET /cameras/<id>/model-config?module=<m>
 * (fonte de verdade, 1 por câmera ativa); (2) desmarcar classe e salvar →
 * POST /cameras/<id>/model-config com config.classes sem ela e roi/thresholds
 * preservados (thresholds podados); (3) sem `cameras:configure` tudo
 * desabilitado e zero POST; (4) 0 classes → salvar desabilitado; (5) módulo
 * = camera.active_module (GET e POST), modelos oferecidos são os do módulo;
 * (6) "sem deployment" não é escolha válida quando já há deployment.
 */
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { CameraModelScope, classesDoModelo, moduloDaCamera, montarConfig } from './CameraModelScope'

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  list: vi.fn(),
  can: true as boolean,
  // Default true preserva o fluxo de engenharia já coberto abaixo (badge de
  // framework visível) — os testes de anti-vazamento ligam false.
  isSuperAdmin: true as boolean,
  permissoesPedidas: [] as string[],
}))

vi.mock('../../services/api', () => ({ api: { get: mocks.get, post: mocks.post } }))
vi.mock('../../services/cameraService', () => ({ cameraService: { list: mocks.list } }))
vi.mock('../../hooks/useAuth', () => ({
  useAuth: () => ({
    can: (chave: string) => { mocks.permissoesPedidas.push(chave); return mocks.can },
    isSuperAdmin: mocks.isSuperAdmin,
  }),
}))

const DEPLOY = {
  id: 'dep-1', model_id: 'm-v9', camera_id: 'cam-1', module_code: 'epi', status: 'active',
  created_at: '2026-08-21T10:00:00Z',
  config: { classes: ['Luvas', 'Oculos'], roi: [[0, 0], [1, 0], [1, 1]], thresholds: { Luvas: 0.4, Botas: 0.5 } },
}

beforeEach(() => {
  vi.clearAllMocks()
  mocks.can = true
  mocks.isSuperAdmin = true
  mocks.permissoesPedidas = []
  mocks.list.mockResolvedValue([
    { id: 'cam-1', name: 'Canal 8', is_active: true, active_module: 'epi' },
    { id: 'cam-2', name: 'Canal 6', is_active: true, active_module: null },
    { id: 'cam-3', name: 'Linha A', is_active: true, active_module: 'quality' },
    { id: 'cam-9', name: 'Desligada', is_active: false },
  ])
  mocks.get.mockImplementation(async (path: string) => {
    if (path === '/v1/models') {
      return { success: true, data: { models: [
        { id: 'm-v9', name: 'rvb-v9', framework: 'rfdetr', r2_onnx_key: 'models/x/v9.onnx', is_active: true, module_code: 'epi', created_at: '2026-08-25T06:45:00Z' },
        { id: 'm-semonnx', name: 'sem-artefato', framework: 'yolox', r2_onnx_key: null, is_active: false, module_code: 'epi' },
        { id: 'm-q1', name: 'qualidade-v1', framework: 'rfdetr', r2_onnx_key: 'models/x/q1.onnx', is_active: true, module_code: 'quality', created_at: '2026-08-25T06:45:00Z' },
      ] } }
    }
    if (path === '/v1/models/m-v9') {
      return { success: true, data: {
        model: { id: 'm-v9', name: 'rvb-v9', is_active: true },
        // Colete tem contagem mas está em __sem_suporte_treino__ → o modelo NÃO a prevê
        lineage: { dataset_version: { class_distribution: { Luvas: 120, Oculos: 80, Botas: 30, Colete: 7, __sem_suporte_treino__: ['Colete'] } } },
      } }
    }
    if (path === '/v1/models/m-q1') {
      return { success: true, data: {
        model: { id: 'm-q1', name: 'qualidade-v1', is_active: true },
        lineage: { dataset_version: { class_distribution: { Risco: 10 } } },
      } }
    }
    // Fonte de verdade do deployment: 1 GET por MÓDULO distinto, não por
    // câmera — com as 28 da RVB, uma chamada por câmera estourava o pool de
    // conexões da API e a aba não abria (medido no DEV em 2026-08-25).
    if (path === '/cameras/model-config?module=epi') {
      return { success: true, data: { deployments: { 'cam-1': DEPLOY } } }
    }
    if (path === '/cameras/model-config?module=quality') {
      return { success: true, data: { deployments: {} } }
    }
    throw new Error(`GET inesperado: ${path}`)
  })
  mocks.post.mockImplementation(async (_path: string, body: { model_id: string; config: { classes: string[] } }) => ({
    success: true,
    data: { deployment: { ...DEPLOY, id: 'dep-2', model_id: body.model_id, config: body.config } },
  }))
})

describe('CameraModelScope', () => {
  it('renderiza câmeras ativas e as classes do modelo com o escopo do deployment marcado', async () => {
    render(<CameraModelScope classesCatalogo={[]} />)

    await waitFor(() => expect(screen.getByLabelText('Modelo da câmera Canal 8')).toBeDefined())
    expect(screen.queryByText('Desligada')).toBeNull()
    expect(mocks.get).not.toHaveBeenCalledWith('/v1/models/m-semonnx')

    const sel = screen.getByLabelText('Modelo da câmera Canal 8') as HTMLSelectElement
    expect(sel.value).toBe('m-v9')
    expect(screen.getByLabelText('Classe Luvas em Canal 8').getAttribute('aria-pressed')).toBe('true')
    expect(screen.getByLabelText('Classe Oculos em Canal 8').getAttribute('aria-pressed')).toBe('true')
    expect(screen.getByLabelText('Classe Botas em Canal 8').getAttribute('aria-pressed')).toBe('false')
    expect(screen.queryByLabelText(/__sem_suporte_treino__/)).toBeNull()
    // Colete está em __sem_suporte_treino__ → o modelo não a prevê → não é escopo
    expect(screen.queryByLabelText('Classe Colete em Canal 8')).toBeNull()
    expect(screen.getAllByText('RF-DETR').length).toBeGreaterThan(0)
    // Deployment veio do lote por módulo — uma chamada, não uma por câmera
    expect(mocks.get).toHaveBeenCalledWith('/cameras/model-config?module=epi')
    expect(mocks.get).toHaveBeenCalledWith('/cameras/model-config?module=quality')
    const porCamera = mocks.get.mock.calls.filter(
      (args: unknown[]) => /\/cameras\/[^/]+\/model-config/.test(String(args[0])),
    )
    expect(porCamera).toEqual([])
    expect(mocks.get).not.toHaveBeenCalledWith(expect.stringContaining('/cameras/cam-9/'))
    // Câmera sem deployment: sem modelo selecionado, sem checkboxes
    expect((screen.getByLabelText('Modelo da câmera Canal 6') as HTMLSelectElement).value).toBe('')
    expect(screen.queryByLabelText('Classe Luvas em Canal 6')).toBeNull()
    // Copy honesta: sem deployment = detector padrão do ambiente (não "ativo do módulo")
    expect(screen.getByText(/Sem deployment = detector padrão do ambiente/)).toBeDefined()
    expect(screen.queryByText(/herda/)).toBeNull()
  })

  it('"sem deployment" não é escolha válida quando a câmera já tem deployment (sem rota de desativar)', async () => {
    render(<CameraModelScope classesCatalogo={[]} />)
    await waitFor(() => expect(screen.getByLabelText('Modelo da câmera Canal 8')).toBeDefined())

    const comDep = screen.getByLabelText('Modelo da câmera Canal 8') as HTMLSelectElement
    const semDep = screen.getByLabelText('Modelo da câmera Canal 6') as HTMLSelectElement
    const opt = (sel: HTMLSelectElement) => Array.from(sel.options).find(o => o.value === '')!
    expect(opt(comDep).disabled).toBe(true)
    expect(opt(semDep).disabled).toBe(false)
    expect(opt(semDep).textContent).toMatch(/sem deployment/)
  })

  it('módulo da câmera (active_module) vai no GET e no POST e filtra os modelos oferecidos', async () => {
    render(<CameraModelScope classesCatalogo={[]} />)
    await waitFor(() => expect(screen.getByLabelText('Modelo da câmera Linha A')).toBeDefined())

    expect(mocks.get).toHaveBeenCalledWith('/cameras/model-config?module=quality')
    const sel = screen.getByLabelText('Modelo da câmera Linha A') as HTMLSelectElement
    const ids = Array.from(sel.options).map(o => o.value)
    expect(ids).toContain('m-q1')
    expect(ids).not.toContain('m-v9')
    // Câmera EPI não vê o modelo de qualidade
    const selEpi = screen.getByLabelText('Modelo da câmera Canal 6') as HTMLSelectElement
    expect(Array.from(selEpi.options).map(o => o.value)).not.toContain('m-q1')
    expect(screen.getByText('quality')).toBeDefined()

    fireEvent.change(sel, { target: { value: 'm-q1' } })
    fireEvent.click(screen.getByLabelText('Salvar escopo de Linha A'))
    await waitFor(() => expect(mocks.post).toHaveBeenCalledWith('/cameras/cam-3/model-config', {
      model_id: 'm-q1', module_code: 'quality', config: { classes: ['Risco'] },
    }))
  })

  it('desmarcar classe + salvar → POST com config.classes sem ela e roi/thresholds preservados', async () => {
    render(<CameraModelScope classesCatalogo={[]} />)
    await waitFor(() => expect(screen.getByLabelText('Classe Oculos em Canal 8')).toBeDefined())

    const salvar = screen.getByLabelText('Salvar escopo de Canal 8') as HTMLButtonElement
    expect(salvar.disabled).toBe(true) // nada mudou ainda

    fireEvent.click(screen.getByLabelText('Classe Oculos em Canal 8'))
    expect(salvar.disabled).toBe(false)
    fireEvent.click(salvar)

    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(1))
    expect(mocks.post).toHaveBeenCalledWith('/cameras/cam-1/model-config', {
      model_id: 'm-v9',
      module_code: 'epi',
      config: { classes: ['Luvas'], roi: [[0, 0], [1, 0], [1, 1]], thresholds: { Luvas: 0.4 } },
    })
    // Linha atualizada com o deployment devolvido → botão volta a desabilitar
    await waitFor(() => expect((screen.getByLabelText('Salvar escopo de Canal 8') as HTMLButtonElement).disabled).toBe(true))
    expect(screen.getByLabelText('Classe Oculos em Canal 8').getAttribute('aria-pressed')).toBe('false')
  })

  it('escolher modelo numa câmera sem deployment marca todas as classes do modelo', async () => {
    render(<CameraModelScope classesCatalogo={[]} />)
    await waitFor(() => expect(screen.getByLabelText('Modelo da câmera Canal 6')).toBeDefined())

    fireEvent.change(screen.getByLabelText('Modelo da câmera Canal 6'), { target: { value: 'm-v9' } })
    expect(screen.getByLabelText('Classe Botas em Canal 6').getAttribute('aria-pressed')).toBe('true')
    fireEvent.click(screen.getByLabelText('Salvar escopo de Canal 6'))

    await waitFor(() => expect(mocks.post).toHaveBeenCalledWith('/cameras/cam-2/model-config', {
      model_id: 'm-v9', module_code: 'epi', config: { classes: ['Luvas', 'Oculos', 'Botas'] },
    }))
  })

  it('sem cameras:configure → tudo desabilitado e nenhum POST', async () => {
    mocks.can = false
    render(<CameraModelScope classesCatalogo={[]} />)
    await waitFor(() => expect(screen.getByLabelText('Modelo da câmera Canal 8')).toBeDefined())

    expect(screen.getByText(/somente leitura/i)).toBeDefined()
    expect((screen.getByLabelText('Modelo da câmera Canal 8') as HTMLSelectElement).disabled).toBe(true)
    expect((screen.getByLabelText('Classe Luvas em Canal 8') as HTMLButtonElement).disabled).toBe(true)
    expect((screen.getByLabelText('Salvar escopo de Canal 8') as HTMLButtonElement).disabled).toBe(true)

    fireEvent.click(screen.getByLabelText('Classe Luvas em Canal 8'))
    fireEvent.click(screen.getByLabelText('Salvar escopo de Canal 8'))
    expect(mocks.post).not.toHaveBeenCalled()
  })

  it('deployment sem config.classes não pré-marca nada (escopo não gravado ≠ todas as classes)', async () => {
    // O POST desta tela sempre grava `classes` (geometry_validation exige ≥1);
    // um deployment sem a chave veio de fora da API (script ad-hoc do shadow,
    // que gravou `classes_scope`). Pré-marcar tudo afirmaria um escopo que
    // ninguém escreveu — e, com `base` no mesmo fallback, nem dava para corrigir.
    const original = mocks.get.getMockImplementation()!
    mocks.get.mockImplementation(async (path: string) =>
      path === '/cameras/model-config?module=epi'
        ? { success: true, data: { deployments: {
            'cam-1': { ...DEPLOY, config: { classes_scope: ['Luvas'] } },
          } } }
        : original(path),
    )

    render(<CameraModelScope classesCatalogo={[]} />)
    await waitFor(() => expect(screen.getByLabelText('Classe Luvas em Canal 8')).toBeDefined())

    expect(screen.getByLabelText('Classe Luvas em Canal 8').getAttribute('aria-pressed')).toBe('false')
    expect(screen.getByLabelText('Classe Oculos em Canal 8').getAttribute('aria-pressed')).toBe('false')
    expect(screen.getByLabelText('Classe Botas em Canal 8').getAttribute('aria-pressed')).toBe('false')
    expect(screen.getByText(/marque ≥1 classe/)).toBeDefined()
    // Corrigível: marcar 1 classe já habilita Salvar (antes `mudou` era false)
    fireEvent.click(screen.getByLabelText('Classe Luvas em Canal 8'))
    expect((screen.getByLabelText('Salvar escopo de Canal 8') as HTMLButtonElement).disabled).toBe(false)
  })

  it('GET falhando mostra erro e retry, nunca "nenhuma câmera ativa"', async () => {
    mocks.list.mockRejectedValue(new Error('boom'))
    render(<CameraModelScope classesCatalogo={[]} />)

    await waitFor(() => expect(screen.getByRole('button', { name: 'Tentar de novo' })).toBeDefined())
    expect(screen.queryByText(/Nenhuma câmera ativa/)).toBeNull()
    expect(screen.getByText(/boom/)).toBeDefined()
  })

  it('0 classes marcadas → salvar desabilitado', async () => {
    render(<CameraModelScope classesCatalogo={[]} />)
    await waitFor(() => expect(screen.getByLabelText('Classe Luvas em Canal 8')).toBeDefined())

    fireEvent.click(screen.getByLabelText('Classe Luvas em Canal 8'))
    fireEvent.click(screen.getByLabelText('Classe Oculos em Canal 8'))
    expect((screen.getByLabelText('Salvar escopo de Canal 8') as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByText(/marque ≥1 classe/)).toBeDefined()
    fireEvent.click(screen.getByLabelText('Salvar escopo de Canal 8'))
    expect(mocks.post).not.toHaveBeenCalled()
  })
})

describe('CameraModelScope — anti-vazamento de stack interno (política F5-LEVE)', () => {
  // Forma real de um modelo "não rebatizado": name/framework internos,
  // display_name ainda NULL (ninguém atribuiu na tela de rebranding).
  const MODELO_INTERNO = {
    id: 'm-leak', name: 'YOLO26 yolo26n - Job abc12345', framework: 'yolox',
    r2_onnx_key: 'models/x/leak.onnx', is_active: true, module_code: 'epi',
    display_name: null as string | null,
  }

  function mockaModeloUnico(modelo: typeof MODELO_INTERNO, { comDeployment = false } = {}) {
    mocks.get.mockImplementation(async (path: string) => {
      if (path === '/v1/models') return { success: true, data: { models: [modelo] } }
      if (path === '/v1/models/m-leak') {
        return { success: true, data: { model: modelo, lineage: { dataset_version: { class_distribution: { Luvas: 10 } } } } }
      }
      if (path === '/cameras/model-config?module=epi') {
        return {
          success: true,
          data: {
            deployments: comDeployment ? {
              'cam-1': {
                id: 'dep-leak', model_id: 'm-leak', camera_id: 'cam-1', module_code: 'epi',
                status: 'active', created_at: '2026-08-21T10:00:00Z', config: { classes: ['Luvas'] },
              },
            } : {},
          },
        }
      }
      if (path.startsWith('/cameras/model-config')) return { success: true, data: { deployments: {} } }
      throw new Error(`GET inesperado: ${path}`)
    })
  }

  it('não-superadmin: nome interno e framework NUNCA aparecem — cai em "Logikos"', async () => {
    mocks.isSuperAdmin = false
    mockaModeloUnico(MODELO_INTERNO)
    render(<CameraModelScope classesCatalogo={[]} />)

    const sel = await screen.findByLabelText('Modelo da câmera Canal 8') as HTMLSelectElement
    const opt = Array.from(sel.options).find(o => o.value === 'm-leak')!
    // "Logikos" sozinho é o mesmo rótulo para TODO modelo não rebatizado — o
    // que separa um treino do outro é a contagem (e a data, quando vem).
    expect(opt.textContent).toBe('Logikos · 1 classe')
    expect(document.body.innerHTML).not.toMatch(/yolo|rf-?detr|onnx/i)
  })

  it('não-superadmin com display_name atribuído: mostra o nome escolhido para o cliente', async () => {
    mocks.isSuperAdmin = false
    mockaModeloUnico({ ...MODELO_INTERNO, display_name: 'Logikos V1' })
    render(<CameraModelScope classesCatalogo={[]} />)

    const sel = await screen.findByLabelText('Modelo da câmera Canal 8') as HTMLSelectElement
    const opt = Array.from(sel.options).find(o => o.value === 'm-leak')!
    expect(opt.textContent).toBe('Logikos V1 · 1 classe')
  })

  it('superadmin: continua vendo nome e framework internos — não regressão da engenharia', async () => {
    mocks.isSuperAdmin = true
    mockaModeloUnico(MODELO_INTERNO, { comDeployment: true })
    render(<CameraModelScope classesCatalogo={[]} />)

    const sel = await screen.findByLabelText('Modelo da câmera Canal 8') as HTMLSelectElement
    const opt = Array.from(sel.options).find(o => o.value === 'm-leak')!
    expect(opt.textContent).toBe(`${MODELO_INTERNO.name} · 1 classe`)
    expect(screen.getAllByText('YOLOX').length).toBeGreaterThan(0)
  })
})

describe('CameraModelScope — o operador tem de saber QUAL modelo é', () => {
  it('rótulo do modelo: nome + quantas classes + quando (não só o id do job)', async () => {
    render(<CameraModelScope classesCatalogo={[]} />)
    const sel = await screen.findByLabelText('Modelo da câmera Canal 8') as HTMLSelectElement

    const opt = Array.from(sel.options).find(o => o.value === 'm-v9')!
    // 3 = as classes que o modelo DE FATO prevê (Colete está em
    // __sem_suporte_treino__ e não conta). Data sem fuso fixo no rótulo, por
    // isso a forma e não o valor — o valor exato é `dataCurta` em
    // services/modelDisplay.test.ts.
    expect(opt.textContent).toMatch(/^rvb-v9 · 3 classes · \d{2}\/\d{2}\/\d{2} \d{2}h\d{2}$/)
  })

  it('modelo que não declara classes: rótulo SEM contagem — o catálogo do tenant não vira medida do modelo', async () => {
    mocks.list.mockResolvedValue([{ id: 'cam-1', name: 'Canal 8', is_active: true, active_module: 'epi' }])
    mocks.get.mockImplementation(async (path: string) => {
      if (path === '/v1/models') {
        return { success: true, data: { models: [
          { id: 'm-mudo', name: 'treino-mudo', r2_onnx_key: 'models/x/m.onnx', is_active: true, module_code: 'epi' },
        ] } }
      }
      if (path === '/v1/models/m-mudo') {
        return { success: true, data: { model: { id: 'm-mudo' }, lineage: { dataset_version: { class_distribution: {} } } } }
      }
      if (path.startsWith('/cameras/model-config')) return { success: true, data: { deployments: {} } }
      throw new Error(`GET inesperado: ${path}`)
    })

    render(<CameraModelScope classesCatalogo={[
      { id: 1, name: 'Capacete', color: '#000' }, { id: 2, name: 'Luvas', color: '#111' },
    ]} />)
    const sel = await screen.findByLabelText('Modelo da câmera Canal 8') as HTMLSelectElement

    const opt = Array.from(sel.options).find(o => o.value === 'm-mudo')!
    expect(opt.textContent).toBe('treino-mudo')
    expect(opt.textContent).not.toMatch(/classe/)
    // O catálogo continua nomeando os chips (fallback de NOMES, decisão
    // antiga) — o que ele não pode é virar "2 classes" no rótulo.
    fireEvent.change(sel, { target: { value: 'm-mudo' } })
    expect(screen.getByLabelText('Classe Capacete em Canal 8')).toBeDefined()
  })

  it('nome de câmera e data de deploy truncam com o texto inteiro no title (a última coluna parava cortada)', async () => {
    render(<CameraModelScope classesCatalogo={[]} />)
    await screen.findByLabelText('Modelo da câmera Canal 8')

    expect(screen.getByText('Canal 8').getAttribute('title')).toBe('Canal 8')
    const celulaData = screen.getByText(/^\d{2}\/\d{2}\/\d{2} \d{2}h\d{2}$/)
    expect(celulaData.getAttribute('title')).toBe(new Date(DEPLOY.created_at).toLocaleString('pt-BR'))
  })
})

describe('CameraModelScope — aviso curto que expande (o texto integral empurrava a tabela para fora da tela)', () => {
  const TRECHO_INTEGRAL =
    'protetor auditivo universal): é a CAPACIDADE do modelo ali, não a EXIGÊNCIA do cliente.'

  it('fechado: a distinção capacidade × exigência aparece; o texto de 8 linhas, não', async () => {
    render(<CameraModelScope classesCatalogo={[]} />)
    await screen.findByLabelText('Modelo da câmera Canal 8')

    expect(document.body.textContent).toContain('capacidade')
    expect(document.body.textContent).toContain('exigência')
    expect(document.body.textContent).not.toContain(TRECHO_INTEGRAL)
  })

  it('"Entenda": o texto original volta inteiro, palavra por palavra', async () => {
    render(<CameraModelScope classesCatalogo={[]} />)
    await screen.findByLabelText('Modelo da câmera Canal 8')

    const entenda = screen.getByRole('button', { name: 'Entenda' })
    expect(entenda.getAttribute('aria-expanded')).toBe('false')
    fireEvent.click(entenda)

    expect(document.body.textContent).toContain(TRECHO_INTEGRAL)
    expect(document.body.textContent).toContain(
      'Será substituído pela matriz de exigência da RVB assim que ela ficar pronta',
    )
    expect(document.body.textContent).toContain(
      'O equipamento instalado no site ainda NÃO aplica esse recorte por câmera — essa parte segue pendente.',
    )
    // Some de novo — é um aviso que se lê uma vez, não um bloco fixo.
    fireEvent.click(screen.getByRole('button', { name: 'Ocultar' }))
    expect(document.body.textContent).not.toContain(TRECHO_INTEGRAL)
  })
})

describe('helpers puros', () => {
  it('classesDoModelo ignora chaves reservadas, subtrai __sem_suporte_treino__ e cai no catálogo quando vazio', () => {
    expect(classesDoModelo({ Luvas: 1, __sem_suporte_treino__: ['x'] }, ['cat'])).toEqual(['Luvas'])
    // Colete tem contagem (120/7) mas o treino a excluiu → o modelo NÃO a prevê
    expect(classesDoModelo({ Luvas: 120, Colete: 7, __sem_suporte_treino__: ['Colete'] }, ['cat'])).toEqual(['Luvas'])
    // Tudo sem suporte → fallback, não lista vazia
    expect(classesDoModelo({ Colete: 7, __sem_suporte_treino__: ['Colete'] }, ['cat'])).toEqual(['cat'])
    expect(classesDoModelo({}, ['cat'])).toEqual(['cat'])
    expect(classesDoModelo(null, ['cat'])).toEqual(['cat'])
  })

  it('moduloDaCamera usa active_module com fallback epi', () => {
    expect(moduloDaCamera({ active_module: 'quality' })).toBe('quality')
    expect(moduloDaCamera({ active_module: null })).toBe('epi')
    expect(moduloDaCamera({})).toBe('epi')
    expect(moduloDaCamera({ active_module: '  ' })).toBe('epi')
  })

  it('montarConfig preserva roi/line, troca classes e poda thresholds', () => {
    expect(montarConfig({ line: [[0, 0], [1, 1]], thresholds: { A: 0.3, B: 0.6 } }, ['B']))
      .toEqual({ line: [[0, 0], [1, 1]], classes: ['B'], thresholds: { B: 0.6 } })
    expect(montarConfig(null, ['A'])).toEqual({ classes: ['A'] })
  })

  it('a permissão pedida é cameras:configure, não training:approve', async () => {
    // 'training:approve' é aprovar TREINAMENTO e hoje só existe para
    // superadmin — com ele, o admin da RVB (quem conhece a área) via a aba em
    // somente leitura. Editar o escopo de uma câmera é configuração de câmera.
    render(<CameraModelScope classesCatalogo={[]} />)
    await waitFor(() => expect(screen.getByLabelText('Modelo da câmera Canal 8')).toBeDefined())

    expect(mocks.permissoesPedidas).toContain('cameras:configure')
    expect(mocks.permissoesPedidas).not.toContain('training:approve')
  })
})
