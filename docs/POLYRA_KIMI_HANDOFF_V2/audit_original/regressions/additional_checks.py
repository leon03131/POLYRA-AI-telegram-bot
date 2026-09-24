"""Isolated checks against audited source. No external API or production DB access.
SQLite reproduces declarative CASCADE semantics only; it is NOT PostgreSQL integration.
"""
import asyncio, json, sys, tempfile, uuid
from pathlib import Path
from types import SimpleNamespace
root=Path(sys.argv[1]).resolve(); out=Path(sys.argv[2]).resolve();sys.path.insert(0,str(root))
import httpx
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session
from app.api.app import create_app
from app.config import Settings
from app.db.base import Base
from app.db.models import User, Chat, GenerationRun
from app.llm.registry import default_registry
from app.security.crypto import CryptoBox
from app.services.credentials import get_provider_api_key

async def main():
    checks=[]
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp,'index.html').write_text('<html>miniapp</html>')
        app=create_app(settings=Settings(_env_file=None,miniapp_dist=tmp),session_factory=None,crypto=CryptoBox('audit-only-test-key'),registry=default_registry())
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://audit') as c:
            good=await c.get('/'); bad=await c.get('/admin')
        assert good.status_code==200 and bad.status_code==404
        checks.append({'id':'ADMIN_ROUTE_404','status':'reproduced','root':good.status_code,'admin':bad.status_code,'scope':'Production create_app static mount with minimal index.html, not frontend build.'})
    class FakeSession:
        async def execute(self,*args):return SimpleNamespace(scalar_one_or_none=lambda:SimpleNamespace(enabled=False,encrypted_api_key='unused'))
    key=await get_provider_api_key(FakeSession(),CryptoBox('audit-only-test-key'),'alibaba','DEMO_ENV_KEY_NOT_A_SECRET')
    assert key=='DEMO_ENV_KEY_NOT_A_SECRET'
    checks.append({'id':'DISABLED_DB_CREDENTIAL_USES_ENV','status':'reproduced','returned_env_fallback':True})
    engine=create_engine('sqlite://')
    @event.listens_for(engine,'connect')
    def foreign_keys(dbapi_connection,record):dbapi_connection.execute('PRAGMA foreign_keys=ON')
    Base.metadata.create_all(engine,tables=[User.__table__,Chat.__table__,GenerationRun.__table__])
    with Session(engine) as s:
        u=User(telegram_user_id=123456,first_name='Audit',status='active');s.add(u);s.flush()
        ch=Chat(owner_user_id=u.id);s.add(ch);s.flush()
        run=GenerationRun(chat_id=ch.id,user_id=u.id,provider='alibaba',model_id='kimi-k3',status='completed',input_tokens=100,output_tokens=200);s.add(run);s.commit()
        def usage():return s.execute(select(func.count(GenerationRun.id),func.sum(GenerationRun.input_tokens+GenerationRun.output_tokens)).where(GenerationRun.user_id==u.id)).one()
        before=tuple(usage());s.delete(ch);s.commit();after=tuple(usage())
        assert before==(1,300) and after==(0,None)
        checks.append({'id':'DELETE_CHAT_ERASES_USAGE_LEDGER','status':'reproduced','before_count_tokens':before,'after_count_tokens':after,'scope':'Original declarative models on SQLite with foreign_keys=ON; corroborated by migration 0002 CASCADE.'})
    engine.dispose()
    (out/'additional_checks.json').write_text(json.dumps(checks,indent=2))
    print(json.dumps(checks,indent=2))
asyncio.run(main())
