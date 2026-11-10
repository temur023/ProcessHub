import React, { useState, useEffect } from 'react'
import {
  X,
  Check,
  Sliders,
  SquarePlus,
  ArrowRight,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import type { ProcessNode, StepCategory, BusinessProcess } from '@/types/process'
import { NODE_TYPE_LABELS, isTaskNode } from '@/types/process'
import { fetchSystems, type SystemUsage } from '@/lib/api'
import { formatMoney } from '@/lib/presentation'

interface NodeDetailDrawerProps {
  node: ProcessNode | null
  process: BusinessProcess
  onClose: () => void
  onSaveNode: (updated: ProcessNode) => void
  /** Открыть страницу файла, которую раскрывает шаг-подпроцесс. */
  onOpenPage: (pageId: string) => void
}

export const NodeDetailDrawer: React.FC<NodeDetailDrawerProps> = ({
  node,
  process,
  onClose,
  onSaveNode,
  onOpenPage,
}) => {
  const [formData, setFormData] = useState<ProcessNode | null>(null)
  const [validationError, setValidationError] = useState<string | null>(null)
  const [systems, setSystems] = useState<SystemUsage[]>([])

  useEffect(() => {
    void fetchSystems().then(setSystems).catch(() => setSystems([]))
  }, [])

  useEffect(() => {
    setFormData(node ? { ...node } : null)
    setValidationError(null)
  }, [node])

  // Панель перекрывает половину экрана, но раньше закрывалась только крестиком:
  // привычное Escape не работало, и уйти с клавиатуры было нечем.
  useEffect(() => {
    if (!node) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [node, onClose])

  if (!node || !formData) return null

  /**
   * Время, себестоимость, роль и потенциал роботизации есть только у шага.
   *
   * Панель открывалась одинаково для любой фигуры и требовала SLA: переименовать
   * цилиндр системы «EHA» было нельзя — сохранение отвечало «SLA должен быть
   * ≥ 1 мин», хотя времени у хранилища данных нет и быть не может.
   */
  const isStep = isTaskNode(node.type)
  const calledPage = node.calledSubProcessId
    ? [...(process.subProcesses ?? []), ...(process.variants ?? [])].find((p) => p.id === node.calledSubProcessId)
    : undefined

  const handleSave = () => {
    if (isStep && (formData.slaMinutes ?? 0) < 1) {
      setValidationError('SLA должен быть ≥ 1 мин')
      return
    }
    if (isStep && (formData.costPerExecution ?? 0) < 0) {
      setValidationError('Себестоимость не может быть отрицательной')
      return
    }
    if (!formData.name.trim()) {
      setValidationError(isStep ? 'Наименование операции не может быть пустым' : 'Наименование не может быть пустым')
      return
    }
    setValidationError(null)
    if (!isStep) {
      onSaveNode({ ...formData, name: formData.name.trim() })
      return
    }
    const slaMinutes = Math.max(1, Math.round(formData.slaMinutes ?? 1))
    onSaveNode({
      ...formData,
      name: formData.name.trim(),
      slaMinutes,
      // Время, введённое сотрудником, — уже не догадка импорта по типу
      // операции: оно идёт на карту студии и снимает замечание о времени.
      slaMeasured: Boolean(node.slaMeasured) || slaMinutes !== node.slaMinutes,
      waitMinutes: Math.max(0, Math.round(formData.waitMinutes ?? 0)),
      costPerExecution: Math.max(0, Math.round(formData.costPerExecution ?? 0)),
    })
  }

  return (
    <>
      {/* Затемнение: без него панель просто ложилась поверх таблицы, и было
          непонятно, что остальной экран сейчас не в работе. */}
      <div
        className="fixed inset-0 z-40 bg-black/40 animate-in fade-in duration-150"
        onClick={onClose}
        aria-hidden
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-label={`${isStep ? 'Свойства шага' : 'Свойства фигуры'} «${node.name}»`}
        className="fixed inset-y-0 right-0 z-50 flex w-full max-w-full flex-col justify-between border-l bg-card shadow-2xl animate-in slide-in-from-right duration-200 sm:w-[460px]"
      >
      {/* Drawer Header */}
      <div className="p-4 border-b bg-muted/40 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className="p-2 rounded-lg bg-emerald-100 dark:bg-emerald-950/60 text-emerald-600">
            <Sliders className="w-4 h-4" />
          </div>
          <div>
            <h3 className="font-bold text-sm text-foreground">
              {isStep ? `${formData.code ? `${formData.code}: ` : ''}Свойства шага` : 'Свойства фигуры'}
            </h3>
            <p className="text-[11px] text-muted-foreground">
              {isStep ? 'Параметры регламента и конфигурация PIX RPA' : NODE_TYPE_LABELS[node.type]}
            </p>
          </div>
        </div>

        <Button
          variant="ghost"
          size="icon"
          className="h-8 w-8 text-muted-foreground"
          onClick={onClose}
        >
          <X className="w-4 h-4" />
        </Button>
      </div>

      {/* Ссылка шага-подпроцесса — как ссылка фигуры в draw.io: шаг выделен,
          а рядом переход на страницу, которую он раскрывает. */}
      {calledPage && (
        <button
          type="button"
          onClick={() => onOpenPage(calledPage.id)}
          className="group flex items-center gap-2 border-b bg-primary/5 px-4 py-2.5 text-left text-xs transition-colors hover:bg-primary/10"
        >
          <SquarePlus className="h-4 w-4 shrink-0 text-primary" />
          <span className="shrink-0 text-muted-foreground">Подпроцесс:</span>
          <span className="min-w-0 truncate font-semibold text-primary group-hover:underline">{calledPage.name}</span>
          <ArrowRight className="ml-auto h-4 w-4 shrink-0 text-primary" />
        </button>
      )}

      {/* Drawer Body Form */}
      <div className="p-5 flex-1 overflow-y-auto space-y-4 text-xs">
        {/* Step Name & Code */}
        <div className="space-y-1.5">
          <Label className="text-xs font-semibold">{isStep ? 'Наименование операции' : 'Наименование'}</Label>
          <Input
            value={formData.name}
            onChange={(e) => setFormData({ ...formData, name: e.target.value })}
            className="text-xs"
          />
        </div>

        {isStep && (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label className="text-xs font-semibold">Код шага</Label>
            <Input
              value={formData.code || ''}
              onChange={(e) => setFormData({ ...formData, code: e.target.value })}
              placeholder="STEP-01"
              className="text-xs font-mono"
            />
          </div>

          <div className="space-y-1.5">
            <Label className="text-xs font-semibold">Категория</Label>
            <select
              value={formData.category || 'manual'}
              onChange={(e) => {
                const nextCategory = e.target.value as StepCategory
                const lockedTypes = new Set([
                  'startEvent',
                  'endEvent',
                  'exclusiveGateway',
                  'parallelGateway',
                  'inclusiveGateway',
                  'complexGateway',
                  'lane',
                ])
                let nextType = formData.type
                if (!lockedTypes.has(formData.type)) {
                  if (nextCategory === 'rpa_bot' || nextCategory === 'api_service') {
                    nextType = 'serviceTask'
                  } else if (formData.type === 'serviceTask') {
                    nextType = 'userTask'
                  }
                }
                setFormData({
                  ...formData,
                  category: nextCategory,
                  type: nextType,
                })
              }}
              className="w-full h-9 px-2 text-xs rounded-md border bg-background"
            >
              <option value="manual">Ручная задача</option>
              <option value="rpa_bot">PIX RPA Робот</option>
              <option value="approval">Согласование / Комитет</option>
              <option value="validation">Проверка / Скоринг</option>
              <option value="api_service">Интеграция АБС</option>
              <option value="notification">Уведомление</option>
            </select>
          </div>
        </div>
        )}

        {/* Lane / Department & Role */}
        <div className={`grid grid-cols-1 gap-3 ${isStep ? 'sm:grid-cols-2' : ''}`}>
          <div className="space-y-1.5">
            <Label className="text-xs font-semibold">Подразделение (Дорожка)</Label>
            <select
              value={formData.laneId || ''}
              onChange={(e) => {
                const lane = process.lanes.find((l) => l.id === e.target.value)
                setFormData({
                  ...formData,
                  laneId: e.target.value,
                  laneName: lane?.name || formData.laneName,
                })
              }}
              className="w-full h-9 px-2 text-xs rounded-md border bg-background"
            >
              <option value="">Не привязано</option>
              {process.lanes.map((l) => (
                <option key={l.id} value={l.id}>
                  {l.name}
                </option>
              ))}
            </select>
          </div>

          {isStep && (
          <div className="space-y-1.5">
            <Label className="text-xs font-semibold">Роль / Исполнитель</Label>
            <Input
              value={formData.role || ''}
              onChange={(e) => setFormData({ ...formData, role: e.target.value })}
              placeholder="Кредитный эксперт"
              className="text-xs"
            />
          </div>
          )}
        </div>

        {/* ИТ-система: подсказки — из справочника систем всех карт. */}
        {isStep && (
        <div className="space-y-1.5">
          <Label className="text-xs font-semibold" htmlFor="node-system">ИТ-Система</Label>
          <Input
            id="node-system"
            list="known-systems"
            value={formData.system || ''}
            onChange={(e) => setFormData({ ...formData, system: e.target.value, systemGuessed: false })}
            placeholder="Не указана"
            className="text-xs"
          />
          <datalist id="known-systems">
            {systems.map((s) => (
              <option key={s.name} value={s.name}>{`${s.steps} шаг.`}</option>
            ))}
          </datalist>
          {formData.system && formData.systemGuessed && (
            <p className="text-[11px] text-amber-600 dark:text-amber-400">
              Угадана по названию шага — на карте система не нарисована. Проверьте и при необходимости исправьте.
            </p>
          )}
          {!formData.system && (
            <p className="text-[11px] text-muted-foreground">
              На карте у шага нет связанной системы. Несколько систем — через запятую.
            </p>
          )}
        </div>
        )}

        {/* SLA & Costs */}
        {validationError && (
          <p className="text-xs text-rose-600 bg-rose-50 dark:bg-rose-950/40 border border-rose-200 dark:border-rose-800 rounded-md px-2.5 py-1.5">
            {validationError}
          </p>
        )}
        {isStep && (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label className="text-xs font-semibold flex items-center justify-between">
              <span>Норматив SLA</span>
              <span className="text-muted-foreground font-normal">
                {formData.slaMinutes || 30} мин ({Math.round(((formData.slaMinutes || 30) / 60) * 10) / 10}ч)
              </span>
            </Label>
            <Input
              type="number"
              min={1}
              step={1}
              value={formData.slaMinutes || 30}
              onChange={(e) => {
                const v = Math.max(1, Math.round(Number(e.target.value) || 1))
                setFormData({ ...formData, slaMinutes: v })
              }}
              className="text-xs"
            />
          </div>

          <div className="space-y-1.5">
            <Label className="text-xs font-semibold" title="Сколько шаг ждёт перед началом работы">
              Ожидание (WT), мин
            </Label>
            <Input
              type="number"
              min={0}
              step={1}
              value={formData.waitMinutes ?? 0}
              onChange={(e) => {
                const v = Math.max(0, Math.round(Number(e.target.value) || 0))
                setFormData({ ...formData, waitMinutes: v })
              }}
              className="text-xs"
            />
          </div>

          {/* Себестоимость человека — время × ставка часа дорожки, её
              пересчитывает сервер при сохранении; руками задаётся только
              цена запуска робота. */}
          <div className="space-y-1.5 sm:col-span-2">
            {formData.category === 'rpa_bot' ? (
              <>
                <Label className="text-xs font-semibold">Цена запуска робота (UZS)</Label>
                <Input
                  type="number"
                  min={0}
                  step={100}
                  value={formData.costPerExecution ?? 0}
                  onChange={(e) => {
                    const v = Math.max(0, Math.round(Number(e.target.value) || 0))
                    setFormData({ ...formData, costPerExecution: v })
                  }}
                  className="text-xs"
                />
              </>
            ) : (
              <>
                <Label className="text-xs font-semibold">Себестоимость</Label>
                <p className="rounded-md border bg-muted/40 px-2.5 py-2 text-xs">
                  <span className="font-semibold tabular-nums">{formatMoney(node.costPerExecution ?? 0)}</span>
                  <span className="text-muted-foreground">
                    {' '}— время шага × ставка часа дорожки. Пересчитывается при сохранении;
                    ставку роли задают в панели «Сведения о карте» → «По дорожкам».
                  </span>
                </p>
              </>
            )}
          </div>
        </div>
        )}

        {/* «Потенциал роботизации» убран: ползунок у всех шагов стоял на значении
            по умолчанию (60 %), а не на оценке. Вернуть, когда оценки появятся. */}

        {/* Description / Instructions */}
        <div className="space-y-1.5 pt-2 border-t">
          <Label className="text-xs font-semibold">{isStep ? 'Регламентное описание операции' : 'Описание'}</Label>
          <Textarea
            rows={3}
            value={formData.description || ''}
            onChange={(e) => setFormData({ ...formData, description: e.target.value })}
            placeholder={isStep ? 'Опишите правила выполнения, проверки и требования к шагу...' : 'Пояснение к фигуре...'}
            className="text-xs"
          />
        </div>
      </div>

      {/* Drawer Footer */}
      <div className="flex items-center justify-end gap-2 border-t bg-muted/40 p-4">
        <Button variant="outline" size="sm" onClick={onClose} className="flex-1 sm:flex-none">
          Отмена
        </Button>
        <Button
          size="sm"
          onClick={handleSave}
          className="flex-1 gap-1.5 bg-emerald-600 text-white hover:bg-emerald-700 sm:flex-none"
        >
          <Check className="h-4 w-4" />
          Сохранить изменения
        </Button>
      </div>
      </div>
    </>
  )
}
