/**
 * O que esta tela não pode perder ao mudar de front.
 *
 * Três coisas medidas em produção morreram uma vez por serem "detalhe visual"
 * e voltaram como defeito de segurança operacional. Cada uma tem um caso aqui,
 * e cada caso FALHA se a migração as deixar pelo caminho:
 *
 *  · **Procedência** — o evento não pode PARECER ao vivo sem ser. O shadow
 *    roda sobre frames já coletados; a distância entre captura e gravação é o
 *    único dado que separa uma coisa da outra.
 *  · **Veredito humano ≠ veredito da IA** — a task Celery grava o MESMO
 *    'approve'/'reject' com `verified_by='claude-haiku'`. Ler o verdict sem
 *    olhar quem julgou apresenta decisão de máquina como julgamento de gente.
 *    E o MOTIVO que a pessoa escreveu tem de aparecer: é ele que ensina.
 *  · **Polaridade em TRÊS estados** — `is_violation` é NULLABLE e NULL é
 *    "ninguém decidiu", não "conformidade". Como `event_kind='violation'`
 *    colapsa TRUE e NULL no mesmo balde, quem desempata é o catálogo.
 *
 * Mais a paginação: `page`/`per_page`, o mesmo mecanismo do front atual e do
 * backend (`offset = (page-1)*per_page`). Trocar por offset cru ou cursor
 * nesta família já custou metade das linhas de uma página.
 */
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useParams } from 'react-router-dom'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { beforeEach, describe, expect, it, vi } from 'vitest'

/** `vi.mock` é içado acima das constantes do módulo — daí o `vi.hoisted`. */
const h = vi.hoisted(() => ({
  permissoes: ['alerts:read', 'alerts:feedback', 'alerts:export'] as string[],
  isSuperAdmin: false,
  gets: [] as string[],
  posts: [] as string[],
  /** Corpo de cada POST — é onde o `reason` estruturado aparece (ou não). */
  corpos: [] as unknown[],
  /** Servidor de mentira que GRAVA: deixa o teste conferir que a contagem da
   *  tela anda porque a lista foi RELIDA, não porque alguém carimbou o
   *  veredito no objeto local. */
  aoPostar: null as null | ((path: string, body: unknown) => void),
  /** Projeção de `GET /api/alerts/<id>` — a única que assina a URL do frame.
   *  A LISTA (`/alerts?...`) devolve só a chave, nunca a URL. */
  detalhe: {
    evidence_url: 'https://exemplo.invalido/frame.jpg',
    violations: [
      {
        class: 'no_helmet',
        confidence: 0.87,
        bbox: [10, 20, 100, 200],
        bbox_unidade: 'pixels_xywh_frame_original',
      },
    ],
  } as unknown,
  pagina: {
    alerts: [] as unknown[],
    total: 0,
    page: 1,
    per_page: 20,
    pages: 1,
  },
  falhar: false,
  /** Espelha o `ApiError` real: a tela lê `.status` para dizer o que falhou. */
  ApiErroFalso: class ApiErroFalso extends Error {
    status: number
    constructor(status: number, mensagem?: string) {
      super(mensagem ?? `HTTP ${status}`)
      this.status = status
    }
  },
  /** Erro do POST /verification/<id>/review — `null` = sucesso. */
  erroDoVeredito: null as Error | null,
}))

vi.mock('../../services/api', () => ({
  ApiError: h.ApiErroFalso,
  getToken: () => 't',
  api: {
    get: vi.fn((p: string) => {
      h.gets.push(p)
      if (h.falhar) return Promise.reject(new h.ApiErroFalso(500))
      // `/alerts/<id>` (sem querystring) é o DETALHE — rota diferente da
      // listagem, e a única que assina a URL da evidência.
      if (/^\/alerts\/[^?]+$/.test(p)) {
        return Promise.resolve({ success: true, data: { alert: h.detalhe } })
      }
      return Promise.resolve({ success: true, data: h.pagina })
    }),
    post: vi.fn((p: string, body?: unknown) => {
      h.posts.push(p)
      h.corpos.push(body)
      if (h.erroDoVeredito) return Promise.reject(h.erroDoVeredito)
      h.aoPostar?.(p, body)
      return Promise.resolve({ success: true })
    }),
    downloadBlob: vi.fn(() => Promise.resolve(new Blob(['a']))),
  },
}))

vi.mock('../../hooks/useAuth', () => ({
  useAuth: () => ({ can: (p: string) => h.permissoes.includes(p), isSuperAdmin: h.isSuperAdmin }),
}))

/** Catálogo REAL do módulo (GET /api/modules/epi/classes) — com polaridade. */
const CATALOGO = [
  { class_name: 'no_helmet', display_name: 'Sem capacete', polaridade: 'violacao' },
  { class_name: 'helmet', display_name: 'Capacete', polaridade: 'conformidade' },
  { class_name: 'no_earplug', display_name: 'Sem protetor de ouvido', polaridade: 'indefinida' },
]

vi.mock('../../hooks/useModuleClasses', () => ({
  useModuleClasses: () => ({
    classes: CATALOGO,
    loading: false,
    classLabel: (c: string) =>
      CATALOGO.find((k) => k.class_name === c)?.display_name ?? c,
  }),
}))

vi.mock('../../services/cameraService', () => ({
  cameraService: {
    list: () =>
      Promise.resolve([
        { id: 'cam-4', name: 'CAM-04', location: 'Expedição' },
        { id: 'cam-1', name: 'CAM-01', location: 'Doca Norte' },
      ]),
  },
}))

import { useToastStore } from '../../components/ui/Toast/useToast'
import { Eventos } from './Eventos'
import { rotaNova } from '../RotasNovas'

/** Formato real do backend (RVB Isolantes — CAM-01/04/07). */
const EVENTOS = [
  {
    id: 'e1',
    camera_id: 'cam-4',
    camera_name: 'CAM-04 Expedição',
    violations: [{ class: 'no_helmet', confidence: 0.87 }],
    acknowledged: false,
    created_at: '2026-08-20T14:32:00',
    timestamp: '2026-08-20T14:32:00',
    event_kind: 'violation' as const,
    verification_verdict: null,
    verified_by: null,
  },
  {
    id: 'e2',
    camera_id: 'cam-1',
    camera_name: 'CAM-01 Doca Norte',
    violations: [{ class: 'helmet', confidence: 0.92 }],
    acknowledged: true,
    created_at: '2026-08-20T14:07:00',
    timestamp: '2026-08-20T14:07:00',
    event_kind: 'compliance' as const,
    // A IA gravou 'reject' — NÃO é veredito humano.
    verification_verdict: 'reject',
    verified_by: 'claude-haiku',
  },
  {
    id: 'e3',
    camera_id: 'cam-7',
    camera_name: 'CAM-07 Linha 2',
    violations: [{ class: 'no_earplug', confidence: 0.51 }],
    acknowledged: false,
    // 12 min entre captura e gravação → coleta retroativa, não ao vivo.
    created_at: '2026-08-20T14:32:00',
    timestamp: '2026-08-20T14:20:00',
    event_kind: 'violation' as const,
    verification_verdict: 'approve',
    verified_by: 'user:u-1',
    verification_reason: 'A caixa pegou a luva do outro operador',
  },
  {
    id: 'e4',
    camera_id: 'cam-4',
    camera_name: 'CAM-04 Doca Sul',
    violations: [{ class: 'classe_sem_catalogo', confidence: 0.4 }],
    acknowledged: false,
    created_at: '2026-08-20T13:44:00',
    timestamp: '2026-08-20T13:44:00',
    event_kind: 'violation' as const,
    verification_verdict: null,
    verified_by: null,
  },
]

function montar(rota = '/epi/eventos') {
  return render(
    <MemoryRouter initialEntries={[rota]}>
      <Routes>
        <Route path="/epi/eventos" element={<Eventos />} />
      </Routes>
    </MemoryRouter>,
  )
}

const linhaDe = (texto: string) => screen.getByText(texto).closest('tr') as HTMLTableRowElement

/** O SELO da coluna VEREDITO — o que a tela AFIRMA, não o que ela oferece. */
const seloVeredito = (texto: string) =>
  linhaDe(texto).cells[5].querySelector('span')!.textContent ?? ''

beforeEach(() => {
  h.permissoes = ['alerts:read', 'alerts:feedback', 'alerts:export']
  h.isSuperAdmin = false
  h.gets.length = 0
  h.posts.length = 0
  h.corpos.length = 0
  h.aoPostar = null
  h.falhar = false
  h.erroDoVeredito = null
  useToastStore.setState({ toasts: [] })
  h.pagina = { alerts: EVENTOS, total: 4, page: 1, per_page: 20, pages: 1 }
})

describe('procedência — o evento não pode parecer ao vivo sem ser', () => {
  it('carimba "coleta retroativa" quando a gravação atrasa da captura', async () => {
    montar()
    await screen.findByText('CAM-07 Linha 2')
    expect(linhaDe('CAM-07 Linha 2').textContent).toContain('coleta retroativa')
  })

  it('não carimba nada quando captura e gravação são contemporâneas', async () => {
    montar()
    await screen.findByText('CAM-04 Expedição')
    // Ausência de selo = ausência de afirmação. Não existe carimbo "AO VIVO".
    expect(linhaDe('CAM-04 Expedição').textContent).not.toContain('retroativa')
    expect(screen.queryByText(/ao vivo/i)).toBeNull()
  })
})

describe('veredito humano ≠ veredito da IA', () => {
  it('verdict escrito pela IA aparece como "Não revisado"', async () => {
    montar()
    await screen.findByText('CAM-01 Doca Norte')
    // FALHA se a tela ler verification_verdict sem olhar verified_by. O selo é
    // o que a tela AFIRMA; o botão "Falso positivo" ao lado é oferta de ação.
    expect(seloVeredito('CAM-01 Doca Norte')).toContain('Não revisado')
    expect(seloVeredito('CAM-01 Doca Norte')).not.toContain('Falso positivo')
  })

  it('verdict de gente ("user:") aparece com o MOTIVO que a pessoa deu', async () => {
    montar()
    await screen.findByText('CAM-07 Linha 2')
    expect(seloVeredito('CAM-07 Linha 2')).toContain('Procedente')
    expect(linhaDe('CAM-07 Linha 2').textContent).toContain(
      'A caixa pegou a luva do outro operador',
    )
  })

  it('julgar é clique explícito e vai SEM motivo — o motivo é da tela de detalhe', async () => {
    montar()
    await screen.findByText('CAM-04 Expedição')
    const botoes = [...linhaDe('CAM-04 Expedição').querySelectorAll('button')]
    fireEvent.click(botoes.find((b) => b.textContent === 'Procedente')!)
    await waitFor(() => expect(h.posts).toContain('/verification/e1/review'))
  })
})

describe('polaridade em três estados', () => {
  it('conformidade vem do backend e vale sozinha', async () => {
    montar()
    await screen.findByText('CAM-01 Doca Norte')
    expect(linhaDe('CAM-01 Doca Norte').textContent).toContain('Conformidade')
  })

  it('violação só é afirmada quando o catálogo diz que a classe é violação', async () => {
    montar()
    await screen.findByText('CAM-04 Expedição')
    expect(linhaDe('CAM-04 Expedição').textContent).toContain('Violação')
  })

  it('classe com is_violation NULL é "Não definida" — nunca violação', async () => {
    montar()
    await screen.findByText('CAM-07 Linha 2')
    const linha = linhaDe('CAM-07 Linha 2')
    // FALHA se a tela usar event_kind cru: o backend colapsa NULL em 'violation'.
    expect(linha.textContent).toContain('Não definida')
    expect(linha.textContent).not.toContain('Violação')
  })

  it('classe fora do catálogo não recebe selo nenhum — sem afirmação', async () => {
    montar()
    await screen.findByText('CAM-04 Doca Sul')
    const linha = linhaDe('CAM-04 Doca Sul')
    for (const palavra of ['Violação', 'Conformidade', 'Não definida']) {
      expect(linha.textContent).not.toContain(palavra)
    }
  })

  it('paleta do veredito é disjunta da paleta da polaridade', () => {
    // Se "falso positivo" virar vermelho, veredito e violação viram a mesma
    // cor na mesma linha e o operador perde a diferença entre os dois eixos.
    const css = fs.readFileSync(
      path.join(path.dirname(fileURLToPath(import.meta.url)), 'Eventos.css.ts'),
      'utf-8',
    )
    const bloco = (nome: string) => {
      const i = css.indexOf(`export const ${nome} = styleVariants({`)
      return css.slice(i, css.indexOf('})', i))
    }
    expect(bloco('corVeredito')).not.toContain('lk.estado.nc')
    expect(bloco('corVeredito')).not.toContain('lk.estado.ok')
    expect(bloco('corPolaridade')).toContain('lk.estado.nc')
    expect(bloco('corPolaridade')).toContain('lk.estado.ok')
  })
})

describe('confiança da detecção (§9 paridade / contrato A1c)', () => {
  // Contrato A1c: medido no DEV (vereditos humanos reais, tenant RVB), a
  // confiança crua NÃO prevê acerto — "100%" acerta 58,3% das vezes, plano em
  // toda a faixa. Mostrar o número cru ao cliente é o selo de certeza que o
  // contrato proíbe (mesma família do "zero é uma afirmação" da casa).
  it('NÃO mostra o número cru de confiança ao cliente', async () => {
    montar()
    await screen.findByText('CAM-04 Expedição')
    expect(linhaDe('CAM-04 Expedição').textContent).not.toContain('87%')
  })

  it('sem precisão medida por classe, diz isso — nunca inventa nem mostra 0', async () => {
    montar()
    await screen.findByText('CAM-04 Expedição')
    expect(linhaDe('CAM-04 Expedição').textContent).toContain('precisão ainda não medida')
  })

  it('superadmin (visão de engenharia) continua vendo o número cru', async () => {
    h.isSuperAdmin = true
    montar()
    await screen.findByText('CAM-04 Expedição')
    expect(linhaDe('CAM-04 Expedição').textContent).toContain('87%')
  })

  it('sem confiança mostra travessão, não zero nem vazio', async () => {
    h.pagina = {
      ...h.pagina,
      alerts: [{ ...EVENTOS[0], violations: [{ class: 'no_helmet' }] }],
    }
    montar()
    await screen.findByText('CAM-04 Expedição')
    expect(linhaDe('CAM-04 Expedição').textContent).toContain('—')
  })
})

describe('paginação — page/per_page, como o backend calcula o offset', () => {
  it('pede a primeira página com per_page, nunca offset', async () => {
    montar()
    await screen.findByText('CAM-04 Expedição')
    expect(h.gets[0]).toContain('page=1')
    expect(h.gets[0]).toContain('per_page=20')
    expect(h.gets[0]).not.toContain('offset')
    expect(h.gets[0]).not.toContain('cursor')
  })

  it('avançar troca a PÁGINA e mantém o recorte', async () => {
    h.pagina = { ...h.pagina, pages: 3 }
    montar()
    await screen.findByText('CAM-04 Expedição')
    fireEvent.click(screen.getByLabelText('Próxima página'))
    await waitFor(() => expect(h.gets.at(-1)).toContain('page=2'))
    expect(h.gets.at(-1)).toContain('per_page=20')
    expect(h.gets.at(-1)).toContain('kind=violation')
  })

  it('trocar filtro volta para a página 1 — paginar sobre outro recorte pula linha', async () => {
    h.pagina = { ...h.pagina, pages: 3 }
    montar()
    await screen.findByText('CAM-04 Expedição')
    fireEvent.click(screen.getByLabelText('Próxima página'))
    await waitFor(() => expect(h.gets.at(-1)).toContain('page=2'))
    fireEvent.change(screen.getByLabelText('Status'), { target: { value: 'false' } })
    await waitFor(() => expect(h.gets.at(-1)).toContain('acknowledged=false'))
    expect(h.gets.at(-1)).toContain('page=1')
  })
})

describe('reconhecer é ato explícito', () => {
  it('o clique no botão manda o acknowledge daquele evento', async () => {
    montar()
    await screen.findByText('CAM-04 Expedição')
    const botoes = [...linhaDe('CAM-04 Expedição').querySelectorAll('button')]
    fireEvent.click(botoes.find((b) => b.textContent === 'Reconhecer')!)
    await waitFor(() => expect(h.posts).toContain('/alerts/e1/acknowledge'))
  })

  it('passar o mouse na linha NÃO reconhece', async () => {
    montar()
    await screen.findByText('CAM-04 Expedição')
    fireEvent.mouseOver(linhaDe('CAM-04 Expedição'))
    fireEvent.mouseEnter(linhaDe('CAM-04 Expedição'))
    expect(h.posts).toEqual([])
  })

  it('evento já reconhecido não oferece o botão', async () => {
    montar()
    await screen.findByText('CAM-01 Doca Norte')
    const botoes = [...linhaDe('CAM-01 Doca Norte').querySelectorAll('button')]
    expect(botoes.some((b) => b.textContent === 'Reconhecer')).toBe(false)
  })
})

describe('navegação lista→detalhe', () => {
  // O que a AlertDeepLink.test.tsx original (arquivada em
  // archive/front-antigo-epi-lote1-2026-08-30) provava sobre a lista antiga:
  // o clique leva ao detalhe do evento CERTO. Aqui o clique é no link
  // "Abrir →" (a linha inteira não navega mais — ver Eventos.tsx).
  function DetalheStub() {
    const { id } = useParams<{ id: string }>()
    return <span>detalhe:{id}</span>
  }

  it('clique em "Abrir →" leva ao detalhe do evento certo', async () => {
    render(
      <MemoryRouter initialEntries={['/epi/eventos']}>
        <Routes>
          <Route path="/epi/eventos" element={<Eventos />} />
          <Route path={rotaNova('/epi/eventos/:id')} element={<DetalheStub />} />
        </Routes>
      </MemoryRouter>,
    )
    await screen.findByText('CAM-04 Expedição')
    fireEvent.click(linhaDe('CAM-04 Expedição').querySelector('a')!)
    expect(await screen.findByText('detalhe:e1')).toBeTruthy()
  })
})

describe('permissão', () => {
  it('sem alerts:read a tela não busca nada e diz o porquê', async () => {
    h.permissoes = []
    montar()
    expect(await screen.findByText('Sem permissão')).toBeTruthy()
    expect(h.gets).toEqual([])
  })

  it('sem alerts:feedback não há BOTÃO de veredito (o selo continua)', async () => {
    h.permissoes = ['alerts:read']
    montar()
    await screen.findByText('CAM-04 Expedição')
    expect(screen.queryAllByRole('button', { name: 'Procedente' })).toHaveLength(0)
    expect(screen.queryAllByRole('button', { name: 'Falso positivo' })).toHaveLength(0)
    // O veredito já registrado segue LEGÍVEL — esconder o julgamento de gente
    // por falta de permissão de julgar seria apagar prova, não proteger.
    expect(linhaDe('CAM-07 Linha 2').textContent).toContain('Procedente')
  })

  it('sem alerts:export não há exportação', async () => {
    h.permissoes = ['alerts:read']
    montar()
    await screen.findByText('CAM-04 Expedição')
    expect(screen.queryByText(/Exportar CSV/)).toBeNull()
  })
})

describe('estados da rota', () => {
  it('vazio é vazio honesto, não linha inventada', async () => {
    h.pagina = { alerts: [], total: 0, page: 1, per_page: 20, pages: 1 }
    montar()
    expect(await screen.findByText('Nenhum evento no período')).toBeTruthy()
    // Sem tabela: nada de linha de exemplo para "não ficar vazio".
    expect(screen.queryByRole('table')).toBeNull()
  })

  it('erro diz o que falhou e deixa tentar de novo', async () => {
    h.falhar = true
    montar()
    expect(await screen.findByText('Não foi possível carregar')).toBeTruthy()
    // O detalhe DIZ o que falhou, sem a rota crua da API (rodada V1 do jargão:
    // `GET /api/alerts · HTTP 500` era o que o operador da fábrica lia). O
    // código numérico fica — é o que o suporte pede.
    const detalhe = screen.getByText(/não respondeu/)
    expect(detalhe.textContent).toContain('500')
    expect(detalhe.textContent, 'a rota da API voltou para a tela').not.toMatch(/GET |\/api\//)
    h.falhar = false
  h.erroDoVeredito = null
  useToastStore.setState({ toasts: [] })
    fireEvent.click(screen.getByText('Tentar novamente'))
    expect(await screen.findByText('CAM-04 Expedição')).toBeTruthy()
  })
})

describe('deep-link do sino continua valendo', () => {
  it('camera_id e kind da URL entram como filtro da primeira busca', async () => {
    montar('/epi/eventos?camera_id=cam-4&kind=compliance&acknowledged=false')
    await waitFor(() => expect(h.gets.length).toBeGreaterThan(0))
    expect(h.gets[0]).toContain('camera_id=cam-4')
    expect(h.gets[0]).toContain('kind=compliance')
    expect(h.gets[0]).toContain('acknowledged=false')
  })
})

describe('terceiro estado do filtro — contrato A1', () => {
  it('abertura padrão pede kind=violation, não observação — decisão registrada', async () => {
    // QUEBRA 3 (rodada de correção UX): sem isto, nada travava o default —
    // trocar 'violation' por '' silenciosamente reabriria a tela com
    // CONFORMIDADE misturada (o ruído que ADR-0065 tira daqui); trocar por
    // 'observacao' faria o indecidido dominar a abertura. A decisão (ver
    // comentário da interface `Filtros` em Eventos.tsx) é manter
    // 'violation': o indecidido tem opção própria e visível no MESMO
    // seletor ("Não definida"), não desaparece da tela, só da ABERTURA.
    montar()
    await waitFor(() => expect(h.gets.length).toBeGreaterThan(0))
    expect(h.gets[0]).toContain('kind=violation')
    expect(h.gets[0]).not.toContain('kind=observacao')
    expect(h.gets[0]).not.toContain('kind=compliance')
    expect((screen.getByLabelText('Tipo de evento') as HTMLSelectElement).value).toBe('violation')
  })

  it('"Não definida" é uma opção selecionável e pede kind=observacao', async () => {
    montar()
    await screen.findByText('CAM-04 Expedição')
    fireEvent.change(screen.getByLabelText('Tipo de evento'), {
      target: { value: 'observacao' },
    })
    await waitFor(() => expect(h.gets.at(-1)).toContain('kind=observacao'))
  })

  it('deep-link com kind=observacao chega como filtro da primeira busca', async () => {
    montar('/epi/eventos?kind=observacao')
    await waitFor(() => expect(h.gets.length).toBeGreaterThan(0))
    expect(h.gets[0]).toContain('kind=observacao')
  })
})

// ---------------------------------------------------------------------------
// ux2/dedup — 66 linhas de UMA câmera em 2min eram 2 situações reais. O
// operador não pode ver 65 "aguardando" que são o MESMO fato.
// ---------------------------------------------------------------------------

/** Mesma câmera+classe, gap de 20s (< janela de 60s) — UMA rajada. */
const RAJADA = [
  {
    id: 'r1', camera_id: 'cam-9', camera_name: 'CAM-09 Rajada',
    violations: [{ class: 'no_helmet', confidence: 0.8 }], acknowledged: false,
    created_at: '2026-08-25T13:39:00', timestamp: '2026-08-25T13:39:00',
    event_kind: 'violation' as const, verification_verdict: null, verified_by: null,
  },
  {
    id: 'r2', camera_id: 'cam-9', camera_name: 'CAM-09 Rajada',
    violations: [{ class: 'no_helmet', confidence: 0.81 }], acknowledged: false,
    created_at: '2026-08-25T13:39:20', timestamp: '2026-08-25T13:39:20',
    event_kind: 'violation' as const, verification_verdict: null, verified_by: null,
  },
  {
    id: 'r3', camera_id: 'cam-9', camera_name: 'CAM-09 Rajada',
    violations: [{ class: 'no_helmet', confidence: 0.79 }], acknowledged: false,
    created_at: '2026-08-25T13:39:40', timestamp: '2026-08-25T13:39:40',
    event_kind: 'violation' as const, verification_verdict: null, verified_by: null,
  },
]

describe('rajada (ux2/dedup) — representante + N repetições, nunca esconde', () => {
  beforeEach(() => {
    h.pagina = { alerts: RAJADA, total: 3, total_situacoes: 1, page: 1, per_page: 20, pages: 1 } as never
  })

  it('mostra só o representante por padrão, com alternador "+2 repetições"', async () => {
    montar()
    await screen.findByText(/\+2 repetiç/)
    expect(screen.getAllByText('CAM-09 Rajada')).toHaveLength(1)
  })

  it('expandir revela as N linhas originais — o dado nunca fica escondido', async () => {
    montar()
    fireEvent.click(await screen.findByText(/\+2 repetiç/))
    await waitFor(() => expect(screen.getAllByText('CAM-09 Rajada')).toHaveLength(3))
  })

  it('recolher esconde de novo — é um alternador, não uma perda de dado', async () => {
    montar()
    const alternador = await screen.findByText(/\+2 repetiç/)
    fireEvent.click(alternador)
    await waitFor(() => expect(screen.getAllByText('CAM-09 Rajada')).toHaveLength(3))
    fireEvent.click(screen.getByText(/\+2 repetiç/))
    await waitFor(() => expect(screen.getAllByText('CAM-09 Rajada')).toHaveLength(1))
  })

  it('cada repetição expandida mantém sua PRÓPRIA ação — nada oculto de ação', async () => {
    montar()
    fireEvent.click(await screen.findByText(/\+2 repetiç/))
    await waitFor(() => expect(screen.getAllByText('CAM-09 Rajada')).toHaveLength(3))
    // Nenhuma das 3 está reconhecida — as 3 continuam com o próprio botão.
    expect(screen.getAllByText('Reconhecer')).toHaveLength(3)
  })

  it('badge do cabeçalho conta SITUAÇÕES (total_situacoes), não linhas — e diz o EIXO', async () => {
    montar()
    // O eixo entra no rótulo porque os dois números da tela NÃO são a mesma
    // regra: `total_situacoes` agrupa por hora de GRAVAÇÃO (`created_at`) e as
    // linhas abaixo agrupam por hora de CAPTURA (`timestamp`). Medido no DEV
    // em 2026-09-05 (RVB, 30 dias, todos os tipos): 3.308 pela gravação,
    // 3.385 pela captura, 3.658 somando página a página — a MESMA frase
    // "mesma câmera+classe em <60s" valendo três números. Enquanto o backend
    // não muda de eixo, a tela ao menos não chama as duas coisas de
    // "situações" sem qualificar. Ver cabeçalho de `Eventos.tsx`.
    // Cabeçalho e rodapé passam a dizer a MESMA frase — antes o rodapé
    // omitia "NO PERÍODO" e cada um parecia contar uma coisa.
    expect(await screen.findAllByText('1 SITUAÇÕES (POR HORA DE GRAVAÇÃO) · 3 EVENTOS')).toHaveLength(2)
    expect(screen.queryByText('3 NO PERÍODO')).toBeNull()
  })

  it('o rótulo da rajada diz que a janela de 60s é de CAPTURA', async () => {
    montar()
    // A linha promete a regra que a tela de fato aplica (`timestamp ??
    // created_at`), não a que o badge usou. Sem o "de captura" as duas
    // frases se leem como a mesma coisa e nenhum operador consegue
    // reconciliar os números.
    expect(await screen.findByText(/\+2 repetições da mesma câmera\+classe em <60s de captura/)).toBeTruthy()
  })

  it('agrupa por CAPTURA: gravadas juntas, capturadas longe = DUAS linhas', async () => {
    // O conserto errado do desencontro acima seria mover a tela para
    // `created_at` "para bater com o badge". Medido no DEV: 175 alertas do
    // RVB foram GRAVADOS a menos de 60s do anterior e CAPTURADOS a mais de
    // 60s — carga em lote escrevendo no mesmo segundo o que a fábrica viveu
    // em dias distintos. Agrupar por gravação esconderia essas 175 linhas
    // atrás de um "+N repetições". Este caso trava o eixo certo.
    h.pagina = {
      alerts: [
        { ...RAJADA[0], id: 'x1', created_at: '2026-09-01T10:00:00', timestamp: '2026-08-20T08:00:00' },
        { ...RAJADA[1], id: 'x2', created_at: '2026-09-01T10:00:10', timestamp: '2026-08-25T17:30:00' },
      ],
      total: 2, total_situacoes: 1, page: 1, per_page: 20, pages: 1,
    } as never
    montar()
    await waitFor(() => expect(screen.getAllByText('CAM-09 Rajada')).toHaveLength(2))
    expect(screen.queryByText(/repetiç/)).toBeNull()
  })

  it('sem total_situacoes (backend/mock antigo), o badge cai para o total de linhas', async () => {
    h.pagina = { alerts: RAJADA, total: 3, page: 1, per_page: 20, pages: 1 } as never
    montar()
    await screen.findByText('3 NO PERÍODO')
  })
})

// ── procedência DECLARADA na LISTA (issue #670) ─────────────────────────────

describe('a lista diz QUEM desenhou a caixa, não só quando ela chegou', () => {
  /** Como o acervo de demonstração do DEV realmente está no banco: caixa
   *  desenhada por PESSOA e `created_at == timestamp` (o script grava os dois
   *  iguais), que é justamente o que faz o critério TEMPORAL nunca acender. */
  const SEMEADO = {
    id: 'e9',
    camera_id: 'cam-4',
    camera_name: 'CAM-09 Acervo',
    violations: [{
      class: 'no_helmet', confidence: 0.87,
      origem: 'anotacao_humana', lote: 'acervo_rvb_2026_08',
    }],
    acknowledged: false,
    created_at: '2026-08-20T14:32:00',
    timestamp: '2026-08-20T14:32:00',
    event_kind: 'violation' as const,
    verification_verdict: null,
    verified_by: null,
  }

  it('caixa desenhada por PESSOA é anunciada como tal — 4.609 dos 5.174 eventos do DEV', async () => {
    // FALHA ANTES: a linha só lia `classificarLatencia(timestamp, created_at)`.
    // Com os dois iguais o badge nunca acendia e a anotação humana aparecia
    // indistinguível de detecção do modelo em produção.
    h.pagina = { alerts: [SEMEADO], total: 1, page: 1, per_page: 20, pages: 1 }
    montar()
    await screen.findByText('CAM-09 Acervo')
    const linha = linhaDe('CAM-09 Acervo').textContent ?? ''
    expect(linha).toContain('anotação humana')
    expect(linha).toContain('demonstração')
  })

  it('sem origem declarada, o critério temporal segue valendo (nada se perdeu)', async () => {
    montar()
    await screen.findByText('CAM-07 Linha 2')
    expect(linhaDe('CAM-07 Linha 2').textContent).toContain('coleta retroativa')
  })

  it('declaração vence indício — evento semeado não vira "coleta retroativa"', async () => {
    h.pagina = {
      alerts: [{ ...SEMEADO, timestamp: '2026-08-20T14:20:00' }],
      total: 1, page: 1, per_page: 20, pages: 1,
    }
    montar()
    await screen.findByText('CAM-09 Acervo')
    const linha = linhaDe('CAM-09 Acervo').textContent ?? ''
    expect(linha).toContain('anotação humana')
    expect(linha).not.toContain('coleta retroativa')
  })
})

// ── 409: alguém julgou primeiro (issue #675) ────────────────────────────────

describe('veredito que colidiu com o de outra pessoa', () => {
  const FRASE = 'Maria Silva já avaliou este alerta há 2 minutos'

  const julgarComConflito = async (status: number, frase = FRASE) => {
    h.permissoes = ['alerts:read', 'alerts:feedback', 'verification:write']
    h.erroDoVeredito = new h.ApiErroFalso(status, frase)
    montar()
    await screen.findByText('CAM-04 Expedição')
    fireEvent.click(screen.getAllByRole('button', { name: 'Procedente' })[0])
    await waitFor(() => expect(h.posts.length).toBeGreaterThan(0))
  }

  it('409 informa QUEM julgou e QUANDO — não é erro do operador', async () => {
    // FALHA ANTES: o `catch` era cego e mostrava sempre o vermelho genérico
    // "Não foi possível registrar o veredito", que faz a pessoa clicar de novo
    // no que já foi resolvido.
    await julgarComConflito(409)
    await waitFor(() => {
      const toasts = useToastStore.getState().toasts
      expect(toasts.some((x) => x.variant === 'info' && x.description === FRASE)).toBe(true)
      expect(toasts.some((x) => x.variant === 'error')).toBe(false)
    })
  })

  it('recarrega a lista para a coluna VEREDITO mostrar a decisão que existe', async () => {
    const antes = h.gets.filter((p) => p.startsWith('/alerts?')).length
    await julgarComConflito(409)
    await waitFor(() =>
      expect(h.gets.filter((p) => p.startsWith('/alerts?')).length).toBeGreaterThan(antes + 1))
  })

  it('erro de verdade (500) continua vermelho — 409 não anistia o resto', async () => {
    await julgarComConflito(500, 'boom')
    await waitFor(() =>
      expect(useToastStore.getState().toasts.some((x) => x.variant === 'error')).toBe(true))
  })
})

/**
 * Issues #771 e #795 — a porta de entrada que não abre.
 *
 * A tela nasce em `periodo:'hoje' + kind:'violation'` (ADR-0065, decisão
 * mantida). No vazio ela oferecia UM caminho de saída: o botão "Limpar
 * filtros", que resetava para… `hoje` + `violation`. No primeiro acesso — que
 * é justamente quando o vazio aparece — clicar não mudava NADA (#795), e o
 * usuário nunca via os eventos de conformidade que o modelo produziu (#771:
 * 142 eventos, 100% conformidade, todos escondidos pelo filtro padrão).
 */
describe('vazio: a saída oferecida tem de sair do lugar (#771/#795)', () => {
  const vazio = () => {
    h.pagina = { alerts: [], total: 0, page: 1, per_page: 20, pages: 0 }
  }

  it('o botão do painel vazio AFROUXA o recorte — não repete o filtro de abertura', async () => {
    vazio()
    montar()
    await screen.findByText('Nenhum evento no período')

    const abertura = h.gets.at(-1) as string
    expect(abertura, 'a tela abre em violações — premissa da #771').toContain('kind=violation')

    fireEvent.click(screen.getByRole('button', { name: /30 dias/ }))
    await waitFor(() => expect(h.gets.length).toBeGreaterThan(1))
    const depois = h.gets.at(-1) as string

    expect(depois, 'clicar refez a MESMA consulta — é o no-op da #795').not.toBe(abertura)
    expect(depois, 'continuou filtrando por violação — a #771 segue escondendo tudo').not.toContain('kind=')

    const q = new URLSearchParams(depois.split('?')[1])
    const dias =
      (Date.parse(q.get('end_date') as string) - Date.parse(q.get('start_date') as string)) / 86_400_000
    expect(dias, 'a janela continuou em "hoje"').toBeGreaterThan(20)
  })

  it('com o recorte já no máximo, o painel para de prometer e diz que o vazio é do acervo', async () => {
    vazio()
    montar()
    await screen.findByText('Nenhum evento no período')
    fireEvent.click(screen.getByRole('button', { name: /30 dias/ }))
    await waitFor(() => expect(h.gets.length).toBeGreaterThan(1))

    await waitFor(() =>
      expect(screen.queryByRole('button', { name: /30 dias/ }), 'o botão sobreviveu ao próprio efeito').toBeNull(),
    )
    expect(screen.getByText(/O vazio é do acervo, não do filtro/)).toBeTruthy()
  })
})

// ---------------------------------------------------------------------------
// A gaveta de evidência — julgar SEM SAIR DA LISTA
//
// O pedido do dono, nas palavras dele: data e hora local, acesso rápido à
// evidência para aprovar, "uma tela igual à da validação daquele momento —
// foto do frame inteiro e zoom — para aprovar ou não aprovar", que contabilize
// o evento, e a lista sempre da última para a primeira.
//
// Cada uma dessas seis coisas tem um caso abaixo, e cada caso FALHA se a
// correspondente for revertida (conferido revertendo uma a uma).
// ---------------------------------------------------------------------------

const DIR_TELA = path.dirname(fileURLToPath(import.meta.url))

/** Mensagem que o servidor devolve no 409 — diz QUEM julgou e QUANDO. */
const FRASE_409 = 'Maria Silva já avaliou este alerta há 2 minutos'

/** Abre a gaveta a partir da LINHA — é o caminho que o dono pediu. */
async function abrirEvidencia(camera: string) {
  fireEvent.click(within(linhaDe(camera)).getByRole('button', { name: /Ver evidência/ }))
  return await screen.findByRole('dialog')
}

describe('a lista vai da última para a primeira, e diz a hora local', () => {
  it('as linhas saem da captura mais NOVA para a mais antiga', async () => {
    montar()
    await screen.findByText('CAM-04 Expedição')
    // 14:32 · 14:20 · 14:07 · 13:44 — o backend já ordena assim
    // (`ORDER BY a.timestamp DESC, a.id DESC`) e a tela não pode reembaralhar.
    const texto = document.body.textContent ?? ''
    const posicoes = ['CAM-04 Expedição', 'CAM-07 Linha 2', 'CAM-01 Doca Norte', 'CAM-04 Doca Sul']
      .map((nome) => texto.indexOf(nome))
    expect(posicoes.every((p) => p >= 0)).toBe(true)
    expect([...posicoes].sort((a, b) => a - b)).toEqual(posicoes)
  })

  it('imprime a hora LOCAL de quem lê, nunca o carimbo cru do servidor', async () => {
    montar()
    await screen.findByText('CAM-04 Expedição')
    const linha = linhaDe('CAM-04 Expedição').textContent ?? ''
    expect(linha).toContain(new Date('2026-08-20T14:32:00').toLocaleString('pt-BR'))
    expect(linha).not.toContain('2026-08-20T14:32:00')
  })
})

describe('gaveta de evidência aberta da própria linha', () => {
  it('busca a URL assinada do frame — a listagem não a tem, o detalhe assina', async () => {
    montar()
    await screen.findByText('CAM-04 Expedição')
    const gaveta = await abrirEvidencia('CAM-04 Expedição')
    expect(h.gets).toContain('/alerts/e1')
    const img = await within(gaveta).findByAltText('Frame da evidência')
    expect(img.getAttribute('src')).toBe('https://exemplo.invalido/frame.jpg')
  })

  it('traz o frame INTEIRO e o zoom — a mesma leitura da tela de validação', async () => {
    montar()
    await screen.findByText('CAM-04 Expedição')
    const gaveta = await abrirEvidencia('CAM-04 Expedição')
    expect(within(gaveta).getByRole('button', { name: 'Ampliar' })).toBeTruthy()
    expect(within(gaveta).getByRole('button', { name: 'Reduzir' })).toBeTruthy()
    expect(within(gaveta).getByText(/Frame inteiro/)).toBeTruthy()
  })

  it('a lupa é a MESMA das duas telas — nada de um segundo zoom copiado', () => {
    // Zoom copiado diverge do outro no primeiro ajuste do limite de pan, e o
    // defeito só aparece com a evidência ampliada a 8× — onde ninguém olha
    // duas vezes. A cola do DOM mora em `useLupa`; a matemática, em
    // `pages/epi/lupaEvidencia.ts`. Nenhuma das duas pode reaparecer na tela.
    for (const arquivo of ['EventoDetalhe.tsx', 'PainelEvidencia.tsx']) {
      const fonte = fs.readFileSync(path.join(DIR_TELA, arquivo), 'utf-8')
      expect(fonte).toContain("from './useLupa'")
      expect(fonte).not.toContain("addEventListener('wheel'")
      expect(fonte).not.toContain('setPointerCapture')
      expect(fonte).not.toContain('distanciaEntre')
    }
  })

  it('fechar devolve a lista, que continua inteira atrás', async () => {
    montar()
    await screen.findByText('CAM-04 Expedição')
    const gaveta = await abrirEvidencia('CAM-04 Expedição')
    fireEvent.click(within(gaveta).getByRole('button', { name: 'Fechar evidência' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(screen.getByRole('table')).toBeTruthy()
  })
})

describe('veredito pela gaveta — o que o dono chamou de "aprovar ou não aprovar"', () => {
  it('confirmar registra o veredito do evento certo', async () => {
    montar()
    await screen.findByText('CAM-04 Expedição')
    const gaveta = await abrirEvidencia('CAM-04 Expedição')
    fireEvent.click(within(gaveta).getByRole('button', { name: /Confirmar/ }))
    await waitFor(() => expect(h.posts).toContain('/verification/e1/review'))
    expect(h.corpos[0]).toEqual({ verdict: 'approve' })
  })

  it('rejeitar SEM motivo não manda nada — o motivo é o que ensina a recalibrar', async () => {
    montar()
    await screen.findByText('CAM-04 Expedição')
    const gaveta = await abrirEvidencia('CAM-04 Expedição')
    fireEvent.click(within(gaveta).getByRole('button', { name: /Falso positivo/ }))
    await screen.findByText('Selecione um motivo para rejeitar.')
    expect(h.posts).toEqual([])
  })

  it('com motivo escolhido, a justificativa ESTRUTURADA viaja junto', async () => {
    montar()
    await screen.findByText('CAM-04 Expedição')
    const gaveta = await abrirEvidencia('CAM-04 Expedição')
    fireEvent.change(within(gaveta).getByLabelText(/Motivo/), {
      target: { value: 'epi_presente' },
    })
    fireEvent.click(within(gaveta).getByRole('button', { name: /Falso positivo/ }))
    await waitFor(() => expect(h.posts).toContain('/verification/e1/review'))
    expect(h.corpos[0]).toEqual({ verdict: 'reject', reason: 'epi_presente' })
  })

  it('ao decidir, AVANÇA para o próximo da lista sem fechar a gaveta', async () => {
    montar()
    await screen.findByText('CAM-04 Expedição')
    const gaveta = await abrirEvidencia('CAM-04 Expedição')
    expect(within(gaveta).getByText('1 de 4 nesta página')).toBeTruthy()
    fireEvent.click(within(gaveta).getByRole('button', { name: /Confirmar/ }))
    // Ordem visível: 14:32 → 14:20 → 14:07 → 13:44.
    await screen.findByText('2 de 4 nesta página')
    expect(within(screen.getByRole('dialog')).getByText('CAM-07 LINHA 2')).toBeTruthy()
  })

  it('a releitura NÃO remonta a tabela — é assim que a posição na lista fica', async () => {
    montar()
    await screen.findByText('CAM-04 Expedição')
    const tabelaAntes = screen.getByRole('table')
    const gaveta = await abrirEvidencia('CAM-04 Expedição')
    fireEvent.click(within(gaveta).getByRole('button', { name: /Confirmar/ }))
    // 1) lista inicial 2) detalhe do evento aberto 3) releitura pós-veredito.
    await waitFor(() => expect(h.gets.length).toBe(3))
    // A MESMA tabela, o MESMO nó do documento. Se o veredito acendesse o
    // carregamento de tela inteira, a tabela seria desmontada e remontada — e
    // com ela iriam embora a rolagem e a linha onde a pessoa estava. É esse o
    // "sair da lista" que esta rodada existe para matar; comparar o nó é o
    // único jeito de provar que ele não aconteceu (depois que tudo assenta,
    // as duas versões desenham a mesma coisa).
    expect(screen.getByRole('table')).toBe(tabelaAntes)
    expect(screen.queryByText('CARREGANDO EVENTOS')).toBeNull()
  })

  it('a contagem de trabalho que sobra cai NA HORA, e por releitura do servidor', async () => {
    // e1 e e4 sem veredito humano + e2 (veredito da IA, que não conta como
    // gente) = 3. e3 já tem veredito de pessoa.
    h.aoPostar = (p) => {
      if (!p.startsWith('/verification/e1/')) return
      const alerts = (h.pagina.alerts as Array<Record<string, unknown>>).map((a) =>
        a.id === 'e1' ? { ...a, verification_verdict: 'approve', verified_by: 'user:eu' } : a,
      )
      h.pagina = { ...h.pagina, alerts }
    }
    montar()
    await screen.findByText('3 SEM VEREDITO NESTA PÁGINA')
    const gaveta = await abrirEvidencia('CAM-04 Expedição')
    fireEvent.click(within(gaveta).getByRole('button', { name: /Confirmar/ }))
    expect(await screen.findByText('2 SEM VEREDITO NESTA PÁGINA')).toBeTruthy()
  })

  it('409 (outra pessoa julgou primeiro) informa e AVANÇA — não prende o operador', async () => {
    h.erroDoVeredito = new h.ApiErroFalso(409, FRASE_409)
    montar()
    await screen.findByText('CAM-04 Expedição')
    const gaveta = await abrirEvidencia('CAM-04 Expedição')
    fireEvent.click(within(gaveta).getByRole('button', { name: /Confirmar/ }))
    await waitFor(() => {
      const toasts = useToastStore.getState().toasts
      expect(toasts.some((x) => x.variant === 'info' && x.description === FRASE_409)).toBe(true)
      expect(toasts.some((x) => x.variant === 'error')).toBe(false)
    })
    expect(await screen.findByText('2 de 4 nesta página')).toBeTruthy()
  })

  it('evento já julgado por gente mostra o veredito registrado, não botões', async () => {
    montar()
    await screen.findByText('CAM-07 Linha 2')
    const gaveta = await abrirEvidencia('CAM-07 Linha 2')
    expect(within(gaveta).getByText('Procedente')).toBeTruthy()
    expect(within(gaveta).queryByRole('button', { name: /Confirmar/ })).toBeNull()
  })

  it('sem alerts:feedback a gaveta abre para VER, e diz por que não dá para julgar', async () => {
    h.permissoes = ['alerts:read']
    montar()
    await screen.findByText('CAM-04 Expedição')
    const gaveta = await abrirEvidencia('CAM-04 Expedição')
    expect(within(gaveta).queryByRole('button', { name: /Confirmar/ })).toBeNull()
    expect(within(gaveta).getByText(/não tem permissão para julgar/)).toBeTruthy()
  })
})
