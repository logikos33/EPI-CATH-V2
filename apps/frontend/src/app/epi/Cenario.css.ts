/**
 * Estilos de `Cenario.tsx`. Zero hex solto — cor, fonte e medida só de
 * `lk.css.ts` (o teste `tokens/semHexSolto.test.ts` varre `src/app/**`).
 *
 * Paleta da prancha → token real (mapeamento, não invenção):
 *   #00E5FF → lk.cor.cianoVisao   ·  #3ECF8E → lk.estado.ok
 *   #E8A13C → lk.estado.atencao   ·  #E5484D → lk.estado.nc
 *   #14141C → lk.cor.grafite      ·  #23242F → lk.cor.borda
 *   #3A3D4A → lk.cor.bordaForte   ·  #8A8F98 → lk.cor.cinzaNevoa
 *   #F4F6F8 → lk.cor.brancoSinal  ·  #0A0A0F → lk.cor.preto
 *
 * A prancha também usa cor por classe (capacete amarelo, colete laranja...).
 * Sem token para paleta arco-íris de classe, o chip usa SÓ o par
 * selecionado/não-selecionado do resto da tela (ciano = interativo/marcado,
 * cinza = disponível) — ciano continua só interativo, e zero hex novo.
 */
import { style } from '@vanilla-extract/css'

import { OVERLINE_TRACKING, TELA_ESTREITA, lk } from '../tokens/lk.css'

export const pagina = style({ display: 'flex', flexDirection: 'column', gap: lk.espaco.x2 })

export const centro = style({
  minHeight: '60vh',
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  justifyContent: 'center',
  gap: '14px',
  textAlign: 'center',
  padding: lk.medida.padding,
})

export const centroTitulo = style({ fontFamily: lk.fonte.titulo, fontWeight: 700, fontSize: '19px' })
export const centroTexto = style({ fontSize: '13.5px', color: lk.cor.cinzaNevoa, maxWidth: '400px', lineHeight: 1.55 })
export const centroTecnico = style({ fontFamily: lk.fonte.mono, fontSize: '11.5px', color: lk.cor.cinzaNevoa })

// ── lista ────────────────────────────────────────────────────────────────────

export const cabecalhoLista = style({ display: 'flex', alignItems: 'center', gap: lk.espaco.x1, flexWrap: 'wrap' })
export const titulo = style({ margin: 0, fontFamily: lk.fonte.titulo, fontWeight: 700, fontSize: '20px' })
export const contador = style({ fontFamily: lk.fonte.mono, fontSize: '11px', color: lk.cor.cinzaNevoa })
export const espacador = style({ flex: 1 })

export const linkSecundario = style({
  height: '36px',
  display: 'flex',
  alignItems: 'center',
  gap: lk.espaco.x1,
  padding: `0 ${lk.espaco.x2}`,
  border: `1px solid ${lk.cor.borda}`,
  borderRadius: lk.raio.s,
  color: lk.cor.brancoSinal,
  fontSize: '12.5px',
  fontWeight: 600,
  textDecoration: 'none',
  ':hover': { borderColor: lk.cor.cianoVisao, color: lk.cor.cianoVisao },
})

export const botaoPrimario = style({
  height: '36px',
  padding: `0 ${lk.espaco.x2}`,
  border: 'none',
  borderRadius: lk.raio.s,
  background: lk.cor.cianoVisao,
  color: lk.cor.preto,
  fontFamily: lk.fonte.ui,
  fontSize: '13px',
  fontWeight: 700,
  cursor: 'pointer',
  ':disabled': { opacity: 0.45, cursor: 'not-allowed' },
})

export const explicacao = style({
  fontSize: '12.5px',
  color: lk.cor.cinzaNevoa,
  lineHeight: 1.55,
  maxWidth: '820px',
  marginTop: '-6px',
})

export const cartaoRegra = style({
  display: 'flex',
  flexDirection: 'column',
  background: lk.cor.grafite,
  border: `1px solid ${lk.cor.borda}`,
  borderRadius: lk.raio.g,
  overflow: 'hidden',
})

export const linhaRegra = style({ display: 'flex', alignItems: 'center', gap: '16px', padding: '14px 16px' })

export const thumb = style({
  width: '96px',
  height: '60px',
  flex: 'none',
  borderRadius: lk.raio.s,
  overflow: 'hidden',
  border: `1px solid ${lk.cor.borda}`,
  background: `linear-gradient(200deg, ${lk.cor.grafite}, ${lk.cor.preto})`,
  position: 'relative',
})

export const thumbSvg = style({ position: 'absolute', inset: 0, width: '100%', height: '100%' })

export const infoRegra = style({ display: 'flex', flexDirection: 'column', gap: '3px', minWidth: 0, flex: 1 })
export const infoTopo = style({ display: 'flex', alignItems: 'center', gap: '9px', flexWrap: 'wrap' })
export const nomeRegraTexto = style({ fontSize: '14.5px', fontWeight: 600 })

export const badgeTemplate = style({
  fontFamily: lk.fonte.mono,
  fontSize: '9px',
  letterSpacing: '.08em',
  color: lk.cor.cinzaNevoa,
  border: `1px solid ${lk.cor.borda}`,
  borderRadius: '4px',
  padding: '2px 7px',
})

export const fraseRegra = style({ fontSize: '12.5px', color: lk.cor.cinzaNevoa, lineHeight: 1.5 })

export const statusColuna = style({ display: 'flex', flexDirection: 'column', gap: '3px', alignItems: 'flex-end', flex: 'none' })
export const statusLinha = style({ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '12px', fontWeight: 700 })
export const bolinha = style({ width: '8px', height: '8px', borderRadius: '50%' })
export const ultimoAviso = style({ fontFamily: lk.fonte.mono, fontSize: '10.5px', color: lk.cor.cinzaNevoa })

export const acoesRegra = style({ display: 'flex', gap: '8px', flex: 'none' })

export const botaoAcao = style({
  height: '34px',
  padding: '0 13px',
  background: 'transparent',
  border: `1px solid ${lk.cor.borda}`,
  borderRadius: '7px',
  color: lk.cor.brancoSinal,
  fontFamily: lk.fonte.ui,
  fontSize: '12.5px',
  fontWeight: 600,
  cursor: 'pointer',
  ':hover': { borderColor: lk.cor.cianoVisao, color: lk.cor.cianoVisao },
  ':disabled': { opacity: 0.5, cursor: 'not-allowed' },
})

/** Selo de dependência: controle desenhado, sem endpoint (ainda). Borda
 * tracejada + `title` explica — nunca finge que o clique faz algo. */
export const botaoDependente = style({
  height: '34px',
  padding: '0 13px',
  position: 'relative',
  background: 'transparent',
  border: `1px dashed ${lk.cor.bordaForte}`,
  borderRadius: '7px',
  color: lk.cor.cinzaNevoa,
  fontFamily: lk.fonte.ui,
  fontSize: '12.5px',
  fontWeight: 600,
  cursor: 'not-allowed',
})

export const rodapeAvaliacao = style({
  display: 'flex',
  alignItems: 'center',
  gap: '10px',
  padding: '10px 16px',
  borderTop: `1px solid ${lk.cor.borda}`,
  flexWrap: 'wrap',
})

export const textoAvaliacao = style({ fontSize: '12px', color: lk.cor.cinzaNevoa })

export const seloAguarda = style({
  fontFamily: lk.fonte.mono,
  fontSize: '9px',
  letterSpacing: '.08em',
  color: lk.estado.atencao,
  border: `1px solid rgba(232,161,60,.4)`,
  borderRadius: '4px',
  padding: '2px 7px',
})

export const linkEventos = style({ fontSize: '12px', fontWeight: 600, color: lk.cor.cianoVisao, textDecoration: 'none' })

export const rodapeNota = style({
  display: 'flex',
  gap: lk.espaco.x2,
  padding: '14px 16px',
  background: lk.cor.grafite,
  border: `1px solid ${lk.cor.borda}`,
  borderRadius: lk.raio.m,
  fontSize: '12.5px',
  color: lk.cor.cinzaNevoa,
  lineHeight: 1.6,
  flexWrap: 'wrap',
})

// ── editor ───────────────────────────────────────────────────────────────────

export const editorTopo = style({ display: 'flex', alignItems: 'center', gap: lk.espaco.x1, flexWrap: 'wrap' })

export const voltar = style({
  height: '34px',
  padding: '0 13px',
  background: 'transparent',
  border: `1px solid ${lk.cor.borda}`,
  borderRadius: '7px',
  color: lk.cor.brancoSinal,
  fontFamily: lk.fonte.ui,
  fontSize: '12.5px',
  fontWeight: 600,
  cursor: 'pointer',
})

export const tituloEditor = style({ margin: 0, fontFamily: lk.fonte.titulo, fontWeight: 700, fontSize: '19px' })

export const stepsNav = style({ display: 'flex', gap: '9px', flexWrap: 'wrap' })

const stepBase = {
  height: '34px',
  display: 'flex',
  alignItems: 'center',
  gap: '9px',
  padding: '0 13px',
  borderRadius: lk.raio.s,
  fontFamily: lk.fonte.ui,
  fontSize: '12.5px',
  fontWeight: 600,
  cursor: 'pointer',
  background: 'transparent',
} as const

export const stepBotao = {
  ativo: style({ ...stepBase, border: `1px solid rgba(0,229,255,.4)`, background: 'rgba(0,229,255,.07)', color: lk.cor.cianoVisao }),
  inativo: style({ ...stepBase, border: `1px solid ${lk.cor.borda}`, color: lk.cor.cinzaNevoa }),
}

export const stepNumero = {
  ativo: style({
    width: '19px', height: '19px', borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center',
    fontFamily: lk.fonte.mono, fontSize: '10.5px', fontWeight: 700, background: lk.cor.cianoVisao, color: lk.cor.preto,
  }),
  inativo: style({
    width: '19px', height: '19px', borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center',
    fontFamily: lk.fonte.mono, fontSize: '10.5px', fontWeight: 700, background: lk.cor.borda, color: lk.cor.cinzaNevoa,
  }),
}

// ── passo 1: templates ──────────────────────────────────────────────────────

export const introTemplate = style({ fontSize: '13.5px', color: lk.cor.cinzaNevoa, lineHeight: 1.6, maxWidth: '760px' })

export const gradeTemplates = style({
  display: 'grid',
  gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))',
  gap: '12px',
})

export const templateCard = style({
  display: 'flex',
  flexDirection: 'column',
  gap: '11px',
  padding: '18px',
  background: lk.cor.grafite,
  border: `1px solid ${lk.cor.borda}`,
  borderRadius: '14px',
  cursor: 'pointer',
  textAlign: 'left',
  ':hover': { borderColor: 'rgba(0,229,255,.5)' },
})

export const templateCardSelecionado = style({ borderColor: 'rgba(0,229,255,.5)' })

export const templateTopo = style({ display: 'flex', alignItems: 'center', gap: '10px' })
export const templateNome = style({ fontFamily: lk.fonte.titulo, fontWeight: 700, fontSize: '16px' })
export const templateDesc = style({ fontSize: '12.5px', color: lk.cor.cinzaNevoa, lineHeight: 1.55 })
export const templateExemplo = style({
  fontFamily: lk.fonte.mono,
  fontSize: '10px',
  color: lk.cor.cinzaNevoa,
  borderTop: `1px solid ${lk.cor.borda}`,
  paddingTop: '9px',
})

export const linkAvancado = style({ fontSize: '12px', color: lk.cor.cinzaNevoa, lineHeight: 1.5 })

// ── passo 2/3: editor de verdade ────────────────────────────────────────────

export const corpoEditor = style({ display: 'flex', gap: lk.espaco.x2, alignItems: 'flex-start', flexWrap: 'wrap' })

export const colunaCena = style({ flex: 1, minWidth: '440px', display: 'flex', flexDirection: 'column', gap: '10px' })

export const avisoMobile = style({
  display: 'none',
  '@media': {
    [TELA_ESTREITA]: {
      display: 'flex',
      alignItems: 'center',
      gap: '8px',
      padding: '9px 12px',
      background: lk.cor.grafite,
      border: `1px solid ${lk.cor.borda}`,
      borderRadius: lk.raio.s,
      fontFamily: lk.fonte.mono,
      fontSize: '10.5px',
      color: lk.cor.cinzaNevoa,
    },
  },
})

export const cenaBox = style({
  position: 'relative',
  aspectRatio: '16 / 9',
  borderRadius: lk.raio.g,
  overflow: 'hidden',
  border: `1px solid ${lk.cor.borda}`,
  background: `linear-gradient(200deg, ${lk.cor.grafite} 0%, ${lk.cor.preto} 100%)`,
})

export const cenaImagem = style({ position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'cover', zIndex: 1 })

export const cenaBoxDesenhando = style({ cursor: 'crosshair' })

export const cenaTag = style({
  position: 'absolute',
  top: '10px',
  zIndex: 6,
  background: 'rgba(10,10,15,.78)',
  borderRadius: '5px',
  padding: '4px 9px',
  fontFamily: lk.fonte.mono,
  fontSize: '10px',
  letterSpacing: '.1em',
})

export const cenaTagEsquerda = style([cenaTag, { left: '10px' }])
export const cenaTagDireita = style([cenaTag, {
  right: '10px',
  color: lk.cor.cinzaNevoa,
  maxWidth: '44%',
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  whiteSpace: 'nowrap',
}])

export const cenaSvg = style({ position: 'absolute', inset: 0, width: '100%', height: '100%', zIndex: 2 })

export const zonaSalva = style({ fill: 'rgba(244,246,248,.04)', stroke: 'rgba(244,246,248,.4)', strokeWidth: 0.7, strokeDasharray: '2 2' })
export const zonaSalvaLabel = style({
  position: 'absolute',
  zIndex: 3,
  fontFamily: lk.fonte.mono,
  fontSize: '9px',
  fontWeight: 700,
  color: lk.cor.brancoSinal,
  background: 'rgba(10,10,15,.8)',
  borderRadius: '3px',
  padding: '2px 7px',
  whiteSpace: 'nowrap',
  transform: 'translateY(-140%)',
})

export const formaArea = style({ fill: 'rgba(0,229,255,.09)', stroke: lk.cor.cianoVisao, strokeWidth: 1.2 })
export const formaLinha = style({ fill: 'none', stroke: lk.cor.cianoVisao, strokeWidth: 1.2 })

export const handle = style({
  position: 'absolute',
  width: '14px',
  height: '14px',
  margin: '-7px 0 0 -7px',
  background: lk.cor.cianoVisao,
  border: `2px solid ${lk.cor.preto}`,
  borderRadius: '3px',
  zIndex: 5,
  cursor: 'grab',
  touchAction: 'none',
  padding: 0,
})

export const rotuloFlutuante = style({
  position: 'absolute',
  zIndex: 5,
  fontFamily: lk.fonte.mono,
  fontSize: '10px',
  fontWeight: 700,
  color: lk.cor.preto,
  background: lk.cor.cianoVisao,
  borderRadius: '3px',
  padding: '2px 8px',
  whiteSpace: 'nowrap',
  transform: 'translateY(calc(-100% - 6px))',
  pointerEvents: 'none',
})

export const bannerSemSinal = style({
  position: 'absolute',
  inset: 0,
  zIndex: 4,
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  justifyContent: 'center',
  gap: '8px',
  textAlign: 'center',
  padding: '18px',
  background: 'rgba(10,10,15,.55)',
})

export const bannerSemSinalTexto = style({ fontSize: '12.5px', color: lk.cor.brancoSinal, maxWidth: '280px', lineHeight: 1.5 })

export const toolbarCena = style({ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' })

const botaoToolbarBase = {
  height: '36px',
  display: 'flex',
  alignItems: 'center',
  gap: '8px',
  padding: '0 13px',
  borderRadius: lk.raio.s,
  fontFamily: lk.fonte.ui,
  fontSize: '12.5px',
  fontWeight: 600,
  cursor: 'pointer',
} as const

export const botaoToolbar = {
  ativo: style({ ...botaoToolbarBase, background: 'rgba(0,229,255,.08)', border: '1px solid rgba(0,229,255,.45)', color: lk.cor.cianoVisao }),
  inativo: style({ ...botaoToolbarBase, background: 'transparent', border: `1px solid ${lk.cor.borda}`, color: lk.cor.brancoSinal }),
}

export const contadorPontos = style({ fontFamily: lk.fonte.mono, fontSize: '10.5px', color: lk.cor.cinzaNevoa })
export const ajudaDesenho = style({ fontSize: '12px', color: lk.cor.cinzaNevoa, lineHeight: 1.5 })

// ── painel lateral (430px) ──────────────────────────────────────────────────

export const painelLateral = style({ width: '430px', maxWidth: '100%', flex: 'none', display: 'flex', flexDirection: 'column', gap: lk.espaco.x1 })

export const blocoPasso = style({
  display: 'flex',
  flexDirection: 'column',
  gap: '11px',
  padding: lk.espaco.x1,
  background: lk.cor.grafite,
  border: `1px solid ${lk.cor.borda}`,
  borderRadius: lk.raio.g,
})

export const overlinePasso = style({ fontFamily: lk.fonte.mono, fontSize: '10px', letterSpacing: OVERLINE_TRACKING, color: lk.cor.cinzaNevoa })
export const overlineLinha = style({ display: 'flex', alignItems: 'center', gap: '8px' })
export const dicaInline = style({ marginLeft: 'auto', fontSize: '11.5px', color: lk.cor.cinzaNevoa })

export const inputNome = style({
  height: '40px',
  background: lk.cor.preto,
  border: `1px solid ${lk.cor.borda}`,
  borderRadius: lk.raio.s,
  color: lk.cor.brancoSinal,
  fontFamily: lk.fonte.ui,
  fontSize: '13.5px',
  fontWeight: 600,
  padding: '0 11px',
  outline: 'none',
  ':focus': { borderColor: lk.cor.cianoVisao },
})

export const textoAjuda = style({ fontSize: '12px', color: lk.cor.cinzaNevoa, lineHeight: 1.5 })

export const linhaClasses = style({ display: 'flex', flexWrap: 'wrap', gap: '7px' })

const chipBase = {
  height: '34px',
  display: 'flex',
  alignItems: 'center',
  gap: '8px',
  padding: '0 12px',
  borderRadius: '17px',
  fontFamily: lk.fonte.ui,
  fontSize: '12.5px',
  fontWeight: 600,
  cursor: 'pointer',
} as const

export const chipClasse = {
  ativo: style({ ...chipBase, border: `1.5px solid ${lk.cor.cianoVisao}`, background: 'rgba(244,246,248,.05)', color: lk.cor.brancoSinal }),
  inativo: style({ ...chipBase, border: `1.5px solid ${lk.cor.bordaForte}`, background: 'transparent', color: lk.cor.cinzaNevoa }),
  /** Classe do catálogo que a regra não aceita ou que o modelo desta câmera
   * não reconhece. Cor NÃO é o estado (lk.css.ts): quem não distingue âmbar
   * ainda lê o selo dentro do chip e o motivo logo abaixo da lista. */
  restrito: style({
    ...chipBase,
    border: `1.5px dashed ${lk.estado.atencao}`,
    background: 'rgba(232,161,60,.06)',
    color: lk.estado.atencao,
    cursor: 'default',
  }),
}

export const seloChip = style({
  fontSize: '10px',
  fontWeight: 700,
  letterSpacing: '.06em',
  textTransform: 'uppercase',
  padding: '2px 6px',
  borderRadius: '9px',
  background: 'rgba(232,161,60,.18)',
  color: lk.estado.atencao,
})

export const blocoMotivos = style({ display: 'flex', flexDirection: 'column', gap: '5px' })

export const motivoLinha = style({ fontSize: '11.5px', lineHeight: 1.45, color: lk.estado.atencao })

export const pontoChip = {
  ativo: style({ width: '9px', height: '9px', borderRadius: '50%', background: lk.cor.cianoVisao }),
  inativo: style({ width: '9px', height: '9px', borderRadius: '50%', background: lk.cor.cinzaNevoa }),
}

export const blocoCondicoes = style({ display: 'flex', flexDirection: 'column', gap: '8px' })

const condicaoBase = {
  display: 'flex',
  alignItems: 'flex-start',
  gap: '11px',
  padding: '12px 13px',
  borderRadius: lk.raio.m,
  cursor: 'pointer',
  textAlign: 'left',
} as const

export const condicaoBotao = {
  ativo: style({ ...condicaoBase, background: 'rgba(0,229,255,.05)', border: '1px solid rgba(0,229,255,.4)' }),
  inativo: style({ ...condicaoBase, background: lk.cor.preto, border: `1px solid ${lk.cor.borda}` }),
}

export const radioCirculo = {
  ativo: style({ width: '17px', height: '17px', flex: 'none', marginTop: '1px', borderRadius: '50%', border: `1.5px solid ${lk.cor.cianoVisao}`, background: lk.cor.cianoVisao }),
  inativo: style({ width: '17px', height: '17px', flex: 'none', marginTop: '1px', borderRadius: '50%', border: `1.5px solid ${lk.cor.bordaForte}`, background: 'transparent' }),
}

export const condicaoTextos = style({ display: 'flex', flexDirection: 'column', gap: '2px' })
export const condicaoTitulo = style({ fontSize: '13px', fontWeight: 600, color: lk.cor.brancoSinal })
export const condicaoSub = style({ fontSize: '11.5px', color: lk.cor.cinzaNevoa, lineHeight: 1.45 })

export const blocoSegundos = style({
  display: 'flex',
  flexDirection: 'column',
  gap: '8px',
  padding: '12px 13px',
  background: lk.cor.preto,
  border: `1px solid ${lk.cor.borda}`,
  borderRadius: lk.raio.m,
})

export const linhaSegundos = style({ display: 'flex', alignItems: 'baseline', gap: '8px' })
export const numeroSegundos = style({ fontFamily: lk.fonte.mono, fontWeight: 700, fontSize: '18px', color: lk.cor.brancoSinal })
export const slider = style({ width: '100%', accentColor: lk.cor.cianoVisao })

export const linhaSensibilidade = style({ display: 'flex', background: lk.cor.grafite, borderRadius: lk.raio.s, padding: '3px', gap: '2px' })

export const sensOpcao = {
  ativo: style({ flex: 1, height: '34px', border: 'none', borderRadius: '6px', background: lk.cor.preto, color: lk.cor.cianoVisao, fontFamily: lk.fonte.ui, fontSize: '12.5px', fontWeight: 600, cursor: 'pointer' }),
  inativo: style({ flex: 1, height: '34px', border: 'none', borderRadius: '6px', background: 'transparent', color: lk.cor.cinzaNevoa, fontFamily: lk.fonte.ui, fontSize: '12.5px', fontWeight: 600, cursor: 'pointer' }),
}

export const blocoPreview = style({
  display: 'flex',
  flexDirection: 'column',
  gap: '10px',
  padding: lk.espaco.x1,
  background: lk.cor.grafite,
  border: '1px solid rgba(0,229,255,.35)',
  borderRadius: lk.raio.g,
})

export const overlinePreview = style({ fontFamily: lk.fonte.mono, fontSize: '10px', letterSpacing: OVERLINE_TRACKING, color: lk.cor.cianoVisao })
export const textoPreview = style({ fontSize: '14px', lineHeight: 1.6, color: lk.cor.brancoSinal })
export const textoPreviewTecnico = style({ fontFamily: lk.fonte.mono, fontSize: '10.5px', color: lk.cor.cinzaNevoa, lineHeight: 1.5 })

export const avisoIncompleto = style({
  display: 'flex',
  alignItems: 'center',
  gap: '9px',
  padding: '10px 12px',
  background: 'rgba(232,161,60,.07)',
  border: '1px solid rgba(232,161,60,.4)',
  borderRadius: '9px',
})

export const avisoIncompletoTexto = style({ fontSize: '12.5px', color: lk.estado.atencao, fontWeight: 600 })

export const linhaSalvar = style({ display: 'flex', gap: '8px' })

export const botaoSalvar = style({
  flex: 1,
  height: '46px',
  border: 'none',
  borderRadius: '10px',
  fontFamily: lk.fonte.ui,
  fontSize: '14px',
  fontWeight: 700,
  cursor: 'pointer',
  color: lk.cor.preto,
})

export const botaoSalvarPronto = style({ background: lk.cor.cianoVisao, opacity: 1 })
export const botaoSalvarIncompleto = style({ background: lk.cor.bordaForte, opacity: 0.75, cursor: 'not-allowed' })

export const botaoCancelar = style({
  height: '46px',
  padding: '0 15px',
  background: 'transparent',
  border: `1px solid ${lk.cor.borda}`,
  borderRadius: '10px',
  color: lk.cor.brancoSinal,
  fontFamily: lk.fonte.ui,
  fontSize: '13.5px',
  fontWeight: 600,
  cursor: 'pointer',
})

export const notaPropagacao = style({ fontFamily: lk.fonte.mono, fontSize: '10px', color: lk.cor.cinzaNevoa })

export const erroSalvar = style({ fontSize: '12.5px', color: lk.estado.nc, lineHeight: 1.5 })

// ── modo avançado (só superadmin) ───────────────────────────────────────────

export const blocoAvancado = style({
  display: 'flex',
  flexDirection: 'column',
  background: lk.cor.grafite,
  border: `1px solid ${lk.cor.borda}`,
  borderRadius: lk.raio.g,
  overflow: 'hidden',
})

export const botaoAvancado = style({
  display: 'flex',
  alignItems: 'center',
  gap: '10px',
  padding: '13px 16px',
  background: 'transparent',
  border: 'none',
  cursor: 'pointer',
  textAlign: 'left',
})

export const rotuloAvancado = style({ fontSize: '13px', fontWeight: 600, color: lk.cor.cinzaNevoa })

export const seloSuperadmin = style({
  marginLeft: 'auto',
  fontFamily: lk.fonte.mono,
  fontSize: '9px',
  letterSpacing: '.08em',
  color: lk.estado.atencao,
  border: '1px solid rgba(232,161,60,.4)',
  borderRadius: '4px',
  padding: '2px 7px',
})

export const corpoAvancado = style({ display: 'flex', flexDirection: 'column', gap: '10px', padding: '0 16px 16px' })
export const textoAvancado = style({ fontSize: '12px', color: lk.cor.cinzaNevoa, lineHeight: 1.5 })

export const gradeAvancada = style({
  display: 'grid',
  gridTemplateColumns: 'auto 1fr',
  gap: '7px 14px',
  fontFamily: lk.fonte.mono,
  fontSize: '11.5px',
})

export const chaveAvancada = style({ color: lk.cor.cinzaNevoa })

export const jsonAvancado = style({
  fontFamily: lk.fonte.mono,
  fontSize: '10.5px',
  color: lk.cor.cinzaNevoa,
  lineHeight: 1.6,
  padding: '11px',
  background: lk.cor.preto,
  border: `1px solid ${lk.cor.borda}`,
  borderRadius: lk.raio.s,
  wordBreak: 'break-all',
})
