/**
 * PainelEvidencia — julgar o evento SEM SAIR DA LISTA (`/epi/eventos`).
 *
 * O pedido do dono, nas palavras dele: "acesso rápido à evidência para
 * aprovar, uma tela igual à da validação daquele momento — foto do frame
 * inteiro e zoom — para aprovar ou não aprovar". Hoje isso custava sair da
 * lista, julgar no detalhe e voltar — e voltar recarrega a lista na página 1,
 * perdendo a posição de quem estava no meio de 20 linhas.
 *
 * ─────────────────────────────────────────────────────────────────────────
 * O QUE ESTE PAINEL REUSA, e por que não podia ser cópia
 *
 * · **Lupa** — `useLupa()`, a mesma cola de roda/pinça/teclado que
 *   `EventoDetalhe.tsx` usa, sobre a mesma matemática pura de
 *   `pages/epi/lupaEvidencia.ts`. Dois zooms copiados divergiriam no primeiro
 *   ajuste do limite de pan, e o defeito só apareceria com a orelha ampliada
 *   a 8× — onde ninguém olha duas vezes.
 * · **Projeção da caixa** — `caixaEmPorcento()` e `BBOX_PIXELS`, exportados
 *   por `EventoDetalhe.tsx`. Unidade ausente ou estranha = origem
 *   desconhecida: nenhuma caixa é desenhada, nunca uma caixa mentirosa.
 * · **Estilo do palco** — as classes do próprio `EventoDetalhe.css`. O dono
 *   pediu "uma tela IGUAL à da validação"; recriar o palco aqui com outros
 *   valores entregaria "parecida", que é outra coisa.
 * · **Motivo estruturado** — `MOTIVOS_VERIFICACAO`, a mesma lista fechada de
 *   `Verificacao.tsx`, obrigatória para rejeitar. É ela que ensina a
 *   recalibração; "erramos" sozinho não ensina nada.
 * · **409** — quem julga não é dono da corrida: outra pessoa pode ter julgado
 *   entre a carga da lista e este clique. O 409 é INFORMAÇÃO (a mensagem do
 *   servidor diz quem e quando) e a fila AVANÇA, igual ao bloco 4 de
 *   `Verificacao.tsx`. Quem trata é `Eventos.tsx`, que devolve `'conflito'`.
 *
 * ─────────────────────────────────────────────────────────────────────────
 * POR QUE UMA SEGUNDA REQUISIÇÃO AO ABRIR
 *
 * `GET /api/alerts` (a lista) devolve `evidence_key`, mas NÃO a URL assinada
 * — só o detalhe de UM alerta assina. Por isso a lista não desenha miniatura
 * (seria um N+1 por rolagem, ver o cabeçalho de `Eventos.tsx`) e por isso
 * este painel busca `GET /api/alerts/<id>` ao ABRIR: uma requisição por
 * evento que a pessoa de fato escolheu olhar, não vinte por página.
 *
 * O QUE ELE NÃO AFIRMA: o detalhe não devolve `verified_by`, então quem
 * julgou não é dito aqui — a coluna VEREDITO da própria lista já carrega
 * essa leitura (`vereditoHumano`), e é dela que o selo abaixo sai.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  AlertTriangle, ArrowUpRight, Check, ChevronLeft, ChevronRight, ImageOff,
  Maximize2, Minus, Plus, SearchX, X,
} from 'lucide-react'
import { Link } from 'react-router-dom'

import { useToast } from '../../components/ui/Toast/useToast'
import { vereditoHumano } from '../../components/shared/VereditoHumano'
import { useAuth } from '../../hooks/useAuth'
import { useModuleClasses } from '../../hooks/useModuleClasses'
import { ESCALA_MIN } from '../../pages/epi/lupaEvidencia'
import { api } from '../../services/api'
import { confiancaInternaOuCliente } from '../../services/confidenceDisplay'
import {
  MOTIVOS_VERIFICACAO, labelForVerificationReason, type MotivoVerificacao,
} from '../../utils/labels'
import { rotaNova } from '../RotasNovas'
import * as e from './EventoDetalhe.css'
import * as s from './Eventos.css'
import { useLupa } from './useLupa'
import { estiloDaCaixa, referenciaDaCaixa } from '../../services/bboxProjecao'

const MODULO = 'epi'

type Bbox = [number, number, number, number]

/** O que este painel precisa de UMA linha da lista. Estrutural de propósito:
 *  `Eventos.tsx` passa a linha inteira, sem cast e sem import circular. */
export interface EventoJulgavel {
  id: string
  camera_name?: string
  violations: Array<{ class: string; confidence?: number }>
  created_at: string
  timestamp?: string
  verification_verdict?: string | null
  verified_by?: string | null
  verification_reason?: string | null
}

/** Resultado do veredito, decidido por quem fala com o servidor
 *  (`Eventos.tsx`): `'conflito'` é 409 — outra pessoa julgou primeiro, e a
 *  fila avança do mesmo jeito. */
export type ResultadoVeredito = 'ok' | 'conflito' | 'erro'

/** Só o que a projeção do detalhe entrega e este painel desenha. */
interface Evidencia {
  evidence_url: string | null
  violations: Array<{ class: string; confidence?: number; bbox?: Bbox; bbox_unidade?: string }>
}

type FaseEvidencia = 'carregando' | 'carregada' | 'falhou'

export interface PainelEvidenciaProps {
  evento: EventoJulgavel
  /** Posição na ordem VISÍVEL da página (1-based) — a mesma que a pessoa vê. */
  posicao: number
  totalVisivel: number
  aoAnterior: () => void
  aoProximo: () => void
  aoFechar: () => void
  aoJulgar: (
    id: string,
    verdict: 'approve' | 'reject',
    reason?: string,
  ) => Promise<ResultadoVeredito>
}

export function PainelEvidencia({
  evento, posicao, totalVisivel, aoAnterior, aoProximo, aoFechar, aoJulgar,
}: PainelEvidenciaProps) {
  const { can, isSuperAdmin } = useAuth()
  const { classLabel } = useModuleClasses(MODULO)
  const toast = useToast()

  const [evidencia, setEvidencia] = useState<Evidencia | null>(null)
  const [fase, setFase] = useState<FaseEvidencia>('carregando')
  /** A URL assinada vale 1 hora e depois o link responde negativa: a imagem
   *  falha CALADA e o palco fica preto. Sem este estado o operador lê "evento
   *  sem evidência", que é mentira. */
  const [imagemFalhou, setImagemFalhou] = useState(false)
  const [tentativa, setTentativa] = useState(0)
  /** Dimensões do frame ORIGINAL — só existem depois que a imagem carrega. */
  const [natural, setNatural] = useState<{ w: number; h: number } | null>(null)

  const [motivo, setMotivo] = useState<MotivoVerificacao | ''>('')
  const [motivoFaltando, setMotivoFaltando] = useState(false)
  const [enviando, setEnviando] = useState(false)

  const {
    lupa, refPalco, noPiso, noTeto, ampliar, reenquadrar,
    aoDescerPonteiro, aoMoverPonteiro, aoSoltarPonteiro, aoDuploClique, aoTeclar,
  } = useLupa()

  const gavetaRef = useRef<HTMLDivElement>(null)
  const podeJulgar = can('alerts:feedback')
  const veredito = vereditoHumano(evento.verification_verdict, evento.verified_by)
  const capturadoEm = evento.timestamp ?? evento.created_at

  // Evento novo = evidência nova, enquadramento novo, motivo zerado. O motivo
  // do evento anterior vazando para o próximo grava uma justificativa que
  // ninguém deu para AQUELE evento (mesma regra de `Verificacao.tsx`).
  useEffect(() => {
    let vivo = true
    setFase('carregando')
    setEvidencia(null)
    setImagemFalhou(false)
    setNatural(null)
    setMotivo('')
    setMotivoFaltando(false)
    reenquadrar()
    api
      .get<{ data?: { alert: Evidencia } }>(`/alerts/${evento.id}`)
      .then((res) => {
        if (!vivo) return
        const a = res?.data?.alert
        setEvidencia(a ? { evidence_url: a.evidence_url ?? null, violations: a.violations ?? [] } : null)
        setFase(a ? 'carregada' : 'falhou')
      })
      .catch(() => {
        if (!vivo) return
        setEvidencia(null)
        setFase('falhou')
      })
    return () => { vivo = false }
  }, [evento.id, reenquadrar, tentativa])

  // Abrir a gaveta põe o foco nela: sem isso o Esc e as teclas da lupa
  // continuariam indo para a tabela atrás, e quem navega por teclado ficaria
  // preso na lista com um painel aberto por cima.
  useEffect(() => { gavetaRef.current?.focus() }, [])

  const decidir = useCallback(
    async (verdict: 'approve' | 'reject') => {
      if (!podeJulgar || enviando) return
      // Motivo é obrigatório para REJEITAR — é o que alimenta a recalibração.
      // Para confirmar é opcional: não bloqueia, e só viaja se foi escolhido.
      if (verdict === 'reject' && !motivo) {
        setMotivoFaltando(true)
        toast.error(
          'Selecione um motivo para rejeitar',
          'O motivo é o que alimenta a recalibração do modelo.',
        )
        return
      }
      setEnviando(true)
      const r = await aoJulgar(evento.id, verdict, motivo || undefined)
      setEnviando(false)
      if (r === 'erro') return
      if (r === 'ok') toast.success(verdict === 'approve' ? 'Evento confirmado' : 'Evento rejeitado')
      // 'conflito' (409) também avança: quem já julgou, julgou — e quem opera
      // não pode ficar preso numa linha que não é mais dele. A mensagem de
      // quem/quando já foi mostrada por quem falou com o servidor.
      aoProximo()
    },
    [aoJulgar, aoProximo, enviando, evento.id, motivo, podeJulgar, toast],
  )

  const url = evidencia?.evidence_url ?? null
  // Projetável = a tela sabe contra QUE quadro a caixa foi medida. A do edge
  // é medida no streammux (1280×720) e a imagem vem do RTSP (1920×1080);
  // dividir pelo tamanho da imagem punha a caixa a 2/3 do lugar certo.
  const desenhaveis = (evidencia?.violations ?? []).filter(
    (v) => referenciaDaCaixa(v, { w: 1, h: 1 }) !== null,
  )

  return (
    <div className={s.painelFundo} onClick={aoFechar} role="presentation">
      <div
        ref={gavetaRef}
        className={s.gaveta}
        role="dialog"
        aria-modal="true"
        aria-label={`Evidência do evento de ${evento.camera_name ?? 'câmera'}`}
        tabIndex={-1}
        onClick={(ev) => ev.stopPropagation()}
        onKeyDown={(ev) => { if (ev.key === 'Escape') { ev.stopPropagation(); aoFechar() } }}
      >
        <div className={s.gavetaTopo}>
          <span className={s.gavetaTitulo}>
            {evento.violations?.length
              ? evento.violations.map((v) => classLabel(v.class)).join(', ')
              : 'Evento sem classe registrada'}
          </span>
          <span className={s.espacador} />
          <button className={s.gavetaFechar} onClick={aoFechar} aria-label="Fechar evidência">
            <X size={16} strokeWidth={1.7} aria-hidden="true" />
          </button>
        </div>

        <div className={s.gavetaCorpo}>
          {/* Palco: mesma estrutura da tela de validação — camada transformada
              envolvendo imagem E caixas, para a marcação escalar junto. */}
          <div
            ref={refPalco}
            className={e.palco}
            tabIndex={0}
            role="group"
            aria-label="Frame da evidência. Roda do mouse ou + e − para ampliar, setas para deslocar, 0 para voltar ao enquadramento inteiro."
            onPointerDown={aoDescerPonteiro}
            onPointerMove={aoMoverPonteiro}
            onPointerUp={aoSoltarPonteiro}
            onPointerCancel={aoSoltarPonteiro}
            onDoubleClick={aoDuploClique}
            onKeyDown={aoTeclar}
            style={{ cursor: lupa.escala > ESCALA_MIN ? 'grab' : 'zoom-in' }}
          >
            <span className={e.selo.esquerda}>
              {(evento.camera_name ?? '—').toUpperCase()}
            </span>
            <span className={e.selo.direita}>{new Date(capturadoEm).toLocaleString('pt-BR')}</span>

            {url && !imagemFalhou ? (
              <div
                className={e.camada}
                style={{ transform: `translate(${lupa.x}px, ${lupa.y}px) scale(${lupa.escala})` }}
              >
                <div className={e.quadro}>
                  <img
                    className={e.imagem}
                    src={url}
                    alt="Frame da evidência"
                    draggable={false}
                    onError={() => setImagemFalhou(true)}
                    onLoad={(ev) => {
                      const img = ev.currentTarget
                      if (img.naturalWidth > 0 && img.naturalHeight > 0) {
                        setNatural({ w: img.naturalWidth, h: img.naturalHeight })
                      }
                    }}
                  />
                  {natural && desenhaveis.map((v, i) => (
                    <div
                      key={i}
                      data-testid="caixa-violacao"
                      className={e.caixa}
                      style={{
                        ...(estiloDaCaixa(v, natural) ?? {}),
                        // contra-escala: a 8× uma borda de 2,5px come a evidência
                        borderWidth: `${2.5 / lupa.escala}px`,
                        borderRadius: `${4 / lupa.escala}px`,
                      }}
                    >
                      <span className={e.caixaRotulo} style={{ transform: `scale(${1 / lupa.escala})` }}>
                        {classLabel(v.class)}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            ) : fase === 'carregando' ? (
              <div className={e.semImagem}>Carregando a evidência…</div>
            ) : url ? (
              // A evidência EXISTE no servidor; foi o link que não abriu.
              // Dizer "sem imagem" aqui apagaria a prova do acontecido.
              <div className={e.semImagem} role="alert">
                <ImageOff size={30} strokeWidth={1.5} aria-hidden="true" />
                Não foi possível carregar a imagem desta evidência.
                <button className={s.botao} onClick={() => setTentativa((t) => t + 1)}>
                  Gerar novo link e tentar de novo
                </button>
              </div>
            ) : (
              <div className={e.semImagem}>
                <SearchX size={30} strokeWidth={1.5} aria-hidden="true" />
                {fase === 'falhou'
                  ? 'Não foi possível buscar a evidência deste evento.'
                  : 'Sem imagem de evidência para este evento'}
              </div>
            )}
          </div>

          <div className={e.barraLupa}>
            <button className={e.botaoLupa} aria-label="Ampliar" onClick={() => ampliar(1.5)} disabled={noTeto}>
              <Plus size={15} aria-hidden="true" />
            </button>
            <button className={e.botaoLupa} aria-label="Reduzir" onClick={() => ampliar(1 / 1.5)} disabled={noPiso}>
              <Minus size={15} aria-hidden="true" />
            </button>
            <button className={e.botaoLupa} onClick={reenquadrar} disabled={noPiso}>
              <Maximize2 size={14} aria-hidden="true" /> Frame inteiro
            </button>
            <span className={e.dicaLupa}>
              {lupa.escala.toFixed(1).replace('.', ',')}× · roda amplia · arrastar desloca
            </span>
          </div>

          {/* Câmera e hora já estão CARIMBADAS sobre o frame (os dois selos do
              palco, como na tela de validação) — repeti-las numa ficha aqui
              embaixo seria só ruído. Sobra o que os selos não dizem. */}
          <div className={s.gavetaDados}>
            <span className={s.gavetaRotulo}>Confiança</span>
            <span className={s.confianca}>
              {confiancaInternaOuCliente(evento.violations?.[0]?.confidence, isSuperAdmin)}
            </span>
          </div>

          {veredito !== 'nao-revisado' ? (
            <div className={s.gavetaVeredito}>
              <span className={`${s.selo} ${s.corVeredito[veredito]}`}>
                {veredito === 'procedente' ? 'Procedente' : 'Falso positivo'}
              </span>
              {evento.verification_reason && (
                <span className={s.gavetaRotulo}>
                  {labelForVerificationReason(evento.verification_reason)}
                </span>
              )}
              {/* Não promete "passe ao próximo": no último evento da página o
                  botão está desabilitado, e prometer o que a tela não faz é a
                  mesma família de mentira que um número inventado. */}
              <span className={s.gavetaAjuda}>
                Uma pessoa já julgou este evento. Para rever a marcação, abra a tela inteira.
              </span>
            </div>
          ) : podeJulgar ? (
            <div className={s.gavetaVeredito}>
              <span className={s.gavetaAjuda}>
                O veredito é sobre a DETECÇÃO: ela é procedente ou o sistema errou. Não é
                o mesmo que <strong>Reconhecer</strong>, que só registra que alguém viu.
              </span>
              <label className={s.gavetaRotulo} htmlFor="motivo-painel-evidencia">
                Motivo (obrigatório para rejeitar)
              </label>
              <select
                id="motivo-painel-evidencia"
                className={motivoFaltando ? `${s.filtro} ${s.filtroErro}` : s.filtro}
                value={motivo}
                onChange={(ev) => {
                  setMotivo(ev.target.value as MotivoVerificacao | '')
                  setMotivoFaltando(false)
                }}
              >
                <option value="">Selecione um motivo…</option>
                {MOTIVOS_VERIFICACAO.map((m) => (
                  <option key={m.valor} value={m.valor}>{m.rotulo}</option>
                ))}
              </select>
              {motivoFaltando && (
                <span className={s.gavetaErro}>Selecione um motivo para rejeitar.</span>
              )}
              <div className={s.gavetaBotoes}>
                <button
                  className={s.botaoPrimario}
                  disabled={enviando}
                  onClick={() => void decidir('approve')}
                  title="A detecção está correta (procedente)"
                >
                  <Check size={15} strokeWidth={1.7} aria-hidden="true" /> Confirmar
                </button>
                <button
                  className={s.botao}
                  disabled={enviando}
                  onClick={() => void decidir('reject')}
                  title="A detecção está errada (falso positivo)"
                >
                  <X size={15} strokeWidth={1.7} aria-hidden="true" /> Falso positivo
                </button>
              </div>
            </div>
          ) : (
            <div className={s.gavetaVeredito}>
              <AlertTriangle size={16} strokeWidth={1.7} aria-hidden="true" />
              <span className={s.gavetaAjuda}>
                Você não tem permissão para julgar detecções. Peça a quem administra o seu acesso.
              </span>
            </div>
          )}
        </div>

        <div className={s.gavetaRodape}>
          <button
            className={s.botao}
            onClick={aoAnterior}
            disabled={posicao <= 1}
            aria-label="Evento anterior"
          >
            <ChevronLeft size={16} strokeWidth={1.7} aria-hidden="true" />
          </button>
          <span className={s.overlineLegenda}>{posicao} de {totalVisivel} nesta página</span>
          <button
            className={s.botao}
            onClick={aoProximo}
            disabled={posicao >= totalVisivel}
            aria-label="Próximo evento"
          >
            <ChevronRight size={16} strokeWidth={1.7} aria-hidden="true" />
          </button>
          <span className={s.espacador} />
          {/* Caminho de saída que leva a lugar DIFERENTE da lista: a tela
              inteira é onde se corrige a marcação e se escreve motivo livre. */}
          <Link className={s.botao} to={rotaNova(`/epi/eventos/${evento.id}`)}>
            Abrir tela inteira <ArrowUpRight size={14} strokeWidth={1.7} aria-hidden="true" />
          </Link>
        </div>
      </div>
    </div>
  )
}
