"""Persist metadata and publish an inbox Issue using the workflow's GITHUB_TOKEN."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import httpx
from app.cli import load_state, write_json

class GitHubError(Exception):
    pass

class GitHub:
    def __init__(self):
        token=os.getenv('GITHUB_TOKEN','');repo=os.getenv('GITHUB_REPOSITORY','')
        if not token or not repo or len(repo.split('/'))!=2:
            raise GitHubError('Chybí GITHUB_TOKEN nebo GITHUB_REPOSITORY.')
        self.client=httpx.Client(base_url='https://api.github.com/repos/'+repo+'/',timeout=40,
                                 headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json',
                                          'X-GitHub-Api-Version':'2022-11-28'})

    def call(self,method,path,body=None,missing=False):
        try:
            r=self.client.request(method,path,json=body)
        except httpx.HTTPError:
            raise GitHubError('GitHub API: síťová chyba; stav nelze bezpečně uložit.') from None
        if missing and r.status_code==404:
            return None
        if r.status_code not in {200,201,204}:
            raise GitHubError(f'GitHub API HTTP {r.status_code} při {method} {path.split("?")[0]}; ověřte oprávnění a pravidla větví.')
        return r.json() if r.content else None

    def head(self):
        ref=self.call('GET','git/ref/heads/monitor-state',missing=True)
        return ref['object']['sha'] if ref else None

    def load(self,path):
        head=self.head()
        meta={'head':head}
        if head:
            commit=self.call('GET','git/commits/'+head);meta['tree']=commit['tree']['sha']
            obj=self.call('GET','contents/state/state.json?ref='+head)
            if obj['encoding']!='base64':
                raise GitHubError('Stav má nepodporované kódování.')
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(base64.b64decode(obj['content']))
            load_state(path)  # refuse malformed state; never silently reset
        else:
            # A remote failure is distinct from a valid missing state branch.
            if path.exists():
                raise GitHubError('Lokální stav existuje, ale vzdálená větev chybí; automatický reset zakázán.')
        write_json(path.parent/'remote.json',meta)

    def save(self,path,report):
        state=load_state(path)
        meta=json.loads((path.parent/'remote.json').read_text())
        if self.head()!=meta['head']:
            raise GitHubError('Stav změnil jiný běh; zápis zastaven.')
        tree_entries=[]
        files={'state/state.json':path.read_text(),'reports/latest.md':report.read_text(),
               'reports/'+state['updated_at'][:10]+'.md':report.read_text()}
        for name,content in files.items():
            blob=self.call('POST','git/blobs',{'content':content,'encoding':'utf-8'})
            tree_entries.append({'path':name,'mode':'100644','type':'blob','sha':blob['sha']})
        payload={'tree':tree_entries}
        if meta['head']:
            payload['base_tree']=meta['tree']
        tree=self.call('POST','git/trees',payload)
        commit=self.call('POST','git/commits',{'message':'Monitoring '+state['updated_at'],
                        'tree':tree['sha'],'parents':[meta['head']] if meta['head'] else []})
        if meta['head']:
            self.call('PATCH','git/refs/heads/monitor-state',{'sha':commit['sha'],'force':False})
        else:
            self.call('POST','git/refs',{'ref':'refs/heads/monitor-state','sha':commit['sha']})
        write_json(path.parent/'remote.json',{'head':commit['sha'],'tree':tree['sha']})

    def notify(self,path,notification):
        n=json.loads(notification.read_text())
        if not n['needed']:
            print('Žádná nová notifikace.');return
        # Stable daily marker: rerunning on the same day updates the same Issue.
        marker='<!-- uzemni-monitor:'+n['date']+' -->'
        found=None
        for page in range(1,11):
            issues=self.call('GET',f'issues?state=all&creator=github-actions%5Bbot%5D&per_page=100&page={page}')
            for issue in issues:
                if marker in (issue.get('body') or ''):
                    found=issue;break
            if found or len(issues)<100:
                break
        if not found and page==10 and len(issues)==100:
            raise GitHubError('Příliš dlouhá historie Issues; nebylo možné bezpečně vyloučit duplicitu.')
        body=marker+'\n'+n['body']
        if len(body)>60_000:
            repo=os.environ['GITHUB_REPOSITORY']
            body=marker+'\nDenní report je příliš dlouhý. Úplný výsledek: https://github.com/'+repo+'/blob/monitor-state/reports/'+n['date']+'.md'
        title='Úřední desky — '+n['date']
        if found:
            self.call('PATCH','issues/'+str(found['number']),{'title':title,'body':body})
        else:
            self.call('POST','issues',{'title':title,'body':body})
        state=load_state(path)
        state['notified']=sorted(set(state['notified']+n['ids']))
        write_json(path,state)
        print('Denní GitHub Issue bylo vytvořeno nebo aktualizováno.')

def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=['load','save','notify'])
    p.add_argument('--state',default='state/state.json');p.add_argument('--output',default='output')
    args=p.parse_args();api=GitHub();path=Path(args.state);out=Path(args.output)
    try:
        if args.command=='load':api.load(path)
        elif args.command=='save':api.save(path,out/'latest.md')
        else:api.notify(path,out/'notification.json')
    finally:
        api.client.close()

if __name__=='__main__':
    try:
        main()
    except GitHubError as e:
        raise SystemExit(str(e))
