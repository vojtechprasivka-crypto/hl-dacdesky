from datetime import date
import hashlib

def reminders(notices, today):
    """Select at most one catch-up reminder per current deadline version."""
    result = []
    for key, n in notices.items():
        if n.get('status') != 'analyzed':
            continue
        for d in n.get('analysis', {}).get('deadlines', []):
            if d.get('verification') != 'verified' or not d.get('date'):
                continue
            remaining = (date.fromisoformat(d['date']) - today).days
            if remaining < 0 or remaining > 14:
                continue
            offset = 1 if remaining <= 1 else 7 if remaining <= 7 else 14
            ident = hashlib.sha256(f'{key}|{n["fingerprint"]}|{d["kind"]}|{d["date"]}|{d.get("time")}|{offset}'.encode()).hexdigest()[:24]
            result.append({'id':ident, 'notice_key':key, 'title':n['title'], 'url':n['url'], 'date':d['date'],
                           'time':d.get('time'), 'kind':d['kind'], 'remaining':remaining,
                           'document_url':d['document_url'], 'quote':d['quote'], 'page':d['page']})
    return result
