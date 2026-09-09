/**
 * Central de notificações — o rol com histórico.
 *
 * O que estes testes travam:
 *  · o recorte que a aba PEDE (não-lidas manda `acknowledged=false`; "Todas"
 *    não manda o parâmetro — mandar `true` traria só as lidas, que é o
 *    histórico pela metade);
 *  · "marcar todas" é UMA requisição em lote, e passa por confirmação;
 *  · abrir uma notificação marca como lida — e uma JÁ lida não gasta POST;
 *  · o vazio é dito com honestidade, e tem saída (regra C2);
 *  · a data aparece em hora LOCAL, por extenso.
 *
 * Mutações conferidas (cada uma deixa um teste vermelho):
 *  · aba "Todas" mandando `acknowledged=true`;
 *  · "marcar todas" iterando `/alerts/<id>/acknowledge` no cliente;
 *  · marcar como lida também no item já lido.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const get = vi.fn()
const post = vi.fn()
vi.mock('../../services/api', () => ({
  api: {
    get: (...a: unknown[]) => get(...a),
    post: (...a: unknown[]) => post(...a),
  },
}))

const auth = vi.hoisted(() => ({ can: vi.fn((_p: string) => true) }))
vi.mock('../../hooks/useAuth', () => ({ useAuth: () => auth }))

const navigate = vi.fn()
vi.mock('react-router-dom', async () => {
  const real = await vi.importActual<Record<string, unknown>>('react-router-dom')
  return { ...real, useNavigate: () => navigate }
})

import { Notificacoes } from './Notificacoes'

/** 25/08/2026 14:32 no fuso de quem lê — o backend manda o ISO em UTC. */
const QUANDO = new Date(2026, 7, 25, 14, 32, 0)

const linha = (id: string, extra: Record<string, unknown> = {}) => ({
  id,
  camera_id: 'cam-expedicao',
  camera_name: 'Entrada Expedição',
  violations: [{ class: 'no_helmet', confidence: 0.9 }],
  acknowledged: false,
  created_at: QUANDO.toISOString(),
  ...extra,
})

function responderCom(alerts: unknown[], extra: Record<string, unknown> = {}) {
  get.mockResolvedValue({
    data: { alerts, total: alerts.length, pages: 1, page: 1, ...extra },
  })
}

function montar() {
  const cliente = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={cliente}>
      <MemoryRouter>
        <Notificacoes />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

/** Última querystring pedida ao backend. */
function ultimaConsulta(): URLSearchParams {
  const url = get.mock.calls.at(-1)?.[0] as string
  return new URLSearchParams(url.split('?')[1] ?? '')
}

beforeEach(() => {
  get.mockReset()
  post.mockReset()
  post.mockResolvedValue({ data: { acknowledged: 235 } })
  navigate.mockReset()
  auth.can.mockImplementation(() => true)
})

describe('o recorte que cada aba pede', () => {
  it('abre em "Não lidas" e pede acknowledged=false', async () => {
    responderCom([linha('a1')])
    montar()
    await screen.findByText('Entrada Expedição')
    expect(ultimaConsulta().get('acknowledged')).toBe('false')
    expect(ultimaConsulta().get('kind')).toBe('violation')
  })

  it('"Todas" NÃO manda acknowledged — histórico é lidas E não lidas', async () => {
    responderCom([linha('a1')])
    montar()
    await screen.findByText('Entrada Expedição')
    ;(await screen.findByRole('tab', { name: 'Todas' })).click()
    await waitFor(() => expect(ultimaConsulta().has('acknowledged')).toBe(false))
  })
})

describe('marcar como lida', () => {
  it('"Marcar todas" pede confirmação e manda UMA requisição em lote', async () => {
    responderCom([linha('a1')])
    montar()
    await screen.findByText('Entrada Expedição')
    ;(await screen.findByRole('button', { name: /Marcar todas como lidas/ })).click()

    // Sem confirmar, nada foi marcado.
    expect(post).not.toHaveBeenCalled()
    ;(await screen.findByRole('button', { name: 'Marcar todas' })).click()

    await waitFor(() => expect(post).toHaveBeenCalledTimes(1))
    expect(post).toHaveBeenCalledWith('/alerts/acknowledge', { all: true, kind: 'violation' })
  })

  it('abrir uma NÃO lida marca como lida e vai ao evento', async () => {
    responderCom([linha('a1')])
    montar()
    ;(await screen.findByRole('button', { name: /Abrir evento de Entrada Expedição/ })).click()
    await waitFor(() => expect(post).toHaveBeenCalledWith('/alerts/acknowledge', { ids: ['a1'] }))
    expect(navigate).toHaveBeenCalledWith(
      '/novo/epi/eventos?camera_id=cam-expedicao&kind=violation&highlight=a1',
    )
  })

  it('abrir uma JÁ lida não gasta requisição — só navega', async () => {
    responderCom([linha('a1', { acknowledged: true })])
    montar()
    ;(await screen.findByRole('button', { name: /Abrir evento de Entrada Expedição/ })).click()
    expect(navigate).toHaveBeenCalled()
    await new Promise((r) => setTimeout(r, 30))
    expect(post).not.toHaveBeenCalled()
  })
})

describe('o que a linha diz', () => {
  it('mostra data e hora LOCAIS por extenso, não o ISO cru', async () => {
    responderCom([linha('a1')])
    montar()
    const quando = await screen.findByText(/25\/08\/2026/)
    expect(quando.textContent).toContain('14:32')
    expect(quando.textContent).not.toContain('Z')
  })

  it('distingue lida de não lida por PALAVRA, não só por cor', async () => {
    responderCom([linha('a1'), linha('a2', { acknowledged: true })])
    montar()
    await screen.findByText('Não lida')
    await screen.findByText('Lida')
  })
})

describe('vazio e permissão', () => {
  it('sem nada por ler, diz o que aconteceu e oferece o histórico', async () => {
    responderCom([])
    montar()
    await screen.findByText('Nenhuma notificação por ler')
    await screen.findByRole('link', { name: /Ver a tela de eventos/ })
  })

  it('sem permissão, nem pede a lista — e ainda tem saída', async () => {
    auth.can.mockImplementation(() => false)
    montar()
    await screen.findByText('Sem permissão')
    expect(get).not.toHaveBeenCalled()
    await screen.findByRole('link', { name: /Ir para o painel/ })
  })
})
