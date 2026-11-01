import json
import os
from types import SimpleNamespace

os.environ['DISABLE_WORKER'] = '1'

import pytest
from fastapi.testclient import TestClient
from app import db, rag
from app.main import app, create_user, Registration

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA', tmp_path)
    monkeypatch.setattr(rag, 'DATA', tmp_path)
    monkeypatch.setattr('app.main.DATA', tmp_path)
    rag.collection.cache_clear()
    with TestClient(app) as c:
        yield c
    rag.collection.cache_clear()

def account(client, email='customer@example.com', role='user'):
    body = {'email': email, 'name': 'Test Traveller', 'password': 'good-password-123'}
    if role == 'admin': create_user(Registration(**body), role='admin')
    else: assert client.post('/api/auth/register', json=body).status_code == 201
    assert client.post('/api/auth/login', json=body).status_code == 200
    return client.get('/api/auth/me').json()

def upload(client, text='# Bengaluru\nA three-day package costs INR 12000.', replaces=None):
    response = client.post('/api/admin/documents', files={'file': ('guide.md', text.encode(), 'text/markdown')}, data={'replaces': replaces or ''})
    assert response.status_code == 202
    return response.json()['id']

@pytest.fixture
def embeddings(monkeypatch):
    monkeypatch.setattr(rag, 'embed', lambda texts, query=False: [[1.0] + [0.0] * 767 for _ in texts])

def test_auth_permissions_and_disable(client):
    assert client.get('/api/admin/users').status_code == 401
    customer = account(client)
    cookie = client.cookies.get('session')
    assert client.get('/api/admin/documents').status_code == 403
    account(client, 'admin@example.com', 'admin')
    assert client.patch('/api/admin/users/' + customer['id'], json={'active': False}).status_code == 200
    client.cookies.set('session', cookie)
    assert client.get('/api/auth/me').status_code == 401

def test_conversation_isolation(client, monkeypatch):
    account(client)
    cid = client.post('/api/conversations').json()['id']
    monkeypatch.setattr(rag, 'answer', lambda q, h: ('Please specify a destination.', []))
    assert client.post(f'/api/conversations/{cid}/messages', json={'content': 'Which package?'}).status_code == 200
    assert len(client.get(f'/api/conversations/{cid}/messages').json()) == 2
    account(client, 'other@example.com')
    assert client.get(f'/api/conversations/{cid}/messages').status_code == 404
    assert client.delete(f'/api/conversations/{cid}').status_code == 404

def test_document_lifecycle(client, embeddings):
    account(client, 'admin@example.com', 'admin')
    original = upload(client)
    rag.process(original)
    assert client.get('/api/sources/' + original + ':0').status_code == 200
    replacement = upload(client, '# Bengaluru\nUpdated price INR 15000.', original)
    assert client.get('/api/sources/' + original + ':0').status_code == 200
    rag.process(replacement)
    assert client.get('/api/sources/' + original + ':0').status_code == 404
    assert client.get('/api/sources/' + replacement + ':0').status_code == 200
    assert client.delete('/api/admin/documents/' + replacement).status_code == 200
    assert client.get('/api/sources/' + replacement + ':0').status_code == 404
    assert not rag.collection().get(where={'document_id': replacement})['ids']

def test_failed_replacement_preserves_original(client, embeddings):
    account(client, 'admin@example.com', 'admin')
    original = upload(client)
    rag.process(original)
    replacement = upload(client, '   ', original)
    rag.process(replacement)
    rows = client.get('/api/admin/documents').json()
    assert next(d for d in rows if d['id'] == replacement)['status'] == 'failed'
    assert client.get('/api/sources/' + original + ':0').status_code == 200
    assert client.post('/api/admin/documents/' + replacement + '/retry').status_code == 200

def test_invalid_uploads_and_origin(client):
    account(client, 'admin@example.com', 'admin')
    assert client.post('/api/admin/documents', files={'file': ('bad.txt', b'hello')}).status_code == 422
    assert client.post('/api/admin/documents', files={'file': ('bad.md', b'')}).status_code == 422
    assert client.post('/api/conversations', headers={'origin': 'https://evil.example'}).status_code == 403

def test_end_to_end_citations_and_history(client, embeddings, monkeypatch):
    account(client, 'admin@example.com', 'admin')
    identity = upload(client)
    rag.process(identity)
    def generate(**kwargs):
        sent = json.loads(kwargs['contents'])
        assert 'INR 12000' in sent['sources'][0]['excerpt']
        assert 'untrusted' in kwargs['config'].system_instruction
        return SimpleNamespace(text=json.dumps({'answer': 'The Bengaluru package costs INR 12000. [1]', 'source_numbers': [1]}))
    monkeypatch.setattr(rag, 'ai', lambda: SimpleNamespace(models=SimpleNamespace(generate_content=generate)))
    account(client)
    cid = client.post('/api/conversations').json()['id']
    result = client.post(f'/api/conversations/{cid}/messages', json={'content': 'What Bengaluru package do you offer?'})
    assert result.status_code == 200
    assert result.json()['citations'][0]['document_id'] == identity
    assert 'excerpt' not in result.json()['citations'][0]
    assert len(client.get(f'/api/conversations/{cid}/messages').json()) == 2
    # Reopening a DB connection retains messages and document state.
    with db.connect() as connection:
        assert connection.execute('SELECT COUNT(*) FROM messages').fetchone()[0] == 2

def test_invalid_citation_and_provider_failure(client, embeddings, monkeypatch):
    account(client, 'admin@example.com', 'admin')
    identity = upload(client)
    rag.process(identity)
    account(client)
    cid = client.post('/api/conversations').json()['id']
    monkeypatch.setattr(rag, 'ai', lambda: SimpleNamespace(models=SimpleNamespace(generate_content=lambda **kw: SimpleNamespace(text='{"answer":"Wrong [99]","source_numbers":[99]}'))))
    assert client.post(f'/api/conversations/{cid}/messages', json={'content': 'Price?'}).status_code == 503
    assert client.get(f'/api/conversations/{cid}/messages').json() == []

def test_no_documents(client):
    account(client)
    cid = client.post('/api/conversations').json()['id']
    response = client.post(f'/api/conversations/{cid}/messages', json={'content': 'What can I visit?'})
    assert response.status_code == 200
    assert response.json()['citations'] == []
    assert 'not added' in response.json()['content']

def test_pdf_and_markdown_extraction(tmp_path):
    from pypdf import PdfWriter
    path = tmp_path / 'blank.pdf'
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.write(path)
    with pytest.raises(ValueError, match='OCR'): rag.extract(path)
    md = tmp_path / 'notes.md'
    md.write_text('# Bengaluru\nPackage details\n## Inclusions\nBreakfast', encoding='utf-8')
    assert [loc for loc, _ in rag.chunks(md)] == ['Bengaluru', 'Inclusions']

def test_retry_is_bounded(monkeypatch):
    monkeypatch.setattr(rag.time, 'sleep', lambda seconds: None)
    attempts = []
    def unavailable():
        attempts.append(1)
        raise RuntimeError('Provider unavailable')
    with pytest.raises(RuntimeError): rag.retry(unavailable)
    assert len(attempts) == 3

def test_delete_cancels_pending_replacement(client, embeddings):
    account(client, 'admin@example.com', 'admin')
    original = upload(client)
    rag.process(original)
    replacement = upload(client, '# Bengaluru\nReplacement', original)
    assert client.delete('/api/admin/documents/' + original).status_code == 200
    rag.process(replacement)
    assert client.get('/api/admin/documents').json() == []
    assert client.get('/api/sources/' + replacement + ':0').status_code == 404

def test_worker_recovers_interrupted_jobs(client, embeddings):
    account(client, 'admin@example.com', 'admin')
    identity = upload(client)
    with db.connect() as conn:
        conn.execute("UPDATE jobs SET status='processing'")
        conn.execute("UPDATE documents SET status='processing'")
    # Stop immediately after startup recovery, then inspect durable queue state.
    import threading
    stop = threading.Event()
    stop.set()
    rag.worker(stop)
    with db.connect() as conn:
        assert conn.execute('SELECT status FROM jobs').fetchone()[0] == 'queued'
    rag.process(identity)
    assert client.get('/api/sources/' + identity + ':0').status_code == 200

def test_markdown_chunks_keep_parent_headings(tmp_path):
    md = tmp_path / 'blr.md'
    md.write_text('# Bengaluru Packages\nIntro\n## Package: City Break\nCode BLR-3\n### Sample itinerary\nDay 1: Cubbon Park\n## Booking\nNo bookings', encoding='utf-8')
    parts = dict(rag.chunks(md))
    assert parts['Sample itinerary'].startswith('[Context: Bengaluru Packages > Package: City Break]')
    assert parts['Booking'].startswith('[Context: Bengaluru Packages]')
    assert not parts['Bengaluru Packages'].startswith('[Context')

def test_retrieval_ignores_assistant_text_and_covers_every_document(client, monkeypatch):
    account(client, 'admin@example.com', 'admin')
    # Distinct one-hot vectors per document so the query decides what is closest.
    def embed(texts, query=False):
        def vec(t):
            v = [0.0] * 768
            v[0 if 'Bengaluru' in t else 1 if 'Coimbatore' in t else 2] = 1.0
            return v
        return [vec(t) for t in texts]
    monkeypatch.setattr(rag, 'embed', embed)
    blr = upload(client, '# Bengaluru\nVisit Cubbon Park.')
    cbe = upload(client, '# Coimbatore\nVisit Marudhamalai Temple.')
    rag.process(blr); rag.process(cbe)
    sent = {}
    def generate(**kwargs):
        sent.update(json.loads(kwargs['contents']))
        return SimpleNamespace(text=json.dumps({'answer': 'Cubbon Park. [1]', 'source_numbers': [1]}))
    monkeypatch.setattr(rag, 'ai', lambda: SimpleNamespace(models=SimpleNamespace(generate_content=generate)))
    history = [{'role': 'user', 'content': 'Coimbatore places?'}, {'role': 'assistant', 'content': 'Coimbatore ' * 200}]
    rag.answer('Which places are there in Bengaluru?', history)
    assert sent['current_question'] == 'Which places are there in Bengaluru?'
    assert sent['sources'][0]['document_id'] == blr
    assert {s['document_id'] for s in sent['sources']} == {blr, cbe}
