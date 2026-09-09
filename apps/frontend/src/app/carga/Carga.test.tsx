/**
 * O que esta tela não pode errar:
 *
 *  · mostrar contagem que ninguém gravou (0 fingindo medição);
 *  · inventar "BAIA-01" — ou pior, cuspir o UUID de `bay_id` na tela;
 *  · oferecer aceite que o servidor não sabe registrar;
 *  · citar no erro uma rota que não existe (`/api/carga/sessoes` do desenho).
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
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
const remover = vi.fn()
vi.mock('../../services/api', () => ({
  api: {
    get: (...a: unknown[]) => get(...a),
    delete: (...a: unknown[]) => remover(...a),
  },
}))

import { Carga } from './Carga'

/** Precisa de Router desde que "Voltar" (Link) entrou no cabeçalho. */
const montar = () => render(<MemoryRouter><Carga /></MemoryRouter>)

const BAY_UUID = '7f1b1c2e-9a44-4d0e-bd91-6f0a2c5b1234'
const CAM_UUID = 'a1b2c3d4-e5f6-4711-8899-aabbccddeeff'

/** Forma real de `GET /api/counting/sessions` (SELECT cs.*, c.name). */
const sessao = (extra: Record<string, unknown> = {}) => ({
  id: 'sess-1',
  camera_id: CAM_UUID,
  camera_name: 'Doca Expedição',
  status: 'running',
  total_counts: {},
  started_at: '2026-08-29T13:40:00Z',
  bay_id: BAY_UUID,
  truck_plate: null,
  plate_text: null,
  ...extra,
})

/** Forma real de uma linha de `sessions[]` do validation-report. */
const conferida = (extra: Record<string, unknown> = {}) => ({
  id: 'sess-9',
  camera_id: CAM_UUID,
  bay_id: BAY_UUID,
  truck_plate: 'ABC1D23',
  started_at: '2026-08-28T10:12:00Z',
  ended_at: '2026-08-28T11:03:00Z',
  acceptance_status: 'pending',
  manual_count: 140,
  system_count: 142,
  abs_error: 2,
  error_pct: 1.4286,
  passed: true,
  ...extra,
})

interface Fixture {
  sessoes?: unknown[]
  conferidas?: unknown[]
  daily?: unknown[]
  eventos?: unknown[]
}

function responde({ sessoes = [], conferidas = [], daily = [], eventos = [] }: Fixture = {}) {
  const manual = conferidas.reduce(
    (t: number, c) => t + Number((c as { manual_count: number }).manual_count),
    0,
  )
  const sistema = conferidas.reduce(
    (t: number, c) => t + Number((c as { system_count: number }).system_count),
    0,
  )
  get.mockImplementation((rota: string) => {
    const r = String(rota)
    // validation-report ANTES de /counting/sessions: um é prefixo do outro.
    if (r.startsWith('/counting/sessions/validation-report')) {
      return Promise.resolve({
        data: {
          threshold_pct: 5,
          sessions: conferidas,
          daily,
          summary: {
            sessions_validated: conferidas.length,
            system_count: sistema,
            manual_count: manual,
            abs_error: Math.abs(sistema - manual),
            error_pct: manual ? Number(((Math.abs(sistema - manual) / manual) * 100).toFixed(4)) : null,
            passed: true,
          },
        },
      })
    }
    if (r.startsWith('/counting/sessions')) return Promise.resolve({ data: { sessions: sessoes } })
    if (r.startsWith('/fueling/events')) {
      return Promise.resolve({ data: { events: eventos, total: eventos.length } })
    }
    if (r.startsWith('/cameras')) {
      return Promise.resolve({ data: { cameras: [{ id: CAM_UUID, name: 'Doca Expedição' }] } })
    }
    return Promise.resolve({ data: {} })
  })
}

const irPara = (nome: RegExp) => fireEvent.click(screen.getByRole('tab', { name: nome }))

beforeEach(() => {
  get.mockReset()
  remover.mockReset().mockResolvedValue({ data: {} })
  auth.can.mockReset().mockReturnValue(true)
  auth.hasModule.mockReset().mockReturnValue(true)
})

describe('Carga', () => {
  it('sem counting:read, a tela não abre e diz qual permissão falta — e tem saída', () => {
    auth.can.mockReturnValue(false)
    montar()
    expect(screen.getByText('Sem permissão')).toBeTruthy()
    expect(screen.getByText('counting:read')).toBeTruthy()
    expect(get).not.toHaveBeenCalled()
    // Beco sem saída: este ramo pulava o cabeçalho inteiro e não tinha
    // NENHUM link no DOM. Ver becoSemSaida.test.tsx.
    const link = screen.getByRole('link', { name: /voltar/i })
    expect(link.getAttribute('href')).toBe('/novo/modules')
  })

  it('sem o módulo counting, a tela bloqueia e não chama rota nenhuma — e tem saída', () => {
    // Estado REAL do tenant da demo (rvb só tem o módulo epi habilitado).
    auth.hasModule.mockReturnValue(false)
    montar()
    expect(screen.getByText('Módulo não habilitado')).toBeTruthy()
    expect(get).not.toHaveBeenCalled()
    const link = screen.getByRole('link', { name: /voltar/i })
    expect(link.getAttribute('href')).toBe('/novo/modules')
  })

  it('KPI de sessões em andamento é a contagem que o servidor devolveu', async () => {
    responde({ sessoes: [sessao(), sessao({ id: 'sess-2' })], conferidas: [conferida()] })
    montar()
    const kpi = (await screen.findByText('SESSÕES EM ANDAMENTO')).parentElement as HTMLElement
    expect(within(kpi).getByText('2')).toBeTruthy()
  })

  it('a série é DIÁRIA e diz que não existe agregação por hora', async () => {
    responde({
      sessoes: [sessao()],
      conferidas: [conferida()],
      daily: [
        { day: '2026-08-28', sessions: 2, system_total: 200, manual_total: 198, abs_error: 2, error_pct: 1.01, passed: true },
      ],
    })
    montar()
    expect(await screen.findByText('Sessões conferidas por dia')).toBeTruthy()
    expect(screen.getAllByText('28/08').length).toBeGreaterThan(0)
    expect(screen.getByText(/não há agregação por hora|Não há agregação por hora/i)).toBeTruthy()
    // O desenho pede "sessões por hora" — sem fonte, não se inventa o rótulo.
    expect(screen.queryByText(/sessões de carga por hora/i)).toBeNull()
  })

  it('sem contagem gravada, a baia NÃO mostra zero — diz que ninguém contou', async () => {
    // Nada no edge/inferência/worker escreve counting_events: total_counts = {}.
    responde({ sessoes: [sessao()] })
    montar()
    irPara(/baias/i)
    expect(await screen.findByText(/Sem contagem registrada/i)).toBeTruthy()
    expect(screen.queryByText('0')).toBeNull()
  })

  it('a sessão é identificada pela câmera — o UUID da baia nunca vai para a tela', async () => {
    responde({ sessoes: [sessao()] })
    montar()
    irPara(/baias/i)
    expect(await screen.findByText('Doca Expedição')).toBeTruthy()
    expect(screen.queryByText(new RegExp(BAY_UUID, 'i'))).toBeNull()
    // A prosa CITA "BAIA-01" para explicar a ausência; o que não pode existir é um
    // elemento cujo texto SEJA um rótulo de baia.
    expect(screen.queryByText(/^BAIA-\d/)).toBeNull()
  })

  it('encerrar exige confirmação e chama a rota real', async () => {
    responde({ sessoes: [sessao()] })
    montar()
    irPara(/baias/i)
    const botao = await screen.findByRole('button', { name: 'Encerrar sessão' })
    fireEvent.click(botao)
    // Um clique não encerra: a sessão sai da lista e não entra em fila nenhuma.
    expect(remover).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Confirmar encerramento' }))
    await waitFor(() => expect(remover).toHaveBeenCalledWith('/counting/sessions/sess-1'))
  })

  it('sem counting:write, encerrar fica desabilitado e diz por quê', async () => {
    auth.can.mockImplementation((p: string) => p === 'counting:read')
    responde({ sessoes: [sessao()] })
    montar()
    irPara(/baias/i)
    const botao = (await screen.findByRole('button', {
      name: 'Encerrar sessão',
    })) as HTMLButtonElement
    expect(botao.disabled).toBe(true)
    expect(botao.title).toMatch(/counting:write/)
  })

  it('eventos resolvem a câmera pelo nome e não prometem sessão, baia nem placa', async () => {
    responde({
      eventos: [
        { id: 'ev-1', camera_id: CAM_UUID, class_name: 'truck', confidence: 0.91, created_at: '2026-08-29T14:22:00Z' },
      ],
    })
    montar()
    irPara(/eventos/i)
    expect(await screen.findByText('truck')).toBeTruthy()
    expect(screen.getByText('Doca Expedição')).toBeTruthy()
    expect(screen.queryByText(new RegExp(CAM_UUID, 'i'))).toBeNull()
    // Colunas do desenho que a fonte (public.alerts) não tem:
    expect(screen.queryByRole('columnheader', { name: 'SESSÃO' })).toBeNull()
    expect(screen.queryByRole('columnheader', { name: 'BAIA' })).toBeNull()
    expect(screen.queryByRole('columnheader', { name: 'PLACA' })).toBeNull()
  })

  it('o período fica desabilitado onde a rota não aceita data', async () => {
    responde({ eventos: [] })
    montar()
    irPara(/eventos/i)
    const periodo = screen.getByLabelText('Período') as HTMLSelectElement
    await waitFor(() => expect(periodo.disabled).toBe(true))
    expect(periodo.title).toMatch(/limit/)
  })

  it('o seletor de site fica desabilitado — não há filtro de site em rota nenhuma', () => {
    responde()
    montar()
    const site = screen.getByLabelText('Site') as HTMLSelectElement
    expect(site.disabled).toBe(true)
    expect(site.title).toMatch(/site_id/)
  })

  it('os dois aceites ficam no lugar do desenho, DESABILITADOS', async () => {
    // O servidor grava só acceptance_status — não sabe QUAL contagem foi aceita,
    // nem quem aceitou, nem quando.
    responde({ conferidas: [conferida()] })
    montar()
    irPara(/validação/i)
    const sistema = (await screen.findByRole('button', {
      name: /Aceitar contagem do sistema \(142\)/,
    })) as HTMLButtonElement
    const manual = screen.getByRole('button', {
      name: /Aceitar contagem manual \(140\)/,
    }) as HTMLButtonElement
    expect(sistema.disabled).toBe(true)
    expect(manual.disabled).toBe(true)
    expect(sistema.title).toMatch(/acceptance_status/)
  })

  it('a diferença exibida é sistema − manual, com sinal', async () => {
    responde({ conferidas: [conferida()] })
    montar()
    irPara(/validação/i)
    expect(await screen.findByText('+2')).toBeTruthy()
  })

  it('sem sessão conferida, o vazio explica que não existe fila de pendentes', async () => {
    responde({ conferidas: [] })
    montar()
    irPara(/validação/i)
    expect(await screen.findByText('Nenhuma sessão conferida')).toBeTruthy()
    expect(screen.getByText(/Não existe fila de pendentes/i)).toBeTruthy()
    // "N PENDENTES" do desenho não tem como ser calculado.
    expect(screen.queryByText(/PENDENTES$/)).toBeNull()
  })

  it('erro mostra a rota REAL e o retry refaz a chamada', async () => {
    get.mockRejectedValue(new Error('Timeout na requisicao'))
    montar()
    expect(await screen.findByText(/GET \/api\/counting\/sessions · Timeout/)).toBeTruthy()
    // A rota do desenho não existe no backend.
    expect(screen.queryByText(/\/api\/carga\//)).toBeNull()

    responde({ sessoes: [sessao()], conferidas: [conferida()] })
    fireEvent.click(screen.getByRole('button', { name: /tentar novamente/i }))
    expect(await screen.findByText('SESSÕES EM ANDAMENTO')).toBeTruthy()
  })

  it('nada de dado de baia sorteado: /api/fueling/bays e /dashboard não são chamados', async () => {
    responde({ sessoes: [sessao()], conferidas: [conferida()] })
    montar()
    irPara(/baias/i)
    await screen.findByText('Doca Expedição')
    const rotas = get.mock.calls.map((c) => String(c[0]))
    expect(rotas.some((r) => r.startsWith('/fueling/bays'))).toBe(false)
    expect(rotas.some((r) => r.startsWith('/fueling/dashboard'))).toBe(false)
  })

  it('"Voltar" é o primeiro link do cabeçalho e leva à home do usuário', async () => {
    // Sem barra lateral própria (SEM_BARRA_LATERAL), este é o único jeito de
    // sair do módulo — regra global, ver app/shell/becoSemSaida.test.tsx.
    responde({ sessoes: [sessao()] })
    montar()
    const primeiro = (await screen.findAllByRole('link'))[0]
    expect(primeiro.textContent?.trim()).toBe('Voltar')
    // isSuperAdmin: false no dublê → home é a escolha de módulo.
    expect(primeiro.getAttribute('href')).toBe('/novo/modules')
  })
})
