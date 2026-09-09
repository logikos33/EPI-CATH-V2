/**
 * Régua da projeção de bounding box.
 *
 * O bug que estes testes travam (medido em 09/09, com o edge da RVB em
 * operação): alerta vindo do box grava `bbox_unidade: 'pixels_xywh_streammux'`,
 * as telas filtravam por `'pixels_xywh_frame_original'`, e **nenhuma caixa
 * aparecia**. Depois, mesmo casando a unidade, dividir pelo tamanho da imagem
 * (1920×1080) uma caixa medida no streammux (1280×720) põe a caixa a 2/3 do
 * lugar certo.
 */
import { describe, expect, it } from 'vitest'

import {
  BBOX_FRAME_ORIGINAL,
  BBOX_STREAMMUX,
  estiloDaCaixa,
  projetaveis,
  referenciaDaCaixa,
  type Bbox,
} from './bboxProjecao'

const IMAGEM = { w: 1920, h: 1080 } // evidência capturada do RTSP
const MUX = [1280, 720] as [number, number] // quadro do streammux do DeepStream

describe('referência do quadro', () => {
  it('caixa do quadro original é medida contra a própria imagem', () => {
    const ref = referenciaDaCaixa({ bbox: [10, 20, 30, 40], bbox_unidade: BBOX_FRAME_ORIGINAL }, IMAGEM)
    expect(ref).toEqual(IMAGEM)
  })

  it('caixa do streammux é medida contra o frame_wh que veio no evento', () => {
    const ref = referenciaDaCaixa(
      { bbox: [10, 20, 30, 40], bbox_unidade: BBOX_STREAMMUX, frame_wh: MUX },
      IMAGEM,
    )
    // A asserção que mata o bug: NÃO é a imagem.
    expect(ref).toEqual({ w: 1280, h: 720 })
  })

  it('streammux sem frame_wh é inprojetável — não se chuta a resolução do mux', () => {
    expect(referenciaDaCaixa({ bbox: [1, 2, 3, 4], bbox_unidade: BBOX_STREAMMUX }, IMAGEM)).toBeNull()
    expect(
      referenciaDaCaixa({ bbox: [1, 2, 3, 4], bbox_unidade: BBOX_STREAMMUX, frame_wh: [0, 0] }, IMAGEM),
    ).toBeNull()
  })

  it('unidade desconhecida ou ausente não desenha — melhor nenhuma caixa que caixa mentirosa', () => {
    expect(referenciaDaCaixa({ bbox: [1, 2, 3, 4], bbox_unidade: 'inventada' }, IMAGEM)).toBeNull()
    expect(referenciaDaCaixa({ bbox: [1, 2, 3, 4] }, IMAGEM)).toBeNull()
    expect(referenciaDaCaixa({ bbox_unidade: BBOX_FRAME_ORIGINAL }, IMAGEM)).toBeNull()
  })
})

describe('estilo da caixa', () => {
  it('projeta a caixa do edge no lugar CERTO da imagem', () => {
    // Caixa real medida hoje: x=771,4 num quadro de 1280 → 60,3% da largura.
    // Dividir por 1920 (o erro) daria 40,2% — dois terços do lugar certo.
    const v = { bbox: [771.4, 260.2, 43.5, 38.3] as Bbox, bbox_unidade: BBOX_STREAMMUX, frame_wh: MUX }
    const s = estiloDaCaixa(v, IMAGEM)!
    expect(s.left).toBe('60.2656%')
    expect(s.top).toBe('36.1389%')
    expect(s.left).not.toBe('40.1771%') // o que a tela fazia antes
  })

  it('caixa do quadro original continua projetando como sempre', () => {
    const v = { bbox: [960, 540, 192, 108] as Bbox, bbox_unidade: BBOX_FRAME_ORIGINAL }
    expect(estiloDaCaixa(v, IMAGEM)).toEqual({
      left: '50%', top: '50%', width: '10%', height: '10%',
    })
  })

  it('sem imagem medida ainda, não projeta nada', () => {
    const v = { bbox: [1, 2, 3, 4] as Bbox, bbox_unidade: BBOX_FRAME_ORIGINAL }
    expect(estiloDaCaixa(v, null)).toBeNull()
  })
})

describe('lista de projetáveis', () => {
  it('separa o que a tela sabe desenhar do que ela não sabe', () => {
    const vs = [
      { bbox: [1, 2, 3, 4] as Bbox, bbox_unidade: BBOX_FRAME_ORIGINAL },
      { bbox: [1, 2, 3, 4] as Bbox, bbox_unidade: BBOX_STREAMMUX, frame_wh: MUX },
      { bbox: [1, 2, 3, 4] as Bbox, bbox_unidade: BBOX_STREAMMUX }, // sem frame_wh
      { bbox: [1, 2, 3, 4] as Bbox, bbox_unidade: 'inventada' },
      { bbox_unidade: BBOX_STREAMMUX, frame_wh: MUX }, // sem bbox
    ]
    // Antes deste conserto, a do edge (índice 1) ficava de fora e a tela do
    // operador não mostrava caixa nenhuma em alerta vindo do box.
    expect(projetaveis(vs, IMAGEM)).toHaveLength(2)
  })

  it('sem imagem medida, nada é projetável', () => {
    const vs = [{ bbox: [1, 2, 3, 4] as Bbox, bbox_unidade: BBOX_FRAME_ORIGINAL }]
    expect(projetaveis(vs, null)).toEqual([])
  })
})
