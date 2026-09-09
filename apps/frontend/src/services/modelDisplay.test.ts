/**
 * O que o rótulo de modelo não pode errar:
 *
 *  · devolver um número de classes que não é do modelo (o catálogo do tenant
 *    é fallback de NOMES para os chips — imprimir o tamanho dele ao lado do
 *    nome afirmaria uma medida que ninguém apurou);
 *  · inventar data quando `created_at` não veio;
 *  · deixar o nome interno (motor + id do job) escapar para quem não é
 *    superadmin — é a política que `nomeInternoOuCliente` já guardava e que
 *    o rótulo maior não pode afrouxar.
 */
import { describe, expect, it } from 'vitest'

import { dataCurta, nomeParaCliente, rotuloModelo } from './modelDisplay'

/** ISO de um instante LOCAL — o rótulo é lido no fuso de quem abre a tela,
 * então a expectativa tem de ser construída no mesmo fuso (senão o teste
 * passa em São Paulo e falha no CI em UTC, sem nenhum bug envolvido). */
const isoLocal = (a: number, m: number, d: number, h: number, min: number) =>
  new Date(a, m - 1, d, h, min).toISOString()

describe('dataCurta', () => {
  it('dd/MM/AA HHhmm no fuso do navegador', () => {
    expect(dataCurta(isoLocal(2026, 8, 25, 3, 45))).toBe('25/08/26 03h45')
    expect(dataCurta(isoLocal(2025, 12, 1, 23, 5))).toBe('01/12/25 23h05')
  })

  it('sem data ou data inválida → null (quem chama encurta o texto, não inventa)', () => {
    expect(dataCurta(undefined)).toBeNull()
    expect(dataCurta(null)).toBeNull()
    expect(dataCurta('')).toBeNull()
    expect(dataCurta('nao-e-data')).toBeNull()
  })
})

describe('rotuloModelo', () => {
  const modelo = {
    id: 'a1b2c3d4-0000',
    name: 'RF-DETR - Job 3091cfc9',
    display_name: 'Logikos EPI Parcial',
    created_at: isoLocal(2026, 8, 25, 3, 45),
  }

  it('cliente: nome escolhido + quantas classes + quando (nunca o nome interno)', () => {
    expect(rotuloModelo(modelo, false, 10)).toBe('Logikos EPI Parcial · 10 classes · 25/08/26 03h45')
  })

  it('superadmin: mesmo rótulo, com o nome interno na frente', () => {
    expect(rotuloModelo(modelo, true, 10)).toBe('RF-DETR - Job 3091cfc9 · 10 classes · 25/08/26 03h45')
  })

  it('sem classes declaradas: omite o pedaço em vez de imprimir um número qualquer', () => {
    expect(rotuloModelo(modelo, false, 0)).toBe('Logikos EPI Parcial · 25/08/26 03h45')
    expect(rotuloModelo(modelo, false, undefined)).toBe('Logikos EPI Parcial · 25/08/26 03h45')
    expect(rotuloModelo(modelo, false, null)).toBe('Logikos EPI Parcial · 25/08/26 03h45')
  })

  it('1 classe no singular', () => {
    expect(rotuloModelo(modelo, false, 1)).toBe('Logikos EPI Parcial · 1 classe · 25/08/26 03h45')
  })

  it('sem created_at: o rótulo encurta, não inventa data', () => {
    expect(rotuloModelo({ ...modelo, created_at: null }, false, 10)).toBe('Logikos EPI Parcial · 10 classes')
  })

  it('modelo que ninguém rebatizou: cai no nome padrão do cliente, ainda com data e classes', () => {
    const cru = { ...modelo, display_name: null }
    expect(rotuloModelo(cru, false, 6)).toBe(`${nomeParaCliente(cru)} · 6 classes · 25/08/26 03h45`)
    expect(rotuloModelo(cru, false, 6)).not.toMatch(/job/i)
  })
})
