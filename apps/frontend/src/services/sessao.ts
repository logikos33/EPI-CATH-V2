/**
 * SESSÃO POR ABA — o pacote de sessão mora em `sessionStorage`, não em
 * `localStorage`.
 *
 * Bug que originou o módulo ("um login derruba o outro"): `token` e `user`
 * viviam em `localStorage`, que é UM por perfil de navegador e compartilhado
 * por todas as abas e janelas. Duas pessoas no mesmo computador disputavam
 * uma vaga só — o segundo login sobrescrevia a chave do primeiro, e o
 * `window.location.href` do `login()` recarregava a aba antiga já dentro da
 * sessão nova.
 *
 * O backend nunca revogou nada: `tenants.single_session` está `false` em
 * todos os tenants, `revoke_other_sessions` filtra por `user_id` (não alcança
 * outra pessoa por construção) e as duas sessões concorrentes medidas
 * continuavam vivas e não-revogadas em `public.active_sessions`. O problema
 * era inteiramente de armazenamento no cliente.
 *
 * `sessionStorage` é por ABA (browsing context): sobrevive a F5 e à navegação
 * dentro da mesma aba, e não enxerga o que outra aba gravou. Custo aceito na
 * decisão: aba nova pede login, e fechar a aba encerra a sessão daquela aba.
 * Numa máquina compartilhada isso é a propriedade desejada — uma aba nova
 * NÃO deve herdar a sessão de quem usou o computador antes.
 *
 * Escopo: só o PACOTE DE SESSÃO (credencial e identidade) mora aqui.
 * Preferência de viewer — tema, layout de grade, filtro de dashboard, banner
 * dispensado — continua em `localStorage` de propósito: é conveniência do
 * navegador, não credencial, e faz sentido ser compartilhada entre abas.
 */

/** Chaves do pacote de sessão. Migradas de localStorage uma única vez. */
const CHAVES_DE_SESSAO = [
  'token',
  'user',
  'impersonation',
  'impersonation_backup',
  'tenant_context',
  'tenant_context_backup',
] as const

let migrada = false

/**
 * Adota, UMA vez por aba, a sessão que ficou em `localStorage` de antes desta
 * mudança — sem isso todo mundo que estava logado seria deslogado no deploy.
 *
 * A cópia em `localStorage` é apagada no mesmo passo: só a primeira aba a
 * carregar depois do deploy adota a sessão legada; qualquer aba seguinte
 * nasce limpa, que é o comportamento definitivo.
 *
 * Roda sob try/catch porque `localStorage`/`sessionStorage` lançam em
 * contextos com armazenamento bloqueado (janela anônima com site data off,
 * captura de thumbnail). Uma exceção aqui, no import, apagaria a tela toda.
 */
function migrarSessaoLegada(): void {
  if (migrada) return
  migrada = true
  try {
    for (const chave of CHAVES_DE_SESSAO) {
      const legado = window.localStorage.getItem(chave)
      if (legado === null) continue
      if (window.sessionStorage.getItem(chave) === null) {
        window.sessionStorage.setItem(chave, legado)
      }
      window.localStorage.removeItem(chave)
    }
  } catch {
    /* armazenamento indisponível — segue sem sessão, o app manda pro login */
  }
}

migrarSessaoLegada()

/** Lê uma chave do pacote de sessão desta aba. */
export function lerSessao(chave: string): string | null {
  try {
    return window.sessionStorage.getItem(chave)
  } catch {
    return null
  }
}

/** Grava uma chave do pacote de sessão nesta aba (não vaza para outras). */
export function gravarSessao(chave: string, valor: string): void {
  try {
    window.sessionStorage.setItem(chave, valor)
  } catch {
    /* noop — ver migrarSessaoLegada */
  }
}

/** Apaga uma chave do pacote de sessão desta aba. */
export function apagarSessao(chave: string): void {
  try {
    window.sessionStorage.removeItem(chave)
  } catch {
    /* noop — ver migrarSessaoLegada */
  }
}
