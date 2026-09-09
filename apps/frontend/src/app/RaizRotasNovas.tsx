/**
 * Raiz do prefixo (`/novo` exato — quem clica no logo do Shell cai aqui).
 *
 * Arquivo PRÓPRIO, de propósito, e importado por `RotasNovas.tsx` só via
 * `lazy()` (nunca `import` estático): esta tela precisa de `useAuth()`, que
 * puxa `services/api.ts` — e `services/api.ts` lê `import.meta.env` no TOPO
 * do arquivo, fora de qualquer função. Isso é seguro dentro do bundle do
 * Vite (ele substitui `import.meta.env.*` em build/dev), mas QUEBRA quando
 * o módulo é carregado fora do Vite — exatamente o caso de
 * `test/e2e/identidade-rotas.spec.ts`, que importa `RotasNovas.tsx`
 * ESTATICAMENTE em Node puro (via Playwright), só para ler `ROTAS_NOVAS`
 * como dado (a lista de caminhos), sem nunca renderizar nada.
 *
 * `RotasNovas.tsx` é código de dado (paths + componentes lazy), não de
 * runtime — todo o resto das telas já respeita isso via `lazy()`; esta é a
 * única com lógica de auth, então ganha o mesmo tratamento: TypeError
 * "Cannot read properties of undefined (reading 'VITE_API_URL')" era esse
 * import estático furando essa regra (CI do #623, achado do coordenador).
 */
import { Navigate } from 'react-router-dom'

import { useAuth } from '../hooks/useAuth'
import { rotaHomeDoUsuario } from './RotasNovas'
import { isInTenantContext } from '../services/tenantContext'

export function RaizRotasNovas() {
  const { isSuperAdmin } = useAuth()
  return <Navigate to={rotaHomeDoUsuario(isSuperAdmin, isInTenantContext())} replace />
}
