/**
 * EPI Ao Vivo — estilo. Medidas e cores do `EPI Ao Vivo.dc.html`, todas por
 * token de `lk.css.ts`. ZERO hex solto (trava em `tokens/semHexSolto.test.ts`).
 *
 * SOBRE OS `color-mix`: o desenho pinta as tarjas sobre vídeo com
 * `rgba(10,10,15,.75)` e as bordas de foco com `rgba(0,229,255,.4)` — os MESMOS
 * dois tokens, com alfa. Escrever o rgba literal congelaria a cor do tenant
 * (o token é `var(--color-bg-base, …)`, e o white-label troca a var, não o
 * rgba). `color-mix` aplica o alfa EM CIMA do token, então a tarja continua
 * seguindo a marca do cliente.
 */
import { globalStyle, style, styleVariants } from '@vanilla-extract/css'

import { lk, OVERLINE_TRACKING, TELA_ESTREITA } from '../tokens/lk.css'

/** Preto do desenho a 75% — fundo das tarjas sobre o vídeo. */
const VEU = `color-mix(in srgb, ${lk.cor.preto} 75%, transparent)`
/** Ciano a 40% — borda de "isto está em foco". Ciano é só interativo. */
const CIANO_BORDA = `color-mix(in srgb, ${lk.cor.cianoVisao} 40%, transparent)`
export const pagina = style({
  display: 'flex',
  flexDirection: 'column',
  gap: '14px',
  fontFamily: lk.fonte.ui,
  color: lk.cor.brancoSinal,
})

// ── Barra de controles ──────────────────────────────────────────────────────

export const barra = style({
  display: 'flex',
  alignItems: 'center',
  gap: '12px',
  flexWrap: 'wrap',
})

export const titulo = style({
  margin: 0,
  fontFamily: lk.fonte.titulo,
  fontWeight: 700,
  fontSize: '26px',
})

/** "28 CÂMERAS · 26 ATIVAS" — dado, logo mono. */
export const resumo = style({
  fontFamily: lk.fonte.mono,
  fontSize: '11.5px',
  color: lk.cor.cinzaNevoa,
})

export const espacador = style({ flex: 1 })

export const grupoPresets = style({
  display: 'flex',
  background: lk.cor.grafite,
  borderRadius: lk.raio.s,
  padding: '3px',
  gap: '2px',
})

const presetBase = style({
  height: '32px',
  padding: '0 13px',
  border: 'none',
  borderRadius: '6px',
  fontFamily: lk.fonte.mono,
  fontSize: '12px',
  fontWeight: 600,
  cursor: 'pointer',
})

export const preset = styleVariants({
  inativo: [presetBase, { background: 'transparent', color: lk.cor.cinzaNevoa }],
  ativo: [presetBase, { background: lk.cor.preto, color: lk.cor.cianoVisao }],
})

const caixaControle = style({
  display: 'flex',
  alignItems: 'center',
  gap: '6px',
  height: '38px',
  padding: '0 10px',
  background: lk.cor.grafite,
  border: `1px solid ${lk.cor.borda}`,
  borderRadius: lk.raio.s,
})

export const colunas = styleVariants({
  inativo: [caixaControle],
  ativo: [caixaControle, { borderColor: CIANO_BORDA }],
})

export const rotuloColunas = style({
  fontFamily: lk.fonte.mono,
  fontSize: '10px',
  letterSpacing: '0.12em',
  color: lk.cor.cinzaNevoa,
})

export const passo = style({
  width: '26px',
  height: '26px',
  padding: 0,
  border: `1px solid ${lk.cor.borda}`,
  borderRadius: '6px',
  background: 'transparent',
  color: lk.cor.brancoSinal,
  fontSize: '14px',
  cursor: 'pointer',
  ':disabled': { opacity: 0.4, cursor: 'not-allowed' },
})

const valorBase = style({
  fontFamily: lk.fonte.mono,
  fontSize: '13px',
  fontWeight: 700,
  width: '14px',
  textAlign: 'center',
})

export const valorColunas = styleVariants({
  inativo: [valorBase, { color: lk.cor.brancoSinal }],
  ativo: [valorBase, { color: lk.cor.cianoVisao }],
})

const alternadorBase = style({
  height: '38px',
  display: 'flex',
  alignItems: 'center',
  gap: '9px',
  padding: '0 14px',
  background: lk.cor.grafite,
  border: `1px solid ${lk.cor.borda}`,
  borderRadius: lk.raio.s,
  color: lk.cor.brancoSinal,
  fontFamily: lk.fonte.ui,
  fontSize: '13px',
  cursor: 'pointer',
})

export const alternador = styleVariants({
  desligado: [alternadorBase],
  ligado: [alternadorBase, { borderColor: CIANO_BORDA }],
})

const trilhoBase = style({
  position: 'relative',
  flex: 'none',
  width: '30px',
  height: '16px',
  borderRadius: '8px',
})

export const trilho = styleVariants({
  desligado: [trilhoBase, { background: lk.cor.borda }],
  ligado: [trilhoBase, { background: lk.cor.cianoProfundo }],
})

const botaoTrilhoBase = style({
  position: 'absolute',
  top: '2px',
  width: '12px',
  height: '12px',
  borderRadius: '50%',
  background: lk.cor.brancoSinal,
  // Motion do manual: curto, seco, em steps — nunca deslize decorativo.
  transition: 'left .1s steps(2, end)',
})

export const botaoTrilho = styleVariants({
  desligado: [botaoTrilhoBase, { left: '2px' }],
  ligado: [botaoTrilhoBase, { left: '16px' }],
})

// ── Área: grade (ou destaque) + gaveta ──────────────────────────────────────

export const area = style({
  display: 'flex',
  gap: '16px',
  minHeight: 0,
  // Celular: parede em cima, gaveta embaixo. Lado a lado, a gaveta de 360px
  // sozinha já passa da tela.
  '@media': { [TELA_ESTREITA]: { flexDirection: 'column' } },
})

export const coluna = style({ flex: 1, minWidth: 0 })

export const grade = style({
  display: 'grid',
  gap: '10px',
  alignContent: 'start',
  /**
   * `!important` — e o motivo, por extenso, porque a exceção tem de se
   * justificar: o número de colunas é escolha do operador e chega como
   * `style={{ gridTemplateColumns: ... }}` INLINE (AoVivo.tsx), que ganha de
   * qualquer classe. Sem `!important` a media query não tem efeito nenhum.
   *
   * Por que sobrepor a escolha dele: em 375px, 2×2 dá ladrilhos de 160px e o
   * 4×3 dá 80px. Medido na captura desta rodada com 2 colunas — as tarjas
   * "CAM-01", "OFFLINE" e "SEM SINAL" se atravessam e o texto do player sai
   * cortado ("ANDO · CAM-01"). Uma parede que não se lê não é uma parede.
   * Uma coluna dá 343px por câmera, que é o vídeo inteiro; as outras estão a
   * um rolar de distância. O controle de colunas segue valendo no desktop.
   */
  '@media': { [TELA_ESTREITA]: { gridTemplateColumns: 'minmax(0, 1fr) !important' } },
})

// ── Ladrilho de câmera ──────────────────────────────────────────────────────

const ladrilhoBase = style({
  position: 'relative',
  aspectRatio: '16 / 9',
  borderRadius: lk.raio.m,
  overflow: 'hidden',
  border: `1px solid ${lk.cor.borda}`,
  background: lk.cor.preto,
  cursor: 'pointer',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  ':focus-visible': { outline: `2px solid ${lk.cor.cianoVisao}`, outlineOffset: '2px' },
})

export const ladrilho = styleVariants({
  normal: [ladrilhoBase],
  selecionado: [ladrilhoBase, { borderColor: CIANO_BORDA }],
})

export const foco = style([ladrilhoBase, {
  aspectRatio: 'auto',
  flex: 1,
  minHeight: '320px',
  borderRadius: lk.raio.g,
  borderColor: CIANO_BORDA,
  cursor: 'default',
}])

/**
 * Moldura do player. `CameraPlayer` (front atual) recebe width/height em px e
 * os aplica INLINE — num ladrilho responsivo isso estoura a caixa. O override
 * global abaixo é o menor caminho para o player preencher o ladrilho sem
 * editar um arquivo do front antigo, que segue de pé para as rotas antigas.
 */
export const moldura = style({ position: 'absolute', inset: 0 })

globalStyle(`${moldura} > div`, {
  width: '100% !important',
  height: '100% !important',
  borderRadius: '0 !important',
})

const tarja = style({
  position: 'absolute',
  zIndex: 3,
  borderRadius: '5px',
  padding: '3px 8px',
  background: VEU,
  fontFamily: lk.fonte.mono,
  fontSize: '10px',
  letterSpacing: '0.1em',
  whiteSpace: 'nowrap',
  pointerEvents: 'none',
})

export const tarjaNome = style([tarja, {
  top: '8px',
  left: '8px',
  maxWidth: '55%',
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  color: lk.cor.brancoSinal,
}])

const tarjaEstadoBase = style([tarja, {
  top: '8px',
  right: '8px',
  display: 'flex',
  alignItems: 'center',
  gap: '5px',
}])

/** Estado = cor + ícone + palavra. A cor sozinha nunca é o estado. */
export const tarjaEstado = styleVariants({
  online: [tarjaEstadoBase, { color: lk.estado.ok }],
  conectando: [tarjaEstadoBase, { color: lk.estado.atencao }],
  offline: [tarjaEstadoBase, { color: lk.estado.nc }],
})

export const botaoDestacar = style({
  position: 'absolute',
  bottom: '8px',
  right: '8px',
  zIndex: 4,
  width: '28px',
  height: '28px',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  padding: 0,
  background: VEU,
  border: `1px solid ${lk.cor.borda}`,
  borderRadius: '6px',
  color: lk.cor.cinzaNevoa,
  cursor: 'pointer',
  ':hover': { color: lk.cor.cianoVisao, borderColor: CIANO_BORDA },
})

// ── Overlay de detecção ─────────────────────────────────────────────────────

/**
 * ⛔ REGRA ABSOLUTA (CLAUDE.md): a camada de bounding box tem `pointerEvents:
 * none` e ZERO onClick. Caixa clicável rouba o clique do ladrilho e vira um
 * alvo que se move sozinho. Nunca remover.
 */
export const camadaCaixas = style({
  position: 'absolute',
  inset: 0,
  zIndex: 2,
  pointerEvents: 'none',
})

const caixaBase = style({
  position: 'absolute',
  borderStyle: 'solid',
  borderWidth: '2px',
  borderRadius: '3px',
})

export const caixa = styleVariants({
  ok: [caixaBase, { borderColor: lk.estado.ok }],
  nc: [caixaBase, { borderColor: lk.estado.nc }],
})

const rotuloCaixaBase = style({
  position: 'absolute',
  top: '-20px',
  left: '-2px',
  padding: '2px 6px',
  borderRadius: '3px',
  color: lk.cor.preto,
  fontFamily: lk.fonte.mono,
  fontSize: '9.5px',
  fontWeight: 700,
  whiteSpace: 'nowrap',
})

export const rotuloCaixa = styleVariants({
  ok: [rotuloCaixaBase, { background: lk.estado.ok }],
  nc: [rotuloCaixaBase, { background: lk.estado.nc }],
})

// ── Modo DESTAQUE ───────────────────────────────────────────────────────────

export const destaque = style({
  display: 'flex',
  gap: '10px',
  minHeight: 0,
  flex: 1,
  // Foco em cima, trilho de miniaturas embaixo — no celular não há largura
  // para as duas colunas do modo DESTAQUE.
  '@media': { [TELA_ESTREITA]: { flexDirection: 'column' } },
})

export const trilhoLateral = style({
  flex: 1,
  minWidth: '220px',
  maxWidth: '300px',
  display: 'flex',
  flexDirection: 'column',
  gap: '8px',
  overflow: 'auto',
  // Empilhado, o trilho é uma tira de miniaturas que rola DENTRO de si.
  '@media': {
    [TELA_ESTREITA]: {
      minWidth: 0,
      maxWidth: 'none',
      flex: 'none',
      flexDirection: 'row',
      overflowX: 'auto',
    },
  },
})

export const focoColuna = style({ flex: 2.4, minWidth: 0, display: 'flex' })

// ── Gaveta lateral ──────────────────────────────────────────────────────────

export const gaveta = style({
  width: '360px',
  flex: 'none',
  // 360px fixos estouram uma tela de 375 assim que o padding entra.
  '@media': { [TELA_ESTREITA]: { width: '100%' } },
  display: 'flex',
  flexDirection: 'column',
  gap: '14px',
  padding: '18px',
  boxSizing: 'border-box',
  background: lk.cor.grafite,
  border: `1px solid ${lk.cor.borda}`,
  borderRadius: lk.raio.g,
  overflow: 'auto',
})

export const gavetaTopo = style({ display: 'flex', alignItems: 'center', gap: '10px' })

export const gavetaNome = style({
  fontFamily: lk.fonte.titulo,
  fontWeight: 700,
  fontSize: '17px',
})

export const gavetaFechar = style({
  marginLeft: 'auto',
  width: '28px',
  height: '28px',
  background: 'transparent',
  border: `1px solid ${lk.cor.borda}`,
  borderRadius: '6px',
  color: lk.cor.cinzaNevoa,
  fontSize: '13px',
  cursor: 'pointer',
})

export const gavetaVideo = style({
  position: 'relative',
  aspectRatio: '16 / 9',
  borderRadius: lk.raio.s,
  overflow: 'hidden',
  background: lk.cor.preto,
  border: `1px solid ${lk.cor.borda}`,
})

export const gavetaDados = style({
  display: 'flex',
  gap: '14px',
  flexWrap: 'wrap',
  fontFamily: lk.fonte.mono,
  fontSize: '11px',
  color: lk.cor.cinzaNevoa,
})

export const overline = style({
  fontFamily: lk.fonte.mono,
  fontSize: '10px',
  letterSpacing: OVERLINE_TRACKING,
  textTransform: 'uppercase',
  color: lk.cor.cinzaNevoa,
})

export const blocoLista = style({ display: 'flex', flexDirection: 'column', gap: '8px' })

export const acaoSecundaria = style({
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  gap: '8px',
  height: '40px',
  border: `1px solid ${lk.cor.borda}`,
  borderRadius: lk.raio.s,
  background: 'transparent',
  color: lk.cor.brancoSinal,
  fontFamily: lk.fonte.ui,
  fontSize: '13px',
  fontWeight: 600,
  textDecoration: 'none',
  cursor: 'pointer',
  ':hover': { borderColor: lk.cor.cianoVisao, color: lk.cor.cianoVisao },
})

export const itemEvento = style({
  display: 'flex',
  alignItems: 'center',
  gap: '10px',
  padding: '10px 12px',
  background: lk.cor.preto,
  border: `1px solid ${lk.cor.borda}`,
  borderRadius: lk.raio.s,
  color: lk.cor.brancoSinal,
  textDecoration: 'none',
  ':hover': { borderColor: CIANO_BORDA },
})

export const pontoEvento = style({
  width: '8px',
  height: '8px',
  borderRadius: '50%',
  flex: 'none',
  background: lk.estado.nc,
})

export const eventoTitulo = style({ fontSize: '12.5px', fontWeight: 600 })

export const eventoHora = style({
  marginLeft: 'auto',
  fontFamily: lk.fonte.mono,
  fontSize: '11px',
  color: lk.cor.cinzaNevoa,
})

// ── Estados de tela inteira ─────────────────────────────────────────────────

export const centrado = style({
  flex: 1,
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  justifyContent: 'center',
  gap: '14px',
  textAlign: 'center',
  padding: lk.medida.padding,
  boxSizing: 'border-box',
  minHeight: '320px',
})

export const centradoTitulo = style({
  fontFamily: lk.fonte.titulo,
  fontWeight: 700,
  fontSize: '19px',
  color: lk.cor.brancoSinal,
})

export const centradoTexto = style({
  fontSize: '13.5px',
  color: lk.cor.cinzaNevoa,
  maxWidth: '380px',
  lineHeight: 1.55,
})

/** Detalhe técnico do erro — dado, logo mono. */
export const centradoDetalhe = style({
  fontFamily: lk.fonte.mono,
  fontSize: '12px',
  color: lk.cor.cinzaNevoa,
})

/** Único uso de ciano como fundo permitido: o botão primário. */
export const acaoPrimaria = style({
  display: 'inline-flex',
  alignItems: 'center',
  height: '40px',
  padding: `0 18px`,
  border: 'none',
  borderRadius: lk.raio.s,
  background: lk.cor.cianoVisao,
  color: lk.cor.preto,
  fontFamily: lk.fonte.ui,
  fontSize: '13.5px',
  fontWeight: 700,
  textDecoration: 'none',
  cursor: 'pointer',
  ':hover': { background: lk.cor.cianoProfundo },
})

export const ladrilhoVago = style([ladrilhoBase, {
  cursor: 'pointer',
  borderStyle: 'dashed',
  color: lk.cor.cinzaNevoa,
  flexDirection: 'column',
  gap: '8px',
  fontSize: '12px',
  textDecoration: 'none',
}])

// ── Seletor de site (parede por site) ───────────────────────────────────────

/** Select nu — a caixa vem de `colunas` (reaproveitada), só o controle interno. */
export const seletorSiteControle = style({
  border: 'none',
  background: 'transparent',
  color: lk.cor.brancoSinal,
  fontFamily: lk.fonte.ui,
  fontSize: '13px',
  cursor: 'pointer',
})

// ── Modo Montar ──────────────────────────────────────────────────────────────

const botaoMontarBase = style({
  height: '38px',
  display: 'flex',
  alignItems: 'center',
  gap: '8px',
  padding: '0 14px',
  border: `1px solid ${lk.cor.borda}`,
  borderRadius: lk.raio.s,
  background: 'transparent',
  fontFamily: lk.fonte.ui,
  fontSize: '13px',
  fontWeight: 600,
  cursor: 'pointer',
})

export const botaoMontar = styleVariants({
  inativo: [botaoMontarBase, { color: lk.cor.cianoVisao, borderColor: CIANO_BORDA }],
  ativo: [
    botaoMontarBase,
    { color: lk.cor.preto, background: lk.cor.cianoVisao, borderColor: lk.cor.cianoVisao },
  ],
})

// ── Meus layouts ─────────────────────────────────────────────────────────────

export const barraLayouts = style({
  display: 'flex',
  alignItems: 'center',
  flexWrap: 'wrap',
  gap: '8px',
  padding: '10px 14px',
  background: lk.cor.grafite,
  border: `1px solid ${lk.cor.borda}`,
  borderRadius: lk.raio.m,
})

export const rotuloLayouts = style({
  fontFamily: lk.fonte.mono,
  fontSize: '10.5px',
  letterSpacing: OVERLINE_TRACKING,
  color: lk.cor.cinzaNevoa,
})

const chipLayoutBase = style({
  display: 'flex',
  alignItems: 'center',
  gap: '8px',
  height: '32px',
  padding: '0 12px',
  border: 'none',
  borderRadius: '7px',
  fontSize: '13px',
  cursor: 'pointer',
})

export const chipLayout = styleVariants({
  inativo: [
    chipLayoutBase,
    { border: `1px solid ${lk.cor.borda}`, background: 'transparent', color: lk.cor.cinzaNevoa },
  ],
  ativo: [
    chipLayoutBase,
    { border: `1px solid ${lk.cor.cianoVisao}`, background: lk.cor.preto, color: lk.cor.cianoVisao },
  ],
})

/** Reset de botão dentro do chip — usado tanto no nome (aplica) quanto no X (remove). */
export const botaoChip = style({
  display: 'flex',
  alignItems: 'center',
  padding: 0,
  border: 'none',
  background: 'transparent',
  color: 'inherit',
  font: 'inherit',
  cursor: 'pointer',
})

export const chipSalvar = style({
  display: 'flex',
  alignItems: 'center',
  gap: '7px',
  height: '32px',
  padding: '0 12px',
  borderRadius: '7px',
  border: `1px dashed ${lk.cor.borda}`,
  background: 'transparent',
  color: lk.cor.cinzaNevoa,
  fontFamily: lk.fonte.ui,
  fontSize: '13px',
  cursor: 'pointer',
  ':disabled': { opacity: 0.4, cursor: 'not-allowed' },
})

export const contagemLayouts = style({
  marginLeft: 'auto',
  fontFamily: lk.fonte.mono,
  fontSize: '10.5px',
  color: lk.cor.cinzaNevoa,
})

export const linkGradeCompleta = style({
  alignSelf: 'flex-start',
  border: 'none',
  background: 'transparent',
  color: lk.cor.cianoVisao,
  fontFamily: lk.fonte.ui,
  fontSize: '12.5px',
  cursor: 'pointer',
  textDecoration: 'underline',
  padding: 0,
})

// ── Célula de montagem (slot arrastável) ────────────────────────────────────

/** Wrapper de célula ocupada: só posiciona — o Ladrilho dentro já tem o visual. */
export const celulaOcupada = style({ position: 'relative' })

/** Select nativo transparente sobre um botão/placeholder visual (⋯ ou vago). */
export const selectSobreposto = style({
  position: 'absolute',
  inset: 0,
  width: '100%',
  height: '100%',
  opacity: 0,
  cursor: 'pointer',
})

export const menuCelula = style({
  position: 'absolute',
  bottom: '8px',
  left: '8px',
  zIndex: 4,
  width: '28px',
  height: '28px',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  padding: 0,
  background: VEU,
  border: `1px solid ${lk.cor.borda}`,
  borderRadius: '6px',
  color: lk.cor.cinzaNevoa,
  cursor: 'pointer',
})

/** "Tirar do quadro" — mesmo tamanho e mesma fita do menu, ao lado dele.
 *  Botão PRÓPRIO e não uma opção escondida dentro do <select> de trocar:
 *  tirar a câmera do grid é uma das duas coisas que o operador vem fazer no
 *  modo Montar, e estava enterrada como item "— remover —" de um menu
 *  rotulado "Trocar câmera desta posição". */
export const botaoTirar = style({
  position: 'absolute',
  bottom: '8px',
  left: '42px',
  zIndex: 4,
  width: '28px',
  height: '28px',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  padding: 0,
  background: VEU,
  border: `1px solid ${lk.cor.borda}`,
  borderRadius: '6px',
  color: lk.cor.cinzaNevoa,
  cursor: 'pointer',
  ':hover': { borderColor: lk.estado.nc, color: lk.estado.nc },
})

/** Contagem de câmeras inativas do site — link, não enfeite: some da parede
 *  mas o operador precisa saber para onde foram. */
export const resumoInativas = style({
  fontFamily: lk.fonte.mono,
  fontSize: '11.5px',
  color: lk.cor.cinzaNevoa,
  textDecoration: 'none',
  borderBottom: `1px dotted ${lk.cor.borda}`,
  ':hover': { color: lk.cor.cianoVisao },
})

export const soltePraTrocar = style({
  fontFamily: lk.fonte.mono,
  fontSize: '10px',
  letterSpacing: '0.14em',
  color: lk.cor.cianoVisao,
})

export const soltePraTrocarSobre = style([soltePraTrocar, {
  position: 'absolute',
  left: '11px',
  bottom: '11px',
  zIndex: 5,
}])

export const rodapeMontagem = style({
  flex: 'none',
  fontSize: '12.5px',
  color: lk.cor.cinzaNevoa,
})

// ── Local e módulo no quadrinho (§13) — segunda linha, discreta, sob o nome ──

export const tarjaLocalModulo = style([tarja, {
  top: '30px',
  left: '8px',
  maxWidth: '70%',
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  color: lk.cor.cinzaNevoa,
}])

// ── Aviso de sinal caído (§11) ───────────────────────────────────────────────
// Cor sozinha nunca é o estado (regra do manual): borda âmbar no ladrilho +
// selo com a palavra, espelhando a tarja de nome/local no canto oposto.

export const ladrilhoSemSinal = style({ borderColor: lk.estado.atencao })

export const avisoSemSinal = style([tarja, {
  top: '30px',
  right: '8px',
  color: lk.estado.atencao,
  border: `1px solid ${lk.estado.atencao}`,
}])
