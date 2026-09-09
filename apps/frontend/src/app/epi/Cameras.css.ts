/**
 * EPI Câmeras & Sites — medidas literais de `EPI Câmeras.dc.html`.
 *
 * Toda cor e toda fonte por token (`lk.css.ts`). O desenho escreve hex; aqui
 * eles viram token para o white-label do tenant alcançar a tela — é o que o
 * teste `tokens/semHexSolto.test.ts` trava.
 *
 * O ciano aparece em três lugares e só: botão primário, aba ativa e foco.
 * Nenhum fundo de superfície é ciano.
 */
import { style, styleVariants } from '@vanilla-extract/css'

import { lk, OVERLINE_TRACKING } from '../tokens/lk.css'

// ── cabeçalho ────────────────────────────────────────────────────────────────

export const pagina = style({
  display: 'flex',
  flexDirection: 'column',
  gap: lk.espaco.x2,
  fontFamily: lk.fonte.ui,
  color: lk.cor.brancoSinal,
})

export const cabecalho = style({
  display: 'flex',
  alignItems: 'center',
  gap: lk.espaco.x2,
  flexWrap: 'wrap',
})

export const titulo = style({
  margin: 0,
  fontFamily: lk.fonte.titulo,
  fontWeight: 700,
  fontSize: '26px',
})

export const abas = style({
  display: 'flex',
  background: lk.cor.grafite,
  borderRadius: lk.raio.s,
  padding: '3px',
  gap: '2px',
})

const abaBase = style({
  height: '36px',
  padding: `0 18px`,
  border: 'none',
  borderRadius: '6px',
  fontFamily: lk.fonte.ui,
  fontSize: '14px',
  fontWeight: 600,
  cursor: 'pointer',
  background: 'transparent',
  color: lk.cor.cinzaNevoa,
  ':hover': { color: lk.cor.brancoSinal },
})

/** Ativa: fundo preto + rótulo ciano. O ciano marca ONDE ESTOU — nunca é fundo. */
export const aba = styleVariants({
  inativa: [abaBase],
  ativa: [abaBase, { background: lk.cor.preto, color: lk.cor.cianoVisao }],
})

export const espacador = style({ flex: 1 })

export const botaoPrimario = style({
  height: '38px',
  padding: `0 ${lk.espaco.x2}`,
  border: 'none',
  borderRadius: lk.raio.s,
  background: lk.cor.cianoVisao,
  color: lk.cor.preto,
  fontFamily: lk.fonte.ui,
  fontSize: '13.5px',
  fontWeight: 700,
  cursor: 'pointer',
  display: 'inline-flex',
  alignItems: 'center',
  gap: lk.espaco.x1,
  ':hover': { background: lk.cor.cianoProfundo },
  ':disabled': { opacity: 0.5, cursor: 'not-allowed' },
})

export const botaoSecundario = style({
  height: '32px',
  padding: '0 13px',
  background: 'transparent',
  border: `1px solid ${lk.cor.borda}`,
  borderRadius: '7px',
  color: lk.cor.brancoSinal,
  fontFamily: lk.fonte.ui,
  fontSize: '12.5px',
  cursor: 'pointer',
  display: 'inline-flex',
  alignItems: 'center',
  gap: '6px',
  ':hover': { borderColor: lk.cor.cianoVisao, color: lk.cor.cianoVisao },
  ':disabled': { opacity: 0.5, cursor: 'not-allowed' },
})

/** Ação destrutiva (Excluir câmera). Estilo PRÓPRIO, não `botaoSecundario`
 *  + cor: o hover do secundário pinta de ciano, e um botão que fica ciano ao
 *  passar o mouse lê como ação segura — o oposto do que este botão faz. */
export const botaoPerigo = style({
  height: '32px',
  padding: '0 13px',
  background: 'transparent',
  border: `1px solid ${lk.estado.nc}`,
  borderRadius: '7px',
  color: lk.estado.nc,
  fontFamily: lk.fonte.ui,
  fontSize: '12.5px',
  cursor: 'pointer',
  display: 'inline-flex',
  alignItems: 'center',
  gap: '6px',
  ':hover': { background: lk.estado.nc, color: lk.cor.brancoSinal },
  ':disabled': { opacity: 0.5, cursor: 'not-allowed' },
})

// ── aba Câmeras: lista + detalhe ─────────────────────────────────────────────

export const split = style({ display: 'flex', gap: '14px', alignItems: 'flex-start' })

export const lista = style({
  width: '330px',
  flex: 'none',
  display: 'flex',
  flexDirection: 'column',
  gap: '6px',
  // F5-LEVE (tema não pode estourar): sem isto a lista cresce com a
  // contagem de câmeras (RVB ~28) e empurra a página inteira — rola por si
  // só. O cálculo não desconta a altura do `cabecalho` (variável, quebra
  // linha em telas estreitas): sobra uma folga pequena e CONSTANTE, não
  // proporcional ao nº de câmeras — é essa proporcionalidade que era o bug.
  maxHeight: `calc(100vh - ${lk.medida.topbar} - ${lk.medida.padding} * 2 - var(--global-banner-offset, 0px))`,
  overflowY: 'auto',
})

const itemBase = style({
  display: 'flex',
  alignItems: 'center',
  gap: '12px',
  padding: '13px 14px',
  background: lk.cor.grafite,
  border: `1px solid ${lk.cor.borda}`,
  borderRadius: '9px',
  cursor: 'pointer',
  textAlign: 'left',
  width: '100%',
  ':focus-visible': { outline: `2px solid ${lk.cor.cianoVisao}`, outlineOffset: '2px' },
})

export const item = styleVariants({
  inativo: [itemBase],
  ativo: [itemBase, { borderColor: lk.cor.cianoVisao }],
})

export const itemTextos = style({
  display: 'flex',
  flexDirection: 'column',
  gap: '1px',
  minWidth: 0,
})

export const itemNome = style({
  fontFamily: lk.fonte.mono,
  fontSize: '13px',
  fontWeight: 700,
  color: lk.cor.brancoSinal,
})

export const itemArea = style({ fontSize: '12px', color: lk.cor.cinzaNevoa })

export const itemEstado = style({
  marginLeft: 'auto',
  display: 'inline-flex',
  alignItems: 'center',
  gap: '5px',
  fontFamily: lk.fonte.mono,
  fontSize: '10px',
  whiteSpace: 'nowrap',
})

export const cartao = style({
  flex: 1,
  minWidth: 0,
  display: 'flex',
  flexDirection: 'column',
  gap: '14px',
  background: lk.cor.grafite,
  border: `1px solid ${lk.cor.borda}`,
  borderRadius: lk.raio.g,
  padding: '18px',
  boxSizing: 'border-box',
})

export const cartaoTopo = style({ display: 'flex', alignItems: 'center', gap: '12px', flexWrap: 'wrap' })

export const cartaoNome = style({ fontFamily: lk.fonte.titulo, fontWeight: 700, fontSize: '19px' })

export const estadoLinha = style({
  display: 'inline-flex',
  alignItems: 'center',
  gap: '6px',
  fontFamily: lk.fonte.mono,
  fontSize: '10.5px',
})

/** Estado = cor + ícone + palavra. A cor nunca vem sozinha. */
export const tom = styleVariants({
  ok: { color: lk.estado.ok },
  atencao: { color: lk.estado.atencao },
  nc: { color: lk.estado.nc },
  neutro: { color: lk.cor.cinzaNevoa },
})

export const corpoDetalhe = style({ display: 'flex', gap: '14px', flexWrap: 'wrap' })

export const previa = style({
  width: '300px',
  flex: 'none',
  position: 'relative',
  aspectRatio: '16 / 9',
  borderRadius: lk.raio.s,
  overflow: 'hidden',
  border: `1px solid ${lk.cor.borda}`,
  background: lk.cor.preto,
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
})

export const previaImagem = style({ width: '100%', height: '100%', objectFit: 'cover' })

export const campos = style({
  flex: 1,
  minWidth: '260px',
  display: 'grid',
  gridTemplateColumns: 'auto 1fr',
  gap: '7px 16px',
  fontSize: '12.5px',
  alignContent: 'start',
})

export const rotulo = style({ color: lk.cor.cinzaNevoa })

export const valorMono = style({ fontFamily: lk.fonte.mono, fontSize: '12px' })

export const acoes = style({ display: 'flex', gap: lk.espaco.x1, flexWrap: 'wrap' })

// ── painel de teste de conexão ───────────────────────────────────────────────

export const painelTeste = style({
  display: 'flex',
  flexDirection: 'column',
  gap: lk.espaco.x1,
  padding: '14px',
  background: lk.cor.preto,
  border: `1px solid ${lk.cor.borda}`,
  borderRadius: lk.raio.m,
})

export const overline = style({
  fontFamily: lk.fonte.mono,
  fontSize: '10px',
  letterSpacing: OVERLINE_TRACKING,
  textTransform: 'uppercase',
  color: lk.cor.cinzaNevoa,
})

export const passo = style({ display: 'flex', alignItems: 'center', gap: '10px' })

export const passoMarca = style({
  width: '20px',
  height: '20px',
  flex: 'none',
  borderRadius: '50%',
  border: `1px solid currentColor`,
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  fontSize: '11px',
  fontWeight: 700,
})

export const passoTexto = style({ fontSize: '13px' })

export const resultado = style({
  display: 'flex',
  alignItems: 'center',
  gap: '9px',
  flexWrap: 'wrap',
  padding: '10px 12px',
  borderRadius: lk.raio.s,
  border: `1px solid currentColor`,
  fontSize: '13px',
  fontWeight: 700,
})

export const resultadoDica = style({
  marginLeft: 'auto',
  fontSize: '12px',
  fontWeight: 400,
  color: lk.cor.cinzaNevoa,
})

// ── aba Sites ────────────────────────────────────────────────────────────────

export const cartaoSite = style({
  display: 'flex',
  flexDirection: 'column',
  gap: '14px',
  background: lk.cor.grafite,
  border: `1px solid ${lk.cor.borda}`,
  borderRadius: lk.raio.g,
  padding: '20px',
  maxWidth: '760px',
})

export const bloco = style({ display: 'flex', flexDirection: 'column', gap: lk.espaco.x1 })

export const textoAuxiliar = style({ fontSize: '12px', color: lk.cor.cinzaNevoa })

export const seletorModo = style({
  display: 'flex',
  background: lk.cor.preto,
  borderRadius: lk.raio.s,
  padding: '3px',
  gap: '2px',
  maxWidth: '380px',
})

const modoBase = style({
  flex: 1,
  height: '36px',
  border: 'none',
  borderRadius: '6px',
  background: 'transparent',
  color: lk.cor.cinzaNevoa,
  fontFamily: lk.fonte.ui,
  fontSize: '13px',
  fontWeight: 600,
  cursor: 'pointer',
  ':disabled': { opacity: 0.5, cursor: 'not-allowed' },
})

export const modo = styleVariants({
  inativo: [modoBase],
  ativo: [modoBase, { background: lk.cor.grafite, color: lk.cor.cianoVisao }],
})

export const metricas = style({
  display: 'grid',
  gridTemplateColumns: 'repeat(4, 1fr)',
  gap: '10px',
})

export const metrica = style({
  display: 'flex',
  flexDirection: 'column',
  gap: '4px',
  padding: '12px 14px',
  background: lk.cor.preto,
  border: `1px solid ${lk.cor.borda}`,
  borderRadius: lk.raio.s,
})

export const metricaChave = style({
  fontFamily: lk.fonte.mono,
  fontSize: '9.5px',
  letterSpacing: '0.14em',
  textTransform: 'uppercase',
  color: lk.cor.cinzaNevoa,
})

export const metricaValor = style({
  fontFamily: lk.fonte.mono,
  fontWeight: 700,
  fontSize: '20px',
})

export const linhaSync = style({
  display: 'flex',
  alignItems: 'center',
  gap: '12px',
  flexWrap: 'wrap',
  padding: '12px 14px',
  background: lk.cor.preto,
  border: `1px solid ${lk.cor.borda}`,
  borderRadius: lk.raio.s,
  fontSize: '13px',
})

// ── aba Saúde ────────────────────────────────────────────────────────────────

export const tabela = style({
  width: '100%',
  maxWidth: '900px',
  borderCollapse: 'collapse',
  background: lk.cor.grafite,
  border: `1px solid ${lk.cor.borda}`,
  borderRadius: lk.raio.m,
  overflow: 'hidden',
})

export const th = style({
  padding: '10px 16px',
  fontFamily: lk.fonte.mono,
  fontWeight: 400,
  fontSize: '10px',
  letterSpacing: '0.14em',
  textTransform: 'uppercase',
  color: lk.cor.cinzaNevoa,
  borderBottom: `1px solid ${lk.cor.borda}`,
  textAlign: 'left',
})

export const thNum = style([th, { textAlign: 'right' }])

export const td = style({
  padding: '13px 16px',
  fontFamily: lk.fonte.mono,
  fontSize: '13px',
  borderBottom: `1px solid ${lk.cor.borda}`,
})

export const tdNome = style([td, { fontWeight: 700 }])

export const tdNum = style([td, { textAlign: 'right' }])

export const celulaEstado = style({
  display: 'inline-flex',
  alignItems: 'center',
  gap: '7px',
  fontFamily: lk.fonte.ui,
  fontSize: '12.5px',
  fontWeight: 600,
})

// ── aba Escopo (delta #8) ────────────────────────────────────────────────────

export const seletor = style({
  background: lk.cor.preto,
  border: `1px solid ${lk.cor.borda}`,
  borderRadius: '6px',
  color: lk.cor.brancoSinal,
  padding: '6px 10px',
  fontFamily: lk.fonte.ui,
  fontSize: '13px',
  minWidth: '220px',
  ':disabled': { opacity: 0.6 },
})

// `classes`/`classe` (checkbox nativo de classe) foram removidos daqui —
// consolidados em `ClasseChips`/`ChipDeClasse`
// (`components/training/CameraModelScope.tsx`), a mesma UI usada por lá.

export const nota = style({
  padding: '12px 14px',
  background: lk.cor.preto,
  border: `1px solid ${lk.cor.borda}`,
  borderRadius: lk.raio.s,
  fontSize: '12.5px',
  lineHeight: 1.55,
  color: lk.cor.cinzaNevoa,
  maxWidth: '900px',
})

// ── aba Desempenho (5ª aba — Main.dc.html) ──────────────────────────────────

export const desempenhoCabecalho = style({
  display: 'flex',
  alignItems: 'center',
  gap: lk.espaco.x1,
  flexWrap: 'wrap',
})

export const desempenhoGrid = style({
  display: 'grid',
  gridTemplateColumns: 'repeat(2, minmax(0, 1fr))',
  gap: lk.espaco.x2,
})

export const painelDesempenho = style({
  display: 'flex',
  flexDirection: 'column',
  gap: '18px',
  background: lk.cor.grafite,
  border: `1px solid ${lk.cor.borda}`,
  borderRadius: lk.raio.g,
  padding: '20px',
  boxSizing: 'border-box',
})

export const divisor = style({ height: '1px', background: lk.cor.borda })

export const linhaOpcoes = style({ display: 'flex', gap: lk.espaco.x1 })

const opcaoFpsBase = style({
  flex: 1,
  height: '44px',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  borderRadius: lk.raio.s,
  border: `1px solid ${lk.cor.borda}`,
  background: 'transparent',
  color: lk.cor.cinzaNevoa,
  fontFamily: lk.fonte.mono,
  fontSize: '14px',
  fontWeight: 700,
  cursor: 'pointer',
  ':disabled': { opacity: 0.5, cursor: 'not-allowed' },
})

/** FPS: número em mono, ativo com borda+texto ciano — mesma regra "ciano só
 * interativo" das outras abas. */
export const opcaoFps = styleVariants({
  inativa: [opcaoFpsBase],
  ativa: [opcaoFpsBase, { borderColor: lk.cor.cianoVisao, background: lk.cor.preto, color: lk.cor.cianoVisao }],
})

const opcaoQualidadeBase = style({
  flex: 1,
  height: '44px',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  borderRadius: lk.raio.s,
  border: `1px solid ${lk.cor.borda}`,
  background: 'transparent',
  color: lk.cor.cinzaNevoa,
  fontFamily: lk.fonte.ui,
  fontSize: '14px',
  fontWeight: 600,
  cursor: 'pointer',
  ':disabled': { opacity: 0.5, cursor: 'not-allowed' },
})

export const opcaoQualidade = styleVariants({
  inativa: [opcaoQualidadeBase],
  ativa: [
    opcaoQualidadeBase,
    { borderColor: lk.cor.cianoVisao, background: lk.cor.preto, color: lk.cor.cianoVisao },
  ],
})

export const colunaOpcoes = style({ display: 'flex', flexDirection: 'column', gap: lk.espaco.x1 })

const opcaoColetaBase = style({
  display: 'flex',
  alignItems: 'center',
  gap: lk.espaco.x1,
  height: '48px',
  padding: '0 14px',
  borderRadius: lk.raio.s,
  border: `1px solid ${lk.cor.borda}`,
  background: 'transparent',
  color: lk.cor.cinzaNevoa,
  fontFamily: lk.fonte.ui,
  fontSize: '14px',
  cursor: 'pointer',
  textAlign: 'left',
  ':disabled': { opacity: 0.5, cursor: 'not-allowed' },
})

export const opcaoColeta = styleVariants({
  inativa: [opcaoColetaBase],
  ativa: [opcaoColetaBase, { borderColor: lk.cor.cianoVisao, background: lk.cor.preto, color: lk.cor.brancoSinal }],
})

const anelBase = style({
  width: '16px',
  height: '16px',
  flex: 'none',
  borderRadius: '50%',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
})

export const anel = styleVariants({
  vazio: [anelBase, { border: `2px solid ${lk.cor.borda}` }],
  marcado: [anelBase, { border: `2px solid ${lk.cor.cianoVisao}` }],
})

const pontoBase = style({ width: '8px', height: '8px', borderRadius: '50%' })

export const ponto = styleVariants({
  vazio: [pontoBase, { background: 'transparent' }],
  marcado: [pontoBase, { background: lk.cor.cianoVisao }],
})

/** Borda âmbar do PRÓPRIO box (nunca fundo tingido — regra do handoff). A
 * transparência vem de `color-mix` sobre o token, nunca de `rgba()` escrito à
 * mão (mesma receita de `EventoDetalhe.css.ts`/`AoVivo.css.ts`): só assim o
 * white-label do tenant continua alcançando a cor de estado. */
export const avisoAmbar = style({
  display: 'flex',
  gap: lk.espaco.x1,
  padding: '12px 14px',
  borderRadius: lk.raio.s,
  background: lk.cor.preto,
  border: `1px solid color-mix(in srgb, ${lk.estado.atencao} 40%, transparent)`,
})

export const avisoIcone = style({ flexShrink: 0 })

export const avisoTexto = style({ fontSize: '13px', color: lk.estado.atencao, lineHeight: 1.5 })

export const saudeTopo = style({ display: 'flex', alignItems: 'center', gap: lk.espaco.x1, flexWrap: 'wrap' })

const metricaCardBase = style({
  display: 'flex',
  flexDirection: 'column',
  gap: '5px',
  padding: '12px',
  borderRadius: lk.raio.s,
  background: lk.cor.preto,
  border: `1px solid ${lk.cor.borda}`,
})

export const metricaCard = styleVariants({
  neutro: [metricaCardBase],
  alerta: [metricaCardBase, { borderColor: lk.estado.atencao }],
})

export const metricaRotulo = style({
  fontFamily: lk.fonte.mono,
  fontSize: '9.5px',
  letterSpacing: '0.16em',
  textTransform: 'uppercase',
  color: lk.cor.cinzaNevoa,
})

const metricaValorBase = style({ fontFamily: lk.fonte.titulo, fontWeight: 700, fontSize: '20px' })

export const metricaValorTom = styleVariants({
  neutro: [metricaValorBase, { color: lk.cor.brancoSinal }],
  alerta: [metricaValorBase, { color: lk.estado.atencao }],
})

export const impactoLinha = style({ display: 'flex', alignItems: 'baseline', gap: lk.espaco.x1 })

export const impactoValor = style({
  fontFamily: lk.fonte.titulo,
  fontWeight: 700,
  fontSize: '34px',
  color: lk.cor.brancoSinal,
})

/** Borda ciano CHEIA (não `color-mix`) — o desenho usa `#00E5FF` sólido aqui,
 * diferente da borda âmbar do aviso acima. */
export const impactoCaixa = style({
  display: 'flex',
  alignItems: 'center',
  gap: lk.espaco.x1,
  padding: '12px 14px',
  borderRadius: lk.raio.s,
  background: lk.cor.preto,
  border: `1px solid ${lk.cor.cianoVisao}`,
})

export const impactoCaixaTexto = style({ fontSize: '13.5px', color: lk.cor.brancoSinal })

export const impactoCaixaNumero = style({ fontFamily: lk.fonte.mono, color: lk.cor.cianoVisao })

export const barraSalvar = style({
  display: 'flex',
  alignItems: 'center',
  gap: '14px',
  paddingTop: '2px',
  flexWrap: 'wrap',
})

// ── vazio / erro ─────────────────────────────────────────────────────────────

export const centro = style({
  minHeight: '360px',
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  justifyContent: 'center',
  gap: '14px',
  textAlign: 'center',
  padding: lk.medida.padding,
  boxSizing: 'border-box',
})

export const centroTitulo = style({
  fontFamily: lk.fonte.titulo,
  fontWeight: 700,
  fontSize: '19px',
})

export const centroTexto = style({
  fontSize: '13.5px',
  color: lk.cor.cinzaNevoa,
  maxWidth: '380px',
  lineHeight: 1.55,
})

export const centroMono = style({
  fontFamily: lk.fonte.mono,
  fontSize: '12px',
  color: lk.cor.cinzaNevoa,
})
