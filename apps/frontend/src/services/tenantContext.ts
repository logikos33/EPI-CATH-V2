/**
 * Assumir contexto de tenant (superadmin).
 *
 * Distinto de impersonation.ts (WS6, "ver como um usuário"): aqui o
 * superadmin troca de TENANT mantendo a própria identidade — usado para
 * navegar/depurar o produto como se estivesse "dentro" de um tenant
 * específico, com os próprios privilégios de superadmin.
 *
 * Mesmo mecanismo de troca/backup/restauração de token do WS6 (ver
 * services/api.ts): o token original fica em backup no localStorage e é
 * restaurado ao sair do contexto ou quando o token assumido expira (branch
 * 401 em services/api.ts → restoreTenantContextBackup).
 *
 * D-48 (opção C) — renovação proativa: o token de contexto tem TTL curto
 * (30min, TENANT_CONTEXT_TTL_MINUTES espelhado abaixo). scheduleTenantContextRenewal
 * agenda um POST /tenant-context/renew ~5min antes do TTL expirar, trocando
 * o token da sessão da aba SEM reload — uma sessão de trabalho longa não cai
 * no meio. Falha no renew não reagenda: o token simplesmente expira e o
 * branch 401 de services/api.ts já cuida de restaurar o superadmin.
 */
import { PREFIXO_NOVO } from '../app/RotasNovas'
import {
  api,
  getToken,
  setToken,
  restoreTenantContextBackup,
  TENANT_CONTEXT_BACKUP_KEY,
  TENANT_CONTEXT_META_KEY,
} from './api'
import { gravarSessao, lerSessao } from './sessao'

export interface AvailableTenant {
  id: string
  name: string
  slug: string
}

export interface TenantContextMeta {
  tenant_id: string
  tenant_name: string
  tenant_slug: string
  started_at: string
}

interface ListTenantsResponse {
  success: boolean
  data: { tenants: AvailableTenant[] }
}

interface AssumeContextResponse {
  success: boolean
  data: {
    token: string
    tenant: { id: string; name: string; slug: string }
    user: Record<string, unknown>
    expires_in_minutes: number
  }
}

/** Tenants disponíveis para assumir contexto (ativos, com schema válido). */
export async function listAvailableTenants(): Promise<AvailableTenant[]> {
  const res = await api.get<ListTenantsResponse>('/v1/admin/tenant-context/tenants')
  // O tipo promete um array; o envelope pode não trazer `tenants` (resposta
  // malformada, rota stubada, versão antiga da API). Devolver `undefined` sob
  // uma assinatura de array quebra o chamador longe daqui — e foi assim que o
  // shell inteiro caiu no CI: quem consumia fez `lista.length` e estourou.
  return res.data?.tenants ?? []
}

/** Há um contexto de tenant assumido ativo neste navegador? */
export function isInTenantContext(): boolean {
  return lerSessao(TENANT_CONTEXT_META_KEY) !== null
}

/** Metadados do contexto assumido (nome/slug do tenant) — p/ o banner. */
export function getTenantContextMeta(): TenantContextMeta | null {
  try {
    return JSON.parse(lerSessao(TENANT_CONTEXT_META_KEY) || 'null')
  } catch {
    return null
  }
}

/**
 * Assume o contexto do tenant: troca o token com backup do original e
 * recarrega. Lança Error com mensagem traduzida em caso de falha (caller
 * mostra Toast).
 */
export async function assumeTenantContext(
  tenantId: string,
  /**
   * Para onde recarregar depois de assumir. Padrão: a raiz do prefixo novo
   * (`RaizRotasNovas` → painel admin ou escolha de módulo, conforme o papel).
   *
   * #760: o padrão era `'/'`, e `/` LOGADO caía no `RootRedirect` do front
   * ANTIGO. Como `Tenants.tsx`, `TenantDetalhe.tsx`, `CrossTenantCameraBanner`
   * e o auto-assume chamam SEM destino, um clique em "Ver como tenant" no
   * painel admin NOVO despejava o superadmin no produto velho. Consertar aqui
   * — no default — conserta os quatro chamadores de uma vez; consertar em cada
   * um deixaria o próximo chamador nascer quebrado.
   *
   * Quem quer ficar na tela em que está passa a rota corrente (`SeletorTenant`,
   * o banner de contexto expirado e o auto-assume da grade): assumir o cliente
   * a partir de `/novo/epi/eventos` e ser devolvido na home significava sair da
   * tela no exato momento em que ela finalmente teria dado para mostrar.
   */
  destino: string = PREFIXO_NOVO,
): Promise<void> {
  const res = await api.post<AssumeContextResponse>(
    `/v1/admin/tenant-context/tenants/${tenantId}/assume`,
    {},
  )
  const { token, tenant, user } = res.data

  // Backup do superadmin ANTES de trocar o token
  const backup = { token: getToken(), user: lerSessao('user') }
  gravarSessao(TENANT_CONTEXT_BACKUP_KEY, JSON.stringify(backup))
  gravarSessao(
    TENANT_CONTEXT_META_KEY,
    JSON.stringify({
      tenant_id: tenant.id,
      tenant_name: tenant.name,
      tenant_slug: tenant.slug,
      started_at: new Date().toISOString(),
    } satisfies TenantContextMeta),
  )
  setToken(token)
  gravarSessao('user', JSON.stringify(user))
  window.location.href = destino
}

/**
 * Sai do contexto assumido: restaura o token do superadmin salvo em backup.
 * Sem endpoint de backend — a saída é só troca de token local (TTL curto
 * do token assumido já garante que ele expira sozinho).
 */
export function exitTenantContext(): void {
  cancelTenantContextRenewal()
  // Sem argumento: o default de `restoreTenantContextBackup` já é
  // `rotaNova('/admin/tenants')` — a mesma tela, no front novo (#760).
  restoreTenantContextBackup()
}

// ── Renovação proativa (D-48, opção C) ───────────────────────────────────

/** Espelha TENANT_CONTEXT_TTL_MINUTES de app/core/tenant_context.py — usado
 * SOMENTE como fallback quando o exp do próprio token não é legível; a fonte
 * da verdade do agendamento é o claim `exp` do JWT da sessão da aba
 * (services/sessao.ts). */
const TENANT_CONTEXT_TTL_MINUTES = 30
/** Renova com essa folga antes do TTL expirar. */
const RENEW_BEFORE_EXPIRY_MINUTES = 5
/** Retry curto quando o /renew falha com o token AINDA válido — uma falha
 * transitória (deploy reiniciando a API, rede) não pode matar a corrente de
 * renovação: era exatamente isso que deixava o contexto morrer silencioso aos
 * 30min e derrubava a sessão na próxima chamada autenticada (congelamento
 * 04/08). */
const RENEW_RETRY_MS = 30_000

/**
 * Lê o claim `exp` (epoch s) do JWT corrente em localStorage → epoch ms.
 * null se não houver token ou o payload não for decodificável — o chamador
 * cai no fallback do TTL espelhado.
 */
export function getSessionTokenExpMs(): number | null {
  const token = getToken()
  if (!token) return null
  try {
    const payload = token.split('.')[1]
    // base64url → base64 (atob não aceita -/_)
    const b64 = payload.replace(/-/g, '+').replace(/_/g, '/')
    const claims = JSON.parse(atob(b64)) as { exp?: number }
    return typeof claims.exp === 'number' ? claims.exp * 1000 : null
  } catch {
    return null
  }
}

interface RenewContextResponse {
  success: boolean
  data: {
    token: string
    tenant: { id: string }
    user: Record<string, unknown>
    expires_in_minutes: number
  }
}

let renewTimer: ReturnType<typeof setTimeout> | null = null

/**
 * Renova o token de contexto assumido — mesmas claims, TTL cheio de novo —
 * SEM reload. Só deve ser chamada enquanto isInTenantContext() for true
 * (o backend rejeita renovar um token que não é de contexto assumido).
 */
export async function renewTenantContext(): Promise<void> {
  const res = await api.post<RenewContextResponse>('/v1/admin/tenant-context/renew', {})
  const { token, user } = res.data
  setToken(token)
  gravarSessao('user', JSON.stringify(user))
}

/**
 * Agenda a renovação proativa do contexto assumido, ancorada no `exp` REAL do
 * token (fallback: TTL espelhado - 5min). Sucesso reagenda a próxima — uma
 * sessão longa continua renovando enquanto o contexto seguir ativo.
 *
 * FALHA REAGENDA em retry curto (RENEW_RETRY_MS) enquanto o token ainda
 * estiver vivo. A versão anterior ("falha não reagenda, best-effort") tinha um
 * modo de morte silenciosa: UMA falha transitória — ex.: /renew caindo numa
 * janela de deploy da API, que naquela noite reiniciou 7× — matava a corrente,
 * o contexto vencia aos 30min sem ninguém perceber (a tela de monitoramento
 * não faz outra chamada autenticada), e a PRÓXIMA chamada autenticada (a
 * renovação dos tokens de playback, 55min depois) levava 401 em 8 requests
 * concorrentes → cascata até o /login. Se o token já venceu, aí sim desiste:
 * o branch 401 de services/api.ts restaura o superadmin.
 *
 * Catch-up de visibilidade: timers em aba oculta são estrangulados pelo
 * browser (e não disparam durante sleep). Ao voltar visível, se a renovação
 * já está atrasada e o token ainda vive, renova IMEDIATAMENTE.
 *
 * Deliberadamente setTimeout cru (não usePolling — ver hooks/AGENTS.md):
 * não é polling de status, é um heartbeat de renovação de token de baixa
 * frequência vivendo num módulo de serviço, não amarrado ao ciclo de vida de
 * um componente específico — TenantContextBanner só liga/desliga o
 * agendamento via cancelTenantContextRenewal.
 */
export function scheduleTenantContextRenewal(): void {
  cancelTenantContextRenewal()
  if (!isInTenantContext()) return

  scheduleAt(renewalDelayMs())
  document.addEventListener('visibilitychange', handleVisibilityCatchUp)
}

function renewalDelayMs(): number {
  const expMs = getSessionTokenExpMs()
  if (expMs !== null) {
    return Math.max(0, expMs - Date.now() - RENEW_BEFORE_EXPIRY_MINUTES * 60_000)
  }
  return Math.max(0, TENANT_CONTEXT_TTL_MINUTES - RENEW_BEFORE_EXPIRY_MINUTES) * 60_000
}

function scheduleAt(delayMs: number): void {
  if (renewTimer) clearTimeout(renewTimer)
  renewTimer = setTimeout(() => {
    renewTenantContext()
      .then(() => scheduleAt(renewalDelayMs()))
      .catch(() => {
        if (!isInTenantContext()) return
        const expMs = getSessionTokenExpMs()
        if (expMs === null || Date.now() < expMs) {
          scheduleAt(RENEW_RETRY_MS)
        }
        // Token já venceu: desiste — o branch 401 restaura o superadmin.
      })
  }, delayMs)
}

function handleVisibilityCatchUp(): void {
  if (document.hidden || !isInTenantContext()) return
  const expMs = getSessionTokenExpMs()
  if (expMs === null) return
  const overdue = expMs - Date.now() < RENEW_BEFORE_EXPIRY_MINUTES * 60_000
  if (overdue && Date.now() < expMs) {
    scheduleAt(0)
  }
}

/** Cancela o agendamento de renovação (saída do contexto ou expiração). */
export function cancelTenantContextRenewal(): void {
  document.removeEventListener('visibilitychange', handleVisibilityCatchUp)
  if (renewTimer) {
    clearTimeout(renewTimer)
    renewTimer = null
  }
}
