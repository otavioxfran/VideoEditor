from urllib.parse import urlparse
from video_editor.editing import require_revision


def record(brief, request):
    require_revision(brief, request['expected_revision'])
    claim = next((c for c in brief['claims'] if c['id'] == request['claim_id']), None)
    if claim is None:
        raise ValueError('Claim inexistente.')
    url, excerpt = request['url'], request['excerpt'].strip()
    if url and urlparse(url).scheme not in ('https', 'http'):
        raise ValueError('Fonte precisa de URL HTTP/HTTPS.')
    if request['status'] in ('SUPPORTED', 'CONTRADICTED'):
        if not request['human_confirmed'] or not url or not excerpt:
            raise ValueError('Conclusão exige fonte, trecho e confirmação humana.')
    if url or excerpt:
        claim['evidence'].append(dict(url=url, excerpt=excerpt, origin='manual', fetched=False,
                                      human_confirmed=request['human_confirmed']))
    claim.update(status=request['status'], human_confirmed=request['human_confirmed'])
    brief['revision'] += 1
    return brief
