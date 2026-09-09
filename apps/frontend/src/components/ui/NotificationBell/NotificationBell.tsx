import { useState, useEffect, useMemo, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { Bell, CheckCheck, ChevronDown, ChevronRight } from 'lucide-react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { vars } from '../../../styles/theme.css'
import { agruparPorRajada } from '../../../utils/rajadas'
import {
  CHAVE_PENDENTES,
  buscarPendentes,
  haQuantoTempo,
  marcarLidas,
  marcarTodasLidas,
  quandoAconteceu,
  rotaDoEvento,
  rotuloDasClasses,
  type Notificacao,
  type PaginaNotificacoes,
} from '../../../services/notificacoes'
import { useToast } from '../Toast/useToast'
import { useAvisosDeNotificacao } from './useAvisosDeNotificacao'
import {
  bellWrap,
  bellBtn,
  badge,
  panel,
  panelHeader,
  panelTitle,
  panelBody,
  alertCard,
  alertIcon,
  alertContent,
  alertCamera,
  alertViolation,
  alertTime,
  rajadaToggle,
  rajadaLista,
  rajadaItem,
  emptyPanel,
  viewAllBtn,
  rodape,
  marcarTodasBtn,
} from './NotificationBell.css'

export interface NotificationBellProps {
  /**
   * Base do deep-link dos alertas.
   *
   * O front NOVO monta ESTE mesmo sino (Shell.tsx) — inclusive o dedup de
   * rajada abaixo, que é o motivo de não haver um segundo sino. Só que a tela
   * de eventos dele é `/novo/epi/eventos`: mandar `/epi/alerts` levaria o
   * usuário, calado, para a tela ANTIGA de mesmo assunto — `/epi/alerts` é rota
   * VÁLIDA no app (mesmo pisão descrito em `RotasNovas.tsx`).
   *
   * Default = o endereço do front antigo, para a `TopBar` legada seguir igual.
   */
  rotaAlertas?: string
  /**
   * Central de notificações (o rol com histórico). Opcional DE PROPÓSITO: ela
   * só existe no front novo, e oferecê-la a partir da TopBar legada jogaria
   * quem está no produto velho dentro do novo, sem aviso — o mesmo pisão de
   * `rotaAlertas`, na direção contrária. Sem a prop, o link não aparece.
   */
  rotaCentral?: string
}

export function NotificationBell({
  rotaAlertas = '/epi/alerts',
  rotaCentral,
}: NotificationBellProps = {}) {
  const navigate = useNavigate()
  const toast = useToast()
  const clienteQuery = useQueryClient()
  const [isOpen, setIsOpen] = useState(false)
  const wrapRef = useRef<HTMLDivElement>(null)

  const { data } = useQuery({
    // ADR-0065: o sino NÃO toca por EPI presente. O roteamento de notificação
    // segue desligado (notification_channels vazia) e, quando nascer, nasce
    // ligado a este mesmo recorte de AUSÊNCIA.
    //
    // Chave e consulta vêm de `services/notificacoes` — o aviso (pop-up) e a
    // central leem as MESMAS constantes. Três telas com três recortes seria
    // três números de "quantas pendentes" discordando entre si.
    queryKey: CHAVE_PENDENTES,
    queryFn: buscarPendentes,
    refetchInterval: 30000,
    staleTime: 20000,
  })

  const pagina = data as PaginaNotificacoes | undefined
  const alerts: Notificacao[] = pagina?.alerts ?? []
  const totalSituacoes = pagina?.total_situacoes
  // Badge/contagem conta SITUAÇÕES (rajadas), não linhas — ux2/dedup. Sem
  // `total_situacoes` (backend/mock antigo), cai pro que já existia.
  //
  // ⚠️ issue #803: aqui havia `Math.min(…, 99)`, e o `count > 99 ? '99+'` do
  // badge (30 linhas abaixo) era código morto por causa dele — `count` nunca
  // passava de 99. O efeito não era truncar com honestidade: era AFIRMAR 99.
  // O painel dizia "99 pendentes" com 387 situações abertas no DEV (RVB,
  // 05/09). Truncar o BADGE é legítimo (espaço de 2 dígitos); afirmar o teto
  // como se fosse a conta, não. Agora o número real vive aqui, o badge o
  // trunca para "99+" e o painel imprime o valor inteiro.
  const pendentes = totalSituacoes ?? alerts.length

  const recarregar = () => clienteQuery.invalidateQueries({ queryKey: CHAVE_PENDENTES })

  const abrir = (n: Notificacao) => {
    // Clicar MARCA COMO LIDA (pedido do dono) e abre o evento. A marcação vai
    // em paralelo: segurar a navegação num POST deixaria o operador olhando
    // para o painel parado. Se o POST falhar, o evento continua pendente e o
    // sino o mostra de novo na próxima varredura — o pior caso é reler, nunca
    // perder.
    marcarLidas([n.id]).then(recarregar).catch(() => {
      toast.error('Não foi possível marcar a notificação como lida')
    })
    navigate(rotaDoEvento(rotaAlertas, n))
    setIsOpen(false)
  }

  const marcarTodas = useMutation({
    mutationFn: () => marcarTodasLidas({ kind: 'violation' }),
    onSuccess: async (quantas) => {
      await recarregar()
      // O número vem do backend (linhas que MUDARAM), não do que estava na
      // tela: o painel só carrega 30 e a marcação alcança as 235.
      toast.success(
        quantas === 1 ? '1 notificação marcada como lida' : `${quantas} notificações marcadas como lidas`,
      )
    },
    onError: () => toast.error('Não foi possível marcar todas como lidas'),
  })

  // Pop-up de quem chega agora. Mora aqui porque o sino JÁ tem a lista e JÁ
  // varre de 30 em 30s — um segundo componente com a própria busca criaria uma
  // segunda contagem de pendentes.
  //
  // ⚠️ `pagina?.alerts` e NÃO `alerts`: o segundo é `?? []`, um array NOVO a
  // cada render, e enquanto a busca não volta ele valeria como "já vi, estava
  // vazio". A primeira resposta de verdade viraria 30 pop-ups de eventos
  // antigos — exatamente o que a guarda de 1ª carga existe para impedir.
  // `undefined` = ainda não vi resposta nenhuma; `[]` = vi, e não havia nada.
  useAvisosDeNotificacao(pagina?.alerts, {
    aoAbrir: abrir,
    aoAbrirCentral: rotaCentral ? () => navigate(rotaCentral) : undefined,
  })

  // Agrupa o que está NA TELA (as até 30 linhas buscadas) por câmera+classe
  // em <60s — mesma janela do backend (VerificationService). Representante +
  // alternador "+N repetições"; nunca esconde, cada repetição mantém o
  // próprio deep-link.
  const grupos = useMemo(
    () =>
      agruparPorRajada(alerts, {
        cameraId: (a) => a.camera_id,
        classe: (a) => a.violations?.[0]?.class ?? '',
        criadoEm: (a) => quandoAconteceu(a),
      }),
    [alerts],
  )
  const [expandidos, setExpandidos] = useState<Set<string>>(new Set())
  const alternarExpandido = (id: string) =>
    setExpandidos((atual) => {
      const novo = new Set(atual)
      if (novo.has(id)) novo.delete(id)
      else novo.add(id)
      return novo
    })

  useEffect(() => {
    if (!isOpen) return

    function handleMouseDown(e: MouseEvent) {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setIsOpen(false)
      }
    }

    document.addEventListener('mousedown', handleMouseDown)
    return () => document.removeEventListener('mousedown', handleMouseDown)
  }, [isOpen])

  return (
    <div className={bellWrap} ref={wrapRef}>
      <button
        className={bellBtn}
        onClick={() => setIsOpen(v => !v)}
        aria-label="Notificações"
      >
        <Bell
          size={18}
          color={isOpen ? vars.color.primary : vars.color.textSecondary}
        />
        {pendentes > 0 && (
          <span className={badge} title={`${pendentes.toLocaleString('pt-BR')} pendente(s)`}>
            {pendentes > 99 ? '99+' : pendentes}
          </span>
        )}
      </button>

      {isOpen && (
        <div className={panel}>
          <div className={panelHeader}>
            <span className={panelTitle}>Notificações</span>
            <span style={{ fontSize: 11, color: vars.color.textDim }}>
              {pendentes.toLocaleString('pt-BR')} pendente{pendentes !== 1 ? 's' : ''}
            </span>
          </div>

          {/* UMA requisição para as 235, não 235 (`POST /alerts/acknowledge`). */}
          {pendentes > 0 && (
            <button
              type="button"
              className={marcarTodasBtn}
              onClick={() => marcarTodas.mutate()}
              disabled={marcarTodas.isPending}
            >
              <CheckCheck size={13} />
              {marcarTodas.isPending ? 'Marcando…' : 'Marcar todas como lidas'}
            </button>
          )}

          <div className={panelBody}>
            {alerts.length === 0 ? (
              <div className={emptyPanel}>Nenhum alerta pendente</div>
            ) : (
              grupos.map(grupo => {
                const alert = grupo.representante
                const repeticoes = grupo.repeticoes.filter(r => r.id !== alert.id)
                const expandido = expandidos.has(alert.id)
                const violationText = rotuloDasClasses(alert.violations)
                return (
                  <div key={alert.id}>
                    <button
                      type="button"
                      className={alertCard}
                      onClick={() => abrir(alert)}
                      aria-label={`Abrir alerta de ${alert.camera_name ?? 'câmera'}: ${violationText}`}
                    >
                      <div className={alertIcon}>
                        <span style={{ color: vars.color.warning, fontSize: 14 }}>⚠</span>
                      </div>
                      <div className={alertContent}>
                        <div className={alertCamera}>
                          {alert.camera_name ?? 'Câmera'}
                        </div>
                        <div className={alertViolation}>{violationText}</div>
                        <div className={alertTime}>{haQuantoTempo(quandoAconteceu(alert))}</div>
                      </div>
                    </button>
                    {/* Rajada (ux2/dedup): mesma câmera+classe em <60s — nunca
                        esconde, cada repetição mantém o próprio deep-link. */}
                    {repeticoes.length > 0 && (
                      <button
                        type="button"
                        className={rajadaToggle}
                        onClick={(e) => { e.stopPropagation(); alternarExpandido(alert.id) }}
                      >
                        {expandido ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                        +{repeticoes.length} repetiç{repeticoes.length === 1 ? 'ão' : 'ões'} da mesma cena
                      </button>
                    )}
                    {expandido && repeticoes.length > 0 && (
                      <div className={rajadaLista}>
                        {repeticoes.map(r => (
                          <button
                            key={r.id}
                            type="button"
                            className={rajadaItem}
                            onClick={() => abrir(r)}
                            aria-label={`Abrir alerta de ${r.camera_name ?? 'câmera'} · ${haQuantoTempo(quandoAconteceu(r))}`}
                          >
                            {haQuantoTempo(quandoAconteceu(r))}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                )
              })
            )}
          </div>

          <div className={rodape}>
            <button
              className={viewAllBtn}
              onClick={() => {
                navigate(`${rotaAlertas}?acknowledged=false&kind=violation`)
                setIsOpen(false)
              }}
            >
              Ver todos os alertas →
            </button>
            {rotaCentral && (
              <button
                className={viewAllBtn}
                onClick={() => {
                  navigate(rotaCentral)
                  setIsOpen(false)
                }}
              >
                Central de notificações (lidas e não lidas)
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
