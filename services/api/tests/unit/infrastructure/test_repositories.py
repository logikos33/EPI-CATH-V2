"""Tests: Repository pattern (mocked database)."""
from unittest.mock import MagicMock
from contextlib import contextmanager
from uuid import uuid4

from app.infrastructure.database.repositories.user_repository import UserRepository
from app.infrastructure.database.repositories.video_repository import VideoRepository
from app.infrastructure.database.repositories.camera_repository import CameraRepository


class MockPool:
    """Lightweight mock for DatabasePool with context manager support."""

    def __init__(self) -> None:
        self.mock_cursor = MagicMock()
        self.mock_conn = MagicMock()
        self.mock_conn.cursor.return_value = self.mock_cursor

    @contextmanager
    def get_connection(self):  # type: ignore[no-untyped-def]
        yield self.mock_conn
        self.mock_conn.commit()


class TestUserRepository:
    """Testes para UserRepository."""

    def setup_method(self) -> None:
        self.pool = MockPool()
        self.repo = UserRepository(self.pool)  # type: ignore[arg-type]

    def test_create_user(self) -> None:
        uid = uuid4()
        self.pool.mock_cursor.fetchone.return_value = {
            "id": uid, "email": "test@test.com",
            "name": "Test", "role": "operator",
            "is_active": True, "created_at": "2024-01-01",
        }
        result = self.repo.create("test@test.com", "hashed", "Test")
        assert result["email"] == "test@test.com"
        self.pool.mock_cursor.execute.assert_called_once()

    def test_get_by_email(self) -> None:
        self.pool.mock_cursor.fetchone.return_value = {
            "id": uuid4(), "email": "test@test.com",
            "name": "Test", "role": "operator",
            "password_hash": "hashed", "is_active": True,
            "created_at": "2024-01-01",
        }
        result = self.repo.get_by_email("test@test.com")
        assert result is not None
        assert result["email"] == "test@test.com"

    def test_get_by_email_not_found(self) -> None:
        self.pool.mock_cursor.fetchone.return_value = None
        result = self.repo.get_by_email("nonexistent@test.com")
        assert result is None

    def test_exists_by_email(self) -> None:
        self.pool.mock_cursor.fetchone.return_value = {"exists": True}
        assert self.repo.exists_by_email("test@test.com") is True

    def test_exists_by_email_false(self) -> None:
        self.pool.mock_cursor.fetchone.return_value = {"exists": False}
        assert self.repo.exists_by_email("new@test.com") is False


class TestVideoRepository:
    """Testes para VideoRepository."""

    def setup_method(self) -> None:
        self.pool = MockPool()
        self.repo = VideoRepository(self.pool)  # type: ignore[arg-type]

    def test_create_video(self) -> None:
        vid = uuid4()
        uid = uuid4()
        self.pool.mock_cursor.fetchone.return_value = {
            "id": vid, "user_id": uid, "filename": "test.mp4",
            "status": "uploaded", "frame_count": 0, "created_at": "2024-01-01",
        }
        result = self.repo.create(uid, "test.mp4", "original.mp4", 1024)
        assert result["filename"] == "test.mp4"

    def test_get_by_user(self) -> None:
        self.pool.mock_cursor.fetchall.return_value = [
            {"id": uuid4(), "filename": "v1.mp4"},
            {"id": uuid4(), "filename": "v2.mp4"},
        ]
        result = self.repo.get_by_user(uuid4())
        assert len(result) == 2

    def test_get_by_id(self) -> None:
        vid = uuid4()
        self.pool.mock_cursor.fetchone.return_value = {
            "id": vid, "filename": "raw-videos/u/v/test.mp4", "status": "uploaded",
        }
        result = self.repo.get_by_id(vid)
        assert result is not None
        assert result["status"] == "uploaded"

    def test_update_status(self) -> None:
        vid = uuid4()
        self.pool.mock_cursor.fetchone.return_value = {
            "id": vid, "status": "processing",
        }
        result = self.repo.update_status(vid, "processing")
        assert result["status"] == "processing"


class TestCameraRepository:
    """Testes para CameraRepository."""

    def setup_method(self) -> None:
        self.pool = MockPool()
        self.repo = CameraRepository(self.pool)  # type: ignore[arg-type]

    def test_create_camera(self) -> None:
        cam_id = uuid4()
        self.pool.mock_cursor.fetchone.return_value = {
            "id": cam_id, "name": "Camera 1",
            "host": "192.168.1.100", "port": 554,
            "manufacturer": "generic", "is_active": True,
        }
        result = self.repo.create({
            "tenant_id": uuid4(),
            "user_id": uuid4(),
            "name": "Camera 1",
            "host": "192.168.1.100",
        })
        assert result["name"] == "Camera 1"

    def test_get_all(self) -> None:
        self.pool.mock_cursor.fetchall.return_value = [
            {"id": uuid4(), "name": "Cam 1"},
            {"id": uuid4(), "name": "Cam 2"},
        ]
        result = self.repo.get_all()
        assert len(result) == 2

    def test_toda_leitura_de_camera_exclui_a_apagada(self) -> None:
        """A garantia do requisito: câmera excluída some de TODAS as telas.

        É por AQUI que ela some — todas as telas leem por estes métodos. Se
        alguém tirar `deleted_at IS NULL` de qualquer um deles, a câmera
        excluída reaparece só naquela superfície (lista, Ao Vivo, seletor do
        grid, config do edge, inventário do superadmin, KPI do dashboard) e
        nada mais quebra. Este teste é o que quebra.
        """
        cam_id, tenant_id, site_id = uuid4(), str(uuid4()), str(uuid4())
        self.pool.mock_cursor.fetchone.return_value = None
        self.pool.mock_cursor.fetchall.return_value = []

        leituras = [
            ("get_by_id", lambda: self.repo.get_by_id(cam_id)),
            ("get_by_user", lambda: self.repo.get_by_user(cam_id)),
            ("get_all", lambda: self.repo.get_all()),
            ("get_by_id_and_tenant",
             lambda: self.repo.get_by_id_and_tenant(str(cam_id), tenant_id)),
            ("list_for_site_config",
             lambda: self.repo.list_for_site_config(site_id, tenant_id)),
            ("sum_fps_demand", lambda: self.repo.sum_fps_demand(site_id, tenant_id)),
            ("count_active_all", lambda: self.repo.count_active_all(tenant_id)),
            ("count_all", lambda: self.repo.count_all(tenant_id)),
            ("count_by_module", lambda: self.repo.count_by_module(tenant_id, "epi")),
            ("count_by_status",
             lambda: self.repo.count_by_status(tenant_id, "epi", "active")),
            ("get_inventory", lambda: self.repo.get_inventory(tenant_id=tenant_id)),
        ]
        for nome, chamada in leituras:
            self.pool.mock_cursor.execute.reset_mock()
            chamada()
            sql = self.pool.mock_cursor.execute.call_args[0][0]
            assert "deleted_at IS NULL" in sql, f"{nome} lê câmera excluída"

    def test_soft_delete_marca_deleted_at_e_nao_apaga_linha(self) -> None:
        """Excluir câmera é UPDATE, jamais DELETE físico.

        O DELETE levaria alerta e evidência junto por CASCATA (registro
        histórico, possível valor legal) e travaria por FK em câmera com
        frame de treino — ver migration 138.
        """
        cam_id = uuid4()
        self.pool.mock_cursor.fetchone.return_value = {"id": cam_id, "name": "Portaria"}
        result = self.repo.soft_delete(cam_id)
        assert result == {"id": cam_id, "name": "Portaria"}
        sql = self.pool.mock_cursor.execute.call_args[0][0]
        assert sql.strip().upper().startswith("UPDATE")
        assert "DELETE FROM" not in sql.upper()
        assert "deleted_at = NOW()" in sql
        # is_active junto: todo filtro `is_active = true` que já existe passa a
        # excluir a câmera sem precisar aprender o que é deleted_at.
        assert "is_active = false" in sql


class TestFrameRepository:
    """Testes para FrameRepository."""

    def setup_method(self) -> None:
        self.pool = MockPool()
        from app.infrastructure.database.repositories.frame_repository import FrameRepository
        self.repo = FrameRepository(self.pool)  # type: ignore[arg-type]

    def test_create_frame(self) -> None:
        fid = uuid4()
        vid = uuid4()
        self.pool.mock_cursor.fetchone.return_value = {
            "id": fid, "video_id": vid, "frame_number": 0,
            "filename": "frames/u/v/frame_0000.jpg",
            "is_annotated": False, "quality_status": "pending",
        }
        result = self.repo.create(vid, 0, "frames/u/v/frame_0000.jpg")
        assert result["frame_number"] == 0
        self.pool.mock_cursor.execute.assert_called_once()

    def test_get_by_video(self) -> None:
        vid = uuid4()
        self.pool.mock_cursor.fetchall.return_value = [
            {"id": uuid4(), "frame_number": 0},
            {"id": uuid4(), "frame_number": 1},
        ]
        result = self.repo.get_by_video(vid)
        assert len(result) == 2

    def test_get_by_id(self) -> None:
        fid = uuid4()
        self.pool.mock_cursor.fetchone.return_value = {"id": fid, "frame_number": 3}
        result = self.repo.get_by_id(fid)
        assert result is not None
        assert result["frame_number"] == 3

    def test_get_by_id_not_found(self) -> None:
        self.pool.mock_cursor.fetchone.return_value = None
        result = self.repo.get_by_id(uuid4())
        assert result is None

    def test_get_next_unannotated(self) -> None:
        fid = uuid4()
        self.pool.mock_cursor.fetchone.return_value = {
            "id": fid, "frame_number": 0, "is_annotated": False,
        }
        result = self.repo.get_next_unannotated(uuid4())
        assert result is not None
        assert result["is_annotated"] is False

    def test_mark_annotated(self) -> None:
        fid = uuid4()
        self.pool.mock_cursor.fetchone.return_value = {
            "id": fid, "is_annotated": True,
        }
        result = self.repo.mark_annotated(fid)
        assert result["is_annotated"] is True

    def test_update_quality_status_approved(self) -> None:
        fid = uuid4()
        self.pool.mock_cursor.fetchone.return_value = {
            "id": fid, "quality_status": "approved",
            "quality_scores": '{"blur": 150.5, "brightness": 80.2}',
        }
        result = self.repo.update_quality_status(
            fid, "approved", {"blur": 150.5, "brightness": 80.2}
        )
        assert result["quality_status"] == "approved"
        self.pool.mock_cursor.execute.assert_called_once()

    def test_update_quality_status_rejected(self) -> None:
        fid = uuid4()
        self.pool.mock_cursor.fetchone.return_value = {
            "id": fid, "quality_status": "rejected",
            "quality_scores": '{"reason": "blur"}',
        }
        result = self.repo.update_quality_status(fid, "rejected", {"reason": "blur"})
        assert result["quality_status"] == "rejected"

    def test_get_approved_by_video(self) -> None:
        vid = uuid4()
        self.pool.mock_cursor.fetchall.return_value = [
            {"id": uuid4(), "quality_status": "approved"},
            {"id": uuid4(), "quality_status": "pending"},
        ]
        result = self.repo.get_approved_by_video(vid)
        assert len(result) == 2

    def test_count_by_status(self) -> None:
        vid = uuid4()
        self.pool.mock_cursor.fetchall.return_value = [
            {"is_annotated": False, "count": 5},
            {"is_annotated": True, "count": 3},
        ]
        result = self.repo.count_by_status(vid)
        assert result["annotated"] == 3
        assert result["pending"] == 5
        assert result["total"] == 8


class TestAnnotationRepository:
    """Testes para AnnotationRepository."""

    def setup_method(self) -> None:
        self.pool = MockPool()
        from app.infrastructure.database.repositories.annotation_repository import AnnotationRepository
        self.repo = AnnotationRepository(self.pool)  # type: ignore[arg-type]

    def test_create_class(self) -> None:
        uid = uuid4()
        tid = uuid4()
        self.pool.mock_cursor.fetchone.return_value = {
            "id": 1, "user_id": uid, "name": "Capacete", "color": "#22c55e",
        }
        # tenant_id é keyword-only e obrigatório (fail-closed — sem fallback
        # via tenant "de casa" do usuário, ver test_annotation_repository_tenant.py).
        result = self.repo.create_class(uid, "Capacete", "#22c55e", tenant_id=tid)
        assert result["name"] == "Capacete"

    def test_get_classes_by_user(self) -> None:
        uid = uuid4()
        self.pool.mock_cursor.fetchall.return_value = [
            {"id": 1, "name": "Capacete", "color": "#22c55e"},
            {"id": 2, "name": "Colete", "color": "#f59e0b"},
        ]
        result = self.repo.get_classes_by_user(uid)
        assert len(result) == 2

    def test_create_annotation(self) -> None:
        fid = uuid4()
        ann_id = uuid4()
        self.pool.mock_cursor.fetchone.return_value = {
            "id": ann_id, "frame_id": fid, "class_id": 1,
            "x_center": 0.5, "y_center": 0.5, "width": 0.3, "height": 0.4,
        }
        result = self.repo.create_annotation(fid, 1, 0.5, 0.5, 0.3, 0.4)
        assert result["class_id"] == 1
        assert result["x_center"] == 0.5

    def test_get_by_frame(self) -> None:
        fid = uuid4()
        self.pool.mock_cursor.fetchall.return_value = [
            {"id": uuid4(), "class_id": 1, "class_name": "Capacete",
             "x_center": 0.5, "y_center": 0.5, "width": 0.3, "height": 0.4},
        ]
        result = self.repo.get_by_frame(fid)
        assert len(result) == 1
        assert result[0]["class_name"] == "Capacete"

    def test_delete_by_frame(self) -> None:
        fid = uuid4()
        self.pool.mock_cursor.rowcount = 3
        result = self.repo.delete_by_frame(fid)
        assert result == 3

    def test_save_batch(self) -> None:
        fid = uuid4()
        self.pool.mock_cursor.fetchone.return_value = {
            "id": uuid4(), "class_id": 1,
            "x_center": 0.5, "y_center": 0.5, "width": 0.3, "height": 0.4,
        }
        self.pool.mock_cursor.rowcount = 1
        annotations = [
            {"class_id": 1, "class_name": "no_helmet", "module_code": "epi",
             "x_center": 0.5, "y_center": 0.5, "width": 0.3, "height": 0.4},
            {"class_id": 2, "class_name": "vest", "module_code": "epi",
             "x_center": 0.2, "y_center": 0.8, "width": 0.1, "height": 0.2},
        ]
        count = self.repo.save_batch(fid, annotations)
        assert count == 2

    def test_save_batch_inserts_with_manual_provenance(self) -> None:
        """Save humano (AnnotationInterface.jsx) grava source='manual' +
        created_by=user_id explicitamente no INSERT — antes do fix,
        created_by ficava NULL e não se sabia quem anotou."""
        fid = uuid4()
        uid = uuid4()
        self.pool.mock_cursor.fetchone.return_value = {
            "id": uuid4(), "class_id": 1,
            "x_center": 0.5, "y_center": 0.5, "width": 0.3, "height": 0.4,
        }
        annotations = [
            {"class_id": 1, "class_name": "no_helmet", "module_code": "epi",
             "x_center": 0.5, "y_center": 0.5, "width": 0.3, "height": 0.4},
            {"class_id": 2, "class_name": "vest", "module_code": "epi",
             "x_center": 0.2, "y_center": 0.8, "width": 0.1, "height": 0.2},
        ]
        count = self.repo.save_batch(fid, annotations, uid)
        assert count == 2

        insert_calls = [
            call for call in self.pool.mock_cursor.execute.call_args_list
            if call.args[0].startswith("INSERT")
        ]
        assert len(insert_calls) == 2
        for call in insert_calls:
            sql, params = call.args
            assert "created_by" in sql
            # `source` deixou de ser literal no SQL e virou parâmetro: uma
            # caixa que o humano não tocou herda a proveniência anterior
            # (#536). Sem frame anterior, como aqui, tudo nasce 'manual'.
            source, created_by = params[-6], params[-5]
            assert source == "manual"
            assert created_by == str(uid)

    def test_save_batch_without_user_id_leaves_created_by_null(self) -> None:
        """Chamadas internas/Celery sem contexto de usuário: created_by
        fica NULL (honesto), nunca um fallback silencioso."""
        fid = uuid4()
        self.pool.mock_cursor.fetchone.return_value = {
            "id": uuid4(), "class_id": 1,
            "x_center": 0.5, "y_center": 0.5, "width": 0.3, "height": 0.4,
        }
        annotations = [
            {"class_id": 1, "class_name": "no_helmet", "module_code": "epi",
             "x_center": 0.5, "y_center": 0.5, "width": 0.3, "height": 0.4},
        ]
        count = self.repo.save_batch(fid, annotations)
        assert count == 1

        insert_calls = [
            call for call in self.pool.mock_cursor.execute.call_args_list
            if call.args[0].startswith("INSERT")
        ]
        assert len(insert_calls) == 1
        assert insert_calls[0].args[1][-1] is None

    def test_accept_pre_annotations_inserts_with_provenance(self) -> None:
        """WS-B4: accept_pre_annotations grava source='pre_annotation' +
        created_by/reviewed_by=user_id (migration 095) — INSERT puro, não
        apaga anotações humanas existentes (ao contrário de save_batch)."""
        fid = uuid4()
        uid = uuid4()
        self.pool.mock_cursor.fetchone.return_value = {
            "id": uuid4(), "class_id": 1,
            "x_center": 0.5, "y_center": 0.5, "width": 0.3, "height": 0.4,
        }
        self.pool.mock_cursor.rowcount = 1
        annotations = [
            {"class_id": 1, "x_center": 0.5, "y_center": 0.5, "width": 0.3, "height": 0.4},
        ]
        count = self.repo.accept_pre_annotations(fid, annotations, uid)
        assert count == 1

        # Escolhido por CONTEÚDO, não por posição: a transação abre com um
        # SELECT ... FOR UPDATE (trava de concorrência do #801) e um índice
        # fixo passou a apontar para ele.
        insert_query, insert_params = next(
            chamada[0] for chamada in self.pool.mock_cursor.execute.call_args_list
            if chamada[0][0].startswith("INSERT")
        )
        assert "'pre_annotation'" in insert_query
        assert "DELETE" not in insert_query.upper()
        assert str(uid) in insert_params

    def test_accept_pre_annotations_stamps_review_status_same_transaction(self) -> None:
        """Migration 111: fecha a fila de aprovação (?pending_review=true)
        no MESMO commit do INSERT — sem isso, um request lendo entre as
        duas operações veria um frame com caixa aceita mas ainda pendente."""
        fid = uuid4()
        uid = uuid4()
        self.pool.mock_cursor.fetchone.return_value = {
            "id": uuid4(), "class_id": 1,
            "x_center": 0.5, "y_center": 0.5, "width": 0.3, "height": 0.4,
        }
        self.pool.mock_cursor.rowcount = 1
        annotations = [
            {"class_id": 1, "x_center": 0.5, "y_center": 0.5, "width": 0.3, "height": 0.4},
        ]
        self.repo.accept_pre_annotations(fid, annotations, uid)

        update_query, update_params = self.pool.mock_cursor.execute.call_args_list[-1][0]
        assert "UPDATE training_frames" in update_query
        assert "'accepted'" in update_query
        assert "pre_annotation_review_status" in update_query
        assert str(uid) in update_params
        assert str(fid) in update_params

    def test_accept_pre_annotations_zero_count_does_not_stamp_review(self) -> None:
        """Lista vazia de anotações não deve gerar UPDATE nenhum (nada foi
        de fato aceito — sem contagem, sem estampa)."""
        fid = uuid4()
        uid = uuid4()
        count = self.repo.accept_pre_annotations(fid, [], uid)
        assert count == 0
        # A transação abre travando a linha do frame (SELECT ... FOR UPDATE,
        # #801) — o que não pode acontecer é ESCRITA: nem INSERT de caixa,
        # nem UPDATE de estampa.
        escritas = [
            chamada[0][0] for chamada in self.pool.mock_cursor.execute.call_args_list
            if not chamada[0][0].startswith("SELECT")
        ]
        assert escritas == []


class TestDatasetRepository:
    """Testes para DatasetRepository."""

    def setup_method(self) -> None:
        self.pool = MockPool()
        from app.infrastructure.database.repositories.dataset_repository import DatasetRepository
        self.repo = DatasetRepository(self.pool)  # type: ignore[arg-type]

    def test_create_dataset(self) -> None:
        uid = uuid4()
        did = uuid4()
        self.pool.mock_cursor.fetchone.return_value = {
            "id": did, "user_id": uid, "version": "v1.0.0",
            "frame_count": 100,
        }
        result = self.repo.create({
            "user_id": uid, "version": "v1.0.0",
            "frame_count": 100, "train_count": 70,
            "val_count": 20, "test_count": 10,
        })
        assert result["version"] == "v1.0.0"

    def test_get_by_id(self) -> None:
        did = uuid4()
        self.pool.mock_cursor.fetchone.return_value = {
            "id": did, "version": "v1.0.0",
        }
        result = self.repo.get_by_id(did)
        assert result is not None
        assert result["version"] == "v1.0.0"

    def test_get_by_user(self) -> None:
        uid = uuid4()
        self.pool.mock_cursor.fetchall.return_value = [
            {"id": uuid4(), "version": "v1.0.0"},
            {"id": uuid4(), "version": "v1.1.0"},
        ]
        result = self.repo.get_by_user(uid)
        assert len(result) == 2

    def test_get_latest(self) -> None:
        uid = uuid4()
        self.pool.mock_cursor.fetchone.return_value = {
            "id": uuid4(), "version": "v2.0.0",
        }
        result = self.repo.get_latest(uid)
        assert result is not None
        assert result["version"] == "v2.0.0"


class TestBaseExecuteMany:
    """Tests for _execute_many in BaseRepository."""

    def setup_method(self) -> None:
        self.pool = MockPool()
        from app.infrastructure.database.repositories.frame_repository import FrameRepository
        self.repo = FrameRepository(self.pool)  # type: ignore[arg-type]

    def test_execute_many_bulk(self) -> None:
        vid = uuid4()
        self.pool.mock_cursor.rowcount = 3
        count = self.repo.create_bulk([
            {"video_id": str(vid), "frame_number": 0,
             "filename": "frames/a.jpg", "timestamp_seconds": None},
            {"video_id": str(vid), "frame_number": 1,
             "filename": "frames/b.jpg", "timestamp_seconds": None},
            {"video_id": str(vid), "frame_number": 2,
             "filename": "frames/c.jpg", "timestamp_seconds": None},
        ])
        assert count == 3

    def test_execute_many_receives_dicts_not_tuples(self) -> None:
        """Bug latente corrigido: executemany com placeholders nomeados exige
        lista de DICTS — antes o create_bulk embrulhava cada dict em tupla."""
        vid = uuid4()
        self.pool.mock_cursor.rowcount = 1
        self.repo.create_bulk([
            {"video_id": str(vid), "frame_number": 0, "filename": "frames/a.jpg"},
        ])
        params_list = self.pool.mock_cursor.executemany.call_args[0][1]
        assert isinstance(params_list, list)
        assert isinstance(params_list[0], dict)
        assert params_list[0]["video_id"] == str(vid)
        # Defaults retrocompatíveis preenchidos
        assert params_list[0]["source"] == "video"
        assert params_list[0]["r2_key"] == "frames/a.jpg"
        assert params_list[0]["timestamp_seconds"] is None


class TestTrainingRepository:
    """Testes para TrainingRepository."""

    def setup_method(self) -> None:
        self.pool = MockPool()
        from app.infrastructure.database.repositories.training_repository import TrainingRepository
        self.repo = TrainingRepository(self.pool)  # type: ignore[arg-type]

    def test_create_job(self) -> None:
        uid = uuid4()
        job_id = uuid4()
        self.pool.mock_cursor.fetchone.return_value = {
            "id": job_id, "user_id": uid, "status": "queued",
            "preset": "balanced", "model_size": "yolo26n", "total_epochs": 100,
        }
        result = self.repo.create_job(uid, "balanced", "yolo26n", 100)
        assert result["status"] == "queued"
        assert result["preset"] == "balanced"

    def test_get_job_by_id(self) -> None:
        job_id = uuid4()
        self.pool.mock_cursor.fetchone.return_value = {
            "id": job_id, "status": "running",
        }
        result = self.repo.get_job_by_id(job_id)
        assert result is not None
        assert result["status"] == "running"

    def test_get_job_by_id_not_found(self) -> None:
        self.pool.mock_cursor.fetchone.return_value = None
        result = self.repo.get_job_by_id(uuid4())
        assert result is None

    def test_get_jobs_by_user(self) -> None:
        uid = uuid4()
        self.pool.mock_cursor.fetchall.return_value = [
            {"id": uuid4(), "status": "completed"},
            {"id": uuid4(), "status": "queued"},
        ]
        result = self.repo.get_jobs_by_user(uid)
        assert len(result) == 2

    def test_update_job_status_simple(self) -> None:
        job_id = uuid4()
        self.pool.mock_cursor.fetchone.return_value = {
            "id": job_id, "status": "running",
        }
        result = self.repo.update_job_status(job_id, "running")
        assert result["status"] == "running"

    def test_update_job_status_with_progress(self) -> None:
        job_id = uuid4()
        self.pool.mock_cursor.fetchone.return_value = {
            "id": job_id, "status": "running", "progress": 50,
        }
        result = self.repo.update_job_status(job_id, "running", progress=50, current_epoch=50)
        assert result["progress"] == 50

    def test_update_job_status_completed(self) -> None:
        job_id = uuid4()
        self.pool.mock_cursor.fetchone.return_value = {
            "id": job_id, "status": "completed",
        }
        result = self.repo.update_job_status(
            job_id, "completed", metrics={"map50": 0.85}
        )
        assert result["status"] == "completed"

    def test_update_job_status_failed(self) -> None:
        job_id = uuid4()
        self.pool.mock_cursor.fetchone.return_value = {
            "id": job_id, "status": "failed",
        }
        result = self.repo.update_job_status(
            job_id, "failed", error_message="CUDA out of memory"
        )
        assert result["status"] == "failed"

    def test_create_model(self) -> None:
        uid = uuid4()
        model_id = uuid4()
        self.pool.mock_cursor.fetchone.return_value = {
            "id": model_id, "name": "Model v1",
            "model_path": "models/best.pt", "is_active": False,
        }
        result = self.repo.create_model({
            "user_id": uid,
            "job_id": uuid4(),
            "name": "Model v1",
            "model_path": "models/best.pt",
            "map50": 0.85,
            "precision": 0.90,
            "recall": 0.80,
        })
        assert result["name"] == "Model v1"

    def test_get_models_by_tenant_seleciona_framework(self) -> None:
        """task-083: payload precisa carregar tm.framework para a UI exibir
        o backend efetivo (RF-DETR/YOLOX) por modelo na tela de atribuição
        de câmera — antes a query não selecionava essa coluna."""
        tenant_id = uuid4()
        self.pool.mock_cursor.fetchall.return_value = [
            {"id": uuid4(), "name": "Model v1", "is_active": True, "framework": "rfdetr"},
        ]
        result = self.repo.get_models_by_tenant(tenant_id)
        assert result[0]["framework"] == "rfdetr"
        query = self.pool.mock_cursor.execute.call_args[0][0]
        assert "tm.framework" in query

    def test_get_models_by_tenant(self) -> None:
        tenant_id = uuid4()
        self.pool.mock_cursor.fetchall.return_value = [
            {"id": uuid4(), "name": "Model v1", "is_active": True},
        ]
        result = self.repo.get_models_by_tenant(tenant_id)
        assert len(result) == 1


class TestModelRegistryRepository:
    """Task D3 ("job/treino aparece com nome cru"): GET /api/v1/models
    (list_for_tenant/get_for_tenant) precisa servir tm.display_name — sem
    essa coluna o front só recebia o `name` interno."""

    def setup_method(self) -> None:
        self.pool = MockPool()
        from app.infrastructure.database.repositories.model_registry_repository import (
            ModelRegistryRepository,
        )
        self.repo = ModelRegistryRepository(self.pool)  # type: ignore[arg-type]

    def test_list_for_tenant_selects_display_name(self) -> None:
        tenant_id = uuid4()
        self.pool.mock_cursor.fetchall.return_value = [
            {"id": uuid4(), "name": "RF-DETR - Job abc12345", "display_name": "Logikos V1 · 25/08"},
        ]
        result = self.repo.list_for_tenant(str(tenant_id))
        query = self.pool.mock_cursor.execute.call_args[0][0]
        assert "tm.display_name" in query
        assert result[0]["display_name"] == "Logikos V1 · 25/08"

    def test_get_for_tenant_selects_display_name(self) -> None:
        model_id = uuid4()
        tenant_id = uuid4()
        self.pool.mock_cursor.fetchone.return_value = {
            "id": model_id, "name": "YOLOX - Job def67890", "display_name": None,
        }
        self.repo.get_for_tenant(model_id, str(tenant_id))
        query = self.pool.mock_cursor.execute.call_args[0][0]
        assert "tm.display_name" in query


class TestModelTrainingMetricsRepository:
    """Task D3: models_summary() (Dashboard Integrado /epi/edge-observability)
    junta trained_models por nome para servir display_name — antes só
    devolvia model_name cru (o `name` interno gravado por dispatch_training)."""

    def setup_method(self) -> None:
        self.pool = MockPool()
        from app.infrastructure.database.repositories.model_training_metrics_repository import (  # noqa: E501
            ModelTrainingMetricsRepository,
        )
        self.repo = ModelTrainingMetricsRepository(self.pool)  # type: ignore[arg-type]

    def test_models_summary_joins_trained_models_for_display_name(self) -> None:
        tenant_id = str(uuid4())
        self.pool.mock_cursor.fetchall.return_value = [
            {
                "model_name": "RF-DETR - Job abc12345", "framework": "rfdetr",
                "last_epoch": 10, "ap5095": "0.5", "ap_small": "0.3",
                "epoch_count": 10, "display_name": "Logikos V3 · 20/08",
            },
        ]
        result = self.repo.models_summary(tenant_id)
        query = self.pool.mock_cursor.execute.call_args[0][0]
        assert "LEFT JOIN trained_models tm ON tm.name = m.model_name" in query
        assert "tm.display_name" in query
        assert result[0]["display_name"] == "Logikos V3 · 20/08"
        # Q3 (veredito rodada 2, D3-nomes.md): sem estas duas linhas, a
        # query podia perder o escopo de tenant sem nenhum teste reprovar
        # (mutações M6/M7 do cético passavam verde) — pega isolamento sem
        # depender de Postgres real:
        # M6 trocava `WHERE m.tenant_id = %s` por `WHERE %s IS NOT NULL`
        # (aceita qualquer linha do banco inteiro, não só do tenant).
        assert "WHERE m.tenant_id = %s" in query
        # M7 removia o `AND COALESCE(tm.tenant_id, ...) = m.tenant_id` do
        # LEFT JOIN (trained_models de OUTRO tenant com o mesmo nome de
        # modelo vazava o display_name errado).
        assert (
            "AND COALESCE(tm.tenant_id, (SELECT u.tenant_id FROM users u"
            in query
        )
        assert "WHERE u.id = tm.user_id)) = m.tenant_id" in query


class TestAlertRepository:
    """Testes para AlertRepository."""

    def setup_method(self) -> None:
        self.pool = MockPool()
        from app.infrastructure.database.repositories.alert_repository import AlertRepository
        self.repo = AlertRepository(self.pool)  # type: ignore[arg-type]

    def test_create_alert(self) -> None:
        cam_id = uuid4()
        alert_id = uuid4()
        self.pool.mock_cursor.fetchone.return_value = {
            "id": alert_id, "camera_id": cam_id,
            "confidence": 0.95, "acknowledged": False,
        }
        result = self.repo.create(
            cam_id,
            [{"class": "no_helmet", "box": [0.1, 0.2, 0.3, 0.4]}],
            0.95,
            "evidence/cam1/2024-01.jpg",
        )
        assert result["confidence"] == 0.95

    def test_get_by_camera(self) -> None:
        cam_id = uuid4()
        self.pool.mock_cursor.fetchall.return_value = [
            {"id": uuid4(), "confidence": 0.9, "acknowledged": False},
            {"id": uuid4(), "confidence": 0.8, "acknowledged": True},
        ]
        result = self.repo.get_by_camera(cam_id, tenant_id=str(uuid4()))
        assert len(result) == 2

    def test_get_unacknowledged_with_camera(self) -> None:
        cam_id = uuid4()
        self.pool.mock_cursor.fetchall.return_value = [
            {"id": uuid4(), "acknowledged": False},
        ]
        result = self.repo.get_unacknowledged(cam_id)
        assert len(result) == 1

    def test_get_unacknowledged_all(self) -> None:
        self.pool.mock_cursor.fetchall.return_value = [
            {"id": uuid4(), "acknowledged": False},
            {"id": uuid4(), "acknowledged": False},
        ]
        result = self.repo.get_unacknowledged()
        assert len(result) == 2

    def test_acknowledge(self) -> None:
        alert_id = uuid4()
        self.pool.mock_cursor.fetchone.return_value = {
            "id": alert_id, "acknowledged": True,
        }
        result = self.repo.acknowledge(alert_id, tenant_id=str(uuid4()))
        assert result["acknowledged"] is True

    def test_count_by_camera(self) -> None:
        cam_id = uuid4()
        self.pool.mock_cursor.fetchone.return_value = {"count": 7}
        count = self.repo.count_by_camera(cam_id)
        assert count == 7

    def test_count_by_camera_none_row(self) -> None:
        cam_id = uuid4()
        self.pool.mock_cursor.fetchone.return_value = None
        count = self.repo.count_by_camera(cam_id)
        assert count == 0
