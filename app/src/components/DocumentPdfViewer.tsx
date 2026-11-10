import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { ChevronLeft, ChevronRight, Minus, Plus } from 'lucide-react'
import { getDocument, GlobalWorkerOptions, TextLayer, type PDFDocumentProxy, type RenderTask } from 'pdfjs-dist'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Spinner } from '@/components/ui/spinner'
import './document-viewer.css'

// Avoid reusing the worker response cached with the previous incorrect MIME.
GlobalWorkerOptions.workerSrc = `${workerUrl}?module=1`
const assets = '/pdfjs/6.4.299/'

type PageSize = { width: number; height: number }

const pageScale = (base: PageSize, width: number, zoom: number) =>
  Math.min(1200 / base.width, 1800 / base.height, Math.min(width, 840) / base.width) * zoom

type PageProps = {
  pdf: PDFDocumentProxy
  number: number
  total: number
  filename: string
  base: PageSize
  scale: number
  root: HTMLDivElement | null
  register: (number: number, element: HTMLDivElement | null) => void
  onRendered: () => void
  onFailed: () => void
}

/** A page draws only near the visible area, so scans of 100+ pages stay light. */
function PdfPage({ pdf, number, total, filename, base, scale, root, register, onRendered, onFailed }: PageProps) {
  const holder = useRef<HTMLDivElement>(null)
  const canvas = useRef<HTMLCanvasElement>(null)
  const text = useRef<HTMLDivElement>(null)
  const rendering = useRef<RenderTask | null>(null)
  const [near, setNear] = useState(false)
  useEffect(() => {
    register(number, holder.current)
    return () => register(number, null)
  }, [number, register])
  useEffect(() => {
    if (!holder.current || !root) return
    const observer = new IntersectionObserver(entries => setNear(entries[0].isIntersecting), { root, rootMargin: '1200px 0px' })
    observer.observe(holder.current)
    return () => observer.disconnect()
  }, [root])
  useEffect(() => {
    if (!near || !canvas.current || !text.current) return
    let cancelled = false
    let layer: TextLayer | undefined
    let task: RenderTask | undefined
    const drawing = canvas.current
    const textContainer = text.current
    async function render() {
      // Wait for cancellation before reusing a canvas on fast zoom changes.
      await rendering.current?.promise.catch(() => undefined)
      const source = await pdf.getPage(number)
      if (cancelled) return
      const viewport = source.getViewport({ scale })
      const ratio = Math.min(window.devicePixelRatio || 1, 2)
      drawing.width = Math.round(viewport.width * ratio)
      drawing.height = Math.round(viewport.height * ratio)
      textContainer.replaceChildren()
      textContainer.style.setProperty('--total-scale-factor', String(scale))
      task = source.render({ canvas: drawing, viewport, transform: [ratio, 0, 0, ratio, 0, 0] })
      rendering.current = task
      await task.promise
      if (cancelled) return
      onRendered()
      layer = new TextLayer({ container: textContainer, viewport, textContentSource: source.streamTextContent() })
      await layer.render()
    }
    void render().catch(cause => { if (!cancelled && cause?.name !== 'RenderingCancelledException') onFailed() })
    return () => {
      cancelled = true
      task?.cancel()
      layer?.cancel()
      // Far from view: give the bitmap memory back.
      drawing.width = 0
      drawing.height = 0
      textContainer.replaceChildren()
    }
  }, [near, pdf, number, scale, onRendered, onFailed])
  const size = { width: base.width * scale, height: base.height * scale }
  return <div ref={holder} data-page={number} className="relative mx-auto bg-white shadow-md" style={size}>
    <canvas ref={canvas} role="img" aria-label={`Страница ${number} из ${total}: ${filename}`} style={{ ...size, display: 'block' }} />
    <div ref={text} className="document-text-layer" />
  </div>
}

export default function DocumentPdfViewer({ blob, filename, expanded = false }: { blob: Blob; filename: string; expanded?: boolean }) {
  const [pdf, setPdf] = useState<PDFDocumentProxy>()
  const [sizes, setSizes] = useState<PageSize[]>([])
  const [page, setPage] = useState(1)
  const [zoom, setZoom] = useState(1)
  const [busy, setBusy] = useState(true)
  const [error, setError] = useState('')
  const [width, setWidth] = useState(800)
  const [root, setRoot] = useState<HTMLDivElement | null>(null)
  const host = useRef<HTMLDivElement | null>(null)
  const attach = useCallback((element: HTMLDivElement | null) => { host.current = element; setRoot(element) }, [])
  const elements = useRef(new Map<number, HTMLDivElement>())
  const anchor = useRef({ page: 1, offset: 0 })
  const register = useCallback((number: number, element: HTMLDivElement | null) => {
    if (element) elements.current.set(number, element)
    else elements.current.delete(number)
  }, [])
  const rendered = useCallback(() => setBusy(false), [])
  const failed = useCallback(() => { setBusy(false); setError('Страница не отображается. Откройте исходный файл.') }, [])
  useEffect(() => {
    if (!root) return
    const observer = new ResizeObserver(entries => setWidth(Math.max(240, entries[0].contentRect.width)))
    observer.observe(root)
    return () => observer.disconnect()
  }, [root])
  useEffect(() => {
    let cancelled = false
    let task: ReturnType<typeof getDocument> | undefined
    void blob.arrayBuffer().then(data => {
      if (cancelled) return
      setPdf(undefined); setSizes([]); setPage(1); setBusy(true); setError('')
      anchor.current = { page: 1, offset: 0 }
      task = getDocument({ data: new Uint8Array(data),
        cMapUrl: assets + 'cmaps/', cMapPacked: true, standardFontDataUrl: assets + 'standard_fonts/',
        wasmUrl: assets + 'wasm/', useSystemFonts: true })
      return task.promise.then(async document => {
        const pages = await Promise.all(Array.from({ length: document.numPages }, (_, index) => document.getPage(index + 1)))
        const measured = pages.map(item => { const { width: w, height: h } = item.getViewport({ scale: 1 }); return { width: w, height: h } })
        if (cancelled) return
        setPdf(document); setSizes(measured)
      })
    }).catch(() => { if (!cancelled) { setBusy(false); setError('Не удалось показать страницы. Скачайте оригинал для чтения в исходном приложении.') } })
    return () => { cancelled = true; void task?.destroy() }
  }, [blob])
  // Zoom and resizing change page heights: keep the reader on the same place of the same page.
  useLayoutEffect(() => {
    const element = elements.current.get(anchor.current.page)
    if (host.current && element) host.current.scrollTop = element.offsetTop + anchor.current.offset * element.offsetHeight
  }, [width, zoom])
  const pages = sizes.length
  function follow() {
    const scroller = host.current
    if (!scroller) return
    const line = scroller.scrollTop + scroller.clientHeight * 0.4
    let current = 1
    for (let number = 1; number <= pages; number += 1) {
      const element = elements.current.get(number)
      if (!element || element.offsetTop > line) break
      current = number
    }
    const element = elements.current.get(current)
    if (element) anchor.current = { page: current, offset: (scroller.scrollTop - element.offsetTop) / element.offsetHeight }
    setPage(current)
  }
  function choose(value: number) {
    if (!Number.isInteger(value) || value < 1 || value > pages || !host.current) return
    const element = elements.current.get(value)
    setPage(value)
    if (!element) return
    anchor.current = { page: value, offset: 0 }
    host.current.scrollTop = element.offsetTop - 16
  }
  return <div aria-label={`Страницы документа ${filename}`} className={expanded ? 'flex min-h-0 min-w-0 flex-1 flex-col' : 'min-w-0'}>
    <div className="flex items-center justify-between gap-4 border-b bg-card px-4 py-2.5">
      <div className="flex items-center gap-2"><Button size="icon" variant="ghost" disabled={!pages || page === 1} aria-label="Предыдущая страница" onClick={() => choose(page - 1)}><ChevronLeft className="size-4" /></Button><Input type="number" min={1} max={pages || 1} aria-label="Номер страницы" className="h-8 w-16 text-center" value={page} disabled={!pages} onChange={event => choose(Number(event.target.value))} /><span aria-live="polite" className="whitespace-nowrap text-xs text-muted-foreground">из {pages || '…'}</span><Button size="icon" variant="ghost" disabled={!pages || page >= pages} aria-label="Следующая страница" onClick={() => choose(page + 1)}><ChevronRight className="size-4" /></Button></div>
      <div className="flex items-center gap-2"><Button size="icon" variant="ghost" disabled={zoom <= 0.75} aria-label="Уменьшить масштаб документа" onClick={() => setZoom(value => Math.max(0.75, value - 0.25))}><Minus className="size-4" /></Button><button type="button" className="w-24 rounded-md px-2 py-1 text-xs text-muted-foreground hover:bg-muted/40" onClick={() => setZoom(1)} aria-label="Вписать страницу по ширине">{zoom === 1 ? 'По ширине' : `${Math.round(zoom * 100)}%`}</button><Button size="icon" variant="ghost" disabled={zoom >= 1.5} aria-label="Увеличить масштаб документа" onClick={() => setZoom(value => Math.min(1.5, value + 0.25))}><Plus className="size-4" /></Button></div>
    </div>
    <div ref={attach} onScroll={follow} className={`relative overflow-auto bg-[#e5e7eb] p-8 ${expanded ? 'min-h-0 flex-1' : 'h-[72vh] min-h-[560px]'}`}>
      {error && <p role="alert" className="mb-4 rounded-md bg-white p-4 text-sm text-slate-800">{error}</p>}
      {busy && <div role="status" className="sticky left-1/2 top-0 z-10 mx-auto mb-4 flex w-fit items-center gap-2 rounded-md border bg-white px-3 py-2 text-xs text-slate-700 shadow-sm"><Spinner className="size-3.5" />Открываем документ…</div>}
      {pdf && <div className="flex w-fit min-w-full flex-col gap-4">
        {sizes.map((base, index) => <PdfPage key={index + 1} pdf={pdf} number={index + 1} total={pages} filename={filename} base={base} scale={pageScale(base, width, zoom)} root={root} register={register} onRendered={rendered} onFailed={failed} />)}
      </div>}
    </div>
  </div>
}
