/**
 * Aviso (pop-up) de notificação que ACABOU de chegar.
 *
 * ─── O QUE É HONESTO DIZER SOBRE A FONTE ────────────────────────────────────
 *
 * Hoje NADA emite reconhecimento ao vivo: o edge não infere, e o bridge
 * Redis→SocketIO (`app/core/socket_bridge.py`) só encaminha `detection` no
 * namespace `/monitor` — não existe evento `alert` sendo emitido por ninguém
 * (`useMonitoringSocket` escuta `'alert'` desde sempre, e nunca recebeu um).
 *
 * Por isso este aviso NÃO depende de socket. Ele observa a MESMA lista que o
 * sino já busca de 30 em 30 segundos (`/alerts?acknowledged=false&kind=violation`)
 * e dispara quando aparece um id que ainda não tinha aparecido. Quando o edge
 * voltar a inferir, o alerta é gravado pelo worker e entra nessa lista — o
 * aviso nasce funcionando, sem nenhum modo de demonstração e sem nada na tela
 * fingindo atividade que não existe.
 *
 * ─── AS TRÊS FORMAS DE BUGAR, E O QUE IMPEDE CADA UMA ────────────────────────
 *
 * 1. **Cuspir o acervo inteiro ao abrir a tela.** A PRIMEIRA resposta só
 *    carimba os ids como vistos e não avisa nada: 235 pendentes de ontem não
 *    são "chegou agora". Só a 2ª carga em diante pode avisar.
 * 2. **Empilhar até cobrir a tela.** Teto de avisos SIMULTÂNEOS (`TETO_NA_TELA`);
 *    o excedente vira UM aviso agregado, e com a tela cheia o ciclo não empurra
 *    nada — o sino continua contando tudo, ninguém perde evento.
 * 3. **Repetir o mesmo evento.** Set de ids já avisados, aparado por FIFO.
 *
 * Timer: quem conta os 30s é o store do Toast (um `setTimeout` por aviso, que
 * se resolve sozinho). Este hook não cria timer nenhum — não há o que vazar.
 */
import { useEffect, useRef } from 'react'

import {
  dataHoraLocal,
  miniaturaDaEvidencia,
  quandoAconteceu,
  rotuloDasClasses,
  type Notificacao,
} from '../../../services/notificacoes'
import { useToastStore } from '../Toast/useToast'

/** ~30s, como pedido: some sozinho ou quando o usuário fechar. */
export const DURACAO_AVISO_MS = 30_000

/** Avisos vivos ao mesmo tempo. Acima disto o pop-up vira obstáculo. */
export const TETO_NA_TELA = 3

/**
 * Ids já avisados que ficam na memória. Bounded de propósito: uma sessão de
 * turno inteiro numa fábrica com 28 câmeras acumularia milhares.
 * ponytail: FIFO simples; se um evento sair da janela de 30 e voltar à página
 * depois de 500 outros, ele avisa de novo — trocar por marca d'água de
 * `timestamp` se isso um dia incomodar.
 */
const TETO_VISTOS = 500

export interface OpcoesDeAviso {
  /** Abrir o evento (quem chama decide a rota e marca como lida). */
  aoAbrir: (n: Notificacao) => void
  /** Destino do aviso agregado ("+N novas"). Sem ele, o agregado não é clicável. */
  aoAbrirCentral?: () => void
  /** `false` desliga tudo (perfil sem permissão, tela de kiosk…). */
  ativo?: boolean
}

function aparar(vistos: Set<string>): void {
  if (vistos.size <= TETO_VISTOS) return
  // Set em JS preserva ordem de inserção: os primeiros são os mais antigos.
  const excedente = vistos.size - TETO_VISTOS
  let i = 0
  for (const id of vistos) {
    if (i++ >= excedente) break
    vistos.delete(id)
  }
}

export function useAvisosDeNotificacao(
  pendentes: Notificacao[] | undefined,
  opcoes: OpcoesDeAviso,
): void {
  const { ativo = true } = opcoes
  const push = useToastStore((s) => s.push)

  // Os callbacks trocam de identidade a cada render de quem chama; guardá-los
  // num ref mantém o efeito preso APENAS à lista — senão cada render viraria
  // um ciclo de avaliação, e uma comparação errada viraria aviso duplicado.
  const opcoesRef = useRef(opcoes)
  opcoesRef.current = opcoes

  /** `null` = ainda não vi resposta nenhuma. Diferente de "vi e estava vazia". */
  const vistos = useRef<Set<string> | null>(null)

  useEffect(() => {
    if (!ativo || !pendentes) return

    const anterior = vistos.current
    if (anterior === null) {
      vistos.current = new Set(pendentes.map((p) => p.id))
      return // 1ª carga = acervo pendente, não "chegou agora"
    }

    const novos = pendentes.filter((p) => !anterior.has(p.id))
    for (const p of pendentes) anterior.add(p.id)
    aparar(anterior)
    if (novos.length === 0) return

    const vagas = TETO_NA_TELA - useToastStore.getState().toasts.length
    if (vagas <= 0) return // tela cheia: o sino já contabiliza, ninguém perde nada

    const individuais = novos.slice(0, vagas)
    const resto = novos.length - individuais.length

    let cancelado = false
    void (async () => {
      for (const n of individuais) {
        // A miniatura vem antes do push: o store não sabe atualizar um aviso já
        // empurrado, e piscar imagem depois do texto é pior que esperar 200ms.
        const thumb = await miniaturaDaEvidencia(n.id)
        if (cancelado) return
        push({
          variant: 'warning',
          title: n.camera_name ?? 'Câmera',
          description: `${rotuloDasClasses(n.violations)} · ${dataHoraLocal(quandoAconteceu(n))}`,
          duration: DURACAO_AVISO_MS,
          thumbUrl: thumb ?? undefined,
          onClick: () => opcoesRef.current.aoAbrir(n),
        })
      }
      if (cancelado || resto <= 0) return
      const abrirCentral = opcoesRef.current.aoAbrirCentral
      push({
        variant: 'info',
        title: `+${resto} nova${resto > 1 ? 's' : ''} notificaç${resto > 1 ? 'ões' : 'ão'}`,
        description: abrirCentral ? 'Abrir a central de notificações' : 'Veja no sino',
        duration: DURACAO_AVISO_MS,
        onClick: abrirCentral,
      })
    })()

    return () => {
      cancelado = true
    }
  }, [pendentes, ativo, push])
}
