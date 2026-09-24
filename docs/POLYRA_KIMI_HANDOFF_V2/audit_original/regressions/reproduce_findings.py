"""Offline diagnostics against the supplied snapshot. No real APIs or DB.

Usage: python reproduce_findings.py /path/to/POLYRA-AI-telegram-bot-master
The output records observed defects; 'reproduced' is NOT a quality pass.
Production classes are imported without modifications. Test doubles implement
storage and provider interfaces only. No aiogram substitution is used.
"""
from __future__ import annotations
import asyncio
import json
import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT))
from tests.unit.test_gemini_pool import (
    FakeProvider, FakeQuotaStore, _make_pool, _project, _request, NOW, MODEL,
)
from app.llm.events import Done, TextDelta, Usage
from app.llm.gemini.quota import QuotaTracker, QuotaLimits, current_minute, pacific_day
from app.llm.providers.alibaba import parse_chat_completions_sse
from app.context.builder import ContextBuilder
from app.context.token_budget import TokenBudgetManager
from app.context.compactor import _normalize_summary
from app.llm.capabilities import ModelDefinition
from app.db.repositories.access import ModelPermissionRepository
from app.db.repositories.messages import MessageRepository
from app.services.access import evaluate_access, GrantView, is_model_allowed

async def diagnostic_pool_break():
    pool, store, quotas = _make_pool([_project('diagnostic')])
    provider = FakeProvider()
    provider.push(TextDelta('hello'), Usage(input_tokens=123), Done('stop'))
    stream = pool.stream_with_failover(provider, _request(), now_fn=lambda: NOW)
    async for event in stream:
        if isinstance(event, Done):
            break  # same consumption policy as GenerationService._consume
    await stream.aclose()
    return {'health_success_calls': len(store.calls.mark_success),
            'token_reconcile_calls': len(quotas.add_tokens_calls),
            'reproduced': not store.calls.mark_success and not quotas.add_tokens_calls}

async def diagnostic_trailing_usage():
    async def lines():
        yield 'data: ' + json.dumps({'choices': [{'delta': {'content': 'ok'}, 'finish_reason': None}]})
        yield 'data: ' + json.dumps({'choices': [{'delta': {}, 'finish_reason': 'stop'}]})
        yield 'data: ' + json.dumps({'choices': [], 'usage': {'prompt_tokens': 123, 'completion_tokens': 45}})
        yield 'data: [DONE]'
    seen = []
    stream = parse_chat_completions_sse(lines())
    async for event in stream:
        seen.append(type(event).__name__)
        if isinstance(event, Done):
            break
    await stream.aclose()
    all_events = [type(event).__name__ async for event in parse_chat_completions_sse(lines())]
    return {'consumer_events': seen, 'fully_drained_events': all_events,
            'reproduced': 'Usage' not in seen and 'Usage' in all_events}

async def diagnostic_quota_race():
    class RacingStore(FakeQuotaStore):
        def __init__(self):
            super().__init__()
            self.barrier = asyncio.Barrier(2)
        async def get_daily_usage(self, project_id, model_id, day):
            snapshot = await super().get_daily_usage(project_id, model_id, day)
            await self.barrier.wait()  # both transactions read before either reserves
            return snapshot
    store = RacingStore()
    tracker = QuotaTracker(store, QuotaLimits(rpm=1, rpd=1))
    pid = uuid4()
    accepted = await asyncio.gather(*[tracker.check_and_reserve(pid, MODEL, now=NOW) for _ in range(2)])
    return {'limit': 1, 'accepted': accepted, 'reservations': len(store.reserve_calls),
            'reproduced': accepted == [True, True]}

async def diagnostic_quota_window():
    store = FakeQuotaStore()
    tracker = QuotaTracker(store, QuotaLimits())
    pid = uuid4()
    await tracker.check_and_reserve(pid, MODEL, now=NOW)
    later = NOW + timedelta(minutes=1)
    await tracker.reconcile(pid, MODEL, input_tokens=123, now=later)
    original = store.minute[(pid, MODEL, current_minute(NOW))]
    finished = store.minute[(pid, MODEL, current_minute(later))]
    return {'original_window': original, 'finish_window': finished,
            'reproduced': original.get('tokens_in', 0) == 0 and finished['tokens_in'] == 123}

async def diagnostic_empty_permissions():
    repo = ModelPermissionRepository(None)
    async def no_rows(_user_id): return []
    repo.get_for_user = no_rows
    allowed = await repo.allowed_model_ids(uuid4())
    permissions = evaluate_access(is_owner=False, user_status='active',
        grant=GrantView(status='active', expires_at=None, requests_per_day=None,
            token_limit=None, max_concurrent_generations=1, can_use_web_search=False, can_use_memory=False),
        allowed_models=allowed, now=NOW)
    return {'no_permission_rows_returns': allowed, 'arbitrary_model_allowed': is_model_allowed(permissions, 'any-model'),
            'reproduced': is_model_allowed(permissions, 'any-model')}

def diagnostic_context_summary_gap():
    history = [SimpleNamespace(role='user', parts=[SimpleNamespace(type='text', text=f'UNSUMMARIZED-{i}')]) for i in range(20)]
    builder = ContextBuilder(TokenBudgetManager(), keep_recent=10)
    result = builder.build(model=ModelDefinition(provider='gemini', model_id='test', display_name='test'),
        base_system_prompt='', summary_json={'conversation_summary': 'Summary of earlier messages, NOT these twenty'},
        memories=[], history=history)
    rendered = json.dumps(result.messages)
    return {'input_messages': len(history), 'output_messages': len(result.messages),
            'needs_compaction': result.needs_compaction, 'dropped_oldest_reported': result.dropped_oldest,
            'reproduced': len(result.messages) == 10 and not result.needs_compaction and 'UNSUMMARIZED-0' not in rendered}

def diagnostic_budget():
    model = ModelDefinition(provider='gemini', model_id='small', display_name='small', max_context=100)
    budget = TokenBudgetManager(chars_per_token=1, reserved_output=10, safety_margin=10)
    result = ContextBuilder(budget).build(model=model, base_system_prompt='', summary_json=None, memories=[],
        history=[SimpleNamespace(role='user', parts=[SimpleNamespace(type='text', text='x'*1000)])])
    estimated = sum(budget.estimate_message(msg) for msg in result.messages)
    return {'available_tokens': budget.budget_for(model, None).available, 'sent_tokens_estimate': estimated,
            'needs_compaction': result.needs_compaction, 'reproduced': estimated > 100 and not result.needs_compaction}

async def diagnostic_photo_persistence():
    class Session:
        def add(self, value): self.value = value
        async def flush(self): pass
    session = Session()
    message = await MessageRepository(session).add_message(uuid4(), 'user',
        parts=[{'type': 'image', 'mime_type': 'image/jpeg', 'data_base64': 'ZmFrZQ=='}])
    part = message.parts[0]
    return {'telegram_file_id': part.telegram_file_id, 'metadata': part.metadata_json,
            'reproduced': part.telegram_file_id is None and part.text is None}

def diagnostic_empty_summary():
    result = _normalize_summary({})
    return {'normalized_empty_object': result,
            'reproduced': not result['conversation_summary'] and not any(result[k] for k in result if k != 'conversation_summary')}

async def main():
    results = {}
    for name, fn in [('gemini_done_skips_reconcile', diagnostic_pool_break),
                     ('alibaba_trailer_usage_lost', diagnostic_trailing_usage),
                     ('quota_check_reserve_race', diagnostic_quota_race),
                     ('quota_reconcile_wrong_window', diagnostic_quota_window),
                     ('empty_permissions_mean_unrestricted', diagnostic_empty_permissions),
                     ('photo_persistence_has_no_reference', diagnostic_photo_persistence)]:
        results[name] = await fn()
    results['summary_discards_uncovered_messages'] = diagnostic_context_summary_gap()
    results['recent_messages_exceed_budget'] = diagnostic_budget()
    results['empty_summary_is_accepted_by_normalizer'] = diagnostic_empty_summary()
    print(json.dumps(results, ensure_ascii=True, indent=2))
    assert all(result['reproduced'] for result in results.values()), 'One observation changed; review output.'

if __name__ == '__main__':
    asyncio.run(main())
