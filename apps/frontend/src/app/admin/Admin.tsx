/**
 * Admin — layout e gate do painel da plataforma (`/novo/admin`).
 *
 * Desenho: `Admin Plataforma.dc.html`. Visão geral (PR-1), Dispositivos e
 * Auditoria (PR-3), Tenants (com white-label) e Usuários (PR-2) — resta só
 * "Links compartilhados", que aguarda backend (pedido registrado).
 * Item apontando para rota inexistente é tela inventada.
 *
 * Gate: `admin:panel` é SUPERADMIN-ONLY (`permissions.py:205-208`, confirmado
 * em `matriz-papeis.json` — nenhum outro papel, nem `admin`, tem esta chave).
 *
 * Banner "você está vendo como" / contexto assumido: NÃO entra aqui. Já é
 * `GlobalBanners`, montado em `App.tsx` fora de qualquer rota — duplicá-lo
 * criaria dois banners e uma segunda fonte de verdade (mesmo raciocínio do
 * `Shell.tsx`).
 *
 * Lateral própria (220px), padrão EXATO de `app/estudio/Estudio.tsx`; a do
 * Shell some via `SEM_BARRA_LATERAL`.
 *
 * "Voltar" no topo da lateral, mesmo padrão do Estúdio — mas por
 * `rotaHomeDoUsuario()`, não caminho chumbado: quem entra aqui é sempre
 * superadmin (o gate abaixo garante), então a home É `/admin` — "Voltar"
 * devolve à Visão geral a partir de qualquer sub-rota (Tenants, Usuários...).
 *
 * NA PRÓPRIA Visão geral (a home), "Voltar" fica de fora: apontar para
 * `rotaHomeDoUsuario()` ali seria um link para a PRÓPRIA rota montada — clique
 * que não navega para lugar nenhum, controle morto com cara de saída (achado
 * do cético, rodada 2 de C2). Nas sub-rotas o link continua real: leva a um
 * lugar DIFERENTE de onde se está.
 */
import { Suspense } from 'react'
import { ArrowLeft, Building2, HardDrive, LayoutDashboard, ScrollText, Users } from 'lucide-react'
import { Link, NavLink, Outlet, useLocation } from 'react-router-dom'

import { useAuth } from '../../hooks/useAuth'
import { rotaHomeDoUsuario } from '../RotasNovas'
import { SemPermissao } from '../shell/SemPermissao'
import * as s from './Admin.css'
import { isInTenantContext } from '../../services/tenantContext'

const ITENS = [
  { rota: '', rotulo: 'Visão geral', Icone: LayoutDashboard },
  { rota: 'tenants', rotulo: 'Tenants', Icone: Building2 },
  { rota: 'usuarios', rotulo: 'Usuários', Icone: Users },
  { rota: 'dispositivos', rotulo: 'Dispositivos', Icone: HardDrive },
  { rota: 'auditoria', rotulo: 'Auditoria', Icone: ScrollText },
]

export function Admin() {
  const { can, isSuperAdmin } = useAuth()
  const { pathname } = useLocation()

  if (!can('admin:panel')) return <SemPermissao permissao="admin:panel" />

  const home = rotaHomeDoUsuario(isSuperAdmin, isInTenantContext())
  const naPropriaHome = pathname === home

  return (
    <div className={s.raiz}>
      <nav className={s.lateral} aria-label="Seções do Admin">
        {!naPropriaHome && (
          <Link to={home} className={s.voltar}>
            <ArrowLeft size={16} strokeWidth={1.7} aria-hidden="true" />
            Voltar
          </Link>
        )}
        <span className={s.lateralTitulo}>Plataforma · Admin</span>
        {ITENS.map(({ rota, rotulo, Icone }) => (
          <NavLink
            key={rotulo}
            to={rota}
            end={rota === ''}
            className={({ isActive }) => (isActive ? `${s.item} ${s.itemAtivo}` : s.item)}
          >
            <Icone size={16} strokeWidth={1.7} aria-hidden="true" />
            {rotulo}
          </NavLink>
        ))}
      </nav>
      <div className={s.conteudo}>
        {/* Boundary local: mesmo raciocínio do Estúdio — sem ele, layout e
            sub-rota (ambos lazy) suspendem em sequência no fallback do Shell. */}
        <Suspense fallback={null}>
          <Outlet />
        </Suspense>
      </div>
    </div>
  )
}
