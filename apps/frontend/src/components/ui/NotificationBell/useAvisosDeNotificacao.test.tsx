/**
 * Aviso (pop-up) de notificação — testado com EVENTO INJETADO.
 *
 * Não há como testar isto "de verdade ponta a ponta" hoje, e o motivo está no
 * produto, não no teste: o edge não infere e nada emite reconhecimento ao vivo
 * (o bridge Redis→SocketIO só encaminha `detection`; o evento `alert` que o
 * `useMonitoringSocket` escuta nunca foi emitido por ninguém). Então o aviso
 * NÃO depende de socket — ele observa a lista que o sino já busca, e aqui essa
 * lista é injetada. No dia em que o edge voltar a inferir, o alerta gravado
 * pelo worker entra nessa mesma lista e o aviso dispara sem uma linha nova.
 *
 * Mutações conferidas (cada uma deixa um teste vermelho):
 *  · tirar a guarda da 1ª carga → "acervo pendente não vira pop-up" falha;
 *  · trocar o Set de vistos por comparação de tamanho → "não repete" falha;
 *  · tirar o teto `TETO_NA_TELA` → "não empilha" falha (5 avisos na tela);
 *  · imprimir `created_at` cru em vez de hora local → "data e hora locais" falha.
 */
import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const get = vi.fn()
vi.mock('../../../services/api', () => ({ api: { get: (...a: unknown[]) => get(...a) } }))

import { ToastProvider } from '../Toast/Toast'
import { useToastStore } from '../Toast/useToast'
import type { Notificacao } from '../../../services/notificacoes'
import { useAvisosDeNotificacao } from './useAvisosDeNotificacao'

/** 25/08/2026 14:32 no fuso de quem lê — o ISO sai em UTC, como o backend manda. */
const QUANDO = new Date(2026, 7, 25, 14, 32, 0)

const evento = (id: string, extra: Partial<Notificacao> = {}): Notificacao => ({
  id,
  camera_id: 'cam-expedicao',
  camera_name: 'Entrada Expedição',
  violations: [{ class: 'no_helmet', confidence: 0.9 }],
  acknowledged: false,
  created_at: QUANDO.toISOString(),
  ...extra,
})

const aoAbrir = vi.fn()
const aoAbrirCentral = vi.fn()

function Palco({ lista }: { lista?: Notificacao[] }) {
  useAvisosDeNotificacao(lista, { aoAbrir, aoAbrirCentral })
  return <ToastProvider />
}

beforeEach(() => {
  get.mockReset()
  get.mockResolvedValue({ data: { snapshot_url: 'https://r2.test/frame.jpg' } })
  aoAbrir.mockReset()
  aoAbrirCentral.mockReset()
  useToastStore.setState({ toasts: [] })
})

afterEach(() => {
  vi.useRealTimers()
})

describe('o que NÃO pode acontecer', () => {
  it('acervo pendente na primeira carga não vira pop-up', async () => {
    const { rerender } = render(<Palco lista={[evento('a1'), evento('a2')]} />)
    // Espaço para o efeito assíncrono rodar, se fosse rodar.
    await waitFor(() => expect(get).not.toHaveBeenCalled())
    rerender(<Palco lista={[evento('a1'), evento('a2')]} />)
    await waitFor(() => expect(useToastStore.getState().toasts).toHaveLength(0))
    expect(screen.queryByText('Entrada Expedição')).toBeNull()
  })

  it('a busca ainda em voo não vale como "já vi, estava vazio"', async () => {
    // O sino monta ANTES da primeira resposta. Se o `undefined` desse instante
    // armasse o hook, a primeira resposta de verdade — as 30 pendentes que já
    // estavam lá — viraria pop-up em cima do operador.
    const { rerender } = render(<Palco lista={undefined} />)
    rerender(<Palco lista={undefined} />)
    rerender(<Palco lista={[evento('velho1'), evento('velho2')]} />)
    await new Promise((r) => setTimeout(r, 60))
    expect(useToastStore.getState().toasts).toHaveLength(0)
    // …e o que chegar DEPOIS dessa primeira resposta avisa normalmente.
    rerender(<Palco lista={[evento('novo'), evento('velho1'), evento('velho2')]} />)
    await screen.findByText('Entrada Expedição')
    expect(useToastStore.getState().toasts).toHaveLength(1)
  })

  it('o mesmo evento não reaparece a cada varredura', async () => {
    const { rerender } = render(<Palco lista={[evento('a1')]} />)
    rerender(<Palco lista={[evento('novo'), evento('a1')]} />)
    await screen.findByText('Entrada Expedição')
    expect(useToastStore.getState().toasts).toHaveLength(1)

    // Três varreduras seguidas com a MESMA lista — o backend responde assim
    // enquanto ninguém reconhece nada.
    for (let i = 0; i < 3; i++) rerender(<Palco lista={[evento('novo'), evento('a1')]} />)
    // `waitFor` sozinho passaria cedo demais aqui (o push é assíncrono — espera
    // a miniatura): ele acertaria o instante ANTES do aviso repetido chegar.
    // A prova é dar tempo e então medir. Um push = uma busca de miniatura.
    await new Promise((r) => setTimeout(r, 60))
    expect(useToastStore.getState().toasts).toHaveLength(1)
    expect(get).toHaveBeenCalledTimes(1)
  })

  it('não empilha: 5 novos de uma vez viram 3 avisos + 1 agregado', async () => {
    const { rerender } = render(<Palco lista={[evento('a1')]} />)
    const cinco = ['n1', 'n2', 'n3', 'n4', 'n5'].map((id) => evento(id))
    rerender(<Palco lista={[...cinco, evento('a1')]} />)
    await waitFor(() => expect(useToastStore.getState().toasts).toHaveLength(4))
    await screen.findByText('+2 novas notificações')
  })

  it('com a tela já cheia, o ciclo seguinte não empurra mais nada', async () => {
    const { rerender } = render(<Palco lista={[evento('a1')]} />)
    rerender(<Palco lista={[evento('n1'), evento('n2'), evento('n3'), evento('a1')]} />)
    await waitFor(() => expect(useToastStore.getState().toasts).toHaveLength(3))
    rerender(<Palco lista={[evento('n4'), evento('n1'), evento('n2'), evento('n3'), evento('a1')]} />)
    await waitFor(() => expect(useToastStore.getState().toasts).toHaveLength(3))
  })
})

describe('o que o aviso mostra', () => {
  it('data e hora LOCAIS, câmera e classe — não o ISO cru do backend', async () => {
    const { rerender } = render(<Palco lista={[evento('a1')]} />)
    rerender(<Palco lista={[evento('novo'), evento('a1')]} />)
    await screen.findByText('Entrada Expedição')
    const descricao = await screen.findByText(/Sem capacete/)
    expect(descricao.textContent).toContain('25/08/2026')
    expect(descricao.textContent).toContain('14:32')
    expect(descricao.textContent).not.toContain('T')
    expect(descricao.textContent).not.toContain('Z')
  })

  it('miniatura da evidência quando ela existe', async () => {
    const { rerender } = render(<Palco lista={[evento('a1')]} />)
    rerender(<Palco lista={[evento('novo'), evento('a1')]} />)
    await screen.findByText('Entrada Expedição')
    await waitFor(() =>
      expect(document.querySelector('img[src="https://r2.test/frame.jpg"]')).not.toBeNull(),
    )
    expect(get).toHaveBeenCalledWith('/alerts/novo/snapshot')
  })

  it('sem evidência disponível, o aviso vem SEM imagem — nunca com uma falsa', async () => {
    get.mockRejectedValue(new Error('storage local não assina URL'))
    const { rerender } = render(<Palco lista={[evento('a1')]} />)
    rerender(<Palco lista={[evento('novo'), evento('a1')]} />)
    await screen.findByText('Entrada Expedição')
    expect(document.querySelector('img')).toBeNull()
  })
})

describe('ciclo de vida', () => {
  it('some sozinho em ~30s', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const { rerender } = render(<Palco lista={[evento('a1')]} />)
    rerender(<Palco lista={[evento('novo'), evento('a1')]} />)
    await vi.waitFor(() => expect(useToastStore.getState().toasts).toHaveLength(1))
    await vi.advanceTimersByTimeAsync(29_000)
    expect(useToastStore.getState().toasts).toHaveLength(1)
    await vi.advanceTimersByTimeAsync(2_000)
    expect(useToastStore.getState().toasts).toHaveLength(0)
  })

  it('clicar abre o evento e tira o aviso da tela', async () => {
    const { rerender } = render(<Palco lista={[evento('a1')]} />)
    rerender(<Palco lista={[evento('novo'), evento('a1')]} />)
    const alvo = await screen.findByText('Entrada Expedição')
    alvo.closest('button')!.click()
    expect(aoAbrir).toHaveBeenCalledWith(expect.objectContaining({ id: 'novo' }))
    await waitFor(() => expect(useToastStore.getState().toasts).toHaveLength(0))
  })

  it('fechar não abre o evento', async () => {
    const { rerender } = render(<Palco lista={[evento('a1')]} />)
    rerender(<Palco lista={[evento('novo'), evento('a1')]} />)
    await screen.findByText('Entrada Expedição')
    ;(await screen.findByLabelText('Fechar')).click()
    await waitFor(() => expect(useToastStore.getState().toasts).toHaveLength(0))
    expect(aoAbrir).not.toHaveBeenCalled()
  })

  it('o agregado leva à central', async () => {
    const { rerender } = render(<Palco lista={[evento('a1')]} />)
    const cinco = ['n1', 'n2', 'n3', 'n4', 'n5'].map((id) => evento(id))
    rerender(<Palco lista={[...cinco, evento('a1')]} />)
    const agregado = await screen.findByText('+2 novas notificações')
    agregado.closest('button')!.click()
    expect(aoAbrirCentral).toHaveBeenCalled()
  })
})
