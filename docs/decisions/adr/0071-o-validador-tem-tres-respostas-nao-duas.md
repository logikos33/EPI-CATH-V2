# ADR-0071 — O validador tem TRÊS respostas, não duas: confirma, descarta, ou não sabe

**Status:** Aceita · **Data:** 2026-09-09 · **Autores:** Logikos
**Relaciona:** ADR-0070 (a evidência é um frame), ADR-0067 (violação nasce de julgamento positivo de
ausência), ADR-0002 (detecções por pub/sub), ADR-0020 (rede do site)

## Contexto

Em 09/09/2026 o modelo entrou em operação real na RVB. No primeiro turno, com o dono julgando a fila
ao vivo, três medições mudaram o desenho:

**1 · A evidência é o quadro errado.** Medido em 200 alertas: a foto que o operador julga é capturada
**2,90 s no mínimo e 4,75 s na mediana** DEPOIS da detecção (p95 15,77 s; máximo 34,30 s). O
mecanismo está no código, não é inferência: `rtsp_frame_capture.py:43` abre conexão RTSP nova e pega
"one frame **right now**". Uma pessoa a 1,4 m/s anda ~6,6 m nesse intervalo. A tela pergunta "esta
pessoa está sem protetor?" mostrando um quadro em que ela pode nem estar.

**2 · Metade das caixas não está sobre pessoa.** Árbitro independente (Faster R-CNN COCO — não foi
treinado com nada deste projeto, o réu não escreveu o gabarito), 234 detecções: **12,4% dos quadros
não têm pessoa alguma**, e **45,7% das caixas** caem inteiramente fora de qualquer pessoa.

**3 · O modelo servido nunca foi medido.** As duas sentenças de campo virgem do projeto mediram o
`46a30ed9`, que **nunca esteve no box** (provado por sha256: o arquivo servido é o `8b3bd146`). O que
gera os alertas de hoje tem **um único número em toda a história do projeto**: F1 agregado 0,511
sobre 289 frames, sem linha por classe.

## Decisão

O caminho do alerta ganha um validador, e ele responde em **três faixas**, não em duas:

| faixa | decisão | por quê |
|---|---|---|
| ≥ `limiar_confirma` | vira alerta | o validador tem certeza de que aconteceu |
| entre os dois | **vai para a fila, marcado "em dúvida"** | quem decide é gente |
| ≤ `limiar_descarta` | descarta, e **nem sobe a imagem** | o validador tem certeza de que NÃO aconteceu |

E o validador roda sobre o quadro **do instante da detecção**, buscado no gravador por playback
ONVIF (`onvif_recorder_client.py::_get_replay_uri`), não sobre o still capturado depois.

## Por que TRÊS faixas, e não um limiar só

Com decisão binária, discordância do validador apaga a violação **em silêncio** — e o alerta que não
nasce ninguém vê. Num produto de segurança, o falso negativo é o pior desfecho possível e é invisível
por construção.

A faixa do meio troca o risco por trabalho: em vez de sumir, o caso duvidoso chega ao operador
marcado como duvidoso. É a mesma disciplina da ADR-0067 — violação nasce de **julgamento positivo**,
não de ausência de evidência contrária.

⚠️ **Os limiares não são escolhidos por chute.** O gabarito de calibração são os vereditos humanos que
o operador já produz na fila: cada "procedente" e "falso positivo" diz onde a confiança do validador
separa verdade de ruído. Ligar com número inventado seria a mesma "avaliação sem predição" que o
projeto já reprovou.

## Como entra: SOMBRA primeiro, sempre

O validador **não descarta nada** até medir. Em sombra ele registra o que teria feito — confirma,
duvida, descarta — e o alerta sobe assim mesmo. Só depois de comparar o descarte proposto com o
julgamento humano é que ele ganha autonomia, e ainda assim só na faixa de descarte.

O contador ingênuo não serve: ele é **maximizado pelo sucesso** (fábrica vazia à noite gera milhares
de descartes corretos). O que precisa ser visível é o **descarte errado** — por isso o frame do
desacordo é guardado para auditoria, e não morre dentro da chamada.

## Consequências

**Boas.** A evidência passa a mostrar o momento certo, o que conserta o julgamento humano e habilita
qualquer comparação automática de caixa. A imagem só sobe ao R2 quando o fato se confirma — menos
banda, menos armazenamento, e mais folga no teto anti-lockout. E a fila do operador passa a ser
ordenada por onde o julgamento dele rende mais.

**Custo.** Playback é conexão RTSP nova contra um gravador com anti-brute-force — 29 câmeras no mesmo
`192.168.35.18`. O desenho **não pode** aumentar a taxa atual (~4 capturas/min desde o PR #913), e
qualquer teto precisa ser medido, não estimado.

**Risco aceito.** Enquanto os limiares não estiverem calibrados, a faixa do meio será larga e a fila
crescerá. Isso é deliberado: é preferível trabalho a mais do que violação a menos.

## Alternativas descartadas

- **Limiar único (binário).** Descartada: apaga violação real em silêncio.
- **Guardar buffer de quadros no Orin.** Descartada por ora: o DeepStream usa `fakesink`, `[osd]
  enable=0`, e não há `pyds` no box — exigiria app custom em C.
- **Só rotular a evidência com "capturada N s depois".** Honesta, mas não resolve: o operador
  continuaria julgando o quadro errado, agora sabendo disso.
