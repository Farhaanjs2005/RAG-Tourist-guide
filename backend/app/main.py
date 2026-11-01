import hashlib
import json
import os
import secrets
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import Depends, FastAPI, HTTPException, Request, Response, UploadFile, File, Form
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .db import DATA, connect, init
from . import rag
from .errors import describe

passwords = PasswordHasher()
def uid(): return uuid.uuid4().hex

@asynccontextmanager
async def lifespan(app):
    init()
    stop = threading.Event()
    thread = threading.Thread(target=rag.worker, args=(stop,), daemon=True)
    if os.getenv('DISABLE_WORKER') != '1':
        thread.start()
    yield
    stop.set()
    if thread.is_alive(): thread.join(timeout=2)

app = FastAPI(title='Wayfarer Travel Guide API', version='1.0.0', lifespan=lifespan)

@app.middleware('http')
async def same_origin(request: Request, call_next):
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        origin = request.headers.get('origin')
        allowed = os.getenv('APP_ORIGIN', 'http://localhost:5173').strip().rstrip('/')
        if origin and origin != allowed:
            return JSONResponse({'detail': f'Frontend address mismatch: browser sent {origin}, but backend allows {allowed}. Open the allowed address or set APP_ORIGIN to your browser origin and restart the backend.'}, status_code=403)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Cache-Control'] = 'no-store'
    return response

def user(request: Request):
    token = request.cookies.get('session', '')
    digest = hashlib.sha256(token.encode()).hexdigest()
    with connect() as db:
        row = db.execute('SELECT u.* FROM users u JOIN sessions s ON s.user_id=u.id WHERE s.token=? AND s.expires>? AND u.active=1', (digest, time.time())).fetchone()
    if not row: raise HTTPException(401, 'Please sign in.')
    return dict(row)

def admin(current=Depends(user)):
    if current['role'] != 'admin': raise HTTPException(403, 'Admin access required.')
    return current

def public(row):
    return {k: row[k] for k in ('id', 'name', 'email', 'role', 'active')}

class Credentials(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=10, max_length=128)

class Registration(Credentials):
    name: str = Field(min_length=1, max_length=100)

def create_user(body, role='user'):
    email = body.email.strip().lower()
    if '@' not in email or '.' not in email.split('@')[-1] or not body.name.strip():
        raise HTTPException(422, 'Enter a valid name and email.')
    with connect() as db:
        if db.execute('SELECT id FROM users WHERE email=?', (email,)).fetchone():
            raise HTTPException(409, 'An account already exists for this email.')
        identity = uid()
        db.execute('INSERT INTO users(id,email,name,password,role) VALUES(?,?,?,?,?)', (identity, email, body.name.strip(), passwords.hash(body.password), role))
        return public(db.execute('SELECT * FROM users WHERE id=?', (identity,)).fetchone())

@app.get('/api/health')
def health(): return {'status': 'ok', 'gemini_configured': bool(os.getenv('GEMINI_API_KEY'))}

@app.post('/api/auth/register', status_code=201)
def register(body: Registration): return create_user(body)

@app.post('/api/auth/login')
def login(body: Credentials, response: Response):
    with connect() as db:
        row = db.execute('SELECT * FROM users WHERE email=?', (body.email.strip().lower(),)).fetchone()
        try:
            if not row or not row['active']: raise ValueError()
            passwords.verify(row['password'], body.password)
        except (VerificationError, ValueError):
            raise HTTPException(401, 'Incorrect email or password, or account disabled.')
        token = secrets.token_urlsafe(32)
        db.execute('DELETE FROM sessions WHERE expires<?', (time.time(),))
        db.execute('INSERT INTO sessions VALUES(?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), row['id'], time.time() + 86400))
    response.set_cookie('session', token, httponly=True, secure=os.getenv('COOKIE_SECURE') == '1', samesite='strict', max_age=86400)
    return public(row)

@app.post('/api/auth/logout')
def logout(request: Request, response: Response):
    with connect() as db:
        db.execute('DELETE FROM sessions WHERE token=?', (hashlib.sha256(request.cookies.get('session', '').encode()).hexdigest(),))
    response.delete_cookie('session')
    return {'ok': True}

@app.get('/api/auth/me')
def me(current=Depends(user)): return public(current)

@app.get('/api/admin/users')
def users(current=Depends(admin)):
    with connect() as db: return [public(r) for r in db.execute('SELECT * FROM users ORDER BY name')]

@app.post('/api/admin/users', status_code=201)
def add_user(body: Registration, current=Depends(admin)): return create_user(body)

class Active(BaseModel):
    active: bool

@app.patch('/api/admin/users/{identity}')
def change_user(identity: str, body: Active, current=Depends(admin)):
    with connect() as db:
        target = db.execute('SELECT * FROM users WHERE id=?', (identity,)).fetchone()
        if not target: raise HTTPException(404, 'User not found.')
        if target['role'] == 'admin': raise HTTPException(400, 'Admin accounts cannot be disabled here.')
        db.execute('UPDATE users SET active=? WHERE id=?', (int(body.active), identity))
        if not body.active: db.execute('DELETE FROM sessions WHERE user_id=?', (identity,))
    return {'ok': True}

@app.get('/api/admin/documents')
def documents(current=Depends(admin)):
    with connect() as db:
        return [dict(r) for r in db.execute("SELECT id,name,status,error,active,replaces,created,chunks FROM documents WHERE status!='deleted' ORDER BY created DESC")]

@app.post('/api/admin/documents', status_code=202)
def upload(file: UploadFile = File(...), replaces: str = Form(''), current=Depends(admin)):
    name = Path((file.filename or '').replace('\\', '/')).name
    suffix = Path(name).suffix.lower()
    if suffix not in ('.pdf', '.md'): raise HTTPException(422, 'Upload a PDF or Markdown (.md) file.')
    identity = uid()
    folder = DATA / 'uploads'
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (identity + suffix)
    try:
        size = 0
        with path.open('wb') as out:
            while block := file.file.read(1024 * 1024):
                size += len(block)
                if size > 20 * 1024 * 1024: raise HTTPException(413, 'Maximum file size is 20 MB.')
                out.write(block)
        if not size: raise HTTPException(422, 'The file is empty.')
        with rag.LOCK, connect() as db:
            if replaces:
                old = db.execute("SELECT id FROM documents WHERE id=? AND active=1 AND status='ready'", (replaces,)).fetchone()
                if not old: raise HTTPException(404, 'Active document to replace not found.')
                pending = db.execute("SELECT id FROM documents WHERE replaces=? AND status IN ('queued','processing')", (replaces,)).fetchone()
                if pending: raise HTTPException(409, 'A replacement is already processing.')
            db.execute('INSERT INTO documents(id,name,path,status,replaces,created) VALUES(?,?,?,?,?,?)', (identity, name, str(path), 'queued', replaces or None, time.time()))
            db.execute('INSERT INTO jobs VALUES(?,?,?)', (uid(), identity, 'queued'))
        return {'id': identity, 'status': 'queued'}
    except Exception:
        path.unlink(missing_ok=True)
        raise

@app.post('/api/admin/documents/{identity}/retry')
def retry_document(identity: str, current=Depends(admin)):
    with rag.LOCK, connect() as db:
        doc = db.execute("SELECT * FROM documents WHERE id=? AND status='failed'", (identity,)).fetchone()
        if not doc: raise HTTPException(409, 'Only failed documents can be retried.')
        if doc['replaces']:
            old = db.execute('SELECT active FROM documents WHERE id=?', (doc['replaces'],)).fetchone()
            if not old or not old['active']: raise HTTPException(409, 'Original is no longer active; upload as a new document.')
        db.execute("UPDATE documents SET status='queued',error=NULL WHERE id=?", (identity,))
        db.execute("UPDATE jobs SET status='queued' WHERE document_id=?", (identity,))
    return {'ok': True}

@app.delete('/api/admin/documents/{identity}')
def delete_document(identity: str, current=Depends(admin)):
    with rag.LOCK, connect() as db:
        doc = db.execute('SELECT * FROM documents WHERE id=?', (identity,)).fetchone()
        if not doc: raise HTTPException(404, 'Document not found.')
        db.execute("UPDATE documents SET status='deleted',active=0 WHERE id=?", (identity,))
        db.execute("UPDATE jobs SET status='cancelled' WHERE document_id=?", (identity,))
        # A pending replacement must not republish a deleted document's content.
        pending = db.execute("SELECT * FROM documents WHERE replaces=? AND status IN ('queued','processing','failed')", (identity,)).fetchall()
        for item in [doc, *pending]:
            db.execute("UPDATE documents SET status='deleted',active=0 WHERE id=?", (item['id'],))
            db.execute("UPDATE jobs SET status='cancelled' WHERE document_id=?", (item['id'],))
            rag.collection().delete(where={'document_id': item['id']})
            Path(item['path']).unlink(missing_ok=True)
    return {'ok': True}

def conversation(db, identity, current):
    row = db.execute('SELECT * FROM conversations WHERE id=? AND user_id=?', (identity, current['id'])).fetchone()
    if not row: raise HTTPException(404, 'Conversation not found.')
    return row

@app.get('/api/conversations')
def conversations(current=Depends(user)):
    with connect() as db: return [dict(r) for r in db.execute('SELECT * FROM conversations WHERE user_id=? ORDER BY created DESC', (current['id'],))]

@app.post('/api/conversations', status_code=201)
def new_conversation(current=Depends(user)):
    identity = uid()
    with connect() as db: db.execute('INSERT INTO conversations VALUES(?,?,?,?)', (identity, current['id'], 'New conversation', time.time()))
    return {'id': identity}

@app.get('/api/conversations/{identity}/messages')
def messages(identity: str, current=Depends(user)):
    with connect() as db:
        conversation(db, identity, current)
        result = []
        for row in db.execute('SELECT * FROM messages WHERE conversation_id=? ORDER BY created', (identity,)):
            item = dict(row)
            item['citations'] = json.loads(item['citations'])
            # Excerpts are fetched separately and respect document deletion.
            result.append(item)
        return result

@app.delete('/api/conversations/{identity}')
def delete_conversation(identity: str, current=Depends(user)):
    with connect() as db:
        conversation(db, identity, current)
        db.execute('DELETE FROM conversations WHERE id=?', (identity,))
    return {'ok': True}

class Question(BaseModel):
    content: str = Field(min_length=1, max_length=4000)

@app.post('/api/conversations/{identity}/messages')
def ask(identity: str, body: Question, request: Request, current=Depends(user)):
    question = body.content.strip()
    if not question: raise HTTPException(422, 'Enter a question.')
    with connect() as db:
        conversation(db, identity, current)
        history = [dict(r) for r in db.execute('SELECT role,content FROM messages WHERE conversation_id=? ORDER BY created DESC LIMIT 6', (identity,))][::-1]
    try:
        content, citations = rag.answer(question, history)
    except Exception as exc:
        raise HTTPException(503, describe(exc) + ' Your question has not been saved.') from None
    user(request)  # Recheck session after a potentially slow provider call.
    with rag.LOCK, connect() as db:
        conversation(db, identity, current)
        for citation in citations:
            valid = db.execute("SELECT id FROM documents WHERE id=? AND active=1 AND status='ready'", (citation['document_id'],)).fetchone()
            if not valid: raise HTTPException(409, 'Sources changed while answering. Please retry.')
        stored = [{k: v for k, v in c.items() if k != 'excerpt'} for c in citations]
        now = time.time()
        db.execute('INSERT INTO messages VALUES(?,?,?,?,?,?)', (uid(), identity, 'user', question, '[]', now))
        db.execute('INSERT INTO messages VALUES(?,?,?,?,?,?)', (uid(), identity, 'assistant', content, json.dumps(stored), now + 0.001))
        if not history: db.execute('UPDATE conversations SET title=? WHERE id=?', (question[:70], identity))
    return {'role': 'assistant', 'content': content, 'citations': stored}

@app.get('/api/sources/{chunk_id}')
def source(chunk_id: str, current=Depends(user)):
    identity = chunk_id.split(':')[0]
    with rag.LOCK, connect() as db:
        doc = db.execute("SELECT * FROM documents WHERE id=? AND status='ready' AND active=1", (identity,)).fetchone()
        if not doc: raise HTTPException(404, 'Source is no longer available.')
        result = rag.collection().get(ids=[chunk_id], include=['documents', 'metadatas'])
        if not result['ids']: raise HTTPException(404, 'Source not found.')
        return {'name': doc['name'], 'location': result['metadatas'][0]['location'], 'excerpt': result['documents'][0]}

frontend = Path(__file__).resolve().parents[2] / 'frontend' / 'dist'
if frontend.exists(): app.mount('/', StaticFiles(directory=frontend, html=True), name='frontend')
