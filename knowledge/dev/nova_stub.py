"""Local imitation of the Nova RAG Gateway for development and tests.

Implements the documented contract (GET /health, GET /v1/models,
POST /retrieve, POST /v1/chat/completions with use_rag=false) over a small
synthetic regulation. Its "model" applies simple word overlap, so results
are deterministic. It is never a measure of the bank model's quality.

Run: uvicorn dev.nova_stub:app --port 11450  (from the knowledge folder)
"""
import json
import os
import re
import uuid

from fastapi import FastAPI, Header, HTTPException, Request

COLLECTION = os.environ.get('NOVA_STUB_COLLECTION', 'ops_policies')
KEY = os.environ.get('NOVA_STUB_KEY', 'stub-key')
# Seconds per model answer, to watch progress as with the real model.
DELAY = float(os.environ.get('NOVA_STUB_DELAY', '0'))
DOCUMENT = 'Operatsion departament to‘g‘risida nizom (sintetik namuna)'
OTHER = 'Kadrlar departamenti to‘g‘risida nizom (sintetik namuna)'
CLAUSES = [
    (DOCUMENT, '2-bob', '8-band', 'Departament mijozlarning to‘lov hujjatlarini qabul qiladi va ularning '
     'belgilangan talablarga muvofiqligini tekshiradi. Проверка платёжных документов клиентов на соответствие требованиям.'),
    (DOCUMENT, '2-bob', '9-band', 'Departament milliy va xorijiy valyutada to‘lovlarni belgilangan limitlar doirasida '
     'amalga oshiradi. Проведение платежей в национальной и иностранной валюте в пределах установленных лимитов.'),
    (DOCUMENT, '2-bob', '10-band', 'Departament to‘lovlarni bajarishdan rad etishlar hisobini yuritadi. '
     'Ведение учёта отказов в исполнении платежей.'),
    (DOCUMENT, '2-bob', '11-band', 'Departament rahbariyat uchun operatsiyalar bo‘yicha hisobotlarni tayyorlaydi. '
     'Подготовка отчётности по операциям для руководства.'),
    (DOCUMENT, '2-bob', '12-band', 'Departament to‘lov hujjatlarini belgilangan muddatlarda saqlashni ta’minlaydi. '
     'Хранение платёжных документов в установленные сроки.'),
    (OTHER, '3-bob', '4-band', 'Kadrlar departamenti xodimlarni ishga qabul qilish hujjatlarini rasmiylashtiradi. '
     'Оформление документов о приёме сотрудников на работу.'),
]
STOP = {'и', 'в', 'на', 'по', 'для', 'с', 'о', 'об', 'от', 'до', 'не', 'за', 'из', 'к', 'в', 'пределах',
        'va', 'bilan', 'uchun', 'departament', 'департамент'}

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


def _words(text):
    return {word for word in re.findall(r"[\w‘’'-]+", text.lower()) if len(word) > 3 and word not in STOP}


def _overlap(left, right):
    a, b = _words(left), _words(right)
    return len(a & b) / max(1, min(len(a), len(b)))


def _auth(authorization):
    if authorization != f'Bearer {KEY}':
        raise HTTPException(401, 'Invalid API key')


def _headers(request):
    return {'X-Request-ID': request.headers.get('x-request-id') or uuid.uuid4().hex}


@app.get('/health')
def health():
    return {'status': 'ok'}


@app.get('/v1/models')
def models(authorization: str = Header(default='')):
    _auth(authorization)
    return {'object': 'list', 'data': [{'id': COLLECTION, 'object': 'model', 'created': 0, 'owned_by': 'rag-gateway'}]}


def retrieve_chunks(query):
    ranked = sorted(((round(_overlap(query, text), 3), document, chapter, clause, text)
                     for document, chapter, clause, text in CLAUSES), reverse=True)
    return [{'index': index, 'score': score, 'text': text,
             'metadata': {'document': document, 'chapter': chapter, 'clause': clause, 'version': '2025-01',
                          'source_file': 'nizomlar/sintetik.md', 'language': 'uz'}}
            for index, (score, document, chapter, clause, text) in enumerate(
                [item for item in ranked if item[0] >= 0.15][:5], 1)]


@app.post('/retrieve')
async def retrieve(request: Request, authorization: str = Header(default='')):
    _auth(authorization)
    body = await request.json()
    if not str(body.get('query') or '').strip():
        raise HTTPException(422, 'query must not be empty')
    if body.get('collection') not in (None, COLLECTION):
        raise HTTPException(403, 'No access to collection')
    from fastapi.responses import JSONResponse
    return JSONResponse({'collection': COLLECTION, 'chunks': retrieve_chunks(body['query'])}, headers=_headers(request))


def _quote(text):
    words = text.split()
    return ' '.join(words[:10])


def _answer(system_prompt, data):
    if '"results":[{"function_id"' in system_prompt.replace(' ', ''):
        results = []
        for function in data['functions']:
            best = max(data['clauses'], key=lambda clause: _overlap(function['text'], clause['text']), default=None)
            score = _overlap(function['text'], best['text']) if best else 0
            status = 'covered' if score >= 0.5 else 'partial' if score >= 0.25 else 'not_found'
            results.append({'function_id': function['id'], 'status': status,
                'clause_ids': [best['id']] if status != 'not_found' else [],
                'quotes': [{'clause_id': best['id'], 'quote': _quote(best['text'])}] if status != 'not_found' else [],
                'explanation': {'covered': 'Функция прямо предусмотрена пунктом Положения.',
                                'partial': 'Положение предусматривает функцию в ином объёме.',
                                'not_found': 'Среди найденных пунктов нет основания для функции.'}[status],
                'recommendation': '' if status == 'covered' else 'Уточните формулировку функции по Положению.'})
        return {'results': results}
    if '"results":[{"clause_id"' in system_prompt.replace(' ', ''):
        results = []
        for clause in data['clauses']:
            best = max(data['functions'], key=lambda function: _overlap(function['text'], clause['text']), default=None)
            score = _overlap(best['text'], clause['text']) if best else 0
            related = clause.get('document') == DOCUMENT
            status = 'covered' if score >= 0.4 else 'missing' if related else 'not_applicable'
            results.append({'clause_id': clause['id'], 'status': status,
                'function_ids': [best['id']] if status == 'covered' else [], 'quote': _quote(clause['text']),
                'explanation': 'Функция департамента не отражена в инструкции.' if status == 'missing' else '',
                'recommendation': 'Рассмотрите включение функции в инструкцию.' if status == 'missing' else ''})
        return {'results': results}
    counts = data.get('counts', {})
    return {'summary': f"Имитатор Nova: из {data.get('functions_total')} функций {counts.get('covered', 0)} "
                       f"соответствуют Положению, {counts.get('missing', 0)} пунктов не отражены в инструкции.",
            'key_points': ['Результат получен имитатором Nova и не является оценкой банковской модели.'],
            'recommendations': ['Повторите анализ после подключения Nova.']}


@app.post('/v1/chat/completions')
async def chat(request: Request, authorization: str = Header(default='')):
    _auth(authorization)
    body = await request.json()
    if body.get('use_rag') is not False:
        raise HTTPException(400, 'Stub supports only use_rag=false')
    if any(message.get('role') == 'system' for message in body.get('messages', [])):
        raise HTTPException(400, 'system role is not allowed with system_prompt')
    if DELAY:
        import asyncio
        await asyncio.sleep(DELAY)
    content = body['messages'][-1]['content']
    payload = content.split('\n\nПредыдущий ответ')[0]
    data = json.loads(payload[payload.index('{'):])
    answer = json.dumps(_answer(body.get('system_prompt', ''), data), ensure_ascii=False)
    from fastapi.responses import JSONResponse
    return JSONResponse({'id': 'chatcmpl-stub', 'object': 'chat.completion',
        'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': answer}, 'finish_reason': 'stop'}],
        'usage': {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}}, headers=_headers(request))
