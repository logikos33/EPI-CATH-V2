import { style } from '@vanilla-extract/css'

import { lk, OVERLINE_TRACKING } from '../tokens/lk.css'

export const raiz = style({ display: 'flex', flexDirection: 'column', gap: lk.espaco.x2 })

export const cabecalho = style({
  display: 'flex',
  alignItems: 'flex-start',
  justifyContent: 'space-between',
  gap: lk.espaco.x2,
  flexWrap: 'wrap',
})

export const titulo = style({
  margin: 0,
  fontFamily: lk.fonte.titulo,
  fontWeight: 700,
  fontSize: '24px',
})

export const subtitulo = style({
  margin: '4px 0 0',
  fontSize: '13px',
  color: lk.cor.cinzaNevoa,
  maxWidth: '52ch',
})

export const abas = style({
  display: 'flex',
  alignItems: 'center',
  gap: lk.espaco.x1,
  borderBottom: `1px solid ${lk.cor.borda}`,
  paddingBottom: lk.espaco.x1,
})

export const aba = style({
  height: '32px',
  padding: `0 ${lk.espaco.x2}`,
  border: `1px solid ${lk.cor.borda}`,
  borderRadius: lk.raio.s,
  background: 'transparent',
  color: lk.cor.cinzaNevoa,
  fontSize: '13px',
  fontWeight: 600,
  cursor: 'pointer',
  ':hover': { color: lk.cor.brancoSinal },
})

export const abaAtiva = style({
  borderColor: lk.cor.cianoVisao,
  color: lk.cor.cianoVisao,
})

export const espacador = style({ flex: 1 })

export const contagem = style({
  fontFamily: lk.fonte.mono,
  fontSize: '11px',
  color: lk.cor.cinzaNevoa,
})

export const lista = style({
  listStyle: 'none',
  margin: 0,
  padding: 0,
  display: 'flex',
  flexDirection: 'column',
  border: `1px solid ${lk.cor.borda}`,
  borderRadius: lk.raio.m,
  overflow: 'hidden',
})

export const linha = style({
  display: 'flex',
  alignItems: 'center',
  gap: lk.espaco.x2,
  width: '100%',
  padding: `${lk.espaco.x2}`,
  border: 'none',
  borderBottom: `1px solid ${lk.cor.borda}`,
  background: 'transparent',
  color: lk.cor.brancoSinal,
  font: 'inherit',
  textAlign: 'left',
  cursor: 'pointer',
  ':hover': { background: lk.cor.grafite },
  selectors: {
    '&:focus-visible': { outline: `2px solid ${lk.cor.cianoVisao}`, outlineOffset: '-2px' },
  },
})

/** Não lida pesa mais: fundo levemente destacado + marcador. */
export const linhaNaoLida = style({ background: lk.cor.grafite })

export const marcador = style({
  width: '8px',
  height: '8px',
  borderRadius: '50%',
  flexShrink: 0,
  background: lk.cor.borda,
})

export const marcadorNaoLido = style({ background: lk.cor.cianoVisao })

export const corpo = style({ display: 'flex', flexDirection: 'column', gap: '2px', flex: 1, minWidth: 0 })

export const camera = style({
  fontSize: '14px',
  fontWeight: 600,
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  whiteSpace: 'nowrap',
})

export const classes = style({ fontSize: '13px', color: lk.cor.cinzaNevoa })

export const quando = style({
  fontFamily: lk.fonte.mono,
  fontSize: '11px',
  color: lk.cor.cinzaNevoa,
})

/** Estado = cor + palavra (o Manual proíbe cor sozinha como estado). */
export const selo = style({
  flexShrink: 0,
  fontFamily: lk.fonte.mono,
  fontSize: '10px',
  letterSpacing: OVERLINE_TRACKING,
  textTransform: 'uppercase',
  color: lk.cor.cinzaNevoa,
  border: `1px solid ${lk.cor.borda}`,
  borderRadius: lk.raio.s,
  padding: '3px 8px',
})

export const seloNova = style({ color: lk.cor.cianoVisao, borderColor: lk.cor.cianoVisao })

export const paginacao = style({
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  gap: lk.espaco.x2,
})

export const botaoPrimario = style({
  height: '38px',
  display: 'flex',
  alignItems: 'center',
  gap: '6px',
  padding: `0 ${lk.espaco.x2}`,
  border: 'none',
  borderRadius: lk.raio.s,
  background: lk.cor.cianoVisao,
  color: lk.cor.preto,
  fontSize: '13px',
  fontWeight: 700,
  cursor: 'pointer',
  ':disabled': { opacity: 0.45, cursor: 'not-allowed' },
})

export const botaoSecundario = style({
  height: '34px',
  padding: `0 ${lk.espaco.x2}`,
  border: `1px solid ${lk.cor.borda}`,
  borderRadius: lk.raio.s,
  background: 'transparent',
  color: lk.cor.brancoSinal,
  fontSize: '13px',
  fontWeight: 600,
  cursor: 'pointer',
  ':disabled': { opacity: 0.4, cursor: 'not-allowed' },
})

export const painelCentral = style({
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  gap: lk.espaco.x1,
  padding: lk.espaco.x5,
  border: `1px solid ${lk.cor.borda}`,
  borderRadius: lk.raio.m,
  color: lk.cor.cinzaNevoa,
  textAlign: 'center',
})

export const painelTitulo = style({ fontSize: '15px', fontWeight: 600, color: lk.cor.brancoSinal })

export const painelTexto = style({ fontSize: '13px', maxWidth: '46ch' })

/** Saída: nenhuma área termina em si mesma (regra C2). */
export const linkSaida = style({
  marginTop: lk.espaco.x1,
  fontSize: '13px',
  fontWeight: 600,
  color: lk.cor.cianoVisao,
  textDecoration: 'none',
})

export const linkInterno = style({ color: lk.cor.cianoVisao, textDecoration: 'none' })

export const aviso = style({ fontSize: '13px', color: lk.cor.cinzaNevoa })

export const rodape = style({
  margin: 0,
  fontSize: '12px',
  color: lk.cor.cinzaNevoa,
  maxWidth: '76ch',
  lineHeight: 1.6,
})
