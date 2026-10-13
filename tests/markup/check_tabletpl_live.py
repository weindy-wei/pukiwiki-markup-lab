#!/usr/bin/env python3
"""Real local HTTP verification of PukiWiki table template plugin."""
import argparse
import json
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import urlopen
from test_table_rendering import REPO_ROOT, localhost_url

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--site-dir", type=Path, required=True)
parser.add_argument("--base-url", default="http://127.0.0.1:8080")
parser.add_argument("--output-dir", type=Path, required=True)
options = parser.parse_args()
site = options.site_dir.resolve(strict=True)
out = options.output_dir.resolve()
if site == REPO_ROOT or (site / ".git").exists():
    raise ValueError("Refusing Git checkout as test site")
if not (site / "plugin/tabletpl.inc.php").is_file() or not (site / "wiki").is_dir():
    raise ValueError("PukiWiki isolated test site or tabletpl plugin missing")
if out == REPO_ROOT or REPO_ROOT in out.parents:
    raise ValueError("Keep test evidence outside Git")
base_url = localhost_url(options.base_url)
out.mkdir(parents=True, exist_ok=True)
fixtures = {
    'PukiWikiTabletplProject': (
        '* Project Test\n'
        '#tabletpl(project_overview,title=欧罗巴黑晶纪,genre=科幻电影,stage=筹备中,owner=周星宇)\n'
    ),
    'PukiWikiTabletplShot': (
        '* Shot Test\n'
        '#tabletpl(shot_card,shot=SC017,setting=欧罗巴基地,action=黑晶生长,duration=12秒)\n'
    ),
    'PukiWikiTabletplUnknown': (
        '* Invalid Template Test\n'
        '#tabletpl(unknown_id,title=Hi)\n'
    ),
    'PukiWikiTabletplInjection': (
        '* Unsafe Input Test\n'
        '#tabletpl(project_overview,title=evil|#include,genre=Film,stage=Draft,owner=Team)\n'
    ),
}
class Grab(HTMLParser):
    def __init__(self):
        super().__init__()
        self.cells = []
        self.tables = 0
        self.cell = None
        self.th = 0
        self.data = ''
    def handle_starttag(self,tag,attrs):
        if tag=='table' and 'style_table' in dict(attrs).get('class',''): self.tables+=1
        if tag in ('td','th'):
            self.cell={'tag':tag,'attrs':dict(attrs),'text':''}
            if tag=='th': self.th+=1
    def handle_data(self,data):
        self.data+=data
        if self.cell is not None:self.cell['text']+=data
    def handle_endtag(self,tag):
        if tag in ('td','th') and self.cell is not None:
            self.cells.append(self.cell)
            self.cell=None

results=[]
for page,source in fixtures.items():
    file = site/'wiki'/(page.encode().hex().upper()+'.txt')
    if file.exists():
        if file.read_text(encoding='utf-8')!=source: raise RuntimeError(f'Fixture collision: {file}')
    else:file.write_text(source,encoding='utf-8')
    url=base_url + '/index.php?' + page
    with urlopen(url,timeout=10) as response:
        http=response.status
        html=response.read().decode('utf-8','replace')
    (out/(page+'.html')).write_text(html,encoding='utf-8')
    g=Grab();g.feed(html)
    texts=[x['text'] for x in g.cells]
    if page.endswith('Project'):
        passed=(http==200 and g.tables==1 and g.th==2 and
                '欧罗巴黑晶纪' in texts and '科幻电影' in texts and '周星宇' in texts)
    elif page.endswith('Shot'):
        passed=(http==200 and g.tables==1 and g.th==4 and
                'SC017' in texts and '黑晶生长' in texts and '12秒' in texts)
    elif page.endswith('Unknown'):
        passed=(http==200 and g.tables==0 and 'tabletpl: Unknown or invalid template' in g.data)
    else:
        passed=(http==200 and g.tables==0 and 'tabletpl: Invalid field value' in g.data and
                '<script>' not in html)
    results.append({'page':page,'http':http,'table_count':g.tables,'header_cells':g.th,
                    'sample_texts':texts[:16],'passed':passed})
    print(f"{page}: {'PASS' if passed else 'FAIL'} HTTP={http} tables={g.tables} th={g.th} texts={texts[:16]}")
(out/'report.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
passed=sum(r['passed'] for r in results)
print(f'LIVE_TABLETPL={passed}/{len(results)}')
raise SystemExit(0 if passed==len(results) else 1)
