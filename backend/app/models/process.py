from typing import List, Optional, Dict, Any, Literal
from pydantic import BaseModel, Field

NodeType = Literal[
    # События (2-ILOVA: Hodisa / Events)
    'startEvent',
    'endEvent',
    'intermediateTimerEvent',    # Kutish vaqti — ожидание внутри потока
    'intermediateMessageEvent',  # приём/отправка сообщения
    # Действия (2-ILOVA: Harakatlar / Activity)
    'task',
    'userTask',
    'serviceTask',               # PIX RPA or ABS integration
    'subProcess',
    # Шлюзы (2-ILOVA: Shlyuz / Gateway)
    'exclusiveGateway',          # XOR
    'parallelGateway',           # AND
    'inclusiveGateway',          # OR
    'complexGateway',            # сложное условие: в draw.io — ромб со звёздочкой
    # Артефакты (2-ILOVA: Artefaktlar / Artifacts)
    'dataStore',                 # IABS, EHA, EDO — информационная система
    'dataObject',                # Dalolatnoma, Yig'ma jild — документ
    'textAnnotation',            # текстовое примечание
    # Зоны ответственности
    'lane'                       # Swimlane
]

#: Как тип фигуры называется в интерфейсе и в отчёте о качестве импорта.
#: Двойник `NODE_TYPE_LABELS` из `app/src/types/process.ts`.
NODE_TYPE_LABELS: Dict[str, str] = {
    'startEvent': 'Стартовое событие',
    'endEvent': 'Завершение процесса',
    'intermediateTimerEvent': 'Ожидание (таймер)',
    'intermediateMessageEvent': 'Промежуточное событие-сообщение',
    'task': 'Пользовательская задача',
    'userTask': 'Ручная операция',
    'serviceTask': 'PIX RPA / Сервис АБС',
    'subProcess': 'Подпроцесс',
    'exclusiveGateway': 'Шлюз «ИЛИ» (XOR)',
    'parallelGateway': 'Шлюз «И» (AND)',
    'inclusiveGateway': 'Шлюз «И/ИЛИ» (OR)',
    'complexGateway': 'Сложный шлюз',
    'dataStore': 'Информационная система',
    'dataObject': 'Документ / объект данных',
    'textAnnotation': 'Примечание',
    'lane': 'Дорожка / Подразделение',
}

#: Узлы, которые участвуют в потоке управления (могут иметь sequenceFlow).
FLOW_NODE_TYPES = (
    'startEvent', 'endEvent', 'intermediateTimerEvent', 'intermediateMessageEvent',
    'task', 'userTask', 'serviceTask', 'subProcess',
    'exclusiveGateway', 'parallelGateway', 'inclusiveGateway', 'complexGateway',
)

#: Узлы-артефакты: соединяются только ассоциациями, шагами процесса не являются.
ARTIFACT_NODE_TYPES = ('dataStore', 'dataObject', 'textAnnotation')

#: Узлы, которые считаются шагами регламента (4-ILOVA).
TASK_NODE_TYPES = ('task', 'userTask', 'serviceTask', 'subProcess')

#: Шлюзы — точки ветвления потока. Двойник `GATEWAY_NODE_TYPES` из
#: `app/src/types/process.ts`.
GATEWAY_NODE_TYPES = (
    'exclusiveGateway', 'parallelGateway', 'inclusiveGateway', 'complexGateway',
)

#: Вид соединения (2-ILOVA: Birlashtiruvchi elementlar / Flows).
#: ``annotationLine`` — оформительская линия draw.io, у которой хотя бы один
#: конец висит в пустоте (разделители этапов). В BPMN и PIX такой конструкции
#: нет, поэтому в выгрузку она не идёт, но на холсте рисуется.
EdgeKind = Literal['sequenceFlow', 'messageFlow', 'association', 'annotationLine']

StepCategory = Literal[
    'manual',
    'rpa_bot',
    'api_service',
    'approval',
    'validation',
    'notification'
]

class LabelBox(BaseModel):
    """Рамка выносной подписи на карте и её текст."""
    x: int
    y: int
    width: int
    height: int
    text: str


class CanvasLayout(BaseModel):
    """Линии и подписи карты — ровно те, что уходят в выгрузку .bpmn.

    Холст сам ничего не трассирует: всё считает ``bpmn_exporter`` по геометрии
    той страницы, которую холст показывает.
    """
    #: Ломаная каждой связи, от фигуры до фигуры, в координатах карты.
    routes: Dict[str, List[List[int]]] = Field(default_factory=dict)
    edgeLabels: Dict[str, LabelBox] = Field(default_factory=dict)
    nodeLabels: Dict[str, LabelBox] = Field(default_factory=dict)
    #: Подпись у часов шага — та же, что у значка длительности в выгрузке.
    durations: Dict[str, str] = Field(default_factory=dict)


class LaneStats(BaseModel):
    """Время и деньги одной дорожки карты."""
    #: ``None`` — шаги, не лежащие ни в одной дорожке.
    laneId: Optional[str] = None
    laneName: str
    hourlyRate: int
    #: Ставка не задана ни у дорожки, ни у роли — взята по умолчанию.
    rateIsDefault: bool
    #: Откуда ставка: ``lane`` — своя в этой карте, ``role`` — справочник ролей, ``default``.
    rateSource: Literal['lane', 'role', 'default'] = 'default'
    stepCount: int
    workMinutes: int
    waitMinutes: int
    costUzs: int


class ProcessStats(BaseModel):
    """Сколько процесс длится и во что обходится — по одной странице карты."""
    stepCount: int
    #: Шаги, время которых импорт подставил по категории, а не снял с карты.
    unmeasuredStepCount: int
    #: Шаги без ИТ-системы и с системой, угаданной по названию, а не взятой с карты.
    noSystemStepCount: int = 0
    guessedSystemStepCount: int = 0
    #: Трудоёмкость: сумма ST всех шагов.
    workMinutes: int
    #: Сумма WT шагов и ожиданий-таймеров, у которых время написано на карте.
    waitMinutes: int
    #: Длительность: самый долгий путь от старта до финиша (ST + WT по пути).
    pathMinutes: int
    pathStepCount: int
    #: Фигуры этого пути по порядку — от старта.
    pathNodeIds: List[str] = Field(default_factory=list)
    #: Себестоимость всех шагов: время × ставка дорожки, у робота — цена запуска.
    costUzs: int
    defaultHourlyRate: int
    lanes: List[LaneStats] = Field(default_factory=list)


class Geometry(BaseModel):
    x: int = 100
    y: int = 100
    width: int = 120
    height: int = 60

class ProcessField(BaseModel):
    id: str
    name: str
    code: str
    type: Literal['string', 'number', 'date', 'boolean', 'select', 'file'] = 'string'
    required: bool = True
    description: Optional[str] = None
    options: Optional[List[str]] = None
    defaultValue: Optional[str] = None

class ProcessNode(BaseModel):
    id: str
    name: str
    type: NodeType = 'userTask'
    category: Optional[StepCategory] = 'manual'
    code: Optional[str] = None  # e.g. STEP-01
    laneId: Optional[str] = None
    laneName: Optional[str] = None
    role: Optional[str] = None
    system: Optional[str] = None
    #: Система угадана по словам в названии шага, а не взята с карты (хранилище
    #: данных у шага или свойство платформы). Как ``slaMeasured`` у времени:
    #: догадку видно, и в отчёты она не выдаётся за факт.
    systemGuessed: bool = False
    slaMinutes: Optional[int] = 30       # ST — время выполнения операции, мин (4-ILOVA)
    #: Время СНЯТО С КАРТЫ, а не подставлено импортом.
    #:
    #: Когда в подписи шага времени нет, импорт ставит правдоподобное значение
    #: по категории (ручная операция — час, согласование — три). Для расчёта SLA
    #: это разумно, но на карте такое время рисовать нельзя: аналитик видит у
    #: шага часы с цифрой «1 ч» и считает, что кто-то её замерил.
    slaMeasured: bool = False
    #: Время так, как оно написано на карте draw.io: «60 min», «5 daqiqa»,
    #: «1 440 min». Эталон вида карты — draw.io, и пересчитанное «1 ч» там не
    #: написано нигде. На карту текст идёт, только пока его число совпадает с
    #: ``slaMinutes``: время, изменённое на платформе, старыми словами не пишем.
    slaText: Optional[str] = None
    waitMinutes: Optional[int] = 0       # WT — время ожидания перед операцией, мин (4-ILOVA)
    #: То же для ожидания: подпись отдельных часов «Kutish vaqti 30 min».
    waitText: Optional[str] = None
    costPerExecution: Optional[int] = 5000
    #: Ставка часа сотрудника дорожки, сум/ч — только у дорожек. По ней шаг
    #: дорожки получает себестоимость (время × ставка); пусто — ставка по
    #: умолчанию (``process_stats.DEFAULT_HOURLY_RATE``).
    hourlyRate: Optional[int] = None
    automationPotential: Optional[int] = 50
    description: Optional[str] = None
    inputArtifacts: Optional[List[str]] = Field(default_factory=list)
    outputArtifacts: Optional[List[str]] = Field(default_factory=list)
    formFields: Optional[List[ProcessField]] = Field(default_factory=list)
    #: Страница файла, которую раскрывает этот свёрнутый подпроцесс.
    #:
    #: Заполняется только у шага типа ``subProcess``, и только когда подпись
    #: фигуры с плюсом совпала с именем другой страницы того же файла. Тогда в
    #: выгрузку шаг идёт не ``<bpmn:subProcess>``, а ``<bpmn:callActivity>`` со
    #: ссылкой на процесс этой страницы. Если совпадения нет — подпроцесс
    #: остаётся свёрнутым: так его и нарисовал аналитик.
    calledSubProcessId: Optional[str] = None
    geometry: Geometry
    #: Рамка фигуры ровно так, как она нарисована в draw.io, — если импорт её
    #: поправил (расширил шаг под подпись холста, отодвинул цилиндр). Холст и
    #: BPMN берут поправленную ``geometry``; карта PIX — эту: там фигуры стоят
    #: так же, как у аналитика, а подпись помещается за счёт кегля draw.io.
    drawioGeometry: Optional[Geometry] = None
    style: str = ''

class ProcessEdgePoint(BaseModel):
    x: int
    y: int

class ProcessEdge(BaseModel):
    id: str
    name: str = ''
    kind: EdgeKind = 'sequenceFlow'
    sourceId: Optional[str] = None
    targetId: Optional[str] = None
    condition: Optional[str] = None
    probability: Optional[int] = 100
    points: List[ProcessEdgePoint] = Field(default_factory=list)
    #: Изломы так, как они нарисованы в draw.io, — если импорт их снял (у
    #: пунктира цилиндра, который он отодвинул). Пара к ``drawioGeometry``.
    drawioPoints: Optional[List[ProcessEdgePoint]] = None
    #: Свободные концы линии в абсолютных координатах карты: заполняются, когда
    #: в draw.io конец не привязан к фигуре (mxPoint as="sourcePoint"/"targetPoint").
    sourcePoint: Optional[ProcessEdgePoint] = None
    targetPoint: Optional[ProcessEdgePoint] = None
    exitX: Optional[float] = None
    exitY: Optional[float] = None
    entryX: Optional[float] = None
    entryY: Optional[float] = None
    labelX: Optional[float] = None
    labelY: Optional[float] = None
    #: Подпись связи с переносами строк, как её разбил аналитик в draw.io
    #: (``name`` — та же подпись в одну строку), и её кегль. Карта PIX печатает
    #: подпись этими строками и этим кеглем: одной строкой в 12 пунктов длинная
    #: подпись ложилась на соседние.
    labelLines: Optional[str] = None
    labelFontSize: Optional[float] = None
    #: Геометрия подписи в draw.io как есть: доля пути x (-1…1), сдвиг y
    #: поперёк отрезка и смещение (dx, dy). По ней карта PIX ставит подпись
    #: туда, где её видит аналитик.
    drawioLabel: Optional[List[float]] = None
    #: Центр отдельной надписи draw.io («XA», «YO'Q», оторванная подпись),
    #: которую разбор отдал этой связи, — абсолютные координаты карты.
    drawioLabelPoint: Optional[List[float]] = None
    style: Optional[str] = None
    dashed: Optional[bool] = None
    dashPattern: Optional[str] = None
    edgeStyle: Optional[str] = None
    strokeColor: Optional[str] = None
    strokeWidth: Optional[float] = None

class ProcessValidation(BaseModel):
    """Замечание к импортированной карте — то, что показывается сотруднику."""
    level: Literal['error', 'warning', 'info']
    message: str
    nodeId: Optional[str] = None
    #: Машинный код замечания: по нему UI группирует однотипные записи.
    code: Optional[str] = None
    #: Имя фигуры на карте — чтобы сотрудник нашёл её без поиска по id.
    nodeName: Optional[str] = None
    #: Все фигуры, которых касается замечание: по нему платформа подсвечивает
    #: на карте не одну фигуру, а всю группу («расширены 226 фигур» — какие?).
    nodeIds: Optional[List[str]] = None
    #: Что с этим делать: замечание без подсказки бесполезно.
    hint: Optional[str] = None

class ProcessPassport(BaseModel):
    code: str = 'PRC-SQB-001'
    name: str
    version: str = '1.0'
    status: Literal['draft', 'in_review', 'approved', 'active', 'archived'] = 'draft'
    #: Вид модели: ``as_is`` — как процесс идёт сейчас, ``to_be`` — каким он
    #: должен стать. Нагрузку ролей считают только по картам «как есть»: иначе
    #: процесс с картами обоих видов считался бы дважды. Пусто — не указан.
    stage: Optional[Literal['as_is', 'to_be']] = None
    owner: str = ''
    department: str = ''
    category: str = 'Банковские процессы'
    targetSlaHours: float = 24.0
    description: str = ''
    createdDate: str
    updatedDate: str

class PixRegistryRecord(BaseModel):
    id: str
    caseId: str
    createdAt: str
    status: Literal['in_progress', 'completed', 'rejected', 'delayed'] = 'in_progress'
    currentStepId: str
    currentStepName: str
    assignedTo: str
    elapsedMinutes: int = 0
    data: Dict[str, Any] = Field(default_factory=dict)

class PixRegistrySchema(BaseModel):
    id: str
    name: str
    code: str
    description: str
    fields: List[ProcessField] = Field(default_factory=list)
    records: List[PixRegistryRecord] = Field(default_factory=list)

class ProcessetDeviation(BaseModel):
    id: str
    type: Literal['redundant_step', 'sla_breach', 'rework_loop', 'unplanned_path', 'bypass_step']
    title: str
    description: str
    severity: Literal['high', 'medium', 'low'] = 'medium'
    affectedSteps: List[str] = Field(default_factory=list)
    occurrenceCount: int = 0
    totalDelayHours: float = 0.0
    financialImpactUzs: int = 0
    rpaOpportunity: Optional[str] = None

class ProcessetMiningMetrics(BaseModel):
    totalCases: int = 100
    conformanceRate: float = 80.0
    avgLeadTimeHours: float = 24.0
    targetLeadTimeHours: float = 24.0
    slaBreachRate: float = 20.0
    reworkRate: float = 15.0
    potentialRpaSavingsUzs: int = 50000000
    deviations: List[ProcessetDeviation] = Field(default_factory=list)

class BusinessProcess(BaseModel):
    id: str
    name: str
    fileName: str
    passport: ProcessPassport
    nodes: List[ProcessNode] = Field(default_factory=list)
    edges: List[ProcessEdge] = Field(default_factory=list)
    lanes: List[ProcessNode] = Field(default_factory=list)
    validation: List[ProcessValidation] = Field(default_factory=list)
    registry: PixRegistrySchema
    miningMetrics: ProcessetMiningMetrics
    #: Остальные страницы файла draw.io, разобранные как самостоятельные карты.
    #:
    #: Раньше они просто терялись: импорт брал одну страницу и сообщал об этом
    #: предупреждением. В «Xizmat avtotransportini boshqarish jarayoni» страницы
    #: — это не варианты одного процесса (AS-IS / TO-BE), а его части: покупка,
    #: страхование, ремонт, топливо, продажа и сводная «JAMI». Каждая едет в
    #: выгрузку своим ``<bpmn:process>`` со своей схемой.
    #:
    #: Вложенность одноуровневая: у страницы своих страниц нет, и рекурсии
    #: здесь не бывает.
    subProcesses: List['BusinessProcess'] = Field(default_factory=list)
    #: Страницы-варианты того же процесса: «To Be», «AS IS ўзгариш», «To-Be».
    #:
    #: Вариант — это не часть процесса, а другое его состояние, поэтому в
    #: выгрузку основной карты он не идёт: .bpmn для «Korporativ Markaz … As Is»
    #: нёс внутри себя и «To Be». Из модели вариант при этом не пропадает — он
    #: открывается вкладкой над картой, как в самом draw.io. Опознаётся по
    #: пометке AS IS / TO BE в имени страницы (``is_variant_page_name``).
    variants: List['BusinessProcess'] = Field(default_factory=list)
    #: Имя страницы draw.io, взятой основной картой («ЭЛЕКТРОН», «ASOSIY
    #: JARAYON»), когда страниц в файле несколько. Дерево карт PIX подписывает
    #: им строку этой страницы: имя файла там говорило бы про весь процесс.
    mainPageName: Optional[str] = None
    #: Номер сохранения карты в хранилище (``app.services.store``). Ставит
    #: сервер; клиент присылает его обратно, и сохранение по устаревшей копии
    #: отклоняется (409), а не стирает чужие правки. Пусто — без проверки.
    revision: Optional[int] = None


BusinessProcess.model_rebuild()


class CanvasPage(BaseModel):
    """Страница карты для расчёта линий — только то, от чего он зависит.

    Холст присылал карту целиком: у «Харид…» главная страница вместе с
    вложенными — 22 МБ, а прокси сервера разработки задерживает тело больше
    мегабайта на пять секунд. Фигуры, связи и дорожки, сжатые gzip, — 60–120 КБ.
    Лишние поля полной карты при разборе отбрасываются, так что старый формат
    запроса тоже принимается.
    """
    nodes: List[ProcessNode] = Field(default_factory=list)
    edges: List[ProcessEdge] = Field(default_factory=list)
    lanes: List[ProcessNode] = Field(default_factory=list)
