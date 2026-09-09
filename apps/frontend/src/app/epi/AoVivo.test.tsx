/**
 * EPI Ao Vivo — o que esta tela não pode perder.
 *
 * Três coisas custaram caro e são protegidas aqui:
 *
 *  1. **A URL do HLS vem do backend, tokenizada.** Desde os PRs 255/256,
 *     `m3u8` sem token dá 404 igual a stream inexistente. Se alguém "otimizar"
 *     montando `${API}/api/cameras/${id}/stream/stream.m3u8` no front, a tela
 *     fica preta e o backend não acusa nada. O teste exige que a URL entregue
 *     ao player seja EXATAMENTE a que `POST /stream/start` devolveu, e que ela
 *     tenha o token no path.
 *
 *  2. **Sessão única de playback por câmera.** Ladrilho e gaveta cada um monta
 *     seu `useLiveView` + player; com os dois vivos, dois tokens baixam o mesmo
 *     `.ts` (visto em produção).
 *
 *  3. **Bounding box não é alvo de clique** (CLAUDE.md). Caixa clicável rouba o
 *     clique do ladrilho e se move sozinha.
 *
 * Mais o contrato de tela: dado real ou vazio honesto — nunca mocado.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { AoVivo } from './AoVivo'
import { api } from '../../services/api'
import { cameraService } from '../../services/cameraService'
import { __resetLiveViewCache } from '../../hooks/useLiveView'
import type { Camera } from '../../types'
import type { Detection } from '../../hooks/useMonitoringSocket'
import { rotaNova } from '../RotasNovas'

// ── Dublês ──────────────────────────────────────────────────────────────────

vi.mock('../../services/api', () => ({
  api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  getToken: vi.fn(() => 'jwt-de-teste'),
  setToken: vi.fn(),
  removeToken: vi.fn(),
}))

vi.mock('../../services/cameraService', () => ({
  cameraService: { start: vi.fn() },
}))

let permissoes = ['cameras:read']
vi.mock('../../hooks/useAuth', () => ({
  useAuth: () => ({ can: (p: string) => permissoes.includes(p) }),
}))

// socket.io real abriria conexão de verdade; o que interessa aqui é o payload.
let deteccoesWs: Record<string, Detection[]> = {}
// Conectado por padrão — o teste de "SEM SINAL" (§11) desliga explicitamente.
let conectadoWs = true
vi.mock('../../hooks/useMonitoringSocket', () => ({
  useMonitoringSocket: () => ({
    connected: conectadoWs,
    detections: deteccoesWs,
    alerts: [],
    subscribeCamera: vi.fn(),
    unsubscribeCamera: vi.fn(),
    clearAlerts: vi.fn(),
  }),
}))

// hls.js não faz parte do que se verifica aqui — mas a URL que chega ao player
// faz, e é por isso que o dublê a expõe no DOM.
vi.mock('../../components/monitoring/CameraPlayer', () => ({
  CameraPlayer: ({ cameraId, hlsUrl }: { cameraId: string; hlsUrl: string }) => (
    <div data-testid="player" data-camera={cameraId} data-url={hlsUrl} />
  ),
}))

// ── Dados ───────────────────────────────────────────────────────────────────

const camera = (id: string, name: string, is_active = true, site_id: string | null = null): Camera => ({
  id,
  name,
  manufacturer: 'intelbras',
  host: '10.0.0.9',
  port: 554,
  channel: 1,
  is_active,
  created_at: '2026-08-01T00:00:00Z',
  location: 'DOCA NORTE',
  fps_target: 12,
  site_id,
})

const CAMS = [camera('cam-1', 'CAM-01 DOCA NORTE'), camera('cam-2', 'CAM-02 PORTARIA')]

const tokenizada = (id: string) => `/api/cameras/${id}/stream/s/1799999999.assinatura/stream.m3u8`

/**
 * Node 25 traz Web Storage nativo e ele colide com o do jsdom neste ambiente
 * (mesmo achado de `Dashboard.test.tsx`/`tenantContextRenewal.test.ts`) — o
 * global real perde métodos. Storage in-memory resolve, e de quebra isola os
 * layouts salvos entre casos.
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

function respondeCameras(lista: Camera[] = CAMS) {
  vi.mocked(api.get).mockImplementation((path: string) => {
    if (path.startsWith('/cameras')) return Promise.resolve({ data: { cameras: lista } } as never)
    if (path.startsWith('/alerts')) return Promise.resolve({ data: { alerts: [] } } as never)
    return Promise.reject(new Error(`rota não esperada: ${path}`))
  })
}

const montar = () => render(<MemoryRouter><AoVivo /></MemoryRouter>)

const playersDe = (id: string) =>
  screen.queryAllByTestId('player').filter((e) => e.getAttribute('data-camera') === id)

beforeEach(() => {
  vi.clearAllMocks()
  __resetLiveViewCache()
  vi.stubGlobal('localStorage', new MemoriaStorage())
  permissoes = ['cameras:read']
  deteccoesWs = {}
  conectadoWs = true
  vi.mocked(cameraService.start).mockImplementation((id: string) =>
    Promise.resolve({ camera_id: id, hls_url: tokenizada(id), status: 'started' } as never),
  )
  respondeCameras()
})

// ── 1. A URL tokenizada ─────────────────────────────────────────────────────

describe('fluxo tokenizado de playback', () => {
  it('entrega ao player a URL que o backend assinou — nunca uma montada aqui', async () => {
    montar()
    await waitFor(() => expect(playersDe('cam-1')).toHaveLength(1))

    const player = playersDe('cam-1')[0]!
    // `useLiveView` só prefixa a origem da API — o caminho assinado é o do backend.
    expect(player.getAttribute('data-url')?.endsWith(tokenizada('cam-1'))).toBe(true)
    // O token no PATH é o que faz os .ts relativos herdarem a autorização.
    expect(player.getAttribute('data-url')).toContain('/stream/s/')
    expect(cameraService.start).toHaveBeenCalledWith('cam-1')
  })

  it('câmera arquivada não entra na parede — nem como ladrilho morto, nem token', async () => {
    // O defeito relatado pelo dono: a arquivada continuava na grade como um
    // quadro "Câmera arquivada" que ele não tinha como tirar. "Ela fica pra
    // sempre." Agora ela não chega à parede; o cabeçalho/estado vazio diz
    // para onde foi, com link — sumir calado seria a outra mentira.
    respondeCameras([camera('cam-3', 'CAM-03 ARQUIVADA', false)])
    montar()
    await screen.findByText('Nenhuma câmera ativa neste site')
    expect(screen.queryByText('CAM-03 ARQUIVADA')).toBeNull()
    expect(screen.queryByText('Câmera arquivada')).toBeNull()
    expect(cameraService.start).not.toHaveBeenCalled()
    // Caminho de volta que leva a lugar DIFERENTE de onde se está.
    expect(screen.getByRole('link', { name: 'Ir para Câmeras' }).getAttribute('href'))
      .toBe(rotaNova('/epi/cameras'))
  })

  it('site com ativa e arquivada: só a ativa na grade, a arquivada contada no cabeçalho', async () => {
    respondeCameras([camera('cam-1', 'CAM-01 DOCA NORTE'), camera('cam-9', 'CAM-09 ARQUIVADA', false)])
    montar()
    await screen.findByText('CAM-01 DOCA NORTE')
    expect(screen.queryByText('CAM-09 ARQUIVADA')).toBeNull()
    expect(screen.getByText('1 CÂMERAS')).toBeTruthy()
    const linkInativas = screen.getByRole('link', { name: /1 INATIVAS, FORA DA PAREDE/ })
    expect(linkInativas.getAttribute('href')).toBe(rotaNova('/epi/cameras'))
  })
})

// ── 2. Sessão única por câmera ──────────────────────────────────────────────

describe('sessão única de playback', () => {
  it('abrir a gaveta não deixa dois players vivos para a mesma câmera', async () => {
    montar()
    await waitFor(() => expect(playersDe('cam-1')).toHaveLength(1))

    fireEvent.click(screen.getByLabelText('Abrir CAM-01 DOCA NORTE'))
    await screen.findByLabelText('Detalhes de CAM-01 DOCA NORTE')

    await waitFor(() => expect(playersDe('cam-1')).toHaveLength(1))
    // A outra câmera segue tocando na grade — a gaveta não para a operação.
    expect(playersDe('cam-2')).toHaveLength(1)

    fireEvent.click(screen.getByLabelText('Fechar painel'))
    await waitFor(() => expect(playersDe('cam-1')).toHaveLength(1))
  })

  // D-56: dois tokens vivos para a mesma câmera baixando o mesmo .ts em
  // produção — a origem era card+gaveta cada um mintando seu próprio
  // /stream/start sem se coordenar (ver MonitoringPage.test.tsx original,
  // arquivado em archive/front-antigo-epi-lote1-2026-08-30).
  it('abrir+fechar a gaveta NÃO dispara segundo /stream/start nem token novo', async () => {
    // cam-2 também sobe seu player na grade — a contagem é POR câmera.
    const chamadasCam1 = () =>
      vi.mocked(cameraService.start).mock.calls.filter((c) => c[0] === 'cam-1').length

    montar()
    await waitFor(() => expect(playersDe('cam-1')).toHaveLength(1))
    expect(chamadasCam1()).toBe(1)

    fireEvent.click(screen.getByLabelText('Abrir CAM-01 DOCA NORTE'))
    await screen.findByLabelText('Detalhes de CAM-01 DOCA NORTE')
    await waitFor(() => expect(playersDe('cam-1')).toHaveLength(1))
    // Cache do useLiveView reaproveitado — nenhum segundo token mintado.
    expect(chamadasCam1()).toBe(1)

    fireEvent.click(screen.getByLabelText('Fechar painel'))
    await waitFor(() => expect(playersDe('cam-1')).toHaveLength(1))
    // Reaquisição do card ao fechar também não minta token novo.
    expect(chamadasCam1()).toBe(1)
  })
})

// ── 3. Bounding box não é alvo de clique ────────────────────────────────────

describe('overlay de detecção', () => {
  const DET: Detection[] = [
    { class: 'sem_capacete', confidence: 0.91, bbox: [64, 36, 128, 180], is_violation: true },
  ]

  it('a camada de caixas tem pointerEvents none e zero onClick', async () => {
    deteccoesWs = { 'cam-1': DET }
    montar()
    await screen.findByText('CAM-01 DOCA NORTE')

    const camada = screen.getAllByTestId('camada-caixas')[0]!
    expect(camada.style.pointerEvents).toBe('none')
    for (const cx of screen.getAllByTestId('caixa-deteccao')) {
      expect(cx.onclick).toBeNull()
    }
  })

  it('converte a bbox para % do ladrilho — o ladrilho é responsivo', async () => {
    deteccoesWs = { 'cam-1': DET }
    montar()
    await screen.findByText('CAM-01 DOCA NORTE')

    const cx = screen.getAllByTestId('caixa-deteccao')[0]!
    expect(cx.style.left).toBe('10%') // 64 / 640
    expect(cx.style.top).toBe('10%') //  36 / 360
    expect(cx.style.width).toBe('20%') // 128 / 640
  })

  it('desligar o toggle apaga as caixas', async () => {
    deteccoesWs = { 'cam-1': DET }
    montar()
    await screen.findByText('CAM-01 DOCA NORTE')
    expect(screen.queryAllByTestId('caixa-deteccao').length).toBeGreaterThan(0)

    fireEvent.click(screen.getByRole('button', { name: /Overlay de detecção/ }))
    expect(screen.queryAllByTestId('caixa-deteccao')).toHaveLength(0)
  })
})

// ── Layout: presets, colunas, destaque ──────────────────────────────────────

describe('layout da grade', () => {
  it('preset define as colunas', async () => {
    montar()
    await screen.findByText('CAM-01 DOCA NORTE')

    expect(screen.getByTestId('grade').style.gridTemplateColumns).toBe('repeat(2, 1fr)')
    fireEvent.click(screen.getByRole('button', { name: '3×3' }))
    expect(screen.getByTestId('grade').style.gridTemplateColumns).toBe('repeat(3, 1fr)')
  })

  it('o passo de colunas anda entre 2 e 6', async () => {
    montar()
    await screen.findByText('CAM-01 DOCA NORTE')

    fireEvent.click(screen.getByLabelText('Mais colunas'))
    expect(screen.getByTestId('colunas-valor').textContent).toBe('5')
    fireEvent.click(screen.getByLabelText('Mais colunas'))
    expect(screen.getByTestId('colunas-valor').textContent).toBe('6')
    expect(screen.getByLabelText('Mais colunas')).toHaveProperty('disabled', true)
  })

  it('em >=5 colunas o estado vira só o ícone e o rótulo da bbox some', async () => {
    deteccoesWs = { 'cam-1': [{ class: 'capacete', confidence: 0.9, bbox: [0, 0, 10, 10] }] }
    montar()
    await screen.findByText('CAM-01 DOCA NORTE')
    expect(screen.getAllByText(/ONLINE/).length).toBeGreaterThan(0)

    fireEvent.click(screen.getByLabelText('Mais colunas')) // 5 colunas
    expect(screen.queryByText('● ONLINE')).toBeNull()
    expect(screen.getAllByTitle('● ONLINE').length).toBeGreaterThan(0)
    expect(screen.queryByText(/capacete 90%/)).toBeNull()
  })

  it('DESTAQUE mostra uma grande e as vizinhas no trilho', async () => {
    montar()
    await screen.findByText('CAM-01 DOCA NORTE')

    fireEvent.click(screen.getByRole('button', { name: 'Destacar CAM-02 PORTARIA' }))
    expect(screen.queryByTestId('grade')).toBeNull()
    // As duas seguem na tela: uma em foco, a outra no trilho.
    expect(screen.getByText('CAM-02 PORTARIA')).toBeTruthy()
    expect(screen.getByText('CAM-01 DOCA NORTE')).toBeTruthy()
  })
})

// ── Estados honestos ────────────────────────────────────────────────────────

describe('estados da tela', () => {
  it('sem câmera: vazio honesto com o caminho de cadastro', async () => {
    respondeCameras([])
    montar()
    await screen.findByText('Nenhuma câmera neste site')
    expect(screen.getByText('Adicionar câmera').getAttribute('href')).toBe(rotaNova('/epi/cameras'))
    expect(screen.queryByTestId('player')).toBeNull()
  })

  it('erro: mostra a falha real e o retry refaz a chamada', async () => {
    vi.mocked(api.get).mockRejectedValueOnce(new Error('Conexão recusada'))
    montar()
    await screen.findByText('Falha ao conectar ao gateway de vídeo')
    expect(screen.getByText('Conexão recusada')).toBeTruthy()
    expect(screen.queryByText(/GET \/cameras/)).toBeNull()

    respondeCameras()
    fireEvent.click(screen.getByRole('button', { name: 'Tentar novamente' }))
    await screen.findByText('CAM-01 DOCA NORTE')
  })

  it('sem cameras:read não busca câmera nenhuma', async () => {
    permissoes = []
    montar()
    await screen.findByText('Sem permissão para ver câmeras')
    expect(screen.queryByText('cameras:read')).toBeNull()
    expect(api.get).not.toHaveBeenCalled()
  })

  it('o cabeçalho conta o que o backend afirma, não o que o player parece', async () => {
    // Conta as que ESTÃO na parede; a inativa aparece à parte, não somada —
    // "3 CÂMERAS" quando só 2 podem acender era o número errado no lugar mais
    // visível da tela.
    respondeCameras([...CAMS, camera('cam-9', 'CAM-09 ARQUIVADA', false)])
    montar()
    await screen.findByText('2 CÂMERAS')
    expect(screen.getByText(/1 INATIVAS, FORA DA PAREDE/)).toBeTruthy()
  })
})

// ── Gaveta ──────────────────────────────────────────────────────────────────

describe('gaveta da câmera', () => {
  it('mostra só dado que a API devolve e lista eventos reais da câmera', async () => {
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path.startsWith('/cameras')) return Promise.resolve({ data: { cameras: CAMS } } as never)
      if (path === '/alerts?camera_id=cam-1&per_page=5') {
        return Promise.resolve({
          data: {
            alerts: [
              { id: 'a1', class_name: 'Sem capacete', captured_at: '2026-08-27T17:32:00Z' },
            ],
          },
        } as never)
      }
      return Promise.reject(new Error(`rota não esperada: ${path}`))
    })

    montar()
    await screen.findByText('CAM-01 DOCA NORTE')
    fireEvent.click(screen.getByLabelText('Abrir CAM-01 DOCA NORTE'))

    const gaveta = await screen.findByLabelText('Detalhes de CAM-01 DOCA NORTE')
    expect(within(gaveta).getByText('12 FPS')).toBeTruthy()
    expect(within(gaveta).getByText('DOCA NORTE')).toBeTruthy()

    const evento = await within(gaveta).findByText('Sem capacete')
    expect(evento.closest('a')?.getAttribute('href')).toBe(rotaNova('/epi/eventos/a1'))
  })

  it('câmera sem evento: vazio honesto, não lista inventada', async () => {
    montar()
    await screen.findByText('CAM-01 DOCA NORTE')
    fireEvent.click(screen.getByLabelText('Abrir CAM-01 DOCA NORTE'))

    const gaveta = await screen.findByLabelText('Detalhes de CAM-01 DOCA NORTE')
    await within(gaveta).findByText('Nenhum evento nesta câmera')
  })
})

// ── Parede POR SITE ─────────────────────────────────────────────────────────

describe('agrupamento por site', () => {
  it('1 site só: o seletor de site some e a grade mostra tudo', async () => {
    montar()
    await screen.findByText('CAM-01 DOCA NORTE')
    expect(screen.queryByLabelText('Site')).toBeNull()
    expect(screen.getByText('CAM-02 PORTARIA')).toBeTruthy()
  })

  it('2+ sites: seletor filtra a grade pelo site_id que já vem da câmera', async () => {
    respondeCameras([
      camera('cam-1', 'CAM-01 DOCA NORTE', true, 'site-a'),
      camera('cam-2', 'CAM-02 PORTARIA', true, 'site-a'),
      camera('cam-3', 'CAM-03 GALPAO', true, 'site-b'),
    ])
    montar()
    await screen.findByText('CAM-01 DOCA NORTE')

    // Default = primeiro site na ordem em que aparece.
    expect(screen.getByText('CAM-02 PORTARIA')).toBeTruthy()
    expect(screen.queryByText('CAM-03 GALPAO')).toBeNull()
    expect(screen.getByText('2 CÂMERAS')).toBeTruthy()

    fireEvent.change(screen.getByLabelText('Site'), { target: { value: 'site-b' } })
    await screen.findByText('CAM-03 GALPAO')
    expect(screen.queryByText('CAM-01 DOCA NORTE')).toBeNull()
    expect(screen.getByText('1 CÂMERAS')).toBeTruthy()
  })
})

// ── Lei: preset define colunas, nunca pagina (intacta após o site+layouts) ──

describe('lei preset=colunas', () => {
  it('preset continua definindo só as colunas — nenhuma câmera some da grade', async () => {
    montar()
    await screen.findByText('CAM-01 DOCA NORTE')

    fireEvent.click(screen.getByRole('button', { name: '4×3' }))
    expect(screen.getByTestId('grade').style.gridTemplateColumns).toBe('repeat(4, 1fr)')
    expect(screen.getByText('CAM-01 DOCA NORTE')).toBeTruthy()
    expect(screen.getByText('CAM-02 PORTARIA')).toBeTruthy()
  })
})

// ── Layouts nomeados (localStorage por site) ────────────────────────────────

describe('meus layouts', () => {
  it('salvar cria um chip aplicado e persiste em lk-parede:<site>', async () => {
    vi.spyOn(window, 'prompt').mockReturnValue('Portaria + Estoque')
    montar()
    await screen.findByText('CAM-01 DOCA NORTE')

    expect(screen.getByText('0 de 10 layouts')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /Salvar layout atual/ }))

    expect(await screen.findByText('Portaria + Estoque')).toBeTruthy()
    expect(screen.getByText('1 de 10 layouts')).toBeTruthy()
    const salvo = JSON.parse(localStorage.getItem('lk-parede:sem-site') ?? '[]')
    expect(salvo).toEqual([
      { nome: 'Portaria + Estoque', colunas: 2, slots: ['cam-1', 'cam-2'] },
    ])
  })

  it('remove com o X e some da tela e do localStorage', async () => {
    vi.spyOn(window, 'prompt').mockReturnValue('Turno da noite')
    montar()
    await screen.findByText('CAM-01 DOCA NORTE')
    fireEvent.click(screen.getByRole('button', { name: /Salvar layout atual/ }))
    await screen.findByText('Turno da noite')

    fireEvent.click(screen.getByLabelText('Remover layout Turno da noite'))
    expect(screen.queryByText('Turno da noite')).toBeNull()
    expect(screen.getByText('0 de 10 layouts')).toBeTruthy()
    expect(JSON.parse(localStorage.getItem('lk-parede:sem-site') ?? '[]')).toEqual([])
  })

  it('respeita o limite de 10 layouts — o botão de salvar desliga', async () => {
    const dez = Array.from({ length: 10 }, (_, i) => ({
      nome: `Layout ${i}`,
      colunas: 2,
      slots: ['cam-1', 'cam-2'],
    }))
    localStorage.setItem('lk-parede:sem-site', JSON.stringify(dez))

    montar()
    await screen.findByText('CAM-01 DOCA NORTE')
    expect(screen.getByText('10 de 10 layouts')).toBeTruthy()
    expect(screen.getByRole('button', { name: /Salvar layout atual/ })).toHaveProperty(
      'disabled',
      true,
    )
  })

  it('aplicar um layout troca a grade para os slots salvos (com vazio) e clicar de novo volta pra grade completa', async () => {
    localStorage.setItem(
      'lk-parede:sem-site',
      JSON.stringify([{ nome: 'Só a portaria', colunas: 3, slots: ['cam-2', null] }]),
    )
    montar()
    await screen.findByText('CAM-01 DOCA NORTE')

    fireEvent.click(screen.getByText('Só a portaria'))
    await waitFor(() => expect(screen.queryByTestId('grade')).toBeNull())
    expect(screen.getByTestId('grade-montagem').style.gridTemplateColumns).toBe('repeat(3, 1fr)')
    expect(screen.getByText('CAM-02 PORTARIA')).toBeTruthy()
    expect(screen.queryByText('CAM-01 DOCA NORTE')).toBeNull()

    fireEvent.click(screen.getByText('Só a portaria'))
    await waitFor(() => expect(screen.queryByTestId('grade-montagem')).toBeNull())
    expect(screen.getByText('CAM-01 DOCA NORTE')).toBeTruthy()
  })

  it('modo Montar: escolher câmera num quadro vazio preenche o slot', async () => {
    localStorage.setItem(
      'lk-parede:sem-site',
      JSON.stringify([{ nome: 'Parcial', colunas: 2, slots: ['cam-1', null] }]),
    )
    montar()
    await screen.findByText('CAM-01 DOCA NORTE')
    fireEvent.click(screen.getByText('Parcial'))
    await screen.findByTestId('grade-montagem')

    fireEvent.click(screen.getByRole('button', { name: 'Montar' }))
    const escolher = screen.getByLabelText('Escolher câmera para o quadro 2')
    fireEvent.change(escolher, { target: { value: 'cam-2' } })

    await waitFor(() => expect(screen.getAllByText('CAM-02 PORTARIA').length).toBeGreaterThan(0))
    const salvo = JSON.parse(localStorage.getItem('lk-parede:sem-site') ?? '[]') as Array<{
      nome: string
    }>
    // Ainda não salvou — a edição é só no estado até "Salvar layout atual".
    expect(salvo[0]?.nome).toBe('Parcial')
  })

  it('modo Montar: o X tira a câmera do quadro e NÃO exclui a câmera', async () => {
    // As duas ações são diferentes e o dono pediu as duas. Esta é a de tirar
    // do grid: a câmera volta no seletor do quadro vago e nenhuma rota de
    // exclusão é chamada. Antes, "remover" só existia como opção escondida
    // dentro do <select> rotulado "Trocar câmera desta posição".
    montar()
    await screen.findByText('CAM-01 DOCA NORTE')
    fireEvent.click(screen.getByRole('button', { name: 'Montar' }))
    await screen.findByTestId('grade-montagem')

    fireEvent.click(screen.getByRole('button', { name: 'Tirar CAM-01 DOCA NORTE do quadro 1' }))

    // O ladrilho da câmera sai da parede (o nome ainda existe como <option>
    // nos seletores — por isso a asserção é no ladrilho, não no texto solto).
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: 'Abrir CAM-01 DOCA NORTE' })).toBeNull(),
    )
    // Quadro virou vago e a câmera está de volta no seletor — não sumiu do
    // sistema, só desta parede.
    const escolher = screen.getByRole('combobox', { name: 'Escolher câmera para o quadro 1' })
    expect(within(escolher).getByRole('option', { name: 'CAM-01 DOCA NORTE' })).toBeTruthy()
    expect(api.delete).not.toHaveBeenCalled()
  })

  it('modo Montar: o menu de trocar não oferece mais "remover" — isso é o X', async () => {
    montar()
    await screen.findByText('CAM-01 DOCA NORTE')
    fireEvent.click(screen.getByRole('button', { name: 'Montar' }))
    await screen.findByTestId('grade-montagem')

    const trocar = screen.getByRole('combobox', { name: 'Trocar câmera do quadro 1' })
    expect(within(trocar).queryByText('— remover —')).toBeNull()
    // Todas as opções são câmeras: nenhuma opção de valor vazio sobrou.
    expect(within(trocar).queryByRole('option', { name: '' })).toBeNull()
  })
})

// ── §11 — aviso de sinal caído ──────────────────────────────────────────────

describe('aviso de sinal caído (§11)', () => {
  it('mostra SEM SINAL no ladrilho quando o WebSocket de detecções está fora', async () => {
    conectadoWs = false
    montar()
    await screen.findByText('CAM-01 DOCA NORTE')
    expect(screen.getAllByText('SEM SINAL').length).toBeGreaterThan(0)
  })

  it('não mostra o aviso com o WebSocket conectado', async () => {
    conectadoWs = true
    montar()
    await screen.findByText('CAM-01 DOCA NORTE')
    expect(screen.queryByText('SEM SINAL')).toBeNull()
  })
})

// ── §13 — local e módulo no quadrinho ────────────────────────────────────────

describe('local e módulo no quadrinho (§13)', () => {
  it('mostra local e módulo como linha secundária no ladrilho — dado que GET /cameras já entrega', async () => {
    respondeCameras([{ ...camera('cam-1', 'CAM-01 DOCA NORTE'), module_code: 'epi' }])
    montar()
    await screen.findByText('CAM-01 DOCA NORTE')
    expect(screen.getByText('DOCA NORTE · EPI')).toBeTruthy()
  })
})
