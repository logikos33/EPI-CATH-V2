/**
 * Serviço de API centralizado.
 *
 * LIÇÃO V1: Token SEMPRE pela mesma chave ('token').
 * LIÇÃO V1: Timeout em todas as requests (15s).
 * LIÇÃO V1: Sem dependência de objeto api.logout() externo.
 */
/**
 * Serviço importando de `app/` — de propósito, e é o único jeito honesto.
 *
 * #760: os `redirect` default daqui eram `'/admin/tenants'` LITERAL, o
 * endereço do painel de tenants do front ANTIGO. Quem clicava em "Sair do
 * contexto" no banner do front NOVO era despejado no produto velho. Repetir
 * `'/novo/admin/tenants'` como literal consertaria o sintoma e criaria o
 * próximo: no tombamento (`PREFIXO_NOVO` some), um literal escondido num
 * serviço não aparece em nenhuma busca por `PREFIXO_NOVO`. `rotaNova()` é a
 * ÚNICA fonte do prefixo — `AppRoutes.tsx` já a importa pelo mesmo motivo.
 */
import { rotaNova } from '../app/RotasNovas'
import { apagarSessao, gravarSessao, lerSessao } from './sessao'

export const TOKEN_KEY = 'token'

/**
 * Error com o status HTTP anexado — retrocompatível (extends Error, então
 * `err instanceof Error` e `err.message` continuam funcionando em todo
 * caller existente). Usar `err instanceof ApiError` quando o caller precisa
 * decidir o tratamento por status (ex.: CameraCell distinguindo 404
 * cross-tenant de outras falhas — ver services/crossTenantCameras.ts).
 */
export class ApiError extends Error {
  status: number
  /**
   * `error_code` do envelope de erro do backend, quando houver. O status
   * sozinho não distingue "403 porque falta permissão" de "403 porque a senha
   * é temporária" — e sem essa distinção a tela de login não tem como oferecer
   * a saída (`password_change_required`). Opcional: rota que não manda
   * `error_code` continua chegando com `code === undefined`.
   */
  code?: string
  constructor(message: string, status: number, code?: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

// Sessão por ABA (services/sessao.ts): localStorage é um só por navegador e
// fazia o segundo login derrubar o primeiro na mesma máquina.
export const getToken = (): string | null => lerSessao(TOKEN_KEY)
export const setToken = (t: string) => gravarSessao(TOKEN_KEY, t)
export const removeToken = () => {
  apagarSessao(TOKEN_KEY)
  apagarSessao('user')
  // WS6: logout/expiração encerra qualquer visualização "ver como" pendente —
  // sem isso o banner reapareceria indevidamente no próximo login
  apagarSessao('impersonation_backup')
  apagarSessao('impersonation')
  // Contexto de tenant assumido: mesma lógica — logout nunca deixa resíduo
  apagarSessao('tenant_context_backup')
  apagarSessao('tenant_context')
}

// ── Impersonation "ver como" (WS6) ──────────────────────────────────────────
// Chaves vivem aqui (e não em impersonation.ts) para o branch 401 abaixo
// restaurar o superadmin sem import circular.
export const IMPERSONATION_BACKUP_KEY = 'impersonation_backup'
export const IMPERSONATION_META_KEY = 'impersonation'
export const IMPERSONATION_EXPIRED_FLAG = 'impersonation_expired'

/**
 * Restaura a sessão original do superadmin a partir do backup salvo ao
 * iniciar uma visualização "ver como". Retorna true se havia backup.
 */
export function restoreImpersonationBackup(redirect = rotaNova('/admin/tenants')): boolean {
  const raw = lerSessao(IMPERSONATION_BACKUP_KEY)
  apagarSessao(IMPERSONATION_BACKUP_KEY)
  apagarSessao(IMPERSONATION_META_KEY)
  if (!raw) return false
  try {
    const backup = JSON.parse(raw) as { token?: string | null; user?: string | null }
    if (backup.token) gravarSessao(TOKEN_KEY, backup.token)
    if (backup.user) gravarSessao('user', backup.user)
    window.location.href = redirect
    return true
  } catch {
    return false
  }
}

// ── Contexto de tenant assumido ("assumir contexto") ────────────────────────
// Mesmo mecanismo do WS6 acima (backup + restore + branch 401), chaves
// separadas de propósito — são estados mutuamente exclusivos (o backend
// recusa aninhar um sobre o outro), mas nada aqui impede as duas telas
// (ImpersonationBanner / TenantContextBanner) de coexistirem no código.
export const TENANT_CONTEXT_BACKUP_KEY = 'tenant_context_backup'
export const TENANT_CONTEXT_META_KEY = 'tenant_context'
export const TENANT_CONTEXT_EXPIRED_FLAG = 'tenant_context_expired'
// bug liveview-contexto-visivel: TENANT_CONTEXT_META_KEY é apagado por
// restoreTenantContextBackup ANTES do reload — sem guardar uma cópia em
// algum lugar que sobrevive ao `window.location.href`, o banner pós-reload
// não sabe de qual tenant era o contexto expirado pra oferecer "Reassumir".
// sessionStorage (não localStorage) de propósito: some sozinho se o usuário
// fechar a aba, mesmo raciocínio do EXPIRED_FLAG.
export const TENANT_CONTEXT_EXPIRED_META_KEY = 'tenant_context_expired_meta'

/**
 * Restaura a sessão original do superadmin a partir do backup salvo ao
 * assumir o contexto de um tenant. Retorna true se havia backup.
 */
export function restoreTenantContextBackup(redirect = rotaNova('/admin/tenants')): boolean {
  const raw = lerSessao(TENANT_CONTEXT_BACKUP_KEY)
  apagarSessao(TENANT_CONTEXT_BACKUP_KEY)
  apagarSessao(TENANT_CONTEXT_META_KEY)
  if (!raw) return false
  try {
    const backup = JSON.parse(raw) as { token?: string | null; user?: string | null }
    if (backup.token) gravarSessao(TOKEN_KEY, backup.token)
    if (backup.user) gravarSessao('user', backup.user)
    window.location.href = redirect
    return true
  } catch {
    return false
  }
}

// Em produção: VITE_API_URL aponta para o service API Railway
// Em dev: vite proxy redireciona /api para localhost:5001
export const API_BASE = import.meta.env.VITE_API_URL
  ? `${import.meta.env.VITE_API_URL}/api`
  : '/api'

// ── Single-flight do branch 401 ─────────────────────────────────────────────
// Bug do congelamento (04/08): quando os 8 tokens de playback da grade venciam
// juntos, 8 refreshLiveViewUrl concorrentes levavam 401 QUASE simultâneos. A
// 1ª resposta consumia o backup de contexto (restoreTenantContextBackup remove
// a chave) e navegava p/ /admin/tenants; as respostas 2..8 chegavam DEPOIS do
// backup removido, caíam no ramo final — removeToken() APAGAVA o token de
// superadmin recém-restaurado — e mandavam p/ /login. Medido no log: 8×
// "token de playback inválido" seguidos de GET /login ×10.
// A navegação recarrega a página inteira (estado deste módulo zera), então o
// guard só precisa segurar as requests em voo DESTA página: a primeira 401
// decide o destino; as demais só lançam, sem tocar em storage/location.
let authRedirectStarted = false

/** Só para testes: reseta o guard entre casos (em produção a navegação
 * recarrega a página e o módulo renasce zerado). */
export function __resetAuthRedirectGuard(): void {
  authRedirectStarted = false
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const token = getToken()
  const isFormData = body instanceof FormData
  const headers: Record<string, string> = {}
  if (!isFormData) headers['Content-Type'] = 'application/json'
  if (token) headers['Authorization'] = `Bearer ${token}`

  const ctrl = new AbortController()
  const timeout = setTimeout(() => ctrl.abort(), 15000)

  try {
    const res = await fetch(`${API_BASE}${path}`, {
      method, headers,
      body: body instanceof FormData ? body : body ? JSON.stringify(body) : undefined,
      signal: ctrl.signal
    })
    const data = await res.json()
    if (!res.ok) {
      const msg = data.error || data.msg || `HTTP ${res.status}`
      if (res.status === 401) {
        if (!path.startsWith('/auth/')) {
          // Single-flight: outra request desta página já está restaurando/
          // redirecionando — não toque em storage nem em location (ver
          // comentário em authRedirectStarted).
          if (authRedirectStarted) {
            throw new Error('Sessão expirada')
          }
          authRedirectStarted = true
          // WS6: token de visualização "ver como" expirou → restaura o
          // superadmin em vez de deslogar (flag p/ toast pós-reload)
          if (lerSessao(IMPERSONATION_BACKUP_KEY)) {
            sessionStorage.setItem(IMPERSONATION_EXPIRED_FLAG, '1')
            if (restoreImpersonationBackup()) {
              throw new Error('Visualização encerrada (token expirou)')
            }
          }
          // Mesma lógica p/ token de contexto de tenant assumido expirado.
          // Guarda uma cópia do meta (tenant_id/nome) ANTES de restaurar —
          // restoreTenantContextBackup apaga TENANT_CONTEXT_META_KEY, e sem
          // isso o banner pós-reload não sabe de qual tenant oferecer
          // "Reassumir".
          if (lerSessao(TENANT_CONTEXT_BACKUP_KEY)) {
            const expiredMeta = lerSessao(TENANT_CONTEXT_META_KEY)
            if (expiredMeta) sessionStorage.setItem(TENANT_CONTEXT_EXPIRED_META_KEY, expiredMeta)
            sessionStorage.setItem(TENANT_CONTEXT_EXPIRED_FLAG, '1')
            if (restoreTenantContextBackup()) {
              throw new Error('Contexto assumido encerrado (token expirou)')
            }
          }
          removeToken()
          window.location.href = '/login'
          throw new Error('Sessão expirada')
        }
        throw new Error(msg)
      }
      // Lazy-import to avoid circular deps
      import('../utils/errorTranslator').then(({ showErrorToast }) => {
        showErrorToast(res.status, path, msg)
      }).catch(() => {})
      throw new ApiError(msg, res.status, data.error_code)
    }
    return data
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      import('../utils/errorTranslator').then(({ showErrorToast }) => {
        showErrorToast(0, path, 'timeout')
      }).catch(() => {})
      throw new Error('Timeout na requisicao')
    }
    throw err
  } finally {
    clearTimeout(timeout)
  }
}

export const api = {
  get:    <T>(path: string)              => request<T>('GET',    path),
  post:   <T>(path: string, b?: unknown) => request<T>('POST',   path, b),
  put:    <T>(path: string, b?: unknown) => request<T>('PUT',    path, b),
  patch:  <T>(path: string, b?: unknown) => request<T>('PATCH',  path, b),
  delete: <T>(path: string)              => request<T>('DELETE', path),

  /**
   * downloadBlob — authenticated file download (CSV/Excel/PDF).
   * Returns raw Blob; caller is responsible for triggering the browser download.
   * Use instead of raw fetch() for any endpoint that returns binary data.
   */
  downloadBlob: async (path: string): Promise<Blob> => {
    const token = getToken()
    const headers: Record<string, string> = {}
    if (token) headers['Authorization'] = `Bearer ${token}`
    const ctrl = new AbortController()
    const timeout = setTimeout(() => ctrl.abort(), 30000)
    try {
      const res = await fetch(`${API_BASE}${path}`, { headers, signal: ctrl.signal })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      return res.blob()
    } finally {
      clearTimeout(timeout)
    }
  },

  /**
   * fetchRaw — authenticated raw fetch that returns the native Response.
   * Use ONLY for endpoints that cannot return JSON — e.g. SSE/streaming responses.
   * Does NOT do JSON parsing or error toast; caller handles the response body.
   */
  fetchRaw: (path: string, init?: RequestInit): Promise<Response> => {
    const token = getToken()
    const authHeader: Record<string, string> = token ? { Authorization: `Bearer ${token}` } : {}
    return fetch(`${API_BASE}${path}`, {
      ...init,
      headers: { ...authHeader, ...(init?.headers as Record<string, string> | undefined) },
    })
  },
}
