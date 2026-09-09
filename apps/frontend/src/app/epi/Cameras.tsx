/**
 * EPI Câmeras & Sites — `/epi/cameras` (F3 da migração).
 *
 * Desenho: `EPI Câmeras.dc.html`. Ele manda: título "Câmeras & Sites", barra de
 * abas, CTA "Adicionar câmera", split lista(330)+detalhe, prévia 16:9, cinco
 * campos, painel "TESTE DE CONEXÃO · PASSO A PASSO", aba Sites (modo do site +
 * saúde do Jetson + sincronização) e aba Saúde (tabela por câmera).
 *
 * ── ABA "ESCOPO" (delta §2 item 8) ──────────────────────────────────────────
 * O handoff NÃO desenhou esta aba — ela é o `CameraModelScope` que a develop
 * ganhou depois do snapshot do design, e o `DELTA-PRE-MIGRACAO.md` a endereça
 * explicitamente a esta tela ("implementar-na-migração (F3 Câmeras)"). Ela
 * entra como QUARTA aba da barra que o desenho já prevê — a barra é uma lista
 * (`{{ abas }}`), não três botões fixos; então isto estende o desenho, não
 * inventa uma seção nova. O contrato duramente conquistado NÃO foi copiado:
 * `classesDoModelo`, `moduloDaCamera` e `montarConfig` são importados do
 * componente original, inclusive a rota EM LOTE (`GET
 * /cameras/model-config?module=<m>`, uma chamada por MÓDULO e não por câmera —
 * a versão por câmera estourava o pool da API nas 28 câmeras da RVB).
 *
 * ── ABA "DESEMPENHO" (5ª aba — `docs/design/handoff-v2/Main.dc.html`) ──────
 * Desenho próprio (screen-label "EPI Câmeras · Desempenho"), diferente do
 * `EPI Câmeras.dc.html` que desenhou as quatro primeiras abas. Ajusta FPS/
 * qualidade/coleta da câmera SELECIONADA (reusa o estado de `selecionadaId`
 * da aba Câmeras) e mostra a saúde do equipamento via `getHealthContext`.
 * `CameraFpsConfig.tsx` (legado) tem a MESMA função só que embutida no card
 * de edição do front antigo — a lógica foi portada (limiares de sobrecarga,
 * condição de desalinhamento coleta×operação), zero import dele aqui.
 * Duas divergências prancha↔backend, sem invenção de dado:
 *   1. "Teto medido do equipamento" (fps): `CameraHealthContext` não tem esse
 *      campo — só `fps_demand_total`/`cameras_active_count`. Frase omitida.
 *   2. Gate de `cameras:configure`: a tela já tinha o padrão (aba Sites/
 *      Escopo) de ficar visível com os controles desabilitados + nota, em vez
 *      de bloquear a aba inteira — foi esse padrão que se seguiu aqui.
 *
 * ── MULTI-TENANCY ───────────────────────────────────────────────────────────
 * Nada aqui escolhe schema. `cameraService`/`edgeService` chamam as MESMAS
 * rotas que o front atual chama, e é o backend que decide `public.cameras`
 * (com `tenant_id`) ou `{tenant_schema}.cameras` (sem) por rota. Cross-tenant
 * volta 404 (C-01) e cai no estado de erro com "tentar de novo".
 *
 * ── PERMISSÕES (as reais de `core/permissions.py`) ──────────────────────────
 *   cameras:read       ler a tela (já é o gate da rota na nav)
 *   cameras:write      Adicionar câmera · Editar · Arquivar/Desarquivar
 *   cameras:test       Testar conexão
 *   cameras:control    Iniciar/Parar monitoramento
 *   cameras:configure  modo de processamento do site · salvar escopo
 *   cameras:delete     Excluir câmera
 *
 * ── ARQUIVAR ≠ EXCLUIR (duas ações, de propósito) ───────────────────────────
 * `Arquivar` tira a câmera do reconhecimento e VOLTA (`Desarquivar`).
 * `Excluir` tira a câmera do sistema: ela some daqui, do Ao Vivo, do grid e
 * do config que o edge baixa, e não há botão de volta — quem quiser de novo
 * cadastra de novo. Antes só existia Arquivar, e a câmera arquivada seguia
 * aparecendo no Ao Vivo para sempre; era esse o defeito.
 *
 * `DELETE /cameras/<id>` NÃO é mais `DELETE FROM cameras`: virou exclusão
 * lógica (`cameras.deleted_at`, migration 138). O DELETE físico apagaria
 * alerta e evidência por CASCATA — registro histórico, com possível valor
 * legal — e travaria por FK em qualquer câmera com frame de treino. Então a
 * câmera sai de todas as telas e o acervo fica.
 *
 * ── PARA O DESIGN (o que o desenho não cobre e ficou no mínimo da identidade)
 *  1. Ações do detalhe (Editar · Iniciar/Parar · Operações · Cenário ·
 *     Arquivar): o desenho só desenhou "Testar conexão", mas isto é CRUD real
 *     do front atual e a rota `/epi/cameras` é a MESMA nos dois fronts — sem
 *     estas ações elas somem do produto. Linha única de botões secundários.
 *  2. Cadastro/edição de câmera: sem tela no handoff. Reusa os wizards atuais
 *     (`CameraOnboardingWizard`/`CameraWizard`) — inventar um fluxo de cadastro
 *     sem desenho seria pior que reusar o que já funciona.
 *  3. "UPTIME 7D" (aba Saúde): NÃO existe endpoint de uptime por câmera. Fica
 *     "—". Uptime existe por SITE (`heartbeat-summary`), não por câmera.
 *  4. "FPS" (aba Saúde) virou "FPS ALVO": o que a API dá por câmera é
 *     `fps_target` (configurado), não FPS medido. Chamar de "FPS" mentiria.
 *  5. "FILA 0" e "Reiniciar agente" (aba Sites): sem fonte. `queue_depth` vem
 *     do backend mas o adaptador do `edgeService` não o expõe, e não há rota de
 *     reinício do agente. Botão morto é pior que botão ausente.
 *  6. "ms" por passo do teste de conexão: `TestResult.checks` não traz tempo.
 *  7. Prévia: usa o snapshot de triagem (`/cameras/<id>/snapshot`), que não
 *     acende a câmera. Vídeo ao vivo é a tela Ao Vivo.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  ArrowRight,
  CircleAlert,
  CircleCheck,
  CircleSlash,
  Frame,
  Gauge,
  Pencil,
  Play,
  Plug,
  Plus,
  RefreshCw,
  Settings2,
  Square,
  Trash2,
  TriangleAlert,
  Video,
  type LucideIcon,
} from 'lucide-react'

/**
 * REUSO DELIBERADO, não dívida de migração (triagem de 05/09).
 *
 * Os três são `INFRA` no MANIFESTO-FRONT-ANTIGO ("não é tela"), e a Fase 3 só
 * apaga `MIGRADO` — a demolição do front antigo não os leva junto.
 *
 * · `ConfirmDialog` é primitiva do design system (`components/ui/`, 9
 *   importadores nos dois fronts). Duplicar em `app/` daria dois diálogos de
 *   confirmação que divergem no primeiro ajuste.
 * · Os dois wizards são o cadastro/edição de câmera REAL, e o front antigo
 *   ainda os usa (`CamerasPage`, `GridPanel`). Mover quebra o antigo; copiar
 *   fragmenta o único fluxo de cadastro que o cliente conhece. O handoff não
 *   desenhou tela de cadastro (item 2 do cabeçalho), então não há nem desenho
 *   para uma versão nova.
 *
 * A regra está travada em `src/test/migracao/fronteira-front-novo.test.ts`.
 */
import { ConfirmDialog } from '../../components/ui/ConfirmDialog/ConfirmDialog'
import { CameraOnboardingWizard } from '../../components/cameras/CameraOnboardingWizard'
import { CameraWizard } from '../../components/cameras/CameraWizard'
import {
  AvisoClassesMortas,
  ClasseChips,
  classesDoModelo,
  classesMortas,
  draftDoDeployment as rascunhoDo,
  draftVivo,
  mesmoConjunto,
  moduloDaCamera,
  montarConfig,
  motivoBloqueioSalvar,
  type ModelDeployment,
  type RegistryModel,
} from '../../components/training/CameraModelScope'
import { useAuth } from '../../hooks/useAuth'
import { useCameraSnapshot } from '../../hooks/useCameraSnapshot'
import { api } from '../../services/api'
import { nomeInternoOuCliente } from '../../services/modelDisplay'
import { cameraService, type CameraConfigPatch, type CameraPropagation, type TestResult } from '../../services/cameraService'
import { edgeService } from '../../services/edgeService'
import type { Camera } from '../../types'
import {
  DEPLOYMENT_MODE_LABELS,
  type CameraHealthContext,
  type DeploymentMode,
  type EdgeSite,
  type SiteHealth,
  type SiteStatus,
} from '../../types/edge'
import { rotaNova } from '../RotasNovas'
import { lk } from '../tokens/lk.css'
import { LogikosLoader } from '../shell/LogikosLoader'
import * as s from './Cameras.css'

type Aba = 'cameras' | 'sites' | 'saude' | 'escopo' | 'desempenho'

const ABAS: Array<{ id: Aba; rotulo: string }> = [
  { id: 'cameras', rotulo: 'Câmeras' },
  { id: 'sites', rotulo: 'Sites' },
  { id: 'saude', rotulo: 'Saúde' },
  { id: 'escopo', rotulo: 'Escopo' },
  { id: 'desempenho', rotulo: 'Desempenho' },
]

/** Estado = cor + ícone + palavra. Nunca só a cor: quem não distingue verde de
 * vermelho tem de conseguir operar a tela. */
interface Estado {
  tom: 'ok' | 'atencao' | 'nc' | 'neutro'
  palavra: string
  Icone: LucideIcon
}

export function estadoDaCamera(cam: Pick<Camera, 'is_active' | 'stream_status'>): Estado {
  if (cam.is_active === false) return { tom: 'neutro', palavra: 'ARQUIVADA', Icone: CircleSlash }
  const st = (cam.stream_status ?? '').toLowerCase()
  if (st === 'active' || st === 'online') return { tom: 'ok', palavra: 'ONLINE', Icone: CircleCheck }
  if (st === 'error') return { tom: 'nc', palavra: 'FALHA', Icone: CircleAlert }
  return { tom: 'atencao', palavra: 'PARADA', Icone: TriangleAlert }
}

const ESTADO_SITE: Record<SiteStatus, Estado> = {
  healthy: { tom: 'ok', palavra: 'EDGE SAUDÁVEL', Icone: CircleCheck },
  degraded: { tom: 'atencao', palavra: 'EDGE DEGRADADO', Icone: TriangleAlert },
  critical: { tom: 'nc', palavra: 'EDGE CRÍTICO', Icone: CircleAlert },
  offline: { tom: 'nc', palavra: 'EDGE OFFLINE', Icone: CircleSlash },
}

const MODO_DESCRICAO: Record<DeploymentMode, string> = {
  edge: 'A detecção roda no equipamento do site. A nuvem recebe evento e evidência.',
  hybrid: 'O site detecta e a nuvem também processa — modo de transição.',
  cloud: 'O vídeo sobe para a nuvem, que detecta. Sem equipamento no site.',
}

/** Passos do teste, na ordem em que o backend os executa (`TestResult.checks`). */
const PASSOS: Array<[keyof TestResult['checks'], string]> = [
  ['url_format', 'Formato do endereço'],
  ['host_reachable', 'Equipamento respondeu'],
  ['port_open', 'Porta aberta'],
  ['rtsp_response', 'Handshake RTSP'],
  ['stream_available', 'Vídeo disponível'],
]

const MARCA_PASSO: Record<string, string> = { ok: '✓', error: '✗', warning: '!', pending: '·' }
const TOM_PASSO: Record<string, Estado['tom']> = {
  ok: 'ok',
  error: 'nc',
  warning: 'atencao',
  pending: 'neutro',
}

function haQuanto(iso: string | null | undefined): string {
  if (!iso) return '—'
  const ms = Date.now() - new Date(iso).getTime()
  if (!Number.isFinite(ms) || ms < 0) return '—'
  const seg = Math.floor(ms / 1000)
  if (seg < 60) return `HÁ ${seg} S`
  const min = Math.floor(seg / 60)
  if (min < 60) return `HÁ ${min} MIN`
  const hor = Math.floor(min / 60)
  if (hor < 24) return `HÁ ${hor} H`
  return `HÁ ${Math.floor(hor / 24)} D`
}

/** Endereço sempre com a senha oculta — a API nunca devolve a senha, e a tela
 * não pode dar a impressão de que devolveria. */
function enderecoMascarado(cam: Camera): string {
  const base = `${cam.host || '...'}:${cam.port || 554}`
  return cam.username ? `rtsp://${cam.username}:****@${base}/…` : `rtsp://${base}/…`
}

function numero(v: number | null | undefined, sufixo = ''): string {
  return v === null || v === undefined ? '—' : `${v}${sufixo}`
}

// ── prévia ───────────────────────────────────────────────────────────────────

/** Prévia 16:9. Usa o snapshot de triagem — NÃO acende a câmera nem abre HLS.
 * `key={cameraId}` no chamador: o hook dispara uma vez por ativação. */
function Previa({ cameraId }: { cameraId: string }) {
  const { url, loading } = useCameraSnapshot(cameraId, true)
  if (url) return <img className={s.previaImagem} src={url} alt="Última imagem capturada da câmera" />
  return (
    <LogikosLoader
      variante="tile"
      estado={loading ? 'waiting' : 'idle'}
      rotulo={loading ? 'BUSCANDO IMAGEM' : 'SEM SINAL'}
      tamanho={36}
    />
  )
}

// ── escopo por câmera (delta §2 item 8) ──────────────────────────────────────

interface Envelope<T> { success: boolean; message?: string; data?: T }
interface ModelDetail {
  model: RegistryModel
  lineage: { dataset_version: { class_distribution?: Record<string, unknown> } | null }
}
// `Rascunho`/`rascunhoDo`/`mesmoConjunto` viviam duplicados aqui (cópia
// quase idêntica de `CameraModelScope.tsx`) — agora importados de lá
// (`draftDoDeployment as rascunhoDo`, `mesmoConjunto`). A forma continua
// igual: {modelId, classes}.
type Rascunho = { modelId: string; classes: string[] }

interface EscopoProps {
  cameras: Camera[]
  podeEditar: boolean
}

function AbaEscopo({ cameras, podeEditar }: EscopoProps) {
  const { isSuperAdmin } = useAuth()
  const ativas = useMemo(() => cameras.filter((c) => c.is_active !== false), [cameras])
  const [carregando, setCarregando] = useState(true)
  const [erro, setErro] = useState<string | null>(null)
  const [modelos, setModelos] = useState<RegistryModel[]>([])
  const [classesPorModelo, setClassesPorModelo] = useState<Record<string, string[]>>({})
  const [deployPorCamera, setDeployPorCamera] = useState<Record<string, ModelDeployment>>({})
  const [rascunhos, setRascunhos] = useState<Record<string, Rascunho>>({})
  const [salvando, setSalvando] = useState<string | null>(null)

  const carregar = useCallback(async () => {
    setCarregando(true)
    setErro(null)
    try {
      const listagem = await api.get<Envelope<{ models: RegistryModel[] }>>('/v1/models')
      const comArtefato = (listagem.data?.models ?? []).filter((m) => !!m.r2_onnx_key)
      // Uma chamada por MÓDULO distinto (hoje 1), NUNCA uma por câmera: a
      // versão por câmera estourava o pool de conexões da API nas 28 da RVB.
      const modulos = [...new Set(ativas.map(moduloDaCamera))]
      const [detalhes, porModulo] = await Promise.all([
        Promise.allSettled(comArtefato.map((m) => api.get<Envelope<ModelDetail>>(`/v1/models/${m.id}`))),
        Promise.all(
          modulos.map((m) =>
            api.get<Envelope<{ deployments: Record<string, ModelDeployment> }>>(
              `/cameras/model-config?module=${encodeURIComponent(m)}`,
            ),
          ),
        ),
      ])
      const porModelo: Record<string, string[]> = {}
      detalhes.forEach((r, i) => {
        const d = r.status === 'fulfilled' ? r.value.data : undefined
        // Sem catálogo de fallback: classe que o modelo não declara não é
        // inventada aqui — a tela diz que o modelo não declarou classes.
        porModelo[comArtefato[i].id] = classesDoModelo(d?.lineage?.dataset_version?.class_distribution, [])
      })
      const porCamera: Record<string, ModelDeployment> = {}
      porModulo.forEach((r, i) => {
        // Só a câmera CUJO módulo é este: uma câmera de 'quality' não herda o
        // deployment 'epi' de outra só porque veio no mesmo lote.
        const doModulo = new Set(ativas.filter((c) => moduloDaCamera(c) === modulos[i]).map((c) => c.id))
        for (const [cameraId, dep] of Object.entries(r.data?.deployments ?? {})) {
          if (dep && doModulo.has(cameraId)) porCamera[cameraId] = dep
        }
      })
      setModelos(comArtefato)
      setClassesPorModelo(porModelo)
      setDeployPorCamera(porCamera)
      // `draftVivo`, não `rascunhoDo`: classe gravada que o modelo não emite
      // sai do rascunho (fica visível à parte) em vez de viajar invisível em
      // todo Salvar — mesmo buraco que a aba do Estúdio tinha.
      setRascunhos(Object.fromEntries(ativas.map((c) => {
        const dep = porCamera[c.id]
        return [c.id, draftVivo(dep, (dep && porModelo[dep.model_id]) || [])]
      })))
    } catch (err) {
      // Sem isto a tabela caía no vazio e afirmava "nenhuma câmera" — falso
      // num tenant de 28 câmeras, e sem como tentar de novo.
      setErro(err instanceof Error ? err.message : 'Erro ao carregar o escopo por câmera')
    } finally {
      setCarregando(false)
    }
  }, [ativas])

  useEffect(() => { void carregar() }, [carregar])

  const mudar = (camId: string, patch: Partial<Rascunho>) =>
    setRascunhos((prev) => ({ ...prev, [camId]: { ...prev[camId], ...patch } }))

  async function salvar(cam: Camera) {
    const rascunho = rascunhos[cam.id]
    if (!podeEditar || !rascunho?.modelId || rascunho.classes.length === 0) return
    setSalvando(cam.id)
    try {
      const res = await api.post<Envelope<{ deployment: ModelDeployment }>>(
        `/cameras/${cam.id}/model-config`,
        {
          model_id: rascunho.modelId,
          module_code: moduloDaCamera(cam),
          config: montarConfig(deployPorCamera[cam.id]?.config, rascunho.classes),
        },
      )
      const novo = res.data?.deployment
      if (novo) {
        setDeployPorCamera((prev) => ({ ...prev, [cam.id]: novo }))
        mudar(cam.id, draftVivo(novo, classesPorModelo[novo.model_id] ?? []))
      }
    } catch (err) {
      setErro(err instanceof Error ? err.message : 'Erro ao salvar o escopo')
    } finally {
      setSalvando(null)
    }
  }

  if (carregando) return <LogikosLoader variante="tile" estado="waiting" rotulo="CARREGANDO ESCOPO" tamanho={48} />

  if (erro) {
    return (
      <div className={s.centro}>
        <CircleAlert size={36} strokeWidth={1.7} className={s.tom.nc} />
        <span className={s.centroTitulo}>Não foi possível carregar o escopo</span>
        <span className={s.centroMono}>{erro}</span>
        <span className={s.centroTexto}>Isto NÃO quer dizer &quot;nenhuma câmera&quot;.</span>
        <button className={s.botaoPrimario} onClick={() => { void carregar() }}>Tentar novamente</button>
      </div>
    )
  }

  return (
    <div className={s.pagina}>
      <p className={s.nota}>
        Modelo e classes que valem em cada câmera. Câmera sem modelo cai no detector padrão do
        ambiente — não há como desativar por aqui, só trocar. O escopo vale hoje no shadow e no
        worker da nuvem; o box edge ainda não recebe classe por câmera.
        {!podeEditar && ' Você tem acesso somente de leitura — para alterar, peça a quem administra o seu acesso a permissão de configurar câmeras.'}
      </p>
      <table className={s.tabela}>
        <thead>
          <tr>
            <th className={s.th}>Câmera</th>
            <th className={s.th}>Modelo</th>
            <th className={s.th}>Classes no escopo</th>
            <th className={s.th}>Último deploy</th>
            <th className={s.th} />
          </tr>
        </thead>
        <tbody>
          {ativas.map((cam) => {
            const rascunho = rascunhos[cam.id] ?? { modelId: '', classes: [] }
            const dep = deployPorCamera[cam.id]
            const modulo = moduloDaCamera(cam)
            const doModulo = modelos.filter((m) => (m.module_code || 'epi') === modulo)
            const todas = rascunho.modelId ? classesPorModelo[rascunho.modelId] ?? [] : []
            const base = rascunhoDo(dep)
            // `base` é o GRAVADO cru (com as mortas): é o que acende o Salvar
            // sozinho na linha que precisa de limpeza.
            const mortas = classesMortas(base.classes, classesPorModelo[base.modelId] ?? [])
            const mudou =
              rascunho.modelId !== base.modelId || !mesmoConjunto(rascunho.classes, base.classes)
            const podeSalvar =
              podeEditar && salvando !== cam.id && mudou && !!rascunho.modelId && rascunho.classes.length > 0
            return (
              <tr key={cam.id}>
                <td className={s.tdNome}>{cam.name}</td>
                <td className={s.td}>
                  <select
                    className={s.seletor}
                    aria-label={`Modelo da câmera ${cam.name}`}
                    value={rascunho.modelId}
                    disabled={!podeEditar || salvando === cam.id}
                    onChange={(e) => {
                      const id = e.target.value
                      mudar(cam.id, { modelId: id, classes: id ? [...(classesPorModelo[id] ?? [])] : [] })
                    }}
                  >
                    <option value="" disabled={!!dep}>— sem modelo (detector padrão)</option>
                    {doModulo.map((m) => (
                      <option key={m.id} value={m.id}>{nomeInternoOuCliente(m, isSuperAdmin)}</option>
                    ))}
                  </select>
                </td>
                <td className={s.td}>
                  {rascunho.modelId ? (
                    todas.length === 0 ? (
                      <span className={s.tom.atencao}>Este modelo não declara classes.</span>
                    ) : (
                      <ClasseChips
                        classes={todas}
                        selecionadas={rascunho.classes}
                        podeEditar={podeEditar && salvando !== cam.id}
                        nomeCamera={cam.name}
                        onAlternar={(cls) =>
                          mudar(cam.id, {
                            classes: rascunho.classes.includes(cls)
                              ? rascunho.classes.filter((c) => c !== cls)
                              : [...rascunho.classes, cls],
                          })
                        }
                      />
                    )
                  ) : (
                    <span className={s.rotulo}>—</span>
                  )}
                  <AvisoClassesMortas mortas={mortas} />
                </td>
                <td className={s.td}>
                  {dep ? new Date(dep.created_at).toLocaleString('pt-BR') : '—'}
                </td>
                <td className={s.td}>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 3, alignItems: 'flex-start' }}>
                    <button
                      className={s.botaoSecundario}
                      aria-label={`Salvar escopo de ${cam.name}`}
                      disabled={!podeSalvar}
                      onClick={() => { void salvar(cam) }}
                    >
                      {salvando === cam.id ? 'Salvando…' : 'Salvar'}
                    </button>
                    {motivoBloqueioSalvar(podeEditar, rascunho.modelId, rascunho.classes, mudou) && (
                      <span className={s.rotulo} style={{ fontSize: 10.5 }}>
                        {motivoBloqueioSalvar(podeEditar, rascunho.modelId, rascunho.classes, mudou)}
                      </span>
                    )}
                  </div>
                </td>
              </tr>
            )
          })}
          {ativas.length === 0 && (
            <tr><td className={s.td} colSpan={5}>Nenhuma câmera ativa no tenant.</td></tr>
          )}
        </tbody>
      </table>
    </div>
  )
}

// ── aba Desempenho (5ª aba — Main.dc.html) ──────────────────────────────────

const FPS_OPCOES = [1, 5, 10, 15, 30] as const
type FpsOpcao = (typeof FPS_OPCOES)[number]

const QUALIDADE_OPCOES: Array<{ valor: 'low' | 'medium' | 'high'; rotulo: string }> = [
  { valor: 'low', rotulo: 'Baixa' },
  { valor: 'medium', rotulo: 'Média' },
  { valor: 'high', rotulo: 'Alta' },
]
type QualidadeOpcao = (typeof QUALIDADE_OPCOES)[number]['valor']

// Eixo COLETA (frame de treino, migration 114) — independente do eixo
// OPERAÇÃO (FPS/qualidade acima). Default 0 (principal/alta): mesmo default
// do backend, anotar em alta é melhor mesmo que a operação rode em baixa.
const COLETA_OPCOES: Array<{ valor: 0 | 1; rotulo: string }> = [
  { valor: 0, rotulo: 'Principal (máxima)' },
  { valor: 1, rotulo: 'Substream (704×480)' },
]
type ColetaOpcao = (typeof COLETA_OPCOES)[number]['valor']

/** Limiares portados de `CameraFpsConfig.severityFromTelemetry` (legado): GPU
 * ≥85% ou VRAM ≥90% ou status derivado crítico/offline é sobrecarga forte;
 * GPU ≥60% ou degradado é sobrecarga moderada. O desenho só tem UM tom de
 * alerta (âmbar) — as duas graduações do legado viram um só booleano aqui. */
function equipamentoSobrecarregado(ctx: CameraHealthContext): boolean {
  const gpu = ctx.metrics?.gpu_pct ?? 0
  const vram = ctx.metrics?.gpu_mem_pct ?? 0
  if (gpu >= 85 || vram >= 90 || ctx.derived_status === 'critical' || ctx.derived_status === 'offline') {
    return true
  }
  return gpu >= 60 || ctx.derived_status === 'degraded'
}

function arred1(v: number | null | undefined): number | null | undefined {
  return v == null ? v : Math.round(v * 10) / 10
}

/** Texto da propagação cloud→edge — nunca afirma "aplica no edge" quando o
 * backend disse que não tinha como enfileirar (sem site, ou erro). */
function notaPropagacao(prop: CameraPropagation | undefined): string {
  if (!prop || prop.queued) return 'Alterado agora · aplica no edge em até 30 s'
  if (prop.reason === 'no_site') return 'Alterado agora · câmera sem site edge vinculado — não há propagação'
  return 'Alterado agora · não foi possível enfileirar a propagação ao edge'
}

interface DesempenhoProps {
  cameras: Camera[]
  selecionada: Camera | null
  sites: EdgeSite[]
  podeConfigurar: boolean
  nomeDoSite: (siteId: string | null | undefined) => string | null
  aoEscolherCamera: () => void
  aoSalvar: (atualizada: Camera) => void
}

function AbaDesempenho({
  cameras,
  selecionada,
  sites,
  podeConfigurar,
  nomeDoSite,
  aoEscolherCamera,
  aoSalvar,
}: DesempenhoProps) {
  const [fps, setFps] = useState<FpsOpcao>(5)
  const [qualidade, setQualidade] = useState<QualidadeOpcao>('medium')
  const [coleta, setColeta] = useState<ColetaOpcao>(0)
  const [ctx, setCtx] = useState<CameraHealthContext | null>(null)
  const [ctxCarregando, setCtxCarregando] = useState(false)
  const [ctxErro, setCtxErro] = useState<string | null>(null)
  const [salvando, setSalvando] = useState(false)
  const [erroSalvar, setErroSalvar] = useState<string | null>(null)
  const [propagacao, setPropagacao] = useState<CameraPropagation | undefined>()
  const [salvoAgora, setSalvoAgora] = useState(false)

  const carregarSaude = useCallback(async (id: string) => {
    setCtxCarregando(true)
    setCtxErro(null)
    try {
      setCtx(await cameraService.getHealthContext(id))
    } catch (err) {
      setCtx(null)
      setCtxErro(err instanceof Error ? err.message : 'Erro ao carregar a saúde do equipamento')
    } finally {
      setCtxCarregando(false)
    }
  }, [])

  useEffect(() => {
    if (!selecionada) { setCtx(null); return }
    setFps((selecionada.fps_target ?? 5) as FpsOpcao)
    setQualidade((selecionada.quality_preset ?? 'medium') as QualidadeOpcao)
    setColeta((selecionada.collection_subtype ?? 0) as ColetaOpcao)
    setErroSalvar(null)
    setSalvoAgora(false)
    void carregarSaude(selecionada.id)
    // Só reage à TROCA de câmera — um refresh de `cameras` que traga o mesmo
    // id não pode zerar edição em andamento do usuário.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selecionada?.id])

  if (!selecionada) {
    return (
      <div className={s.centro}>
        <Gauge size={36} strokeWidth={1.7} className={s.rotulo} aria-hidden="true" />
        <span className={s.centroTitulo}>Nenhuma câmera selecionada</span>
        <span className={s.centroTexto}>
          Escolha uma câmera para ajustar o desempenho e ver a saúde do equipamento.
        </span>
        <button className={s.botaoPrimario} onClick={aoEscolherCamera}>Escolher câmera</button>
      </div>
    )
  }

  const est = estadoDaCamera(selecionada)
  const site = sites.find((st) => st.id === selecionada.site_id)

  const fpsBase = (selecionada.fps_target ?? 5) as FpsOpcao
  const qualidadeBase = (selecionada.quality_preset ?? 'medium') as QualidadeOpcao
  const coletaBase = (selecionada.collection_subtype ?? 0) as ColetaOpcao
  const mudou = fps !== fpsBase || qualidade !== qualidadeBase || coleta !== coletaBase

  // Desalinhamento coleta×operação — mesma condição do legado
  // (`collectionOperationMismatch`): coleta em Principal mas o stream de
  // operação (live view) não está em Principal. `live_view_subtype` não é
  // editável nesta aba, só lido — o fallback ?? 1 é o default do backend
  // (migration 092).
  const desalinhado = coleta === 0 && (selecionada.live_view_subtype ?? 1) !== 0

  const metrics = ctx?.metrics ?? null
  const temTelemetria = ctx?.has_telemetry === true && metrics != null
  const sobrecarga = temTelemetria && ctx != null ? equipamentoSobrecarregado(ctx) : false

  const camerasNoSiteLocal = cameras.filter(
    (c) => c.site_id === selecionada.site_id && c.is_active !== false,
  ).length
  const nCameras = ctx != null && ctx.cameras_active_count > 0 ? ctx.cameras_active_count : camerasNoSiteLocal

  const demandaTotal = ctx?.fps_demand_total ?? null
  const demandaProjetada = demandaTotal != null ? Math.max(0, demandaTotal - fpsBase + fps) : null

  async function salvar() {
    if (!selecionada || !podeConfigurar || !mudou) return
    setSalvando(true)
    setErroSalvar(null)
    try {
      const patch: CameraConfigPatch = { fps_target: fps, quality_preset: qualidade }
      if (coleta !== coletaBase) patch.collection_subtype = coleta
      const atualizada = await cameraService.patchConfig(selecionada.id, patch)
      setPropagacao(atualizada.propagation)
      setSalvoAgora(true)
      aoSalvar(atualizada)
    } catch (err) {
      setErroSalvar(err instanceof Error ? err.message : 'Erro ao salvar a configuração')
    } finally {
      setSalvando(false)
    }
  }

  function descartar() {
    setFps(fpsBase)
    setQualidade(qualidadeBase)
    setColeta(coletaBase)
    setErroSalvar(null)
  }

  const metricasCards: Array<{ rot: string; v: string; alerta: boolean }> = metrics
    ? [
        { rot: 'GPU', v: numero(arred1(metrics.gpu_pct), '%'), alerta: sobrecarga },
        { rot: 'VRAM', v: numero(arred1(metrics.gpu_mem_pct), '%'), alerta: sobrecarga },
        { rot: 'CPU', v: numero(arred1(metrics.cpu_pct), '%'), alerta: false },
        { rot: 'FILA', v: numero(metrics.queue_depth), alerta: sobrecarga },
        { rot: 'FPS MEDIDO', v: numero(arred1(metrics.inference_fps)), alerta: sobrecarga },
        { rot: 'LATÊNCIA', v: numero(arred1(metrics.inference_latency_ms), ' ms'), alerta: sobrecarga },
        { rot: 'TÉRMICA', v: numero(arred1(metrics.gpu_temp_c), ' °C'), alerta: false },
        { rot: 'DECODE', v: numero(arred1(metrics.decode_pct), '%'), alerta: false },
      ]
    : []

  return (
    <div className={s.pagina}>
      <div className={s.desempenhoCabecalho}>
        <span className={s.cartaoNome}>{selecionada.name}</span>
        <span className={`${s.estadoLinha} ${s.tom[est.tom]}`}>
          <est.Icone size={13} strokeWidth={1.7} aria-hidden="true" />
          {est.palavra}
        </span>
        <span className={s.espacador} />
        <span className={s.rotulo}>
          {site ? `${site.name} · ${DEPLOYMENT_MODE_LABELS[site.deployment_mode]}` : 'Sem site vinculado'}
        </span>
      </div>

      <div className={s.desempenhoGrid}>
        {/* OPERAÇÃO */}
        <div className={s.painelDesempenho}>
          <span className={s.overline}>Operação</span>

          <div className={s.bloco}>
            <span className={s.textoAuxiliar}>Quadros por segundo analisados</span>
            <div className={s.linhaOpcoes}>
              {FPS_OPCOES.map((f) => (
                <button
                  key={f}
                  type="button"
                  aria-pressed={fps === f}
                  aria-label={`${f} fps`}
                  disabled={!podeConfigurar}
                  className={fps === f ? s.opcaoFps.ativa : s.opcaoFps.inativa}
                  onClick={() => setFps(f)}
                >
                  {f}
                </button>
              ))}
            </div>
          </div>

          <div className={s.bloco}>
            <span className={s.textoAuxiliar}>Qualidade do vídeo analisado</span>
            <div className={s.linhaOpcoes}>
              {QUALIDADE_OPCOES.map((q) => (
                <button
                  key={q.valor}
                  type="button"
                  aria-pressed={qualidade === q.valor}
                  disabled={!podeConfigurar}
                  className={qualidade === q.valor ? s.opcaoQualidade.ativa : s.opcaoQualidade.inativa}
                  onClick={() => setQualidade(q.valor)}
                >
                  {q.rotulo}
                </button>
              ))}
            </div>
          </div>

          <div className={s.divisor} />

          <div className={s.bloco}>
            <span className={s.overline}>Coleta para treino</span>
            <span className={s.textoAuxiliar}>
              Em que resolução os frames entram no conjunto de treino. Eixo separado da
              operação — dá para operar leve e coletar em alta.
            </span>
            <div className={s.colunaOpcoes}>
              {COLETA_OPCOES.map((c) => (
                <button
                  key={c.valor}
                  type="button"
                  aria-pressed={coleta === c.valor}
                  disabled={!podeConfigurar}
                  className={coleta === c.valor ? s.opcaoColeta.ativa : s.opcaoColeta.inativa}
                  onClick={() => setColeta(c.valor)}
                >
                  <span className={coleta === c.valor ? s.anel.marcado : s.anel.vazio}>
                    <span className={coleta === c.valor ? s.ponto.marcado : s.ponto.vazio} />
                  </span>
                  {c.rotulo}
                </button>
              ))}
            </div>
          </div>

          {desalinhado && (
            <div className={s.avisoAmbar} role="status">
              <TriangleAlert
                size={18}
                strokeWidth={1.7}
                className={s.avisoIcone}
                color={lk.estado.atencao}
                aria-hidden="true"
              />
              <span className={s.avisoTexto}>
                <strong>Coleta em alta com operação em baixa.</strong> O modelo vai treinar em
                imagens melhores do que as que ele vê rodando — o que aprende aqui pode não
                valer lá.
              </span>
            </div>
          )}

          {!podeConfigurar && (
            <span className={s.textoAuxiliar}>
              Somente leitura — ajustar o desempenho exige a permissão de configurar câmeras.
            </span>
          )}
        </div>

        {/* SAÚDE DO EQUIPAMENTO */}
        <div className={s.painelDesempenho}>
          <div className={s.saudeTopo}>
            <span className={s.overline}>Saúde do equipamento</span>
            <span className={s.espacador} />
            <button
              type="button"
              className={s.botaoSecundario}
              disabled={ctxCarregando}
              onClick={() => { void carregarSaude(selecionada.id) }}
            >
              <RefreshCw size={14} strokeWidth={1.7} /> Atualizar
            </button>
          </div>
          <span className={s.overline}>
            Mini PC do site · {(nomeDoSite(selecionada.site_id) ?? 'sem site').toUpperCase()} · {nCameras} câmeras
          </span>

          {ctxCarregando ? (
            <LogikosLoader variante="tile" estado="waiting" rotulo="CARREGANDO SAÚDE" tamanho={32} />
          ) : temTelemetria ? (
            <div className={s.metricas}>
              {metricasCards.map((m) => (
                <div key={m.rot} className={m.alerta ? s.metricaCard.alerta : s.metricaCard.neutro}>
                  <span className={s.metricaRotulo}>{m.rot}</span>
                  <span className={m.alerta ? s.metricaValorTom.alerta : s.metricaValorTom.neutro}>{m.v}</span>
                </div>
              ))}
            </div>
          ) : (
            <div className={s.bloco}>
              <span className={`${s.estadoLinha} ${s.tom.neutro}`}>
                <CircleSlash size={13} strokeWidth={1.7} aria-hidden="true" /> SEM TELEMETRIA
              </span>
              <span className={s.textoAuxiliar}>
                {ctxErro ?? 'Este site ainda não reportou telemetria do equipamento.'}
              </span>
            </div>
          )}

          <div className={s.divisor} />

          <div className={s.bloco}>
            <span className={s.overline}>Impacto da mudança</span>
            {demandaTotal != null ? (
              <>
                <div className={s.impactoLinha}>
                  <span className={s.impactoValor}>{demandaTotal}</span>
                  <span className={s.textoAuxiliar}>fps somados hoje no site</span>
                </div>
                {fps !== fpsBase && demandaProjetada != null && (
                  <div className={s.impactoCaixa}>
                    <ArrowRight size={18} strokeWidth={1.7} color={lk.cor.cianoVisao} aria-hidden="true" />
                    <span className={s.impactoCaixaTexto}>
                      Passará a{' '}
                      <strong className={s.impactoCaixaNumero}>~{demandaProjetada} fps</strong> com esta
                      alteração
                    </span>
                  </div>
                )}
              </>
            ) : (
              <span className={s.textoAuxiliar}>
                {ctxErro ?? 'Sem dado de demanda de FPS para este site.'}
              </span>
            )}
          </div>
        </div>
      </div>

      {erroSalvar && (
        <div className={`${s.resultado} ${s.tom.nc}`} role="alert">{erroSalvar}</div>
      )}

      <div className={s.barraSalvar}>
        <button
          type="button"
          className={s.botaoPrimario}
          disabled={!podeConfigurar || !mudou || salvando}
          onClick={() => { void salvar() }}
        >
          {salvando ? 'Salvando…' : 'Salvar configuração'}
        </button>
        <button
          type="button"
          className={s.botaoSecundario}
          disabled={!mudou || salvando}
          onClick={descartar}
        >
          Descartar
        </button>
        <span className={s.espacador} />
        {salvoAgora && !mudou && (
          <span className={s.textoAuxiliar}>{notaPropagacao(propagacao)}</span>
        )}
      </div>
    </div>
  )
}

// ── tela ─────────────────────────────────────────────────────────────────────

export function Cameras() {
  const { can } = useAuth()
  const navegar = useNavigate()

  const podeCadastrar = can('cameras:write')
  const podeTestar = can('cameras:test')
  const podeControlar = can('cameras:control')
  const podeConfigurar = can('cameras:configure')
  const podeExcluir = can('cameras:delete')

  const [aba, setAba] = useState<Aba>('cameras')
  const [carregando, setCarregando] = useState(true)
  const [erro, setErro] = useState<string | null>(null)
  const [cameras, setCameras] = useState<Camera[]>([])
  const [sites, setSites] = useState<EdgeSite[]>([])
  const [saudeSites, setSaudeSites] = useState<SiteHealth[]>([])
  const [erroSites, setErroSites] = useState<string | null>(null)
  const [selecionadaId, setSelecionadaId] = useState<string | null>(null)

  const [testando, setTestando] = useState(false)
  const [teste, setTeste] = useState<TestResult | null>(null)
  const [erroTeste, setErroTeste] = useState<string | null>(null)

  const [cadastroAberto, setCadastroAberto] = useState(false)
  const [editando, setEditando] = useState<Camera | undefined>()
  const [confirmarArquivo, setConfirmarArquivo] = useState(false)
  const [arquivando, setArquivando] = useState(false)
  const [confirmarExclusao, setConfirmarExclusao] = useState(false)
  const [excluindo, setExcluindo] = useState(false)
  /**
   * Falha de AÇÃO (arquivar/excluir) ≠ falha de CARGA.
   *
   * Antes, arquivar que falhava caía no `erro` da carga e a tela inteira
   * virava "Não foi possível carregar" — o que não aconteceu: a lista tinha
   * carregado bem. Perder as 28 câmeras da tela por causa de um POST que
   * voltou 404 é a tela afirmando uma coisa por outra.
   */
  const [erroAcao, setErroAcao] = useState<string | null>(null)
  const [salvandoModo, setSalvandoModo] = useState(false)

  const carregar = useCallback(async () => {
    setCarregando(true)
    setErro(null)
    try {
      const lista = await cameraService.list()
      setCameras(lista)
      setSelecionadaId((atual) =>
        atual && lista.some((c) => c.id === atual) ? atual : lista[0]?.id ?? null,
      )
    } catch (err) {
      setErro(err instanceof Error ? err.message : 'Erro ao carregar câmeras')
    } finally {
      setCarregando(false)
    }
    // Sites são de outro domínio e o backend só os libera a admin/superadmin:
    // um 403 aqui NÃO pode derrubar a lista de câmeras.
    const [lista, saude] = await Promise.allSettled([
      edgeService.listSites(),
      edgeService.getSitesHealth(),
    ])
    if (lista.status === 'fulfilled') setSites(lista.value)
    if (saude.status === 'fulfilled') setSaudeSites(saude.value)
    setErroSites(
      lista.status === 'rejected'
        ? lista.reason instanceof Error
          ? lista.reason.message
          : 'Não foi possível carregar os sites'
        : null,
    )
  }, [])

  useEffect(() => { void carregar() }, [carregar])

  const selecionada = useMemo(
    () => cameras.find((c) => c.id === selecionadaId) ?? null,
    [cameras, selecionadaId],
  )

  const nomeDoSite = useCallback(
    (siteId: string | null | undefined) => sites.find((st) => st.id === siteId)?.name ?? null,
    [sites],
  )

  function selecionar(id: string) {
    setSelecionadaId(id)
    setTeste(null)
    setErroTeste(null)
  }

  async function testarConexao() {
    if (!selecionada || !podeTestar) return
    setTestando(true)
    setTeste(null)
    setErroTeste(null)
    try {
      setTeste(await cameraService.test(selecionada.id))
    } catch (err) {
      setErroTeste(err instanceof Error ? err.message : 'Erro ao testar a conexão')
    } finally {
      setTestando(false)
    }
  }

  async function alternarMonitoramento() {
    if (!selecionada || !podeControlar) return
    const ligada = estadoDaCamera(selecionada).palavra === 'ONLINE'
    try {
      if (ligada) await cameraService.stop(selecionada.id)
      else await cameraService.start(selecionada.id)
      await carregar()
    } catch (err) {
      setErroTeste(err instanceof Error ? err.message : 'Erro ao alternar o monitoramento')
    }
  }

  async function arquivarOuRestaurar() {
    if (!selecionada || !podeCadastrar) return
    setArquivando(true)
    setErroAcao(null)
    try {
      if (selecionada.is_active === false) await cameraService.restore(selecionada.id)
      else await cameraService.archive(selecionada.id)
      await carregar()
    } catch (err) {
      setErroAcao(err instanceof Error ? err.message : 'Erro ao arquivar a câmera')
    } finally {
      setArquivando(false)
      setConfirmarArquivo(false)
    }
  }

  /** Exclui a câmera do sistema. Só é chamada pelo onConfirm do diálogo —
   *  nunca direto de um clique, porque não há desfazer. */
  async function excluir() {
    if (!selecionada || !podeExcluir) return
    setExcluindo(true)
    setErroAcao(null)
    try {
      await cameraService.remove(selecionada.id)
      // `carregar()` reescolhe a seleção: a câmera excluída não volta na
      // lista, então `selecionadaId` cai na primeira restante (ou em null,
      // que cai no estado vazio com o CTA de cadastrar — sem beco).
      await carregar()
      setConfirmarExclusao(false)
    } catch (err) {
      setErroAcao(err instanceof Error ? err.message : 'Erro ao excluir a câmera')
      setConfirmarExclusao(false)
    } finally {
      setExcluindo(false)
    }
  }

  async function trocarModo(site: EdgeSite, modo: DeploymentMode) {
    if (!podeConfigurar || site.deployment_mode === modo) return
    setSalvandoModo(true)
    try {
      const atualizado = await edgeService.updateSite(site.id, { deployment_mode: modo })
      setSites((prev) => prev.map((st) => (st.id === atualizado.id ? atualizado : st)))
    } catch (err) {
      setErroSites(err instanceof Error ? err.message : 'Erro ao trocar o modo do site')
    } finally {
      setSalvandoModo(false)
    }
  }

  /** Merge local do PATCH /config — sem `carregar()` completo (que reexibe o
   * loader de tela cheia): a aba Desempenho fica no lugar, só a nota de
   * "alterado agora" aparece. */
  function aplicarConfigSalva(atualizada: Camera) {
    setCameras((prev) => prev.map((c) => (c.id === atualizada.id ? { ...c, ...atualizada } : c)))
  }

  if (carregando) {
    return <LogikosLoader variante="fullscreen" estado="waiting" rotulo="CARREGANDO CÂMERAS" />
  }

  if (erro) {
    return (
      <div className={s.centro}>
        <CircleAlert size={36} strokeWidth={1.7} className={s.tom.nc} />
        <span className={s.centroTitulo}>Não foi possível carregar</span>
        <span className={s.centroMono}>A lista de câmeras não respondeu · {erro}</span>
        <button className={s.botaoPrimario} onClick={() => { void carregar() }}>Tentar novamente</button>
      </div>
    )
  }

  const cabecalho = (
    <div className={s.cabecalho}>
      <h1 className={s.titulo}>Câmeras &amp; Sites</h1>
      <div className={s.abas} role="tablist" aria-label="Seções de câmeras e sites">
        {ABAS.map((a) => (
          <button
            key={a.id}
            role="tab"
            aria-selected={aba === a.id}
            className={aba === a.id ? s.aba.ativa : s.aba.inativa}
            onClick={() => setAba(a.id)}
          >
            {a.rotulo}
          </button>
        ))}
      </div>
      <span className={s.espacador} />
      <button className={s.botaoSecundario} onClick={() => { void carregar() }}>
        <RefreshCw size={14} strokeWidth={1.7} /> Atualizar
      </button>
      {podeCadastrar && (
        <button className={s.botaoPrimario} onClick={() => setCadastroAberto(true)}>
          <Plus size={15} strokeWidth={1.7} /> Adicionar câmera
        </button>
      )}
    </div>
  )

  const modais = (
    <>
      {cadastroAberto && (
        <CameraOnboardingWizard
          onComplete={() => { setCadastroAberto(false); void carregar() }}
          onCancel={() => setCadastroAberto(false)}
        />
      )}
      <CameraWizard
        isOpen={!!editando}
        onClose={() => setEditando(undefined)}
        onSuccess={() => { void carregar() }}
        camera={editando}
      />
      <ConfirmDialog
        open={confirmarArquivo}
        onClose={() => setConfirmarArquivo(false)}
        onConfirm={() => { void arquivarOuRestaurar() }}
        title="Arquivar câmera"
        description={`A câmera "${selecionada?.name}" sai do reconhecimento e do export de dataset. Os frames, anotações e detecções dela continuam no banco — nada é apagado, e dá para desarquivar depois.`}
        confirmLabel="Arquivar"
        variant="primary"
        loading={arquivando}
      />
      {/* Diz o que se PERDE e o que NÃO se perde. A frase sobre alertas e
          evidências não é conforto: é o que o backend de fato faz (exclusão
          lógica, migration 138) — se um dia virar DELETE físico, esta frase
          vira mentira e o teste de `soft_delete` cai antes. */}
      <ConfirmDialog
        open={confirmarExclusao}
        onClose={() => setConfirmarExclusao(false)}
        onConfirm={() => { void excluir() }}
        title="Excluir câmera"
        description={
          `A câmera "${selecionada?.name}" sai do sistema: some desta lista, do Ao Vivo, ` +
          'dos quadros do grid e do escopo de modelo, e o equipamento do site para de ligá-la. ' +
          'Não dá para desfazer — para tê-la de volta é preciso cadastrar de novo. ' +
          'NÃO são apagados: os alertas, as evidências gravadas e os frames de treino já ' +
          'anotados dela continuam no histórico. Para tirar do reconhecimento sem excluir, ' +
          'use Arquivar.'
        }
        confirmLabel="Excluir para sempre"
        variant="danger"
        loading={excluindo}
      />
    </>
  )

  if (cameras.length === 0) {
    return (
      <div className={s.pagina}>
        {cabecalho}
        <div className={s.centro}>
          <Video size={36} strokeWidth={1.7} className={s.rotulo} />
          <span className={s.centroTitulo}>Nenhuma câmera cadastrada</span>
          <span className={s.centroTexto}>
            Cadastre a primeira câmera do site para começar a detectar.
          </span>
          {podeCadastrar && (
            <button className={s.botaoPrimario} onClick={() => setCadastroAberto(true)}>
              <Plus size={15} strokeWidth={1.7} /> Adicionar câmera
            </button>
          )}
        </div>
        {modais}
      </div>
    )
  }

  return (
    <div className={s.pagina}>
      {cabecalho}

      {aba === 'cameras' && (
        <div className={s.split}>
          <div className={s.lista} role="list">
            {cameras.map((cam) => {
              const est = estadoDaCamera(cam)
              const ativa = cam.id === selecionadaId
              return (
                <button
                  key={cam.id}
                  role="listitem"
                  aria-current={ativa}
                  className={ativa ? s.item.ativo : s.item.inativo}
                  onClick={() => selecionar(cam.id)}
                >
                  <span className={s.itemTextos}>
                    <span className={s.itemNome}>{cam.name}</span>
                    <span className={s.itemArea}>{cam.location || 'Sem área definida'}</span>
                  </span>
                  <span className={`${s.itemEstado} ${s.tom[est.tom]}`}>
                    <est.Icone size={12} strokeWidth={1.7} aria-hidden="true" />
                    {est.palavra}
                  </span>
                </button>
              )
            })}
          </div>

          {selecionada && (
            <div className={s.cartao}>
              <div className={s.cartaoTopo}>
                <span className={s.cartaoNome}>{selecionada.name}</span>
                {(() => {
                  const est = estadoDaCamera(selecionada)
                  return (
                    <span className={`${s.estadoLinha} ${s.tom[est.tom]}`}>
                      <est.Icone size={13} strokeWidth={1.7} aria-hidden="true" />
                      {est.palavra}
                    </span>
                  )
                })()}
                <span className={s.espacador} />
                {podeTestar && (
                  <button className={s.botaoPrimario} disabled={testando} onClick={() => { void testarConexao() }}>
                    {testando
                      ? <LogikosLoader variante="spinner" estado="waiting" tamanho={16} />
                      : <Plug size={14} strokeWidth={1.7} />}
                    Testar conexão
                  </button>
                )}
              </div>

              <div className={s.corpoDetalhe}>
                <div className={s.previa}>
                  <Previa key={selecionada.id} cameraId={selecionada.id} />
                </div>
                <dl className={s.campos}>
                  <dt className={s.rotulo}>Site</dt>
                  <dd>{nomeDoSite(selecionada.site_id) ?? 'Sem site vinculado'}</dd>
                  <dt className={s.rotulo}>Área</dt>
                  <dd>{selecionada.location || '—'}</dd>
                  {/* §15 paridade: única leitura de fabricante fora da edição
                      (só admin abre). Visível a quem lê a câmera. */}
                  <dt className={s.rotulo}>Fabricante</dt>
                  <dd>{selecionada.manufacturer || '—'}</dd>
                  <dt className={s.rotulo}>Endereço</dt>
                  <dd className={s.valorMono}>
                    {enderecoMascarado(selecionada)} <span className={s.rotulo}>· senha oculta</span>
                  </dd>
                  <dt className={s.rotulo}>Processa em</dt>
                  <dd>
                    {(() => {
                      const site = sites.find((st) => st.id === selecionada.site_id)
                      if (!site) return '—'
                      return `${DEPLOYMENT_MODE_LABELS[site.deployment_mode]} · ${site.name}`
                    })()}
                  </dd>
                  <dt className={s.rotulo}>Cenário</dt>
                  <dd>{moduloDaCamera(selecionada).toUpperCase()}</dd>
                </dl>
              </div>

              {/* PARA O DESIGN (1): sem estas ações, o CRUD do front atual some
                  do produto — a rota `/epi/cameras` é a mesma nos dois fronts. */}
              <div className={s.acoes}>
                {podeCadastrar && (
                  <button className={s.botaoSecundario} onClick={() => setEditando(selecionada)}>
                    <Pencil size={13} strokeWidth={1.7} /> Editar
                  </button>
                )}
                {podeControlar && (
                  <button className={s.botaoSecundario} onClick={() => { void alternarMonitoramento() }}>
                    {estadoDaCamera(selecionada).palavra === 'ONLINE'
                      ? <><Square size={13} strokeWidth={1.7} /> Parar monitoramento</>
                      : <><Play size={13} strokeWidth={1.7} /> Iniciar monitoramento</>}
                  </button>
                )}
                <button
                  className={s.botaoSecundario}
                  onClick={() => navegar(rotaNova(`/epi/cameras/${selecionada.id}/operations`))}
                >
                  <Settings2 size={13} strokeWidth={1.7} /> Operações
                </button>
                {/* F5-LEVE: Cenário migrado pro shell novo — tela própria em
                    `epi/cameras/:cameraId/cenario` (Cenario.tsx), registrada
                    em ROTAS_NOVAS. Substitui o antigo `/scenario`, que quebra
                    hoje com erro cru de JS (conserto emergencial fora deste
                    desenho). */}
                <button
                  className={s.botaoSecundario}
                  onClick={() => navegar(rotaNova(`/epi/cameras/${selecionada.id}/cenario`))}
                >
                  <Frame size={13} strokeWidth={1.7} /> Cenário
                </button>
                {podeCadastrar && (
                  <button
                    className={s.botaoSecundario}
                    disabled={arquivando}
                    onClick={() =>
                      selecionada.is_active === false
                        ? void arquivarOuRestaurar()
                        : setConfirmarArquivo(true)
                    }
                  >
                    {selecionada.is_active === false ? 'Desarquivar' : 'Arquivar'}
                  </button>
                )}
                {podeExcluir && (
                  <button
                    className={s.botaoPerigo}
                    disabled={excluindo}
                    onClick={() => setConfirmarExclusao(true)}
                  >
                    <Trash2 size={13} strokeWidth={1.7} /> Excluir
                  </button>
                )}
              </div>

              {erroAcao && (
                <div className={`${s.resultado} ${s.tom.nc}`} role="status">{erroAcao}</div>
              )}

              {(teste || erroTeste) && (
                <div className={s.painelTeste}>
                  <span className={s.overline}>Teste de conexão · passo a passo</span>
                  {teste &&
                    PASSOS.map(([chave, texto]) => {
                      const check = teste.checks?.[chave]
                      const tom = TOM_PASSO[check?.status ?? 'pending'] ?? 'neutro'
                      return (
                        <div key={chave} className={s.passo}>
                          <span className={`${s.passoMarca} ${s.tom[tom]}`} aria-hidden="true">
                            {MARCA_PASSO[check?.status ?? 'pending'] ?? '·'}
                          </span>
                          <span className={s.passoTexto}>{texto}</span>
                          <span className={`${s.resultadoDica} ${s.tom[tom]}`}>
                            {check?.message ?? 'não executado'}
                          </span>
                        </div>
                      )
                    })}
                  {teste && (
                    <div className={`${s.resultado} ${teste.success ? s.tom.ok : s.tom.nc}`} role="status">
                      {teste.success ? 'Conexão estabelecida' : teste.error || 'Falha na conexão'}
                      {teste.suggestion && <span className={s.resultadoDica}>{teste.suggestion}</span>}
                    </div>
                  )}
                  {erroTeste && (
                    <div className={`${s.resultado} ${s.tom.nc}`} role="status">{erroTeste}</div>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {aba === 'sites' && (
        <div className={s.pagina}>
          {erroSites && (
            <p className={s.nota}>
              Não foi possível carregar os sites: {erroSites}. Isto não quer dizer que não há sites —
              a listagem é restrita a administradores.
            </p>
          )}
          {sites.length === 0 && !erroSites && (
            <p className={s.nota}>Nenhum site edge cadastrado para este tenant.</p>
          )}
          {sites.map((site) => {
            const saude = saudeSites.find((h) => h.site_id === site.id)
            const est = saude ? ESTADO_SITE[saude.status] : null
            return (
              <div key={site.id} className={s.cartaoSite}>
                <div className={s.cartaoTopo}>
                  <span className={s.cartaoNome}>{site.name}</span>
                  {est ? (
                    <span className={`${s.estadoLinha} ${s.tom[est.tom]}`}>
                      <est.Icone size={13} strokeWidth={1.7} aria-hidden="true" />
                      {est.palavra}
                    </span>
                  ) : (
                    <span className={`${s.estadoLinha} ${s.tom.neutro}`}>
                      <CircleSlash size={13} strokeWidth={1.7} aria-hidden="true" /> SEM TELEMETRIA
                    </span>
                  )}
                </div>

                <div className={s.bloco}>
                  <span className={s.textoAuxiliar}>Modo de processamento do site</span>
                  <div className={s.seletorModo}>
                    {(Object.keys(DEPLOYMENT_MODE_LABELS) as DeploymentMode[]).map((m) => (
                      <button
                        key={m}
                        className={site.deployment_mode === m ? s.modo.ativo : s.modo.inativo}
                        aria-pressed={site.deployment_mode === m}
                        disabled={!podeConfigurar || salvandoModo}
                        onClick={() => { void trocarModo(site, m) }}
                      >
                        {DEPLOYMENT_MODE_LABELS[m]}
                      </button>
                    ))}
                  </div>
                  <span className={s.textoAuxiliar}>{MODO_DESCRICAO[site.deployment_mode]}</span>
                  {!podeConfigurar && (
                    <span className={s.textoAuxiliar}>
                      Somente leitura — trocar o modo exige a permissão de configurar câmeras.
                    </span>
                  )}
                </div>

                <div className={s.bloco}>
                  <span className={s.overline}>Saúde do equipamento · {site.name}</span>
                  <div className={s.metricas}>
                    <div className={s.metrica}>
                      <span className={s.metricaChave}>Câmeras</span>
                      <span className={s.metricaValor}>
                        {saude ? `${saude.cameras_online}/${saude.cameras_total}` : '—'}
                      </span>
                    </div>
                    <div className={s.metrica}>
                      <span className={s.metricaChave}>FPS reconhecimento</span>
                      <span className={s.metricaValor}>{numero(saude?.fps)}</span>
                    </div>
                    <div className={s.metrica}>
                      <span className={s.metricaChave}>GPU °C</span>
                      <span className={s.metricaValor}>{numero(saude?.gpu_temp_c)}</span>
                    </div>
                    <div className={s.metrica}>
                      <span className={s.metricaChave}>FPS decode</span>
                      <span className={s.metricaValor}>{numero(saude?.decode_fps)}</span>
                    </div>
                  </div>
                </div>

                <div className={s.linhaSync}>
                  <span>Sincronização edge → cloud</span>
                  <span className={`${s.estadoLinha} ${s.tom[est?.tom ?? 'neutro']}`}>
                    {haQuanto(saude?.last_heartbeat)}
                  </span>
                </div>
              </div>
            )
          })}
        </div>
      )}

      {aba === 'saude' && (
        <div className={s.pagina}>
          <table className={s.tabela}>
            <thead>
              <tr>
                <th className={s.th}>Câmera</th>
                <th className={s.th}>Status</th>
                <th className={s.thNum}>Uptime 7d</th>
                <th className={s.thNum}>FPS alvo</th>
                <th className={s.thNum}>Último frame</th>
              </tr>
            </thead>
            <tbody>
              {cameras.map((cam) => {
                const est = estadoDaCamera(cam)
                return (
                  <tr key={cam.id}>
                    <td className={s.tdNome}>{cam.name}</td>
                    <td className={s.td}>
                      <span className={`${s.celulaEstado} ${s.tom[est.tom]}`}>
                        <est.Icone size={13} strokeWidth={1.7} aria-hidden="true" />
                        {est.palavra}
                      </span>
                    </td>
                    <td className={s.tdNum}>—</td>
                    <td className={s.tdNum}>{numero(cam.fps_target)}</td>
                    <td className={s.tdNum}>{haQuanto(cam.last_seen)}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
          <p className={s.nota}>
            &quot;Uptime 7d&quot; fica vazio de propósito: não existe endpoint de uptime por câmera —
            só por site (heartbeat do edge). &quot;FPS alvo&quot; é o configurado na câmera, não o
            medido.
          </p>
        </div>
      )}

      {aba === 'escopo' && <AbaEscopo cameras={cameras} podeEditar={podeConfigurar} />}

      {aba === 'desempenho' && (
        <AbaDesempenho
          cameras={cameras}
          selecionada={selecionada}
          sites={sites}
          podeConfigurar={podeConfigurar}
          nomeDoSite={nomeDoSite}
          aoEscolherCamera={() => setAba('cameras')}
          aoSalvar={aplicarConfigSalva}
        />
      )}

      {modais}
    </div>
  )
}

export default Cameras
