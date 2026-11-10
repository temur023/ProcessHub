import { cp, mkdir } from 'node:fs/promises'
const source = new URL('../node_modules/pdfjs-dist/', import.meta.url)
const target = new URL('../public/pdfjs/6.4.299/', import.meta.url)
await mkdir(target, { recursive: true })
for (const name of ['cmaps', 'standard_fonts', 'wasm']) {
  await cp(new URL(name, source), new URL(name, target), { recursive: true })
}
