import hashlib
import re
import time
from urllib.parse import urljoin, urlsplit
from lxml import html

class SourceError(Exception):
    pass

def parse_listing(raw, base):
    tree = html.fromstring(raw, parser=html.HTMLParser(encoding="utf-8"))
    anchors = tree.xpath('//a[contains(concat(" ",normalize-space(@class)," ")," item-href ")]')
    if not anchors:
        raise SourceError('Seznam nemá očekávané položky; změna webu nebo prázdný seznam vyžaduje kontrolu.')
    result = []
    for a in anchors:
        url = urljoin(base, a.get('href', ''))
        match = re.search(r'-(\d+)\.html$', urlsplit(url).path)
        if not match:
            raise SourceError('Položka nemá očekávané stabilní ID.')
        result.append({'id': match[1], 'title': ' '.join(a.text_content().split()), 'url': url})
    # This adapter intentionally fails rather than silently omitting paginated records.
    for a in tree.xpath('//a[@href]'):
        if re.search(r'[?&](?:page|strana|pos)=', a.get('href', '')):
            raise SourceError('Zjištěno stránkování; adaptér je nutné rozšířit.')
    return result

def parse_detail(raw, base):
    tree = html.fromstring(raw, parser=html.HTMLParser(encoding="utf-8"))
    main = tree.xpath('//main | //*[@id="gcm-main"]')
    if not main:
        raise SourceError('Chybí hlavní obsah detailu.')
    area = main[0]
    for element in area.xpath('.//script|.//style|.//nav|.//form'):
        element.drop_tree()
    text = ' '.join(area.text_content().split())
    files = []
    for a in area.xpath('.//a[@href]'):
        url = urljoin(base, a.get('href'))
        if ('file' in a.get('class', '').split() or '.pdf' in url.lower()) and url not in files:
            files.append(url)
    if len(files) > 15:
        raise SourceError('Příliš mnoho příloh.')
    return text[:35_000], files

def discover(source, web):
    if source.get('adapter') != 'galileo':
        raise SourceError('Nepodporovaný adaptér.')
    notices = {}
    for url in source['lists']:
        time.sleep(.25)
        for n in parse_listing(web.get(url, 3_000_000), url):
            notices.setdefault(n['id'], n)
    return list(notices.values())

def digest(data):
    return hashlib.sha256(data).hexdigest()
