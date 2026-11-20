/**
 * Result of mapping an API error to a user-friendly representation.
 *
 * @public
 */
export type MappedApiError =
  | { message: string; fieldErrors?: undefined }
  | { message: string; fieldErrors: Record<string, string> };

import { HttpError } from './client';

/**
 * Map a caught API error (HttpError / unknown) to a structured, user-friendly result.
 *
 * @remarks
 * Handles:
 * - AbortError (request cancelled) → "Request aborted"
 * - FastAPI 422 validation errors → per-field message extraction
 * - Status-code-specific friendly messages (500, 404, 401, 403, etc.)
 * - Generic HttpError → detail field or raw text
 * - Non-HttpError → Error.message or "Unknown error"
 *
 * @param err - The caught error from an API call.
 * @returns A {@link MappedApiError} with a top-level message and optional field errors.
 *
 * @public
 */
export function mapApiError(err: unknown): MappedApiError {
  // Handle AbortError (request was cancelled)
  if (err instanceof DOMException && err.name === 'AbortError') {
    return { message: 'Request aborted' };
  }

  if (err instanceof HttpError) {
    const body = err.body as { detail?: unknown } | undefined;

    // FastAPI 422 validation error shape (highest priority — most specific)
    if (err.status === 422 && body?.detail && Array.isArray(body.detail)) {
      const fieldErrors: Record<string, string> = {};
      for (const item of body.detail) {
        const loc = (item as { loc?: unknown[] }).loc;
        const msg = (item as { msg?: string }).msg ?? 'Invalid value';
        if (loc && Array.isArray(loc)) {
          // Skip the leading 'body' segment if present
          const fieldPath = loc.filter((x) => x !== 'body').join('.');
          if (fieldPath) {
            fieldErrors[fieldPath] = msg;
          }
        }
      }
      if (Object.keys(fieldErrors).length > 0) {
        return { message: 'Validation failed', fieldErrors };
      }
    }

    // Body detail from the API (more specific than generic status messages)
    const bodyDetail =
      body && typeof body === 'object' && 'detail' in body ? String(body.detail) : undefined;
    if (bodyDetail) {
      return { message: bodyDetail };
    }

    // Fall back to status-code-specific friendly messages
    const statusMessage = getStatusMessage(err.status);
    if (statusMessage) {
      return { message: statusMessage };
    }

    // Last resort: raw text or error message
    const detail = err.text ?? err.message;
    return { message: detail };
  }

  // Non-HttpError (network, unknown)
  const message = err instanceof Error ? err.message : 'Unknown error';
  return { message };
}

/**
 * Return a friendly message for well-known HTTP status codes.
 *
 * @param status - The HTTP status code.
 * @returns A user-friendly message, or `undefined` if no mapping exists.
 *
 * @internal
 */
function getStatusMessage(status: number): string | undefined {
  switch (status) {
    case 400:
      return 'Bad request. Please check your input and try again.';
    case 401:
      return 'Authentication required. Please log in.';
    case 403:
      return 'Access denied. You do not have permission for this action.';
    case 404:
      return 'Resource not found.';
    case 409:
      return 'Conflict. The resource already exists or has been modified.';
    case 422:
      // Handled separately with field-level errors
      return undefined;
    case 429:
      return 'Too many requests. Please wait a moment and try again.';
    case 500:
      return 'Service temporarily unavailable. Please try again later.';
    case 502:
    case 503:
    case 504:
      return 'Service temporarily unavailable. Please try again later.';
    default:
      return undefined;
  }
}
