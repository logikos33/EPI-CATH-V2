/**
 * scheduleTenantContextRenewal / cancelTenantContextRenewal (D-48, opção C).
 *
 * O token de contexto assumido tem TTL curto (30min, TENANT_CONTEXT_TTL_MINUTES
 * em app/core/tenant_context.py). Sem renovação proativa, uma sessão de
 * trabalho mais longa cairia no meio (branch 401 restaura o superadmin —
 * services/api.ts). Este teste tranca: dispara ~5min antes do TTL (25min),
 * troca o token SEM reload, reagenda em caso de sucesso, e NÃO reagenda em
 * caso de falha (deixa expirar naturalmente).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api, TOKEN_KEY, TENANT_CONTEXT_META_KEY } from '../../services/api'
import { cancelTenantContextRenewal, scheduleTenantContextRenewal } from '../../services/tenantContext'

vi.mock('../../services/api', async () => {
  const actual = await vi.importActual<typeof import('../../services/api')>('../../services/api')
  return { ...actual, api: { ...actual.api, post: vi.fn() } }
})

// sessionStorage real é pouco confiável neste ambiente de teste (mesmo motivo
// documentado em tenantContextExpiry.test.ts) — substituído por um Storage
// in-memory via vi.stubGlobal.
class MemoryStorage implements Storage {
  private store = new Map<string, string>()
  get length(): number { return this.store.size }
  clear(): void { this.store.clear() }
  getItem(key: string): string | null { return this.store.has(key) ? this.store.get(key)! : null }
  key(index: number): string | null { return Array.from(this.store.keys())[index] ?? null }
  removeItem(key: string): void { this.store.delete(key) }
  setItem(key: string, value: string): void { this.store.set(key, String(value)) }
}

// TENANT_CONTEXT_TTL_MINUTES (30) - RENEW_BEFORE_EXPIRY_MINUTES (5), espelhado
// de services/tenantContext.ts (não exportado — cálculo replicado aqui).
const RENEW_DELAY_MS = (30 - 5) * 60_000

function activateContext(tenantId = 'tenant-rvb'): void {
  sessionStorage.setItem(
    TENANT_CONTEXT_META_KEY,
    JSON.stringify({
      tenant_id: tenantId,
      tenant_name: 'RVB Isolantes',
      tenant_slug: 'rvb',
      started_at: new Date().toISOString(),
    }),
  )
}

function renewResponse(token: string) {
  return {
    status: 'success',
    data: {
      token,
      tenant: { id: 'tenant-rvb' },
      user: { role: 'superadmin' },
      expires_in_minutes: 30,
    },
  }
}

describe('scheduleTenantContextRenewal', () => {
  beforeEach(() => {
    vi.stubGlobal('sessionStorage', new MemoryStorage())
    vi.stubGlobal('sessionStorage', new MemoryStorage())
    vi.mocked(api.post).mockReset()
    vi.useFakeTimers()
  })

  afterEach(() => {
    cancelTenantContextRenewal()
    vi.clearAllTimers()
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('sem contexto ativo: não agenda nada — renew nunca é chamado', async () => {
    scheduleTenantContextRenewal()

    await vi.advanceTimersByTimeAsync(RENEW_DELAY_MS + 60_000)

    expect(api.post).not.toHaveBeenCalled()
  })

  it('em contexto ativo: renova ~5min antes do TTL, trocando o token sem reload', async () => {
    activateContext()
    vi.mocked(api.post).mockResolvedValue(renewResponse('renewed-token-1'))

    scheduleTenantContextRenewal()
    // Não dispara antes da hora.
    expect(api.post).not.toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(RENEW_DELAY_MS)

    expect(api.post).toHaveBeenCalledWith('/v1/admin/tenant-context/renew', {})
    expect(sessionStorage.getItem(TOKEN_KEY)).toBe('renewed-token-1')
  })

  it('sucesso reagenda a próxima renovação — sessão longa continua renovando', async () => {
    activateContext()
    vi.mocked(api.post)
      .mockResolvedValueOnce(renewResponse('renewed-token-1'))
      .mockResolvedValueOnce(renewResponse('renewed-token-2'))

    scheduleTenantContextRenewal()

    await vi.advanceTimersByTimeAsync(RENEW_DELAY_MS)
    expect(api.post).toHaveBeenCalledTimes(1)
    expect(sessionStorage.getItem(TOKEN_KEY)).toBe('renewed-token-1')

    await vi.advanceTimersByTimeAsync(RENEW_DELAY_MS)
    expect(api.post).toHaveBeenCalledTimes(2)
    expect(sessionStorage.getItem(TOKEN_KEY)).toBe('renewed-token-2')
  })

  // JWT mínimo só com o claim exp — getSessionTokenExpMs lê o payload do
  // token corrente para ancorar o agendamento e decidir se ainda vale insistir.
  function makeJwt(expEpochSec: number): string {
    const payload = btoa(JSON.stringify({ exp: expEpochSec }))
      .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
    return `header.${payload}.sig`
  }

  it('falha no renew com token AINDA vivo: retry curto — a corrente não morre (bug do congelamento 04/08)', async () => {
    // Era "falha não reagenda, best-effort": UMA falha transitória (a API
    // reiniciou 7× naquela noite por cascata de deploys) matava a corrente, o
    // contexto vencia silenciosamente aos 30min e a PRÓXIMA chamada
    // autenticada (renovação de playback, 55min) levava 401 ×8 → /login.
    activateContext()
    sessionStorage.setItem(TOKEN_KEY, makeJwt(Math.floor((Date.now() + 30 * 60_000) / 1000)))
    vi.mocked(api.post)
      .mockRejectedValueOnce(new Error('API reiniciando (deploy)'))
      .mockResolvedValueOnce(renewResponse('renewed-token-1'))

    scheduleTenantContextRenewal()

    // Borda de renovação (exp - 5min = 25min): tentativa falha.
    await vi.advanceTimersByTimeAsync(RENEW_DELAY_MS)
    expect(api.post).toHaveBeenCalledTimes(1)

    // Retry vem em ~30s — com o token ainda vivo — e desta vez renova.
    await vi.advanceTimersByTimeAsync(31_000)
    expect(api.post).toHaveBeenCalledTimes(2)
    expect(sessionStorage.getItem(TOKEN_KEY)).toBe('renewed-token-1')
  })

  it('falha com token JÁ vencido: desiste — o branch 401 de api.ts restaura o superadmin', async () => {
    activateContext()
    sessionStorage.setItem(TOKEN_KEY, makeJwt(Math.floor((Date.now() - 60_000) / 1000)))
    vi.mocked(api.post).mockRejectedValue(new Error('401'))

    scheduleTenantContextRenewal()

    // exp no passado → dispara imediatamente, falha, e NÃO fica martelando.
    await vi.advanceTimersByTimeAsync(1000)
    expect(api.post).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(RENEW_DELAY_MS)
    expect(api.post).toHaveBeenCalledTimes(1)
  })

  it('agendamento ancorado no exp REAL do token, não na constante espelhada', async () => {
    activateContext()
    // Token com só 10min de vida (renew anterior atrasado, relógio, etc.):
    // renovação deve vir em ~5min (exp - margem), não em 25min.
    sessionStorage.setItem(TOKEN_KEY, makeJwt(Math.floor((Date.now() + 10 * 60_000) / 1000)))
    vi.mocked(api.post).mockResolvedValue(renewResponse('renewed-token-1'))

    scheduleTenantContextRenewal()

    await vi.advanceTimersByTimeAsync(4 * 60_000)
    expect(api.post).not.toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(90_000)
    expect(api.post).toHaveBeenCalledTimes(1)
  })

  it('catch-up ao voltar visível com a renovação atrasada: renova já, sem esperar o timer', async () => {
    activateContext()
    vi.mocked(api.post).mockResolvedValue(renewResponse('renewed-token-1'))

    scheduleTenantContextRenewal()
    // Timer pendente no fallback de 25min (token sem exp legível ainda);
    // enquanto isso o token corrente "ficou" a 3min do vencimento (timer
    // estrangulado por aba oculta/sleep é o cenário real).
    sessionStorage.setItem(TOKEN_KEY, makeJwt(Math.floor((Date.now() + 3 * 60_000) / 1000)))

    document.dispatchEvent(new Event('visibilitychange'))
    await vi.advanceTimersByTimeAsync(100)

    expect(api.post).toHaveBeenCalledTimes(1)
    expect(sessionStorage.getItem(TOKEN_KEY)).toBe('renewed-token-1')
  })

  it('cancelTenantContextRenewal impede a renovação agendada de disparar', async () => {
    activateContext()
    vi.mocked(api.post).mockResolvedValue(renewResponse('renewed-token-1'))

    scheduleTenantContextRenewal()
    cancelTenantContextRenewal()

    await vi.advanceTimersByTimeAsync(RENEW_DELAY_MS + 60_000)

    expect(api.post).not.toHaveBeenCalled()
  })

  it('reagendar (assumir de novo) cancela qualquer timer pendente anterior', async () => {
    activateContext()
    vi.mocked(api.post).mockResolvedValue(renewResponse('renewed-token-1'))

    scheduleTenantContextRenewal()
    scheduleTenantContextRenewal() // idempotente — não deve duplicar o timer

    await vi.advanceTimersByTimeAsync(RENEW_DELAY_MS)

    expect(api.post).toHaveBeenCalledTimes(1)
  })
})
