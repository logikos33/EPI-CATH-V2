"""
Unit — `POST /api/alerts/acknowledge` (reconhecer em LOTE).

Por que este arquivo existe: o "marcar todas como lida" era um laço no
CLIENTE sobre `POST /alerts/<id>/acknowledge`. Com 235 pendentes isso é 235
requisições, e uma falha no meio deixava o operador sem saber o que ficou
marcado.

Atravessa a fronteira HTTP de verdade (rota registrada, JWT, parsing do corpo,
envelope de resposta); o repositório é dublê porque o que se prova aqui é o
CONTRATO da rota, não o SQL. O SQL de `acknowledge_many` (predicados de `kind`
e escopo de tenant) precisa de Postgres real — está registrado como pendência,
não coberto aqui.

Teste de mutação (conferido rodando):
  · tirar a guarda de `all` → `test_corpo_vazio_nao_reconhece_tudo` fica
    vermelho (a rota passa a chamar o repo sem filtro nenhum);
  · devolver `len(ids)` em vez do rowcount → `test_resposta_conta_o_que_mudou`
    fica vermelho.
"""
import uuid
from unittest.mock import MagicMock

import pytest
from flask_jwt_extended import create_access_token

import app.api.v1.alerts.routes as alerts_routes

TENANT = "11111111-1111-1111-1111-111111111111"
ROTA = "/api/alerts/acknowledge"


def _auth(app, role: str = "operator") -> dict[str, str]:
    with app.app_context():
        token = create_access_token(
            identity=str(uuid.uuid4()),
            additional_claims={
                "tenant_id": TENANT,
                "tenant_schema": "tenant_test",
                "role": role,
                "modules": ["epi"],
            },
        )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def repo(monkeypatch):
    mock = MagicMock()
    mock.acknowledge_many.return_value = 0
    monkeypatch.setattr(alerts_routes, "_get_repo", lambda: mock)
    return mock


def test_sem_token_e_401(client, repo):
    assert client.post(ROTA, json={"all": True}).status_code == 401
    repo.acknowledge_many.assert_not_called()


def test_corpo_vazio_nao_reconhece_tudo(app, client, repo):
    """`{}` de um cliente com bug NUNCA pode significar "marque tudo"."""
    r = client.post(ROTA, json={}, headers=_auth(app))
    assert r.status_code == 400
    repo.acknowledge_many.assert_not_called()


def test_all_precisa_ser_true_explicito(app, client, repo):
    for corpo in ({"all": False}, {"all": "true"}, {"all": 1}):
        r = client.post(ROTA, json=corpo, headers=_auth(app))
        assert r.status_code == 400, corpo
    repo.acknowledge_many.assert_not_called()


def test_all_true_reconhece_o_recorte(app, client, repo):
    repo.acknowledge_many.return_value = 235
    r = client.post(
        ROTA, json={"all": True, "kind": "violation"}, headers=_auth(app)
    )
    assert r.status_code == 200
    corpo = r.get_json()
    assert corpo["success"] is True
    assert corpo["data"]["acknowledged"] == 235
    # `requested` é None no modo "all": não há lista pedida para comparar.
    assert corpo["data"]["requested"] is None
    repo.acknowledge_many.assert_called_once_with(
        tenant_id=TENANT, ids=None, kind="violation", camera_id=None
    )


def test_ids_sao_repassados_normalizados(app, client, repo):
    repo.acknowledge_many.return_value = 2
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    r = client.post(ROTA, json={"ids": [a.upper(), b]}, headers=_auth(app))
    assert r.status_code == 200
    assert repo.acknowledge_many.call_args.kwargs["ids"] == [a, b]


def test_id_malformado_e_400_e_nao_chega_ao_sql(app, client, repo):
    """O id vai para um `::uuid[]`: lixo aqui viraria 500 lá embaixo."""
    r = client.post(ROTA, json={"ids": ["nao-e-uuid"]}, headers=_auth(app))
    assert r.status_code == 400
    repo.acknowledge_many.assert_not_called()


def test_lista_vazia_e_400(app, client, repo):
    r = client.post(ROTA, json={"ids": []}, headers=_auth(app))
    assert r.status_code == 400
    repo.acknowledge_many.assert_not_called()


def test_teto_de_500_ids(app, client, repo):
    demais = [str(uuid.uuid4()) for _ in range(501)]
    r = client.post(ROTA, json={"ids": demais}, headers=_auth(app))
    assert r.status_code == 400
    repo.acknowledge_many.assert_not_called()


def test_resposta_conta_o_que_mudou_nao_o_que_foi_pedido(app, client, repo):
    """3 ids pedidos, 1 mudou (os outros já estavam reconhecidos ou são de
    outro tenant). A resposta não pode inflar o número para 3."""
    repo.acknowledge_many.return_value = 1
    ids = [str(uuid.uuid4()) for _ in range(3)]
    r = client.post(ROTA, json={"ids": ids}, headers=_auth(app))
    dados = r.get_json()["data"]
    assert dados["acknowledged"] == 1
    assert dados["requested"] == 3


def test_kind_invalido_vira_sem_recorte_nunca_500(app, client, repo):
    r = client.post(ROTA, json={"all": True, "kind": "banana"}, headers=_auth(app))
    assert r.status_code == 200
    assert repo.acknowledge_many.call_args.kwargs["kind"] is None


def test_camera_id_malformado_e_400(app, client, repo):
    r = client.post(
        ROTA, json={"all": True, "camera_id": "nao-e-uuid"}, headers=_auth(app)
    )
    assert r.status_code == 400
    repo.acknowledge_many.assert_not_called()


def test_falha_do_repo_vira_500_e_nao_estoura(app, client, repo):
    repo.acknowledge_many.side_effect = RuntimeError("banco caiu")
    r = client.post(ROTA, json={"all": True}, headers=_auth(app))
    assert r.status_code == 500
    assert r.get_json()["success"] is False
