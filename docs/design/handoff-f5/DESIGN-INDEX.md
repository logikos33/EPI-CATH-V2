# DESIGN-INDEX — qual prancha rege qual tela

**Levantado em:** 2026-08-30 · **Contra:** `origin/develop` `3956182` · **Método:** rotas lidas de
`apps/frontend/src/app/RotasNovas.tsx`, componentes abertos um a um. ⛔ Estado **medido**, não chutado.

> 🔴 **Este arquivo é a resposta para "qual desenho manda nesta tela?".** Se a sua tela não está
> aqui, ⛔ **não desenhe em código** — ver `CLAUDE.md § Design`.

## Como ler o estado

| estado | significa |
|---|---|
| **IMPLEMENTADA** | existe prancha **e** rota, e a tela foi construída contra ela |
| **PARCIAL** | existe prancha e rota, mas parte da prancha ⛔ não chegou ao código |
| **PENDENTE** | existe **prancha** e ⛔ **não existe rota/tela** — o desenho está esperando |
| **SEM-DESENHO** | existe **tela** e ⛔ **não existe prancha** — 🔴 o pior caso: código sem origem |

⚠️ **PENDENTE e SEM-DESENHO são coisas opostas.** PENDENTE é trabalho de front por fazer;
SEM-DESENHO é dívida de design, e é o caso que a regra nova existe para não deixar crescer.

## Shell e identidade

| prancha | rege | rota | estado |
|---|---|---|---|
| `Shell Logikos Vision.dc.html` | shell inteiro (topbar 56px, sidebar 236/64px, banner admin) | `/novo/*` | **IMPLEMENTADA** |
| `EPI Sidebar.dc.html` | navegação lateral | `/novo/*` | **IMPLEMENTADA** |
| `EPI Topbar.dc.html` | barra superior, ⌘K | `/novo/*` | **IMPLEMENTADA** |
| `Logikos Loading.dc.html` | loader (franja magenta) | global | **IMPLEMENTADA** |
| `Contrato de Migração.dc.html` | regra de coexistência antigo × `/novo` | — | referência, ⛔ não é tela |

## EPI

| prancha | rota | componente | estado |
|---|---|---|---|
| `EPI Dashboard.dc.html` | `/novo/epi/dashboard` | `Dashboard.tsx` (1435) | **IMPLEMENTADA** |
| `EPI Ao Vivo.dc.html` | `/novo/epi/live` | `AoVivo.tsx` (1135) | **IMPLEMENTADA** |
| `EPI Eventos.dc.html` | `/novo/epi/eventos` | `Eventos.tsx` (936) | **IMPLEMENTADA** |
| `EPI Evento Detalhe.dc.html` | `/novo/epi/eventos/:id` | `EventoDetalhe.tsx` (1040) | **IMPLEMENTADA** |
| `EPI Verificação.dc.html` | `/novo/epi/verificacao` | `Verificacao.tsx` (1389) | **IMPLEMENTADA** |
| `EPI Ações.dc.html` | `/novo/epi/acoes` | `Acoes.tsx` (649) | **IMPLEMENTADA** |
| `EPI Câmeras.dc.html` | `/novo/epi/cameras` | `Cameras.tsx` (1398) | **IMPLEMENTADA** |
| `EPI Relatórios.dc.html` | `/novo/epi/relatorios` | `Relatorios.tsx` (546) | **IMPLEMENTADA** |
| `Câmera Cenário.dc.html` | `/novo/epi/cameras/:id/cenario` | `Cenario.tsx` (1067) | **IMPLEMENTADA** |
| `Cenário e Operações.dc.html` | `/novo/epi/cameras/:id/operations` | `Operacoes.tsx` (284) | ⚠️ **PARCIAL** — ver nota 1 |

## Qualidade

| prancha | rota | componente | estado |
|---|---|---|---|
| `Qualidade.dc.html` | `/novo/quality` | `Qualidade.tsx` (786) | **IMPLEMENTADA** |
| `Gestão Qualidade.dc.html` | `/novo/quality/gestao` | `GestaoQualidade.tsx` (1191) | **IMPLEMENTADA** |
| `Revisão Qualidade.dc.html` | `/novo/quality/revisao` | `RevisaoQualidade.tsx` (750) | **IMPLEMENTADA** |
| `Configuração Qualidade.dc.html` | `/novo/quality/configuracao` | `ConfigQualidade.tsx` (493) | **IMPLEMENTADA** |
| `Kiosk RVB.dc.html` | `/novo/tablet/:station` | `kiosk/Kiosk.tsx` (368) | **IMPLEMENTADA** |

## Carga

| prancha | rota | componente | estado |
|---|---|---|---|
| `Carga.dc.html` | `/novo/carga` | `Carga.tsx` (978) | **IMPLEMENTADA** |

## Estúdio (persona técnica, gateado)

| prancha | rota | componente | estado |
|---|---|---|---|
| `Estúdio.dc.html` | `/novo/estudio` + abas | `Estudio.tsx` (162) | **IMPLEMENTADA** |
| `Estúdio.dc.html` § Dados | `/novo/estudio/dados` | `Dados.tsx` (190) | **IMPLEMENTADA** |
| `Estúdio.dc.html` § Cobertura | `/novo/estudio/cobertura` | `Cobertura.tsx` (44) | **IMPLEMENTADA** — nota 2 |
| `Estúdio.dc.html` § Classificar | `/novo/estudio/classificar` | `Classificar.tsx` (74) | **IMPLEMENTADA** — nota 2 |
| `Estúdio.dc.html` § Classes | `/novo/estudio/classes` | `Classes.tsx` (557) | **IMPLEMENTADA** |
| `Catálogo de Modelos.dc.html` | `/novo/estudio/modelo` | `Modelo.tsx` (323) | **IMPLEMENTADA** |
| `Modelos por Câmera.dc.html` | `/novo/estudio/modelos-por-camera` | `ModelosPorCamera.tsx` (50) | **IMPLEMENTADA** — nota 2 |
| `Estúdio.dc.html` § Treino | `/novo/estudio/treino` | `Treino.tsx` (471) | **IMPLEMENTADA** |
| `Fila de Propostas.dc.html` | ⛔ **sem rota** | — | 🟡 **PENDENTE** |
| `Estúdio v1 (arquivado).dc.html` | — | — | arquivada, ⛔ não implementar |

## Administração

| prancha | rota | componente | estado |
|---|---|---|---|
| `Admin Plataforma.dc.html` | `/novo/admin` | `Admin.tsx` + `VisaoGeral.tsx` (164) | **IMPLEMENTADA** |
| `Arquitetura de Administração.dc.html` | `/novo/admin/tenants` · `/tenants/:id` | `Tenants` (325) · `TenantDetalhe` (369) | **IMPLEMENTADA** |
| `Arquitetura de Administração.dc.html` § usuários | `/novo/admin/usuarios` | `Usuarios.tsx` (406) | **IMPLEMENTADA** |
| `Arquitetura de Administração — Conexões.dc.html` | `/novo/admin/dispositivos` | `Dispositivos.tsx` (111) | ⚠️ **PARCIAL** — ver nota 3 |
| `Arquitetura de Administração.dc.html` § auditoria | `/novo/admin/auditoria` | `Auditoria.tsx` (192) | **IMPLEMENTADA** |
| `Saúde da Operação.dc.html` | ⛔ **sem rota** | — | 🟡 **PENDENTE** |

## Acesso

| prancha | rota | componente | estado |
|---|---|---|---|
| `Acesso Logikos.dc.html` | `/entrar` · `/esqueci-senha` · `/redefinir-senha` | `Entrar` (256) · `EsqueciSenha` (101) · `RedefinirSenha` (145) | **IMPLEMENTADA** |

## 🔴 Mobile e TV — desenhado e SEM rota

| prancha | estado |
|---|---|
| `Mobile EPI.dc.html` | 🟡 **PENDENTE** — ⛔ nenhuma rota mobile no front novo |
| `Mobile Fluxos.dc.html` | 🟡 **PENDENTE** |
| `Mobile Sistema.dc.html` | 🟡 **PENDENTE** |
| `TV RVB.dc.html` | 🟡 **PENDENTE** — ⛔ nenhuma rota `/tv` no front novo |

⚠️ **Quatro pranchas prontas sem uma linha de código.** É o maior bloco de desenho parado do bundle,
e ⛔ não estava escrito em lugar nenhum antes deste índice.

## Pranchas de processo (⛔ não são telas)

`Handoff LOTE 1/2/3.dc.html` — pacotes de aprovação de 04/08/2026. Servem de contexto e critério de
aceite; ⛔ não têm rota.

## Notas do levantamento

1. **`Operacoes.tsx` (284 linhas) contra `Cenário e Operações.dc.html`** — a prancha cobre cenário
   **e** operações; o cenário virou tela própria (`Cenario.tsx`, 1067). Marcado PARCIAL porque
   ⛔ não confirmei estado a estado que a metade "operações" está completa. **Quem tocar nessa tela
   fecha esta pendência** abrindo a prancha e comparando.
2. **Arquivos curtos que NÃO são stubs.** `Cobertura` (44), `Classificar` (74), `ModelosPorCamera`
   (50) são **embrulhos de núcleo compartilhado** (`CoverageMatrix`, `CameraModelScope`), importados
   como estão e nunca editados dali. ⚠️ Contagem de linhas ⛔ não mede completude — foi por isso que
   abri cada um antes de classificar.
3. **`Dispositivos.tsx` (111) contra `Arquitetura de Administração — Conexões.dc.html`** — a prancha
   de conexões é densa e o componente é curto para o que ela mostra. PARCIAL até alguém comparar.

## ⛔ O que este índice NÃO afirma

- **Fidelidade visual.** IMPLEMENTADA quer dizer *"existe prancha, existe rota, foi construída contra
  ela"* — ⛔ **não** quer dizer que bate pixel a pixel hoje. Isso é trabalho do cético de fidelidade
  em cada PR (`CLAUDE.md § Design`).
- **Nada sobre o front ANTIGO.** Este índice cobre o front novo (`/novo/*`) e o acesso. O antigo
  segue no ar e ⛔ não é regido por estas pranchas.
- Os dois PARCIAL saíram de leitura de tamanho e escopo, ⛔ **não** de comparação tela × prancha.

## Manutenção

Este arquivo entra no **mesmo commit** que a mudança que o afeta:

- tela nova implementada → linha vira **IMPLEMENTADA**
- prancha nova salva no bundle → linha nova, **PENDENTE**
- tela criada sem prancha → 🔴 **SEM-DESENHO**, e isso é reprovável em review
