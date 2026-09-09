/**
 * useAutoAssumeTenantContext — auto-assume de contexto (D-48, opção B).
 *
 * Cobre o contrato do hook usado pelo CrossTenantCameraBanner: quando a
 * grade só tem câmeras cross-tenant de UM tenant estrangeiro, assume aquele
 * contexto sozinho (mesmo fluxo auditado de assumeTenantContext). Quando há
 * 2+ tenants, ou já está em contexto, ou um guard anti-loop recente ainda
 * está de pé, NÃO dispara — o banner manual (CrossTenantCameraBanner)
 * continua sendo o fallback.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { renderHook } from '@testing-library/react'
import {
  hasRecentAutoAssumeAttempt,
  useAutoAssumeTenantContext,
} from '../../hooks/useAutoAssumeTenantContext'
import { useCrossTenantCamerasStore } from '../../services/crossTenantCameras'
import { assumeTenantContext } from '../../services/tenantContext'

// sessionStorage/sessionStorage reais são pouco confiáveis neste ambiente de
// teste (mesmo motivo documentado em tenantContextExpiry.test.ts) —
// substituídos por um Storage in-memory via vi.stubGlobal.
class MemoryStorage implements Storage {
  private store = new Map<string, string>()
  get length(): number { return this.store.size }
  clear(): void { this.store.clear() }
  getItem(key: string): string | null { return this.store.has(key) ? this.store.get(key)! : null }
  key(index: number): string | null { return Array.from(this.store.keys())[index] ?? null }
  removeItem(key: string): void { this.store.delete(key) }
  setItem(key: string, value: string): void { this.store.set(key, String(value)) }
}

let mockIsSuperAdmin = true
vi.mock('../../hooks/useAuth', () => ({
  useAuth: () => ({ isSuperAdmin: mockIsSuperAdmin }),
}))

vi.mock('../../services/tenantContext', async () => {
  const actual = await vi.importActual<typeof import('../../services/tenantContext')>(
    '../../services/tenantContext',
  )
  return { ...actual, assumeTenantContext: vi.fn().mockResolvedValue(undefined) }
})

const RVB = { tenantId: 'tenant-rvb', tenantName: 'RVB Isolantes', cameraCount: 1 }
const OUTRO = { tenantId: 'tenant-outro', tenantName: 'Outro Cliente', cameraCount: 1 }

function setForeignTenants(...tenants: (typeof RVB)[]): void {
  const cameras = Object.fromEntries(
    tenants.map((t, i) => [
      `cam-${i}`,
      { cameraId: `cam-${i}`, tenantId: t.tenantId, tenantName: t.tenantName },
    ]),
  )
  useCrossTenantCamerasStore.setState({ cameras, tenants })
}

describe('useAutoAssumeTenantContext', () => {
  beforeEach(() => {
    mockIsSuperAdmin = true
    vi.stubGlobal('sessionStorage', new MemoryStorage())
    vi.stubGlobal('sessionStorage', new MemoryStorage())
    useCrossTenantCamerasStore.setState({ cameras: {}, tenants: [] })
    vi.mocked(assumeTenantContext).mockClear()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('superadmin + 1 tenant estrangeiro + fora de contexto → assume uma vez e grava o guard', () => {
    setForeignTenants(RVB)

    renderHook(() => useAutoAssumeTenantContext())

    expect(assumeTenantContext).toHaveBeenCalledTimes(1)
    // 2º argumento = a tela corrente. O auto-assume dispara COM a grade de
    // monitoramento aberta; sem destino explícito ele caía no default do
    // serviço, que era `'/'` — e `/` logado entregava o front ANTIGO (#760).
    expect(assumeTenantContext).toHaveBeenCalledWith(
      'tenant-rvb',
      window.location.pathname + window.location.search,
    )
    expect(hasRecentAutoAssumeAttempt('tenant-rvb')).toBe(true)
  })

  it('já em contexto assumido → NÃO chama assumeTenantContext', () => {
    sessionStorage.setItem(
      'tenant_context',
      JSON.stringify({
        tenant_id: 'tenant-rvb',
        tenant_name: 'RVB Isolantes',
        tenant_slug: 'rvb',
        started_at: new Date().toISOString(),
      }),
    )
    setForeignTenants(RVB)

    renderHook(() => useAutoAssumeTenantContext())

    expect(assumeTenantContext).not.toHaveBeenCalled()
  })

  it('em contexto ativo: limpa o guard do tenant assumido (permite auto-assume futuro se expirar)', () => {
    sessionStorage.setItem('auto_assume_attempt:tenant-rvb', String(Date.now()))
    sessionStorage.setItem(
      'tenant_context',
      JSON.stringify({
        tenant_id: 'tenant-rvb',
        tenant_name: 'RVB Isolantes',
        tenant_slug: 'rvb',
        started_at: new Date().toISOString(),
      }),
    )

    renderHook(() => useAutoAssumeTenantContext())

    expect(sessionStorage.getItem('auto_assume_attempt:tenant-rvb')).toBeNull()
  })

  it('2 tenants estrangeiros na grade → NÃO auto-assume (banner manual decide)', () => {
    setForeignTenants(RVB, OUTRO)

    renderHook(() => useAutoAssumeTenantContext())

    expect(assumeTenantContext).not.toHaveBeenCalled()
  })

  it('não-superadmin → NÃO auto-assume', () => {
    mockIsSuperAdmin = false
    setForeignTenants(RVB)

    renderHook(() => useAutoAssumeTenantContext())

    expect(assumeTenantContext).not.toHaveBeenCalled()
  })

  it('anti-loop: guard recente (<60s) sem contexto ativo → NÃO re-dispara', () => {
    sessionStorage.setItem('auto_assume_attempt:tenant-rvb', String(Date.now() - 1_000))
    setForeignTenants(RVB)

    renderHook(() => useAutoAssumeTenantContext())

    expect(assumeTenantContext).not.toHaveBeenCalled()
  })

  it('guard expirado (>60s) sem contexto ativo → permite nova tentativa de auto-assume', () => {
    sessionStorage.setItem('auto_assume_attempt:tenant-rvb', String(Date.now() - 61_000))
    setForeignTenants(RVB)

    renderHook(() => useAutoAssumeTenantContext())

    expect(assumeTenantContext).toHaveBeenCalledTimes(1)
  })
})
