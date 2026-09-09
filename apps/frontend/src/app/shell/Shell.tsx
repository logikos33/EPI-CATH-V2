/**
 * Shell Logikos Vision — F1 da migração.
 *
 * TopBar (56) · sidebar (236/64) · ⌘K · loader · aviso de sessão. Medidas do
 * README do handoff, todas por token.
 *
 * ⚠️ COEXISTE com o `AppLayout` atual, de propósito (decisão do Vitor, 27/08):
 * as rotas novas montam este shell, as antigas seguem no antigo, e nada é
 * removido até a migração inteira estar feita. Quem controla o que pode sair
 * depois é o `docs/migration/MANIFESTO-FRONT-ANTIGO.md`.
 *
 * O que este shell NÃO faz, de propósito:
 *
 * · **Banner de contexto assumido.** Já existe, montado em `App.tsx` FORA das
 *   rotas (`GlobalBanners`), com TTL de 30min, renovação proativa, expiração e
 *   "Reassumir" — três arquivos de teste. Reconstruí-lo aqui renderizaria DOIS
 *   banners e criaria uma segunda fonte de verdade sobre quem estou vendo. A
 *   topbar só desce o que ele ocupa, via `--global-banner-offset`.
 *   O visual novo (42px + faixa âmbar de 2px) entra quando o front antigo sair
 *   — restilizá-lo agora mudaria o front antigo junto, que tem de seguir de pé.
 *
 * · **Expiração da sessão.** `getSessionTokenExpMs()` lê o claim `exp` do JWT
 *   corrente, de minuto em minuto — o token TROCA sem desmontar o shell
 *   (renovação de contexto assumido, e agora o refresh da issue #667).
 *   Decodificar token em mais um lugar divergiria no primeiro refresh.
 */
import { Suspense, useEffect, useLayoutEffect, useMemo, useState } from 'react'
import { LogOut, Menu, Search } from 'lucide-react'
import { Link, NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'

import { ErrorBoundary } from '../../components/shared/ErrorBoundary'
import { NotificationBell } from '../../components/ui/NotificationBell/NotificationBell'
import { useAuth } from '../../hooks/useAuth'
import { getSessionTokenExpMs } from '../../services/tenantContext'
import { LogikosLoader } from './LogikosLoader'
import { PaletaComandos, type GrupoPaleta } from './PaletaComandos'
import { SeletorTenant } from './SeletorTenant'
import { SessaoExpirando } from './SessaoExpirando'
import { PREFIXO_NOVO, rotaHomeDoUsuario, rotaNova } from '../RotasNovas'
import { useMarcaDoTenant } from '../tokens/MarcaDoTenant'
import { NAV_ADMIN, NAV_EPI, NAV_ESTUDIO, navVisivel } from './navPorPerfil'
import * as s from './Shell.css'

/**
 * Marca: monograma + wordmark, link para a home do usuário (F5-LEVE item 1).
 * Geometria do monograma é canônica — nunca distorcer.
 */
function Marca({ para }: { para: string }) {
  return (
    <Link to={para} className={s.marca}>
      <svg width="22" height="22" viewBox="0 0 100 100" aria-hidden="true">
        <path d="M40 55.3 A20 20 0 1 1 60 55.3 L67 88 L33 88 Z" fill="currentColor" />
      </svg>
      <span>LOGIKOS</span>
    </Link>
  )
}

export interface ShellProps {
  /** Cobre o conteúdo com o loader — usado enquanto a sessão carrega. */
  carregando?: boolean
}

/**
 * Módulos que trazem a PRÓPRIA navegação e por isso não recebem a barra lateral.
 *
 * Não é economia de espaço: os desenhos de Qualidade e Carga importam só o
 * `EPI Topbar`, nunca o `EPI Sidebar` — a navegação deles é a barra de abas
 * dentro da tela. Impor o menu do EPI ali mostra "Eventos" e "Verificação" para
 * quem está inspecionando peça, e some com a navegação que a tela realmente tem.
 */
export const SEM_BARRA_LATERAL = [
  `${PREFIXO_NOVO}/quality`,
  `${PREFIXO_NOVO}/carga`,
  `${PREFIXO_NOVO}/estudio`,
  `${PREFIXO_NOVO}/admin`,
]

export function Shell({ carregando }: ShellProps) {
  const { can, isSuperAdmin, logout } = useAuth()
  const { pathname, search } = useLocation()
  const comBarraLateral = !SEM_BARRA_LATERAL.some((r) => pathname.startsWith(r))
  // Publica --lk-marca clampada; os tokens leem dela. Ver DECISÃO v2 item 3.
  useMarcaDoTenant()
  // F5-LEVE (identidade, rodada 2): estende o remap de `Shell.css.ts` até o
  // que Radix portaliza pra `document.body` (Modal, Popover, Tooltip) e até
  // o `ToastProvider` (irmão de `<App/>`, fora de `.raiz`) — nenhum dos dois
  // é descendente da raiz do shell, então só um marcador no documentElement
  // alcança os dois (ver comentário em `Shell.css.ts` e em `AppShell.tsx`,
  // mesma técnica pro tema legado). `useLayoutEffect`: aplica antes do
  // primeiro paint, sem flash de roxo caso algo portalize de cara.
  useLayoutEffect(() => {
    const el = document.documentElement
    el.setAttribute('data-lk-shell', '1')
    return () => el.removeAttribute('data-lk-shell')
  }, [])
  const navegar = useNavigate()
  const [colapsada, setColapsada] = useState(false)
  const [paletaAberta, setPaletaAberta] = useState(false)

  const grupos = useMemo(() => navVisivel([...NAV_EPI, ...NAV_ESTUDIO, ...NAV_ADMIN], can), [can])

  /**
   * `navPorPerfil` guarda o endereço FINAL de cada tela (`/epi/eventos`), que é
   * o do desenho. Enquanto os dois fronts convivem, o front novo mora sob o
   * prefixo — então o prefixo entra aqui, na hora de navegar, e não na tabela.
   * Assim o tombamento é apagar `PREFIXO_NOVO` daqui, e a tabela já está certa.
   *
   * Sem isto o menu do front NOVO levaria para as telas do front ANTIGO, em
   * silêncio: `/epi/dashboard` é rota válida nos dois.
   */

  /**
   * Telas na paleta = as MESMAS que a navegação mostra. Derivar das duas
   * fontes separadamente faria a paleta oferecer atalho para tela que o perfil
   * não pode abrir — o ⌘K viraria um caminho lateral em volta da permissão.
   */
  const gruposPaleta = useMemo<GrupoPaleta[]>(
    () => [
      {
        id: 'telas',
        titulo: 'Telas',
        itens: grupos.flatMap((g) =>
          g.itens.map((i) => ({
            id: i.rota,
            rotulo: i.rotulo,
            detalhe: g.titulo,
            aoEscolher: () => navegar(PREFIXO_NOVO + i.rota),
          })),
        ),
      },
    ],
    [grupos, navegar],
  )

  // Quando a sessão morre. null = token ilegível; sem prazo não há o que avisar.
  const [expiraEm, setExpiraEm] = useState<number | null>(() => getSessionTokenExpMs())
  useEffect(() => {
    // O token troca sem desmontar o shell (renovação de contexto, refresh).
    // Relê de minuto em minuto para o aviso seguir o token vigente.
    const id = setInterval(() => setExpiraEm(getSessionTokenExpMs()), 60_000)
    return () => clearInterval(id)
  }, [])

  return (
    <div className={s.raiz}>
      <header className={s.topbar}>
        {comBarraLateral && (
          <button
            className={s.botaoIcone}
            onClick={() => setColapsada((v) => !v)}
            aria-label={colapsada ? 'Expandir menu' : 'Recolher menu'}
            aria-expanded={!colapsada}
          >
            <Menu size={18} strokeWidth={1.7} />
          </button>
        )}
        <Marca para={rotaHomeDoUsuario(isSuperAdmin)} />
        <span className={s.espacador} />
        <SeletorTenant />
        {/**
          * O sino é o MESMO componente do front antigo — inclusive o dedup de
          * rajada (câmera+classe em <60s) que a ux2 pôs nele. Reescrevê-lo aqui
          * criaria uma quarta contagem de "quantas situações existem", que
          * divergiria da do front antigo no primeiro ajuste.
          *
          * `rotaAlertas`: o deep-link tem de cair na tela de eventos do front
          * NOVO. O default do componente (`/epi/alerts`) é rota VÁLIDA no app —
          * mandaria o usuário, calado, para a tela ANTIGA (mesmo pisão descrito
          * em `RotasNovas.tsx`).
          *
          * Gate `alerts:read`: é a MESMA permissão do item "Eventos" da nav
          * (`navPorPerfil.ts`). Sino sem ela seria um badge que só sabe pedir
          * 403 e um clique que leva a uma tela que o perfil não abre.
          */}
        {can('alerts:read') && (
          <NotificationBell
            rotaAlertas={rotaNova('/epi/eventos')}
            rotaCentral={rotaNova('/notificacoes')}
          />
        )}
        <button
          className={s.botaoIcone}
          onClick={() => setPaletaAberta(true)}
          aria-label="Buscar (Command K)"
        >
          <Search size={18} strokeWidth={1.7} />
        </button>
        <span className={s.dicaAtalho}>⌘K</span>
        {/**
          * Saída. Medido nesta rodada: o front novo NÃO tinha nenhuma — o único
          * `logout()` da árvore `app/` era o do aviso de sessão, que só aparece
          * nos 5 minutos finais. Em máquina compartilhada de chão de fábrica
          * isso é um beco: entrou com a conta errada, não sai mais (a não ser
          * indo ao front antigo ou limpando o localStorage).
          *
          * Botão só de ícone, como os outros da topbar; o menu de usuário com
          * nome/papel é do desenho e vem quando ele for implementado.
          */}
        <button className={s.botaoIcone} onClick={logout} aria-label="Sair">
          <LogOut size={18} strokeWidth={1.7} />
        </button>
      </header>

      <div className={s.corpo}>
        {comBarraLateral && (
        <nav
          className={colapsada ? `${s.sidebar} ${s.sidebarColapsada}` : s.sidebar}
          aria-label="Navegação principal"
        >
          {grupos.map((g) => (
            <div key={g.titulo}>
              {!colapsada && <p className={s.grupoTitulo}>{g.titulo}</p>}
              {g.itens.map((i) => {
                const Icone = i.icone
                return (
                  <NavLink
                    key={i.rota}
                    to={PREFIXO_NOVO + i.rota}
                    className={({ isActive }) =>
                      isActive ? `${s.item} ${s.itemAtivo}` : s.item
                    }
                    title={colapsada ? i.rotulo : undefined}
                  >
                    <Icone size={18} strokeWidth={1.7} />
                    <span className={colapsada ? s.rotuloColapsado : undefined}>
                      {i.rotulo}
                    </span>
                  </NavLink>
                )
              })}
            </div>
          ))}
        </nav>
        )}

        <main className={s.conteudo}>
          <div className={s.conteudoInterno}>
            {carregando ? (
              <LogikosLoader estado="waiting" variante="fullscreen" rotulo="CARREGANDO" />
            ) : (
              /**
               * `Suspense` cobre o VÃO do `lazy()`; não cobre ERRO. Um `throw`
               * no render de qualquer tela, ou um pedaço que não baixa (deploy
               * no meio da sessão), sobe até a raiz e o React desmonta a árvore
               * INTEIRA: página em branco, sem topbar, sem menu, sem nada em
               * que clicar. O `ErrorBoundary` já existia — mas envolvendo só o
               * `AppRoutes` do front ANTIGO (`AppRoutes.tsx`).
               *
               * Ele fica POR FORA do `Suspense` de propósito: erro de carga do
               * `lazy()` atravessa o `Suspense` (que não captura erro), então
               * um boundary por dentro nunca o veria.
               *
               * `key`: o boundary guarda `hasError` no estado e não o solta
               * sozinho. Sem chave, quem clicasse no menu depois do erro
               * trocava a URL e continuava olhando a tela de erro — beco sem
               * saída até dar F5. Trocar a chave remonta o boundary limpo, e a
               * nav volta a ser saída de verdade (regra C2).
               *
               * A chave é a localização INTEIRA (caminho + querystring), e não
               * só o caminho. Com `pathname` sozinho, um deep-link para a tela
               * em que o usuário JÁ ESTÁ não chegava: o React Router mantém o
               * elemento montado quando só a querystring muda, e as quatro
               * telas do /novo que leem querystring (`epi/Eventos`,
               * `estudio/Dados`, `estudio/Classificar`,
               * `acesso/RedefinirSenha`) a leem UMA vez, em inicializador de
               * `useState`. Medido com a tela real montada: estando em
               * `/novo/epi/eventos` e clicando na notificação do sino, as
               * chamadas depois do clique eram `[]` — nem refetch, nem filtro,
               * nem realce; a URL mudava e a tela não. É o caminho mais
               * provável do sino, porque é onde o operador de EPI fica.
               *
               * O preço, dito por extenso: a subárvore remonta em toda troca
               * de caminho E de querystring — entre sub-abas de uma mesma área
               * (`/novo/estudio/dados` → `/novo/estudio/cobertura`) e num
               * deep-link que só troca parâmetros (a tela de eventos perde
               * seleção e página, que é o que se quer ao pular para outra
               * câmera). Medido antes de aceitar: os layouts de Estúdio e
               * Admin não guardam estado nenhum (nenhum `useState`/`useQuery`),
               * o cache do react-query sobrevive ao remonte, e NENHUMA tela do
               * /novo escreve na URL (`setSearchParams` = zero ocorrências em
               * `src/app`) — então remonte por querystring só acontece quando
               * alguém navega de propósito. Essa premissa tem alarme: o teste
               * "nenhuma tela do /novo escreve na URL" em `Shell.test.tsx`
               * fica vermelho no dia em que uma passar a escrever. Nesse dia o
               * conserto é o `ErrorBoundary` ganhar `resetKeys` (como o do
               * `react-error-boundary`) em vez de `key`, e a tela que escreve
               * derivar o estado dos parâmetros em vez de copiá-los no mount.
               */
              <ErrorBoundary key={pathname + search}>
                <Suspense
                  fallback={
                    <LogikosLoader estado="entering" variante="fullscreen" rotulo="ABRINDO" />
                  }
                >
                  <Outlet />
                </Suspense>
              </ErrorBoundary>
            )}
          </div>
        </main>
      </div>

      <PaletaComandos
        grupos={gruposPaleta}
        aberta={paletaAberta}
        onAbertaChange={setPaletaAberta}
      />
      {/**
        * O aviso RENOVA de verdade desde a issue #667: o botão chama
        * `POST /api/auth/refresh`, que troca o token vivo por outro com prazo
        * cheio. Ele não precisa de prop nenhuma daqui para isso — a renovação
        * mora na camada de auth (`hooks/useAuth`), não no shell.
        *
        * O que ele NÃO faz é `window.location.reload()` sob o rótulo "Renovar
        * sessão", como já fez: recarregar traz o MESMO token de volta, com o
        * MESMO `exp`, e o aviso reaparecia em segundos.
        *
        * `logout` continua ligado como saída: é o que o cartão oferece quando a
        * sessão já expirou (aí o backend recusa renovar) ou quando a renovação
        * falha.
        */}
      {expiraEm !== null && (
        <SessaoExpirando expiraEm={expiraEm} onEntrarDeNovo={logout} />
      )}
    </div>
  )
}
