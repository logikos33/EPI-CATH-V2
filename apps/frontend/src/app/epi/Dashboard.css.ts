/**
 * EPI Dashboard — medidas e cores do `EPI Dashboard.dc.html`.
 *
 * Zero hex: cor só por `lk`. Tintas (borda de acento, trilho de barra) saem de
 * `color-mix` sobre o token — assim o white-label do tenant continua alcançando
 * a cor, o que um rgba escrito à mão não faria.
 *
 * O Shell já dá `padding: 24px` e `max-width: 1280px` ao conteúdo. Repetir aqui
 * daria 48px de respiro e uma segunda fonte de verdade sobre a largura da tela.
 */
import { style, styleVariants } from '@vanilla-extract/css'

import { OVERLINE_TRACKING, TELA_ESTREITA, lk } from '../tokens/lk.css'

export const raiz = style({
  display: 'flex',
  flexDirection: 'column',
  gap: lk.espaco.x2,
})

// ── Cabeçalho ───────────────────────────────────────────────────────────────

export const cabecalho = style({
  display: 'flex',
  alignItems: 'center',
  gap: '12px',
  flexWrap: 'wrap',
  // `minWidth: 0`: sem isto, o botão "Personalizar widgets" (uma única
  // palavra sem quebra) vira o "automatic minimum size" do cabeçalho inteiro
  // e arrasta a página pra fora do viewport — o clássico estouro de flexbox.
  '@media': { [TELA_ESTREITA]: { flexDirection: 'column', alignItems: 'stretch', minWidth: '0' } },
})

export const titulo = style({
  margin: 0,
  fontFamily: lk.fonte.titulo,
  fontWeight: 700,
  fontSize: '26px',
  // "Dashboard" é uma palavra só — min-content de texto é o tamanho da MAIOR
  // PALAVRA, então a 26px ela sozinha já é mais larga que a coluna do
  // telefone. Só a fonte menor resolve, wrap não ajuda numa palavra única.
  '@media': { [TELA_ESTREITA]: { fontSize: '21px' } },
})

export const espacador = style({ flex: 1 })

export const seletor = style({
  height: '36px',
  padding: `0 ${lk.espaco.x1}`,
  background: lk.cor.grafite,
  border: `1px solid ${lk.cor.borda}`,
  borderRadius: lk.raio.s,
  color: lk.cor.brancoSinal,
  fontFamily: lk.fonte.ui,
  fontSize: '13px',
  cursor: 'pointer',
  ':focus-visible': { outline: `2px solid ${lk.cor.cianoVisao}`, outlineOffset: '1px' },
  '@media': { [TELA_ESTREITA]: { height: '44px', width: '100%' } },
})

export const botaoFantasma = style({
  height: '36px',
  padding: '0 14px',
  background: 'transparent',
  border: `1px solid ${lk.cor.borda}`,
  borderRadius: lk.raio.s,
  color: lk.cor.cinzaNevoa,
  fontFamily: lk.fonte.ui,
  fontSize: '13px',
  display: 'inline-flex',
  alignItems: 'center',
  gap: '6px',
  cursor: 'pointer',
  ':hover': { color: lk.cor.brancoSinal, borderColor: lk.cor.cianoVisao },
  '@media': { [TELA_ESTREITA]: { height: '44px', minWidth: '0', flexWrap: 'wrap' } },
})

export const envoltorioPopover = style({ position: 'relative' })

/** Popover "Personalizar widgets" — ancorado no botão, sem portal. */
export const popover = style({
  position: 'absolute',
  top: 'calc(100% + 8px)',
  right: 0,
  width: '260px',
  display: 'flex',
  flexDirection: 'column',
  gap: lk.espaco.x1,
  padding: lk.espaco.x2,
  background: lk.cor.grafite,
  border: `1px solid ${lk.cor.borda}`,
  borderRadius: lk.raio.g,
  boxShadow: '0 8px 32px rgba(0, 0, 0, 0.7)',
  zIndex: 40,
})

export const popoverLinha = style({
  display: 'flex',
  alignItems: 'center',
  gap: lk.espaco.x1,
  fontSize: '13px',
  color: lk.cor.brancoSinal,
  cursor: 'pointer',
})

export const popoverCheck = style({ accentColor: lk.cor.cianoVisao, cursor: 'pointer' })

// ── Cartões de KPI ──────────────────────────────────────────────────────────

export const gridKpi = style({
  display: 'grid',
  // `minmax(0, …)` também aqui, e não só nos breakpoints logo abaixo: o
  // comentário do `[TELA_ESTREITA]` já explicava por quê ("uma track `1fr`
  // ainda respeita o min-content dos filhos") e a declaração de desktop era a
  // única da folha que não seguia a própria regra.
  gridTemplateColumns: '360px minmax(0, 1fr) minmax(0, 1fr) minmax(0, 1fr)',
  gap: '12px',
  '@media': {
    '(max-width: 1180px)': { gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1fr)' },
    // Harmonizado com o breakpoint mobile único (era 720px solto) — "cards
    // empilham 1 coluna" abaixo de 768px, SR3. `minmax(0, 1fr)`, não `1fr`
    // sozinho: uma track `1fr` ainda respeita o min-content dos filhos e um
    // cartão com número de 84px de fonte estoura a única coluna.
    [TELA_ESTREITA]: { gridTemplateColumns: 'minmax(0, 1fr)' },
  },
})

const cartaoBase = style({
  display: 'flex',
  flexDirection: 'column',
  background: lk.cor.grafite,
  border: `1px solid ${lk.cor.borda}`,
  borderRadius: lk.raio.g,
})

export const cartaoScore = style([
  cartaoBase,
  { gap: '10px', padding: '20px', position: 'relative' },
])

export const cartaoKpi = style([
  cartaoBase,
  { gap: '6px', padding: '16px 18px', borderTopWidth: '2px', borderTopStyle: 'solid' },
])

export const overline = style({
  fontFamily: lk.fonte.mono,
  fontSize: '10px',
  letterSpacing: OVERLINE_TRACKING,
  textTransform: 'uppercase',
  color: lk.cor.cinzaNevoa,
})

export const linhaOverline = style({
  display: 'flex',
  alignItems: 'center',
  gap: lk.espaco.x1,
  '@media': { [TELA_ESTREITA]: { flexWrap: 'wrap' } },
})

export const botaoAjuda = style({
  width: '16px',
  height: '16px',
  flex: 'none',
  borderRadius: '50%',
  border: `1px solid ${lk.cor.cinzaNevoa}`,
  background: 'transparent',
  color: lk.cor.cinzaNevoa,
  fontFamily: lk.fonte.mono,
  fontSize: '10px',
  lineHeight: 1,
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  cursor: 'help',
  ':hover': { color: lk.cor.brancoSinal, borderColor: lk.cor.cianoVisao },
})

export const dica = style({
  position: 'absolute',
  top: '44px',
  left: '20px',
  right: '20px',
  zIndex: 10,
  background: lk.cor.preto,
  border: `1px solid ${lk.cor.borda}`,
  borderRadius: lk.raio.s,
  padding: '12px',
  fontSize: '12px',
  color: lk.cor.cinzaNevoa,
  lineHeight: 1.55,
  boxShadow: '0 8px 32px rgba(0, 0, 0, 0.7)',
})

export const scoreLinha = style({
  display: 'flex',
  alignItems: 'flex-end',
  gap: '14px',
  '@media': { [TELA_ESTREITA]: { flexWrap: 'wrap' } },
})

export const scoreNumero = style({
  fontFamily: lk.fonte.titulo,
  fontWeight: 700,
  fontSize: '84px',
  lineHeight: 0.9,
  // 84px de fonte é maior que a coluna inteira num telefone — o score
  // continua sendo o dado principal do cartão, só menor.
  '@media': { [TELA_ESTREITA]: { fontSize: '48px' } },
})

export const scoreLado = style({
  display: 'flex',
  flexDirection: 'column',
  gap: '2px',
  paddingBottom: lk.espaco.x1,
})

/** Estado = cor + ícone + palavra. A cor entra inline; a palavra é obrigatória. */
export const estadoLinha = style({
  display: 'flex',
  alignItems: 'center',
  gap: '5px',
  fontSize: '12px',
  fontWeight: 600,
})

export const legenda = style({ fontSize: '12px', color: lk.cor.cinzaNevoa })

export const rodapeMono = style({
  display: 'flex',
  justifyContent: 'space-between',
  gap: lk.espaco.x1,
  marginTop: 'auto',
  fontFamily: lk.fonte.mono,
  fontSize: '10.5px',
  letterSpacing: '0.06em',
  color: lk.cor.cinzaNevoa,
  '@media': { [TELA_ESTREITA]: { flexWrap: 'wrap' } },
})

export const kpiValor = style({
  fontFamily: lk.fonte.mono,
  fontWeight: 700,
  fontSize: '34px',
  lineHeight: 1,
  // Número em coluna com o cartão de baixo: largura de dígito fixa mantém a
  // vírgula do milhar alinhada entre cartões e entre atualizações.
  fontVariantNumeric: 'tabular-nums',
})

export const kpiValorSufixo = style({ fontSize: '20px', color: lk.cor.cinzaNevoa })

export const atalho = style({
  fontSize: '12.5px',
  fontWeight: 600,
  marginTop: 'auto',
  color: lk.cor.cianoVisao,
  textDecoration: 'none',
  ':hover': { color: lk.cor.cianoProfundo },
})

/** Mesmo atalho, no cabeçalho de um painel: encosta à direita, sem empurrão. */
export const atalhoInline = style({
  marginLeft: 'auto',
  fontSize: '12px',
  fontWeight: 600,
  color: lk.cor.cianoVisao,
  textDecoration: 'none',
  ':hover': { color: lk.cor.cianoProfundo },
})

// ── Painéis (widgets) ───────────────────────────────────────────────────────

export const gridPaineis = style({
  display: 'grid',
  // `minmax(0, …)` e não `1.4fr 1fr 1fr` solto — MESMA regra que `gridKpi` já
  // aplica acima, pelo mesmo motivo. Uma track `1fr` é `minmax(auto, 1fr)`:
  // ela cresce até o MIN-CONTENT do painel que estiver nela, e a grade inteira
  // estoura o container. Medido no DEV a 1440px (RVB, 09/09): `Eventos por
  // hora` tem 565px de min-content (24 rótulos "00h" que não encolhem) e
  // `Câmeras com mais eventos`, 416px (nome de câmera em `nowrap`) — as tracks
  // resolviam para `565 · 238 · 416` = 1243px dentro de uma coluna de 1150px,
  // e a TERCEIRA coluna nascia 93px fora da viewport, cortada pela borda
  // direita da tela.
  //
  // ⚠️ E os painéis são ARRASTÁVEIS: qualquer um pode parar em qualquer
  // coluna. Consertar só o painel largo de hoje deixaria a próxima
  // reordenação reabrir o mesmo corte — o piso tem de morrer na TRACK, não no
  // painel. Cada painel cuida do próprio transbordo (ver `barrasRolagem`).
  gridTemplateColumns: 'minmax(0, 1.4fr) minmax(0, 1fr) minmax(0, 1fr)',
  gap: '12px',
  alignItems: 'start',
  '@media': { '(max-width: 1180px)': { gridTemplateColumns: 'minmax(0, 1fr)' } },
})

export const painel = style([cartaoBase, { gap: '12px', padding: '18px' }])

export const painelCabecalho = style({
  display: 'flex',
  alignItems: 'center',
  gap: lk.espaco.x1,
  '@media': { [TELA_ESTREITA]: { flexWrap: 'wrap' } },
})

export const painelTitulo = style({ fontSize: '14px', fontWeight: 600 })

export const painelNota = style({
  marginLeft: 'auto',
  fontFamily: lk.fonte.mono,
  fontSize: '10.5px',
  letterSpacing: '0.06em',
  color: lk.cor.cinzaNevoa,
})

/** Alça de arrasto — `cursor: grab`, como no desenho. */
export const alca = style({
  display: 'inline-flex',
  padding: 0,
  border: 'none',
  background: 'transparent',
  color: lk.cor.cinzaNevoa,
  cursor: 'grab',
  touchAction: 'none',
  ':focus-visible': { outline: `2px solid ${lk.cor.cianoVisao}` },
})

export const arrastando = style({ opacity: 0.6, zIndex: 20 })

// Eventos por hora
export const barras = style({
  display: 'flex',
  alignItems: 'flex-end',
  gap: '5px',
  height: '110px',
  // Rótulo de hora ("14h") tem largura mínima própria; com ~24 barras isso
  // soma mais que a tela some — rolagem CONTIDA aqui, nunca na página.
  '@media': { [TELA_ESTREITA]: { overflowX: 'auto' } },
})

/**
 * A MESMA barra, quando cada coluna leva rótulo próprio ("Eventos por hora":
 * 24 colunas, 24 rótulos de hora). Aí o min-content é a soma dos rótulos —
 * 527px medidos —, e com `minmax(0, 1fr)` na grade o painel deixou de esticar
 * a coluna: quem tem de ceder é o gráfico.
 *
 * Rolagem CONTIDA aqui e não `min-width: 0` na coluna: encolher a coluna abaixo
 * do rótulo faz "08h" e "09h" se sobreporem, e um eixo de HORA ilegível é pior
 * que um eixo que rola. É a mesma decisão que `barras` já tomava no telefone —
 * só que a largura da coluna da grade não é a largura da tela, e media query
 * nenhuma enxerga isso.
 *
 * ⛔ Não promova isto para `barras`: os painéis empilhados usam `rotuloEixo`
 * com `width: 0` e texto transbordando para os lados, e um container de
 * rolagem passa a RECORTAR a metade esquerda do primeiro rótulo ("00h").
 */
export const barrasRolagem = style([barras, { overflowX: 'auto' }])

export const colunaBarra = style({
  flex: 1,
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  gap: '5px',
  height: '100%',
  justifyContent: 'flex-end',
  '@media': { [TELA_ESTREITA]: { minWidth: '10px' } },
})

export const barra = style({
  width: '100%',
  minHeight: '2px',
  borderRadius: '3px 3px 0 0',
})

export const barraRotulo = style({
  fontFamily: lk.fonte.mono,
  fontSize: '9.5px',
  color: lk.cor.cinzaNevoa,
})

// Violações por classe
export const classeLinha = style({
  display: 'flex',
  alignItems: 'center',
  gap: '10px',
})

export const classeNome = style({ fontSize: '12.5px', width: '76px', flex: 'none' })

export const classeTrilho = style({
  flex: 1,
  height: '14px',
  background: lk.cor.preto,
  borderRadius: '4px',
  overflow: 'hidden',
})

export const classePreenchimento = style({ height: '100%', background: lk.estado.nc })

export const classeValor = style({
  fontFamily: lk.fonte.mono,
  fontSize: '13px',
  width: '38px',
  textAlign: 'right',
  fontVariantNumeric: 'tabular-nums',
})

// ── Violações por horário do dia ────────────────────────────────────────────
//
// Barra EMPILHADA: o segmento de baixo é o total do horário, o de cima é a
// fatia que foi violação. Só a violação sai colorida — a leitura de relance é
// "onde está o vermelho", e o resto do volume fica como contexto neutro.

export const colunaEmpilhada = style({
  flex: 1,
  minWidth: 0,
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  gap: '5px',
  height: '100%',
  justifyContent: 'flex-end',
  '@media': { [TELA_ESTREITA]: { minWidth: '9px' } },
})

export const pilha = style({
  width: '100%',
  display: 'flex',
  flexDirection: 'column',
  justifyContent: 'flex-end',
  borderRadius: '3px 3px 0 0',
  overflow: 'hidden',
  background: 'transparent',
})

/**
 * Rótulo do eixo com LARGURA ZERO. Uma coluna de flex respeita o min-content
 * dos filhos, então "18h" em mono (~22px) alargava a coluna rotulada e
 * espremia as vizinhas: as barras saíam do passo regular e o rótulo passava a
 * apontar para a barra errada — num gráfico de "em que HORA", ler uma hora ao
 * lado é o erro que importa. Zero de largura + overflow visível deixa o texto
 * centrado sem participar da conta da grade.
 */
export const rotuloEixo = style({
  width: 0,
  overflow: 'visible',
  whiteSpace: 'nowrap',
  textAlign: 'center',
  fontFamily: lk.fonte.mono,
  fontSize: '9.5px',
  color: lk.cor.cinzaNevoa,
  // ALTURA FIXA, e não a do texto: só uma coluna em cada seis leva rótulo, e
  // um <span> vazio tem zero de altura — as colunas sem rótulo desciam 11px a
  // mais e a base das barras deixava de ser uma linha só. Numa comparação
  // visual de alturas, base torta é erro de leitura direto.
  height: '12px',
  lineHeight: '12px',
  flex: 'none',
})

export const segmentoViolacao = style({ width: '100%', background: lk.estado.nc })

export const segmentoRestante = style({ width: '100%', background: lk.cor.borda })

/** Legenda de cor do gráfico — cor + palavra, nunca cor sozinha. */
export const chaveCores = style({
  display: 'flex',
  flexWrap: 'wrap',
  gap: '14px',
  fontSize: '11.5px',
  color: lk.cor.cinzaNevoa,
})

export const chaveItem = style({ display: 'flex', alignItems: 'center', gap: '6px' })

export const chaveMarca = style({
  width: '9px',
  height: '9px',
  flex: 'none',
  borderRadius: '2px',
})

/** Frase de destaque do painel: o padrão que o gráfico revelou, em texto. */
export const destaqueLinha = style({
  fontSize: '13px',
  lineHeight: 1.5,
  color: lk.cor.cinzaNevoa,
})

export const destaqueNumero = style({
  fontFamily: lk.fonte.mono,
  fontWeight: 700,
  fontSize: '15px',
  color: lk.cor.brancoSinal,
  fontVariantNumeric: 'tabular-nums',
})

// ── Composição dos eventos ──────────────────────────────────────────────────

/**
 * Uma paleta por eixo: aqui o eixo é POLARIDADE (violação · conformidade · não
 * definida), e ela não empresta o ciano da marca nem a cor de reconhecimento —
 * mesma regra que `Eventos.tsx` e `Acoes.tsx` já seguem.
 */
export const polaridadePreenchimento = styleVariants({
  violacao: { height: '100%', background: lk.estado.nc },
  conformidade: { height: '100%', background: lk.estado.ok },
  indefinido: { height: '100%', background: lk.cor.cinzaNevoa },
})

export const linhaResumo = style({
  display: 'flex',
  alignItems: 'baseline',
  justifyContent: 'space-between',
  gap: '10px',
  fontSize: '12.5px',
  color: lk.cor.cinzaNevoa,
  '@media': { [TELA_ESTREITA]: { flexWrap: 'wrap' } },
})

export const linhaResumoValor = style({
  fontFamily: lk.fonte.mono,
  fontSize: '13px',
  fontWeight: 700,
  color: lk.cor.brancoSinal,
  fontVariantNumeric: 'tabular-nums',
})

// Câmeras com mais eventos
export const rankingLista = style({
  display: 'flex',
  flexDirection: 'column',
  gap: '9px',
})

export const rankingLinha = style({
  display: 'flex',
  alignItems: 'center',
  gap: '12px',
  '@media': { [TELA_ESTREITA]: { flexWrap: 'wrap' } },
})

export const rankingPos = style({
  width: '18px',
  flex: 'none',
  textAlign: 'right',
  fontFamily: lk.fonte.mono,
  fontSize: '11px',
  color: lk.cor.cinzaNevoa,
})

export const rankingNome = style({
  // `flex: none` + 190px fixos comiam a coluna inteira: o painel ocupa uma
  // faixa de ~330px na grade de 3 colunas, e 190 (nome) + 18 (posição) + 44
  // (valor) + gaps deixavam ~50px de trilho. A barra — que é O DADO — virava
  // um risco de 3px e a lista se lia como tabela sem gráfico. O nome passa a
  // ceder espaço (`flex-shrink`), com piso de 80px e reticências.
  flex: '0 1 190px',
  minWidth: '80px',
  fontSize: '13.5px',
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  whiteSpace: 'nowrap',
  // Sobra menos coluna pro nome — dá respiro pro trilho, que é o dado.
  '@media': { [TELA_ESTREITA]: { flexBasis: '96px', minWidth: '64px' } },
})

/** Regra do ciano: só as 3 primeiras posições saem em destaque — resto neutro. */
export const rankingDestaque = styleVariants({
  top: { color: lk.cor.brancoSinal },
  resto: { color: lk.cor.cinzaNevoa },
})

export const rankingTrilho = style({
  flex: '1 1 auto',
  // Piso: abaixo de ~90px a diferença entre 647 e 77 deixa de ser visível e o
  // trilho vira enfeite. Quem cede espaço para ele é o nome, não o número.
  minWidth: '90px',
  height: '22px',
  borderRadius: '4px',
  background: lk.cor.preto,
  overflow: 'hidden',
  '@media': { [TELA_ESTREITA]: { minWidth: '60px' } },
})

export const rankingPreenchimento = styleVariants({
  top: { height: '100%', borderRadius: '4px', background: lk.cor.cianoVisao },
  resto: { height: '100%', borderRadius: '4px', background: lk.cor.borda },
})

export const rankingValor = style({
  width: '44px',
  flex: 'none',
  textAlign: 'right',
  fontFamily: lk.fonte.mono,
  fontSize: '13px',
  fontWeight: 700,
  fontVariantNumeric: 'tabular-nums',
})

export const rankingDivisor = style({ height: '1px', background: lk.cor.borda })

export const rankingRodape = style({
  display: 'flex',
  alignItems: 'center',
  gap: '10px',
})

export const rankingEnfase = style({ color: lk.cor.brancoSinal, fontWeight: 700 })

export const rankingVazio = style({
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  gap: '10px',
  padding: '36px 0',
  textAlign: 'center',
})

export const rankingVazioTitulo = style({
  fontFamily: lk.fonte.titulo,
  fontWeight: 700,
  fontSize: '15px',
})

export const rankingVazioTexto = style({
  fontSize: '13px',
  color: lk.cor.cinzaNevoa,
  maxWidth: '280px',
  lineHeight: 1.5,
})

// ── Estados de tela inteira ─────────────────────────────────────────────────

export const telaCentral = style({
  minHeight: '60vh',
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  justifyContent: 'center',
  gap: '14px',
  textAlign: 'center',
  padding: lk.medida.padding,
})

export const telaTitulo = style({
  fontFamily: lk.fonte.titulo,
  fontWeight: 700,
  fontSize: '19px',
})

export const telaTexto = style({
  fontSize: '13.5px',
  color: lk.cor.cinzaNevoa,
  maxWidth: '380px',
  lineHeight: 1.55,
})

export const telaDetalhe = style({
  fontFamily: lk.fonte.mono,
  fontSize: '12px',
  letterSpacing: '0.06em',
  color: lk.cor.cinzaNevoa,
})

/** Único botão primário da tela — o ciano é interativo, e só. */
export const botaoPrimario = style({
  display: 'inline-flex',
  alignItems: 'center',
  justifyContent: 'center',
  height: '40px',
  padding: '0 18px',
  textDecoration: 'none',
  border: 'none',
  borderRadius: lk.raio.s,
  background: lk.cor.cianoVisao,
  color: lk.cor.preto,
  fontFamily: lk.fonte.ui,
  fontSize: '13.5px',
  fontWeight: 700,
  cursor: 'pointer',
  ':hover': { background: lk.cor.cianoProfundo },
  '@media': { [TELA_ESTREITA]: { height: '44px' } },
})

/** Vazio/erro DENTRO de um painel — não derruba a tela inteira. */
export const painelVazio = style({
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  justifyContent: 'center',
  gap: lk.espaco.x1,
  minHeight: '110px',
  textAlign: 'center',
  fontSize: '12.5px',
  color: lk.cor.cinzaNevoa,
})

/** Bars/linhas/KPI viram <Link>: reset de âncora (sem sublinhado azul do
 * browser), cursor e foco visíveis — o layout flex de quem usa isto (barra,
 * linha) já vem de outra classe, concatenada no uso. */
export const linkLimpo = style({
  textDecoration: 'none',
  color: 'inherit',
  cursor: 'pointer',
  ':hover': { opacity: 0.8 },
  ':focus-visible': { outline: `2px solid ${lk.cor.cianoVisao}`, outlineOffset: '2px' },
})

export const botaoRetentar = style({
  height: '30px',
  padding: '0 12px',
  background: 'transparent',
  border: `1px solid ${lk.cor.borda}`,
  borderRadius: lk.raio.s,
  color: lk.cor.cianoVisao,
  fontFamily: lk.fonte.ui,
  fontSize: '12.5px',
  fontWeight: 600,
  cursor: 'pointer',
  ':hover': { borderColor: lk.cor.cianoVisao },
})

/** Acento de topo do cartão por estado. Neutro = cinza a 40%, como no desenho. */
export const acento = styleVariants({
  neutro: { borderTopColor: `color-mix(in srgb, ${lk.cor.cinzaNevoa} 40%, transparent)` },
  ok: { borderTopColor: lk.estado.ok },
  atencao: { borderTopColor: lk.estado.atencao },
  nc: { borderTopColor: lk.estado.nc },
})
