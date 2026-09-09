import { style } from '@vanilla-extract/css'
import { vars } from '../../../styles/theme.css'

export const bellWrap = style({
  position: 'relative',
  display: 'inline-flex',
})

export const bellBtn = style({
  width: '36px',
  height: '36px',
  background: 'transparent',
  border: 'none',
  cursor: 'pointer',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  borderRadius: vars.radius.md,
  transition: `background ${vars.animation.duration} ${vars.animation.easing}`,
  ':hover': {
    background: vars.color.bgHover,
  },
})

export const badge = style({
  position: 'absolute',
  top: 0,
  right: 0,
  minWidth: '16px',
  height: '16px',
  borderRadius: vars.radius.full,
  background: vars.color.danger,
  color: vars.color.textOnPrimary,
  fontSize: '10px',
  fontWeight: 700,
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  padding: '0 3px',
  pointerEvents: 'none',
  transform: 'translate(4px, -4px)',
})

export const panel = style({
  position: 'fixed',
  top: '60px',
  right: '16px',
  width: '360px',
  // Ancorado por `right` com largura FIXA, o painel vaza pela ESQUERDA em tela
  // estreita — medido com ele aberto: left = -1px em 375, -16px em 360 (Android
  // comum de chão de fábrica) e -56px em 320, o piso do handoff. O que some é o
  // começo de cada linha: ícone e nome da câmera. Nenhuma régua de `<main>` ou
  // de `scrollWidth` pega isto — ele é `fixed` (fora do fluxo) e vazamento para
  // a esquerda não gera rolagem. Mesmo teto que o aviso de sessão já usa.
  maxWidth: 'calc(100vw - 32px)',
  maxHeight: '480px',
  background: vars.color.bgSurface,
  border: `1px solid ${vars.color.borderDefault}`,
  borderRadius: vars.radius.lg,
  boxShadow: vars.shadow.lg,
  zIndex: 200,
  display: 'flex',
  flexDirection: 'column',
  overflow: 'hidden',
})

export const panelHeader = style({
  display: 'flex',
  justifyContent: 'space-between',
  alignItems: 'center',
  padding: `${vars.space.sm} ${vars.space.md}`,
  borderBottom: `1px solid ${vars.color.borderSubtle}`,
  flexShrink: 0,
})

export const panelTitle = style({
  fontSize: '14px',
  fontWeight: 600,
  color: vars.color.textPrimary,
})

export const panelBody = style({
  flex: 1,
  overflowY: 'auto',
})

// Card de alerta é um <button> (deep-link para /epi/alerts filtrado) —
// resets de botão + estados hover/focus com tokens do tema.
export const alertCard = style({
  display: 'flex',
  width: '100%',
  textAlign: 'left',
  gap: vars.space.sm,
  padding: `${vars.space.sm} ${vars.space.md}`,
  border: 'none',
  borderBottom: `1px solid ${vars.color.borderSubtle}`,
  background: 'transparent',
  font: 'inherit',
  color: 'inherit',
  cursor: 'pointer',
  transition: `background ${vars.animation.duration} ${vars.animation.easing}`,
  ':hover': {
    background: vars.color.bgHover,
  },
  ':focus-visible': {
    outline: `2px solid ${vars.color.primary}`,
    outlineOffset: '-2px',
  },
})

export const alertIcon = style({
  width: '20px',
  flexShrink: 0,
  paddingTop: '2px',
})

export const alertContent = style({
  flex: 1,
  minWidth: 0,
})

export const alertCamera = style({
  fontSize: '13px',
  fontWeight: 600,
  color: vars.color.textPrimary,
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  whiteSpace: 'nowrap',
})

export const alertViolation = style({
  fontSize: '12px',
  color: vars.color.textSecondary,
  marginTop: '2px',
})

export const alertTime = style({
  fontSize: '11px',
  color: vars.color.textDim,
  marginTop: '2px',
})

/**
 * Rajada (ux2/dedup) — "10 pendentes" que eram a MESMA cena de 6 dias atrás.
 * O cartão mostra o representante + este alternador; nunca esconde — cada
 * repetição continua com o próprio deep-link.
 */
export const rajadaToggle = style({
  display: 'flex',
  alignItems: 'center',
  gap: '4px',
  width: '100%',
  margin: 0,
  padding: `2px ${vars.space.md}`,
  border: 'none',
  background: 'transparent',
  font: 'inherit',
  fontSize: '11px',
  fontWeight: 600,
  color: vars.color.primary,
  cursor: 'pointer',
})

export const rajadaLista = style({
  display: 'flex',
  flexDirection: 'column',
  paddingBottom: '4px',
  borderBottom: `1px solid ${vars.color.borderSubtle}`,
})

export const rajadaItem = style([
  alertCard,
  { padding: `4px ${vars.space.md} 4px 40px`, fontSize: '11px', borderBottom: 'none' },
])

export const emptyPanel = style({
  padding: '32px 16px',
  textAlign: 'center',
  color: vars.color.textMuted,
  fontSize: '13px',
})

export const viewAllBtn = style({
  display: 'block',
  width: '100%',
  padding: `${vars.space.sm} ${vars.space.md}`,
  textAlign: 'center',
  fontSize: '13px',
  fontWeight: 600,
  color: vars.color.primary,
  background: 'transparent',
  border: 'none',
  borderTop: `1px solid ${vars.color.borderSubtle}`,
  cursor: 'pointer',
  flexShrink: 0,
  transition: `background ${vars.animation.duration} ${vars.animation.easing}`,
  ':hover': {
    background: vars.color.bgHover,
  },
})

/** "Marcar todas como lidas" — uma requisição para as 235, não 235. */
export const marcarTodasBtn = style({
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  gap: '6px',
  width: '100%',
  padding: `6px ${vars.space.md}`,
  border: 'none',
  borderBottom: `1px solid ${vars.color.borderSubtle}`,
  background: 'transparent',
  font: 'inherit',
  fontSize: '12px',
  fontWeight: 600,
  color: vars.color.primary,
  cursor: 'pointer',
  flexShrink: 0,
  ':hover': { background: vars.color.bgHover },
  ':disabled': { opacity: 0.5, cursor: 'progress' },
})

/** Rodapé: as saídas do painel ficam juntas e nunca rolam com a lista. */
export const rodape = style({ flexShrink: 0 })
