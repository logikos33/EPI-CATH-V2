"""Testes do resolvedor de classes do manifesto (`scripts/ops/manifesto_modelos_r2.py`).

O que se prova aqui é a única coisa que faz o manifesto valer alguma coisa: a
lista de classes é PROVENIÊNCIA, não enfeite. Ela sai de onde o fato foi
registrado — avaliação (`metrics…per_class`) na frente de treino
(`dataset_versions.class_distribution`) — e, quando não há fato nenhum, sai
`null` em vez de um chute.

O chute que este arquivo REPROVA tem nome: derivar classe do NOME do modelo.
No acervo real do DEV o nome mente — `6ca25ee9` se chama "5 classes" e de fato
tem 5, todas de PRESENÇA (incapaz de acusar violação); `93fa2610` se chama
"Logikos EPI · origem desconhecida". Um resolvedor que lesse o nome pareceria
funcionar nos dois e estaria inventando taxonomia.

Sem banco, sem R2 — funções puras.
"""
import importlib.util
from pathlib import Path

_SCRIPT = (
    Path(__file__).resolve().parents[4] / "scripts" / "ops" / "manifesto_modelos_r2.py"
)


def _carregar():
    spec = importlib.util.spec_from_file_location("manifesto_modelos_r2", _SCRIPT)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


mr = _carregar()

# Formato real do censo (metrics._censo_2026_09.eval_val.per_class no DEV).
CENSO = {
    "_censo_2026_09": {
        "eval_val": {
            "map50": 0.4304,
            "split": "val",
            "sample_n": 60,
            "per_class": {
                "Botas": {"ap": 0.86},
                "Sem Luvas": {"ap": 0.0},
            },
        }
    }
}


class TestPrecedencia:
    def test_avaliacao_vence_treino(self):
        """`per_class` (foi MEDIDO) na frente de `class_distribution` (foi
        treinado): medir é prova mais forte que treinar."""
        classes, origem = mr.resolver_classes(
            {"name": "irrelevante", "metrics": CENSO},
            {"Capacete": 10, "Colete": 20},
        )
        assert classes == ["Botas", "Sem Luvas"]
        assert origem == "metrics._censo_2026_09.eval_val.per_class"

    def test_cai_para_class_distribution_sem_per_class(self):
        classes, origem = mr.resolver_classes(
            {"name": "irrelevante", "metrics": {"map50": 0.7}},
            {"Luvas": 255, "Botas": 704},
        )
        assert classes == ["Botas", "Luvas"]
        assert origem == "dataset_versions.class_distribution"

    def test_per_class_mais_raso_vence(self):
        """`8e8fedf7` tem DOIS per_class (topo split=test, censo split=val).
        A busca é em largura: o mais raso vence, sempre o mesmo."""
        metrics = dict(CENSO)
        metrics["per_class"] = {"Óculos": {"ap": 0.4}}
        classes, origem = mr.resolver_classes({"name": "x", "metrics": metrics}, None)
        assert classes == ["Óculos"]
        assert origem == "metrics.per_class"

    def test_chave_reservada_nao_e_classe(self):
        """`__sem_suporte_treino__` registra classes EXCLUÍDAS do treino —
        contá-la inflaria n_classes com uma classe que o modelo não detecta."""
        classes, _ = mr.resolver_classes(
            {"name": "x", "metrics": {}},
            {"Botas": 531, "__sem_suporte_treino__": ["Sem botas"]},
        )
        assert classes == ["Botas"]


class TestNaoInventa:
    def test_sem_fonte_nenhuma_sai_null(self):
        classes, origem = mr.resolver_classes({"name": "x", "metrics": {}}, None)
        assert classes is None
        assert origem == "nao_registrado"

    def test_nome_do_modelo_nunca_vira_classe(self):
        """⛔ O caso que dá nome a este arquivo.

        O modelo chega com TUDO que tenta o resolvedor — um nome que anuncia a
        contagem ("5 classes"), um display_name que anuncia as classes por
        extenso — e ZERO fato registrado. A resposta certa é "não sei".

        Este teste FALHA no instante em que alguém acrescentar qualquer
        fallback por nome (contar "5 classes", quebrar o display_name em
        vírgulas, casar rótulo do catálogo com substring do nome): `classes`
        deixaria de ser None.
        """
        modelo = {
            "name": "rfdetr-5-classes-04508616",
            "display_name": "Logikos EPI 5 classes · Botas, Luvas, mascara, Óculos",
            "metrics": {"map50": 0.764, "stage": 2.0},
        }
        classes, origem = mr.resolver_classes(modelo, None)
        assert classes is None, (
            "resolvedor derivou classe do NOME — nome não é contrato: "
            "'6ca25ee9' se chama '5 classes' e todas são de presença"
        )
        assert origem == "nao_registrado"

    def test_manifesto_completo_de_modelo_anonimo(self):
        """O manifesto inteiro de um modelo sem fato registrado não pode
        inventar polaridade nem capacidade de alertar."""
        from datetime import datetime

        man = mr.montar_manifesto(
            {
                "id": "6ca25ee9-0000-0000-0000-000000000000",
                "name": "rfdetr-5-classes",
                "display_name": "Logikos EPI 5 classes · 02/09 17h22",
                "created_at": datetime(2026, 9, 2, 17, 22, 4),
                "metrics": {"map50": 0.764},
                "is_active": False,
            },
            None,
            {},
        )
        assert man["classes"] is None
        assert man["classes_origem"] == "nao_registrado"
        assert man["n_classes"] is None
        assert man["classes_de_ausencia"] is None
        assert man["capaz_de_acusar_violacao"] is None
        # Fuso explícito: created_at é naive no banco, o manifesto carimba UTC.
        assert man["created_at"].endswith("+00:00")


class TestPolaridade:
    def test_ausencia_vem_do_catalogo_nao_do_prefixo_sem(self):
        """A polaridade tem UMA fonte (ADR-0065): o catálogo. Heurística de
        prefixo "Sem " marcaria `Sem mascara` como violação (o catálogo do DEV
        diz FALSE) e perderia `Uso incorreto de mascara` (é violação e não
        começa com "Sem")."""
        pol = {
            "sem luvas": True,
            "uso incorreto de mascara": True,
            "sem mascara": False,
            "botas": False,
            "sem óculos": None,
        }
        r = mr.classificar_polaridade(
            ["Botas", "Sem Luvas", "Sem mascara", "Sem Óculos",
             "Uso incorreto de mascara", "Fora do catalogo"],
            pol,
        )
        assert r["classes_de_ausencia"] == ["Sem Luvas", "Uso incorreto de mascara"]
        assert r["classes_de_presenca"] == ["Botas", "Sem mascara"]
        # NULL no catálogo e ausente do catálogo são a MESMA coisa aqui:
        # "ninguém decidiu" — nunca "não é violação".
        assert r["classes_sem_polaridade"] == ["Sem Óculos", "Fora do catalogo"]
        assert r["capaz_de_acusar_violacao"] is True

    def test_so_presenca_nao_acusa_violacao(self):
        """O caso `6ca25ee9`: melhor mAP do acervo, zero capacidade de alertar."""
        r = mr.classificar_polaridade(
            ["Botas", "Luvas", "mascara", "Óculos", "Protetor auditivo"],
            {"botas": False, "luvas": False, "mascara": False,
             "óculos": False, "protetor auditivo": False},
        )
        assert r["classes_de_ausencia"] == []
        assert r["capaz_de_acusar_violacao"] is False


class TestMap50EChave:
    def test_map50_carrega_sample_n(self):
        """0,430 sem `sample_n: 60` engana — parece avaliação completa."""
        valor, origem = mr.resolver_map50({"metrics": CENSO, "map50": 0.4304})
        assert valor == 0.4304
        assert origem["split"] == "val"
        assert origem["sample_n"] == 60

    def test_map50_de_treino_nao_assume_split(self):
        valor, origem = mr.resolver_map50({"metrics": {"map50": 0.431}, "map50": 0.431})
        assert valor == 0.431
        assert origem["split"] is None

    def test_chave_fica_ao_lado_do_artefato(self):
        assert mr.chave_do_manifesto(
            "models/63c219d8/runpod/a04d6633/model.onnx"
        ) == "models/63c219d8/runpod/a04d6633/manifest.json"

    def test_carimbo_de_geracao_nao_conta_como_mudanca(self):
        """Idempotência: só o CONTEÚDO decide regravar."""
        a = {"model_id": "x", "gerado_em": "2026-09-08T00:00:00+00:00"}
        b = {"model_id": "x", "gerado_em": "2027-01-01T00:00:00+00:00"}
        assert mr._comparavel(a) == mr._comparavel(b)


class _R2Falso:
    """Bucket de mentira que começa com o artefato real dentro — se o script
    encostar nele, o teste vê."""

    def __init__(self, inicial):
        self.objetos = dict(inicial)

    def exists(self, chave):
        return chave in self.objetos

    def download_bytes(self, chave):
        return self.objetos[chave]

    def upload_bytes(self, chave, dados, content_type):
        self.objetos[chave] = dados


class _CursorFalso:
    LINHAS_MODELO = [{
        "id": "m1", "name": "RF-DETR - Job J",
        "display_name": "Logikos EPI 10 classes",
        "created_at": __import__("datetime").datetime(2026, 9, 4, 6, 29, 20),
        "is_active": True, "origin": "runpod", "framework": "rfdetr",
        "module_code": "epi", "map50": 0.431, "dataset_version_id": "d1",
        "r2_onnx_key": "models/T/runpod/J/model.onnx",
        "model_path": "models/T/runpod/J/model.onnx",
        "tenant_id": "T", "metrics": {"map50": 0.431},
    }]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        if "trained_models" in sql:
            self._linhas = [dict(r) for r in self.LINHAS_MODELO]
        elif "dataset_versions" in sql:
            self._linhas = [{"id": "d1",
                             "class_distribution": {"Botas": 5, "Sem Luvas": 3}}]
        else:
            self._linhas = [{"n": "sem luvas", "is_violation": True},
                            {"n": "botas", "is_violation": False}]

    def fetchall(self):
        return self._linhas


class _ConexaoFalsa:
    def cursor(self, **kwargs):
        return _CursorFalso()

    def close(self):
        pass


class TestEscritaNoBucket:
    """O que o `--aplicar` promete: ADITIVO e IDEMPOTENTE. Sem R2 real."""

    def _rodar(self, monkeypatch, bucket):
        import sys
        import types

        monkeypatch.setattr(
            mr, "psycopg2", types.SimpleNamespace(connect=lambda dsn: _ConexaoFalsa())
        )
        monkeypatch.setattr(mr, "abrir_r2", lambda tenant: bucket)
        monkeypatch.setenv("DATABASE_URL", "postgres://falso")
        monkeypatch.setattr(sys, "argv", ["manifesto", "--aplicar"])
        return mr.main()

    def test_nunca_encosta_no_artefato_e_nao_regrava_a_toa(self, monkeypatch):
        bucket = _R2Falso({"models/T/runpod/J/model.onnx": b"ONNX-ORIGINAL"})

        assert self._rodar(monkeypatch, bucket) == 0
        assert set(bucket.objetos) == {
            "models/T/runpod/J/model.onnx",
            "models/T/runpod/J/manifest.json",
            "models/T/MANIFESTO.json",
        }
        # Aditivo: o artefato tem de sair da rodada byte a byte como entrou.
        assert bucket.objetos["models/T/runpod/J/model.onnx"] == b"ONNX-ORIGINAL"
        primeiro = bucket.objetos["models/T/runpod/J/manifest.json"]

        # Idempotente: a 2ª rodada não pode produzir escrita nova. Se o
        # `gerado_em` entrasse na comparação, este assert falharia.
        assert self._rodar(monkeypatch, bucket) == 0
        assert bucket.objetos["models/T/runpod/J/manifest.json"] == primeiro
        assert bucket.objetos["models/T/runpod/J/model.onnx"] == b"ONNX-ORIGINAL"


class TestFalhaAlto:
    def test_storage_que_nao_e_r2_aborta(self, monkeypatch):
        """Sem credencial de R2, `get_storage` pode devolver LocalStorage
        (disco EFÊMERO) quando ALLOW_EPHEMERAL_STORAGE=1. Gravar manifesto ali
        seria "sucesso" com zero upload — exatamente o que não pode acontecer."""
        import pytest

        from app.infrastructure.storage import local_storage

        monkeypatch.setattr(
            local_storage, "get_storage",
            lambda tenant_id=None: local_storage.LocalStorage("/tmp/nao-e-r2"),
        )
        with pytest.raises(SystemExit) as exc:
            mr.abrir_r2("T")
        assert "R2Storage" in str(exc.value)
