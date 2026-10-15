import copy
from datetime import datetime, date
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import pymupdf
import httpx
from app.analysis import validate, AnalysisError, AI, explicit_date, candidate
from app.cli import run, load_state, write_json
from app.deadlines import reminders
from app.documents import extract_pdf, DocumentError
from app.net import validate_url, FetchError
from app.source import parse_listing, SourceError

URL='https://www.example.com/board/example-123.html'
PDF='https://www.example.com/example.pdf'
TEXT='Verejne projednani navrhu zmeny uzemniho planu se kona 20. 11. 2026 v 17:00 hodin.'
DOCS=[{'url':PDF,'pages':[{'page':1,'text':TEXT}]}]
RESULT={'relevant':True,'topic':'municipal_plan','event_type':'hearing','summary':'Oznámeno projednání.',
        'evidence':[{'document_url':PDF,'page':1,'quote':TEXT}],
        'deadlines':[{'kind':'hearing','date':'2026-11-20','time':'17:00','document_url':PDF,'page':1,'quote':TEXT}],
        'uncertainties':[]}
SOURCE={'id':'test','name':'Test','adapter':'galileo','url':'https://www.example.com/board/',
        'lists':['https://www.example.com/board/'],'allowed_hosts':['www.example.com']}
LIST=f'<main id="gcm-main"><a class="item-href" href="{URL}">Změna územního plánu</a></main>'.encode()
DETAIL=f'<main id="gcm-main"><h1>Změna územního plánu</h1><p>{TEXT}</p><a class="file" href="{PDF}">PDF</a></main>'.encode()

def pdf(text=TEXT):
    with pymupdf.open() as d:
        p=d.new_page();p.insert_text((50,60),text,fontsize=9)
        return d.tobytes()

class FakeWeb:
    data={SOURCE['url']:LIST, URL:DETAIL, PDF:pdf()}
    def __init__(self,hosts):pass
    def get(self,url,limit=20_000_000):
        value=self.data[url]
        if isinstance(value,Exception):raise value
        return value
    def close(self):pass

class FakeAI:
    provider='test';calls=0
    def analyze(self,docs):
        self.calls+=1
        return validate(copy.deepcopy(RESULT),docs)

class Tests(unittest.TestCase):
    def setUp(self):
        self.pause=patch('time.sleep');self.pause.start()
        self.addCleanup(self.pause.stop)
        self.now=datetime(2026,11,13,8,tzinfo=__import__('zoneinfo').ZoneInfo('Europe/Prague'))
        self.empty={'schema':1,'notices':{},'sources':{},'notified':[],'baseline_complete':False}

    def test_listing_stable_id(self):
        self.assertEqual(parse_listing(LIST,SOURCE['url'])[0]['id'],'123')
    def test_broken_listing_fails(self):
        with self.assertRaises(SourceError):parse_listing(b'<html>maintenance</html>',SOURCE['url'])
    def test_pdf_extract_real(self):
        self.assertIn('20. 11. 2026',extract_pdf(pdf())[0]['text'])
    def test_corrupt_pdf(self):
        with self.assertRaises(DocumentError):extract_pdf(b'not a pdf')
    def test_valid_analysis(self):
        r=validate(copy.deepcopy(RESULT),DOCS)
        self.assertEqual(r['deadlines'][0]['verification'],'verified')
    def test_hallucinated_quote_rejected(self):
        r=copy.deepcopy(RESULT);r['evidence'][0]['quote']='Tento citat v dokumentu neexistuje.'
        with self.assertRaises(AnalysisError):validate(r,DOCS)
    def test_wrong_page_rejected(self):
        r=copy.deepcopy(RESULT);r['evidence'][0]['page']=2
        with self.assertRaises(AnalysisError):validate(r,DOCS)
    def test_wrong_document_rejected(self):
        r=copy.deepcopy(RESULT);r['evidence'][0]['document_url']='https://evil.example/fake.pdf'
        with self.assertRaises(AnalysisError):validate(r,DOCS)
    def test_relative_date_not_accepted(self):
        self.assertFalse(explicit_date('2026-11-20','do 30 dnů ode dne doručení 20. 11. 2026'))
    def test_missing_year_not_accepted(self):
        self.assertFalse(explicit_date('2026-11-20','20. listopadu'))
    def test_czech_date(self):
        self.assertTrue(explicit_date('2026-11-20','do 20. listopadu 2026'))
    def test_invented_date_unverified(self):
        r=copy.deepcopy(RESULT);r['deadlines'][0]['date']='2026-11-21'
        self.assertIsNone(validate(r,DOCS)['deadlines'][0]['date'])
    def test_non_planning_title(self):
        self.assertFalse(candidate('Veřejná vyhláška, opatření obecné povahy: regulace bolševníku velkolepého'))
    def test_private_endpoint_blocked(self):
        with patch('socket.getaddrinfo',return_value=[(2,1,6,'',('127.0.0.1',443))]):
            with self.assertRaises(FetchError):validate_url('https://example.com')
    def test_http_blocked(self):
        with self.assertRaises(FetchError):validate_url('http://example.com')
    def test_foreign_attachment_blocked(self):
        with self.assertRaises(FetchError):validate_url('https://example.com',['www.example.com'])
    def test_baseline_and_second_run(self):
        ai=FakeAI();s,report,n,errors=run([SOURCE],self.empty,FakeWeb,ai,self.now)
        self.assertFalse(errors);self.assertIn(PDF,report);self.assertIn(URL,report)
        self.assertEqual(len(s['notices']),1)
        s2,_,_,_=run([SOURCE],s,FakeWeb,ai,self.now)
        self.assertEqual(ai.calls,1)
        self.assertEqual(s2['notices']['test:123']['fingerprint'],s['notices']['test:123']['fingerprint'])
        self.assertNotIn('pages',json.dumps(s2))
        self.assertNotIn(TEXT,json.dumps(s2,ensure_ascii=False).split('"quote"')[0])
    def test_missing_ai_preserves_pending(self):
        with patch.dict('os.environ',{'AI_API_KEY':''}):
            s,report,n,_=run([SOURCE],self.empty,FakeWeb,AI(),self.now)
        self.assertEqual(s['notices']['test:123']['status'],'pending')
        self.assertIn('AI_API_KEY',report)
    def test_fetch_failure_does_not_remove_or_remind(self):
        s,_,_,_=run([SOURCE],self.empty,FakeWeb,FakeAI(),self.now)
        class Broken(FakeWeb):data={SOURCE['url']:FetchError('HTTP 503')}
        s2,_,n,errors=run([SOURCE],s,Broken,FakeAI(),self.now)
        self.assertIn('test:123',s2['notices']);self.assertTrue(errors)
        self.assertFalse(any(i.startswith('reminder:') for i in n['ids']))
    def test_replaced_pdf_is_new_version(self):
        s,_,_,_=run([SOURCE],self.empty,FakeWeb,FakeAI(),self.now)
        class Changed(FakeWeb):data={**FakeWeb.data,PDF:pdf(TEXT+' Updated file.')}
        s2,_,n,_=run([SOURCE],s,Changed,FakeAI(),self.now)
        self.assertNotEqual(s['notices']['test:123']['fingerprint'],s2['notices']['test:123']['fingerprint'])
        self.assertEqual(len(s2['notices']['test:123']['history']),1)
        self.assertTrue(any(i.startswith('event:') for i in n['ids']))
    def test_reminder_sent_once(self):
        s,_,n,_=run([SOURCE],self.empty,FakeWeb,FakeAI(),self.now)
        self.assertTrue(any(i.startswith('reminder:') for i in n['ids']))
        s['notified']+=n['ids']
        _,_,n2,_=run([SOURCE],s,FakeWeb,FakeAI(),self.now)
        self.assertEqual(n2['ids'],[])
    def test_expired_deadlines_not_reminded(self):
        s,_,_,_=run([SOURCE],self.empty,FakeWeb,FakeAI(),self.now)
        self.assertEqual(reminders(s['notices'],date(2026,11,21)),[])
    def test_pending_never_reminded(self):
        s,_,_,_=run([SOURCE],self.empty,FakeWeb,FakeAI(),self.now)
        s['notices']['test:123']['status']='pending'
        self.assertEqual(reminders(s['notices'],self.now.date()),[])
    def test_invalid_state_not_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'state.json';p.write_text('{"schema":99}')
            with self.assertRaises(ValueError):load_state(p)
    def test_state_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'state.json';write_json(p,self.empty)
            self.assertEqual(load_state(p),self.empty)
    def test_gemini_request_and_validation(self):
        answer={'candidates':[{'content':{'parts':[{'text':json.dumps(RESULT)}]}}]}
        with patch.dict('os.environ',{'AI_API_KEY':'test-key','AI_PROVIDER':'gemini'}),patch('app.analysis.validate_url'),patch('httpx.post',return_value=httpx.Response(200,json=answer)) as post:
            r=AI().analyze(DOCS)
            self.assertTrue(r['relevant'])
            self.assertEqual(post.call_args.kwargs['headers']['x-goog-api-key'],'test-key')
    def test_api_error_does_not_expose_secret(self):
        with patch.dict('os.environ',{'AI_API_KEY':'secret-example','AI_PROVIDER':'gemini'}),patch('app.analysis.validate_url'),patch('httpx.post',return_value=httpx.Response(429,text='secret-example')):
            with self.assertRaises(AnalysisError) as e:AI().analyze(DOCS)
            self.assertNotIn('secret-example',str(e.exception))
