/**
 * Usuários — `/novo/admin/usuarios`. Desenho: `Admin Plataforma.dc.html`,
 * seção "Usuários". Paridade funcional: `AdminUsersPage.tsx` +
 * `CreateUserWizard.tsx` (lidos função a função), reimplementados no
 * vocabulário lk.
 *
 * DIVERGÊNCIAS registradas (C-04 — o código vence a prancha/a missão):
 *
 *  1. **"LINK COPIÁVEL" de convite**: `POST /v1/admin/users` devolve
 *     `first_access_token` (`routes.py:885-899`), mas **não existe rota
 *     nenhuma no backend que consuma essa chave** (`grep "first_access:"` no
 *     backend só acha o `setex` que a grava — nunca um `get`). Não é um link
 *     funcional, é infraestrutura morta. O caminho real e testável é o mesmo
 *     do reset de senha: `temp_password` exibida UMA VEZ com botão copiar —
 *     é o que `AdminUsersPage.tsx` já faz e o que de fato loga o usuário.
 *  2. **Wizard de 3 passos** (`CreateUserWizard`) virou um formulário de UM
 *     passo aqui (email, papel, tenant) — a etapa "Acesso" dele só adiciona
 *     role customizada opcional, que a missão desta PR não pede; e a etapa
 *     "Credenciais" é a mesma tela final de senha-uma-vez do item 1.
 *  3. **Overrides de permissão / sessões do usuário**: `UserPermissionsDrawer`
 *     (333 linhas: matriz de permissões + revogar sessões) não foi recriado
 *     nesta PR. O link desta linha ("Ver tenant →") já foi corrigido para
 *     `/novo/admin/tenants/<tenant_id>` (C1 — antes vazava pro front antigo
 *     via `href` absoluto, driblando a régua de coexistência que só olhava
 *     `to=`/`navigate()`) e mostra dados reais do tenant (módulos, limites,
 *     marca) em `TenantDetalhe.tsx` — mas esse drawer específico (matriz de
 *     permissão por usuário + revogar sessão) **não tem equivalente no front
 *     novo ainda**. Pendência nomeada, não fingida: não é "Permissões
 *     avançadas" até esse drawer existir aqui.
 */
import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Check, Copy, KeyRound } from 'lucide-react'

import { adminService } from '../../modules/admin/services/adminService'
import type { AdminUser, UserRole } from '../../modules/admin/types/admin'
import { EmptyState } from '../../components/ui/EmptyState/EmptyState'
import { useToast } from '../../components/ui/Toast/useToast'
import { LogikosLoader } from '../shell/LogikosLoader'
import { lk } from '../tokens/lk.css'
import { rotaNova } from '../RotasNovas'
import {
  PAPEIS_ATRIBUIVEIS, PAPEL_LABEL, ROTULO_SEM_PAPEL, SEM_PAPEL,
} from '../../modules/admin/papeis'
import * as s from './Usuarios.css'

const PAGE_SIZE = 20

const iniciais = (texto: string) => texto.trim().slice(0, 2).toUpperCase()

function ModalCredencial({
  titulo,
  aviso,
  email,
  senha,
  onClose,
}: {
  titulo: string
  aviso: string
  email: string
  senha: string
  onClose: () => void
}) {
  const toast = useToast()
  const [copiado, setCopiado] = useState(false)

  const copiar = async () => {
    try {
      await navigator.clipboard.writeText(senha)
      setCopiado(true)
      toast.success('Copiado', 'Senha temporária copiada.')
    } catch {
      toast.error('Não foi possível copiar', 'Copie manualmente o valor exibido.')
    }
  }

  return (
    <div className={s.overlay} role="dialog" aria-modal="true" aria-label={titulo}>
      <div className={s.modal}>
        <span className={s.modalTitulo}>{titulo}</span>
        <span className={s.aviso}>{aviso}</span>
        <div className={s.credenciais}>
          <div className={s.credenciaisLinha}>
            <span className={s.campoLabel}>Usuário</span>
            <span className={s.credenciaisCodigo}>{email}</span>
          </div>
          <div className={s.credenciaisLinha}>
            <span className={s.campoLabel}>Senha temp.</span>
            <span className={s.credenciaisCodigo}>{senha}</span>
            <button type="button" className={s.botaoSecundario} onClick={() => void copiar()}>
              {copiado ? <Check size={13} /> : <Copy size={13} />} {copiado ? 'Copiado' : 'Copiar'}
            </button>
          </div>
        </div>
        <div className={s.acoesModal}>
          <button type="button" className={s.botaoPrimario} onClick={onClose}>Fechar</button>
        </div>
      </div>
    </div>
  )
}

function ModalConvidar({
  tenants,
  onClose,
  onCriado,
}: {
  tenants: { id: string; name: string }[]
  onClose: () => void
  onCriado: (cred: { email: string; senha: string }) => void
}) {
  const [nome, setNome] = useState('')
  const [email, setEmail] = useState('')
  const [papel, setPapel] = useState<UserRole | typeof SEM_PAPEL>(SEM_PAPEL)
  const [tenantId, setTenantId] = useState('')
  const [salvando, setSalvando] = useState(false)
  const [erro, setErro] = useState<string | null>(null)

  const papelEscolhido = PAPEIS_ATRIBUIVEIS.find((p) => p.valor === papel) ?? null

  const criar = async () => {
    if (!papelEscolhido) return
    setSalvando(true)
    setErro(null)
    try {
      const res = await adminService.createUser({
        email: email.trim().toLowerCase(),
        name: nome.trim() || undefined,
        role: papel,
        tenant_id: tenantId,
      })
      onCriado({ email: res.user.email, senha: res.temp_password })
    } catch (e: unknown) {
      setErro(e instanceof Error ? e.message : 'Erro ao criar usuário')
    } finally {
      setSalvando(false)
    }
  }

  return (
    <div className={s.overlay} role="dialog" aria-modal="true" aria-label="Novo usuário">
      <div className={s.modal}>
        <span className={s.modalTitulo}>Novo usuário</span>

        <label className={s.campoLabel} htmlFor="us-nome">Nome</label>
        <input
          id="us-nome"
          type="text"
          className={s.campo}
          value={nome}
          onChange={(e) => setNome(e.target.value)}
          placeholder="Como a pessoa assina — ex.: Ana Ribeiro"
        />
        <span className={s.subtitulo}>
          É o que aparece ao validar um evento. Em branco, a tela cai no e-mail.
        </span>

        <label className={s.campoLabel} htmlFor="us-email">Email</label>
        <input id="us-email" type="email" className={s.campo} value={email} onChange={(e) => setEmail(e.target.value)} placeholder="nome@empresa.com" />

        <label className={s.campoLabel} htmlFor="us-papel">Papel</label>
        <select id="us-papel" className={s.campo} value={papel} onChange={(e) => setPapel(e.target.value as UserRole)}>
          <option value={SEM_PAPEL}>{ROTULO_SEM_PAPEL}</option>
          {PAPEIS_ATRIBUIVEIS.map((p) => <option key={p.valor} value={p.valor}>{p.rotulo}</option>)}
        </select>
        {papelEscolhido && (
          <>
            <span className={s.subtitulo}>{papelEscolhido.resumo}</span>
            <span className={s.aviso}>{papelEscolhido.alerta}</span>
          </>
        )}

        <label className={s.campoLabel} htmlFor="us-tenant">Tenant</label>
        <select id="us-tenant" className={s.campo} value={tenantId} onChange={(e) => setTenantId(e.target.value)}>
          <option value="">Selecione um tenant</option>
          {tenants.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
        </select>

        {erro && <span className={s.erro}>{erro}</span>}
        <div className={s.acoesModal}>
          <button type="button" className={s.botaoSecundario} onClick={onClose}>Cancelar</button>
          <button
            type="button"
            className={s.botaoPrimario}
            disabled={salvando || !email || !tenantId || !papelEscolhido}
            onClick={() => void criar()}
          >
            {salvando ? 'Criando...' : 'Criar usuário'}
          </button>
        </div>
      </div>
    </div>
  )
}

export function Usuarios() {
  const toast = useToast()
  const nav = useNavigate()
  const qc = useQueryClient()
  const [busca, setBusca] = useState('')
  const [tenantFiltro, setTenantFiltro] = useState('')
  const [pagina, setPagina] = useState(1)
  const [modalConvidar, setModalConvidar] = useState(false)
  const [credencial, setCredencial] = useState<{ titulo: string; aviso: string; email: string; senha: string } | null>(null)
  const [ocupado, setOcupado] = useState<string | null>(null)
  /** id do usuário cujo nome está em edição na linha (null = ninguém). */
  const [renomeando, setRenomeando] = useState<string | null>(null)

  useEffect(() => setPagina(1), [busca, tenantFiltro])

  const tenantsQ = useQuery({
    queryKey: ['admin', 'tenants'],
    queryFn: () => adminService.getTenants(),
    staleTime: 60_000,
  })
  const tenantsMap = useMemo(
    () => new Map((tenantsQ.data ?? []).map((t) => [t.id, t.name])),
    [tenantsQ.data],
  )

  const consulta = useQuery({
    queryKey: ['admin', 'users', { busca, tenantFiltro, pagina }],
    queryFn: () => adminService.getUsers({
      search: busca || undefined,
      tenant_id: tenantFiltro || undefined,
      page: pagina,
    }),
  })

  const recarregar = () => void qc.invalidateQueries({ queryKey: ['admin', 'users'] })

  const resetarSenha = async (u: AdminUser) => {
    if (!window.confirm(`Resetar a senha de ${u.email}? A senha atual deixa de funcionar imediatamente.`)) return
    setOcupado(u.id)
    try {
      const res = await adminService.resetPassword(u.id)
      setCredencial({
        titulo: 'Senha temporária gerada',
        aviso: `Repasse esta senha a ${res.email}. Ela é exibida uma única vez.`,
        email: res.email,
        senha: res.temp_password,
      })
    } catch (e: unknown) {
      toast.error('Erro ao resetar senha', e instanceof Error ? e.message : undefined)
    } finally {
      setOcupado(null)
    }
  }

  /** Renomear na linha. Existe porque os usuários criados antes do campo
   *  "Nome" nasceram com `email.split("@")[0]` gravado como nome — sem um
   *  caminho de edição eles ficariam presos nisso para sempre, e é esse nome
   *  que o operador lê ao validar um evento. */
  const salvarNome = async (u: AdminUser, valor: string) => {
    const novo = valor.trim()
    setRenomeando(null)
    if (novo === (u.name?.trim() ?? '')) return
    setOcupado(u.id)
    try {
      await adminService.updateUser(u.id, { name: novo })
      recarregar()
    } catch (e: unknown) {
      toast.error('Erro ao renomear usuário', e instanceof Error ? e.message : undefined)
    } finally {
      setOcupado(null)
    }
  }

  const alternarAtivo = async (u: AdminUser) => {
    const acao = u.is_active ? 'desativar' : 'reativar'
    if (!window.confirm(
      u.is_active
        ? `Desativar ${u.email}? O acesso é encerrado imediatamente.`
        : `Reativar ${u.email}? O acesso volta a funcionar imediatamente.`,
    )) return
    setOcupado(u.id)
    try {
      await (u.is_active ? adminService.deactivateUser(u.id) : adminService.reactivateUser(u.id))
      recarregar()
    } catch (e: unknown) {
      toast.error(`Erro ao ${acao} usuário`, e instanceof Error ? e.message : undefined)
    } finally {
      setOcupado(null)
    }
  }

  if (consulta.isPending) {
    return <LogikosLoader estado="waiting" variante="tile" rotulo="CARREGANDO USUÁRIOS" />
  }

  if (consulta.isError) {
    return (
      <div className={s.centro}>
        <AlertTriangle size={36} strokeWidth={1.5} color={lk.estado.nc} aria-hidden="true" />
        <span className={s.centroTitulo}>Não foi possível carregar os usuários</span>
        <span className={s.centroTecnico}>GET /v1/admin/users</span>
        <button type="button" className={s.botaoRetry} onClick={() => void consulta.refetch()}>
          Tentar novamente
        </button>
      </div>
    )
  }

  const { items, total } = consulta.data

  return (
    <div className={s.raiz}>
      <div className={s.cabecalho}>
        <h1 className={s.titulo}>Usuários</h1>
        <span className={s.subtitulo}>{total.toLocaleString('pt-BR')} usuários cadastrados</span>
        <div className={s.spacer} />
        <input
          className={s.busca}
          placeholder="Buscar por email..."
          value={busca}
          onChange={(e) => setBusca(e.target.value)}
          aria-label="Buscar usuário por email"
        />
        <select
          className={s.select}
          value={tenantFiltro}
          onChange={(e) => setTenantFiltro(e.target.value)}
          aria-label="Filtrar por tenant"
        >
          <option value="">Todos os tenants</option>
          {(tenantsQ.data ?? []).map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
        </select>
        <button type="button" className={s.botaoPrimario} onClick={() => setModalConvidar(true)}>
          Convidar usuário
        </button>
      </div>

      {items.length === 0 ? (
        <EmptyState
          title="Nenhum usuário encontrado"
          description={busca || tenantFiltro ? 'Ajuste a busca ou o filtro de tenant.' : 'Convide o primeiro usuário para este tenant.'}
        />
      ) : (
        <div className={s.tabelaWrap}>
          <table className={s.tabela}>
            <thead>
              <tr>
                <th className={s.th}>Nome</th>
                <th className={s.th}>Papel · Tenant</th>
                <th className={s.th}>E-mail</th>
                <th className={s.th}>Último acesso</th>
                <th className={s.th}></th>
              </tr>
            </thead>
            <tbody>
              {items.map((u) => {
                const nome = u.name?.trim() || u.email
                const tenantNome = u.tenant_name ?? (u.tenant_id ? tenantsMap.get(u.tenant_id) : undefined) ?? '—'
                return (
                  <tr key={u.id}>
                    <td className={s.td}>
                      <div className={s.pessoa}>
                        <span className={s.avatar}>{iniciais(nome)}</span>
                        {renomeando === u.id ? (
                          <input
                            className={s.nomeInput}
                            autoFocus
                            defaultValue={u.name?.trim() ?? ''}
                            aria-label={`Nome de ${u.email}`}
                            disabled={ocupado === u.id}
                            onBlur={(e) => void salvarNome(u, e.currentTarget.value)}
                            onKeyDown={(e) => {
                              if (e.key === 'Enter') e.currentTarget.blur()
                              if (e.key === 'Escape') setRenomeando(null)
                            }}
                          />
                        ) : (
                          <button
                            type="button"
                            className={s.nomeBotao}
                            onClick={() => setRenomeando(u.id)}
                            title="Clique para editar o nome"
                          >
                            {nome}
                          </button>
                        )}
                        <span className={u.is_active ? `${s.dot} ${s.dotOk}` : `${s.dot} ${s.dotNc}`} title={u.is_active ? 'Ativo' : 'Inativo'} />
                      </div>
                    </td>
                    <td className={s.td}>
                      <div className={s.papelTenant}>
                        <span className={s.papel}>{PAPEL_LABEL[u.role]}</span>
                        <span className={s.tenantNome} title={tenantNome}>{tenantNome.toUpperCase()}</span>
                      </div>
                    </td>
                    <td className={s.td}><span className={s.mono}>{u.email}</span></td>
                    <td className={s.td}><span className={s.mono}>{u.last_login_at ? new Date(u.last_login_at).toLocaleString('pt-BR') : '—'}</span></td>
                    <td className={s.td}>
                      <div className={s.acoes}>
                        <button
                          type="button"
                          className={s.botaoSecundario}
                          disabled={ocupado === u.id}
                          onClick={() => void resetarSenha(u)}
                        >
                          <KeyRound size={12} /> Resetar senha
                        </button>
                        <button
                          type="button"
                          className={s.botaoSecundario}
                          disabled={ocupado === u.id}
                          onClick={() => void alternarAtivo(u)}
                        >
                          {u.is_active ? 'Desativar' : 'Reativar'}
                        </button>
                        {u.tenant_id && (
                          <button
                            type="button"
                            className={s.linkPermissoes}
                            onClick={() => nav(rotaNova(`/admin/tenants/${u.tenant_id}`))}
                            title="Dados do tenant — módulos, limites e marca"
                          >
                            Ver tenant →
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {total > PAGE_SIZE && (
        <div className={s.paginacao}>
          <button type="button" className={s.botaoSecundario} disabled={pagina <= 1} onClick={() => setPagina((p) => p - 1)}>Anterior</button>
          <span className={s.subtitulo}>Página {pagina}</span>
          <button type="button" className={s.botaoSecundario} disabled={pagina * PAGE_SIZE >= total} onClick={() => setPagina((p) => p + 1)}>Próxima</button>
        </div>
      )}

      {modalConvidar && (
        <ModalConvidar
          tenants={tenantsQ.data ?? []}
          onClose={() => setModalConvidar(false)}
          onCriado={(cred) => {
            setModalConvidar(false)
            recarregar()
            setCredencial({
              titulo: 'Usuário criado',
              aviso: 'Sem provedor de e-mail configurado hoje — repasse esta senha manualmente. Ela é exibida uma única vez.',
              email: cred.email,
              senha: cred.senha,
            })
          }}
        />
      )}

      {credencial && (
        <ModalCredencial
          titulo={credencial.titulo}
          aviso={credencial.aviso}
          email={credencial.email}
          senha={credencial.senha}
          onClose={() => setCredencial(null)}
        />
      )}
    </div>
  )
}
