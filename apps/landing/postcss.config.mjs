/**
 * Tailwind entra por PostCSS, não pela integração `@astrojs/tailwind`.
 *
 * Motivo: a integração parou no Astro 5 (peer `^3 || ^4 || ^5`) e o Astro
 * subiu para 7 na migração do #421 — que é o conserto do advisory CRÍTICO do
 * astro, não um bump cosmético. O caminho alternativo seria `@tailwindcss/vite`,
 * que arrasta Tailwind 3→4: config CSS-first e utilitários renomeados, ou seja,
 * risco de mudar a APARÊNCIA do site numa mudança que era de segurança.
 * PostCSS é suportado nativamente pelo Vite e mantém o Tailwind 3 intacto.
 */
export default {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
}
