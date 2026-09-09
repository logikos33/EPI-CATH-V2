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
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

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


@dataclass(frozen=True)
class Veredito:
    tem_pessoa: bool
    indeterminado: bool
    confianca: float
    ms: int

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
    ) -> None:
        self._detector = detector
        self._modo = modo if modo in _MODOS else MODO_SOMBRA
        self._ring_dir = ring_dir
        self._ring_max = max(0, ring_max)
        self._alta = alta_confianca
        self._ring_i = 0
        self._avaliados = 0
        self._indeterminados = 0
        self._barrados = 0
        self._barrados_alta_confianca = 0
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
            v = self._avaliar(frame_bytes)
        except Exception:  # noqa: BLE001 — guarda nunca bloqueia alerta
            logger.warning("guarda_pessoa_falhou camera=%s", camera_id, exc_info=True)
            return True

        payload["guarda_pessoa"] = {
            "veredito": v.rotulo,
            "confianca": round(v.confianca, 3),
            "modo": self._modo,
            "ms": v.ms,
        }
        self._contar(camera_id, v)

        if v.indeterminado or v.tem_pessoa or self._modo != MODO_BARRAR:
            return True

        self._barrados += 1
        conf_epi = _confianca_epi(payload)
        if conf_epi >= self._alta:
            self._barrados_alta_confianca += 1
            logger.warning(
                "guarda_pessoa_barrado_alta_confianca camera=%s conf_epi=%.2f "
                "classes=%s — o modelo de EPI afirma e o detector de pessoa não "
                "vê ninguém; frame no anel %s para auditoria",
                camera_id, conf_epi, _classes(payload), self._ring_dir,
            )
        for classe in _classes(payload):
            self._classes_barradas[classe] = self._classes_barradas.get(classe, 0) + 1
        self._gravar_no_anel(camera_id, payload, v, frame_bytes)
        return False

    def _avaliar(self, frame_bytes: bytes) -> Veredito:
        t0 = time.monotonic()
        r = self._detector.detect(frame_bytes)
        ms = int((time.monotonic() - t0) * 1000)
        return Veredito(
            tem_pessoa=bool(getattr(r, "found", False)),
            indeterminado=bool(getattr(r, "undetermined", False)),
            confianca=float(getattr(r, "max_confidence", 0.0) or 0.0),
            ms=ms,
        )

    # ── contadores ───────────────────────────────────────────────────────────

    def _contar(self, camera_id: str, v: Veredito) -> None:
        self._avaliados += 1
        if v.indeterminado:
            self._indeterminados += 1
        else:
            par = self._por_camera.setdefault(camera_id, [0, 0])
            par[1 if v.tem_pessoa else 0] += 1
        if self._avaliados % _RESUMO_A_CADA == 0:
            logger.info("guarda_pessoa_resumo %s", json.dumps(self.resumo()))

    def resumo(self) -> dict:
        return {
            "modo": self._modo,
            "avaliados": self._avaliados,
            "indeterminados": self._indeterminados,
            "barrados": self._barrados,
            # Sobe quando o guarda ERRA, não quando acerta — ver o topo.
            "barrados_alta_confianca": self._barrados_alta_confianca,
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
    guarda = GuardaPessoa(
        detector,
        modo=modo,
        ring_dir=source.get("EDGE_GUARDA_RING_DIR", _DEFAULT_RING_DIR),
        ring_max=int(source.get("EDGE_GUARDA_RING_MAX", str(_DEFAULT_RING_MAX))),
    )
    logger.info(
        "guarda_pessoa_pronto modo=%s%s",
        modo,
        " — NADA é barrado, só medido" if modo == MODO_SOMBRA else "",
    )
    return guarda


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
