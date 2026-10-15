import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import httpx
from app.github import GitHub, GitHubError
from app.cli import write_json

class GitHubTests(unittest.TestCase):
    def api(self):
        with patch.dict(os.environ,{'GITHUB_TOKEN':'example','GITHUB_REPOSITORY':'owner/repo'}):
            return GitHub()

    def test_auth_failure_is_not_missing_state(self):
        api=self.api()
        with patch.object(api.client,'request',return_value=httpx.Response(403,text='sensitive')):
            with self.assertRaises(GitHubError) as e:api.head()
            self.assertNotIn('sensitive',str(e.exception))
        api.client.close()

    def test_first_load_missing_branch(self):
        api=self.api()
        with tempfile.TemporaryDirectory() as tmp,patch.object(api,'head',return_value=None):
            path=Path(tmp)/'state.json';api.load(path)
            self.assertFalse(path.exists())
            self.assertEqual(json.loads((Path(tmp)/'remote.json').read_text()),{'head':None})
        api.client.close()

    def test_existing_local_state_is_not_reset(self):
        api=self.api()
        with tempfile.TemporaryDirectory() as tmp,patch.object(api,'head',return_value=None):
            path=Path(tmp)/'state.json';path.write_text('{}')
            with self.assertRaises(GitHubError):api.load(path)
        api.client.close()

    def test_concurrent_state_change_aborts(self):
        api=self.api()
        with tempfile.TemporaryDirectory() as tmp,patch.object(api,'head',return_value='changed'),patch.object(api,'call') as call:
            path=Path(tmp)/'state.json'
            write_json(path,{'schema':1,'notices':{},'sources':{},'notified':[],'updated_at':'2026-10-15T08:00:00+02:00'})
            write_json(Path(tmp)/'remote.json',{'head':'old','tree':'tree'})
            with self.assertRaises(GitHubError):api.save(path,Path(tmp)/'report.md')
            call.assert_not_called()
        api.client.close()

    def test_atomic_first_save(self):
        api=self.api();calls=[]
        def fake(method,path,body=None,missing=False):
            calls.append((method,path,body))
            if path=='git/blobs':return {'sha':'blob'}
            if path=='git/trees':return {'sha':'tree'}
            if path=='git/commits':return {'sha':'commit'}
            if path=='git/refs':return {}
            raise AssertionError(path)
        with tempfile.TemporaryDirectory() as tmp,patch.object(api,'head',return_value=None),patch.object(api,'call',side_effect=fake):
            path=Path(tmp)/'state.json';report=Path(tmp)/'latest.md';report.write_text('report')
            write_json(path,{'schema':1,'notices':{},'sources':{},'notified':[],'updated_at':'2026-10-15T08:00:00+02:00'})
            write_json(Path(tmp)/'remote.json',{'head':None})
            api.save(path,report)
            tree=next(body for _,p,body in calls if p=='git/trees')
            self.assertEqual({e['path'] for e in tree['tree']},{'state/state.json','reports/latest.md','reports/2026-10-15.md'})
            commit=next(body for _,p,body in calls if p=='git/commits')
            self.assertEqual(commit['parents'],[])
            self.assertEqual(json.loads((Path(tmp)/'remote.json').read_text())['head'],'commit')
        api.client.close()

    def test_issue_failure_does_not_acknowledge(self):
        api=self.api()
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'state.json';notification=Path(tmp)/'notification.json'
            write_json(path,{'schema':1,'notices':{},'sources':{},'notified':[]})
            write_json(notification,{'needed':True,'date':'2026-10-15','body':'report','ids':['reminder:test']})
            def fail(method,p,body=None,missing=False):
                if method=='GET':return []
                raise GitHubError('HTTP 403')
            with patch.object(api,'call',side_effect=fail):
                with self.assertRaises(GitHubError):api.notify(path,notification)
            self.assertEqual(json.loads(path.read_text())['notified'],[])
        api.client.close()

class LargeStateTests(unittest.TestCase):
    def test_large_content_falls_back_to_git_blob(self):
        import base64
        data={'schema':1,'notices':{},'sources':{},'notified':[]}
        calls=[]
        with patch.dict(os.environ,{'GITHUB_TOKEN':'example','GITHUB_REPOSITORY':'owner/repo'}):api=GitHub()
        def fake(method,path,body=None,missing=False):
            calls.append(path)
            if path=='git/commits/head':return {'tree':{'sha':'tree'}}
            if path.startswith('contents/'):return {'encoding':'none','sha':'large-blob'}
            if path=='git/blobs/large-blob':return {'encoding':'base64','content':base64.b64encode(json.dumps(data).encode()).decode()}
            raise AssertionError(path)
        try:
            with tempfile.TemporaryDirectory() as tmp,patch.object(api,'head',return_value='head'),patch.object(api,'call',side_effect=fake):
                path=Path(tmp)/'state.json';api.load(path)
                self.assertEqual(json.loads(path.read_text()),data)
                self.assertIn('git/blobs/large-blob',calls)
        finally:api.client.close()
