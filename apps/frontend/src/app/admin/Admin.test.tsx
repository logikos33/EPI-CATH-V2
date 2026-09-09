/**
 * O gate é o que não pode falhar aqui: `admin:panel` é superadmin-only
 * (`permissions.py:205-208`) — nem `admin` do tenant entra.
 */
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const auth = vi.hoisted(() => ({ can: vi.fn((_p: string) => true), isSuperAdmin: true }))
vi.mock('../../hooks/useAuth', () => ({ useAuth: () => auth }))
vi.mock('../../services/tenantContext', () => ({
  isInTenantContext: () => false,
  getSessionTokenExpMs: () => null,
}))

import { Admin } from './Admin'

/** `rotaAtual` deixa montar tanto a Visão geral (raiz) quanto uma sub-rota. */
function monta(rotaAtual = '/novo/admin') {
  return render(
    <MemoryRouter initialEntries={[rotaAtual]}>
      <Routes>
        <Route path="/novo/admin" element={<Admin />}>
          <Route index element={<div>conteudo-visao-geral</div>} />
          <Route path="tenants" element={<div>conteudo-tenants</div>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  )
}

describe('Admin (layout + gate)', () => {
  beforeEach(() => {
    auth.can.mockReset()
    auth.can.mockReturnValue(true)
  })

  it('sem admin:panel (ex.: admin do tenant) → Sem permissão, e NADA do conteúdo vaza', () => {
    auth.can.mockImplementation((p: string) => p !== 'admin:panel')
    monta()
    expect(screen.getByText('Sem permissão')).toBeTruthy()
    // issue #810: o gate segue; a chave crua não é mais servida.
    expect(screen.getByText(/permissão de abrir o painel de administração/)).toBeTruthy()
    expect(screen.queryByText('admin:panel')).toBeNull()
    expect(screen.queryByText('conteudo-visao-geral')).toBeNull()
    expect(screen.queryByRole('navigation')).toBeNull()
  })

  it('com admin:panel (superadmin) → lateral própria com Visão geral e o Outlet', () => {
    monta()
    expect(auth.can).toHaveBeenCalledWith('admin:panel')
    const lateral = screen.getByRole('navigation', { name: 'Seções do Admin' })
    expect(lateral).toBeTruthy()
    expect(screen.getByRole('link', { name: /visão geral/i })).toBeTruthy()
    expect(screen.getByText('conteudo-visao-geral')).toBeTruthy()
  })

  it('numa sub-rota (Tenants), "Voltar" é o primeiro link da lateral e leva à Visão geral', () => {
    // A lateral do Admin SUBSTITUI a nav principal do Shell — sem este link,
    // quem entra aqui não tem caminho de volta nenhum (regra global, ver
    // app/shell/becoSemSaida.test.tsx). Quem chega aqui é sempre superadmin
    // (o gate acima garante), então a home É `/admin` — e daqui (Tenants) é
    // um destino DIFERENTE de onde se está: link real, não decoração.
    monta('/novo/admin/tenants')
    const primeiro = screen.getAllByRole('link')[0]
    expect(primeiro.textContent?.trim()).toBe('Voltar')
    expect(primeiro.getAttribute('href')).toBe('/novo/admin')
  })

  it('na PRÓPRIA Visão geral (raiz), "Voltar" não aparece — seria link para a rota já montada', () => {
    // Achado do cético (rodada 2 de C2): `rotaHomeDoUsuario(isSuperAdmin)` na
    // Visão geral aponta para `/novo/admin` — a MESMA rota já montada. Um
    // clique nesse link não navega para lugar nenhum: controle morto com cara
    // de saída. A régua que aceitava esse self-link como "saída válida" foi
    // quem deixou isso passar na rodada anterior — aqui o teste reprova
    // exatamente esse caso: nenhum "Voltar" deve existir quando já se está na
    // home, e a nav continua alcançável pelos outros itens (Tenants, ...).
    monta('/novo/admin')
    expect(screen.queryByRole('link', { name: /voltar/i })).toBeNull()
    expect(screen.getByRole('link', { name: /visão geral/i })).toBeTruthy()
  })
})
