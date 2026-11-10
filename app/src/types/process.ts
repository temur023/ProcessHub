export type NodeType =
  // События (2-ILOVA: Hodisa / Events)
  | 'startEvent'
  | 'endEvent'
  | 'intermediateTimerEvent' // Kutish vaqti — ожидание внутри потока
  | 'intermediateMessageEvent'
  // Действия (2-ILOVA: Harakatlar / Activity)
  | 'task'
  | 'userTask'
  | 'serviceTask' // PIX RPA Bot or ABS Integration
  | 'subProcess'
  // Шлюзы (2-ILOVA: Shlyuz / Gateway)
  | 'exclusiveGateway' // XOR
  | 'parallelGateway' // AND
  | 'inclusiveGateway' // OR
  | 'complexGateway' // сложное условие: в draw.io — ромб со звёздочкой
  // Артефакты (2-ILOVA: Artefaktlar / Artifacts)
  | 'dataStore' // IABS, EHA, EDO — информационная система
  | 'dataObject' // Dalolatnoma, Yig'ma jild — документ
  | 'textAnnotation'
  // Зоны ответственности
  | 'lane' // Department / Role Swimlane

/** Артефакты: соединяются только ассоциациями, шагами процесса не являются. */
export const ARTIFACT_NODE_TYPES = ['dataStore', 'dataObject', 'textAnnotation'] as const satisfies readonly NodeType[]

/** Узлы, которые считаются шагами регламента (4-ILOVA). */
export const TASK_NODE_TYPES = ['task', 'userTask', 'serviceTask', 'subProcess'] as const satisfies readonly NodeType[]

/** Шлюзы: развилки потока, шагами регламента не являются. */
export const GATEWAY_NODE_TYPES = [
  'exclusiveGateway', 'parallelGateway', 'inclusiveGateway', 'complexGateway',
] as const satisfies readonly NodeType[]

export const isTaskNode = (type: NodeType): boolean => (TASK_NODE_TYPES as readonly NodeType[]).includes(type)
export const isArtifactNode = (type: NodeType): boolean => (ARTIFACT_NODE_TYPES as readonly NodeType[]).includes(type)
export const isGatewayNode = (type: NodeType): boolean => (GATEWAY_NODE_TYPES as readonly NodeType[]).includes(type)

/**
 * Вид соединения (2-ILOVA: Birlashtiruvchi elementlar / Flows).
 * `annotationLine` — оформительская линия draw.io (разделитель этапов): хотя бы
 * один её конец не опирается на шаг процесса. В BPMN и PIX такой конструкции
 * нет, поэтому в выгрузку она не идёт, но на холсте рисуется.
 */
export type EdgeKind = 'sequenceFlow' | 'messageFlow' | 'association' | 'annotationLine'

export type StepCategory =
  | 'manual'
  | 'rpa_bot'
  | 'api_service'
  | 'approval'
  | 'validation'
  | 'notification'

export interface Geometry {
  x: number
  y: number
  width: number
  height: number
}

export interface ProcessField {
  id: string
  name: string
  code: string
  type: 'string' | 'number' | 'date' | 'boolean' | 'select' | 'file'
  required: boolean
  description?: string
  options?: string[]
  defaultValue?: string
}

export interface ProcessNode {
  id: string
  name: string
  type: NodeType
  category?: StepCategory
  code?: string // e.g. STEP-01
  laneId?: string
  laneName?: string
  role?: string // e.g. Кредитный эксперт, Робот PIX RPA
  system?: string // e.g. АБС ЦФТ, ЕПИГУ, SAP, CRM SQB
  slaMinutes?: number // ST — время выполнения операции, мин (4-ILOVA)
  /**
   * Время снято с карты, а не подставлено импортом.
   *
   * Когда в подписи шага времени нет, импорт ставит правдоподобное значение по
   * категории. Для расчёта SLA это нужно, но на карте такое время рисовать
   * нельзя: аналитик видит у шага часы с цифрой и считает её замером.
   */
  slaMeasured?: boolean
  /** ИТ-система угадана по словам названия шага, а не взята с карты. */
  systemGuessed?: boolean
  /** Время словами карты draw.io («60 min», «5 daqiqa») — пока совпадает с `slaMinutes`. */
  slaText?: string | null
  waitMinutes?: number // WT — время ожидания перед операцией, мин (4-ILOVA)
  /** Подпись часов ожидания так, как она написана на карте. */
  waitText?: string | null
  costPerExecution?: number // In UZS
  /**
   * Ставка часа сотрудника дорожки, сум/ч — только у дорожек. Шаг дорожки
   * стоит «время × ставка»; пусто — ставка по умолчанию (считает бэкенд).
   */
  hourlyRate?: number | null
  automationPotential?: number // 0-100%
  description?: string
  inputArtifacts?: string[]
  outputArtifacts?: string[]
  formFields?: ProcessField[]
  /**
   * Страница файла, которую раскрывает этот свёрнутый подпроцесс.
   *
   * Заполняется только у шага типа `subProcess`, и только когда подпись фигуры
   * с плюсом совпала с именем другой страницы того же файла. Тогда в выгрузку
   * шаг идёт не `<bpmn:subProcess>`, а `<bpmn:callActivity>` со ссылкой на
   * процесс этой страницы.
   */
  calledSubProcessId?: string
  geometry: Geometry
  /**
   * Рамка фигуры так, как она нарисована в draw.io, если импорт её поправил.
   * Карта PIX строится по ней, поэтому сдвиг фигуры на холсте двигает и её.
   */
  drawioGeometry?: Geometry | null
  style: string
}

export interface ProcessEdgePoint {
  x: number
  y: number
}

export interface ProcessEdge {
  id: string
  name: string
  kind?: EdgeKind
  sourceId?: string
  targetId?: string
  condition?: string
  probability?: number // 0-100% for branch execution
  points: ProcessEdgePoint[]
  /** Изломы так, как нарисованы в draw.io, — пара к `drawioGeometry`. */
  drawioPoints?: ProcessEdgePoint[] | null
  /** Свободные концы линии в абсолютных координатах: конец не привязан к фигуре */
  sourcePoint?: ProcessEdgePoint
  targetPoint?: ProcessEdgePoint
  /** mxGraph perimeter constraints, 0..1 of the source/target box */
  exitX?: number
  exitY?: number
  entryX?: number
  entryY?: number
  /** Relative edge-label: x along the polyline (0..1), y perpendicular in px */
  labelX?: number
  labelY?: number
  /** draw.io style */
  style?: string
  dashed?: boolean
  dashPattern?: string
  edgeStyle?: string // e.g. orthogonalEdgeStyle
  strokeColor?: string
  strokeWidth?: number
}

/** Замечание к импортированной карте — то, что показывается сотруднику. */
export interface ProcessValidation {
  level: 'error' | 'warning' | 'info'
  message: string
  nodeId?: string
  /** Машинный код замечания: по нему UI группирует однотипные записи. */
  code?: string
  /** Имя фигуры на карте — чтобы сотрудник нашёл её без поиска по id. */
  nodeName?: string
  /**
   * Все фигуры, которых касается замечание: по нему платформа подсвечивает на
   * карте не одну фигуру, а всю группу («расширены 226 фигур» — какие?).
   */
  nodeIds?: string[]
  /** Что с этим делать: замечание без подсказки бесполезно. */
  hint?: string
}

export type ProcessStatus = 'draft' | 'in_review' | 'approved' | 'active' | 'archived'

/** Статус паспорта словами — для паспорта карты и карточек каталога. */
export const PROCESS_STATUS_LABELS: Record<ProcessStatus, string> = {
  draft: 'Черновик',
  in_review: 'На согласовании',
  approved: 'Утверждён',
  active: 'Действует',
  archived: 'В архиве',
}

export type ProcessStage = 'as_is' | 'to_be'

export const PROCESS_STAGE_LABELS: Record<ProcessStage, string> = {
  as_is: 'AS-IS',
  to_be: 'TO-BE',
}

export interface ProcessPassport {
  code: string // e.g. PRC-SQB-CRED-001
  name: string
  version: string
  status: ProcessStatus
  /** Вид модели: «как есть» или «как должно быть»; нагрузку ролей считают по AS-IS. */
  stage?: ProcessStage | null
  owner: string // Владелец процесса
  department: string // Подразделение
  category: string // Направление (Кредитование, Операционный блок, Валютный контроль и т.д.)
  targetSlaHours: number
  description: string
  createdDate: string
  updatedDate: string
}

export interface PixRegistryRecord {
  id: string
  caseId: string
  createdAt: string
  status: 'in_progress' | 'completed' | 'rejected' | 'delayed'
  currentStepId: string
  currentStepName: string
  assignedTo: string
  elapsedMinutes: number
  data: Record<string, string | number | boolean>
}

export interface PixRegistrySchema {
  id: string
  name: string
  code: string
  description: string
  fields: ProcessField[]
  records: PixRegistryRecord[]
}

// Processet Process Mining comparison models
export interface ProcessetDeviation {
  id: string
  type: 'redundant_step' | 'sla_breach' | 'rework_loop' | 'unplanned_path' | 'bypass_step'
  title: string
  description: string
  severity: 'high' | 'medium' | 'low'
  affectedSteps: string[]
  occurrenceCount: number
  totalDelayHours: number
  financialImpactUzs: number
  rpaOpportunity?: string
}

export interface ProcessetMiningMetrics {
  totalCases: number
  conformanceRate: number // e.g. 74%
  avgLeadTimeHours: number
  targetLeadTimeHours: number
  slaBreachRate: number // e.g. 26%
  reworkRate: number // e.g. 18%
  potentialRpaSavingsUzs: number
  deviations: ProcessetDeviation[]
}

export interface BusinessProcess {
  id: string
  name: string
  fileName: string
  passport: ProcessPassport
  nodes: ProcessNode[]
  edges: ProcessEdge[]
  lanes: ProcessNode[]
  validation: ProcessValidation[]
  registry: PixRegistrySchema
  miningMetrics: ProcessetMiningMetrics
  /**
   * Остальные страницы файла draw.io, разобранные как самостоятельные карты.
   *
   * Раньше они терялись: импорт брал одну страницу и сообщал об этом
   * замечанием. В «Xizmat avtotransportini boshqarish jarayoni» страницы — не
   * варианты одного процесса (AS-IS / TO-BE), а его части: покупка,
   * страхование, ремонт, топливо, продажа и сводная «JAMI». Каждая едет в
   * выгрузку своим `<bpmn:process>` со своей схемой.
   *
   * Вложенность одноуровневая: у страницы своих страниц нет.
   */
  subProcesses?: BusinessProcess[]
  /**
   * Страницы-варианты того же процесса: «To Be», «AS IS ўзгариш», «To-Be».
   *
   * Вариант — другое состояние процесса, а не его часть: открывается вкладкой
   * над картой, но в выгрузку этой карты не идёт.
   */
  variants?: BusinessProcess[]
  /** Имя первой страницы файла draw.io, если в файле их несколько. */
  mainPageName?: string | null
  /**
   * Номер сохранения карты на сервере. Уходит обратно с каждым сохранением:
   * если на сервере уже другой, сохранение отклоняется (409), а не стирает
   * чужие правки.
   */
  revision?: number | null
}

/** Рамка выносной подписи на карте и её текст. */
export interface LabelBox {
  x: number
  y: number
  width: number
  height: number
  text: string
}

/**
 * Линии и подписи страницы карты — тем же расчётом, что уходит в .bpmn.
 * Холст сам ничего не трассирует: всё считает бэкенд.
 */
export interface CanvasLayout {
  routes: Record<string, [number, number][]>
  edgeLabels: Record<string, LabelBox>
  nodeLabels: Record<string, LabelBox>
  /** Подпись у часов шага — та же, что у значка длительности в выгрузке. */
  durations: Record<string, string>
}

/** Строка истории версий карты (`GET /processes/{id}/versions`). */
export interface ProcessVersion {
  version: number
  revision: number
  /** Откуда версия: import, edit, restore, migration. */
  source: string
  note?: string | null
  name: string
  nodeCount: number
  edgeCount: number
  stepCount: number
  /** Когда версия началась: правки подряд дописываются в неё же. */
  createdAt: string
  savedAt: string
  /** Кто сохранил; у версий, сохранённых до входа в систему, — никто. */
  author?: string | null
  authorEmail?: string | null
}

/** Время и деньги одной дорожки карты (считает бэкенд). */
export interface LaneStats {
  /** `null` — шаги, не лежащие ни в одной дорожке. */
  laneId: string | null
  laneName: string
  hourlyRate: number
  rateIsDefault: boolean
  /** Откуда ставка: своя в этой карте, справочник ролей или по умолчанию. */
  rateSource: 'lane' | 'role' | 'default'
  stepCount: number
  workMinutes: number
  waitMinutes: number
  costUzs: number
}

/** Сколько страница карты длится и во что обходится (`POST /canvas/stats`). */
export interface ProcessStats {
  stepCount: number
  /** Шаги, время которых импорт подставил, а не снял с карты. */
  unmeasuredStepCount: number
  /** Шаги без ИТ-системы и с системой-догадкой. */
  noSystemStepCount: number
  guessedSystemStepCount: number
  /** Трудоёмкость: сумма ST всех шагов. */
  workMinutes: number
  waitMinutes: number
  /** Самый долгий путь от старта до финиша. */
  pathMinutes: number
  pathStepCount: number
  pathNodeIds: string[]
  costUzs: number
  defaultHourlyRate: number
  lanes: LaneStats[]
}

export const NODE_TYPE_LABELS: Record<NodeType, string> = {
  startEvent: 'Стартовое событие',
  endEvent: 'Завершение процесса',
  intermediateTimerEvent: 'Ожидание (таймер)',
  intermediateMessageEvent: 'Промежуточное событие-сообщение',
  task: 'Пользовательская задача',
  userTask: 'Ручная операция',
  serviceTask: 'PIX RPA / Сервис АБС',
  subProcess: 'Подпроцесс',
  exclusiveGateway: 'Шлюз «ИЛИ» (XOR)',
  parallelGateway: 'Шлюз «И» (AND)',
  inclusiveGateway: 'Шлюз «И/ИЛИ» (OR)',
  complexGateway: 'Сложный шлюз',
  dataStore: 'Информационная система',
  dataObject: 'Документ / объект данных',
  textAnnotation: 'Примечание',
  lane: 'Дорожка / Подразделение',
}
