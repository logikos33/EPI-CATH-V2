import { useMemo } from 'react'
import { create } from 'zustand'

export type ToastVariant = 'success' | 'error' | 'warning' | 'info'

export interface ToastItem {
  id: string
  variant: ToastVariant
  title: string
  description?: string
  duration?: number
  /**
   * Miniatura da evidência (URL assinada). Opcional e SEM placeholder: sem
   * imagem, o aviso mostra o ícone da variante. Desenhar um quadrado cinza no
   * lugar de uma evidência que não existe é afirmar que ela existe.
   */
  thumbUrl?: string
  /**
   * Torna o corpo do aviso clicável (o botão de fechar segue independente).
   * Usado pelo aviso de notificação: clicar abre o evento E marca como lida.
   */
  onClick?: () => void
}

interface ToastStore {
  toasts: ToastItem[]
  push: (item: Omit<ToastItem, 'id'>) => void
  dismiss: (id: string) => void
}

let counter = 0

export const useToastStore = create<ToastStore>((set) => ({
  toasts: [],
  push: (item) => {
    const id = String(++counter)
    const duration = item.duration ?? 4000
    set((s) => ({ toasts: [...s.toasts, { ...item, id }] }))
    if (duration > 0) {
      setTimeout(() => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })), duration)
    }
  },
  dismiss: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}))

export function useToast() {
  const push = useToastStore((s) => s.push)
  // Identidade estável: este objeto entra em arrays de dependência de
  // useCallback/useEffect (estúdio, galeria, classes). Recriá-lo a cada render
  // redispara efeitos de fetch em loop até o rate limit (429) derrubar a tela.
  return useMemo(
    () => ({
      success: (title: string, description?: string) => push({ variant: 'success' as const, title, description }),
      error: (title: string, description?: string) => push({ variant: 'error' as const, title, description }),
      warning: (title: string, description?: string) => push({ variant: 'warning' as const, title, description }),
      info: (title: string, description?: string) => push({ variant: 'info' as const, title, description }),
    }),
    [push],
  )
}
