/**
 * Projeção de bounding box: de que quadro a caixa fala, e em que imagem ela é
 * desenhada.
 *
 * O bug que originou este módulo (09/09): o edge entrou em operação e passou a
 * gravar `bbox_unidade: 'pixels_xywh_streammux'`. As três telas que desenham
 * caixa filtravam por `=== 'pixels_xywh_frame_original'`, então `desenhaveis`
 * ficava VAZIO e **nenhuma caixa aparecia** — em nenhum alerta vindo do box.
 *
 * O filtro estava certo no espírito: "melhor nenhuma caixa que caixa
 * mentirosa". O que faltava era ensinar a tela a projetar a unidade nova.
 *
 * ── Por que a unidade nova não é a mesma coisa ──────────────────────────────
 * A caixa do edge é medida no quadro do **streammux** do DeepStream, que roda
 * numa resolução própria (medido: 1280×720). A imagem de evidência é capturada
 * do RTSP em outra (medido: 1920×1080). Dividir a caixa pelo tamanho da imagem
 * — que é o que as telas faziam — põe a caixa a 2/3 do lugar certo.
 *
 * A referência correta vem junto no próprio evento, no campo `frame_wh`.
 *
 * ⚠️ SUPOSIÇÃO EXPLÍCITA: streammux e captura enquadram o MESMO campo de visão,
 * mudando só a resolução. Vale hoje (1280×720 e 1920×1080 são ambos 16:9, e o
 * streammux recebe o quadro inteiro da câmera). Se algum dia o pipeline
 * recortar ou adicionar barras, a projeção deixa de ser um escalonamento
 * uniforme e este módulo passa a mentir — por isso a suposição está escrita
 * aqui, e não subentendida.
 */

/** Caixa em pixels do quadro ORIGINAL da câmera (caminho da nuvem). */
export const BBOX_FRAME_ORIGINAL = 'pixels_xywh_frame_original'

/** Caixa em pixels do quadro do STREAMMUX do DeepStream (caminho do edge). */
export const BBOX_STREAMMUX = 'pixels_xywh_streammux'

export type Bbox = [number, number, number, number]

/** O que uma violação precisa ter para a caixa dela ser projetável. */
export interface ViolacaoProjetavel {
  bbox?: Bbox
  bbox_unidade?: string
  /** Largura e altura do quadro em que a `bbox` foi medida. */
  frame_wh?: [number, number] | number[]
}

/** Dimensões da imagem realmente exibida na tela. */
export interface Dimensao {
  w: number
  h: number
}

/**
 * Contra que quadro esta caixa deve ser dividida — ou `null` quando não dá
 * para saber.
 *
 * `null` é resposta legítima e importante: caixa de origem desconhecida NÃO é
 * desenhada. A tela avisa em vez de projetar palpite.
 */
export function referenciaDaCaixa(
  v: ViolacaoProjetavel,
  imagem: Dimensao,
): Dimensao | null {
  if (!v.bbox) return null

  if (v.bbox_unidade === BBOX_FRAME_ORIGINAL) {
    // Medida no quadro original: a imagem exibida É esse quadro.
    return imagem
  }

  if (v.bbox_unidade === BBOX_STREAMMUX) {
    const [w, h] = v.frame_wh ?? []
    // Sem `frame_wh` a caixa do streammux é inprojetável: a resolução do mux
    // não dá para adivinhar, e chutar a da imagem foi exatamente o erro.
    if (!w || !h || w <= 0 || h <= 0) return null
    return { w, h }
  }

  return null
}

/** As violações desta lista que a tela sabe projetar. */
export function projetaveis<T extends ViolacaoProjetavel>(
  vs: readonly T[],
  imagem: Dimensao | null,
): T[] {
  if (!imagem) return []
  return vs.filter((v) => referenciaDaCaixa(v, imagem) !== null)
}

/**
 * Caixa como percentuais da imagem exibida — prontos para `style`.
 *
 * Percentual em vez de pixel de propósito: a mesma caixa serve em qualquer
 * tamanho de exibição, e a lupa pode escalar o conjunto sem recalcular nada.
 */
export function caixaEmPorcento(
  [x, y, w, h]: Bbox,
  ref: Dimensao,
): { left: string; top: string; width: string; height: string } {
  // toFixed(4) só apara o ruído binário; 4 casas em % é sub-pixel em qualquer quadro.
  const pct = (n: number, total: number) => `${+((n / total) * 100).toFixed(4)}%`
  return {
    left: pct(x, ref.w),
    top: pct(y, ref.h),
    width: pct(w, ref.w),
    height: pct(h, ref.h),
  }
}

/**
 * Estilo da caixa de uma violação, ou `null` se ela não for projetável.
 * É o ponto único que as telas chamam — daí a garantia de que nenhuma delas
 * volte a dividir pelo quadro errado.
 */
export function estiloDaCaixa(
  v: ViolacaoProjetavel,
  imagem: Dimensao | null,
): { left: string; top: string; width: string; height: string } | null {
  if (!imagem) return null
  const ref = referenciaDaCaixa(v, imagem)
  if (!ref || !v.bbox) return null
  return caixaEmPorcento(v.bbox, ref)
}
