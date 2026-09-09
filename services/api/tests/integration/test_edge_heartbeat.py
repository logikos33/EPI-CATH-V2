"""
Tests for POST /api/v1/edge/heartbeat.

Covers (spec eval cases):
  1. Valid RS256 token + valid payload → 201, heartbeat persisted
  2. Token signed by wrong key → 401, nothing persisted
  3. Revoked device → 403, nothing persisted
  4. Expired token → 401, nothing persisted
  5. Missing Authorization header → 401
  6. Payload missing required field (status) → 422

Keypair is generated in-test; no enrollment dependency.
"""
import time
from unittest.mock import MagicMock, patch
from uuid import uuid4

import jwt
from contextlib import ExitStack

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _generate_keypair() -> tuple[str, str]:
    """Generate a fresh RS256 keypair. Returns (private_pem, public_pem)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode()
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return private_pem, public_pem


def _make_token(
    private_pem: str,
    tenant_id: object,
    site_id: object,
    device_id: str,
    exp_offset: int = 3600,
) -> str:
    """Sign a DeviceClaims JWT with the given private key."""
    now = int(time.time())
    return jwt.encode(
        {
            "tenant_id": str(tenant_id),
            "site_id": str(site_id),
            "device_id": device_id,
            "scopes": ["heartbeat:write"],
            "iat": now,
            "exp": now + exp_offset,
        },
        private_pem,
        algorithm="RS256",
    )


VALID_PAYLOAD = {
    "device_id": "edge-device-001",
    "status": "healthy",
    "cpu_pct": "12.5",
    "mem_pct": "40.0",
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def device_setup():
    """Returns (private_pem, public_pem, tenant_id, site_id, device_id)."""
    private_pem, public_pem = _generate_keypair()
    return private_pem, public_pem, uuid4(), uuid4(), "edge-device-001"


@pytest.fixture()
def device_record(device_setup):
    """Simulates a device_tokens row as returned by the repository."""
    _, public_pem, tenant_id, site_id, device_id = device_setup
    return {
        "id": str(uuid4()),
        "tenant_id": tenant_id,
        "site_id": site_id,
        "device_id": device_id,
        "public_key_pem": public_pem,
        "revoked": False,
    }


@pytest.fixture()
def mock_repo(device_record):
    """Mock EdgeHeartbeatRepository with a seeded active device."""
    repo = MagicMock()
    repo.get_device_by_device_id.return_value = device_record
    repo.insert_heartbeat.return_value = {
        "id": 42,
        "received_at": "2026-06-02T00:00:00+00:00",
    }
    repo.update_last_seen.return_value = None
    return repo


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

class TestEdgeHeartbeatIngest:

    def test_valid_token_and_payload_returns_201(
        self, client, device_setup, mock_repo
    ) -> None:
        private_pem, _, tenant_id, site_id, device_id = device_setup
        token = _make_token(private_pem, tenant_id, site_id, device_id)

        with patch("app.api.v1.edge.routes._get_repo", return_value=mock_repo):
            res = client.post(
                "/api/v1/edge/heartbeat",
                json=VALID_PAYLOAD,
                headers={"Authorization": f"Bearer {token}"},
            )

        assert res.status_code == 201
        data = res.get_json()
        assert data["success"] is True
        assert data["data"]["id"] == 42
        mock_repo.insert_heartbeat.assert_called_once()
        mock_repo.update_last_seen.assert_called_once()

    def test_token_signed_by_wrong_key_returns_401(
        self, client, device_setup, mock_repo
    ) -> None:
        wrong_private_pem, _ = _generate_keypair()
        _, _, tenant_id, site_id, device_id = device_setup
        token = _make_token(wrong_private_pem, tenant_id, site_id, device_id)

        with patch("app.api.v1.edge.routes._get_repo", return_value=mock_repo):
            res = client.post(
                "/api/v1/edge/heartbeat",
                json=VALID_PAYLOAD,
                headers={"Authorization": f"Bearer {token}"},
            )

        assert res.status_code == 401
        mock_repo.insert_heartbeat.assert_not_called()

    def test_revoked_device_returns_403(
        self, client, device_setup, mock_repo, device_record
    ) -> None:
        device_record["revoked"] = True
        private_pem, _, tenant_id, site_id, device_id = device_setup
        token = _make_token(private_pem, tenant_id, site_id, device_id)

        with patch("app.api.v1.edge.routes._get_repo", return_value=mock_repo):
            res = client.post(
                "/api/v1/edge/heartbeat",
                json=VALID_PAYLOAD,
                headers={"Authorization": f"Bearer {token}"},
            )

        assert res.status_code == 403
        mock_repo.insert_heartbeat.assert_not_called()

    def test_expired_token_returns_401(
        self, client, device_setup, mock_repo
    ) -> None:
        private_pem, _, tenant_id, site_id, device_id = device_setup
        token = _make_token(private_pem, tenant_id, site_id, device_id, exp_offset=-60)

        with patch("app.api.v1.edge.routes._get_repo", return_value=mock_repo):
            res = client.post(
                "/api/v1/edge/heartbeat",
                json=VALID_PAYLOAD,
                headers={"Authorization": f"Bearer {token}"},
            )

        assert res.status_code == 401
        mock_repo.insert_heartbeat.assert_not_called()

    def test_missing_authorization_returns_401(self, client) -> None:
        res = client.post("/api/v1/edge/heartbeat", json=VALID_PAYLOAD)
        assert res.status_code == 401

    def test_invalid_payload_missing_status_returns_422(
        self, client, device_setup, mock_repo
    ) -> None:
        private_pem, _, tenant_id, site_id, device_id = device_setup
        token = _make_token(private_pem, tenant_id, site_id, device_id)

        with patch("app.api.v1.edge.routes._get_repo", return_value=mock_repo):
            res = client.post(
                "/api/v1/edge/heartbeat",
                json={"device_id": "edge-device-001"},  # missing required 'status'
                headers={"Authorization": f"Bearer {token}"},
            )

        assert res.status_code == 422
        mock_repo.insert_heartbeat.assert_not_called()

    def test_forged_claims_tenant_mismatch_returns_403_and_nothing_stored(
        self, client, device_setup, mock_repo
    ) -> None:
        """C-01: device forja claims com tenant_b diferente do enrollment (tenant_a).
        Deve retornar 403 e nenhuma linha deve ser gravada sob tenant_b ou tenant_a."""
        private_pem, _, tenant_a, site_a, device_id = device_setup
        tenant_b, site_b = uuid4(), uuid4()
        # Token válido (assinado pela chave correta), mas claims apontam para tenant_b
        token = _make_token(private_pem, tenant_b, site_b, device_id)

        with patch("app.api.v1.edge.routes._get_repo", return_value=mock_repo):
            res = client.post(
                "/api/v1/edge/heartbeat",
                json=VALID_PAYLOAD,
                headers={"Authorization": f"Bearer {token}"},
            )

        assert res.status_code == 403
        # Nada gravado — tenant forjado (B) nunca persiste
        mock_repo.insert_heartbeat.assert_not_called()
        # Assert explícito: tenant_b não aparece em nenhuma chamada ao repo
        for call in mock_repo.insert_heartbeat.call_args_list:
            stored_tenant = call.args[0] if call.args else call.kwargs.get("tenant_id")
            assert str(stored_tenant) != str(tenant_b), (
                f"tenant forjado {tenant_b} NÃO deve ser gravado em edge_heartbeats"
            )


# ---------------------------------------------------------------------------
# ADR-0058 — divergência de config_version observável no heartbeat.
#
# fail-before/pass-after: antes desta ADR, `config_version_applied` não
# existia no payload e nada era comparado — o heartbeat sempre "passava"
# porque não havia checagem nenhuma.
# ---------------------------------------------------------------------------

class TestEdgeConfigDivergence:

    @pytest.fixture(autouse=True)
    def _sem_estado_de_supressao(self):
        """O rate-limit do aviso (#477) guarda estado no módulo. Sem zerar entre
        os casos, um teste suprimiria o aviso do seguinte e o verde seria falso."""
        import app.api.v1.edge.routes as edge_routes

        edge_routes._reset_divergence_state()
        yield
        edge_routes._reset_divergence_state()

    def _camera_row(self, channel: int = 1) -> dict:
        return {
            "id": "44444444-4444-4444-4444-444444444444",
            "name": "Cam Doca",
            "host": "10.0.0.20",
            "port": 554,
            "channel": channel,
            "subtype": 0,
            "rtsp_substream_url": None,
            "rtsp_url_override": None,
            "fps_target": 10,
            "quality_preset": "medium",
            "is_active": True,
            "module_code": "epi",
        }

    @staticmethod
    def _patch_repos(mock_repo, camera_repo, *, op_repo=None, mod_repo=None, dep_repo=None):
        """Patcha TODOS os repos que `_build_edge_config_payload` consulta.

        Desde que o config/poll passou a mandar rules/scenario/model, o
        config_version do heartbeat deriva do payload INTEIRO — patchar só o
        repo de câmeras deixaria os outros três caírem num DatabasePool
        inexistente e o check viraria "erro best-effort", que é verde falso.
        """
        op_repo = op_repo or MagicMock(**{"list_for_site_config.return_value": []})
        mod_repo = mod_repo or MagicMock(**{"get_by_tenant.return_value": []})
        dep_repo = dep_repo or MagicMock(**{"list_active_for_site.return_value": []})
        stack = ExitStack()
        stack.enter_context(patch("app.api.v1.edge.routes._get_repo", return_value=mock_repo))
        stack.enter_context(
            patch("app.api.v1.edge.routes._get_camera_repo", return_value=camera_repo)
        )
        stack.enter_context(
            patch("app.api.v1.edge.routes._get_operation_repo", return_value=op_repo)
        )
        stack.enter_context(
            patch("app.api.v1.edge.routes._get_module_repo", return_value=mod_repo)
        )
        stack.enter_context(
            patch("app.api.v1.edge.routes._get_deployment_repo", return_value=dep_repo)
        )
        return stack

    @classmethod
    def _versao_corrente(cls, mock_repo, cameras) -> str:
        """config_version que a nuvem calcularia para estas câmeras — via o
        MESMO builder da rota, não reconstruindo o payload no teste."""
        import app.api.v1.edge.routes as edge_routes

        camera_repo = MagicMock()
        camera_repo.list_for_site_config.return_value = cameras
        with cls._patch_repos(mock_repo, camera_repo):
            return edge_routes._compute_config_version(
                edge_routes._build_edge_config_payload("site", "tenant")
            )

    def test_no_config_version_applied_never_touches_camera_repo(
        self, client, device_setup, mock_repo
    ) -> None:
        """Agente antigo (sem o campo) — o check nem consulta câmeras."""
        private_pem, _, tenant_id, site_id, device_id = device_setup
        token = _make_token(private_pem, tenant_id, site_id, device_id)
        camera_repo = MagicMock()

        with self._patch_repos(mock_repo, camera_repo):
            res = client.post(
                "/api/v1/edge/heartbeat",
                json=VALID_PAYLOAD,  # sem config_version_applied
                headers={"Authorization": f"Bearer {token}"},
            )

        assert res.status_code == 201
        camera_repo.list_for_site_config.assert_not_called()

    def test_matching_config_version_logs_nothing(
        self, client, device_setup, mock_repo, caplog
    ) -> None:

        private_pem, _, tenant_id, site_id, device_id = device_setup
        token = _make_token(private_pem, tenant_id, site_id, device_id)
        cameras = [self._camera_row()]
        current = self._versao_corrente(mock_repo, cameras)

        camera_repo = MagicMock()
        camera_repo.list_for_site_config.return_value = cameras
        payload = {**VALID_PAYLOAD, "config_version_applied": current}

        with self._patch_repos(mock_repo, camera_repo), caplog.at_level("WARNING"):
            res = client.post(
                "/api/v1/edge/heartbeat",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )

        assert res.status_code == 201
        assert "edge_config_divergence" not in caplog.text

    def test_mismatched_config_version_logs_divergence_warning(
        self, client, device_setup, mock_repo, caplog
    ) -> None:
        """O caso motivador da ADR: o device aplicou uma config mais antiga
        (menos câmeras) que a corrente do site — deve virar um WARNING
        visível, mas o heartbeat continua sendo aceito (best-effort)."""
        private_pem, _, tenant_id, site_id, device_id = device_setup
        token = _make_token(private_pem, tenant_id, site_id, device_id)

        camera_repo = MagicMock()
        camera_repo.list_for_site_config.return_value = [self._camera_row(), self._camera_row(2)]
        payload = {**VALID_PAYLOAD, "config_version_applied": "stale-outdated-hash"}

        with self._patch_repos(mock_repo, camera_repo), caplog.at_level("WARNING"):
            res = client.post(
                "/api/v1/edge/heartbeat",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )

        assert res.status_code == 201  # best-effort: nunca falha o heartbeat
        assert "edge_config_divergence" in caplog.text
        assert "stale-outdated-hash" in caplog.text

    def test_camera_repo_error_never_breaks_heartbeat_ingest(
        self, client, device_setup, mock_repo
    ) -> None:
        """DB indisponível pro check de divergência não pode derrubar o
        heartbeat — best-effort, mesmo padrão de _bridge_heartbeat_to_telemetry."""
        private_pem, _, tenant_id, site_id, device_id = device_setup
        token = _make_token(private_pem, tenant_id, site_id, device_id)

        camera_repo = MagicMock()
        camera_repo.list_for_site_config.side_effect = RuntimeError("db down")
        payload = {**VALID_PAYLOAD, "config_version_applied": "some-hash"}

        with self._patch_repos(mock_repo, camera_repo):
            res = client.post(
                "/api/v1/edge/heartbeat",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )

        assert res.status_code == 201
        mock_repo.insert_heartbeat.assert_called_once()

    # -----------------------------------------------------------------------
    # #477 — rate-limit do aviso. A definição de pronto da issue são os três
    # primeiros casos; o quarto (a resolução) é o que impede a supressão de
    # virar esquecimento.
    # -----------------------------------------------------------------------

    def _bate_heartbeat(self, client, device_setup, mock_repo, cameras, applied):
        private_pem, _, tenant_id, site_id, device_id = device_setup
        token = _make_token(private_pem, tenant_id, site_id, device_id)
        camera_repo = MagicMock()
        camera_repo.list_for_site_config.return_value = cameras
        payload = {**VALID_PAYLOAD, "config_version_applied": applied}
        with self._patch_repos(mock_repo, camera_repo):
            res = client.post(
                "/api/v1/edge/heartbeat",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
        assert res.status_code == 201
        return res

    def test_repeticao_identica_dentro_da_janela_NAO_loga(
        self, client, device_setup, mock_repo, caplog
    ) -> None:
        """(1) da definição de pronto. Antes de #477 o aviso saía a CADA
        heartbeat — a divergência é um estado, não um evento."""
        cameras = [self._camera_row(), self._camera_row(2)]

        with caplog.at_level("WARNING"):
            self._bate_heartbeat(client, device_setup, mock_repo, cameras, "stale-a")
        assert caplog.text.count("edge_config_divergence:") == 1

        caplog.clear()
        with caplog.at_level("WARNING"):
            for _ in range(5):
                self._bate_heartbeat(client, device_setup, mock_repo, cameras, "stale-a")
        assert "edge_config_divergence" not in caplog.text

    def test_mudanca_de_conteudo_loga_mesmo_dentro_da_janela(
        self, client, device_setup, mock_repo, caplog
    ) -> None:
        """(2) da definição de pronto. Silenciar por tempo é o jeito fácil e é
        onde isto daria errado: divergência DIFERENTE é informação nova."""
        cameras = [self._camera_row(), self._camera_row(2)]

        with caplog.at_level("WARNING"):
            self._bate_heartbeat(client, device_setup, mock_repo, cameras, "stale-a")
            self._bate_heartbeat(client, device_setup, mock_repo, cameras, "stale-a")
            caplog.clear()
            self._bate_heartbeat(client, device_setup, mock_repo, cameras, "stale-B-outro")

        assert "edge_config_divergence" in caplog.text
        assert "stale-B-outro" in caplog.text

    def test_resolucao_loga_com_a_contagem_de_suprimidos(
        self, client, device_setup, mock_repo, caplog
    ) -> None:
        """(3) da definição de pronto — a linha que impede a supressão de virar
        esquecimento: quem lê o log sabe que houve silêncio e de que tamanho."""

        cameras = [self._camera_row(), self._camera_row(2)]
        atual = self._versao_corrente(mock_repo, cameras)

        with caplog.at_level("WARNING"):
            self._bate_heartbeat(client, device_setup, mock_repo, cameras, "stale-a")
            for _ in range(4):
                self._bate_heartbeat(client, device_setup, mock_repo, cameras, "stale-a")
            caplog.clear()
            self._bate_heartbeat(client, device_setup, mock_repo, cameras, atual)

        assert "edge_config_divergence_resolvida" in caplog.text
        assert "repeticoes_suprimidas=4" in caplog.text

    def test_sem_divergencia_previa_a_resolucao_NAO_loga(
        self, client, device_setup, mock_repo, caplog
    ) -> None:
        """Config em dia desde sempre não pode gerar linha de 'resolvida' —
        seria ruído novo no lugar do que a issue veio remover."""

        cameras = [self._camera_row()]
        atual = self._versao_corrente(mock_repo, cameras)

        with caplog.at_level("WARNING"):
            self._bate_heartbeat(client, device_setup, mock_repo, cameras, atual)

        assert "edge_config_divergence" not in caplog.text

    def test_passada_a_janela_volta_a_avisar_dizendo_ha_quanto_tempo(
        self, client, device_setup, mock_repo, caplog, monkeypatch
    ) -> None:
        """(4) a janela expira: o aviso volta, e volta dizendo que PERSISTE —
        um aviso repetido idêntico não distinguiria 'de novo' de 'ainda'."""
        import app.api.v1.edge.routes as edge_routes

        cameras = [self._camera_row(), self._camera_row(2)]
        monkeypatch.setattr(edge_routes, "_DIVERGENCE_WINDOW_S", 60.0)

        relogio = {"t": 1000.0}
        monkeypatch.setattr(edge_routes.time, "monotonic", lambda: relogio["t"])

        with caplog.at_level("WARNING"):
            self._bate_heartbeat(client, device_setup, mock_repo, cameras, "stale-a")
            relogio["t"] += 10
            self._bate_heartbeat(client, device_setup, mock_repo, cameras, "stale-a")
            caplog.clear()
            relogio["t"] += 120  # passou a janela
            self._bate_heartbeat(client, device_setup, mock_repo, cameras, "stale-a")

        assert "edge_config_divergence" in caplog.text
        assert "PERSISTE ha 130s" in caplog.text
        assert "1 repeticoes suprimidas" in caplog.text

