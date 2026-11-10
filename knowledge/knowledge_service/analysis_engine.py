"""Instruction ↔ regulation analysis over the Nova Gateway.

Stages: read instruction functions → find regulation clauses (/retrieve) →
match functions to clauses (model, our prompt) → find clauses of the
position's area missing from the instruction → conclusion.

Nova returns at most five clauses per search and cannot hand over a whole
regulation, so the report never claims full coverage of the regulation.
The model's answer is data: its JSON is validated, unknown IDs are dropped
and every quote is checked against the clause text it cites.
"""
import asyncio
import json
import re
import unicodedata
from collections import Counter

from pydantic import BaseModel, Field, ValidationError

from .analysis_prompts import MATCH_SYSTEM, METHOD_VERSION, REPAIR_MESSAGE, REVERSE_SYSTEM, SUMMARY_SYSTEM
from .nova import NovaError

STAGES = (
    ('instruction', 'Чтение инструкции'),
    ('retrieval', 'Поиск оснований в положении'),
    ('matching', 'Сопоставление функций с положением'),
    ('reverse', 'Функции положения вне инструкции'),
    ('summary', 'Заключение'),
)
MAX_FUNCTIONS = 60
QUERY_CHARS = 600
FUNCTION_CHARS = 1500
CANDIDATES_PER_FUNCTION = 6
MATCH_BATCH = 4
MATCH_CLAUSES = 16
REVERSE_BATCH = 8
REVERSE_LIMIT = 40
TEMPERATURE = 0.1
MATCH_STATUSES = ('covered', 'partial', 'conflict', 'not_found')
REVERSE_STATUSES = ('missing', 'covered', 'not_applicable')


class Cancelled(Exception):
    pass


class AnalysisFailed(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def normalize(text):
    text = unicodedata.normalize('NFKC', text or '').lower().replace('­', '')
    text = re.sub(r"[ʻʼ‘’`´']", "'", text)
    text = re.sub(r'[«»“”„"]', '"', text)
    return ' '.join(text.split())


def quote_verified(quote, clause_text):
    quote = normalize(quote).strip(' .,;:"')
    return len(quote.split()) >= 3 and quote in normalize(clause_text)


def parse_json(content):
    text = content.strip()
    fenced = re.search(r'```(?:json)?\s*(.*?)```', text, re.S)
    if fenced:
        text = fenced.group(1)
    start, end = text.find('{'), text.rfind('}')
    if start < 0 or end <= start:
        raise ValueError('ответ не содержит JSON-объект')
    return json.loads(text[start:end + 1])


class _Quote(BaseModel):
    clause_id: str
    quote: str = Field(max_length=2000)


class _Match(BaseModel):
    function_id: str
    status: str
    clause_ids: list[str] = Field(default_factory=list, max_length=20)
    quotes: list[_Quote] = Field(default_factory=list, max_length=20)
    explanation: str = Field(default='', max_length=3000)
    recommendation: str = Field(default='', max_length=3000)


class _Reverse(BaseModel):
    clause_id: str
    status: str
    function_ids: list[str] = Field(default_factory=list, max_length=20)
    quote: str = Field(default='', max_length=2000)
    explanation: str = Field(default='', max_length=3000)
    recommendation: str = Field(default='', max_length=3000)


class _Summary(BaseModel):
    summary: str = Field(min_length=1, max_length=4000)
    key_points: list[str] = Field(default_factory=list, max_length=10)
    recommendations: list[str] = Field(default_factory=list, max_length=10)


class NovaCaller:
    """Retries exactly as the Gateway documentation prescribes.

    429: wait Retry-After (5 s by default) and repeat; 502: repeat up to
    twice after a pause. 400/401/403/422/500 and timeouts are not repeated.
    Requests go one at a time: the key has a concurrency limit.
    """

    def __init__(self, nova, tag, sleep=asyncio.sleep, rate_limit_retries=6, gateway_retries=2, gateway_pause=15):
        self.nova, self.tag, self.sleep = nova, tag, sleep
        self.rate_limit_retries, self.gateway_retries, self.gateway_pause = rate_limit_retries, gateway_retries, gateway_pause
        self.count = 0
        self.trace = []

    async def _call(self, kind, call):
        waits = bad_gateway = 0
        while True:
            self.count += 1
            request_id = f'{self.tag}-{self.count}'
            try:
                result, returned_id = await call(request_id)
                self.trace.append({'kind': kind, 'request_id': returned_id or request_id})
                return result
            except NovaError as error:
                self.trace.append({'kind': kind, 'request_id': error.request_id or request_id, 'error': error.code})
                if error.code == 'http_429' and waits < self.rate_limit_retries:
                    waits += 1
                    await self.sleep(int(error.retry_after or 5))
                    continue
                if error.code == 'http_502' and bad_gateway < self.gateway_retries:
                    bad_gateway += 1
                    await self.sleep(self.gateway_pause)
                    continue
                raise

    async def search(self, query):
        return await self._call('search', lambda request_id: self.nova.search_candidates(query, request_id=request_id))

    async def model(self, system_prompt, text):
        return await self._call('model', lambda request_id: self.nova.analyze(
            system_prompt, text, temperature=TEMPERATURE, request_id=request_id))


async def _model_json(caller, system_prompt, payload, validate):
    """One repair attempt; the second invalid answer is reported, not guessed."""
    text = 'Данные для анализа (JSON):\n' + json.dumps(payload, ensure_ascii=False)
    problem = None
    for _ in range(2):
        content = await caller.model(system_prompt, text if problem is None else
                                     text + '\n\n' + REPAIR_MESSAGE.format(problem=problem))
        try:
            return validate(parse_json(content))
        except (ValueError, ValidationError, TypeError, KeyError) as error:
            problem = str(error).splitlines()[0][:300] or 'неверная структура'
    return None


def _clause_view(clause):
    return {key: clause[key] for key in ('id', 'document', 'chapter', 'clause', 'text') if clause.get(key)}


async def analyze_instruction(context, caller, progress):
    """Return the report body. `progress(stage, done, total)` may raise Cancelled."""
    warnings = []
    all_functions = context['functions']
    if not all_functions:
        raise AnalysisFailed('no_functions', 'В инструкции не найден раздел функций (обязанностей). '
                             'Проверьте разметку разделов в оригинале или подготовку текста.')
    if len(all_functions) > MAX_FUNCTIONS:
        warnings.append(f'В инструкции {len(all_functions)} функций; проанализированы первые {MAX_FUNCTIONS}.')
    functions = [{**item, 'id': f'F{index}', 'text': item['text'].strip()[:FUNCTION_CHARS]}
                 for index, item in enumerate(all_functions[:MAX_FUNCTIONS], 1)]
    progress('instruction', len(functions), len(functions))

    # Retrieval: one search per function plus searches by the position itself.
    binding = context.get('binding_document')
    position_queries = [query for query in dict.fromkeys(filter(None, (
        context.get('position_name'),
        f"{context.get('department_name')}: {context.get('position_name')}" if context.get('position_name') else None,
    )))]
    queries = [(item['id'], ' '.join(item['text'].split())[:QUERY_CHARS]) for item in functions]
    queries += [(None, query) for query in position_queries]
    clauses, keys, documents = {}, {}, Counter()
    candidates = {item['id']: [] for item in functions}
    filtered_out = 0
    for done, (function_id, query) in enumerate(queries, 1):
        result = await caller.search(query)
        for chunk in result.chunks:
            metadata = chunk.metadata or {}
            document = metadata.get('document') if isinstance(metadata.get('document'), str) else None
            documents[document or 'Документ не указан'] += 1
            if binding and document != binding:
                filtered_out += 1
                continue
            key = (document, str(metadata.get('chapter') or ''), str(metadata.get('clause') or ''), normalize(chunk.text))
            if key not in keys:
                clause_id = f'C{len(keys) + 1}'
                keys[key] = clause_id
                clauses[clause_id] = {'id': clause_id, 'text': chunk.text, 'score': chunk.score, 'document': document,
                    **{name: str(metadata[name]) for name in ('chapter', 'clause', 'version', 'source_file')
                       if metadata.get(name) is not None}}
            clause_id = keys[key]
            clauses[clause_id]['score'] = max(filter(lambda value: value is not None,
                                                     (clauses[clause_id]['score'], chunk.score)), default=None)
            if function_id and clause_id not in candidates[function_id]:
                candidates[function_id].append(clause_id)
        progress('retrieval', done, len(queries))
    for function_id in candidates:
        candidates[function_id] = candidates[function_id][:CANDIDATES_PER_FUNCTION]
    if not binding and len([name for name in documents if name != 'Документ не указан']) > 1:
        warnings.append('Поиск вернул пункты нескольких документов Nova. Закрепите положение департамента, '
                        'чтобы анализ учитывал только его.')
    if binding and filtered_out:
        warnings.append(f'Исключено {filtered_out} найденных пунктов других документов.')

    # Matching: functions without any candidate need no model call.
    results = {}
    with_candidates = [item for item in functions if candidates[item['id']]]
    for item in functions:
        if not candidates[item['id']]:
            results[item['id']] = {'status': 'not_found', 'clause_ids': [], 'quotes': [],
                'explanation': 'Поиск по базе положений не нашёл пунктов, относящихся к этой функции.',
                'recommendation': 'Проверьте, предусмотрена ли функция Положением о департаменте; '
                                  'при отсутствии — согласуйте её закрепление или исключите из инструкции.',
                'decided_by': 'search'}
    batches = [with_candidates[start:start + MATCH_BATCH] for start in range(0, len(with_candidates), MATCH_BATCH)]
    progress('matching', 0, len(batches))
    for done, batch in enumerate(batches, 1):
        ids = [item['id'] for item in batch]
        clause_ids = list(dict.fromkeys(cid for item in batch for cid in candidates[item['id']]))[:MATCH_CLAUSES]
        payload = {'department': context.get('department_name'), 'position': context.get('position_name'),
                   'functions': [{'id': item['id'], 'text': item['text']} for item in batch],
                   'clauses': [_clause_view(clauses[cid]) for cid in clause_ids]}

        def validate(data, ids=ids, clause_ids=clause_ids):
            items = [_Match.model_validate(item) for item in data['results']]
            found = {}
            for item in items:
                if item.function_id in ids and item.status in MATCH_STATUSES and item.function_id not in found:
                    found[item.function_id] = item
            if not found:
                raise ValueError('нет результатов по переданным функциям')
            return found

        answer = await _model_json(caller, MATCH_SYSTEM, payload, validate) or {}
        for function_id in ids:
            item = answer.get(function_id)
            if item is None:
                results[function_id] = {'status': 'unresolved', 'clause_ids': [], 'quotes': [],
                    'explanation': 'Модель не вернула проверяемый результат по этой функции.',
                    'recommendation': 'Повторите анализ или проверьте функцию вручную.', 'decided_by': 'none'}
                continue
            cited = [cid for cid in dict.fromkeys(item.clause_ids) if cid in clause_ids]
            quotes = []
            for quote in item.quotes:
                if quote.clause_id in clause_ids and quote.quote.strip():
                    quotes.append({'clause_id': quote.clause_id, 'quote': quote.quote.strip(),
                                   'verified': quote_verified(quote.quote, clauses[quote.clause_id]['text'])})
                    if quote.clause_id not in cited:
                        cited.append(quote.clause_id)
            status = item.status
            if status != 'not_found' and not cited:
                status = 'unresolved'
            results[function_id] = {'status': status, 'clause_ids': cited if status != 'not_found' else [],
                'quotes': quotes if status != 'not_found' else [], 'explanation': item.explanation.strip(),
                'recommendation': item.recommendation.strip() if status != 'covered' else '', 'decided_by': 'model'}
        progress('matching', done, len(batches))

    # Reverse: clauses found for this position but matched to no function.
    used = {cid for result in results.values() if result['status'] in ('covered', 'partial', 'conflict')
            for cid in result['clause_ids']}
    unmatched = sorted((cid for cid in clauses if cid not in used),
                       key=lambda cid: -(clauses[cid]['score'] or 0))[:REVERSE_LIMIT]
    reverse_batches = [unmatched[start:start + REVERSE_BATCH] for start in range(0, len(unmatched), REVERSE_BATCH)]
    progress('reverse', 0, len(reverse_batches))
    missing, reverse_errors = [], 0
    compact = [{'id': item['id'], 'text': item['text'][:300]} for item in functions]
    function_ids = {item['id'] for item in functions}
    for done, batch in enumerate(reverse_batches, 1):
        payload = {'department': context.get('department_name'), 'position': context.get('position_name'),
                   'functions': compact, 'clauses': [_clause_view(clauses[cid]) for cid in batch]}

        def validate(data, batch=batch):
            items = {}
            for item in (_Reverse.model_validate(value) for value in data['results']):
                if item.clause_id in batch and item.status in REVERSE_STATUSES and item.clause_id not in items:
                    items[item.clause_id] = item
            if not items:
                raise ValueError('нет результатов по переданным пунктам')
            return items

        answer = await _model_json(caller, REVERSE_SYSTEM, payload, validate)
        if answer is None:
            reverse_errors += len(batch)
        for clause_id, item in (answer or {}).items():
            if item.status != 'missing':
                continue
            quote = item.quote.strip()
            missing.append({'id': f'M{len(missing) + 1}', 'clause_id': clause_id,
                'quote': quote, 'quote_verified': bool(quote) and quote_verified(quote, clauses[clause_id]['text']),
                'related_function_ids': [fid for fid in item.function_ids if fid in function_ids],
                'explanation': item.explanation.strip(), 'recommendation': item.recommendation.strip()})
        progress('reverse', done, len(reverse_batches))
    if reverse_errors:
        warnings.append(f'Для {reverse_errors} пунктов положения модель не вернула проверяемый ответ.')

    counts = Counter(result['status'] for result in results.values())
    counts = {status: counts.get(status, 0) for status in (*MATCH_STATUSES, 'unresolved')}
    counts['missing'] = len(missing)
    unverified = sum(1 for result in results.values() for quote in result['quotes'] if not quote['verified'])
    unverified += sum(1 for item in missing if item['quote'] and not item['quote_verified'])
    if unverified:
        warnings.append(f'{unverified} цитат модели не найдены в тексте пунктов дословно — проверьте их вручную.')

    progress('summary', 0, 1)
    summary = await _summary(caller, context, functions, results, missing, counts)
    progress('summary', 1, 1)

    return {
        'method_version': METHOD_VERSION,
        'position': context.get('position_name'), 'department': context.get('department_name'),
        'regulation': {'source': 'nova', 'binding_document': binding,
                       'documents': [{'name': name, 'clauses': count} for name, count in documents.most_common()],
                       'clauses_found': len(clauses), 'complete': False},
        'counts': counts,
        'functions': [{'id': item['id'], 'text': item['text'], 'label': item.get('label'),
                       'locator': item.get('locator'), **results[item['id']]} for item in functions],
        'missing': missing,
        'clauses': clauses,
        'summary': summary,
        'warnings': warnings,
        'requests': {'search': sum(1 for item in caller.trace if item['kind'] == 'search'),
                     'model': sum(1 for item in caller.trace if item['kind'] == 'model'),
                     'trace': caller.trace[-300:]},
    }


async def _summary(caller, context, functions, results, missing, counts):
    texts = {item['id']: item['text'] for item in functions}
    issues = [{'status': result['status'], 'function': texts[function_id][:200], 'explanation': result['explanation'][:300]}
              for function_id, result in results.items() if result['status'] != 'covered'][:30]
    issues += [{'status': 'missing_in_instruction', 'explanation': item['explanation'][:300]} for item in missing[:10]]
    payload = {'department': context.get('department_name'), 'position': context.get('position_name'),
               'functions_total': len(functions), 'counts': counts, 'positions_complete': False, 'issues': issues}
    answer = await _model_json(caller, SUMMARY_SYSTEM, payload, lambda data: _Summary.model_validate(data))
    if answer:
        return {'text': answer.summary.strip(), 'key_points': [x.strip() for x in answer.key_points if x.strip()][:5],
                'recommendations': [x.strip() for x in answer.recommendations if x.strip()][:5], 'generated_by': 'model'}
    total = len(functions)
    text = (f"Из {total} функций инструкции {counts['covered']} предусмотрены Положением, {counts['partial']} — частично, "
            f"для {counts['not_found']} основание не найдено, {counts['conflict']} противоречат Положению. "
            f"Найдено {counts['missing']} пунктов Положения, не отражённых в инструкции.")
    return {'text': text, 'key_points': [], 'recommendations': [], 'generated_by': 'rules'}
