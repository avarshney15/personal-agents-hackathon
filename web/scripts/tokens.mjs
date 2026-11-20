// Generates src/tokens.css from design/tokens.json (the Event Agent design system).
// Run with `npm run tokens` whenever design/tokens.json changes.
import { readFileSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const src = resolve(here, '../../design/tokens.json')
const out = resolve(here, '../src/tokens.css')
const t = JSON.parse(readFileSync(src, 'utf8'))

const colors = Object.fromEntries(t.color.tokens.map((c) => [c.name, c.value]))
const resolveRef = (value, theme) => {
  const m = /^\{(.+)\}$/.exec(value)
  if (!m) return value
  return resolveRef(colors[m[1]][theme], theme)
}
const themeVars = (theme) => {
  const lines = t.color.tokens.map((c) => `  --${c.name}: ${resolveRef(c.value[theme], theme)};`)
  for (const s of t.shadow.tokens) lines.push(`  --${s.name}: ${s.value[theme]};`)
  return lines.join('\n')
}

const fixed = []
for (const [name, value] of Object.entries(t.type.families)) fixed.push(`  --font-${name}: ${value};`)
for (const s of t.spacing.tokens) fixed.push(`  --${s.name}: ${s.value};`)
for (const r of t.radius.tokens) fixed.push(`  --${r.name}: ${r.value};`)

const typeClasses = []
for (const group of t.type.groups) {
  for (const s of group.styles) {
    const family = s.family ?? group.family
    const rules = [
      `font-family: var(--font-${family});`,
      `font-size: ${s.fontSize};`,
      `line-height: ${s.lineHeight};`,
      `font-weight: ${s.fontWeight};`,
    ]
    if (s.letterSpacing) rules.push(`letter-spacing: ${s.letterSpacing};`)
    typeClasses.push(`/* ${s.usage} */\n.type-${s.name} { ${rules.join(' ')} }`)
  }
}

const css = `/* Generated from design/tokens.json by scripts/tokens.mjs. Do not edit by hand. */

:root {
  color-scheme: light dark;
${themeVars('light')}
${fixed.join('\n')}
}

@media (prefers-color-scheme: dark) {
  :root {
${themeVars('dark').replace(/^/gm, '  ')}
  }
}

${typeClasses.join('\n')}
`
writeFileSync(out, css)
console.log(`Wrote ${out}`)
