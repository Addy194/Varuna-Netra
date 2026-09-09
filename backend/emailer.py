import asyncio, logging, os
from datetime import datetime, timezone
import resend
from db import db
logger=logging.getLogger('emailer')
EMAIL_MODE=os.environ.get('EMAIL_MODE','demo')

async def get_config():
    s=await db.settings.find_one({'key':'email'},{'_id':0}) or {}; key=s.get('resend_api_key') or os.environ.get('RESEND_API_KEY') or ''; mode=s.get('mode') or EMAIL_MODE
    if mode=='live' and not key: mode='demo'
    return {'api_key':key,'sender_email':s.get('sender_email') or os.environ.get('SENDER_EMAIL') or 'onboarding@resend.dev','enabled':s.get('enabled',True),'alerts_enabled':s.get('alerts_enabled',True),'alert_recipients':s.get('alert_recipients') or [],'mode':mode,'source':'settings' if s.get('resend_api_key') else ('env' if os.environ.get('RESEND_API_KEY') else 'demo-outbox'),'updated_at':s.get('updated_at'),'updated_by':s.get('updated_by'),'last_test':s.get('last_test')}

async def configured():
    c=await get_config(); return bool(c['enabled']) and (c['mode']=='demo' or bool(c['api_key']))

async def send_email(to,subject,html):
    c=await get_config()
    if not c['enabled']: return {'sent':False,'error':'email disabled','mode':c['mode']}
    if c['mode']=='demo':
        rec={'id':f'demo-{datetime.now(timezone.utc).timestamp():.0f}','to':to,'subject':subject,'html':html,'status':'queued-demo','created_at':datetime.now(timezone.utc)}; await db.demo_outbox.insert_one(dict(rec)); return {'sent':True,'id':rec['id'],'mode':'demo-outbox'}
    try:
        resend.api_key=c['api_key']; res=await asyncio.to_thread(resend.Emails.send,{'from':c['sender_email'],'to':[to],'subject':subject,'html':html}); return {'sent':True,'id':res.get('id') if isinstance(res,dict) else str(res),'mode':'live'}
    except Exception as e: return {'sent':False,'error':str(e)[:300],'mode':'live'}

async def record_test(result,to): await db.settings.update_one({'key':'email'},{'$set':{'last_test':{**result,'to':to,'at':datetime.now(timezone.utc)}}},upsert=True)

def reset_email_html(name,link): return f'<h2>VarunaNetra password reset</h2><p>Hello {name}, reset your password using <a href="{link}">this link</a>.</p>'
def test_email_html(name): return f'<div><h2>VarunaNetra delivery test</h2><p>Hello {name}, the notification channel is configured.</p></div>'
