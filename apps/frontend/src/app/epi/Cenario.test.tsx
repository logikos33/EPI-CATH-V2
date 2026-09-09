/**
 * O que esta tela não pode errar: prometer que pausou sem endpoint,
 * inventar hora do último aviso, mostrar jargão de motor pro cliente, ou
 * deixar o modo avançado visível pra quem não cuida da plataforma.
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const h = vi.hoisted(() => ({
  permissoes: ['cameras:configure'] as string[],
  superadmin: false,
  ApiErroFalso: class ApiErroFalso extends Error {
    status: number
    constructor(status: number) {
      super(`HTTP ${status}`)
      this.status = status
    }
  },
}))

vi.mock('../../hooks/useAuth', () => ({
  useAuth: () => ({ can: (p: string) => h.permissoes.includes(p), isSuperAdmin: h.superadmin }),
}))

// A miniatura ao vivo é detalhe de outra tela — aqui só precisa não quebrar.
vi.mock('../../hooks/useCameraSnapshot', () => ({
  useCameraSnapshot: () => ({
    status: 'idle', url: null, capturedAt: null, errorReason: null, loading: false, timedOut: false,
    refresh: vi.fn(),
  }),
}))

const get = vi.fn()
const post = vi.fn()
const put = vi.fn()
const del = vi.fn()

vi.mock('../../services/api', () => ({
  ApiError: h.ApiErroFalso,
  api: {
    get: (...a: unknown[]) => get(...(a as [string])),
    post: (...a: unknown[]) => post(...(a as [string, unknown?])),
    put: (...a: unknown[]) => put(...(a as [string, unknown?])),
    delete: (...a: unknown[]) => del(...(a as [string])),
  },
}))

import { Cenario } from './Cenario'

const CAMERA_ID = 'cam-1'

const CLASSES = [
  { class_name: 'capacete', display_name: 'Capacete', polaridade: 'conformidade' },
  { class_name: 'colete', display_name: 'Colete refletivo', polaridade: 'violacao' },
]

/** O que o RVB tem de verdade no cadastro: a calibração da ADR-0067
 * (`scripts/ops/aplicar_calibracao_rvb.py`) rebaixou esta classe para
 * INDECISA — `yolo_classes.is_violation IS NULL`. O endpoint que enche a tela
 * (`GET /modules/epi/classes`) devolve `polaridade: 'indefinida'` e continua
 * listando; `EpiZoneOperation._classes_validas()` NÃO a inclui (soma
 * `presence_class_names` + `violation_class_names`, ambas filtrando
 * `is_violation IS TRUE`/`IS FALSE`). Era isto que a tela oferecia e o
 * backend recusava. */
const CLASSE_INDECISA = {
  class_name: 'Sem Óculos',
  display_name: 'Sem Óculos',
  polaridade: 'indefinida',
}

/** A recusa literal do backend, copiada de `epi_zone.validate_config`. */
const ERRO_DO_BACKEND =
  "Configuração inválida: classe inválida: 'Sem Óculos' não pertence ao módulo epi deste cliente"

/** Config REAL de `epi_zone.py` — zone_points + watch_classes. */
const opEpi = (extra: Record<string, unknown> = {}) => ({
  id: 1,
  camera_id: CAMERA_ID,
  module_id: 'mod-epi',
  type_id: 'epi_zone',
  template_id: 'epi',
  name: 'Doca 3',
  status: 'active',
  config: {
    zone_points: [[0.1, 0.1], [0.5, 0.1], [0.5, 0.5], [0.1, 0.5]],
    watch_classes: ['capacete'],
  },
  ...extra,
})

function servir(
  ops: unknown[],
  opcoes: { classes?: unknown[]; escopo?: string[] | null } = {},
) {
  const catalogo = opcoes.classes ?? CLASSES
  get.mockImplementation((path: string) => {
    if (path.includes('/model-config')) {
      return Promise.resolve({
        data: { deployment: opcoes.escopo ? { config: { classes: opcoes.escopo } } : null },
      })
    }
    if (path.includes('/operations')) return Promise.resolve({ data: { operations: ops } })
    if (path.startsWith('/modules/') && path.includes('/classes')) {
      return Promise.resolve({ data: { classes: catalogo } })
    }
    if (path === '/modules/') return Promise.resolve({ data: { modules: [{ id: 'mod-epi', module_code: 'epi' }] } })
    if (path === `/cameras/${CAMERA_ID}`) {
      return Promise.resolve({ data: { id: CAMERA_ID, name: 'CAM-04 Expedição', active_module: 'epi' } })
    }
    return Promise.resolve({ data: {} })
  })
}

function montar() {
  return render(
    <MemoryRouter initialEntries={[`/novo/epi/cameras/${CAMERA_ID}/cenario`]}>
      <Routes>
        <Route path="/novo/epi/cameras/:cameraId/cenario" element={<Cenario />} />
      </Routes>
    </MemoryRouter>,
  )
}

async function abrirTemplateEpi() {
  fireEvent.click(await screen.findByRole('button', { name: /desenhar nova regra/i }))
  fireEvent.click(await screen.findByRole('button', { name: /zona de epi obrigatório/i }))
  await screen.findByPlaceholderText(/nome deste lugar/i)
}

beforeEach(() => {
  get.mockReset()
  post.mockReset()
  put.mockReset()
  del.mockReset()
  post.mockResolvedValue({ data: { operation: opEpi() } })
  h.permissoes = ['cameras:configure']
  h.superadmin = false
})

describe('lista', () => {
  it('renderiza a regra real, com a frase em linguagem de gente e o último aviso', async () => {
    servir([opEpi({ last_event_at: '2026-08-29T14:32:00Z' })])
    montar()
    expect(await screen.findByText('Doca 3')).toBeTruthy()
    expect(screen.getByText('Você verá um evento quando alguém entrar em "Doca 3" sem capacete.')).toBeTruthy()
    expect(screen.getByText(/ÚLTIMO AVISO/)).toBeTruthy()
  })

  it('sem último disparo, o cartão OMITE a linha — nunca inventa hora', async () => {
    servir([opEpi({ last_event_at: null })])
    montar()
    await screen.findByText('Doca 3')
    expect(screen.queryByText(/ÚLTIMO AVISO/)).toBeNull()
  })

  it('vazio honesto quando a câmera ainda não vigia nada', async () => {
    servir([])
    montar()
    expect(await screen.findByText(/esta câmera ainda não vigia nada/i)).toBeTruthy()
    expect(screen.getByRole('button', { name: /desenhe sua primeira zona/i })).toBeTruthy()
  })
})

describe('editor — template primeiro', () => {
  it('escolher o template pré-desenha a geometria e só habilita salvar com os 3 passos', async () => {
    servir([])
    montar()
    await abrirTemplateEpi()

    // geometria padrão da área (4 cantos) já entra pré-desenhada
    expect(screen.getByText(/4 cantos na zona/i)).toBeTruthy()

    const salvarIncompleto = screen.getByRole('button', { name: /complete os 3 passos/i })
    expect((salvarIncompleto as HTMLButtonElement).disabled).toBe(true)

    fireEvent.change(screen.getByPlaceholderText(/nome deste lugar/i), { target: { value: 'Doca 3' } })
    expect(screen.getByRole('button', { name: /complete os 3 passos/i })).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Capacete' }))

    const salvarCompleto = await screen.findByRole('button', { name: /salvar e começar a valer/i })
    expect((salvarCompleto as HTMLButtonElement).disabled).toBe(false)
  })

  it('o preview em linguagem natural muda a cada edição', async () => {
    servir([])
    montar()
    await abrirTemplateEpi()

    fireEvent.change(screen.getByPlaceholderText(/nome deste lugar/i), { target: { value: 'Doca 3' } })
    fireEvent.click(screen.getByRole('button', { name: 'Capacete' }))
    expect(
      await screen.findByText('Você verá um evento quando alguém entrar em "Doca 3" sem capacete.'),
    ).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: /só avisar se ficar um tempo dentro/i }))
    expect(
      await screen.findByText(/Você verá um evento quando alguém ficar mais de \d+ segundos em "Doca 3" sem capacete\./),
    ).toBeTruthy()
  })
})

describe('aproximação — sem geometria real no motor (B11)', () => {
  it('preview e aviso NUNCA afirmam recorte espacial — falha se o aviso sumir', async () => {
    servir([])
    montar()
    fireEvent.click(await screen.findByRole('button', { name: /desenhar nova regra/i }))
    fireEvent.click(await screen.findByRole('button', { name: /aproximação perigosa/i }))
    await screen.findByPlaceholderText(/nome deste lugar/i)

    fireEvent.change(screen.getByPlaceholderText(/nome deste lugar/i), { target: { value: 'Pátio X' } })
    fireEvent.click(screen.getByRole('button', { name: 'Capacete' }))
    fireEvent.click(screen.getByRole('button', { name: 'Colete refletivo' }))

    // o mesmo aviso aparece duas vezes: painel do passo 3 + preview
    const avisos = await screen.findAllByText(
      /vale para toda a imagem — a marcação serve só de referência para quem for olhar depois/i,
    )
    expect(avisos.length).toBe(2)

    // a frase natural também não promete recorte — nunca cita "em <lugar>"
    expect(screen.getByText(/vale para toda a imagem desta câmera\./)).toBeTruthy()
    expect(screen.queryByText(/em "Pátio X"/)).toBeNull()
  })
})

describe('pausar/retomar — degradação graciosa', () => {
  it('funciona de ponta a ponta quando a rota responde', async () => {
    servir([opEpi()])
    post.mockResolvedValue({ data: { operation: opEpi({ status: 'inactive' }) } })
    montar()
    const pausar = await screen.findByRole('button', { name: 'Pausar' })
    fireEvent.click(pausar)
    expect(await screen.findByRole('button', { name: 'Retomar' })).toBeTruthy()
    expect(post).toHaveBeenCalledWith(`/operations/1/pause`)
  })

  it('rota ainda sem deploy (404/405) vira selo de dependência, sem quebrar a tela', async () => {
    servir([opEpi()])
    post.mockRejectedValue(new h.ApiErroFalso(404))
    montar()
    const pausar = await screen.findByRole('button', { name: 'Pausar' })
    fireEvent.click(pausar)

    const dependente = await screen.findByTitle(/depende do pedido b1/i)
    expect((dependente as HTMLButtonElement).disabled).toBe(true)
    // a tela segue inteira — nada quebrou
    expect(screen.getByText('Doca 3')).toBeTruthy()
  })
})

describe('modo avançado — só superadmin', () => {
  it('fica invisível para quem não é superadmin', async () => {
    h.superadmin = false
    servir([])
    montar()
    await abrirTemplateEpi()
    expect(screen.queryByText(/modo avançado/i)).toBeNull()
  })

  it('aparece para superadmin', async () => {
    h.superadmin = true
    servir([])
    montar()
    await abrirTemplateEpi()
    expect(await screen.findByText(/modo avançado/i)).toBeTruthy()
  })
})

describe('linguagem', () => {
  const PROIBIDAS = [
    /\bthreshold\b/i, /\biou\b/i, /condition_satisfied/i, /bounding box/i, /\boverlap\b/i,
    /\bpolygon\b/i, /\bpayload\b/i, /\bjson\b/i, /confidence score/i, /infer[êe]ncia/i,
    /\byolo\b/i, /\btracker\b/i,
  ]

  it('zero jargão proibido no texto renderizado (lista + editor, sessão de cliente)', async () => {
    h.superadmin = false
    servir([opEpi()])
    const { container } = montar()
    await screen.findByText('Doca 3')
    fireEvent.click(screen.getByRole('button', { name: /editar/i }))
    await screen.findByPlaceholderText(/nome deste lugar/i)

    const texto = container.textContent ?? ''
    for (const proibida of PROIBIDAS) {
      expect(texto, `achou "${proibida}" no texto renderizado`).not.toMatch(proibida)
    }
  })
})

describe('avaliação e simulação — nunca fingem', () => {
  it('avaliação OK/NOK fica desabilitada com o selo do pedido B2', async () => {
    servir([opEpi()])
    montar()
    await screen.findByText('Doca 3')
    const sim = screen.getByRole('button', { name: /sim, está boa/i })
    const nao = screen.getByRole('button', { name: /não, precisa ajuste/i })
    expect((sim as HTMLButtonElement).disabled).toBe(true)
    expect((nao as HTMLButtonElement).disabled).toBe(true)
  })

  it('simular sobre a cena fica desabilitado com o selo do pedido B6', async () => {
    servir([])
    montar()
    await abrirTemplateEpi()
    const simular = screen.getByRole('button', { name: /simular sobre a cena/i })
    expect((simular as HTMLButtonElement).disabled).toBe(true)
  })
})

describe('sem permissão', () => {
  it('com regras, a lista continua visível e os botões viram um selo explicando — não somem em silêncio', async () => {
    h.permissoes = []
    servir([opEpi()])
    montar()
    await screen.findByText('Doca 3')
    expect(screen.queryByRole('button', { name: /desenhar nova regra/i })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Editar' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Pausar' })).toBeNull()
    expect(screen.getByText('SOMENTE LEITURA')).toBeTruthy()
  })

  it('vazio sem permissão explica a falta de permissão — nunca manda desenhar sem dar um botão', async () => {
    h.permissoes = []
    servir([])
    montar()
    await screen.findByText(/esta câmera ainda não vigia nada/i)
    // texto exato da prancha — card "SEM PERMISSÃO"
    expect(screen.getByText(/alterar o que a câmera vigia é do administrador do site/i)).toBeTruthy()
    // nunca manda fazer o que a pessoa não pode: nem o texto, nem o botão
    expect(screen.queryByText(/desenhe a primeira zona sobre a imagem/i)).toBeNull()
    expect(screen.queryByRole('button', { name: /desenhe sua primeira zona/i })).toBeNull()
    expect(screen.queryByRole('button', { name: /desenhar nova regra/i })).toBeNull()
  })

  it('vazio com permissão continua mostrando o CTA de sempre (texto exato da prancha)', async () => {
    h.permissoes = ['cameras:configure']
    servir([])
    montar()
    expect(await screen.findByRole('button', { name: /desenhe sua primeira zona/i })).toBeTruthy()
    expect(
      screen.getByText(/ela está gravando, mas ninguém disse o que observar\. desenhe a primeira zona/i),
    ).toBeTruthy()
  })
})


describe('a lista oferecida é subconjunto do que o backend aceita', () => {
  /**
   * A DIVERGÊNCIA, medida nos dois lados:
   *
   *   OFERECE  `GET /modules/epi/classes` → `module_service.get_classes()`:
   *            catálogo global ∪ classes do tenant, sem filtro de polaridade.
   *   ACEITA   `EpiZoneOperation._classes_validas()`: presença ∪ violação, e
   *            as duas filtram `is_violation IS TRUE`/`IS FALSE`.
   *
   * Logo: classe com polaridade indecisa é oferecida e recusada. O teste
   * atravessa o render de verdade e olha o que a tela MANDA na rota — não o
   * estado interno.
   */
  it('a classe indecisa aparece marcada como indisponível, com motivo, e não entra no que é salvo', async () => {
    servir([], { classes: [...CLASSES, CLASSE_INDECISA] })
    montar()
    await abrirTemplateEpi()

    const chip = screen.getByRole('button', { name: /sem óculos/i })
    expect((chip as HTMLButtonElement).disabled).toBe(true)
    // O motivo é VISÍVEL, não só um `title` que ninguém lê no demo.
    expect(screen.getByText(/decida a polaridade no estúdio/i)).toBeTruthy()

    fireEvent.click(chip)
    fireEvent.change(screen.getByPlaceholderText(/nome deste lugar/i), { target: { value: 'Doca 3' } })
    fireEvent.click(screen.getByRole('button', { name: 'Capacete' }))
    fireEvent.click(await screen.findByRole('button', { name: /salvar e começar a valer/i }))

    const corpo = post.mock.calls[0][1] as { config: { watch_classes: string[] } }
    expect(corpo.config.watch_classes).toEqual(['capacete'])
  })

  it('a trava é a do epi_zone, não da tela: na linha de contagem a mesma classe é escolhível', async () => {
    // `counting_line.validate_config` só exige que `target_class` não venha
    // vazio — marcar "indisponível" ali seria a mentira ao contrário.
    servir([], { classes: [...CLASSES, CLASSE_INDECISA] })
    montar()
    fireEvent.click(await screen.findByRole('button', { name: /desenhar nova regra/i }))
    fireEvent.click(await screen.findByRole('button', { name: /linha de contagem/i }))
    await screen.findByPlaceholderText(/nome deste lugar/i)

    const chip = screen.getByRole('button', { name: /sem óculos/i })
    expect((chip as HTMLButtonElement).disabled).toBe(false)

    fireEvent.change(screen.getByPlaceholderText(/nome deste lugar/i), { target: { value: 'Portão' } })
    fireEvent.click(chip)
    fireEvent.click(await screen.findByRole('button', { name: /salvar e começar a valer/i }))

    const corpo = post.mock.calls[0][1] as { config: { target_class: string } }
    expect(corpo.config.target_class).toBe('Sem Óculos')
  })

  it('recusa que a tela não previu vira marca no chip citado, com saída', async () => {
    // Rede de segurança: seja qual for o motivo da recusa, o operador vê em
    // QUAL classe está o problema — e consegue tirá-la e salvar.
    servir([], { classes: [...CLASSES, { ...CLASSE_INDECISA, polaridade: 'violacao' }] })
    post.mockRejectedValueOnce(new Error(ERRO_DO_BACKEND))
    montar()
    await abrirTemplateEpi()

    fireEvent.change(screen.getByPlaceholderText(/nome deste lugar/i), { target: { value: 'Doca 3' } })
    fireEvent.click(screen.getByRole('button', { name: 'Capacete' }))
    fireEvent.click(screen.getByRole('button', { name: /sem óculos/i }))
    fireEvent.click(await screen.findByRole('button', { name: /salvar e começar a valer/i }))

    expect(await screen.findByText(/o backend recusou esta classe/i)).toBeTruthy()
    // Escolhida + recusada continua clicável — só para TIRAR (sem beco).
    const chip = screen.getByRole('button', { name: /sem óculos/i })
    expect((chip as HTMLButtonElement).disabled).toBe(false)
    fireEvent.click(chip)
    fireEvent.click(screen.getByRole('button', { name: /salvar e começar a valer/i }))
    const corpo = post.mock.calls[1][1] as { config: { watch_classes: string[] } }
    expect(corpo.config.watch_classes).toEqual(['capacete'])
  })
})

describe('o que o modelo desta câmera enxerga', () => {
  it('sem modelo próprio, a tela NÃO afirma o que a câmera reconhece', async () => {
    servir([], { escopo: null })
    montar()
    await abrirTemplateEpi()
    expect(screen.getByText(/não dá para afirmar aqui o que ela reconhece/i)).toBeTruthy()
    expect(screen.queryByText(/fora do escopo/i)).toBeNull()
  })

  it('com escopo gravado, marca o que está fora dele — mas deixa escolher, porque salva', async () => {
    servir([], { escopo: ['capacete'] })
    montar()
    await abrirTemplateEpi()

    const fora = screen.getByRole('button', { name: /colete refletivo/i })
    expect((fora as HTMLButtonElement).disabled).toBe(false)
    expect(screen.getByText(/nunca dispara aqui/i)).toBeTruthy()
    // A que está no escopo não é marcada.
    expect(screen.getByRole('button', { name: 'Capacete' })).toBeTruthy()
  })
})
