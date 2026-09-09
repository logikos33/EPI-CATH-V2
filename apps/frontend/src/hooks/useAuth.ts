/**
 * Auth centralizado.
 * LIÇÃO V1: estado inicializado do localStorage para sobreviver reload.
 * LIÇÃO V1: logout inline — sem depender de api.logout().
 */
import { useState, useCallback } from 'react'
import { api, setToken, removeToken, getToken } from '../services/api'
import { gravarSessao, lerSessao } from '../services/sessao'

export interface User {
  id: string
  email: string
  name: string
  // WS7 fix (P3): union incluía só 4 roles — 'analyst' e 'trainer' existem
  // no backend desde a migration 029
  role: 'superadmin' | 'admin' | 'operator' | 'analyst' | 'trainer' | 'viewer'
  tenant_id?: string
  tenant_schema?: string
  modules?: string[]
  /** WS7 — permissões efetivas ('dominio:acao') retornadas no login. */
  permissions?: string[]
}

/**
 * Renova a sessão: troca o token AINDA VÁLIDO por outro com prazo cheio.
 *
 * Antes disto (issue #667) não havia como renovar: o JWT vale JWT_EXPIRY_HOURS
 * (24h) e quem estivesse no meio de uma anotação era derrubado sem apelação.
 *
 * Por que fora do hook: quem precisa disso é o aviso de sessão expirando, que
 * não tem (nem quer) uma instância do `useAuth` — cada `useAuth()` é um estado
 * separado, e a sessão mora no localStorage, não em nenhum deles.
 *
 * Por que NÃO é automático: renovar sozinho a cada aviso mantém viva para
 * sempre a sessão de uma máquina compartilhada que ninguém está usando. O
 * clique é a prova barata de que tem gente ali; quem foi embora expira.
 *
 * Devolve o novo instante de expiração (epoch, ms). Vem PRONTO do backend —
 * o front não decodifica JWT em dois lugares diferentes (é assim que as duas
 * contagens de "quando a sessão acaba" divergem no primeiro refresh).
 *
 * Lança em qualquer falha (401 de token já morto, 403 de contexto assumido,
 * rede): o chamador mostra o motivo e oferece o login. `/auth/*` é isento do
 * redirect automático de 401 do `api.ts`, então o erro chega aqui inteiro em
 * vez de arrastar a página para /login no meio do trabalho.
 */
export async function renovarSessao(): Promise<number> {
  const res = await api.post<{ data: { token: string; user: User; expires_at: number } }>(
    '/auth/refresh',
  )
  const { token, user, expires_at: expiraEm } = res.data ?? {}
  if (!token || !expiraEm) throw new Error('Resposta de renovação incompleta')
  setToken(token)
  if (user) gravarSessao('user', JSON.stringify(user))
  return expiraEm * 1000
}

export function useAuth() {
  const [user, setUser] = useState<User | null>(() => {
    try { return JSON.parse(lerSessao('user') || 'null') }
    catch { return null }
  })

  const isAuthenticated = !!(getToken() && user)

  // Helpers de autorização (não fazem request — apenas leem o estado em memória)
  const isSuperAdmin = user?.role === 'superadmin'
  const isAdmin = user?.role === 'admin' || user?.role === 'superadmin'
  const modules = user?.modules ?? []
  const hasModule = (mod: string) => modules.includes(mod)

  /** WS7 — gating de UI por permissão efetiva ('dominio:acao'). */
  const can = (permission: string): boolean => {
    if (!user) return false
    if (user.role === 'superadmin') return true
    return user.permissions?.includes(permission) ?? false
  }

  const login = useCallback(async (
    email: string, password: string, redirectTo = '/'
  ): Promise<User> => {
    const res = await api.post<any>('/auth/login', { email, password })
    const { token, user } = res.data  // ✅ correto: res.data contém {token, user}
    setToken(token)
    gravarSessao('user', JSON.stringify(user))
    setUser(user)
    // Reload para App.tsx ler do localStorage (hooks são instâncias separadas).
    // ⚠️ `redirectTo` SÓ aceita literais internos dos call sites — nunca ligue
    // a query param (`params.get('next')`): vira open-redirect (achado do cético).
    // `redirectTo` default '/' preserva o Login antigo; a Entrar nova (F5 SR2)
    // manda rotaNova('/') para cair no front novo pós-login.
    window.location.href = redirectTo
    return user
  }, [])

  const logout = useCallback(() => {
    // LIÇÃO V1: inline — não depende de nenhuma função externa
    removeToken()
    setUser(null)
    window.location.href = '/'
  }, [])

  const register = useCallback(async (
    name: string, email: string, password: string
  ): Promise<User> => {
    const res = await api.post<any>('/auth/register', { name, email, password })
    const { token, user } = res.data  // ✅ correto
    setToken(token)
    gravarSessao('user', JSON.stringify(user))
    setUser(user)
    return user
  }, [])

  return { user, isAuthenticated, isSuperAdmin, isAdmin, modules, hasModule, can, login, logout, register }
}
