"""GuardaPessoa — cena sem gente não vira alerta. Ou viraria, e a gente mede.

PROBLEMA (medido em 2026-09-09 sobre 60 frames de evidência REAIS da RVB, com
árbitro independente Faster R-CNN COCO @0.60 para `person`): **9 de 60 alertas
(15%) nasceram de cena vazia**. Por classe: `Uso incorreto de mascara` 3/10
(30%), `Sem protetor de ouvido` 6/49 (12%). Dois exemplos abertos a olho: uma
caçamba de retalhos de madeira a 0,702 e uma escada vazia a 0,642.

POR QUE O PIXEL, E NÃO O PAYLOAD: o modelo servido no box NÃO tem classe
`person` — `labels_46a30ed9.txt` traz 10 classes de EPI mais `recognition`
(interna). Nada na mensagem do barramento `det:*` responde "tem gente aqui?".
Só o quadro responde.

ONDE: no `DetectionRelay`, sobre o **mesmo** JPEG que ele já captura como
evidência (`SnapshotExecutor.capture_evidence`). Zero conexão RTSP nova — o
gravador da RVB pune tentativa repetida com lockout (CLAUDE.md) e o relay já
paga esse custo uma vez por evento acima do piso. Zero GPU: o ONNX roda em
`CPUExecutionProvider`, a GPU fica com o DeepStream e o live view.

⚠️  O RISCO QUE DECIDE TUDO — e a razão de o padrão ser SOMBRA
─────────────────────────────────────────────────────────────
Barrar por um detector de pessoa troca falso POSITIVO por falso NEGATIVO, e num
produto de segurança a violação que ninguém viu é o pior desfecho. Medido no
Orin, nos mesmos 60 frames, com o árbitro independente como verdade:

    malha  limiar  recall  barra  vazios barrados  GENTE SILENCIADA   ms/frame
     2x2    0,35    0,80     19          9               10             214
     2x2    0,10    0,92     10          6                4             214
     3x3    0,10    0,88     10          4                6             429
     4x4    0,10    0,98      5          4                1             733
     4x4    0,05    1,00      0          0                0             733

Com o ajuste que o coletor usa hoje (2x2 @ 0,35) o guarda barraria 19 dos 60
alertas — e **10 deles têm gente**: 53% do que ele silenciaria seria violação
real. Um dos frames tem QUATRO pessoas em plena luz, a 0,999 no árbitro. Ligar
`barrar` com o ajuste de hoje seria trocar 9 alertas falsos por 10 violações
mudas: pior que não fazer nada. É por isso que o padrão desta classe é 4x4 @
0,10 — o único ponto medido em que o guarda mata 4 dos 9 vazios silenciando 1
dos 51 verdadeiros — e que o modo padrão é SOMBRA, não `barrar`.

Mesmo em 4x4 @ 0,10 o intervalo de Wilson 95% sobre 50/51 chega a ~90%: o pior
caso admitido pelo dado ainda é 1 violação real silenciada a cada 10. Quem
assume esse risco é o dono do produto, não este módulo.

VIÉS CONHECIDO, NÃO MEDIDO: a evidência é um frame AO VIVO capturado depois que
o evento chegou (dump KITTI -> publicador -> pub/sub -> ONVIF), com alguns
segundos de atraso. Parte dos "sem pessoa" pode ser gente que SAIU do quadro,
não alucinação do modelo — e nesses casos o guarda estaria certo sobre o frame
e errado sobre o evento. As câmeras que mais erram são entradas e corredores,
exatamente onde se passa rápido. O modo sombra é o que separa as duas coisas.

COMO O FALSO NEGATIVO FICA VISÍVEL (o requisito difícil)
────────────────────────────────────────────────────────
O contador ingênuo `barrados` é maximizado pelo SUCESSO: fábrica vazia à noite
gera milhares de barrados corretos e nenhum sinal de erro. Três mecanismos aqui
existem só para tornar o erro visível:

1. **Sombra publica tudo.** Em `sombra` nada é barrado: o evento sobe, o alerta
   nasce com a imagem no R2, e o payload carrega `guarda_pessoa.veredito`. O
   falso negativo vira um alerta que se ABRE e se OLHA, com o pixel real, na
   própria tela do produto — sem UI nova, sem export, sem ferramenta.
2. **Anel de auditoria em `/dev/shm`.** Em `barrar` o alerta não existe mais na
   nuvem, então o pixel morreria dentro da chamada. O anel guarda os últimos N
   frames barrados com um `.json` ao lado (câmera, classes de EPI, confiança do
   modelo de EPI, score do detector de pessoa). É o único lugar onde um humano
   pode julgar o que foi silenciado.
3. **`barrados_alta_confianca`.** Contador que sobe quando o guarda ERRA, não
   quando acerta: evento barrado cujo modelo de EPI estava >= 0,85 de certeza.
   Cena vazia costuma produzir palpite fraco; o modelo afirmar com 0,9 que vê
   uma orelha desprotegida enquanto o detector não vê ninguém é o desacordo com
   maior chance de ser violação muda. Fábrica vazia não infla este número.
   ⚠️ É hipótese coerente com o dado, NÃO medida: os 60 frames de evidência não
   trouxeram a confiança do EPI por frame. O turno em sombra é o que a testa.

E o resumo separa `sem_pessoa`/`com_pessoa` **por câmera**: uma câmera em ~100%
de `sem_pessoa` enquanto as outras não é candidata a cegueira do guarda
(contraluz, gente pequena ao fundo) — leitura que o total global apaga.

CUSTO MEDIDO NO ORIN (com DeepStream a 65% e a stack toda no ar):
  · ~710 ms de CPU por evento avaliado, a ~4 capturas de evidência por minuto
    = 2,8 core-s/min = 4,7% de UM núcleo dos 8;
  · +93 MB de RSS no processo do edge-sync-agent (7 -> 100 MB num processo
    limpo: sessão onnxruntime + numpy + Pillow). O daemon roda hoje em 68 MB e
    a unit tem MemoryHigh=256M / MemoryMax=384M — sobra folga, mas é a conta
    que precisa ser refeita se alguém apertar o budget da unit.
O `PersonDetector` é importado DENTRO do builder, não no topo: com
`EDGE_GUARDA_PESSOA=off` o daemon não paga nem o import.

DEGRADA PARA O LADO SEGURO, SEMPRE: detector não carregado, ONNX ausente, erro
de inferência, frame ausente (abaixo do piso de evidência, teto estourado ou
pausa após falha) -> veredito `indeterminado` -> **publica**. Alerta a mais é
ruim; alerta que não chega é pior.

════════════════════════════════════════════════════════════════════════════════
CONTENÇÃO (2026-09-09) — a pergunta que faltava
════════════════════════════════════════════════════════════════════════════════
Tudo acima responde "tem gente NO QUADRO?". O caso que trouxe este bloco não é
esse: o operador abriu um alerta em que a caixa de violação estava sobre um
palete no pátio e a pessoa estava DENTRO do galpão, à esquerda. O guarda de
quadro aprova esse alerta — tem gente no quadro. E a caixa continua sendo
alucinação.

A pergunta certa é por caixa: **esta caixa cai sobre uma pessoa?**

    contencao = área(caixa ∩ união das pessoas) / área(caixa)

`contencao == 0` é a assinatura de EPI reconhecido onde não há corpo. A
distribuição medida sobre a evidência da RVB é bimodal com **45,7% em zero
exato** — a melhor forma possível para um limiar.

Duas asserções que este módulo NÃO faz:

· **Não converte espaço sem a referência.** A caixa do evento é medida no
  quadro do streammux do DeepStream (1280×720) e as pessoas no quadro da
  evidência (1920×1080 ou 2560×1440). Sem `frame_wh` no evento a caixa é
  inprojetável e a contenção sai `None` — cai no veredito de quadro, não chuta.
  Mesma regra do front (`apps/frontend/src/services/bboxProjecao.ts`), e mesma
  suposição explícita: streammux e captura enquadram o MESMO campo de visão.

· **Não barra por contenção até alguém medir.** `EDGE_GUARDA_CONTENCAO` nasce em
  `0.0` = só mede. O motivo é o viés já descrito acima, e a contenção é MAIS
  sensível a ele que a presença: quem andou dois metros continua no quadro
  (presença aprova) mas já saiu de baixo da caixa (contenção reprova). Enquanto
  a evidência for um frame atrasado, parte da contenção zero é movimento, não
  alucinação. Quem separa os dois é a captura no instante da detecção.

O número que calibra o limiar não precisa de harness: a contenção é gravada em
cada detecção do payload, `violations` sobe crua para `alerts` (routes.py), e um
turno de tráfego responde por SQL — com o veredito humano na linha ao lado.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

from . import anatomia

logger = logging.getLogger(__name__)

MODO_OFF = "off"
MODO_SOMBRA = "sombra"
MODO_BARRAR = "barrar"
_MODOS = (MODO_OFF, MODO_SOMBRA, MODO_BARRAR)

#: Ponto de operação MEDIDO (ver tabela no topo), não escolhido por gosto. É
#: mais caro que o do coletor (733ms contra 214ms) porque aqui o custo de errar
#: é uma violação muda, não um frame a menos no pool de treino.
_DEFAULT_CONFIANCA = 0.10
_DEFAULT_TILES = (4, 4)
#: RAM, não disco: o Orin tem 128GB que são SO + app, e disco cheio é
#: intertravamento do device (CLAUDE.md). 20 frames de 1080p ~ 11MB.
_DEFAULT_RING_DIR = "/dev/shm/recognition-guarda-pessoa"
_DEFAULT_RING_MAX = 20
#: Piso de confiança do modelo de EPI a partir do qual um evento barrado é
#: contado como desacordo forte — ver mecanismo (3) no topo.
_ALTA_CONFIANCA = 0.85
_RESUMO_A_CADA = 100

#: Fração da caixa de violação que precisa cair sobre alguém. **0.0 = só mede**
#: — ver o bloco CONTENÇÃO no topo: até a evidência ser o instante da detecção,
#: contenção zero mistura alucinação com gente que andou.
_DEFAULT_PISO_CONTENCAO = 0.0
#: Cortes do histograma que calibra o limiar a partir do tráfego real.
_FAIXAS_CONTENCAO = (0.0, 0.25, 0.50, 0.75)

_UNIDADE_STREAMMUX = "pixels_xywh_streammux"
_UNIDADE_FRAME = "pixels_xywh_frame_original"

#: `EDGE_GUARDA_ANATOMIA=barrar` liga o corte por parte do corpo. Nasce em
#: "só mede" pelo mesmo motivo da contenção: a régua é grosseira (não há pose,
#: só a caixa da pessoa) e quem assume o risco de silenciar é o dono do produto.
_ANATOMIA_SO_MEDE = "medir"
_ANATOMIA_BARRAR = "barrar"


@dataclass(frozen=True)
class Veredito:
    tem_pessoa: bool
    indeterminado: bool
    confianca: float
    ms: int
    #: Maior fração de caixa de violação que cai sobre alguém, ou `None` quando
    #: nenhuma caixa do evento era projetável (sem `frame_wh`, sem bbox).
    contencao: float | None = None
    caixas: int = 0
    pessoas: int = 0
    #: `False` = alguma caixa caiu numa parte do corpo incompatível com a
    #: classe (máscara no joelho). `None` = indeterminado, nunca reprovado.
    anatomia_ok: bool | None = None

    @property
    def rotulo(self) -> str:
        if self.indeterminado:
            return "indeterminado"
        return "com_pessoa" if self.tem_pessoa else "sem_pessoa"


class GuardaPessoa:
    """Decide se um evento de violação vira alerta, olhando o frame.

    *detector* é um `app.collector.person_detector.PersonDetector` (YOLOX-nano
    ONNX, Apache 2.0 — ZERO ultralytics no caminho servido, ADR-0043) ou
    qualquer objeto com `.detect(bytes) -> PersonResult`.
    """

    def __init__(
        self,
        detector: Any,
        modo: str = MODO_SOMBRA,
        ring_dir: str = _DEFAULT_RING_DIR,
        ring_max: int = _DEFAULT_RING_MAX,
        alta_confianca: float = _ALTA_CONFIANCA,
        piso_contencao: float = _DEFAULT_PISO_CONTENCAO,
        anatomia: str = _ANATOMIA_SO_MEDE,
    ) -> None:
        self._detector = detector
        self._modo = modo if modo in _MODOS else MODO_SOMBRA
        self._ring_dir = ring_dir
        self._ring_max = max(0, ring_max)
        self._alta = alta_confianca
        self._piso_contencao = piso_contencao
        self._anatomia = (
            anatomia if anatomia in (_ANATOMIA_SO_MEDE, _ANATOMIA_BARRAR) else _ANATOMIA_SO_MEDE
        )
        self._ring_i = 0
        self._avaliados = 0
        self._indeterminados = 0
        self._barrados = 0
        self._barrados_por_contencao = 0
        self._barrados_alta_confianca = 0
        # Histograma da contenção — é ele que calibra `piso_contencao` sem
        # harness nenhum: um turno de tráfego e o corte aparece.
        self._faixas_contencao: dict[str, int] = {}
        self._sem_projecao = 0
        # {"plausivel"|"lugar_errado"|"indeterminado": n} — o mesmo censo que
        # mediu 175 de 420 caixas na parte errada do corpo.
        self._anatomia_censo: dict[str, int] = {}
        self._barrados_por_anatomia = 0
        # {camera_id: [sem_pessoa, com_pessoa]} — a RAZÃO por câmera é o que
        # denuncia cegueira do guarda; o total global não denuncia nada.
        self._por_camera: dict[str, list[int]] = {}
        # {classe_de_epi: n} entre os barrados — mostra QUAL violação está
        # sendo silenciada, não só quantas.
        self._classes_barradas: dict[str, int] = {}

    @property
    def modo(self) -> str:
        return self._modo

    # ── decisão ──────────────────────────────────────────────────────────────

    def julgar(self, camera_id: str, payload: dict, frame_bytes: bytes) -> bool:
        """True = PUBLICA o evento; False = barra. **Nunca levanta.**

        Anota `payload["guarda_pessoa"]` sempre que consegue opinar — inclusive
        em sombra, que é justamente onde essa marca vira a prova: o alerta sobe,
        o operador abre, e o frame diz se o guarda teria acertado.
        """
        try:
            v = self._avaliar(frame_bytes, payload)
        except Exception:  # noqa: BLE001 — guarda nunca bloqueia alerta
            logger.warning("guarda_pessoa_falhou camera=%s", camera_id, exc_info=True)
            return True

        payload["guarda_pessoa"] = {
            "veredito": v.rotulo,
            "confianca": round(v.confianca, 3),
            "modo": self._modo,
            "ms": v.ms,
            # `None` quando nenhuma caixa era projetável — e `None` no JSON diz
            # "não sei", que é diferente de 0.0 ("olhei e não cai em ninguém").
            "contencao": None if v.contencao is None else round(v.contencao, 3),
            "pessoas": v.pessoas,
            "piso_contencao": self._piso_contencao,
            # `None` = indeterminado (pessoa sentada/cortada, ou classe fora do
            # mapa). Diferente de `False`, que é "caiu na parte errada".
            "anatomia_ok": v.anatomia_ok,
            "anatomia": self._anatomia,
        }
        self._contar(camera_id, v)

        if v.indeterminado or self._modo != MODO_BARRAR:
            return True

        fora_de_pessoa = (
            v.contencao is not None
            and self._piso_contencao > 0.0
            and v.contencao < self._piso_contencao
        )
        # SÓ `is False` barra: `None` é indeterminado e publica, sempre.
        parte_errada = self._anatomia == _ANATOMIA_BARRAR and v.anatomia_ok is False
        if v.tem_pessoa and not fora_de_pessoa and not parte_errada:
            return True

        if fora_de_pessoa:
            self._barrados_por_contencao += 1
        if parte_errada:
            self._barrados_por_anatomia += 1
        self._barrados += 1
        conf_epi = _confianca_epi(payload)
        if conf_epi >= self._alta:
            self._barrados_alta_confianca += 1
            logger.warning(
                "guarda_pessoa_barrado_alta_confianca camera=%s conf_epi=%.2f "
                "classes=%s motivo=%s — o modelo de EPI afirma com força e o "
                "detector discorda; frame no anel %s para auditoria",
                camera_id, conf_epi, _classes(payload),
                "caixa_fora_de_pessoa" if fora_de_pessoa
                else ("caixa_na_parte_errada" if parte_errada else "quadro_sem_pessoa"),
                self._ring_dir,
            )
        for classe in _classes(payload):
            self._classes_barradas[classe] = self._classes_barradas.get(classe, 0) + 1
        self._gravar_no_anel(camera_id, payload, v, frame_bytes)
        return False

    def _avaliar(self, frame_bytes: bytes, payload: dict) -> Veredito:
        t0 = time.monotonic()
        r = self._detector.detect(frame_bytes)
        if getattr(r, "undetermined", False):
            return Veredito(False, True, 0.0, int((time.monotonic() - t0) * 1000))

        quadro = _tamanho_do_quadro(frame_bytes)
        pessoas = _pessoas_em_fracao(r, quadro) if quadro else []
        contencao, caixas, anat = _anotar_contencao(payload, pessoas, quadro)
        return Veredito(
            tem_pessoa=bool(getattr(r, "found", False)),
            indeterminado=False,
            confianca=float(getattr(r, "max_confidence", 0.0) or 0.0),
            ms=int((time.monotonic() - t0) * 1000),
            contencao=contencao,
            caixas=caixas,
            pessoas=len(pessoas),
            anatomia_ok=anat,
        )

    # ── contadores ───────────────────────────────────────────────────────────

    def _contar(self, camera_id: str, v: Veredito) -> None:
        self._avaliados += 1
        if v.indeterminado:
            self._indeterminados += 1
        else:
            par = self._por_camera.setdefault(camera_id, [0, 0])
            par[1 if v.tem_pessoa else 0] += 1
        rotulo = {True: "plausivel", False: "lugar_errado", None: "indeterminado"}[v.anatomia_ok]
        self._anatomia_censo[rotulo] = self._anatomia_censo.get(rotulo, 0) + 1
        if v.contencao is None:
            self._sem_projecao += 1
        else:
            faixa = _faixa(v.contencao)
            self._faixas_contencao[faixa] = self._faixas_contencao.get(faixa, 0) + 1
        if self._avaliados % _RESUMO_A_CADA == 0:
            logger.info("guarda_pessoa_resumo %s", json.dumps(self.resumo()))

    def resumo(self) -> dict:
        return {
            "modo": self._modo,
            "avaliados": self._avaliados,
            "indeterminados": self._indeterminados,
            "barrados": self._barrados,
            "barrados_por_contencao": self._barrados_por_contencao,
            # Sobe quando o guarda ERRA, não quando acerta — ver o topo.
            "barrados_alta_confianca": self._barrados_alta_confianca,
            # {"0", "0-25", "25-50", "50-75", "75-100"} -> n. Zero exato tem
            # faixa própria de propósito: é a assinatura da alucinação.
            "contencao": dict(self._faixas_contencao),
            "contencao_sem_projecao": self._sem_projecao,
            "piso_contencao": self._piso_contencao,
            "barrados_por_anatomia": self._barrados_por_anatomia,
            # {"plausivel"|"lugar_errado"|"indeterminado": n} — mede o que a
            # contenção sozinha não vê: caixa sobre a pessoa, na parte errada.
            "anatomia": dict(self._anatomia_censo),
            "modo_anatomia": self._anatomia,
            "classes_barradas": dict(self._classes_barradas),
            # {camera: [sem_pessoa, com_pessoa]}
            "por_camera": {c: list(p) for c, p in self._por_camera.items()},
        }

    # ── anel de auditoria ────────────────────────────────────────────────────

    def _gravar_no_anel(
        self, camera_id: str, payload: dict, v: Veredito, frame_bytes: bytes
    ) -> None:
        """Últimos N frames barrados em RAM, com o contexto ao lado.

        ponytail: são os N MAIS RECENTES, não uma amostra uniforme do turno —
        se a auditoria precisar de amostra justa ao longo do dia, trocar por
        reservoir sampling (~6 linhas). Enquanto o modo for `sombra`, este anel
        nem roda: lá o frame já está no R2, ligado ao alerta.
        """
        if self._ring_max <= 0:
            return
        try:
            os.makedirs(self._ring_dir, exist_ok=True)
            base = os.path.join(self._ring_dir, "%03d" % self._ring_i)
            with open(base + ".jpg", "wb") as fh:
                fh.write(frame_bytes)
            with open(base + ".json", "w", encoding="utf-8") as fh:
                json.dump(
                    {
                        "camera_id": camera_id,
                        "quando": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        "classes_epi": _classes(payload),
                        "confianca_epi": _confianca_epi(payload),
                        "score_pessoa": round(v.confianca, 3),
                        "evidence_r2_key": payload.get("evidence_r2_key"),
                    },
                    fh,
                    ensure_ascii=False,
                )
            self._ring_i = (self._ring_i + 1) % self._ring_max
        except OSError as exc:
            logger.warning("guarda_pessoa_anel_falhou dir=%s err=%s", self._ring_dir, exc)


# ── contenção: esta caixa cai sobre alguém? ─────────────────────────────────
#
# Todo mundo aqui trabalha em FRAÇÃO do quadro (x0, y0, x1, y1 em 0..1). É o que
# torna comparável a caixa medida no streammux (1280×720) com a pessoa medida na
# evidência (1920×1080): normalizar cada uma pelo SEU quadro e comparar frações.
# Converter para pixels de um dos dois lados foi exatamente o bug do front.

_Ret = tuple[float, float, float, float]


def _tamanho_do_quadro(frame_bytes: bytes) -> tuple[float, float] | None:
    """(largura, altura) do JPEG pelo cabeçalho.

    `Image.open` é preguiçoso: responde `.size` sem decodificar pixel nenhum. O
    import é local para `EDGE_GUARDA_PESSOA=off` não pagar nem por ele.
    """
    try:
        import io  # noqa: PLC0415

        from PIL import Image  # noqa: PLC0415

        w, h = Image.open(io.BytesIO(frame_bytes)).size
    except Exception:  # noqa: BLE001 — sem tamanho, a contenção fica `None`
        return None
    return (float(w), float(h)) if w > 0 and h > 0 else None


def _pessoas_em_fracao(resultado: Any, quadro: tuple[float, float]) -> list[_Ret]:
    """Caixas de pessoa (pixels do quadro da evidência) -> frações."""
    largura, altura = quadro
    return [
        (b.x / largura, b.y / altura, (b.x + b.w) / largura, (b.y + b.h) / altura)
        for b in (getattr(resultado, "boxes", ()) or ())
    ]


def _caixa_em_fracao(det: dict, quadro: tuple[float, float] | None) -> _Ret | None:
    """Caixa de violação -> fração do quadro, ou `None` se inprojetável.

    Espelha `referenciaDaCaixa` do front: caixa do streammux SEM `frame_wh` não
    é projetável, e chutar a resolução é o erro que se está evitando.
    """
    bbox = det.get("bbox")
    if not bbox or len(bbox) != 4:
        return None
    x, y, w, h = (float(n) for n in bbox)
    if w <= 0 or h <= 0:
        return None
    unidade = det.get("bbox_unidade")
    if unidade == _UNIDADE_STREAMMUX:
        wh = det.get("frame_wh") or []
        if len(wh) != 2 or not wh[0] or not wh[1]:
            return None
        ref_w, ref_h = float(wh[0]), float(wh[1])
    elif unidade == _UNIDADE_FRAME:
        # Medida no quadro original: a evidência É esse quadro.
        if quadro is None:
            return None
        ref_w, ref_h = quadro
    else:
        return None
    if ref_w <= 0 or ref_h <= 0:
        return None
    return (x / ref_w, y / ref_h, (x + w) / ref_w, (y + h) / ref_h)


def _intersecao(a: _Ret, b: _Ret) -> _Ret | None:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    return (x0, y0, x1, y1) if x1 > x0 and y1 > y0 else None


def _area_da_uniao(rets: list[_Ret]) -> float:
    """Área da união por compressão de coordenadas.

    União e não soma: duas pessoas sobrepostas somariam a interseção duas vezes
    e a contenção passaria de 1. Com meia dúzia de pessoas a grade é minúscula.
    """
    if not rets:
        return 0.0
    xs = sorted({v for r in rets for v in (r[0], r[2])})
    ys = sorted({v for r in rets for v in (r[1], r[3])})
    total = 0.0
    for x0, x1 in zip(xs, xs[1:]):
        for y0, y1 in zip(ys, ys[1:]):
            if any(r[0] <= x0 and r[2] >= x1 and r[1] <= y0 and r[3] >= y1 for r in rets):
                total += (x1 - x0) * (y1 - y0)
    return total


def _contencao_da_caixa(caixa: _Ret, pessoas: list[_Ret]) -> float:
    area = (caixa[2] - caixa[0]) * (caixa[3] - caixa[1])
    if area <= 0:
        return 0.0
    pedacos = [p for p in (_intersecao(caixa, q) for q in pessoas) if p]
    return min(1.0, _area_da_uniao(pedacos) / area)


def _anotar_contencao(
    payload: dict, pessoas: list[_Ret], quadro: tuple[float, float] | None
) -> tuple[float | None, int, bool | None]:
    """Grava `contencao` e `anatomia_ok` em cada detecção projetável.

    Devolve (contenção máxima, quantas caixas, veredito anatômico do evento).

    O máximo, e não a média: basta UMA caixa cair sobre alguém para o evento ser
    plausível. Média puniria o evento por caixas extras do mesmo modelo.

    A anotação por detecção é o que faz o limiar ser calibrável depois: ela sobe
    crua em `alerts.violations` e um turno de tráfego vira uma consulta SQL, com
    o veredito humano na linha ao lado.
    """
    deteccoes = payload.get("detections") or []
    valores: list[float] = []
    vereditos: list[bool | None] = []
    for det in deteccoes:
        if not isinstance(det, dict):
            continue
        caixa = _caixa_em_fracao(det, quadro)
        if caixa is None:
            continue
        c = _contencao_da_caixa(caixa, pessoas)
        det["contencao"] = round(c, 3)
        valores.append(c)
        # A parte do corpo é pergunta SEPARADA da contenção, e a resposta vai
        # junto na detecção: `violations` sobe cru para `alerts`, então um turno
        # de tráfego vira consulta SQL com o veredito humano ao lado.
        ok, pos = anatomia.plausivel(det.get("class"), caixa, pessoas)
        det["anatomia_ok"] = ok
        if pos is not None:
            det["posicao_no_corpo"] = round(pos, 3)
        vereditos.append(ok)
    # `False` só se ALGUMA caixa está na parte errada; `True` exige ao menos uma
    # decidida e nenhuma errada; senão `None` (indeterminado).
    if any(v is False for v in vereditos):
        anat: bool | None = False
    elif any(v is True for v in vereditos):
        anat = True
    else:
        anat = None
    return (max(valores) if valores else None), len(valores), anat


def _faixa(contencao: float) -> str:
    """Zero EXATO tem faixa própria: é a assinatura de caixa sem corpo."""
    if contencao <= 0.0:
        return "0"
    for i, corte in enumerate(_FAIXAS_CONTENCAO[1:], start=1):
        if contencao < corte:
            return "%d-%d" % (_FAIXAS_CONTENCAO[i - 1] * 100, corte * 100)
    return "75-100"


def _classes(payload: dict) -> list[str]:
    return [
        str(d.get("class") or d.get("class_name") or "?")
        for d in (payload.get("detections") or [])
    ]


def _confianca_epi(payload: dict) -> float:
    return max(
        (float(d.get("confidence") or 0.0) for d in (payload.get("detections") or [])),
        default=0.0,
    )


def build_guarda_pessoa_from_env(env: dict[str, str] | None = None) -> GuardaPessoa | None:
    """`EDGE_GUARDA_PESSOA` = off | sombra (padrão) | barrar.

    Reusa `COLLECTOR_PERSON_MODEL_PATH` — o mesmo `yolox_nano.onnx` que o
    coletor já carrega no box, já configurado no `.env` de produção. Uma
    segunda variável para o mesmo arquivo só produziria duas verdades.

    Sem o arquivo do modelo (ou sem onnxruntime) o `PersonDetector` fica com
    `is_ready=False` e todo veredito vira `indeterminado` -> publica. Nesse
    caso devolvemos None de uma vez: um guarda que sempre publica é só custo.
    """
    source = env if env is not None else os.environ
    modo = source.get("EDGE_GUARDA_PESSOA", MODO_SOMBRA).strip().lower()
    if modo not in _MODOS:
        logger.warning("EDGE_GUARDA_PESSOA inválido (%r) — usando %s", modo, MODO_SOMBRA)
        modo = MODO_SOMBRA
    if modo == MODO_OFF:
        logger.info("guarda_pessoa_desligado por EDGE_GUARDA_PESSOA=off")
        return None

    from .collector.person_detector import PersonDetector  # noqa: PLC0415

    detector = PersonDetector(
        model_path=source.get(
            "COLLECTOR_PERSON_MODEL_PATH", "/home/pandora/recognition/models/yolox_nano.onnx"
        ),
        confidence=float(source.get("EDGE_GUARDA_CONFIANCA", str(_DEFAULT_CONFIANCA))),
        tile_grid=_parse_tiles(source.get("EDGE_GUARDA_TILES", "")),
    )
    if not detector.is_ready:
        logger.warning(
            "guarda_pessoa_sem_detector — todo evento seguiria como indeterminado; "
            "guarda desligado (o alerta continua subindo normalmente)"
        )
        return None
    piso = _parse_piso(source.get("EDGE_GUARDA_CONTENCAO", ""))
    anat = source.get("EDGE_GUARDA_ANATOMIA", _ANATOMIA_SO_MEDE).strip().lower()
    if anat not in (_ANATOMIA_SO_MEDE, _ANATOMIA_BARRAR):
        logger.warning("EDGE_GUARDA_ANATOMIA inválido (%r) — só medindo", anat)
        anat = _ANATOMIA_SO_MEDE
    guarda = GuardaPessoa(
        detector,
        modo=modo,
        ring_dir=source.get("EDGE_GUARDA_RING_DIR", _DEFAULT_RING_DIR),
        ring_max=int(source.get("EDGE_GUARDA_RING_MAX", str(_DEFAULT_RING_MAX))),
        piso_contencao=piso,
        anatomia=anat,
    )
    logger.info(
        "guarda_pessoa_pronto modo=%s piso_contencao=%.2f anatomia=%s%s",
        modo,
        piso,
        anat,
        " — NADA é barrado, só medido"
        if modo == MODO_SOMBRA
        else (" — contenção só MEDE (piso 0)" if piso <= 0 else ""),
    )
    return guarda


def _parse_piso(raw: str) -> float:
    """Fração em 0..1. Fora disso, 0.0 — e 0.0 nunca barra por contenção.

    Recusar valor inválido caindo em "só mede" é deliberado: a falha de digitação
    de quem liga o gate não pode virar violação silenciada.
    """
    if not raw.strip():
        return _DEFAULT_PISO_CONTENCAO
    try:
        v = float(raw)
    except ValueError:
        v = -1.0
    if not 0.0 <= v <= 1.0:
        logger.warning("EDGE_GUARDA_CONTENCAO inválido (%r) — contenção só mede", raw)
        return _DEFAULT_PISO_CONTENCAO
    return v


def _parse_tiles(raw: str) -> tuple[int, int]:
    """"4x4" -> (4, 4). Vazio/inválido cai no ponto de operação medido."""
    if not raw.strip():
        return _DEFAULT_TILES
    try:
        nx, ny = (int(p) for p in raw.lower().split("x", 1))
        if nx < 1 or ny < 1:
            raise ValueError
    except ValueError:
        logger.warning("EDGE_GUARDA_TILES inválido (%r) — usando %dx%d", raw, *_DEFAULT_TILES)
        return _DEFAULT_TILES
    return nx, ny
