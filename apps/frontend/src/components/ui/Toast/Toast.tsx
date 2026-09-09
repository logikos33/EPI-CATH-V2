import { CheckCircle, XCircle, AlertTriangle, Info, X } from 'lucide-react'
import { useToastStore } from './useToast'
import {
  viewport, toast, toastIcon, toastBody, toastTitle, toastDescription, toastClose,
  toastCorpoClicavel, toastMiniatura,
} from './Toast.css'
import type { ToastVariant } from './useToast'

const ICONS: Record<ToastVariant, typeof CheckCircle> = {
  success: CheckCircle,
  error: XCircle,
  warning: AlertTriangle,
  info: Info,
}

export function ToastProvider() {
  const toasts = useToastStore((s) => s.toasts)
  const dismiss = useToastStore((s) => s.dismiss)

  return (
    <div className={viewport} aria-live="polite" aria-label="Notificações">
      {toasts.map((t) => {
        const Icon = ICONS[t.variant]
        const corpo = (
          <>
            <div className={toastTitle}>{t.title}</div>
            {t.description && <div className={toastDescription}>{t.description}</div>}
          </>
        )
        return (
          <div key={t.id} className={toast({ variant: t.variant })} role="status">
            {/* Miniatura no lugar do ícone quando ela EXISTE. Sem URL, fica o
                ícone da variante — nunca um quadro cinza fingindo evidência. */}
            {t.thumbUrl ? (
              <img className={toastMiniatura} src={t.thumbUrl} alt="" aria-hidden="true" />
            ) : (
              <div className={toastIcon({ variant: t.variant })}>
                <Icon size={16} />
              </div>
            )}
            {t.onClick ? (
              // Botão de verdade (não `<div onClick>`): teclado e leitor de tela
              // alcançam o aviso. O "Fechar" fica FORA dele — botão dentro de
              // botão é HTML inválido, e o clique de fechar viraria o clique do
              // aviso, abrindo o evento de quem queria justamente dispensá-lo.
              <button
                type="button"
                className={`${toastBody} ${toastCorpoClicavel}`}
                onClick={() => {
                  t.onClick?.()
                  dismiss(t.id)
                }}
              >
                {corpo}
              </button>
            ) : (
              <div className={toastBody}>{corpo}</div>
            )}
            <button className={toastClose} onClick={() => dismiss(t.id)} aria-label="Fechar">
              <X size={14} />
            </button>
          </div>
        )
      })}
    </div>
  )
}
