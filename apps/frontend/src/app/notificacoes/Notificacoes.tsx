/**
 * `/novo/notificacoes` — a central: o rol com HISTÓRICO, lidas e não lidas.
 *
 * ─── DE ONDE VEM O "LIDA" ───────────────────────────────────────────────────
 *
 * Não existe caixa de entrada por usuário no backend (`/api/v1/notifications/*`
 * é só CANAL: para onde mandar WhatsApp/e-mail/webhook). O que existe é
 * `alerts.acknowledged` — e é ele que esta tela chama de "lida". Consequência
 * que o rodapé da tela DIZ, em vez de esconder: a caixa é do TENANT, não de
 * cada pessoa. Quando um operador lê, sai da fila dos outros também.
 *
 * ─── O QUE ESTA TELA NÃO INVENTA ────────────────────────────────────────────
 *
 * · Não há "notificação de sistema", "menção" nem "resumo semanal": o único
 *   fato notificável que o produto grava hoje é o ALERTA de violação (ADR-0065
 *   — EPI presente é telemetria, não alerta). O rol mostra o que existe.
 * · Sem contadores derivados de nada: o total vem do backend, no MESMO recorte
 *   que a lista pede.
 * · Sem miniatura por linha: a URL de evidência é ASSINADA uma a uma
 *   (`/alerts/<id>/snapshot`) e 20 linhas seriam 20 requisições por página —
 *   é o caminho conhecido para o 429. A evidência está no evento, a um clique.
 */
import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle, Bell, CheckCheck } from 'lucide-react'
import { Link, useNavigate } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'

import { useAuth } from '../../hooks/useAuth'
import { api } from '../../services/api'
import {
  CHAVE_PENDENTES,
  dataHoraLocal,
  haQuantoTempo,
  marcarLidas,
  marcarTodasLidas,
  quandoAconteceu,
  rotaDoEvento,
  rotuloDasClasses,
  type Notificacao,
  type PaginaNotificacoes,
} from '../../services/notificacoes'
import { ConfirmDialog } from '../../components/ui/ConfirmDialog/ConfirmDialog'
import { useToast } from '../../components/ui/Toast/useToast'
import { rotaNova } from '../RotasNovas'
import * as s from './Notificacoes.css'

const POR_PAGINA = 20

type Aba = 'nao-lidas' | 'todas'

const ABAS: { id: Aba; rotulo: string }[] = [
  { id: 'nao-lidas', rotulo: 'Não lidas' },
  { id: 'todas', rotulo: 'Todas' },
]

export function Notificacoes() {
  const { can } = useAuth()
  const toast = useToast()
  const navegar = useNavigate()
  const clienteQuery = useQueryClient()

  const [aba, setAba] = useState<Aba>('nao-lidas')
  const [pagina, setPagina] = useState(1)
  const [dados, setDados] = useState<PaginaNotificacoes | null>(null)
  const [carregando, setCarregando] = useState(true)
  const [erro, setErro] = useState<string | null>(null)
  const [confirmando, setConfirmando] = useState(false)
  const [marcandoTodas, setMarcandoTodas] = useState(false)

  const podeLer = can('alerts:read')

  const carregar = useCallback(async () => {
    setCarregando(true)
    setErro(null)
    const p = new URLSearchParams({
      kind: 'violation',
      per_page: String(POR_PAGINA),
      page: String(pagina),
    })
    // "Todas" NÃO manda `acknowledged`: o backend trata ausência como "os dois
    // estados". Mandar `acknowledged=true` traria só as lidas — que é outra
    // coisa, e é o histórico pela metade.
    if (aba === 'nao-lidas') p.set('acknowledged', 'false')
    try {
      const r = await api.get<{ data?: PaginaNotificacoes }>(`/alerts?${p}`)
      const d = r?.data ?? (r as unknown as PaginaNotificacoes | undefined)
      setDados({
        alerts: d?.alerts ?? [],
        total: d?.total ?? 0,
        total_situacoes: d?.total_situacoes,
        page: d?.page ?? pagina,
        pages: d?.pages ?? 1,
      })
    } catch {
      setDados(null)
      setErro('Não foi possível carregar as notificações.')
    } finally {
      setCarregando(false)
    }
  }, [aba, pagina])

  useEffect(() => {
    if (podeLer) void carregar()
  }, [carregar, podeLer])

  /** Abrir = marcar como lida + ir ao evento (pedido do dono). */
  const abrir = (n: Notificacao) => {
    if (!n.acknowledged) {
      marcarLidas([n.id])
        .then(() => clienteQuery.invalidateQueries({ queryKey: CHAVE_PENDENTES }))
        .catch(() => toast.error('Não foi possível marcar a notificação como lida'))
    }
    navegar(rotaDoEvento(rotaNova('/epi/eventos'), n))
  }

  const marcarTodas = async () => {
    setMarcandoTodas(true)
    try {
      const quantas = await marcarTodasLidas({ kind: 'violation' })
      await clienteQuery.invalidateQueries({ queryKey: CHAVE_PENDENTES })
      await carregar()
      // Número do BACKEND (linhas que mudaram de estado). A página mostra 20;
      // a marcação alcança todas as pendentes do recorte.
      toast.success(
        quantas === 1
          ? '1 notificação marcada como lida'
          : `${quantas} notificações marcadas como lidas`,
      )
    } catch {
      toast.error('Não foi possível marcar todas como lidas')
    } finally {
      setMarcandoTodas(false)
      setConfirmando(false)
    }
  }

  if (!podeLer) {
    return (
      <div className={s.painelCentral}>
        <AlertTriangle size={36} strokeWidth={1.5} aria-hidden="true" />
        <span className={s.painelTitulo}>Sem permissão</span>
        <span className={s.painelTexto}>
          Ver notificações exige permissão para consultar eventos. Peça a quem
          administra o seu acesso.
        </span>
        <Link className={s.linkSaida} to={rotaNova('/epi/dashboard')}>
          Ir para o painel
        </Link>
      </div>
    )
  }

  const linhas = dados?.alerts ?? []
  const totalPaginas = Math.max(1, dados?.pages ?? 1)

  return (
    <div className={s.raiz}>
      <div className={s.cabecalho}>
        <div>
          <h1 className={s.titulo}>Notificações</h1>
          <p className={s.subtitulo}>
            Todo evento de violação que o sistema registrou — o que você já leu e
            o que ainda não.
          </p>
        </div>
        <button
          type="button"
          className={s.botaoPrimario}
          onClick={() => setConfirmando(true)}
          disabled={marcandoTodas}
        >
          <CheckCheck size={15} />
          Marcar todas como lidas
        </button>
      </div>

      <div className={s.abas} role="tablist" aria-label="Filtro de leitura">
        {ABAS.map((a) => (
          <button
            key={a.id}
            type="button"
            role="tab"
            aria-selected={aba === a.id}
            className={aba === a.id ? `${s.aba} ${s.abaAtiva}` : s.aba}
            onClick={() => {
              setAba(a.id)
              setPagina(1)
            }}
          >
            {a.rotulo}
          </button>
        ))}
        <span className={s.espacador} />
        {dados && (
          <span className={s.contagem}>
            {dados.total.toLocaleString('pt-BR')}{' '}
            {dados.total === 1 ? 'notificação' : 'notificações'}
          </span>
        )}
      </div>

      {carregando && <p className={s.aviso}>Carregando…</p>}

      {erro && !carregando && (
        <div className={s.painelCentral}>
          <AlertTriangle size={32} strokeWidth={1.5} aria-hidden="true" />
          <span className={s.painelTitulo}>{erro}</span>
          <button type="button" className={s.botaoSecundario} onClick={() => void carregar()}>
            Tentar de novo
          </button>
        </div>
      )}

      {!carregando && !erro && linhas.length === 0 && (
        <div className={s.painelCentral}>
          <Bell size={32} strokeWidth={1.5} aria-hidden="true" />
          <span className={s.painelTitulo}>
            {aba === 'nao-lidas' ? 'Nenhuma notificação por ler' : 'Nenhuma notificação registrada'}
          </span>
          <span className={s.painelTexto}>
            {aba === 'nao-lidas'
              ? 'Tudo o que chegou já foi lido. O histórico continua na aba "Todas".'
              : 'Nada foi registrado ainda neste recorte.'}
          </span>
          <Link className={s.linkSaida} to={rotaNova('/epi/eventos')}>
            Ver a tela de eventos
          </Link>
        </div>
      )}

      {!carregando && !erro && linhas.length > 0 && (
        <ul className={s.lista}>
          {linhas.map((n) => (
            <li key={n.id}>
              <button
                type="button"
                className={n.acknowledged ? s.linha : `${s.linha} ${s.linhaNaoLida}`}
                onClick={() => abrir(n)}
                aria-label={`Abrir evento de ${n.camera_name ?? 'câmera'}: ${rotuloDasClasses(n.violations)}`}
              >
                <span
                  className={n.acknowledged ? s.marcador : `${s.marcador} ${s.marcadorNaoLido}`}
                  aria-hidden="true"
                />
                <span className={s.corpo}>
                  <span className={s.camera}>{n.camera_name ?? 'Câmera'}</span>
                  <span className={s.classes}>{rotuloDasClasses(n.violations)}</span>
                  {/* Data e hora LOCAIS por extenso — o relativo sozinho ("há 6d")
                      não serve para quem precisa registrar o que aconteceu. */}
                  <span className={s.quando}>
                    {dataHoraLocal(quandoAconteceu(n))} · {haQuantoTempo(quandoAconteceu(n))}
                  </span>
                </span>
                <span className={n.acknowledged ? s.selo : `${s.selo} ${s.seloNova}`}>
                  {n.acknowledged ? 'Lida' : 'Não lida'}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}

      {!carregando && !erro && totalPaginas > 1 && (
        <div className={s.paginacao}>
          <button
            type="button"
            className={s.botaoSecundario}
            disabled={pagina <= 1}
            onClick={() => setPagina((p) => Math.max(1, p - 1))}
          >
            ← Anteriores
          </button>
          <span className={s.contagem}>
            Página {pagina} de {totalPaginas}
          </span>
          <button
            type="button"
            className={s.botaoSecundario}
            disabled={pagina >= totalPaginas}
            onClick={() => setPagina((p) => p + 1)}
          >
            Mais antigas →
          </button>
        </div>
      )}

      <p className={s.rodape}>
        &quot;Lida&quot; é o reconhecimento do evento, e vale para a equipe inteira:
        o produto não guarda leitura por pessoa. Quando alguém lê, sai da fila de
        todos — é o mesmo estado que a tela de{' '}
        <Link className={s.linkInterno} to={rotaNova('/epi/eventos')}>
          Eventos
        </Link>{' '}
        mostra como &quot;Reconhecido&quot;.
      </p>

      <ConfirmDialog
        open={confirmando}
        onClose={() => setConfirmando(false)}
        onConfirm={() => void marcarTodas()}
        loading={marcandoTodas}
        variant="primary"
        title="Marcar todas como lidas?"
        description="Todas as notificações de violação ainda pendentes ficarão marcadas como lidas — para a equipe inteira, não só para você. Elas continuam no histórico, na aba Todas."
        confirmLabel="Marcar todas"
      />
    </div>
  )
}
