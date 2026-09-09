# ADR-0072 — Ausência não tem pixel: o estágio 2 é classificador, não detector

- **Status:** Proposta
- **Data:** 2026-09-09
- **Módulo:** EPI · **Tenant:** RVB (`63c219d8-fbef-4f3c-a7c9-058c742482e2`)
- **Substitui na prática:** o esquema de classes do modelo servido hoje
- **Relacionada:** [ADR-0071](0071-o-validador-tem-tres-respostas-nao-duas.md) ·
  [avaliação de dois estágios, 14/08](../avaliacao-dois-estagios-classificacao-por-recorte.md)

---

## Contexto

O modelo servido no edge da RVB tem classes de **ausência** — `Sem protetor de
ouvido`, `Sem mascara`, `Sem Óculos`, `Sem Luvas`. Em produção ele desenha caixa
de violação sobre palete, asfalto e painel de máquina, com pessoas em outro
canto do quadro. A precisão medida contra veredito humano nos alertas reais do
edge é **33%** (n=51).

Existiam três treinos do RunPod de 02/09/2026 sobre os **mesmos 4.983 quadros**,
com o **mesmo split**, mudando só o esquema de classes — `v17a-presenca`,
`v17b-ausencia`, `v17c-partes`. Nunca foram lidos comparativamente. Este ADR é o
resultado dessa leitura.

## Medição

248 quadros de **teste**, retidos do treino dos três modelos. Limiar 0,30,
casamento por IoU ≥ 0,5.

### Por esquema, agregado

| esquema | modelo | precisão | recall | F1 |
|---|---|---:|---:|---:|
| partes do corpo + EPI | `11f1303c` | 0,62 | 0,48 | **0,54** |
| só presença | `04508616` | 0,70 | 0,33 | 0,45 |
| presença + **ausência** ← servido | `9cc62fd6` | 0,59 | 0,33 | 0,42 |

### A classe que domina a operação

`Sem protetor de ouvido` é 657 das detecções recentes em produção.

| | precisão | recall | F1 |
|---|---:|---:|---:|
| `Sem protetor de ouvido` (ausência, servido) | 0,16 | 0,14 | **0,15** |
| `orelha` (partes) | 0,65 | 0,62 | **0,63** |
| `protetor_auricular` (partes) | 0,61 | 0,45 | **0,52** |

**O modelo acha orelha e acha protetor. Não sabe dizer "orelha sem protetor".**
A informação está no quadro; a formulação da classe a destrói.

O motivo é físico, não estatístico: **ausência não tem aparência**. Não existe
pixel de "protetor que não está lá". Pedir a um detector que produza uma caixa
para uma ausência é pedir que ele invente um enquadramento para nada — e o lugar
onde ele inventa é arbitrário. Daí as caixas no palete.

## A hipótese que testamos e que FALHOU

Se o modelo acha as duas presenças, a violação deveria sair da geometria:
*orelha sem `protetor_auricular` sobreposto*. Contra o gabarito humano de
`v17b`, varrendo cobertura (0,02–0,50) e limiares de ambas as classes:

| formulação | precisão | recall | F1 |
|---|---:|---:|---:|
| detectada direto | 0,23 | 0,19 | 0,21 |
| **derivada por geometria** (melhor ajuste de toda a varredura) | 0,17 | 0,75 | **0,28** |

Ganha recall, perde precisão, e nenhum ajuste sai desse patamar.

**Por que falha, e é a lição que generaliza:** a regra derivada dispara quando
*não* se detecta o protetor. Com recall de `protetor_auricular` em **0,45**, mais
da metade dos protetores reais é perdida — e cada perda vira uma violação falsa
sobre uma pessoa **protegida**. Trocamos "detectar uma ausência" por "falhar em
detectar uma presença": o silêncio do detector virou alarme. É o mesmo defeito um
nível abaixo.

## Decisão

**O estágio 2 é um CLASSIFICADOR sobre o recorte, não um detector cuja não-
detecção significa violação.**

```
estágio 1  detector de pessoa/parte  →  recorte (orelha, cabeça, mão)
estágio 2  classificador multilabel  →  P(protetor|recorte) ∈ [0,1]
violação   =  P abaixo do limiar, com as três faixas da ADR-0071
```

Três propriedades que só o classificador tem:

1. **Sempre responde.** Não existe "não detectei" — existe 0,82 ou 0,11. A
   distinção entre *não vi* e *não tem* deixa de ser feita por omissão.
2. **A saída é calibrável.** Uma probabilidade tem limiar; uma não-detecção não
   tem. É o que a ADR-0071 precisa para ter três faixas de verdade.
3. **Não precisa enquadrar nada.** O enquadramento vem do estágio 1, sobre uma
   presença real. Caixa em palete deixa de ser possível por construção.

### Consequências aceitas

- O modelo servido hoje sai do caminho — o esquema de classes dele é o defeito.
- Custo de GPU: com o tracker NvDCF e `interval=1` ligados em 09/09, a GPU do
  Orin caiu de **97% travado** para média **16%** (mediana 3%, p90 75%, n=68) com
  os 17 streams intactos a 3 fps. O orçamento para o estágio 2 existe **porque**
  essa medição foi feita; antes dela não existia.
- O estágio 1 já roda no edge desde antes disto (~8.620 recortes de pessoa) — só
  o caminho SERVIDO continua single-stage.

### O que esta decisão NÃO afirma

- **Não afirma que o classificador vai funcionar.** É o caminho que a medição
  indica, não um resultado medido. O que está medido é que as duas formulações
  atuais não funcionam e por quê.
- **Não afirma que o modelo de partes é o final.** Ele é a melhor base medida
  (F1 0,54 agregado) e tem as classes certas para recortar.
- O gabarito de `v17b` marca a violação onde o anotador achou; a regra derivada
  marcava na orelha. Parte da diferença de IoU pode ser convenção de
  enquadramento — por isso a tabela acima também traz o acerto por quadro, que
  independe de enquadramento, e a conclusão não muda.

## Como saber se erramos

O classificador do estágio 2 precisa bater **P ≥ 0,60 na faixa de aprovação
automática** contra veredito humano em campo virgem, com intervalo de Wilson 95%
que não cruze a precisão de hoje (0,33). Se não bater, a decisão volta à mesa: o
problema seria de dado, não de formulação, e o caminho passa a ser coleta
dirigida de protetor auricular em vez de arquitetura.
