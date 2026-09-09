/**
 * NotificationBell — ux2/dedup.
 *
 * Achado do Vitor: "10 pendentes", todas a MESMA cena de 6 dias atrás (Entrada
 * Expedição · Sem máscara/Sem Luvas · há 6d). Um badge que conta linhas em vez
 * de situações mente sobre quanto trabalho existe — e "clicar na notificação
 * TEM de levar ao evento" (deep-link) já funcionava antes desta rodada; estes
 * testes travam que continua funcionando depois de agrupar.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const get = vi.fn()
const post = vi.fn()
vi.mock('../../../services/api', () => ({
  api: {
    get: (...a: unknown[]) => get(...a),
    post: (...a: unknown[]) => post(...a),
  },
}))

const navigate = vi.fn()
vi.mock('react-router-dom', async () => {
  const real = await vi.importActual<Record<string, unknown>>('react-router-dom')
  return { ...real, useNavigate: () => navigate }
})

import { NotificationBell } from './NotificationBell'
import { useToastStore } from '../Toast/useToast'

const alerta = (id: string, extra: Record<string, unknown> = {}) => ({
  id,
  camera_id: 'cam-expedicao',
  camera_name: 'Entrada Expedição',
  violations: [{ class: 'no_helmet', confidence: 0.9 }],
  acknowledged: false,
  created_at: '2026-08-25T13:39:00Z',
  ...extra,
})

function montar(props: { rotaAlertas?: string; rotaCentral?: string } = {}) {
  const cliente = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={cliente}>
      <MemoryRouter>
        <NotificationBell {...props} />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

async function abrirPainel() {
  const botao = await screen.findByLabelText('Notificações')
  botao.click()
}

beforeEach(() => {
  get.mockReset()
  post.mockReset()
  post.mockResolvedValue({ data: { acknowledged: 1 } })
  navigate.mockReset()
  useToastStore.setState({ toasts: [] })
})

/**
 * ⚠️ O deep-link PERDEU `acknowledged=false` nesta rodada, e é de propósito.
 *
 * Clicar numa notificação passou a MARCÁ-LA COMO LIDA (pedido do dono). Se o
 * destino continuasse filtrando por não-reconhecidos, o usuário clicaria e
 * cairia numa lista onde o evento que ele acabou de abrir não está mais — beco
 * sem saída construído com as próprias mãos. `camera_id`, `kind` e `highlight`
 * continuam, que é o que leva ao evento.
 */
describe('deep-link continua valendo (não regrediu ao agrupar)', () => {
  it('clicar num alerta sem irmãos leva ao evento com highlight', async () => {
    get.mockResolvedValue({ data: { alerts: [alerta('a1')], total: 1, total_situacoes: 1 } })
    montar()
    await abrirPainel()
    const cartao = await screen.findByRole('button', { name: /Entrada Expedição/ })
    cartao.click()
    expect(navigate).toHaveBeenCalledWith(
      '/epi/alerts?camera_id=cam-expedicao&kind=violation&highlight=a1',
    )
  })
})

describe('rajada (ux2/dedup) — badge e painel contam SITUAÇÕES, não linhas', () => {
  it('10 alertas da MESMA câmera+classe em <60s viram 1 situação no badge', async () => {
    const dez = Array.from({ length: 10 }, (_, i) =>
      alerta(`r${i}`, { created_at: new Date(Date.parse('2026-08-25T13:39:00Z') + i * 2000).toISOString() }),
    )
    get.mockResolvedValue({ data: { alerts: dez, total: 10, total_situacoes: 1 } })
    montar()
    await waitFor(() => expect(screen.getByLabelText('Notificações').textContent).toContain('1'))
    await abrirPainel()
    await screen.findByText('1 pendente')
  })

  it('painel mostra 1 cartão representante + alternador "+9 repetições" — nunca 10 cartões idênticos', async () => {
    const dez = Array.from({ length: 10 }, (_, i) =>
      alerta(`r${i}`, { created_at: new Date(Date.parse('2026-08-25T13:39:00Z') + i * 2000).toISOString() }),
    )
    get.mockResolvedValue({ data: { alerts: dez, total: 10, total_situacoes: 1 } })
    montar()
    await abrirPainel()
    expect(await screen.findAllByRole('button', { name: /Entrada Expedição/ })).toHaveLength(1)
    await screen.findByText(/\+9 repetiç/)
  })

  it('expandir revela as N repetições, e cada uma mantém o PRÓPRIO deep-link', async () => {
    const tres = [
      alerta('x1', { created_at: '2026-08-25T13:39:00Z' }),
      alerta('x2', { created_at: '2026-08-25T13:39:10Z' }),
      alerta('x3', { created_at: '2026-08-25T13:39:20Z' }),
    ]
    get.mockResolvedValue({ data: { alerts: tres, total: 3, total_situacoes: 1 } })
    montar()
    await abrirPainel()
    const alternador = await screen.findByText(/\+2 repetiç/)
    alternador.click()
    // As duas repetições (x1, x2 — x3 é o representante mais recente) viram
    // botões clicáveis próprios, não texto morto escondido. O aria-label da
    // repetição usa "·" (o do representante usa ":") — separa os dois grupos.
    const botoesRepeticao = await screen.findAllByRole('button', { name: /Abrir alerta de Entrada Expedição ·/ })
    expect(botoesRepeticao).toHaveLength(2)
    botoesRepeticao[0].click()
    expect(navigate).toHaveBeenCalledWith(expect.stringContaining('highlight=x1'))
  })

  it('sem total_situacoes no payload (backend/mock antigo), o badge cai pro nº de linhas — como sempre foi', async () => {
    get.mockResolvedValue({ data: { alerts: [alerta('y1'), alerta('y2', { camera_id: 'outra-cam' })], total: 2 } })
    montar()
    await waitFor(() => expect(screen.getByLabelText('Notificações').textContent).toContain('2'))
  })
})


/**
 * O front NOVO monta ESTE sino (Shell.tsx). `/epi/alerts` é rota VÁLIDA no app:
 * sem `rotaAlertas`, o sino do front novo jogaria o usuário, calado, na tela
 * ANTIGA — o mesmo pisão que `RotasNovas.tsx` descreve (aconteceu em 10 lugares
 * na primeira leva, e nenhum teste pegou).
 */
describe('deep-link segue o front que montou o sino', () => {
  it('sem prop, continua no endereço do front antigo (TopBar legada)', async () => {
    get.mockResolvedValue({ data: { alerts: [alerta('a1')], total: 1, total_situacoes: 1 } })
    montar()
    await abrirPainel()
    ;(await screen.findByRole('button', { name: /Entrada Expedição/ })).click()
    expect(navigate).toHaveBeenCalledWith(expect.stringContaining('/epi/alerts?'))
  })

  it('com rotaAlertas, cartão E "Ver todos" vão para a tela do front novo', async () => {
    get.mockResolvedValue({ data: { alerts: [alerta('a1')], total: 1, total_situacoes: 1 } })
    montar({ rotaAlertas: '/novo/epi/eventos' })
    await abrirPainel()
    ;(await screen.findByRole('button', { name: /Entrada Expedição/ })).click()
    expect(navigate).toHaveBeenCalledWith(
      '/novo/epi/eventos?camera_id=cam-expedicao&kind=violation&highlight=a1',
    )
    await abrirPainel()
    ;(await screen.findByRole('button', { name: /Ver todos os alertas/ })).click()
    expect(navigate).toHaveBeenCalledWith('/novo/epi/eventos?acknowledged=false&kind=violation')
  })
})

/**
 * issue #803 — o teto tem de aparecer COMO teto.
 *
 * Havia `const count = Math.min(totalSituacoes ?? alerts.length, 99)` antes do
 * render, e por isso o `count > 99 ? '99+' : count` do badge era código morto.
 * O badge dizia `99` e o painel dizia `99 pendentes` com 387 situações abertas
 * (medido no DEV, RVB, 05/09) — não é truncar, é afirmar.
 *
 * Reintroduza o clamp (`Math.min(pendentes, 99)`) e os dois primeiros testes
 * ficam vermelhos: o badge volta a "99" e o painel a "99 pendentes".
 */
describe('#803 — badge trunca, painel não mente', () => {
  it('387 situações: badge mostra "99+" (teto declarado), nunca "99" liso', async () => {
    get.mockResolvedValue({ data: { alerts: [alerta('a1')], total: 514, total_situacoes: 387 } })
    montar()
    const botao = await screen.findByLabelText('Notificações')
    await waitFor(() => expect(botao.textContent).toBe('99+'))
  })

  it('387 situações: o painel imprime 387, não o teto do badge', async () => {
    get.mockResolvedValue({ data: { alerts: [alerta('a1')], total: 514, total_situacoes: 387 } })
    montar()
    await abrirPainel()
    await screen.findByText('387 pendentes')
    expect(screen.queryByText('99 pendentes')).toBeNull()
  })

  it('exatamente 99 continua "99" — o "+" só entra quando há mais', async () => {
    get.mockResolvedValue({ data: { alerts: [alerta('a1')], total: 99, total_situacoes: 99 } })
    montar()
    const botao = await screen.findByLabelText('Notificações')
    await waitFor(() => expect(botao.textContent).toBe('99'))
    await abrirPainel()
    await screen.findByText('99 pendentes')
  })
})


/**
 * "Marcar todas como lida" — o pedido do dono.
 *
 * Antes desta rodada não existia: `Eventos.tsx` fazia laço no CLIENTE sobre
 * `POST /alerts/<id>/acknowledge`, e com 235 pendentes isso é 235 requisições.
 * O sino nem oferecia a ação.
 *
 * Mutação (conferida): fazer o botão iterar sobre `alerts` chamando
 * `/alerts/<id>/acknowledge` deixa o primeiro teste vermelho — e o segundo
 * mostra por quê: as 235 nunca estiveram na tela, só 30 estão.
 */
describe('marcar todas como lidas', () => {
  it('é UMA requisição em lote, não uma por alerta', async () => {
    const trinta = Array.from({ length: 30 }, (_, i) =>
      alerta(`m${i}`, { camera_id: `cam-${i}` }),
    )
    get.mockResolvedValue({ data: { alerts: trinta, total: 235, total_situacoes: 235 } })
    montar()
    await abrirPainel()
    ;(await screen.findByRole('button', { name: /Marcar todas como lidas/ })).click()
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1))
    expect(post).toHaveBeenCalledWith('/alerts/acknowledge', { all: true, kind: 'violation' })
  })

  it('não aparece quando não há nada pendente', async () => {
    get.mockResolvedValue({ data: { alerts: [], total: 0, total_situacoes: 0 } })
    montar()
    await abrirPainel()
    await screen.findByText('Nenhum alerta pendente')
    expect(screen.queryByRole('button', { name: /Marcar todas como lidas/ })).toBeNull()
  })
})

/**
 * "Clicar marca como lida" (pedido do dono). O POST é em LOTE (a mesma rota
 * nova), com um id só — não a rota unitária: um caminho de marcação, não dois.
 */
describe('clicar na notificação marca como lida', () => {
  it('manda o id para a rota de lote E navega para o evento', async () => {
    get.mockResolvedValue({ data: { alerts: [alerta('a1')], total: 1, total_situacoes: 1 } })
    montar({ rotaAlertas: '/novo/epi/eventos' })
    await abrirPainel()
    ;(await screen.findByRole('button', { name: /Entrada Expedição/ })).click()
    await waitFor(() =>
      expect(post).toHaveBeenCalledWith('/alerts/acknowledge', { ids: ['a1'] }),
    )
    expect(navigate).toHaveBeenCalledWith(expect.stringContaining('highlight=a1'))
  })

  it('cada repetição de uma rajada marca a SI MESMA, não o representante', async () => {
    const tres = [
      alerta('x1', { created_at: '2026-08-25T13:39:00Z' }),
      alerta('x2', { created_at: '2026-08-25T13:39:10Z' }),
      alerta('x3', { created_at: '2026-08-25T13:39:20Z' }),
    ]
    get.mockResolvedValue({ data: { alerts: tres, total: 3, total_situacoes: 1 } })
    montar()
    await abrirPainel()
    ;(await screen.findByText(/\+2 repetiç/)).click()
    const repeticoes = await screen.findAllByRole('button', {
      name: /Abrir alerta de Entrada Expedição ·/,
    })
    repeticoes[0].click()
    await waitFor(() =>
      expect(post).toHaveBeenCalledWith('/alerts/acknowledge', { ids: ['x1'] }),
    )
  })
})

/**
 * A central só é oferecida a quem recebeu o endereço dela. Sem a prop, a
 * TopBar legada não ganha um link que joga o usuário do produto velho dentro
 * do novo — mesmo pisão de `rotaAlertas`, na direção contrária.
 */
describe('link para a central de notificações', () => {
  it('não existe sem a prop (TopBar legada)', async () => {
    get.mockResolvedValue({ data: { alerts: [alerta('a1')], total: 1, total_situacoes: 1 } })
    montar()
    await abrirPainel()
    await screen.findByRole('button', { name: /Ver todos os alertas/ })
    expect(screen.queryByRole('button', { name: /Central de notificações/ })).toBeNull()
  })

  it('com a prop, leva ao endereço recebido', async () => {
    get.mockResolvedValue({ data: { alerts: [alerta('a1')], total: 1, total_situacoes: 1 } })
    montar({ rotaAlertas: '/novo/epi/eventos', rotaCentral: '/novo/notificacoes' })
    await abrirPainel()
    ;(await screen.findByRole('button', { name: /Central de notificações/ })).click()
    expect(navigate).toHaveBeenCalledWith('/novo/notificacoes')
  })
})


/**
 * O sino ARMA o pop-up — e é aqui que a fiação se prova, não no teste do hook.
 *
 * A primeira renderização acontece com a busca em voo (`data === undefined`).
 * Passar para o hook o `alerts ?? []` desse instante o armaria com uma lista
 * vazia, e a PRIMEIRA resposta de verdade — as pendentes que já estavam lá —
 * viraria pop-up em cima de quem acabou de abrir a tela.
 *
 * Mutação conferida: trocar `pagina?.alerts` por `alerts` na chamada do hook
 * deixa este teste vermelho (2 avisos na tela).
 */
describe('o acervo que já existia não vira pop-up ao abrir a tela', () => {
  it('a primeira resposta do backend não dispara aviso nenhum', async () => {
    get.mockResolvedValue({
      data: {
        alerts: [alerta('velho1'), alerta('velho2', { camera_id: 'outra-cam' })],
        total: 2,
        total_situacoes: 2,
      },
    })
    montar()
    // Espera o badge provar que a resposta CHEGOU — sem isso o teste passaria
    // por medir antes da hora.
    await waitFor(() => expect(screen.getByLabelText('Notificações').textContent).toContain('2'))
    await new Promise((r) => setTimeout(r, 60))
    expect(useToastStore.getState().toasts).toHaveLength(0)
  })
})
