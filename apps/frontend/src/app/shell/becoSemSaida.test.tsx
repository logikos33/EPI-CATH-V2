/**
 * REGRA GLOBAL — nenhuma área do front novo é beco sem saída (contrato C2).
 *
 * Deriva a lista de áreas SEM barra lateral própria de `SEM_BARRA_LATERAL`
 * (Shell.tsx) — a MESMA constante que decide se o Shell esconde a nav
 * principal — em vez de escrever a lista à mão aqui: foi uma lista escrita à
 * mão que deixou Admin de fora na rodada anterior (dívida registrada,
 * consertada nesta).
 *
 * ── POR QUE ISTO RENDERIZA DE VERDADE, E NÃO LÊ O ARQUIVO COMO TEXTO ────────
 *
 * A versão anterior deste teste lia o arquivo da área como STRING e casava
 * `/voltar/i` + `/rotaNova\(/` por regex. Isso passa mesmo que o link não
 * funcione — ou pior, que ele exista com OUTRO significado: `RevisaoQualidade
 * .tsx` tem um `s.voltar` que fecha um painel de detalhe, nada a ver com sair
 * da área. Regex não distingue os dois. Por isso `ABRE_AREA` abaixo MONTA a
 * área de verdade (React Testing Library) e busca o elemento REAL no DOM: um
 * link quebrado, removido, ou que virou `<button>` sem `href` reprova aqui —
 * é a mesma garantia que `getByRole('link')` já dá em `Estudio.test.tsx` e
 * `Admin.test.tsx`, só que agora para TODA área, num só lugar, sem depender de
 * ninguém lembrar de escrever o teste local.
 *
 * Critério objetivo:
 *
 *  · Shell COM a nav principal (sidebar visível — rota fora de
 *    `SEM_BARRA_LATERAL`) já satisfaz: a sidebar sempre mostra ao menos
 *    "Dashboard", mesmo sem NENHUMA permissão (`Shell.test.tsx` cobra isso à
 *    parte — "sem permissão nenhuma, sobra só o que não exige permissão").
 *  · Área com NAV PRÓPRIA (`SEM_BARRA_LATERAL`: a sidebar do Shell some) pede
 *    mais: o logo do topbar (link desde F5-LEVE item 1) chega lá, mas é
 *    pequeno e não é o que se procura quando a lateral inteira virou outra
 *    coisa — por isso a ÁREA precisa do PRÓPRIO link explícito, e é ele que
 *    `ABRE_AREA` verifica.
 *  · Rota SEM Shell (`ROTAS_NOVAS_SEM_SHELL`): `/modules` É a home de quem
 *    não é superadmin (a raiz do prefixo cai lá, `rotaHomeDoUsuario`) — não
 *    há "nível acima" dela. `/tablet/:station` é o kiosk físico da bancada
 *    (Quality Gate), sem chrome de propósito: tablet fixo, não é navegação
 *    de gente logada — ver docstring de `Kiosk.tsx`.
 *
 * O destino esperado é `rotaHomeDoUsuario(role)` para as três áreas
 * consertadas nesta rodada (Admin/Quality/Carga) — Admin só é alcançável por
 * superadmin (gate `admin:panel`), então a home é a própria Visão geral: o
 * link devolve de qualquer sub-rota (Tenants, Usuários...) a ela, e não é um
 * no-op. O Estúdio é o ÚNICO com destino FIXO (`/epi/dashboard`, medido em
 * `Estudio.tsx`): ele não tem home própria e por isso não usa
 * `rotaHomeDoUsuario` — comportamento já existente, fora do escopo desta
 * rodada, mantido aqui como o valor esperado documentado.
 */
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { PREFIXO_NOVO, ROTAS_NOVAS_SEM_SHELL, rotaHomeDoUsuario } from '../RotasNovas'
import { SEM_BARRA_LATERAL } from './Shell'

const auth = vi.hoisted(() => ({
  can: vi.fn((_p: string) => true),
  hasModule: vi.fn((_m: string) => true),
  isSuperAdmin: false,
}))
vi.mock('../../hooks/useAuth', () => ({ useAuth: () => auth }))

// Qualidade e Carga chamam `services/api` (e Qualidade também
// `cameraService`) ao montar — sem o dublê, os testes fariam rede de
// verdade. Mesma receita de `Qualidade.test.tsx` / `Carga.test.tsx`.
const get = vi.fn()
vi.mock('../../services/api', () => ({
  api: { get: (...a: unknown[]) => get(...a), patch: vi.fn(), delete: vi.fn() },
}))
vi.mock('../../services/cameraService', () => ({ cameraService: { test: vi.fn() } }))
// Estes casos cobrem a navegação SEM contexto assumido; o caso COM contexto é
// exercido direto sobre `rotaHomeDoUsuario`, que é a função que decide.
vi.mock('../../services/tenantContext', () => ({
  isInTenantContext: () => false,
  getSessionTokenExpMs: () => null,
}))

import { Admin } from '../admin/Admin'
import { Carga } from '../carga/Carga'
import { Estudio } from '../estudio/Estudio'
import { Gabarito } from '../estudio/Gabarito'
import { Qualidade } from '../qualidade/Qualidade'

const AREAS_SEM_BARRA = SEM_BARRA_LATERAL.map((r) => r.replace(`${PREFIXO_NOVO}/`, ''))

interface AberturaArea {
  /** Monta a área de verdade, na sua própria rota, com o gate mínimo aberto. */
  montar: () => void
  /** Destino esperado do link de saída. */
  espera: string
  /** true quando a área só é alcançável por superadmin (o gate garante). */
  comoSuperadmin?: boolean
}

/**
 * Um "abridor" por área. `null` = dívida conhecida — a área ainda entra na
 * varredura de sanidade abaixo (trava de "não esquecer"), só não é
 * renderizada. Hoje nenhuma área tem impedimento real: as três desta rodada
 * (Admin/Quality/Carga) e o Estúdio (rodada anterior) estão todas cobertas.
 */
const ABRE_AREA: Record<string, AberturaArea | null> = {
  estudio: {
    montar: () =>
      render(
        <MemoryRouter initialEntries={[`${PREFIXO_NOVO}/estudio/dados`]}>
          <Routes>
            <Route path={`${PREFIXO_NOVO}/estudio`} element={<Estudio />}>
              <Route path="dados" element={<div />} />
            </Route>
          </Routes>
        </MemoryRouter>,
      ),
    espera: `${PREFIXO_NOVO}/epi/dashboard`,
  },
  admin: {
    // Monta numa SUB-rota (Tenants), não na Visão geral: na própria home o
    // link de saída (Voltar) é retirado de propósito, porque lá apontaria
    // para a rota JÁ montada — controle morto com cara de saída (achado do
    // cético, rodada 2 de C2; a régua anterior aceitava isso como válido). Ver
    // o describe dedicado abaixo, que cobre exatamente esse caso ausente.
    montar: () =>
      render(
        <MemoryRouter initialEntries={[`${PREFIXO_NOVO}/admin/tenants`]}>
          <Routes>
            <Route path={`${PREFIXO_NOVO}/admin`} element={<Admin />}>
              <Route path="tenants" element={<div />} />
            </Route>
          </Routes>
        </MemoryRouter>,
      ),
    espera: rotaHomeDoUsuario(true, false),
    comoSuperadmin: true,
  },
  quality: {
    montar: () => render(<MemoryRouter><Qualidade /></MemoryRouter>),
    espera: rotaHomeDoUsuario(false, false),
  },
  carga: {
    montar: () => render(<MemoryRouter><Carga /></MemoryRouter>),
    espera: rotaHomeDoUsuario(false, false),
  },
}

beforeEach(() => {
  auth.can.mockReset().mockReturnValue(true)
  auth.hasModule.mockReset().mockReturnValue(true)
  auth.isSuperAdmin = false
  get.mockReset().mockResolvedValue({ data: {} })
})

describe('nenhuma área do front novo é beco sem saída', () => {
  it('SEM_BARRA_LATERAL não cresce sem entrar no mapa de aberturas deste teste', () => {
    // Trava de sanidade do PRÓPRIO teste: área nova com nav própria e sem
    // entrada aqui seria um buraco silencioso na regra global — foi assim
    // que Admin ficou de fora na rodada anterior.
    for (const area of AREAS_SEM_BARRA) {
      expect(
        Object.keys(ABRE_AREA),
        `área nova em SEM_BARRA_LATERAL sem entrada em ABRE_AREA: "${area}"`,
      ).toContain(area)
    }
  })

  it.each(
    Object.entries(ABRE_AREA).filter(
      (par): par is [string, AberturaArea] => par[1] !== null,
    ),
  )(
    'área "%s" (nav própria): RENDERIZADA de verdade, tem link real de volta para a home do usuário',
    (_area, { montar, espera, comoSuperadmin }) => {
      auth.isSuperAdmin = comoSuperadmin ?? false
      montar()
      // Elemento REAL do DOM, não texto do arquivo-fonte: link quebrado,
      // removido, ou virado `<button>` sem `href` reprova aqui.
      const link = screen.getByRole('link', { name: /voltar/i })
      expect(link.getAttribute('href')).toBe(espera)
    },
  )

  it('rotas SEM Shell são a home (/modules), o kiosk físico (/tablet) ou tela de celular (/estudio/gabarito)', () => {
    const caminhos = ROTAS_NOVAS_SEM_SHELL.map((r) => (r.props as { path: string }).path)
    expect(caminhos).toEqual([
      `${PREFIXO_NOVO}/modules`,
      `${PREFIXO_NOVO}/tablet/:station`,
      // Triagem do gabarito: tela de TELEFONE. Topbar do Shell + lateral do
      // Estúdio (220px) comem metade da largura útil de um aparelho em pé, e
      // a foto 1920x1080 precisa de cada pixel para o dono conseguir ver se
      // há luva na mão. Sair do Shell aqui NÃO a isenta da regra — ela tem o
      // próprio link de saída, provado no caso abaixo.
      `${PREFIXO_NOVO}/estudio/gabarito`,
    ])
  })

  it('a triagem do gabarito (sem Shell, sem lateral) tem link real de saída', async () => {
    // Sem Shell, sem lateral do Estúdio e sem nav própria, esta tela seria o
    // beco sem saída mais fechado do front novo. Aceitá-la na lista acima sem
    // cobrar a saída transformaria a lista numa isenção — e a regra existe
    // exatamente para não ter isenção.
    get.mockResolvedValue({ success: true, data: { classes: [], frames: [] } })
    render(
      <MemoryRouter initialEntries={[`${PREFIXO_NOVO}/estudio/gabarito`]}>
        <Gabarito />
      </MemoryRouter>,
    )
    const link = await screen.findByRole('link', { name: /voltar/i })
    expect(link.getAttribute('href')).toBe(`${PREFIXO_NOVO}/estudio/dados`)
  })

  it('o logo do Shell é link para a home do usuário (comportamento real coberto em Shell.test.tsx)', () => {
    // Checagem de existência do próprio mecanismo (Marca usa
    // `rotaHomeDoUsuario`); o comportamento fim-a-fim (href muda por papel)
    // já é RENDERIZADO em `Shell.test.tsx` ("o logo é um link para a home do
    // usuário") — não duplicado aqui.
    expect(rotaHomeDoUsuario(true, false)).toBe(`${PREFIXO_NOVO}/admin`)
    expect(rotaHomeDoUsuario(false, false)).toBe(`${PREFIXO_NOVO}/modules`)
  })

  it('com tenant assumido, a home do superadmin é a do CLIENTE — senão o logo devolve pro beco', () => {
    // O beco relatado: assumido o contexto de um tenant, o superadmin está
    // vendo COMO aquele cliente, mas logo, "Voltar" e raiz do prefixo
    // apontavam todos para /admin — nenhum caminho levava ao produto do
    // cliente, e a pessoa ficava presa na área de plataforma.
    expect(rotaHomeDoUsuario(true, true)).toBe(`${PREFIXO_NOVO}/modules`)
    // Sair do contexto devolve a home de plataforma.
    expect(rotaHomeDoUsuario(true, false)).toBe(`${PREFIXO_NOVO}/admin`)
  })

  describe('Admin: a própria Visão geral (raiz) não mostra "Voltar"', () => {
    it('link para a rota já montada seria controle morto, não saída — por isso some, e a nav segue navegável', () => {
      auth.isSuperAdmin = true
      render(
        <MemoryRouter initialEntries={[`${PREFIXO_NOVO}/admin`]}>
          <Routes>
            <Route path={`${PREFIXO_NOVO}/admin`} element={<Admin />}>
              <Route index element={<div />} />
            </Route>
          </Routes>
        </MemoryRouter>,
      )
      expect(screen.queryByRole('link', { name: /voltar/i })).toBeNull()
      expect(screen.getByRole('navigation', { name: 'Seções do Admin' })).toBeTruthy()
    })
  })

  // Os testes acima mockam `can`/`hasModule` sempre abertos: cegos para os
  // ramos em que a própria tela nega acesso — e é lá que a Carga travava. Um
  // gate negado é RENDER DIFERENTE (early return antes do cabeçalho), não
  // decoração: precisa da própria varredura, não só do caminho feliz.
  describe('Carga: os ramos NEGADOS (não só o feliz) também têm saída', () => {
    it.each([
      ['sem counting:read', { can: false, hasModule: true }],
      // Estado REAL do tenant da demo (rvb): módulo counting não habilitado.
      ['módulo counting desligado', { can: true, hasModule: false }],
    ])('%s', (_nome, gates) => {
      auth.can.mockReturnValue(gates.can)
      auth.hasModule.mockReturnValue(gates.hasModule)
      render(<MemoryRouter><Carga /></MemoryRouter>)
      const link = screen.getByRole('link', { name: /voltar/i })
      expect(link.getAttribute('href')).toBe(rotaHomeDoUsuario(false, false))
    })
  })
})
