import argparse
import copy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import time
from zoneinfo import ZoneInfo
from app.analysis import AI, AnalysisError, candidate
from app.deadlines import reminders
from app.documents import extract_pdf, DocumentError
from app.net import Web, FetchError
from app.source import discover, parse_detail, digest, SourceError

VERSION = 1

def load_state(path):
    if not path.exists():
        return {'schema':VERSION, 'notices':{}, 'sources':{}, 'notified':[], 'baseline_complete':False}
    data = json.loads(path.read_text())
    if data.get('schema') != VERSION or not isinstance(data.get('notices'), dict) or not isinstance(data.get('sources'), dict) or not isinstance(data.get('notified'), list):
        raise ValueError('Neplatný stav; automatický reset je zakázán.')
    return data

def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')
    temp.replace(path)

def safe(text):
    return str(text).replace('<','&lt;').replace('>','&gt;').replace('[','\\[').replace(']','\\]').replace('\n',' ')

def link(label, url):
    from urllib.parse import quote, urlsplit
    if urlsplit(url).scheme != 'https':
        return safe(label)
    return '['+safe(label)+']('+quote(url, safe=':/?&=%#@+,-._~')+')'

def make_report(state, now, new, pending, due, baseline, errors, ai):
    lines = ['# Monitoring úředních desek', '', f'Kontrola: {now.isoformat(timespec="minutes")}', '',
             'Režim: '+('první kontrola — výchozí přehled' if baseline else 'porovnání s předchozí kontrolou'),
             f'AI: {ai.provider}; volání v tomto běhu: {ai.calls}.', '', '## Stav zdrojů', '']
    for s in state['sources'].values():
        lines.append(f'- {safe(s["name"])}: {safe(s["status"])}; položek: {s.get("count",0)}; '+safe(s.get('error','')))
    lines += ['', 'Výpadek zdroje nebo neúplná analýza neznamenají, že nejsou změny.', '', '## Nové a změněné nálezy', '']
    for key in new:
        n=state['notices'][key];a=n['analysis']
        lines += ['### '+safe(n['source_name']+' — '+n['title']), '', safe(a['summary']), '',
                  f'Téma: {safe(a["topic"])}; fáze: {safe(a["event_type"])}.', '', link('Stránka na úřední desce', n['url'])]
        for u in n['attachment_urls']:
            lines.append('- '+link('Původní příloha',u))
        for e in a['evidence']:
            lines.append(f'- Důkaz (str. {e["page"]}, 0 = HTML): „{safe(e["quote"])}“ — '+link('zdroj',e['document_url']))
        for d in a['deadlines']:
            lines.append('- Termín: '+safe(d['date'] or 'neověřeno — nutná kontrola')+' '+safe(d.get('time') or '')+'; '+safe(d['kind']))
        if n.get('history'):
            previous=n['history'][-1]
            if previous.get('previous_summary'):
                lines.append('- Předchozí závěr: '+safe(previous['previous_summary']))
            for old_deadline in previous.get('previous_deadlines',[]):
                lines.append('- Předchozí termín: '+safe(old_deadline.get('date') or 'neověřený')+'; '+safe(old_deadline['kind']))
        for u in a['uncertainties']:
            lines.append('- K ověření: '+safe(u))
        lines.append('')
    if not new:
        lines += ['Žádný nový dokončený relevantní nález. Rozpracované položky jsou uvedeny níže.', '']
    lines += ['## Připomínky ověřených termínů', '']
    for d in due:
        lines.append(f'- **{safe(d["date"])}** ({d["remaining"]} dní): '+link(d['title'], d['url'])+' — '+link('dokument',d['document_url'])+f'; str. {d["page"]}: „{safe(d["quote"])}“')
    if not due:
        lines.append('Žádná splatná připomínka.')
    lines += ['', '## Čeká na analýzu / ruční kontrolu', '']
    for key in pending:
        n=state['notices'][key]
        lines.append('- '+link(n['title'],n['url'])+': '+safe(n.get('error','Neúplné zpracování.')))
        for u in n.get('attachment_urls',[]):
            lines.append('  - '+link('Původní příloha',u))
    if not pending:
        lines.append('Žádné nevyřízené položky.')
    lines += ['', '## Přehled známých relevantních dokumentů', '']
    known=[n for n in state['notices'].values() if n.get('status')=='analyzed' and n.get('analysis',{}).get('relevant')]
    for n in known:
        lines.append('- '+link(n['source_name']+' — '+n['title'],n['url'])+': '+safe(n['analysis']['summary']))
        for d in n['analysis']['deadlines']:
            lines.append('  - '+safe(d['kind'])+': '+safe(d['date'] or 'k ověření')+'; '+link('zdroj',d['document_url']))
    if not known:
        lines.append('Dosud žádný dokončený relevantní výsledek AI.')
    lines += ['', 'Dokumenty se nearchivují. Odkazy mohou po sejmutí přestat fungovat. AI výstup je orientační; před podáním ověřte originál.']
    return '\n'.join(lines)+'\n'

def run(config, state, web_factory=Web, ai=None, now=None):
    ai = ai or AI()
    now = now or datetime.now(ZoneInfo('Europe/Prague'))
    state = copy.deepcopy(state)
    baseline = not state['baseline_complete']
    new, errors = [], []
    for source in config:
        sid=source['id']; web=web_factory(source['allowed_hosts'])
        stats={'name':source['name'],'checked_at':now.isoformat(),'status':'ok','count':0}
        try:
            notices = discover(source,web);stats['count']=len(notices)
            print(f'{sid}: nalezeno {len(notices)} položek.',flush=True)
            for index,n in enumerate(notices,1):
                print(f'{sid}: zpracování {index}/{len(notices)} (ID {n["id"]}).',flush=True)
                key=sid+':'+n['id']; old=state['notices'].get(key)
                entry={**(old or {}),**n,'source_name':source['name'],'source_id':sid,
                       'first_seen':(old or {}).get('first_seen',now.isoformat()),'last_seen':now.isoformat()}
                try:
                    time.sleep(.15)
                    raw=web.get(n['url'],3_000_000)
                    text, urls=parse_detail(raw,n['url'])
                    # Raw files live only in memory / TemporaryDirectory.
                    files=[]
                    for url in urls:
                        time.sleep(.15);data=web.get(url)
                        files.append((url,digest(data),data))
                    fingerprint=digest(json.dumps([n['title'],text,[(u,h) for u,h,_ in files]],ensure_ascii=False).encode())
                    entry.update(fingerprint=fingerprint,attachment_urls=urls)
                    changed=not old or old.get('fingerprint')!=fingerprint
                    if not changed and old.get('status') in {'analyzed','irrelevant'}:
                        state['notices'][key]=entry;continue
                    # Supersede the previous analysis immediately on content change.
                    entry.pop('analysis',None);entry.pop('error',None)
                    if changed and old:
                        history=old.get('history',[])[-19:]
                        entry['history']=history+[{'observed_at':now.isoformat(),'previous_fingerprint':old.get('fingerprint'),
                                                   'previous_summary':old.get('analysis',{}).get('summary'),
                                                   'previous_deadlines':old.get('analysis',{}).get('deadlines',[])}]
                    documents=[{'url':n['url'],'pages':[{'page':0,'text':n['title']+'\n'+text}]}]
                    # Read attachments even when title alone does not look relevant.
                    for url,_,data in files:
                        if not data.startswith(b'%PDF'):
                            raise DocumentError('Nepodporovaná příloha; automaticky se zpracovává pouze PDF.')
                        documents.append({'url':url,'pages':extract_pdf(data)})
                    relevant=candidate(' '.join(p['text'] for d in documents for p in d['pages']))
                    if relevant:
                        entry['analysis']=ai.analyze(documents);entry['status']='analyzed';entry['analyzed_at']=now.isoformat()
                        if entry['analysis']['relevant']:
                            new.append(key)
                    else:
                        entry['status']='irrelevant'
                except (FetchError,SourceError,DocumentError,AnalysisError,ValueError) as e:
                    entry['status']='pending';entry['error']=str(e)
                    # No reminder from an obsolete/unverified analysis.
                    entry.pop('analysis',None)
                    if not isinstance(e,AnalysisError):
                        stats['status']='partial';errors.append(sid+':'+str(e))
                state['notices'][key]=entry
        except (FetchError,SourceError,ValueError) as e:
            stats.update(status='failed',error=str(e));errors.append(sid+':'+str(e))
        finally:
            web.close()
        if stats['status']=='ok':
            stats['last_success']=now.isoformat()
        else:
            stats['last_success']=state['sources'].get(sid,{}).get('last_success')
        state['sources'][sid]=stats
    active={s['id'] for s in config}
    # Do not send reminders for disabled or currently inaccessible sources.
    eligible={k:n for k,n in state['notices'].items() if n['source_id'] in active and state['sources'][n['source_id']]['status']=='ok'}
    due=[d for d in reminders(eligible,now.date()) if 'reminder:'+d['id'] not in state['notified']]
    pending=[k for k,n in state['notices'].items() if n['source_id'] in active and n.get('status')=='pending']
    state['baseline_complete']=state.get('baseline_complete',False) or any(s.get('count',0)>0 for s in state['sources'].values())
    state['updated_at']=now.isoformat()
    current_events={k:'event:'+k+':'+n['fingerprint'] for k,n in eligible.items() if n.get('status')=='analyzed' and n.get('analysis',{}).get('relevant')}
    if baseline:
        state['notified']=sorted(set(state['notified']+list(current_events.values())))
    else:
        new=list(dict.fromkeys(new+[k for k,ident in current_events.items() if ident not in state['notified']]))
    report=make_report(state,now,new,pending,due,baseline,errors,ai)
    events=[]
    if not baseline:
        events += ['event:'+k+':'+state['notices'][k]['fingerprint'] for k in new]
    events += ['reminder:'+d['id'] for d in due]
    # Operational failures and AI backlog are visible in the report; daily Issue is the inbox.
    has_pending=bool(pending or errors)
    notify_ids=[x for x in events if x not in state['notified']]
    notification={'ids':notify_ids,'body':report,'date':now.date().isoformat(),'needed':bool(notify_ids or has_pending or baseline),
                  'baseline':baseline}
    return state,report,notification,errors

def main():
    p=argparse.ArgumentParser(description='Denní monitoring úředních desek bez archivu.')
    p.add_argument('--config',default='config/sources.json');p.add_argument('--state',default='state/state.json');p.add_argument('--output',default='output')
    args=p.parse_args();path=Path(args.state);out=Path(args.output)
    config=json.loads(Path(args.config).read_text())
    if not config or len({s['id'] for s in config})!=len(config):
        raise ValueError('Konfigurace musí obsahovat zdroje s jedinečnými ID.')
    result,report,notification,errors=run(config,load_state(path))
    write_json(path,result);out.mkdir(parents=True,exist_ok=True)
    (out/'latest.md').write_text(report);write_json(out/'notification.json',notification)
    print(f'Hotovo: {len(result["notices"])} položek; report {out / "latest.md"}; chyby zdrojů: {len(errors)}.')
    return 1 if errors else 0

if __name__=='__main__':
    raise SystemExit(main())
