/**
 * Unit tests for the API error mapper.
 *
 * @packageDocumentation
 */

import { describe, expect, it } from 'vitest';

import { HttpError } from '@/lib/api/client';
import { mapApiError } from '@/lib/api/error-map';

describe('mapApiError', () => {
  it('should handle AbortError', () => {
    const abortError = new DOMException('The operation was aborted', 'AbortError');

    const result = mapApiError(abortError);

    expect(result).toEqual({ message: 'Request aborted' });
  });

  it('should map 404 to friendly message', () => {
    const error = new HttpError('Not found', 404);

    const result = mapApiError(error);

    expect(result).toEqual({ message: 'Resource not found.' });
  });

  it('should map 401 to authentication message', () => {
    const error = new HttpError('Unauthorized', 401);

    const result = mapApiError(error);

    expect(result).toEqual({ message: 'Authentication required. Please log in.' });
  });

  it('should map 403 to access denied message', () => {
    const error = new HttpError('Forbidden', 403);

    const result = mapApiError(error);

    expect(result).toEqual({
      message: 'Access denied. You do not have permission for this action.',
    });
  });

  it('should map 500 to service unavailable message', () => {
    const error = new HttpError('Internal server error', 500);

    const result = mapApiError(error);

    expect(result).toEqual({
      message: 'Service temporarily unavailable. Please try again later.',
    });
  });

  it('should extract field errors from FastAPI 422 response', () => {
    const error = new HttpError('Validation error', 422, {
      detail: [
        {
          loc: ['body', 'name'],
          msg: 'Field required',
          type: 'value_error.missing',
        },
        {
          loc: ['body', 'email'],
          msg: 'value is not a valid email address',
          type: 'value_error.email',
        },
      ],
    });

    const result = mapApiError(error);

    expect(result).toEqual({
      message: 'Validation failed',
      fieldErrors: {
        name: 'Field required',
        email: 'value is not a valid email address',
      },
    });
  });

  it('should handle generic HttpError with detail', () => {
    const error = new HttpError('Bad request', 400, undefined, 'Invalid input');

    const result = mapApiError(error);

    // Status 400 gets a friendly message override
    expect(result).toEqual({ message: 'Bad request. Please check your input and try again.' });
  });

  it('should handle non-HttpError (generic Error)', () => {
    const error = new Error('Network failure');

    const result = mapApiError(error);

    expect(result).toEqual({ message: 'Network failure' });
  });

  it('should handle unknown error type', () => {
    const result = mapApiError('string error');

    expect(result).toEqual({ message: 'Unknown error' });
  });
});
