/**
 * EPI Eventos — estilo da lista (handoff `EPI Eventos.dc.html`).
 *
 * Medidas, pesos e raios saem do desenho; cor, fonte e espaço saem de
 * `tokens/lk.css`. Zero hex solto: o teste `tokens/semHexSolto.test.ts` varre
 * `src/app/**` inteiro e reprova qualquer cor escrita à mão.
 *
 * Onde o desenho pede transparência sobre uma cor de token (barra de seleção
 * ciano a 5%, divisor de linha cinza a 8%), a receita é `color-mix`, não
 * `rgba(...)`: os tokens de superfície são `var(--color-*)` do white-label, e
 * `rgba()` não aceita variável — escrever o rgba na mão devolveria o hex fixo
 * do desenho e a tela deixaria de acompanhar a marca do tenant.
 */
import { globalStyle, style, styleVariants } from '@vanilla-extract/css'

import { OVERLINE_TRACKING, TELA_ESTREITA, lk } from '../tokens/lk.css'

/** Fio de 1px entre linhas — o desenho usa cinza a 8%, não a borda cheia. */
const DIVISOR = `color-mix(in srgb, ${lk.cor.cinzaNevoa} 8%, transparent)`

export const pagina = style({
  display: 'flex',
  flexDirection: 'column',
  gap: '14px',
  fontFamily: lk.fonte.ui,
  color: lk.cor.brancoSinal,
})

export const cabecalho = style({
  display: 'flex',
  alignItems: 'center',
  flexWrap: 'wrap',
  gap: '10px',
  /** SR3: coluna única — título, meta e cada filtro em sua própria linha. */
  '@media': { [TELA_ESTREITA]: { flexDirection: 'column', alignItems: 'stretch' } },
})

export const titulo = style({
  margin: 0,
  fontFamily: lk.fonte.titulo,
  fontWeight: 700,
  fontSize: '26px',
})

/** "23 NO PERÍODO" — dado, então mono. */
export const meta = style({
  fontFamily: lk.fonte.mono,
  fontSize: '12px',
  color: lk.cor.cinzaNevoa,
})

export const espacador = style({ flex: 1 })

export const filtro = style({
  height: '36px',
  background: lk.cor.grafite,
  border: `1px solid ${lk.cor.borda}`,
  borderRadius: '6px',
  color: lk.cor.brancoSinal,
  fontFamily: lk.fonte.ui,
  fontSize: '13px',
  padding: `0 ${lk.espaco.x1}`,
  cursor: 'pointer',
  selectors: {
    '&:focus-visible': { outline: `2px solid ${lk.cor.cianoVisao}`, outlineOffset: '1px' },
  },
  /** SR3: alvo ≥44px e largura cheia — o select vira uma linha da coluna. */
  '@media': { [TELA_ESTREITA]: { height: '44px', width: '100%', boxSizing: 'border-box' } },
})

/** Botão secundário do desenho: 32px, borda fria, ciano só no hover/foco. */
export const botao = style({
  height: '32px',
  padding: '0 13px',
  display: 'inline-flex',
  alignItems: 'center',
  gap: '6px',
  background: 'transparent',
  border: `1px solid ${lk.cor.borda}`,
  borderRadius: '7px',
  color: lk.cor.brancoSinal,
  fontFamily: lk.fonte.ui,
  fontSize: '12.5px',
  fontWeight: 600,
  cursor: 'pointer',
  ':hover': { borderColor: lk.cor.cianoVisao, color: lk.cor.cianoVisao },
  ':disabled': { opacity: 0.5, cursor: 'default' },
  selectors: {
    '&:focus-visible': { outline: `2px solid ${lk.cor.cianoVisao}`, outlineOffset: '1px' },
  },
  /** SR3: alvo ≥44px — regra do handoff mobile para quem opera em pé. */
  '@media': { [TELA_ESTREITA]: { height: '44px' } },
})

/** Primário: ciano CHEIO — o único lugar da tela onde ele é fundo, e é botão. */
export const botaoPrimario = style([
  botao,
  {
    height: '34px',
    padding: '0 16px',
    border: 'none',
    background: lk.cor.cianoVisao,
    color: lk.cor.preto,
    fontWeight: 700,
    fontSize: '13px',
    ':hover': { background: lk.cor.cianoProfundo, color: lk.cor.preto },
    // `botao` já herda o alvo ≥44px, mas esta classe redeclara `height` sem
    // media — sem repetir aqui, o valor incondicional de 34px venceria.
    '@media': { [TELA_ESTREITA]: { height: '44px' } },
  },
])

export const barraSelecao = style({
  display: 'flex',
  alignItems: 'center',
  gap: '12px',
  padding: `10px ${lk.espaco.x2}`,
  background: `color-mix(in srgb, ${lk.cor.cianoVisao} 5%, transparent)`,
  border: `1px solid color-mix(in srgb, ${lk.cor.cianoVisao} 30%, transparent)`,
  borderRadius: lk.raio.s,
  '@media': { [TELA_ESTREITA]: { flexDirection: 'column', alignItems: 'stretch' } },
})

export const contagemSelecao = style({
  fontSize: '13px',
  fontWeight: 600,
  color: lk.cor.cianoVisao,
})

export const cartao = style({
  background: lk.cor.grafite,
  border: `1px solid ${lk.cor.borda}`,
  borderRadius: lk.raio.m,
  overflowX: 'auto',
})

export const tabela = style({
  width: '100%',
  borderCollapse: 'collapse',
  minWidth: '980px',
  /**
   * SR3: "lista vira cards". O `<table>` real não vira bloco sozinho — thead,
   * tbody, tr e td precisam do próprio `display` trocado, senão o navegador
   * refaz a caixa de tabela por baixo (fixup do CSS 2.1) e a rolagem
   * horizontal volta pela porta dos fundos.
   */
  '@media': { [TELA_ESTREITA]: { display: 'block', width: '100%', minWidth: '0' } },
})

globalStyle(`${tabela} thead`, {
  // Sem coluna, o rótulo "EVENTO/CÂMERA/HORA…" perde sentido; o cartão lê
  // pela cor+ícone+palavra que cada célula já carrega.
  '@media': { [TELA_ESTREITA]: { display: 'none' } },
})

globalStyle(`${tabela} tbody`, {
  '@media': { [TELA_ESTREITA]: { display: 'block', width: '100%' } },
})

globalStyle(`${tabela} tbody tr`, {
  '@media': {
    [TELA_ESTREITA]: {
      display: 'flex',
      flexDirection: 'column',
      width: '100%',
      padding: '10px 0',
      borderBottom: `1px solid ${lk.cor.borda}`,
    },
  },
})

globalStyle(`${tabela} tbody tr:last-child`, {
  '@media': { [TELA_ESTREITA]: { borderBottom: 'none' } },
})

/** Overline do handoff: mono, caixa alta, tracking largo. */
export const cabecalhoCelula = style({
  padding: `10px 14px`,
  textAlign: 'left',
  fontFamily: lk.fonte.mono,
  fontWeight: 400,
  fontSize: '10px',
  letterSpacing: '0.14em',
  textTransform: 'uppercase',
  color: lk.cor.cinzaNevoa,
  borderBottom: `1px solid ${lk.cor.borda}`,
  whiteSpace: 'nowrap',
})

export const celula = style({
  padding: `12px 14px`,
  borderBottom: `1px solid ${DIVISOR}`,
  fontSize: '13px',
  verticalAlign: 'middle',
  // A `tr` já ganha a borda/o espaçamento do cartão a 768px; aqui é só tirar
  // o divisor duplicado e deixar cada dado ocupar a largura toda.
  '@media': {
    [TELA_ESTREITA]: { display: 'block', width: '100%', padding: '3px 0', borderBottom: 'none' },
  },
})

export const celulaMono = style([celula, { fontFamily: lk.fonte.mono }])

export const celulaAcoes = style([
  celula,
  {
    display: 'flex',
    gap: lk.espaco.x1,
    justifyContent: 'flex-end',
    flexWrap: 'wrap',
    // Empilha "Reconhecer"/"Abrir →" full-width — mesmo alvo ≥44px do `botao`.
    '@media': { [TELA_ESTREITA]: { flexDirection: 'column', alignItems: 'stretch' } },
  },
])

/** Linha apontada por deep-link (sino de notificações) — realce temporário. */
export const linhaDestacada = style({
  outline: `2px solid ${lk.cor.cianoVisao}`,
  outlineOffset: '-2px',
})

export const caixaSelecao = style({
  width: '16px',
  height: '16px',
  accentColor: lk.cor.cianoVisao,
  cursor: 'pointer',
})

/**
 * Selo genérico: mono, caixa alta, borda da própria cor. A COR vem de quem
 * usa — polaridade e veredito têm paletas deliberadamente disjuntas.
 */
export const selo = style({
  display: 'inline-flex',
  alignItems: 'center',
  gap: '5px',
  fontFamily: lk.fonte.mono,
  fontSize: '10px',
  letterSpacing: '0.08em',
  textTransform: 'uppercase',
  borderRadius: '5px',
  padding: '3px 8px',
  border: '1px solid currentColor',
  whiteSpace: 'nowrap',
})

/**
 * POLARIDADE — o que o evento É (classe do modelo, ADR-0065). Verde/vermelho
 * moram AQUI e só aqui. Três estados: NULL em `is_violation` não é
 * conformidade, é "ninguém decidiu".
 */
export const corPolaridade = styleVariants({
  violacao: { color: lk.estado.nc },
  conformidade: { color: lk.estado.ok },
  indefinida: { color: lk.cor.cinzaNevoa },
})

/**
 * VEREDITO — o que uma PESSOA julgou. Paleta disjunta da polaridade de
 * propósito: se "falso positivo" fosse vermelho, veredito e violação viravam
 * a mesma cor na mesma linha e o operador perderia a diferença.
 */
export const corVeredito = styleVariants({
  procedente: { color: lk.cor.brancoSinal },
  'falso-positivo': { color: lk.estado.atencao },
  'nao-revisado': { color: lk.cor.cinzaNevoa },
})

/** Estado de FLUXO — terceiro eixo, terceira paleta. Três degraus: ninguém
 *  tocou (atenção), alguém deu ciência (ok), alguém julgou (ok, e o veredito
 *  em si mora na coluna ao lado). "Avaliado" NÃO ganha cor de veredito: se
 *  procedente e falso positivo tivessem cores diferentes aqui, esta coluna
 *  viraria uma segunda coluna de veredito. */
export const corStatus = styleVariants({
  novo: { color: lk.estado.atencao },
  reconhecido: { color: lk.estado.ok },
  avaliado: { color: lk.estado.ok },
})

/** Procedência temporal — só a afirmação NEGATIVA ("coleta retroativa"). */
export const seloRetroativo = style([selo, { color: lk.estado.atencao }])

/** Procedência DECLARADA (`violations[].origem`, issue #670). Caixa desenhada
 *  por PESSOA é ressalva (atenção); caixa do modelo é o caso esperado e não
 *  grita (névoa). Nenhuma das duas usa a paleta de veredito nem a de
 *  polaridade — são eixos diferentes. */
export const seloProcedencia = styleVariants({
  humana: [selo, { color: lk.estado.atencao }],
  modelo: [selo, { color: lk.cor.cinzaNevoa }],
})

/** Motivo do veredito: some por elipse, inteiro no `title`. */
export const motivo = style({
  display: 'block',
  marginTop: '3px',
  maxWidth: '160px',
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  whiteSpace: 'nowrap',
  fontSize: '11px',
  color: lk.cor.cinzaNevoa,
})

export const celulaEvento = style([
  celula,
  { display: 'flex', alignItems: 'center', gap: '9px', flexWrap: 'wrap' },
])

export const nomeClasse = style({ fontSize: '13.5px', fontWeight: 600 })

export const rodape = style({
  display: 'flex',
  alignItems: 'center',
  gap: '12px',
  fontFamily: lk.fonte.mono,
  fontSize: '11px',
  letterSpacing: '0.08em',
  color: lk.cor.cinzaNevoa,
})

export const nota = style({ fontSize: '12px', color: lk.cor.cinzaNevoa })

/** Vazio / erro / sem permissão — mesma caixa, copy diferente. */
export const painelCentral = style({
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  justifyContent: 'center',
  gap: '14px',
  textAlign: 'center',
  padding: lk.espaco.x3,
  minHeight: '340px',
})

export const painelTitulo = style({
  fontFamily: lk.fonte.titulo,
  fontWeight: 700,
  fontSize: '19px',
})

export const painelTexto = style({
  fontSize: '13.5px',
  color: lk.cor.cinzaNevoa,
  maxWidth: '380px',
  lineHeight: 1.55,
})

/** Detalhe técnico do erro: dado, então mono. */
export const painelDetalhe = style({
  fontFamily: lk.fonte.mono,
  fontSize: '12px',
  letterSpacing: '0.08em',
  color: lk.cor.cinzaNevoa,
})

export const botaoPainel = style([
  botaoPrimario,
  { height: '40px', padding: '0 18px', fontSize: '13.5px', borderRadius: lk.raio.s },
])

export const overlineLegenda = style({
  fontFamily: lk.fonte.mono,
  fontSize: '10px',
  letterSpacing: OVERLINE_TRACKING,
  textTransform: 'uppercase',
  color: lk.cor.cinzaNevoa,
})

/** Confiança da detecção (§9 paridade) — dado, então mono; cinza, sem cor de estado. */
export const confianca = style({
  fontFamily: lk.fonte.mono,
  fontSize: '12px',
  color: lk.cor.cinzaNevoa,
})

/**
 * Rajada (ux2/dedup) — o representante vem seguido de UMA linha "+N
 * repetições" quando a câmera+classe se repetiu em <60s. Clicável, nunca
 * esconde: expandir revela as N linhas originais.
 */
export const linhaRajadaToggle = style({
  padding: `8px 14px`,
  borderBottom: `1px solid ${DIVISOR}`,
  cursor: 'pointer',
  fontFamily: lk.fonte.mono,
  fontSize: '11px',
  letterSpacing: '0.04em',
  color: lk.cor.cianoVisao,
  background: `color-mix(in srgb, ${lk.cor.cianoVisao} 4%, transparent)`,
  ':hover': { background: `color-mix(in srgb, ${lk.cor.cianoVisao} 9%, transparent)` },
})

/** Linhas-repetição reveladas — mesma célula, tom apagado (não é evento novo). */
export const linhaRepeticao = style({ opacity: 0.72 })

// ── Gaveta de evidência (julgar sem sair da lista) ───────────────────────────
//
// Sobrepõe a lista em vez de dividir a largura com ela: a tabela tem 8 colunas
// e um painel LATERAL a empurraria para a rolagem horizontal — que é onde a
// posição na lista se perde de verdade. O fundo escurece mas a lista continua
// atrás, no mesmo lugar e na mesma rolagem, e é para lá que o Esc volta.

export const painelFundo = style({
  position: 'fixed',
  inset: 0,
  // `color-mix` e não `rgba()`: o preto é token de white-label (ver topo).
  background: `color-mix(in srgb, ${lk.cor.preto} 72%, transparent)`,
  zIndex: 900,
  display: 'flex',
  justifyContent: 'flex-end',
})

export const gaveta = style({
  width: 'min(760px, 100vw)',
  height: '100%',
  boxSizing: 'border-box',
  display: 'flex',
  flexDirection: 'column',
  background: lk.cor.grafite,
  borderLeft: `1px solid ${lk.cor.borda}`,
  fontFamily: lk.fonte.ui,
  color: lk.cor.brancoSinal,
  ':focus-visible': { outline: 'none' },
})

export const gavetaTopo = style({
  display: 'flex',
  alignItems: 'center',
  gap: lk.espaco.x1,
  padding: `12px ${lk.espaco.x2}`,
  borderBottom: `1px solid ${lk.cor.borda}`,
  flexShrink: 0,
})

export const gavetaTitulo = style({
  fontFamily: lk.fonte.titulo,
  fontWeight: 700,
  fontSize: '16px',
})

export const gavetaFechar = style({
  width: '32px',
  height: '32px',
  display: 'inline-flex',
  alignItems: 'center',
  justifyContent: 'center',
  background: 'transparent',
  border: `1px solid ${lk.cor.borda}`,
  borderRadius: lk.raio.s,
  color: lk.cor.cinzaNevoa,
  cursor: 'pointer',
  ':hover': { color: lk.cor.brancoSinal, borderColor: lk.cor.cianoVisao },
  '@media': { [TELA_ESTREITA]: { width: '44px', height: '44px' } },
})

export const gavetaCorpo = style({
  flex: 1,
  minHeight: 0,
  overflowY: 'auto',
  display: 'flex',
  flexDirection: 'column',
  gap: '12px',
  padding: lk.espaco.x2,
})

/** Ficha do evento: rótulo à esquerda, dado à direita. */
export const gavetaDados = style({
  display: 'grid',
  gridTemplateColumns: 'auto 1fr',
  gap: '6px 14px',
  alignItems: 'baseline',
  fontSize: '13px',
})

export const gavetaRotulo = style({
  fontFamily: lk.fonte.mono,
  fontSize: '10px',
  letterSpacing: OVERLINE_TRACKING,
  textTransform: 'uppercase',
  color: lk.cor.cinzaNevoa,
})

export const gavetaVeredito = style({
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'flex-start',
  gap: '8px',
  padding: lk.espaco.x2,
  border: `1px solid ${lk.cor.borda}`,
  borderRadius: lk.raio.m,
})

export const gavetaAjuda = style({ fontSize: '12.5px', color: lk.cor.cinzaNevoa, lineHeight: 1.5 })

export const gavetaErro = style({ fontSize: '12px', color: lk.estado.nc })

export const gavetaBotoes = style({
  display: 'flex',
  gap: lk.espaco.x1,
  flexWrap: 'wrap',
  '@media': { [TELA_ESTREITA]: { flexDirection: 'column', alignItems: 'stretch', width: '100%' } },
})

export const gavetaRodape = style({
  display: 'flex',
  alignItems: 'center',
  gap: lk.espaco.x1,
  padding: `10px ${lk.espaco.x2}`,
  borderTop: `1px solid ${lk.cor.borda}`,
  flexShrink: 0,
})

/** Campo que falta preencher — borda de erro, sem trocar a cor do texto. */
export const filtroErro = style({ borderColor: lk.estado.nc })

/** Linha cujo evento está aberto na gaveta: é ELA que a pessoa não pode
 *  perder de vista ao decidir e avançar. Paleta neutra de propósito — não é
 *  polaridade, não é veredito, não é status: é só "onde eu estou". */
export const linhaAberta = style({
  outline: `2px solid ${lk.cor.bordaForte}`,
  outlineOffset: '-2px',
  background: `color-mix(in srgb, ${lk.cor.cinzaNevoa} 6%, transparent)`,
})
