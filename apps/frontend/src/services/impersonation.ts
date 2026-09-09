/**
 * Impersonation "ver como" (WS6).
 *
 * Superadmin troca temporariamente o token pelo do usuário-alvo (30 min).
 * O token original fica em backup no localStorage e é restaurado:
 *   - ao clicar em "Sair da visualização" (stopImpersonation), ou
 *   - automaticamente quando o token de visualização expira (branch 401
 *     em services/api.ts → restoreImpersonationBackup).
 *
 * NUNCA armazena credenciais do alvo — apenas o token JWT temporário,
 * mesmo perfil de risco do token normal de sessão.
 */
import { PREFIXO_NOVO } from '../app/RotasNovas'
import {
  api,
  getToken,
  setToken,
  restoreImpersonationBackup,
  IMPERSONATION_BACKUP_KEY,
  IMPERSONATION_META_KEY,
} from './api'
import { gravarSessao, lerSessao } from './sessao'

export interface ImpersonationMeta {
  target_name: string
  target_email: string
  tenant_id?: string
  started_at: string
}

interface ImpersonateResponse {
  success: boolean
  data: {
    token: string
    user: Record<string, unknown>
    expires_in_minutes: number
  }
}

/** Há uma visualização "ver como" ativa neste navegador? */
export function isImpersonating(): boolean {
  return lerSessao(IMPERSONATION_META_KEY) !== null
}

/** Metadados da visualização ativa (nome/email do alvo) — p/ o banner. */
export function getImpersonationMeta(): ImpersonationMeta | null {
  try {
    return JSON.parse(lerSessao(IMPERSONATION_META_KEY) || 'null')
  } catch {
    return null
  }
}

/**
 * Inicia a visualização: troca o token com backup do original e recarrega.
 * Lança Error com mensagem traduzida em caso de falha (caller mostra Toast).
 */
export async function startImpersonation(userId: string): Promise<void> {
  const res = await api.post<ImpersonateResponse>(
    `/v1/admin/users/${userId}/impersonate`,
    {},
  )
  const { token, user } = res.data

  // Backup do superadmin ANTES de trocar o token
  const backup = { token: getToken(), user: lerSessao('user') }
  gravarSessao(IMPERSONATION_BACKUP_KEY, JSON.stringify(backup))
  gravarSessao(
    IMPERSONATION_META_KEY,
    JSON.stringify({
      target_name: (user.name as string) || '',
      target_email: (user.email as string) || '',
      tenant_id: (user.tenant_id as string) || undefined,
      started_at: new Date().toISOString(),
    } satisfies ImpersonationMeta),
  )
  setToken(token)
  gravarSessao('user', JSON.stringify(user))
  // #760: era `'/'`. Logado, `/` cai no `RootRedirect` — que ATÉ ESTE PR
  // mandava para `/admin`|`/modules`, os dois no front ANTIGO. Recarregar na
  // raiz do prefixo novo é o mesmo destino ("a home de quem eu virei"), sem
  // depender do roteador do outro front para chegar lá.
  window.location.href = PREFIXO_NOVO
}

/**
 * Encerra a visualização: avisa o backend (best-effort), restaura o token
 * do superadmin e redireciona para o painel de tenants.
 */
export async function stopImpersonation(): Promise<void> {
  try {
    await api.post('/v1/impersonation/stop', {})
  } catch {
    // Best-effort: o registro de saída falhou, mas a restauração local
    // NUNCA pode ficar presa nisso (o exp do token fecha a sessão)
  }
  // Sem argumento: o default de `restoreImpersonationBackup` já é
  // `rotaNova('/admin/tenants')` — a mesma tela, no front novo (#760).
  restoreImpersonationBackup()
}
