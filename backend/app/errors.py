"""Safe error diagnostics: never log provider payloads, keys or chat content."""
import logging

logger = logging.getLogger('uvicorn.error')

def describe(exc):
    if getattr(exc, 'stage', None):
        prefix = f'{exc.stage} using {exc.model}: '
        return prefix + describe(exc.__cause__ or RuntimeError('Model request failed'))
    code = getattr(exc, 'code', None)
    try:
        code = int(code) if code is not None else None
    except (TypeError, ValueError):
        code = None
    logger.error('Travel guide failure: type=%s provider_status=%s', type(exc).__name__, code)
    if code == 429:
        return 'Gemini request limit or quota exceeded (429). Check the API project quota and billing, then retry when capacity is available.'
    if code in (401, 403):
        return 'Gemini rejected authentication or access. Check the backend API key and its permissions.'
    if code == 404:
        return 'The configured Gemini model was not found or is unavailable to this API key. Check GEMINI_MODEL and GEMINI_EMBEDDING_MODEL.'
    if code == 400:
        return 'Gemini rejected the request (400). Check the API key, configured models, and supported model settings.'
    if code is not None and code >= 500:
        return 'Gemini is temporarily unavailable. Please retry shortly.'
    if 'timeout' in type(exc).__name__.lower():
        return 'The AI request timed out. Please retry shortly.'
    if 'connect' in type(exc).__name__.lower():
        return 'The backend could not connect to Gemini. Check its network and proxy configuration.'
    # Only expose exact messages that originate in our own validation code.
    known = {
        'GEMINI_API_KEY is missing. Configure it and retry.': 'The backend has not loaded GEMINI_API_KEY. Restart it with --env-file ../.env.',
        'Embedding model changed. Restore the original model or rebuild the Chroma index.': 'The embedding model differs from the document index. Restore the model used when uploading documents.',
        'Invalid model response': 'Gemini returned an unusable answer format. Please retry.',
        'Invalid model citations': 'Gemini returned inconsistent source references. Please retry; the answer was rejected to avoid displaying incorrect citations.',
    }
    if str(exc) in known:
        logger.error('Travel guide validation: %s', str(exc))
        return known[str(exc)]
    if isinstance(exc, (ValueError, TypeError)):
        return 'The guide could not process the retrieved data or AI response. Check the backend diagnostic log.'
    return 'The guide encountered an internal error. Check the backend diagnostic log for the error type.'
