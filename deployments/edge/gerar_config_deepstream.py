#!/usr/bin/env python3
"""Gera o config do DeepStream apontando para as câmeras REAIS da RVB.

⛔ RODA NO BOX, à mão, com o runbook `destravar_edge_rvb.md` ao lado. Não
   habilita serviço, não reinicia nada, não fala com o banco.

POR QUE ESTE SCRIPT EXISTE
--------------------------
Os configs que estão no box (`~/jetson-experiments/mm/app_mm_*.txt`) apontam
para `rtsp://127.0.0.1:8554/camN` — o gerador sintético do teste de carga. O
box pode rodar o melhor modelo do mundo sobre vídeo de mentira e ninguém
percebe: o teste de carga foi feito para medir GPU, não para ver a fábrica.

AS DUAS FONTES DA VERDADE JÁ ESTÃO NO BOX — este script só as junta
---------------------------------------------------------------------
  · `config_cache.json` (ADR-0058): camera_id (UUID) -> canal do gravador.
    Escrito pelo ConfigPoller a cada poll bem-sucedido de
    GET /api/v1/edge/config/poll. É a lista de câmeras da NUVEM, não um
    palpite local. NUNCA carrega segredo (por desenho da ADR).
  · `edge-sync-agent.env`: RECORDER_HOST/PORT/USERNAME/PASSWORD — a mesma
    credencial que o `edge-live-view` já usa com sucesso hoje.

⚠️ SENHA — as três regras
--------------------------
1. A senha NUNCA entra por argumento de linha de comando (apareceria em `ps`
   e no histórico do shell). Só é lida do arquivo .env, que já é 600.
2. A senha NUNCA é impressa: o resumo na tela mostra a URL redigida.
3. O config gerado CONTÉM a senha (o nvurisrcbin do DeepStream só aceita a
   URI pronta, não há substituição por env). Por isso o arquivo nasce 0600,
   fora do repo, em ~/.config/recognition/deepstream/. NÃO COMMITAR.

⚠️ LOCKOUT — leia antes de rodar
---------------------------------
As Intelbras da RVB TRAVAM por proteção anti-brute-force se apanharem
credencial errada em sequência (CLAUDE.md §Cloud→Edge). Por isso este script:
  · não aceita credencial nova — só reusa a que já está no .env e já
    funciona no live view;
  · aborta se RECORDER_PASSWORD estiver vazia (melhor não gerar nada do que
    gerar 29 fontes que vão bater com senha em branco);
  · tem `--cameras` para começar com UMA câmera, como manda o runbook.

O MODELO É PARÂMETRO
--------------------
`--infer-config` aponta para o config do nvinfer (engine + labelfile +
parser). Quem vencer o duelo entra por aqui, sem tocar neste script.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path
from urllib.parse import quote

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

CACHE_PADRAO = "~/.local/share/recognition/edge-sync/config_cache.json"
ENV_PADRAO = "~/.config/recognition/edge-sync-agent.env"
SAIDA_PADRAO = "~/.config/recognition/deepstream"

_LINHA_ENV = re.compile(r"^([A-Z_][A-Z0-9_]*)=(.*)$")


def ler_env(caminho: Path) -> dict[str, str]:
    """KEY=VALUE do EnvironmentFile do systemd. Comentário e linha solta ignorados.

    Não usa `dotenv`: o formato do systemd é este subconjunto e o box não tem
    a lib. Aspas externas são removidas porque o systemd também as remove.
    """
    valores: dict[str, str] = {}
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        m = _LINHA_ENV.match(linha.strip())
        if not m:
            continue
        valor = m.group(2).strip()
        if len(valor) >= 2 and valor[0] == valor[-1] and valor[0] in "\"'":
            valor = valor[1:-1]
        valores[m.group(1)] = valor
    return valores


def url_rtsp(host: str, porta: str, usuario: str, senha: str, canal: int, subtype: int) -> str:
    """Dialeto Dahua/Intelbras — o MESMO de rtsp_timestamp_recorder_client.

    `quote(safe="")` porque senha de gravador costuma ter `@`/`/`, que sem
    escapar quebram o parsing da URI e o DeepStream tenta conectar num host
    inventado (e isso conta como tentativa falha no contador de lockout).
    """
    cred = f"{quote(usuario, safe='')}:{quote(senha, safe='')}@" if usuario else ""
    return (
        f"rtsp://{cred}{host}:{porta}/cam/realmonitor"
        f"?channel={canal}&subtype={subtype}"
    )


def redigir(url: str) -> str:
    """URL sem a senha, para log/tela. Espelha app/redact.py do agente."""
    return re.sub(r"://([^:/@]+):[^@]*@", r"://\1:***@", url)


def cadencia_da_fonte(fps_target: int, fps_fonte: int) -> int:
    """`drop-frame-interval` para a câmera entregar ~`fps_target` à inferência.

    O decoder emite 1 quadro a cada N. Para 30 fps de fonte e alvo de 5,
    N = 6. **O decode continua em taxa cheia** — `drop-frame-interval` descarta
    na SAÍDA do decoder, então isto poupa GPU de inferência, não NVDEC
    (`docs/edge/REGRAS_PLATAFORMA_JETSON.md`: "decode segue 30 fps, honesto").

    Limite 1..30: o elemento recusa fora dessa faixa, então alvo abaixo de
    1 fps não é expressável — e alvo acima da taxa da fonte vira 1 (sem
    descarte), que é o correto, não um erro.
    """
    if fps_target <= 0:
        raise ValueError(f"fps_target inválido: {fps_target}")
    return max(1, min(30, round(fps_fonte / fps_target)))


def montar_config(
    fontes: list[tuple[int, str, str, int]],
    infer_config: str,
    kitti_dir: str,
    intervalo: int,
    mux_wh: tuple[int, int],
) -> str:
    """Config do `deepstream-app`. `fontes` = [(indice, camera_id, uri, drop)].

    Decisões, e por quê:
      · sink type=1 (fakesink): a saída ÚTIL é o dump KITTI, não vídeo. Um
        sink de tela exigiria DISPLAY e desperdiçaria GPU.
      · gie-kitti-output-dir: é o único jeito de tirar metadado do
        `deepstream-app` sem pyds (não instalado no box) e sem msgbroker (o
        adaptador Redis fala Streams/XADD, o relay do agente fala pub/sub).
      · tracker desligado: o dump do GIE não carrega track_id, então o
        tracker custaria GPU sem mudar nada na saída. Ligar só quando algo
        consumir track (ex.: contagem).
      · select-rtp-protocol=4 (TCP): a LAN do gravador perde UDP e o
        DeepStream não reconecta bem de stream truncado.
    """
    blocos = [
        "[application]",
        "enable-perf-measurement=1",
        "perf-measurement-interval-sec=5",
        f"gie-kitti-output-dir={kitti_dir}",
        "",
        "[tiled-display]",
        "enable=0",
        "",
    ]
    for indice, camera_id, uri, drop in fontes:
        blocos += [
            f"# {camera_id}",
            f"[source{indice}]",
            "enable=1",
            "type=4",
            f"uri={uri}",
            "gpu-id=0",
            "select-rtp-protocol=4",
            "latency=100",
            "rtsp-reconnect-interval-sec=10",
            # Cadência DESTA câmera, vinda do fps_target que o dono escolhe na
            # tela. É o que faz "configurar no front" responder no edge.
            f"drop-frame-interval={drop}",
            "",
        ]
    blocos += [
        "[sink0]",
        "enable=1",
        "type=1",
        "sync=0",
        "qos=0",
        "",
        "[osd]",
        "enable=0",
        "",
        "[streammux]",
        "gpu-id=0",
        f"batch-size={len(fontes)}",
        "batched-push-timeout=33000",
        f"width={mux_wh[0]}",
        f"height={mux_wh[1]}",
        "live-source=1",
        "",
        "[tracker]",
        "enable=0",
        "",
        "[primary-gie]",
        "enable=1",
        "gpu-id=0",
        f"interval={intervalo}",
        f"config-file={infer_config}",
        "",
    ]
    return "\n".join(blocos)


def autoteste() -> int:
    """Trava a conversão fps_target -> drop-frame-interval. Sem banco, sem box."""
    # 30 fps de fonte: o alvo do dono (3 a 5 fps) cai em drop 6..10.
    assert cadencia_da_fonte(5, 30) == 6
    assert cadencia_da_fonte(3, 30) == 10
    assert cadencia_da_fonte(1, 30) == 30
    # Alvo ACIMA da fonte não é erro: vira 1 (sem descarte), que é o correto.
    assert cadencia_da_fonte(60, 30) == 1
    # A faixa do elemento é 1..30; alvo minúsculo satura no teto em vez de
    # emitir valor que o DeepStream recusaria no boot.
    assert cadencia_da_fonte(1, 3000) == 30
    # Fonte a 15 fps (substream costuma vir assim) muda a conta, e é por isso
    # que --fps-fonte existe em vez de 30 fixo.
    assert cadencia_da_fonte(5, 15) == 3
    for ruim in (0, -1):
        try:
            cadencia_da_fonte(ruim, 30)
        except ValueError:
            pass
        else:  # pragma: no cover
            raise AssertionError(f"fps_target {ruim} deveria falhar alto")

    # O config gerado carrega a cadência POR FONTE — é o que faz a tela
    # responder no box. Duas câmeras com alvos diferentes = dois drops.
    conf = montar_config(
        [(0, "cam-a", "rtsp://x/1", 6), (1, "cam-b", "rtsp://x/2", 30)],
        "infer.txt", "/dev/shm/k", 4, (1280, 720),
    )
    assert "drop-frame-interval=6" in conf and "drop-frame-interval=30" in conf
    assert conf.count("drop-frame-interval=") == 2
    print("autoteste OK")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--autoteste", action="store_true", help="roda as asserções e sai")
    p.add_argument("--nome", default="epi", help="sufixo dos arquivos gerados (default: epi)")
    p.add_argument("--cache", default=CACHE_PADRAO, help="config_cache.json (camera->canal)")
    p.add_argument("--env", default=ENV_PADRAO, help=".env do edge-sync-agent (credencial do gravador)")
    p.add_argument("--saida", default=SAIDA_PADRAO, help="diretório de saída (nasce 0700)")
    p.add_argument("--infer-config", default="", help="config do nvinfer (engine+labelfile+parser)")
    p.add_argument("--kitti-dir", default="/dev/shm/recognition-kitti",
                   help="dump do GIE. /dev/shm = RAM: disco cheio é intertravamento do device")
    p.add_argument("--cameras", default="",
                   help="csv de camera_id para incluir (vazio = todas do cache). COMECE COM UMA.")
    p.add_argument("--subtype", type=int, default=None,
                   help="0=principal 1=substream (default: RECORDER_STREAM_SUBTYPE do .env, ou 0)")
    p.add_argument("--intervalo", type=int, default=4,
                   help="interval do primary-gie: infere 1 a cada N+1 frames (default 4)")
    p.add_argument("--fps-fonte", type=int, default=30,
                   help="taxa que o gravador entrega, para converter fps_target em "
                        "drop-frame-interval (default 30)")
    p.add_argument("--fps-padrao", type=int, default=None,
                   help="fps a usar quando a câmera não tem fps_target no cache. "
                        "SEM isto o gerador RECUSA — assumir taxa cheia satura a GPU em silêncio")
    p.add_argument("--mux", default="1280x720", help="resolução do streammux (default 1280x720)")
    args = p.parse_args()

    if args.autoteste:
        return autoteste()

    if not args.infer_config:
        p.error("--infer-config é obrigatório (exceto com --autoteste)")

    cache_path = Path(os.path.expanduser(args.cache))
    env_path = Path(os.path.expanduser(args.env))
    for caminho in (cache_path, env_path):
        if not caminho.is_file():
            logger.error("arquivo obrigatório ausente: %s", caminho)
            return 2

    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    channel_map: dict[str, int] = {str(k): int(v) for k, v in (cache.get("channel_map") or {}).items()}
    if not channel_map:
        logger.error(
            "%s tem channel_map vazio — o ConfigPoller ainda não trouxe câmera "
            "nenhuma da nuvem. Sem isso não há como saber que canal é que câmera.",
            cache_path,
        )
        return 2

    env = ler_env(env_path)
    host = env.get("RECORDER_HOST", "")
    porta = env.get("RECORDER_PORT", "554")
    usuario = env.get("RECORDER_USERNAME", "")
    senha = env.get("RECORDER_PASSWORD", "")
    if not host or not senha:
        # Gerar com senha vazia produziria N tentativas falhas seguidas contra
        # o gravador — exatamente o gatilho do lockout anti-brute-force.
        logger.error(
            "RECORDER_HOST/RECORDER_PASSWORD ausentes em %s — abortando ANTES de "
            "gerar config que bateria no gravador com credencial vazia (lockout).",
            env_path,
        )
        return 2

    subtype = args.subtype if args.subtype is not None else int(env.get("RECORDER_STREAM_SUBTYPE", "0") or 0)
    escolhidas = [c.strip() for c in args.cameras.split(",") if c.strip()]
    if escolhidas:
        desconhecidas = [c for c in escolhidas if c not in channel_map]
        if desconhecidas:
            logger.error("camera_id fora do config_cache: %s", ", ".join(desconhecidas))
            return 2
        itens = [(c, channel_map[c]) for c in escolhidas]
    else:
        # Ordem por canal: torna o índice da fonte estável entre gerações, o
        # que importa porque é ele que o publicador usa para achar a câmera.
        itens = sorted(channel_map.items(), key=lambda kv: kv[1])

    # Eixo OPERAÇÃO: fps_target por câmera, escrito no cache pelo ConfigPoller.
    fps_map = {str(k): int(v) for k, v in (cache.get("fps_target_map") or {}).items()}
    sem_fps = [c for c, _ in itens if c not in fps_map]
    if sem_fps and args.fps_padrao is None:
        # ⛔ NÃO assumir taxa cheia em silêncio: 17 fontes a 30 fps saturam a
        # GPU do Orin e ninguém descobre olhando o config. Recusar é a falha
        # ALTA que o runbook pede — ou o poll ainda não trouxe o valor, ou a
        # câmera não tem fps_target no banco, e as duas se consertam na nuvem.
        logger.error(
            "sem fps_target no cache para %d câmera(s): %s. O ConfigPoller não "
            "trouxe o valor (cache antigo?) ou a câmera não tem fps_target no "
            "banco. Use --fps-padrao para forçar, ciente de que taxa cheia "
            "satura a GPU.",
            len(sem_fps), ", ".join(sem_fps[:5]),
        )
        return 2

    fontes = [
        (
            i,
            camera_id,
            url_rtsp(host, porta, usuario, senha, canal, subtype),
            cadencia_da_fonte(fps_map.get(camera_id, args.fps_padrao or 0) or args.fps_padrao,
                              args.fps_fonte),
        )
        for i, (camera_id, canal) in enumerate(itens)
    ]
    logger.info(
        "cadencia por fonte: %s",
        {c: f"{fps_map.get(c, args.fps_padrao)}fps->drop{d}" for _, c, _, d in fontes},
    )

    saida = Path(os.path.expanduser(args.saida))
    saida.mkdir(parents=True, exist_ok=True)
    os.chmod(saida, 0o700)

    largura, altura = (int(x) for x in args.mux.lower().split("x"))
    conf_path = saida / f"app_{args.nome}.txt"
    conf_path.write_text(
        montar_config(fontes, args.infer_config, args.kitti_dir, args.intervalo, (largura, altura)),
        encoding="utf-8",
    )
    os.chmod(conf_path, 0o600)  # contém a senha do gravador na URI

    # Mapa índice-da-fonte -> camera_id. Sem segredo: é o que o publicador lê
    # para saber em qual `det:{camera_id}` publicar o que o KITTI diz da
    # fonte N. Separado do config de propósito — este pode ser lido por
    # qualquer um, o outro não.
    fontes_path = saida / f"fontes_{args.nome}.json"
    fontes_path.write_text(
        json.dumps({str(i): camera_id for i, camera_id, _ in fontes}, indent=2),
        encoding="utf-8",
    )
    os.chmod(fontes_path, 0o644)

    logger.info("gerado %s (0600, %d fontes)", conf_path, len(fontes))
    logger.info("gerado %s", fontes_path)
    for i, camera_id, uri in fontes:
        logger.info("  source%-2d %s  %s", i, camera_id, redigir(uri))
    logger.warning(
        "NÃO COMMITAR %s (contém a senha do gravador). Confira o canal de UMA "
        "câmera antes de subir as %d — credencial errada em sequência trava a "
        "Intelbras por anti-brute-force.",
        conf_path, len(fontes),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
