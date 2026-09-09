/**
 * CameraModelScope — aba "Modelos por câmera" (Treinamento).
 *
 * Por câmera: qual modelo treinado responde e quais classes dele valem
 * (escopo). Contrato (WS-C2, migration 100):
 *   GET  /api/cameras/model-config?module=<m>       → deployments ativos do
 *        tenant, mapa por camera_id — 1 chamada por MÓDULO distinto (hoje 1);
 *        é a FONTE DE VERDADE do que está gravado. A versão por câmera
 *        (`/api/cameras/<id>/model-config`) continua existindo, mas ABRIR A
 *        ABA com ela estourava o pool de conexões da API nas 28 câmeras da
 *        RVB — a tela quebrava exatamente no tenant de tamanho real.
 *   POST /api/cameras/<id>/model-config             → novo deployment
 *        {model_id, module_code:<m>, config:{classes:[...], roi?, line?, thresholds?}}
 * `<m>` = `camera.active_module` (fallback 'epi') — o MESMO módulo pelo qual
 * o resolver do worker (tasks/inference.py::_resolve_camera_model) lê o
 * deployment; mandar 'epi' fixo pra câmera `quality` criaria deployment que
 * ninguém lê. Os modelos oferecidos são os do módulo da câmera.
 * O escopo É `config.classes` (validator exige ≥1 — "nenhuma classe" não é
 * representável). Não há rota de desativar deployment: câmera SEM deployment
 * cai no detector padrão do ambiente (DETECTOR_BACKEND/DETECTOR_MODEL_PATH do
 * worker), não num "ativo do módulo". Lista de modelos vem de GET
 * /api/v1/models (só os com artefato ONNX) e as classes de cada modelo de
 * GET /api/v1/models/<id> → lineage.dataset_version.class_distribution (o que
 * o detector de fato emite — menos as listadas em `__sem_suporte_treino__`).
 *
 * Gate: `cameras:configure` (superadmin + admin) — sem ele, somente leitura.
 * NÃO é `training:approve`: aquilo é aprovar treinamento, outra coisa, e por
 * ser só-superadmin deixava o admin da RVB — quem conhece a área — sem editar.
 *
 * Rótulo do modelo no seletor: `rotuloModelo` (services/modelDisplay.ts) —
 * nome · quantas classes · quando. O nome cru não identificava nada: o
 * cliente via todos como "Logikos" e o superadmin via "<motor> - Job <hash>".
 * A contagem só aparece quando é do MODELO (`classesDeclaradas`); o catálogo
 * do tenant segue sendo fallback de nomes para os chips, não uma medida.
 *
 * Honestidade (está na TELA, não só aqui): o escopo de classes vale hoje no
 * shadow E no worker cloud, que passou a descartar detecção fora do escopo
 * antes de virar violação (tasks/inference.py::_no_escopo_da_camera). O box
 * edge RVB ainda NÃO consome classe por câmera (poll_edge_config não envia) —
 * essa ponta segue aberta na #519.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../../services/api'
import { cameraService } from '../../services/cameraService'
import { dataCurta, rotuloModelo } from '../../services/modelDisplay'
import { useToast } from '../ui/Toast/useToast'
import { useAuth } from '../../hooks/useAuth'
import { Badge } from '../ui/Badge/Badge'
import { Banner } from '../ui/Banner/Banner'
import { Button } from '../ui/Button/Button'
import type { Camera, YoloClass } from '../../types'
import { vars } from '../../styles/theme.css'

export interface DeploymentConfig {
  classes?: string[]
  roi?: unknown
  line?: unknown
  thresholds?: Record<string, number>
}

export interface ModelDeployment {
  id: string
  model_id: string
  camera_id: string
  module_code: string
  config: DeploymentConfig | null
  status: string
  created_at: string
}

export interface RegistryModel {
  id: string
  name: string | null
  /** Nome voltado ao cliente (migration 129) — ver `nomeParaCliente`. */
  display_name?: string | null
  framework?: string | null
  r2_onnx_key?: string | null
  is_active: boolean
  module_code?: string | null
  created_at?: string
}

interface Envelope<T> { success: boolean; message?: string; data?: T }

interface ModelDetail {
  model: RegistryModel
  lineage: { dataset_version: { class_distribution?: Record<string, unknown> } | null }
}

interface Draft { modelId: string; classes: string[] }

const MODULO_PADRAO = 'epi'
const FRAMEWORK_LABELS: Record<string, string> = { yolox: 'YOLOX', rfdetr: 'RF-DETR' }

const SEM_SUPORTE = '__sem_suporte_treino__'

/** Nomes de classe que o modelo DE FATO prevê, a partir de class_distribution:
 * ignora chaves reservadas `__*` E subtrai as listadas em
 * `__sem_suporte_treino__` (contadas na distribuição mas excluídas do treino —
 * versioning_v2.py). Fallback = catálogo. */
export function classesDoModelo(
  dist: Record<string, unknown> | null | undefined,
  fallback: string[],
): string[] {
  const raw = dist?.[SEM_SUPORTE]
  const semSuporte = new Set(Array.isArray(raw) ? raw.filter((x): x is string => typeof x === 'string') : [])
  const names = Object.keys(dist ?? {}).filter(k => !k.startsWith('__') && !semSuporte.has(k))
  return names.length ? names : fallback
}

/** Módulo pelo qual o worker resolve o deployment desta câmera. */
export function moduloDaCamera(cam: Pick<Camera, 'active_module'>): string {
  return cam.active_module?.trim() || MODULO_PADRAO
}

/** Config a enviar: espalha a existente (roi/line preservados), troca
 * `classes` e poda `thresholds` pra ⊆ classes (validator rejeita fora). */
export function montarConfig(base: DeploymentConfig | null | undefined, classes: string[]): DeploymentConfig {
  const config: DeploymentConfig = { ...(base ?? {}), classes }
  if (base?.thresholds) {
    config.thresholds = Object.fromEntries(
      Object.entries(base.thresholds).filter(([k]) => classes.includes(k)),
    )
  }
  return config
}

export function mesmoConjunto(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every(x => b.includes(x))
}

/** Escopo GRAVADO no deployment. `config.classes` ausente NÃO é "todas as
 * classes": é escopo não gravado. Devolver `todas` aqui pré-marcava tudo e
 * fazia a tela afirmar um escopo que ninguém escreveu — e, como `base` usava
 * o mesmo fallback, `mudou` ficava false e nem dava para corrigir. O POST
 * daqui sempre grava `classes` (geometry_validation exige ≥1), então um
 * deployment sem a chave veio de fora da API (script ad-hoc). */
export function draftDoDeployment(dep: ModelDeployment | undefined): Draft {
  if (!dep) return { modelId: '', classes: [] }
  const classes = dep.config?.classes
  return { modelId: dep.model_id, classes: Array.isArray(classes) ? classes : [] }
}

/** Botão-chip para (des)marcar UMA classe no escopo — substitui o
 * `<input type=checkbox>` nativo (achado B2, rodada UX 2026-08): tokenizado
 * (usa `vars`, branco-rotulado por tenant), altura de 1 linha, sem o
 * quadradinho do navegador que não segue tema nenhum. */
export interface ChipDeClasseProps {
  rotulo: string
  marcada: boolean
  podeEditar: boolean
  ariaLabel: string
  onAlternar: () => void
}

export function ChipDeClasse({ rotulo, marcada, podeEditar, ariaLabel, onAlternar }: ChipDeClasseProps) {
  return (
    <button
      type="button"
      aria-pressed={marcada}
      aria-label={ariaLabel}
      disabled={!podeEditar}
      onClick={onAlternar}
      style={{
        display: 'inline-flex', alignItems: 'center', height: 26, padding: '0 11px',
        borderRadius: vars.radius.full,
        border: `1px solid ${marcada ? vars.color.primary : vars.color.borderDefault}`,
        background: marcada ? vars.color.primaryAlpha : 'transparent',
        color: marcada ? vars.color.primary : vars.color.textSecondary,
        fontSize: 12, fontWeight: marcada ? 600 : 400,
        cursor: podeEditar ? 'pointer' : 'default',
        opacity: podeEditar ? 1 : 0.65,
        whiteSpace: 'nowrap',
      }}
    >
      {rotulo}
    </button>
  )
}

/** Linha de chips de classe — NÚCLEO COMPARTILHADO entre esta tela e
 * `app/epi/Cameras.tsx` (aba Escopo): antes duas cópias quase idênticas do
 * mesmo `<input type=checkbox>` (uma com estilo inline) — foi nessa mesma
 * duplicação que um dropdown de 3ª cópia vazou jargão nesta rodada.
 * `maxHeight` + scroll interno mantém a LINHA DA CÂMERA compacta mesmo com
 * módulos de mais classes: sem isto uma tabela de 14 câmeras crescia sem
 * limite (achado B2). Cada chamador decide o que fazer quando `classes`
 * chega vazio (`CameraModelScope` cai no catálogo-fallback; `Cameras.tsx`
 * não tem fallback e mostra "modelo não declara classes") — por isso este
 * componente só cuida da lista que já chegou pronta. */
export interface ClasseChipsProps {
  classes: string[]
  selecionadas: string[]
  podeEditar: boolean
  nomeCamera: string
  onAlternar: (cls: string) => void
}

export function ClasseChips({ classes, selecionadas, podeEditar, nomeCamera, onAlternar }: ClasseChipsProps) {
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '5px 8px', maxHeight: 62, overflowY: 'auto' }}>
      {classes.map(cls => (
        <ChipDeClasse
          key={cls}
          rotulo={cls}
          marcada={selecionadas.includes(cls)}
          podeEditar={podeEditar}
          ariaLabel={`Classe ${cls} em ${nomeCamera}`}
          onAlternar={() => onAlternar(cls)}
        />
      ))}
      {classes.length > 0 && selecionadas.length === 0 && (
        <span style={{ fontSize: 11, color: vars.color.warning }}>marque ≥1 classe</span>
      )}
    </div>
  )
}

/** Por que o Salvar está desabilitado — texto ao lado do botão em vez de um
 * cinza mudo sem explicação (achado B2). `null` = liberado (ou é problema
 * de permissão, que a tela já explica em outro lugar — não repete aqui). */
export function motivoBloqueioSalvar(
  podeEditar: boolean, modelId: string, classes: string[], mudou: boolean,
): string | null {
  if (!podeEditar) return null
  if (!modelId) return 'escolha um modelo'
  if (classes.length === 0) return 'marque ao menos 1 classe'
  if (!mudou) return 'sem alterações para salvar'
  return null
}

export function CameraModelScope({ classesCatalogo }: { classesCatalogo: YoloClass[] }) {
  const toast = useToast()
  const { can, isSuperAdmin } = useAuth()
  // Editar o escopo de uma câmera é CONFIGURAÇÃO DE CÂMERA, não aprovação de
  // treino: 'cameras:configure' já existe, já inclui admin, e sua própria
  // descrição no registry é "alterar configurações técnicas da câmera (FPS,
  // modelo, conexão)". Com 'training:approve' o admin da RVB via a aba em
  // somente leitura — e é ele quem conhece a área.
  const podeEditar = can('cameras:configure')

  const [loading, setLoading] = useState(true)
  const [cameras, setCameras] = useState<Camera[]>([])
  const [models, setModels] = useState<RegistryModel[]>([])
  // Guarda as classes que o modelo DECLARA (pode ser vazio), não já
  // misturadas com o catálogo: é o que permite o rótulo dizer "10 classes"
  // só quando o número é do modelo. O catálogo continua entrando como
  // fallback de NOMES para os chips, agora em `classesOferecidas`.
  const [classesDeclaradas, setClassesDeclaradas] = useState<Record<string, string[]>>({})
  const [deploymentByCamera, setDeploymentByCamera] = useState<Record<string, ModelDeployment>>({})
  const [drafts, setDrafts] = useState<Record<string, Draft>>({})
  const [saving, setSaving] = useState<string | null>(null)
  const [erro, setErro] = useState<string | null>(null)
  const [avisoAberto, setAvisoAberto] = useState(false)

  const fallback = useMemo(() => classesCatalogo.map(c => c.name), [classesCatalogo])
  const classesOferecidas = (modelId: string) => {
    const declaradas = classesDeclaradas[modelId]
    return declaradas?.length ? declaradas : fallback
  }

  const load = useCallback(async () => {
    setLoading(true)
    setErro(null)
    try {
      const [cams, modelsRes] = await Promise.all([
        cameraService.list(),
        api.get<Envelope<{ models: RegistryModel[] }>>('/v1/models'),
      ])
      const comArtefato = (modelsRes.data?.models ?? []).filter(m => !!m.r2_onnx_key)
      const ativas = cams.filter(c => c.is_active)
      // Um GET por MÓDULO distinto (hoje 1), não por câmera. A versão antiga
      // disparava as 28 do RVB em Promise.all e estourava o pool de conexões
      // da API — a tela quebrava exatamente no tenant de tamanho real, com
      // "connection pool exhausted", enquanto o banco estava folgado (5
      // conexões de 500). O gargalo era a concorrência da própria tela.
      const modulos = [...new Set(ativas.map(moduloDaCamera))]
      const [details, porModulo] = await Promise.all([
        Promise.allSettled(comArtefato.map(m => api.get<Envelope<ModelDetail>>(`/v1/models/${m.id}`))),
        Promise.all(modulos.map(m =>
          api.get<Envelope<{ deployments: Record<string, ModelDeployment> }>>(
            `/cameras/model-config?module=${encodeURIComponent(m)}`,
          ),
        )),
      ])
      const porModelo: Record<string, string[]> = {}
      details.forEach((r, i) => {
        const d = r.status === 'fulfilled' ? r.value.data : undefined
        // Fallback VAZIO: aqui é o que o modelo declara. Modelo que não
        // declara (ou detalhe que falhou) fica sem número no rótulo — o
        // catálogo do tenant vira nome de chip em `classesOferecidas`, nunca
        // uma contagem atribuída a este modelo.
        porModelo[comArtefato[i].id] = classesDoModelo(d?.lineage?.dataset_version?.class_distribution, [])
      })
      const porCamera: Record<string, ModelDeployment> = {}
      porModulo.forEach((r, i) => {
        // Só a câmera CUJO módulo é este: uma câmera de 'quality' não pode
        // herdar o deployment 'epi' de outra só porque veio no mesmo lote.
        const doModulo = new Set(
          ativas.filter(c => moduloDaCamera(c) === modulos[i]).map(c => c.id),
        )
        for (const [cameraId, dep] of Object.entries(r.data?.deployments ?? {})) {
          if (dep && doModulo.has(cameraId)) porCamera[cameraId] = dep
        }
      })
      const novos: Record<string, Draft> = {}
      for (const c of ativas) {
        novos[c.id] = draftDoDeployment(porCamera[c.id])
      }
      setCameras(ativas)
      setModels(comArtefato)
      setClassesDeclaradas(porModelo)
      setDeploymentByCamera(porCamera)
      setDrafts(novos)
    } catch (err) {
      // Sem isto a tabela caía no vazio e afirmava "Nenhuma câmera ativa no
      // tenant." — falso num tenant de 28 câmeras, e sem como tentar de novo.
      const msg = err instanceof Error ? err.message : 'Erro ao carregar modelos por câmera'
      setErro(msg)
      toast.error(msg)
    } finally {
      setLoading(false)
    }
  }, [toast, fallback])

  useEffect(() => { load() }, [load])

  const setDraft = (camId: string, patch: Partial<Draft>) =>
    setDrafts(prev => ({ ...prev, [camId]: { ...prev[camId], ...patch } }))

  const salvar = async (cam: Camera) => {
    const draft = drafts[cam.id]
    if (!podeEditar || !draft?.modelId || draft.classes.length === 0) return
    const dep = deploymentByCamera[cam.id]
    setSaving(cam.id)
    try {
      const res = await api.post<Envelope<{ deployment: ModelDeployment }>>(
        `/cameras/${cam.id}/model-config`,
        { model_id: draft.modelId, module_code: moduloDaCamera(cam), config: montarConfig(dep?.config, draft.classes) },
      )
      const novo = res.data?.deployment
      if (novo) {
        setDeploymentByCamera(prev => ({ ...prev, [cam.id]: novo }))
        setDraft(cam.id, draftDoDeployment(novo))
      }
      toast.success(`Modelo e escopo salvos para ${cam.name}`)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Erro ao salvar')
    } finally {
      setSaving(null)
    }
  }

  const cell: React.CSSProperties = {
    padding: '8px 10px', borderBottom: `1px solid ${vars.color.borderDefault}`,
    verticalAlign: 'top', fontSize: 13, color: vars.color.textPrimary,
  }
  const th: React.CSSProperties = {
    ...cell, fontSize: 11, fontWeight: 600, textTransform: 'uppercase',
    letterSpacing: '0.05em', color: vars.color.textMuted, textAlign: 'left',
  }
  // `width:100%` + `minWidth:0` em vez de `minWidth:220`: a largura natural de
  // um <select> é a da MAIOR opção, então o rótulo longo (nome · classes ·
  // data) empurrava a tabela para além da tela e a última coluna ficava
  // cortada na borda. Com a tabela em `table-layout: fixed`, o seletor cabe na
  // célula e o texto que sobra vira reticências do próprio navegador.
  const selectStyle: React.CSSProperties = {
    background: vars.color.bgSurface, border: `1px solid ${vars.color.borderStrong}`,
    borderRadius: 6, color: vars.color.textPrimary, padding: '6px 10px', fontSize: 13,
    width: '100%', minWidth: 0, cursor: podeEditar ? 'pointer' : 'default',
  }
  /** Uma linha que trunca com reticências — padrão da casa (`admin/Usuarios`):
   * o texto inteiro fica no `title`, então truncar não esconde informação. */
  const truncado: React.CSSProperties = {
    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
  }

  if (loading) {
    return <p style={{ margin: 0, fontSize: 12, color: vars.color.textMuted }}>Carregando modelos por câmera...</p>
  }

  if (erro) {
    return (
      <Banner variant="danger">
        {erro} — a lista NÃO foi carregada. Isto não quer dizer "nenhuma câmera".{' '}
        <Button size="sm" variant="secondary" onClick={() => { void load() }}>Tentar de novo</Button>
      </Banner>
    )
  }

  return (
    <div style={{ background: vars.color.bgElevated, borderRadius: 8, overflowX: 'auto' }}>
      {/* O texto integral (8 linhas) empurrava a tabela para fora da primeira
          tela toda vez que alguém abria a aba. Ele diz o que ninguém pode
          deixar de ler UMA vez — capacidade ≠ exigência — mas não precisa ser
          relido a cada visita: fica a distinção em uma linha e "Entenda"
          revela o texto original, palavra por palavra. */}
      <Banner variant="warning">
        <span>
          O escopo abaixo é a <strong>capacidade</strong> do modelo em cada câmera — não é a{' '}
          <strong>exigência</strong> do cliente.{' '}
        </span>
        <Button
          size="sm"
          variant="ghost"
          // `color: inherit` porque o ghost pinta com `textMuted`, que dentro
          // do aviso (fundo âmbar, texto âmbar) fica cinza apagado e passa por
          // botão desabilitado — justamente o que ninguém clicaria.
          style={{ color: 'inherit', textDecoration: 'underline', padding: '0 6px' }}
          aria-expanded={avisoAberto}
          onClick={() => setAvisoAberto(v => !v)}
        >
          {avisoAberto ? 'Ocultar' : 'Entenda'}
        </Button>
        {/* `span` em bloco, não `<p>`: o Banner já embrulha o conteúdo num
            `span` (conteúdo de frase) e um `p` dentro dele é HTML inválido. */}
        {avisoAberto && (
          <span style={{ display: 'block', marginTop: 8 }}>
            O escopo abaixo é uma <strong>sugestão derivada de evidência</strong> (classe com ≥10 anotações
            humanas naquela câmera + protetor auditivo universal): é a CAPACIDADE do modelo ali, não a
            EXIGÊNCIA do cliente. Será substituído pela matriz de exigência da RVB assim que ela ficar
            pronta — até lá, uma área sem nenhuma luva anotada hoje fica sem escopo de luvas, e nenhuma
            violação de luva seria vista nela. Se uma classe que devia estar marcada não aparece aqui, é
            porque falta anotação dela nesta câmera — fale com quem cuida do treinamento. Onde este
            escopo já vale: no shadow sobre os frames que o equipamento envia e no reconhecimento que
            roda na nuvem, que já descarta detecção fora do escopo antes de virar violação. O
            equipamento instalado no site ainda NÃO aplica esse recorte por câmera — essa parte segue
            pendente.
            {isSuperAdmin && (
              <>
                {' '}
                {/* jargao-ok: modo avançado (superadmin) — referência de implementação, só quem mexe no código precisa disto */}
                <span style={{ opacity: 0.72 }}>{'Detalhe técnico: aplica em tasks/inference.py::_no_escopo_da_camera · pendências nas issues #519 (equipamento do site) e #535 (matriz de exigência).'}</span>
              </>
            )}
          </span>
        )}
      </Banner>
      <div style={{ padding: '10px 12px', fontSize: 12, color: vars.color.textMuted, display: 'flex', gap: 12, flexWrap: 'wrap' }}>
        <span>Modelo + classes que valem em cada câmera (escopo). Sem deployment = detector padrão do ambiente (não há como desativar por aqui — só trocar).</span>
        {!podeEditar && <span style={{ color: vars.color.warning }}>(somente leitura — requer permissão de aprovação)</span>}
        {models.length === 0 && <span style={{ color: vars.color.warning }}>Nenhum modelo pronto para uso neste tenant.</span>}
      </div>
      {/* `table-layout: fixed` + larguras em % = as 5 colunas cabem na largura
          disponível; sem isso o conteúdo (seletor e chips) dita a largura, a
          tabela passa da tela e "Último deploy" fica cortado na borda.
          `minWidth` mantém a tabela legível em tela estreita — aí sim o
          contêiner rola, de propósito. */}
      <table style={{ width: '100%', minWidth: 720, tableLayout: 'fixed', borderCollapse: 'collapse' }}>
        <colgroup>
          <col style={{ width: '16%' }} />
          <col style={{ width: '26%' }} />
          <col style={{ width: '30%' }} />
          <col style={{ width: '12%' }} />
          <col style={{ width: '16%' }} />
        </colgroup>
        <thead>
          <tr>
            <th style={th}>Câmera</th>
            <th style={th}>Modelo</th>
            <th style={th}>Classes no escopo</th>
            <th style={th}>Último deploy</th>
            <th style={th} />
          </tr>
        </thead>
        <tbody>
          {cameras.map(cam => {
            const draft = drafts[cam.id] ?? { modelId: '', classes: [] }
            const dep = deploymentByCamera[cam.id]
            const modulo = moduloDaCamera(cam)
            const modelosDoModulo = models.filter(m => (m.module_code || MODULO_PADRAO) === modulo)
            const model = models.find(m => m.id === draft.modelId)
            const todas = draft.modelId ? classesOferecidas(draft.modelId) : []
            const base = draftDoDeployment(dep)
            const mudou = draft.modelId !== base.modelId || !mesmoConjunto(draft.classes, base.classes)
            const podeSalvar = podeEditar && saving !== cam.id && mudou && !!draft.modelId && draft.classes.length > 0
            const framework = model?.framework ? FRAMEWORK_LABELS[model.framework] ?? model.framework : null
            return (
              <tr key={cam.id}>
                <td style={{ ...cell, ...truncado }} title={cam.name}>{cam.name}</td>
                <td style={cell}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
                    <select
                      aria-label={`Modelo da câmera ${cam.name}`}
                      value={draft.modelId}
                      disabled={!podeEditar || saving === cam.id}
                      style={selectStyle}
                      onChange={e => {
                        const id = e.target.value
                        setDraft(cam.id, { modelId: id, classes: id ? [...classesOferecidas(id)] : [] })
                      }}
                    >
                      {/* Sem rota de desativar: com deployment gravado, "sem" não é escolha válida. */}
                      <option value="" disabled={!!dep}>— sem deployment (detector padrão do ambiente)</option>
                      {modelosDoModulo.map(m => (
                        <option key={m.id} value={m.id}>
                          {rotuloModelo(m, isSuperAdmin, classesDeclaradas[m.id]?.length)}
                        </option>
                      ))}
                    </select>
                    {modulo !== MODULO_PADRAO && <Badge variant="neutral">{modulo}</Badge>}
                    {/* framework (YOLOX/RF-DETR) é stack interno — SÓ superadmin. */}
                    {framework && isSuperAdmin && <Badge variant="accent">{framework}</Badge>}
                  </div>
                </td>
                <td style={cell}>
                  {draft.modelId ? (
                    <ClasseChips
                      classes={todas}
                      selecionadas={draft.classes}
                      podeEditar={podeEditar && saving !== cam.id}
                      nomeCamera={cam.name}
                      onAlternar={cls => setDraft(cam.id, {
                        classes: draft.classes.includes(cls)
                          ? draft.classes.filter(c => c !== cls)
                          : [...draft.classes, cls],
                      })}
                    />
                  ) : (
                    <span style={{ color: vars.color.textMuted }}>—</span>
                  )}
                </td>
                {/* Data curta na célula, data completa no `title`: o formato
                    longo ("21/08/2026 07:00:00") era o que mais alargava a
                    coluna, e o segundo do deploy não muda decisão nenhuma. */}
                <td
                  style={{ ...cell, ...truncado, color: vars.color.textMuted }}
                  title={dep ? new Date(dep.created_at).toLocaleString('pt-BR') : undefined}
                >
                  {(dep && dataCurta(dep.created_at)) || '—'}
                </td>
                <td style={cell}>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 3, alignItems: 'flex-start' }}>
                    {/* nowrap só no BOTÃO (o motivo abaixo pode quebrar em duas
                        linhas): coluna estreita não pode partir "Salvar". */}
                    <Button
                      size="sm"
                      variant="primary"
                      style={{ whiteSpace: 'nowrap' }}
                      aria-label={`Salvar escopo de ${cam.name}`}
                      disabled={!podeSalvar}
                      loading={saving === cam.id}
                      onClick={() => salvar(cam)}
                    >
                      Salvar
                    </Button>
                    {motivoBloqueioSalvar(podeEditar, draft.modelId, draft.classes, mudou) && (
                      <span style={{ fontSize: 10.5, color: vars.color.textMuted }}>
                        {motivoBloqueioSalvar(podeEditar, draft.modelId, draft.classes, mudou)}
                      </span>
                    )}
                  </div>
                </td>
              </tr>
            )
          })}
          {cameras.length === 0 && (
            <tr><td style={cell} colSpan={5}>Nenhuma câmera ativa no tenant.</td></tr>
          )}
        </tbody>
      </table>
    </div>
  )
}
