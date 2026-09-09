/**
 * AppRoutes — o front ANTIGO, montado no `*` de `App.tsx`.
 *
 * Pós-login a home sai de `rotaHomeDoUsuario()` (#808): superadmin →
 * `/novo/admin`, demais → `/novo/modules` — as duas no front NOVO.
 * Rotas /admin/* seguem protegidas por AdminRoute (role superadmin).
 *
 * Endereço antigo que já tem substituta PRONTA vira `<Redireciona>` daqui
 * para o novo (#762); o que não tem continua servindo a tela velha, de
 * propósito e nomeado — ver `app/rotasAntigas.test.tsx`, que guarda os dois
 * lados.
 */
import { Routes, Route, Navigate, useLocation, useParams } from 'react-router-dom'
import { ErrorBoundary } from './components/shared/ErrorBoundary'
import { AdminRoute } from './components/guards/AdminRoute'
import { useAuth } from './hooks/useAuth'
import { CameraTriagePage } from './pages/CameraTriagePage'
import { FuelingPage } from './pages/fueling/FuelingPage'
import { FuelingValidationPage } from './pages/fueling/FuelingValidationPage'
import { CountingPage } from './pages/CountingPage'
import { EpiOperationsPage } from './pages/epi/EpiOperationsPage'
import { EpiScenarioEditorPage } from './pages/epi/EpiScenarioEditorPage'
import { rotaHomeDoUsuario, rotaNova } from './app/RotasNovas'
import { InvestigationPage } from './pages/epi/InvestigationPage'
import { EpiSitesPage } from './pages/epi/EpiSitesPage'
import { DashboardIntegradoPage } from './pages/DashboardIntegradoPage'
import { lazy, Suspense } from 'react'
import { isInTenantContext } from './services/tenantContext'
const QualityLayout = lazy(() => import('./modules/quality/QualityLayout').then(m => ({ default: m.QualityLayout })))
const AdminLayout = lazy(() => import('./modules/admin/AdminLayout').then(m => ({ default: m.AdminLayout })))
const DesignSystemPage = lazy(() => import('./pages/DesignSystemPage').then(m => ({ default: m.DesignSystemPage })))
// Tablet Kiosk — rota pública sem JWT, acesso por IP interno (Quality Gate RVB)
const TabletKiosk = lazy(() => import('./modules/quality/tablet/TabletKiosk').then(m => ({ default: m.TabletKiosk })))
// /monitoring — página OCULTA de observabilidade do box edge (superadmin only)
const EdgeMonitoringPage = lazy(() => import('./pages/monitoring/EdgeMonitoringPage').then(m => ({ default: m.EdgeMonitoringPage })))

/**
 * A RAIZ LOGADA (#762). Serve `/` e o catch-all deste roteador.
 *
 * Era `'/admin'` | `'/modules'`, os dois no front ANTIGO — então `/`, o
 * endereço que a pessoa digita ao reabrir o produto no dia seguinte,
 * entregava o produto velho. Deslogado a raiz já estava certa (pinta `Entrar`,
 * com teste em `App.porta.test.tsx`); logado não havia teste nenhum.
 *
 * `rotaHomeDoUsuario` é a MESMA regra por papel (superadmin → painel da
 * plataforma, demais → escolha de módulo), apontando para as telas novas.
 * Ela já era a home do prefixo (`RaizRotasNovas`) e do logo do Shell — agora
 * os três lugares respondem a mesma coisa.
 */
function RootRedirect() {
  const { isSuperAdmin } = useAuth()
  return <Navigate to={rotaHomeDoUsuario(isSuperAdmin, isInTenantContext())} replace />
}

/**
 * Endereço antigo → endereço novo, PRESERVANDO query e hash.
 *
 * O hash entrou em 06/09 (#762): sem ele, `?token=` sobrevivia mas `#linha-3`
 * não, e é o mesmo salto para os dois — quem chega por link de e-mail ou por
 * filtro salvo carrega as duas metades da URL, não uma.
 *
 * Conserto na função COMPARTILHADA, não em cada rota: são 19 chamadores
 * (17 aqui, 2 no ramo deslogado de `App.tsx`).
 */
export function Redireciona({ para }: { para: string }) {
  const { search, hash } = useLocation()
  return <Navigate to={`${para}${search}${hash}`} replace />
}

function AlertaRedirect() {
  const { alertId } = useParams()
  return <Redireciona para={rotaNova(`/epi/eventos/${alertId}`)} />
}

/**
 * Gate da página oculta /monitoring (observabilidade do Jetson edge):
 * superadmin vê a página; QUALQUER outro usuário recebe o MESMO RootRedirect
 * do catch-all — comportamento idêntico ao de rota inexistente, para não
 * vazar que a rota existe (C-01). Sem link em menu/sidebar de propósito.
 */
function EdgeMonitoringGate() {
  const { isSuperAdmin } = useAuth()
  if (!isSuperAdmin) return <RootRedirect />
  return (
    <Suspense fallback={<div style={{ padding: 32 }}>Carregando...</div>}>
      <EdgeMonitoringPage />
    </Suspense>
  )
}

/**
 * Redirect role-aware da rota legada /epi/sites-health (WS9):
 * o painel de frota edge (multi-tenant, observability) vive em
 * /admin/observability?tab=edge (superadmin only).
 * task-093: admin (tenant, não superadmin) tem permissão de backend
 * edge:manage para editar deployment_mode dos sites do PRÓPRIO tenant
 * (GET/PATCH /api/v1/edge/sites — já eram role-gated, só não tinham UI),
 * então vai para /epi/sites em vez do dashboard. Demais papéis (sem a
 * permissão) continuam caindo no dashboard.
 */
function SitesHealthRedirect() {
  const { isSuperAdmin, isAdmin } = useAuth()
  const target = isSuperAdmin
    ? '/admin/observability?tab=edge'
    : isAdmin
      ? '/epi/sites'
      : '/epi/dashboard'
  return <Navigate to={target} replace />
}

/**
 * Redirect role-aware da rota legada /epi/health (WS11):
 * o status de streams agora vive em /admin/observability?tab=streams
 * (agregado em uma request — a StreamHealthPage com N+1 foi removida).
 */
function StreamHealthRedirect() {
  const { isSuperAdmin } = useAuth()
  return (
    <Navigate
      to={isSuperAdmin ? '/admin/observability?tab=streams' : '/epi/dashboard'}
      replace
    />
  )
}

export function AppRoutes() {
  return (
    <ErrorBoundary>
      <Routes>
        {/* Entry point — role-based redirect */}
        <Route path="/" element={<RootRedirect />} />
        {/*
          A ESCOLHA DE MÓDULO (#762). `rotaHomeDoUsuario(false)` já manda `/`
          para `/novo/modules` desde o #808; digitar `/modules` ainda entregava
          `ModuleSelectionPage`, a tela velha, para o MESMO usuário no MESMO
          passo da jornada. `app/modulos/Modulos.tsx` é a substituta (spec
          `design/Módulos.dc.html`).
        */}
        <Route path="/modules" element={<Redireciona para={rotaNova('/modules')} />} />

        {/* EPI module — canonical routes */}
        <Route path="/epi/dashboard" element={<Redireciona para={rotaNova('/epi/dashboard')} />} />
        <Route path="/epi/cameras" element={<Redireciona para={rotaNova('/epi/cameras')} />} />
        <Route path="/epi/cameras/triagem" element={<CameraTriagePage />} />
        <Route path="/epi/alerts" element={<Redireciona para={rotaNova('/epi/eventos')} />} />
        <Route path="/epi/alerts/:alertId" element={<AlertaRedirect />} />
        {/*
          ESTÚDIO (#762). `pages/TrainingPage.tsx` e `pages/ModuleClassesPage.tsx`
          estão carimbados `MIGRADO` no manifesto — paridade FECHADA em 30/08
          (PRs #572/#574/#577/#580/#583/#586), não só "tem tela parecida". A
          raiz do Estúdio manda para `dados`, que é a aba de anotação.
        */}
        <Route path="/epi/training" element={<Redireciona para={rotaNova('/estudio')} />} />
        <Route path="/epi/training/classes" element={<Redireciona para={rotaNova('/estudio/classes')} />} />
        <Route path="/epi/cameras/:cameraId/operations" element={<EpiOperationsPage />} />
        <Route path="/epi/cameras/:cameraId/scenario" element={<EpiScenarioEditorPage />} />
        {/* `pages/ReportsPage.tsx` está carimbado para `app/epi/Relatorios.tsx`
            SEM pendência de paridade — a antiga é um placeholder de 31 linhas.
            (Sem escrever as marcas com arroba: `gera-manifesto-front-antigo.mjs`
            varre por elas e classificaria ESTE arquivo como tela superada.) */}
        <Route path="/epi/reports" element={<Redireciona para={rotaNova('/epi/relatorios')} />} />
        <Route path="/epi/verification" element={<Redireciona para={rotaNova('/epi/verificacao')} />} />
        <Route path="/epi/counting" element={<CountingPage />} />
        <Route path="/epi/health" element={<StreamHealthRedirect />} />
        <Route path="/epi/sites-health" element={<SitesHealthRedirect />} />
        <Route path="/epi/sites" element={<EpiSitesPage />} />
        <Route path="/epi/edge-observability" element={<DashboardIntegradoPage />} />
        <Route path="/epi/investigation" element={<InvestigationPage />} />

        {/* Admin module — superadmin only, lazy-loaded */}
        <Route element={<AdminRoute />}>
          {/*
            #762 — as seções do Admin que JÁ existem no front novo (duas: a
            terceira, `/admin/tenants`, está explicada no fim deste bloco).
            Ficam DENTRO do `AdminRoute` de propósito: quem não é superadmin
            continua sendo devolvido para `/` exatamente como antes, em vez de
            bater no `SemPermissao` do painel novo (que confirmaria a rota).

            Casam antes de `/admin/*` por ranking do React Router (segmento
            estático ganha de splat), então o resto do Admin antigo —
            observability, integrations, planos, flags, branding… — segue
            intocado.

            `/admin/tenants` FICA DE FORA da lista, e não por gosto: a lista
            antiga é a ÚNICA PORTA CLICÁVEL para `/admin/tenants/:id`
            (`AdminTenantsPage.tsx:123`, clique na linha), e é lá que vive o
            `UserPermissionsDrawer` — matriz de permissão por usuário e
            revogar sessão, 333 linhas SEM equivalente no front novo, dito no
            §3 de `app/admin/Usuarios.tsx`. A outra porta,
            `CreateUserWizard.tsx:331`, mora dentro de `/admin/users`. Redirecionar
            a lista mantém o detalhe roteável e o deixa órfão: some do clique,
            sobra só digitar o UUID na barra. Manter função que a substituta
            não tem vale mais do que fechar mais um endereço antigo —
            especialmente um que só o superadmin alcança (o `AdminRoute` acima
            barra todo o resto), enquanto o drawer é justamente a ferramenta de
            consertar permissão de usuário na segunda de manhã.
            (Mesma razão pela qual `/admin/tenants/:id` já ficava de fora.)
          */}
          <Route path="/admin" element={<Redireciona para={rotaNova('/admin')} />} />
          <Route path="/admin/users" element={<Redireciona para={rotaNova('/admin/usuarios')} />} />
          <Route
            path="/admin/*"
            element={
              <Suspense fallback={<div style={{ padding: 32 }}>Carregando...</div>}>
                <AdminLayout />
              </Suspense>
            }
          />
          <Route
            path="/design-system"
            element={
              <Suspense fallback={<div style={{ padding: 32 }}>Carregando...</div>}>
                <DesignSystemPage />
              </Suspense>
            }
          />
        </Route>

        {/* Legacy routes → redirect to canonical */}
        <Route path="/home" element={<Navigate to="/epi/dashboard" replace />} />
        <Route path="/dashboard" element={<Navigate to="/epi/dashboard" replace />} />
        <Route path="/cameras" element={<Navigate to="/epi/cameras" replace />} />
        {/* Apontavam para `/epi/training`, que agora é ele mesmo um redirect —
            saltam DIRETO para o destino final, sem o pulo do gato intermediário
            (que, de quebra, perdia a query). */}
        <Route path="/annotation" element={<Redireciona para={rotaNova('/estudio')} />} />
        <Route path="/training" element={<Redireciona para={rotaNova('/estudio')} />} />
        <Route path="/module-classes" element={<Redireciona para={rotaNova('/estudio/classes')} />} />
        <Route path="/monitoring" element={<EdgeMonitoringGate />} />
        <Route path="/epi/monitoring" element={<Redireciona para={rotaNova('/epi/live')} />} />
        <Route path="/alerts" element={<Redireciona para={rotaNova('/epi/eventos')} />} />

        {/* Quality module — carregado via lazy para isolamento de bundle */}
        <Route
          path="/quality/*"
          element={
            <Suspense fallback={null}>
              <QualityLayout />
            </Suspense>
          }
        />

        {/* Fueling module */}
        <Route path="/fueling/validation" element={<FuelingValidationPage />} />
        <Route path="/fueling/*" element={<FuelingPage />} />

        {/* Tablet Kiosk — rota pública sem JWT, acesso por IP interno */}
        <Route
          path="/tablet/:station"
          element={
            <Suspense fallback={<div style={{ background: '#0a0c10' /* allow: bgBase tablet fallback */, minHeight: '100vh' }} />}>
              <TabletKiosk />
            </Suspense>
          }
        />

        {/* Catch-all */}
        <Route path="*" element={<RootRedirect />} />
      </Routes>
    </ErrorBoundary>
  )
}
