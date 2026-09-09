"""A fila entrega o não-julgado primeiro — provado pela ROTA, com Postgres real.

Pedido do dono da RVB em 09/09/2026, com a tela em operação e centenas de
eventos do dia: "a fila tem que mudar com base no status: o que foi avaliado
tem que ir para o final".

POR QUE ESTE TESTE É DE INTEGRAÇÃO, E NÃO UNITÁRIO
--------------------------------------------------
A ordenação tem de acontecer ANTES do `LIMIT/OFFSET`, senão ela não é
ordenação de fila — é embaralhamento da página. Um mock de cursor devolve a
lista que o teste mandou devolver e passa com qualquer `ORDER BY`, inclusive
com nenhum. O que precisa ser provado é o oposto: que o SERVIDOR escolhe QUAIS
linhas entram na página 1. Isso só o Postgres decide, e só através da rota
(`GET /api/alerts`) — que é onde a tela lê.

O caso da PÁGINA 2 (`test_pagina_2_...`) é o que o cliente jamais consegue
consertar sozinho: reordenar a página carregada no navegador deixa a página 1
bonita e a 2 errada, porque quando ela chega o servidor já escolheu o recorte.
Este projeto já pagou por isso duas vezes (reordenação no cliente brigando com
o cursor; `OFFSET` perdendo metade das linhas).

ARMADILHA DO SQL, travada em `test_alerta_intocado_nao_afunda_com_os_julgados`
-----------------------------------------------------------------------------
`verified_by LIKE 'user:%'` com `verified_by` NULL devolve NULL, e
`ORDER BY <expr> ASC` põe NULL POR ÚLTIMO no Postgres. Sem o `COALESCE(...,
false)` do repositório, o alerta que NINGUÉM tocou — o mais urgente da fila —
afundaria junto com os já julgados. É exatamente o oposto do pedido, e passaria
despercebido num cenário em que todo mundo tem `verified_by` preenchido.

FALHA ANTES / PASSA DEPOIS (rodado neste worktree, saída no corpo do PR):
`ORDER BY a.timestamp DESC, a.id DESC` → os três casos de ordem VERMELHOS.

Pulado automaticamente sem INTEGRATION_DATABASE_URL/HARNESS_DATABASE_URL.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from flask_jwt_extended import create_access_token

BASE = datetime(2026, 9, 9, 8, 0, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _jwt(app, tenant_id: str) -> str:
    with app.app_context():
        return create_access_token(
            identity=str(uuid4()),
            additional_claims={"tenant_id": str(tenant_id), "role": "admin"},
        )


@pytest.fixture
def cenario(pg_raw, tenant_id):
    """Seis alertas, uma câmera, capturas de hora em hora.

    Cada linha tem uma combinação diferente de `verification_verdict` +
    `verified_by`, porque a regra NÃO é "tem verdict": a task Celery de triagem
    grava o MESMO 'approve'/'reject' com `verified_by='claude-haiku'`. A prova
    de gente é o prefixo `user:`.

    Classes DIFERENTES de propósito: com a mesma classe na mesma câmera a menos
    de 60s, `total_situacoes` colapsaria tudo numa rajada e o cenário deixaria
    de falar de ordem. Aqui as capturas são de hora em hora, então nem isso.
    """
    user = str(uuid4())
    cam = str(uuid4())
    linhas = [
        # (sufixo do id, horas depois da BASE, verdict, verified_by)
        ("aaaa", 5, None, None),                    # intocado, o mais recente
        ("bbbb", 4, "approve", "user:op-1"),        # JULGADO por gente
        ("cccc", 3, "reject", "claude-haiku"),      # veredito da IA — não conta
        ("dddd", 2, "reject", "user:op-2"),         # JULGADO por gente
        ("eeee", 1, None, "user:op-3"),             # abriu e não decidiu
        ("ffff", 0, "approve", None),               # verdict órfão, sem autor
    ]
    ids = {}
    with pg_raw.cursor() as cur:
        cur.execute(
            "INSERT INTO public.users (id, email, password_hash, name, role, tenant_id) "
            "VALUES (%s, %s, 'x', 'IntTest Fila', 'admin', %s)",
            (user, f"fila-{user[:8]}@test.dev", tenant_id),
        )
        cur.execute(
            "INSERT INTO public.cameras "
            "(id, tenant_id, user_id, name, host, module_code, active_module, is_active) "
            "VALUES (%s, %s, %s, 'Corredor Expedição', '10.0.0.1', 'epi', 'epi', true)",
            (cam, tenant_id, user),
        )
        for sufixo, horas, verdict, verified_by in linhas:
            alerta = str(uuid4())
            ids[sufixo] = alerta
            quando = BASE + timedelta(hours=horas)
            cur.execute(
                "INSERT INTO public.alerts "
                "  (id, camera_id, tenant_id, module_code, timestamp, violations, "
                "   confidence, evidence_key, created_at, "
                "   verification_verdict, verified_by) "
                "VALUES (%s, %s, %s, 'epi', %s, %s::jsonb, 0.9, %s, %s, %s, %s)",
                (
                    alerta, cam, tenant_id, quando,
                    json.dumps([{"class": f"Sem item {sufixo}", "confidence": 0.9}]),
                    f"evidence/{alerta}.jpg", quando, verdict, verified_by,
                ),
            )

    yield {"cam": cam, "ids": ids}

    with pg_raw.cursor() as cur:
        cur.execute("DELETE FROM public.alerts WHERE tenant_id = %s", (tenant_id,))
        cur.execute("DELETE FROM public.cameras WHERE tenant_id = %s", (tenant_id,))
        cur.execute("DELETE FROM public.users WHERE tenant_id = %s", (tenant_id,))


def _lista(client, token, extra: str = "") -> dict:
    de = _iso(BASE - timedelta(hours=1))
    ate = _iso(BASE + timedelta(hours=10))
    r = client.get(
        f"/api/alerts?start_date={de}&end_date={ate}&kind=&per_page=100{extra}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.get_json()
    return r.get_json()["data"]


def test_julgado_por_gente_vai_para_o_fim_da_fila(
    app, client, pg_pool, pg_raw, tenant_id, cenario
):
    """O critério inteiro numa linha, pela rota que a tela chama."""
    token = _jwt(app, tenant_id)
    ids = cenario["ids"]

    ordem = [a["id"] for a in _lista(client, token)["alerts"]]

    assert ordem == [
        ids["aaaa"],  # não julgado, 13h
        ids["cccc"],  # veredito da IA não é veredito de gente, 11h
        ids["eeee"],  # verdict NULL com autor, 9h
        ids["ffff"],  # verdict sem autor, 8h
        ids["bbbb"],  # JULGADO, 12h — desceu
        ids["dddd"],  # JULGADO, 10h — desceu
    ], "não-julgado antes de julgado, e dentro de cada grupo o mais recente primeiro"


def test_cronologia_sobrevive_dentro_de_cada_grupo(
    app, client, pg_pool, pg_raw, tenant_id, cenario
):
    """A ordem nova NÃO é 'qualquer ordem' — a cronologia continua mandando.

    "Mais recente primeiro" era o comportamento antigo e não pode se perder: é
    a leitura que o operador tem da tela desde sempre.
    """
    token = _jwt(app, tenant_id)
    alertas = _lista(client, token)["alerts"]
    julgado = {cenario["ids"]["bbbb"], cenario["ids"]["dddd"]}

    pendentes = [a["timestamp"] for a in alertas if a["id"] not in julgado]
    julgados = [a["timestamp"] for a in alertas if a["id"] in julgado]

    assert pendentes == sorted(pendentes, reverse=True)
    assert julgados == sorted(julgados, reverse=True)


def test_alerta_intocado_nao_afunda_com_os_julgados(
    app, client, pg_pool, pg_raw, tenant_id, cenario
):
    """`verified_by` NULL é o caso MAIS COMUM e o que o SQL quase inverteu.

    Sem `COALESCE(..., false)`, o predicado vira NULL e `ASC` manda NULL para o
    fim: o alerta que ninguém tocou sairia depois dos já resolvidos.
    """
    token = _jwt(app, tenant_id)
    alertas = _lista(client, token)["alerts"]
    posicao = {a["id"]: i for i, a in enumerate(alertas)}
    ids = cenario["ids"]

    for intocado in ("aaaa", "ffff"):
        for resolvido in ("bbbb", "dddd"):
            assert posicao[ids[intocado]] < posicao[ids[resolvido]], (
                f"{intocado} (verified_by NULL) afundou abaixo de {resolvido} — "
                "o COALESCE do predicado de julgamento sumiu"
            )


def test_pagina_2_tambem_respeita_a_ordem_o_que_o_cliente_nao_consegue(
    app, client, pg_pool, pg_raw, tenant_id, cenario
):
    """A prova de que a ordenação é do SERVIDOR, não da tela.

    Com `per_page=2`, quem escolhe quais duas linhas entram em cada página é o
    `ORDER BY` — o `OFFSET` só conta a partir dele. Reordenar no navegador
    deixaria a página 1 certa e a 2 errada; aqui as TRÊS páginas saem na ordem
    da fila, e a 3ª é só julgado.
    """
    token = _jwt(app, tenant_id)
    ids = cenario["ids"]

    de = _iso(BASE - timedelta(hours=1))
    ate = _iso(BASE + timedelta(hours=10))
    paginas = []
    for p in (1, 2, 3):
        r = client.get(
            f"/api/alerts?start_date={de}&end_date={ate}&kind=&per_page=2&page={p}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200, r.get_json()
        paginas.append([a["id"] for a in r.get_json()["data"]["alerts"]])

    assert paginas[0] == [ids["aaaa"], ids["cccc"]]
    assert paginas[1] == [ids["eeee"], ids["ffff"]]
    assert paginas[2] == [ids["bbbb"], ids["dddd"]], (
        "a última página é a dos julgados — se a ordenação fosse do cliente, "
        "esta página traria linhas fora de ordem"
    )

    # Nenhuma linha se perdeu nem se repetiu entre páginas (o defeito de OFFSET
    # que já custou metade de uma página nesta família de telas).
    plano = [i for pagina in paginas for i in pagina]
    assert sorted(plano) == sorted(ids.values())


def test_ordem_nao_muda_o_conjunto_nem_as_contagens(
    app, client, pg_pool, pg_raw, tenant_id, cenario
):
    """Ordenar não é filtrar: nada some, e o total continua o mesmo."""
    token = _jwt(app, tenant_id)
    dados = _lista(client, token)
    assert dados["total"] == 6
    assert len(dados["alerts"]) == 6
