// Check the deployed server, including lazy PDF.js assets, rather than Vite.
const base = new URL(process.argv[2] || 'http://127.0.0.1:8080/')
const pending = [new URL('/', base).href]
const checked = new Set()
const modules = new Set(['application/javascript', 'text/javascript'])
let workers = 0
while (pending.length) {
  const address = pending.shift()
  if (checked.has(address)) continue
  checked.add(address)
  const url = new URL(address)
  if (url.origin !== base.origin) throw new Error('Asset points outside the platform')
  const response = await fetch(url, { redirect: 'manual', cache: 'no-store' })
  if (response.status !== 200) throw new Error(`${url.pathname}: HTTP ${response.status}`)
  const type = (response.headers.get('content-type') || '').split(';')[0].trim()
  if (/\.m?js$/.test(url.pathname) && !modules.has(type)) {
    throw new Error(`${url.pathname}: expected JavaScript, received ${type}`)
  }
  if (url.pathname.endsWith('.css') && type !== 'text/css') throw new Error(`${url.pathname}: ${type}`)
  const body = await response.text()
  for (const match of body.matchAll(/["'`](\/?assets\/[A-Za-z0-9_.-]+\.(?:m?js|css))["'`]/g)) {
    pending.push(new URL('/' + match[1].replace(/^\//, ''), base).href)
  }
  if (/\/pdf\.worker[^/]*\.mjs$/.test(url.pathname)) {
    workers += 1
    if (!url.search) pending.push(new URL(url.pathname + '?module=1', base).href)
  }
}
if (!workers) throw new Error('The deployed PDF worker was not found')
console.log(`Checked ${checked.size} deployed assets; PDF worker MIME is correct.`)
