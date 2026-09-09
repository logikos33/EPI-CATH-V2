/**
 * useLupa — a COLA React da lupa da evidência (roda, pinça, arrasto, teclado).
 *
 * A MATEMÁTICA já era compartilhada: `pages/epi/lupaEvidencia.ts`, pura e
 * testada. O que não era, e é o que este arquivo passa a ser, são as ~90
 * linhas de ligação com o DOM que `EventoDetalhe.tsx` guardava dentro de si:
 * mapa de ponteiros, pinça, listener de roda não-passivo, teclas.
 *
 * O painel de julgamento da lista precisa exatamente disso. Copiar daria dois
 * zooms que divergem no primeiro ajuste — o limite de pan mudaria num e não no
 * outro, e o defeito só apareceria com a orelha ampliada a 8×, que é onde
 * ninguém olha duas vezes. Então as DUAS telas chamam este hook.
 *
 * ⚠️ O `transform` que o consumidor tem de aplicar é
 * `translate(x px, y px) scale(escala)` com `transform-origin: center` — a
 * âncora do zoom é medida a partir do CENTRO do palco. Trocar a origem para
 * `0 0` quebra a âncora em silêncio (ver cabeçalho de `lupaEvidencia.ts`).
 */
import { useCallback, useRef, useState } from 'react'

import {
  ESCALA_MAX, ESCALA_MIN, LUPA_INICIAL, distanciaEntre, proximoEstado,
  type EstadoLupa, type EventoLupa, type Palco,
} from '../../pages/epi/lupaEvidencia'

/** Deslocamento por tecla de seta, em pixels do palco. Zoom só na roda é
 *  inutilizável sem mouse — isto não é enfeite de acessibilidade. */
const PASSO_TECLA = 40

/** Âncora relativa ao CENTRO do palco — `transformOrigin: center` assume isso. */
function ancorar(rect: DOMRect, clientX: number, clientY: number) {
  return {
    ancoraX: clientX - (rect.left + rect.width / 2),
    ancoraY: clientY - (rect.top + rect.height / 2),
  }
}

export interface UsoLupa {
  /** Estado atual — o consumidor lê `escala` para contra-escalar bordas e rótulos. */
  lupa: EstadoLupa
  /** `ref` do palco. É callback de propósito: o listener de roda precisa ser
   *  NÃO-passivo (o `onWheel` do React é passivo e não deixa `preventDefault`),
   *  e amarrá-lo à montagem do elemento evita a dependência frágil que a tela
   *  de detalhe tinha — ela reanexava quando a URL da evidência mudava, o que
   *  só funcionava por coincidência de renderização. */
  refPalco: (el: HTMLDivElement | null) => void
  /** Já no enquadramento inteiro (nada a reduzir). */
  noPiso: boolean
  /** Já na ampliação máxima. */
  noTeto: boolean
  aoDescerPonteiro: (e: React.PointerEvent<HTMLElement>) => void
  aoMoverPonteiro: (e: React.PointerEvent<HTMLElement>) => void
  aoSoltarPonteiro: (e: React.PointerEvent<HTMLElement>) => void
  aoDuploClique: (e: React.MouseEvent<HTMLElement>) => void
  /** Devolve `true` quando a tecla foi consumida (e já teve `preventDefault`),
   *  para o consumidor saber que não deve tratá-la de novo. */
  aoTeclar: (e: React.KeyboardEvent<HTMLElement>) => boolean
  /** Botão de ampliar/reduzir: ancora no centro do palco, não no cursor. */
  ampliar: (fator: number) => void
  /** Volta ao frame inteiro. Estável, e serve também para reenquadrar quando o
   *  consumidor troca de evento — enquadramento não atravessa evidência. */
  reenquadrar: () => void
}

export function useLupa(): UsoLupa {
  const palcoRef = useRef<HTMLDivElement | null>(null)
  const [lupa, setLupa] = useState(LUPA_INICIAL)
  // O listener de roda é registrado uma vez; o ref dá a ele o estado atual sem
  // reanexar a cada render.
  const lupaRef = useRef(lupa)
  lupaRef.current = lupa
  const ponteiros = useRef(new Map<number, { x: number; y: number }>())
  const distPinca = useRef(0)

  const medir = useCallback((): { rect: DOMRect; palco: Palco } | null => {
    const el = palcoRef.current
    if (!el) return null
    const rect = el.getBoundingClientRect()
    return { rect, palco: { largura: rect.width, altura: rect.height } }
  }, [])

  const despachar = useCallback((ev: EventoLupa, palco: Palco) => {
    setLupa((prev) => proximoEstado(prev, ev, palco))
  }, [])

  const aoRodar = useCallback(
    (ev: WheelEvent) => {
      // Já no piso e afastando: NÃO sequestra a roda — a página rola normal.
      if (lupaRef.current.escala === ESCALA_MIN && ev.deltaY > 0) return
      ev.preventDefault() // exige passive:false; o onWheel do React é passivo.
      const m = medir()
      if (!m) return
      despachar(
        {
          tipo: 'zoom',
          fator: ev.deltaY < 0 ? 1.15 : 1 / 1.15,
          ...ancorar(m.rect, ev.clientX, ev.clientY),
        },
        m.palco,
      )
    },
    [despachar, medir],
  )

  const refPalco = useCallback(
    (el: HTMLDivElement | null) => {
      palcoRef.current?.removeEventListener('wheel', aoRodar)
      palcoRef.current = el
      el?.addEventListener('wheel', aoRodar, { passive: false })
    },
    [aoRodar],
  )

  const aoDescerPonteiro = useCallback((e: React.PointerEvent<HTMLElement>) => {
    ponteiros.current.set(e.pointerId, { x: e.clientX, y: e.clientY })
    if (ponteiros.current.size === 2) {
      distPinca.current = distanciaEntre([...ponteiros.current.values()])
    }
    e.currentTarget.setPointerCapture?.(e.pointerId)
  }, [])

  const aoMoverPonteiro = useCallback(
    (e: React.PointerEvent<HTMLElement>) => {
      const anterior = ponteiros.current.get(e.pointerId)
      if (!anterior) return
      ponteiros.current.set(e.pointerId, { x: e.clientX, y: e.clientY })
      const m = medir()
      if (!m) return
      const pontos = [...ponteiros.current.values()]
      if (pontos.length >= 2) {
        // Pinça: fator = variação da distância, âncora no ponto médio.
        const nova = distanciaEntre(pontos)
        if (distPinca.current > 0 && nova > 0) {
          const meio = {
            x: (pontos[0].x + pontos[1].x) / 2,
            y: (pontos[0].y + pontos[1].y) / 2,
          }
          despachar(
            { tipo: 'zoom', fator: nova / distPinca.current, ...ancorar(m.rect, meio.x, meio.y) },
            m.palco,
          )
        }
        distPinca.current = nova
        return
      }
      // Em escala 1 o limite de pan é 0: arrastar já é inócuo, sem guarda extra.
      despachar({ tipo: 'arrastar', dx: e.clientX - anterior.x, dy: e.clientY - anterior.y }, m.palco)
    },
    [despachar, medir],
  )

  const aoSoltarPonteiro = useCallback((e: React.PointerEvent<HTMLElement>) => {
    ponteiros.current.delete(e.pointerId)
    distPinca.current = 0
  }, [])

  const aoDuploClique = useCallback(
    (e: React.MouseEvent<HTMLElement>) => {
      const m = medir()
      if (!m) return
      if (lupaRef.current.escala >= ESCALA_MAX) {
        despachar({ tipo: 'reset' }, m.palco)
        return
      }
      despachar({ tipo: 'zoom', fator: 2, ...ancorar(m.rect, e.clientX, e.clientY) }, m.palco)
    },
    [despachar, medir],
  )

  const aoTeclar = useCallback(
    (e: React.KeyboardEvent<HTMLElement>): boolean => {
      const m = medir()
      if (!m) return false
      const noCentro = { ancoraX: 0, ancoraY: 0 }
      const setas: Record<string, [number, number]> = {
        ArrowLeft: [PASSO_TECLA, 0], ArrowRight: [-PASSO_TECLA, 0],
        ArrowUp: [0, PASSO_TECLA], ArrowDown: [0, -PASSO_TECLA],
      }
      if (e.key === '+' || e.key === '=') despachar({ tipo: 'zoom', fator: 1.5, ...noCentro }, m.palco)
      else if (e.key === '-' || e.key === '_') despachar({ tipo: 'zoom', fator: 1 / 1.5, ...noCentro }, m.palco)
      else if (e.key === '0') despachar({ tipo: 'reset' }, m.palco)
      else if (setas[e.key]) {
        const [dx, dy] = setas[e.key]
        despachar({ tipo: 'arrastar', dx, dy }, m.palco)
      } else return false
      e.preventDefault()
      return true
    },
    [despachar, medir],
  )

  const ampliar = useCallback(
    (fator: number) => {
      const m = medir()
      if (m) despachar({ tipo: 'zoom', fator, ancoraX: 0, ancoraY: 0 }, m.palco)
    },
    [despachar, medir],
  )

  // Não passa por `despachar`: reenquadrar tem de funcionar mesmo antes de o
  // palco existir (é o que o consumidor chama ao TROCAR de evento, e nesse
  // instante a evidência nova ainda não montou).
  const reenquadrar = useCallback(() => setLupa(LUPA_INICIAL), [])

  return {
    lupa,
    refPalco,
    noPiso: lupa.escala <= ESCALA_MIN,
    noTeto: lupa.escala >= ESCALA_MAX,
    aoDescerPonteiro,
    aoMoverPonteiro,
    aoSoltarPonteiro,
    aoDuploClique,
    aoTeclar,
    ampliar,
    reenquadrar,
  }
}
