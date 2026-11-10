/**
 * Карта картинкой — для регламента и презентаций.
 *
 * Берётся тот же SVG, что нарисован на холсте, без слоёв редактора и
 * подсветки, с рамкой во всю карту. PNG рисуется из него в canvas: у браузера
 * есть предел размера холста, поэтому огромную карту масштаб уменьшает.
 */

/** Сторона и площадь PNG, которые браузеры ещё рисуют. */
const PNG_MAX_SIDE = 16384
const PNG_MAX_AREA = 100_000_000

export interface MapFrame {
  x: number
  y: number
  w: number
  h: number
}

function saveBlob(blob: Blob, fileName: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = fileName
  a.click()
  URL.revokeObjectURL(url)
}

/** Самостоятельный SVG карты: всё, что помечено `data-export-skip`, выброшено. */
function standaloneSvg(scene: SVGSVGElement, frame: MapFrame, background: string): string {
  const ns = 'http://www.w3.org/2000/svg'
  const svg = scene.cloneNode(true) as SVGSVGElement
  svg.querySelectorAll('[data-export-skip]').forEach((el) => el.remove())
  svg.removeAttribute('class')
  svg.setAttribute('xmlns', ns)
  svg.setAttribute('viewBox', `${frame.x} ${frame.y} ${frame.w} ${frame.h}`)
  svg.setAttribute('width', String(Math.ceil(frame.w)))
  svg.setAttribute('height', String(Math.ceil(frame.h)))
  const bg = document.createElementNS(ns, 'rect')
  bg.setAttribute('x', String(frame.x))
  bg.setAttribute('y', String(frame.y))
  bg.setAttribute('width', String(frame.w))
  bg.setAttribute('height', String(frame.h))
  bg.setAttribute('fill', background)
  svg.insertBefore(bg, svg.firstChild)
  return new XMLSerializer().serializeToString(svg)
}

export async function downloadMapImage(
  scene: SVGSVGElement,
  frame: MapFrame,
  background: string,
  format: 'svg' | 'png',
  baseName: string,
): Promise<void> {
  const text = standaloneSvg(scene, frame, background)
  const svgBlob = new Blob([text], { type: 'image/svg+xml;charset=utf-8' })
  if (format === 'svg') {
    saveBlob(svgBlob, `${baseName}.svg`)
    return
  }
  const scale = Math.min(2, PNG_MAX_SIDE / frame.w, PNG_MAX_SIDE / frame.h, Math.sqrt(PNG_MAX_AREA / (frame.w * frame.h)))
  const url = URL.createObjectURL(svgBlob)
  try {
    const img = new Image()
    img.src = url
    await img.decode()
    const canvas = document.createElement('canvas')
    canvas.width = Math.max(1, Math.floor(frame.w * scale))
    canvas.height = Math.max(1, Math.floor(frame.h * scale))
    const ctx = canvas.getContext('2d')
    if (!ctx) throw new Error('Браузер не дал нарисовать картинку')
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height)
    const png = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, 'image/png'))
    if (!png) throw new Error('Карта слишком большая для PNG — скачайте SVG')
    saveBlob(png, `${baseName}.png`)
  } finally {
    URL.revokeObjectURL(url)
  }
}
