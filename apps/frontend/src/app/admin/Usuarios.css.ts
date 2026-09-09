import { style } from '@vanilla-extract/css'

import { lk } from '../tokens/lk.css'

export const raiz = style({
  display: 'flex',
  flexDirection: 'column',
  gap: lk.espaco.x2,
  maxWidth: lk.medida.conteudoMax,
})

export const cabecalho = style({
  display: 'flex',
  alignItems: 'center',
  gap: lk.espaco.x1,
  flexWrap: 'wrap',
})

export const titulo = style({
  margin: 0,
  fontFamily: lk.fonte.titulo,
  fontWeight: 700,
  fontSize: '26px',
})

export const subtitulo = style({
  fontSize: '12.5px',
  color: lk.cor.cinzaNevoa,
})

export const spacer = style({ flex: 1 })

export const busca = style({
  height: '36px',
  minWidth: '220px',
  padding: '0 12px',
  background: lk.cor.grafite,
  border: `1px solid ${lk.cor.borda}`,
  borderRadius: lk.raio.s,
  color: lk.cor.brancoSinal,
  fontSize: '13px',
})

export const select = style({
  height: '36px',
  padding: '0 10px',
  background: lk.cor.grafite,
  border: `1px solid ${lk.cor.borda}`,
  borderRadius: lk.raio.s,
  color: lk.cor.brancoSinal,
  fontSize: '13px',
})

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
  selectors: { '&:disabled': { opacity: 0.6, cursor: 'default' } },
})

export const botaoSecundario = style({
  height: '32px',
  padding: '0 12px',
  background: 'transparent',
  border: `1px solid ${lk.cor.borda}`,
  borderRadius: lk.raio.s,
  color: lk.cor.brancoSinal,
  fontFamily: lk.fonte.ui,
  fontSize: '12px',
  fontWeight: 600,
  cursor: 'pointer',
  whiteSpace: 'nowrap',
  selectors: { '&:disabled': { opacity: 0.6, cursor: 'default' } },
})

export const tabelaWrap = style({
  background: lk.cor.grafite,
  border: `1px solid ${lk.cor.borda}`,
  borderRadius: lk.raio.m,
  overflow: 'auto',
})

export const tabela = style({
  width: '100%',
  borderCollapse: 'collapse',
})

export const th = style({
  textAlign: 'left',
  padding: '10px 16px',
  fontFamily: lk.fonte.mono,
  fontSize: '10px',
  letterSpacing: '.14em',
  textTransform: 'uppercase',
  color: lk.cor.cinzaNevoa,
  borderBottom: `1px solid ${lk.cor.borda}`,
  whiteSpace: 'nowrap',
})

export const td = style({
  padding: '12px 16px',
  fontSize: '13.5px',
  color: lk.cor.brancoSinal,
  borderBottom: `1px solid ${lk.cor.borda}`,
  verticalAlign: 'middle',
})

export const pessoa = style({
  display: 'flex',
  alignItems: 'center',
  gap: '10px',
})

export const avatar = style({
  width: '28px',
  height: '28px',
  borderRadius: '50%',
  background: lk.cor.preto,
  border: `1px solid ${lk.cor.bordaForte}`,
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  fontSize: '11px',
  fontWeight: 600,
  flexShrink: 0,
})

export const papelTenant = style({
  display: 'flex',
  flexDirection: 'column',
  gap: '1px',
})

export const papel = style({ fontSize: '12.5px' })

export const tenantNome = style({
  fontFamily: lk.fonte.mono,
  fontSize: '10.5px',
  color: lk.cor.cinzaNevoa,
  // O nome do tenant era o que estourava a largura ("RVB ISOLANTES PARA
  // TRANSFORMADORES" quebrava em 3 linhas em toda linha da tabela e empurrava
  // a coluna de ações para fora). Uma linha só, com reticências; o nome
  // inteiro fica no `title` do elemento.
  maxWidth: '200px',
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  whiteSpace: 'nowrap',
})

export const mono = style({ fontFamily: lk.fonte.mono, fontSize: '12px', color: lk.cor.cinzaNevoa })

export const linkPermissoes = style([
  mono,
  {
    background: 'none',
    border: 'none',
    padding: 0,
    cursor: 'pointer',
    selectors: { '&:hover': { color: lk.cor.cianoVisao } },
  },
])

export const acoes = style({
  display: 'flex',
  gap: '8px',
  justifyContent: 'flex-end',
  alignItems: 'center',
  whiteSpace: 'nowrap',
})

export const dot = style({
  width: '8px',
  height: '8px',
  borderRadius: '50%',
  display: 'inline-block',
})

export const dotOk = style({ background: lk.estado.ok })
export const dotNc = style({ background: lk.estado.nc })

export const paginacao = style({
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  gap: lk.espaco.x1,
})

// ── modais (convidar / senha temporária) ──────────────────────────────────

export const overlay = style({
  position: 'fixed',
  inset: 0,
  background: 'rgba(10,10,15,.7)',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  zIndex: 1000,
})

export const modal = style({
  width: '440px',
  maxHeight: '90vh',
  overflowY: 'auto',
  display: 'flex',
  flexDirection: 'column',
  gap: lk.espaco.x1,
  padding: lk.espaco.x2,
  background: lk.cor.grafite,
  border: `1px solid ${lk.cor.borda}`,
  borderRadius: lk.raio.g,
})

export const modalTitulo = style({
  fontFamily: lk.fonte.titulo,
  fontWeight: 700,
  fontSize: '18px',
})

export const campoLabel = style({
  fontSize: '12.5px',
  color: lk.cor.cinzaNevoa,
})

export const campo = style({
  width: '100%',
  boxSizing: 'border-box',
  height: '36px',
  padding: '0 12px',
  background: lk.cor.preto,
  border: `1px solid ${lk.cor.borda}`,
  borderRadius: lk.raio.s,
  color: lk.cor.brancoSinal,
  fontSize: '13px',
})

export const aviso = style({
  fontSize: '12px',
  color: lk.estado.atencao,
  lineHeight: 1.5,
})

export const credenciais = style({
  display: 'flex',
  flexDirection: 'column',
  gap: '8px',
  padding: lk.espaco.x1,
  background: lk.cor.preto,
  border: `1px solid ${lk.cor.borda}`,
  borderRadius: lk.raio.s,
})

export const credenciaisLinha = style({
  display: 'flex',
  alignItems: 'center',
  gap: '8px',
})

export const credenciaisCodigo = style({
  flex: 1,
  fontFamily: lk.fonte.mono,
  fontSize: '13px',
  color: lk.cor.brancoSinal,
  wordBreak: 'break-all',
})

export const erro = style({
  fontSize: '12.5px',
  color: lk.estado.nc,
  padding: '8px 10px',
  border: `1px solid ${lk.estado.nc}`,
  borderRadius: lk.raio.s,
})

export const acoesModal = style({
  display: 'flex',
  justifyContent: 'flex-end',
  gap: lk.espaco.x1,
  marginTop: lk.espaco.x1,
})

// ── estados de tela ──────────────────────────────────────────────────────

export const centro = style({
  minHeight: '50vh',
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  justifyContent: 'center',
  gap: '14px',
  textAlign: 'center',
  padding: lk.medida.padding,
})

export const centroTitulo = style({
  fontFamily: lk.fonte.titulo,
  fontWeight: 700,
  fontSize: '18px',
})

export const centroTecnico = style({
  fontFamily: lk.fonte.mono,
  fontSize: '12px',
  color: lk.cor.cinzaNevoa,
})

export const botaoRetry = style({
  height: '38px',
  padding: `0 ${lk.espaco.x2}`,
  border: 'none',
  borderRadius: lk.raio.s,
  background: lk.cor.cianoVisao,
  color: lk.cor.preto,
  fontSize: '13px',
  fontWeight: 700,
  cursor: 'pointer',
})

/** Nome clicável: mesma caixa do texto, para a linha não mudar de altura
 *  ao entrar em edição. Editar aqui é o único caminho de renomear alguém que
 *  já foi cadastrado — os 18 primeiros nasceram com o pedaço do e-mail. */
export const nomeBotao = style({
  background: 'none',
  border: 'none',
  padding: 0,
  margin: 0,
  color: 'inherit',
  font: 'inherit',
  textAlign: 'left',
  cursor: 'text',
  borderBottom: '1px dashed transparent',
  selectors: {
    '&:hover': { borderBottomColor: lk.cor.bordaForte },
    '&:focus-visible': { outline: `2px solid ${lk.cor.cianoVisao}`, outlineOffset: '2px' },
  },
})

export const nomeInput = style({
  background: lk.cor.preto,
  border: `1px solid ${lk.cor.cianoVisao}`,
  borderRadius: lk.raio.s,
  color: lk.cor.brancoSinal,
  font: 'inherit',
  padding: '2px 6px',
  width: '180px',
})
