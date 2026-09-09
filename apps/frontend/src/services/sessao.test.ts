/**
 * Régua da sessão por aba.
 *
 * O bug que estes testes travam: `token`/`user` viviam em `localStorage`, que
 * é UM por perfil de navegador e compartilhado por todas as abas. Duas
 * pessoas no mesmo computador tinham uma vaga só — o segundo login
 * sobrescrevia a chave do primeiro, e o reload do `login()` jogava a aba
 * antiga para dentro da sessão nova.
 *
 * jsdom tem UM `sessionStorage` por ambiente, então não dá para instanciar
 * duas abas de verdade. O que estes testes provam é a propriedade que a
 * isolação por aba exige e que a regressão quebraria primeiro: o pacote de
 * sessão não pode encostar em `localStorage`. Reverter qualquer chave para
 * `localStorage` faz este arquivo falhar.
 *
 * Storage real é pouco confiável neste ambiente de teste (mesmo motivo
 * documentado em test/components/tenantContextExpiry.test.ts) — substituído
 * por um Storage in-memory via vi.stubGlobal.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

class MemoryStorage implements Storage {
  private store = new Map<string, string>()
  get length(): number { return this.store.size }
  clear(): void { this.store.clear() }
  getItem(key: string): string | null { return this.store.has(key) ? this.store.get(key)! : null }
  key(index: number): string | null { return Array.from(this.store.keys())[index] ?? null }
  removeItem(key: string): void { this.store.delete(key) }
  setItem(key: string, value: string): void { this.store.set(key, String(value)) }
}

const CHAVES_DE_SESSAO = [
  'token',
  'user',
  'impersonation',
  'impersonation_backup',
  'tenant_context',
  'tenant_context_backup',
]

/** Recarrega o módulo — equivale a "uma aba nova abriu o app". */
async function abrirAba() {
  vi.resetModules()
  return import('./sessao')
}

beforeEach(() => {
  vi.stubGlobal('localStorage', new MemoryStorage())
  vi.stubGlobal('sessionStorage', new MemoryStorage())
})

describe('sessao — o pacote de sessão fica na aba, não no navegador', () => {
  it('grava em sessionStorage e NUNCA em localStorage', async () => {
    const { gravarSessao } = await abrirAba()

    gravarSessao('token', 'jwt-da-aba')

    expect(sessionStorage.getItem('token')).toBe('jwt-da-aba')
    // A asserção que mata a regressão: se voltar para localStorage, falha.
    expect(localStorage.getItem('token')).toBeNull()
  })

  it('lê da aba e ignora o que outra aba deixou no navegador', async () => {
    const { gravarSessao, lerSessao } = await abrirAba()
    gravarSessao('token', 'jwt-desta-aba')

    // Depois da migração de abertura, nada mais entra por localStorage.
    localStorage.setItem('token', 'jwt-de-outra-aba')

    expect(lerSessao('token')).toBe('jwt-desta-aba')
  })

  it('apagar a sessão da aba não mexe na preferência do navegador', async () => {
    const { apagarSessao, gravarSessao, lerSessao } = await abrirAba()
    localStorage.setItem('tema', 'escuro')
    gravarSessao('token', 'jwt')

    apagarSessao('token')

    expect(lerSessao('token')).toBeNull()
    // Preferência de viewer é compartilhada entre abas de propósito.
    expect(localStorage.getItem('tema')).toBe('escuro')
  })

  it('sessão ausente lê null em vez de estourar', async () => {
    const { lerSessao } = await abrirAba()
    expect(lerSessao('token')).toBeNull()
  })

  it('armazenamento bloqueado não derruba o app', async () => {
    const explode = {
      getItem() { throw new Error('SecurityError') },
      setItem() { throw new Error('SecurityError') },
      removeItem() { throw new Error('SecurityError') },
    }
    vi.stubGlobal('sessionStorage', explode)
    vi.stubGlobal('localStorage', explode)

    const { apagarSessao, gravarSessao, lerSessao } = await abrirAba()

    expect(lerSessao('token')).toBeNull()
    expect(() => gravarSessao('token', 'x')).not.toThrow()
    expect(() => apagarSessao('token')).not.toThrow()
  })
})

describe('sessao — migração da sessão legada (o deploy não desloga ninguém)', () => {
  it('adota a sessão que ficou em localStorage e apaga a cópia compartilhada', async () => {
    for (const chave of CHAVES_DE_SESSAO) {
      localStorage.setItem(chave, `legado-${chave}`)
    }

    const { lerSessao } = await abrirAba()

    for (const chave of CHAVES_DE_SESSAO) {
      expect(lerSessao(chave)).toBe(`legado-${chave}`)
      // Some do slot compartilhado: a PRÓXIMA aba nasce limpa, que é o
      // comportamento definitivo numa máquina compartilhada.
      expect(localStorage.getItem(chave)).toBeNull()
    }
  })

  it('não migra preferência de viewer — só o pacote de sessão', async () => {
    localStorage.setItem('tema', 'escuro')
    localStorage.setItem('token', 'legado')

    await abrirAba()

    expect(localStorage.getItem('tema')).toBe('escuro')
    expect(sessionStorage.getItem('tema')).toBeNull()
  })

  it('não sobrescreve a sessão que a aba já tem', async () => {
    sessionStorage.setItem('token', 'jwt-desta-aba')
    localStorage.setItem('token', 'legado-de-outra-pessoa')

    const { lerSessao } = await abrirAba()

    expect(lerSessao('token')).toBe('jwt-desta-aba')
  })
})
