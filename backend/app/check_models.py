"""List models accessible to the configured key without generating content."""
import os
import argparse
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--probe', action='store_true', help='Send a tiny embedding and generation request (may consume API quota).')
    args = parser.parse_args()
    load_dotenv(Path(__file__).resolve().parents[2] / '.env')
    if not os.getenv('GEMINI_API_KEY'):
        print('GEMINI_API_KEY was not loaded from the environment or root .env.')
        return
    print('Configured generation:', os.getenv('GEMINI_MODEL', 'gemini-3.8-flash'))
    print('Configured embedding:', os.getenv('GEMINI_EMBEDDING_MODEL', 'gemini-embedding-001'))
    if args.probe:
        client = genai.Client(api_key=os.environ['GEMINI_API_KEY'], http_options=types.HttpOptions(timeout=20000))
        probes = [
            ('Embedding', os.getenv('GEMINI_EMBEDDING_MODEL', 'gemini-embedding-001'),
             lambda model: client.models.embed_content(model=model, contents=['Travel package'], config=types.EmbedContentConfig(task_type='RETRIEVAL_QUERY', output_dimensionality=768))),
            ('Answer generation', os.getenv('GEMINI_MODEL', 'gemini-3.8-flash'),
             lambda model: client.models.generate_content(model=model, contents='Return JSON with answer set to OK and source_numbers set to an empty array.', config=types.GenerateContentConfig(temperature=0.1, response_mime_type='application/json'))),
        ]
        for stage, model, request in probes:
            try:
                request(model)
                print(f'{stage} ({model}): PASS')
            except Exception as exc:
                print(f'{stage} ({model}): FAIL, type={type(exc).__name__}, status={getattr(exc, "code", None)}')
                message = getattr(exc, 'message', '')
                if message:
                    message = str(message).replace(os.environ['GEMINI_API_KEY'], '[REDACTED]')
                    print('Provider explanation:', message[:1500])
        return
    try:
        client = genai.Client(api_key=os.environ['GEMINI_API_KEY'], http_options=types.HttpOptions(timeout=20000))
        models = list(client.models.list())
    except Exception as exc:
        print('Model lookup failed:', type(exc).__name__, 'status:', getattr(exc, 'code', None))
        return
    for action, title in [('generateContent', 'Available generation models'), ('embedContent', 'Available embedding models')]:
        print('\n' + title + ':')
        names = sorted(m.name.removeprefix('models/') for m in models if action in (m.supported_actions or []))
        for name in names:
            print('  ' + name)
        if not names:
            print('  None reported for this API key.')
    print('\nKeep the embedding model unchanged unless you intend to rebuild the document index.')

if __name__ == '__main__':
    main()
