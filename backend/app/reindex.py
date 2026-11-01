"""Rebuild vectors for every ready document after a chunking change.

Stop the web server first (the Chroma client is process-local), then run:
    python -m app.reindex
"""
from .db import connect, init
from . import rag

def main():
    init()
    with connect() as db:
        docs = [dict(r) for r in db.execute("SELECT id, name FROM documents WHERE status='ready' AND active=1")]
    for doc in docs:
        rag.process(doc['id'])
        with connect() as db:
            row = db.execute('SELECT status, chunks, error FROM documents WHERE id=?', (doc['id'],)).fetchone()
        print(f"{doc['name']}: {row['status']} ({row['chunks']} chunks){' - ' + row['error'] if row['error'] else ''}")
    if not docs:
        print('No ready documents to re-index.')

if __name__ == '__main__':
    main()
