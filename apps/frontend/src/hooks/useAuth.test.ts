/**
 * `renovarSessao` — a ÚNICA ligação entre o botão "Renovar sessão" e o
 * backend (issue #667).
 *
 * Por que este arquivo existe: o teste do cartão injeta `renovar`, e o do
 * Shell mocka o módulo inteiro. Ou seja, nenhum dos dois toca nesta função —
 * caminho errado, envelope errado ou `setToken` esquecido passariam verdes e
 * quebrariam só no chão de fábrica. Aqui a chamada cruza a fronteira HTTP de
 * verdade (fetch dublado, `api.ts` real no meio).
 *
 * O que ela não pode errar:
 *  · POST em /api/auth/refresh levando o token ATUAL no Authorization;
 *  · trocar o token guardado pelo novo e devolver o prazo em ms;
 *  · em qualquer falha, NÃO trocar o token — um token pela metade derruba a
 *    sessão que a renovação existe para salvar.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { TOKEN_KEY } from '../services/api'
import { renovarSessao } from './useAuth'

const fetchDublê = vi.fn()

/** jsdom deste projeto não traz `sessionStorage` — mesmo dublê dos outros testes. */
class MemoriaStorage implements Storage {
  private mapa = new Map<string, string>()
  get length(): number { return this.mapa.size }
  clear(): void { this.mapa.clear() }
  getItem(k: string): string | null { return this.mapa.get(k) ?? null }
  key(i: number): string | null { return Array.from(this.mapa.keys())[i] ?? null }
  removeItem(k: string): void { this.mapa.delete(k) }
  setItem(k: string, v: string): void { this.mapa.set(k, String(v)) }
}

/** Resposta do `fetch` no formato mínimo que o `api.ts` consome. */
const resposta = (body: unknown, status = 200) => ({
  ok: status < 400,
  status,
  json: async () => body,
})

const EXP_SEG = 1_800_000_000

beforeEach(() => {
  vi.stubGlobal('sessionStorage', new MemoriaStorage())
  sessionStorage.setItem(TOKEN_KEY, 'token-velho')
  fetchDublê.mockReset()
  vi.stubGlobal('fetch', fetchDublê)
})
afterEach(() => vi.unstubAllGlobals())

describe('renovarSessao', () => {
  it('troca o token vivo por outro e devolve o prazo novo em ms', async () => {
    fetchDublê.mockResolvedValue(
      resposta({
        success: true,
        message: 'OK',
        data: {
          token: 'token-novo',
          user: { id: 'u1', email: 'op@rvb.com', name: 'Op', role: 'operator' },
          expires_at: EXP_SEG,
        },
      }),
    )

    const prazo = await renovarSessao()

    const [url, init] = fetchDublê.mock.calls[0]
    expect(String(url)).toMatch(/\/api\/auth\/refresh$/)
    expect(init.method).toBe('POST')
    // Leva o token ATUAL: é ele que o backend troca (não há refresh token).
    expect(init.headers.Authorization).toBe('Bearer token-velho')

    expect(sessionStorage.getItem(TOKEN_KEY)).toBe('token-novo')
    expect(prazo).toBe(EXP_SEG * 1000)
    // Papel/permissões vêm relidos do banco: guardar o user novo é o que faz
    // um rebaixamento valer sem esperar o próximo login.
    expect(JSON.parse(sessionStorage.getItem('user') || '{}').email).toBe('op@rvb.com')
  })

  it('401 do JWT não troca o token guardado', async () => {
    // Envelope dos erros de JWT (app/__init__.py) — diferente do
    // {success,error} das rotas. O `api.ts` não acha mensagem aqui e cai no
    // fallback "HTTP 401"; o cartão é quem traduz isso (SessaoExpirando).
    fetchDublê.mockResolvedValue(
      resposta({ status: 'error', data: { error: 'Token revogado.' } }, 401),
    )

    await expect(renovarSessao()).rejects.toThrow()
    expect(sessionStorage.getItem(TOKEN_KEY)).toBe('token-velho')
  })

  it('403 de sessão temporária chega com a mensagem do servidor', async () => {
    fetchDublê.mockResolvedValue(
      resposta(
        {
          success: false,
          error: 'Esta sessão é temporária e não pode ser renovada por aqui.',
          error_code: 'refresh_not_allowed',
        },
        403,
      ),
    )

    await expect(renovarSessao()).rejects.toThrow(/temporária/i)
    expect(sessionStorage.getItem(TOKEN_KEY)).toBe('token-velho')
  })

  it('resposta sem prazo não substitui o token', async () => {
    // Sem `expires_at` o front não sabe até quando a sessão vale. Guardar o
    // token assim mesmo deixaria o aviso contando o prazo velho por cima de
    // um token novo — e a pessoa clicando "Renovar" sem nada acontecer.
    fetchDublê.mockResolvedValue(
      resposta({ success: true, data: { token: 'token-novo' } }),
    )

    await expect(renovarSessao()).rejects.toThrow()
    expect(sessionStorage.getItem(TOKEN_KEY)).toBe('token-velho')
  })
})
