/**
 * Nome de modelo voltado ao cliente (rebranding F5-LEVE, política do dono):
 * cliente NUNCA vê stack interno (nome do motor "YOLO26 ..." / framework
 * YOLOX/RF-DETR). `display_name` (migration 129, `public.trained_models`) é
 * atribuído manualmente — nunca inferido do `name` interno nem computado a
 * partir da versão. Vazio = "Logikos" (ninguém rebatizou ainda).
 *
 * Nome interno (`name`) e `framework` seguem existindo no payload — só para
 * superadmin exibir (telas que fazem essa distinção decidem no call site).
 */
export const NOME_PADRAO_CLIENTE = 'Logikos'

export function nomeParaCliente(m: { display_name?: string | null }): string {
  return m.display_name?.trim() || NOME_PADRAO_CLIENTE
}

/**
 * Rótulo de modelo para QUALQUER superfície com dropdown/lista (option, select,
 * card): superadmin vê o nome interno cru (fallback "Modelo <id curto>"),
 * qualquer outro papel vê `nomeParaCliente`. Função única de propósito — achado
 * na prova DEV (2026-08-30): uma tela nova (`app/epi/Cameras.tsx` AbaEscopo)
 * copiou a UI de `CameraModelScope` sem essa regra, e o vazamento voltou por
 * uma superfície não coberta pelos testes. Todo novo dropdown de modelo DEVE
 * chamar esta função, nunca `m.name` cru.
 */
export function nomeInternoOuCliente(
  m: { id: string; name?: string | null; display_name?: string | null },
  isSuperAdmin: boolean,
): string {
  if (isSuperAdmin) return m.name || `Modelo ${m.id.slice(0, 8)}`
  return nomeParaCliente(m)
}

/**
 * Data curta de um ISO — `dd/MM/AA HHhmm`, no fuso do navegador. Devolve
 * `null` quando não há data (ou ela é inválida): quem chama decide o que
 * mostrar no lugar, e nunca cai numa data plausível inventada.
 *
 * Ano de 2 dígitos DE PROPÓSITO: dois modelos treinados em 25/08 de anos
 * diferentes ficam idênticos sem ele — e é justamente "qual é o mais novo"
 * que o operador vem olhar aqui.
 */
export function dataCurta(iso?: string | null): string | null {
  if (!iso) return null
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return null
  const pad = (n: number) => String(n).padStart(2, '0')
  return (
    `${pad(d.getDate())}/${pad(d.getMonth() + 1)}/${pad(d.getFullYear() % 100)} ` +
    `${pad(d.getHours())}h${pad(d.getMinutes())}`
  )
}

/**
 * Rótulo de modelo num seletor: NOME · N classes · QUANDO.
 *
 * O nome sozinho não identifica modelo nenhum: para o cliente todos caem no
 * mesmo "Logikos" enquanto ninguém rebatiza (`display_name` é NULL até
 * alguém atribuir), e para o superadmin o nome interno é "<motor> - Job
 * <hash>" — o id do job, que ninguém decora. Quantas classes e de quando
 * são o que de fato distingue um treino do outro.
 *
 * `qtdClasses` só entra quando o MODELO declara as classes: 0/null omite o
 * pedaço em vez de imprimir o tamanho do catálogo do tenant, que é fallback
 * de NOMES para os chips e não uma medida deste modelo. Mesma regra para a
 * data ausente — o rótulo encurta, não inventa.
 */
export function rotuloModelo(
  m: { id: string; name?: string | null; display_name?: string | null; created_at?: string | null },
  isSuperAdmin: boolean,
  qtdClasses?: number | null,
): string {
  const partes = [nomeInternoOuCliente(m, isSuperAdmin)]
  if (qtdClasses) partes.push(qtdClasses === 1 ? '1 classe' : `${qtdClasses} classes`)
  const quando = dataCurta(m.created_at)
  if (quando) partes.push(quando)
  return partes.join(' · ')
}
