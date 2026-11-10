import { useState } from 'react'
import type React from 'react'
import { Link2, Wrench } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { setEdgeName, updateNode } from '@/lib/map-edit'
import type { BusinessProcess, ProcessNode, ProcessValidation } from '@/types/process'

/** Подписи веток, которые аналитики банка ставят чаще всего. */
const BRANCH_LABELS = ['Ha', "Yo'q"]

/** Замечания, для которых связь рисуют на холсте: исправить их одним полем нельзя. */
const CONNECT_CODES = new Set(['no_incoming', 'no_outgoing', 'isolated_node', 'off_flow_node'])

interface IssueQuickFixProps {
  issue: ProcessValidation | undefined
  node: ProcessNode
  page: BusinessProcess
  /**
   * Исправленная страница — в отмену, на экран и на сервер. `done` — замечание
   * исправлено целиком (у шлюза подписана последняя ветка).
   */
  onEdit: (page: BusinessProcess, done: boolean) => void
  /** Включить правку холста и выделить фигуру — чтобы провести связь. */
  onConnect: (nodeId: string) => void
}

/**
 * Исправить замечание, не уходя с карты. Без этого путь до исправления был
 * длинным: найти фигуру, открыть draw.io, поправить, сохранить, — а ошибки
 * есть в 59 картах из 68. Здесь — то, что правится одним полем: подпись ветки
 * шлюза, время шага, подпись фигуры; для фигуры без связей — переход к правке
 * холста с ней выделенной. Проверка пересчитывается после сохранения.
 */
export const IssueQuickFix: React.FC<IssueQuickFixProps> = ({ issue, node, page, onEdit, onConnect }) => {
  const code = issue?.code ?? ''
  let body: React.ReactNode = null
  if (code === 'gateway_branch_unlabeled') {
    const branches = page.edges.filter(
      (e) => e.sourceId === node.id && (e.kind ?? 'sequenceFlow') === 'sequenceFlow' && !(e.name || e.condition || '').trim(),
    )
    const names = new Map(page.nodes.map((n) => [n.id, n.name]))
    body = branches.length ? (
      <div className="space-y-1.5">
        {branches.map((edge) => (
          <TextFix
            key={edge.id}
            label={`Ветка → ${names.get(edge.targetId ?? '') || 'без цели'}`}
            placeholder="Условие ветки"
            chips={BRANCH_LABELS}
            onApply={(text) => onEdit(setEdgeName(page, edge.id, text), branches.length === 1)}
          />
        ))}
      </div>
    ) : null
  } else if (code === 'no_step_time') {
    body = (
      <TextFix
        label="Время выполнения, мин"
        placeholder={String(node.slaMinutes ?? '')}
        numeric
        onApply={(text) => onEdit(updateNode(page, node.id, { slaMinutes: Number(text), slaMeasured: true, slaText: null }), true)}
      />
    )
  } else if (code === 'generated_name') {
    body = (
      <TextFix
        label="Подпись фигуры"
        placeholder="Что происходит на этом шаге"
        onApply={(text) => onEdit(updateNode(page, node.id, { name: text }), true)}
      />
    )
  } else if (CONNECT_CODES.has(code)) {
    body = (
      <Button variant="outline" size="sm" className="h-7 gap-1.5 text-xs" onClick={() => onConnect(node.id)}>
        <Link2 className="h-3.5 w-3.5" />
        Соединить на холсте
      </Button>
    )
  }
  if (!body) return null
  return (
    <div className="mt-2 rounded-lg border border-emerald-500/30 bg-emerald-500/5 px-2 py-1.5">
      <p className="mb-1.5 flex items-center gap-1 text-[11px] font-medium text-emerald-700 dark:text-emerald-400">
        <Wrench className="h-3 w-3" /> Исправить здесь
      </p>
      {body}
    </div>
  )
}

/** Поле быстрого исправления: значение и «Применить»; готовые варианты — кнопками. */
const TextFix: React.FC<{
  label: string
  placeholder: string
  chips?: string[]
  numeric?: boolean
  onApply: (text: string) => void
}> = ({ label, placeholder, chips, numeric, onApply }) => {
  const [value, setValue] = useState('')
  const clean = value.trim()
  const valid = numeric ? /^\d+$/.test(clean) && Number(clean) > 0 : clean.length > 0
  const apply = (text: string) => {
    if (text.trim()) onApply(text.trim())
  }
  return (
    <div>
      <p className="mb-1 truncate text-[11px] text-muted-foreground" title={label}>{label}</p>
      <div className="flex flex-wrap items-center gap-1.5">
        {chips?.map((chip) => (
          <Button key={chip} variant="outline" size="sm" className="h-7 px-2 text-xs" onClick={() => apply(chip)}>
            {chip}
          </Button>
        ))}
        <input
          value={value}
          inputMode={numeric ? 'numeric' : undefined}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && valid) apply(clean)
          }}
          placeholder={placeholder}
          aria-label={label}
          className="h-7 min-w-0 flex-1 rounded-md border bg-background px-2 text-xs"
        />
        <Button size="sm" className="h-7 text-xs" disabled={!valid} onClick={() => apply(clean)}>
          Применить
        </Button>
      </div>
    </div>
  )
}
