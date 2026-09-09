-- Migration 138 — exclusão de câmera que não apaga o histórico
--
-- ═══ O PROBLEMA ═══
--
-- Só existia "Arquivar". A câmera arquivada continuava aparecendo no Ao Vivo
-- (ladrilho morto "Câmera arquivada"), no grid e nos seletores — o dono não
-- tinha como tirar do sistema uma câmera que não é mais dele. "Ela fica pra
-- sempre."
--
-- ═══ POR QUE NÃO O DELETE FÍSICO ═══
--
-- `DELETE FROM public.cameras` não é uma opção:
--
--   · alerts, camera_events, counting_sessions, demo_videos e operations
--     referenciam a câmera com ON DELETE CASCADE — apagar a câmera apaga
--     junto, em silêncio, os alertas e as evidências gravadas. Isso é
--     registro histórico e pode ter valor legal.
--   · training_frames e model_deployments são NO ACTION — a operação trava
--     por FK assim que a câmera tem um frame de treino. Ou seja: para as
--     câmeras que mais importam, o DELETE nem completa.
--
-- Então a exclusão é LÓGICA: `deleted_at` marca a câmera como fora do
-- sistema. Ela some de todas as telas e do config do edge; alerta, evidência
-- e frame anotado continuam onde estão.
--
-- `deleted_at` NÃO é sinônimo de `is_active = false`. `is_active` está
-- sobrecarregado (arquivada E rascunho de import em lote) e é reversível pela
-- tela; `deleted_at` é o estado final, sem botão de volta na UI — quem quiser
-- a câmera de novo cadastra de novo.
--
-- Forward-only e idempotente (C-04 / regra de migrations): só ADD COLUMN
-- IF NOT EXISTS e CREATE INDEX IF NOT EXISTS. Nenhum DROP, nenhum DELETE,
-- nenhum ALTER COLUMN TYPE. Rodar duas vezes é no-op.

ALTER TABLE public.cameras
    ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;

-- Todo SELECT de "quais câmeras existem" passa a carregar `deleted_at IS NULL`
-- junto do tenant_id. Índice parcial: só as vivas entram, que são as que as
-- telas leem.
CREATE INDEX IF NOT EXISTS idx_cameras_tenant_vivas
    ON public.cameras (tenant_id)
    WHERE deleted_at IS NULL;

COMMENT ON COLUMN public.cameras.deleted_at IS 'Exclusao logica: preenchido = camera fora do sistema (some de todas as telas e do config do edge). Alertas, evidencias e frames de treino dela permanecem. NULL = camera viva.';
