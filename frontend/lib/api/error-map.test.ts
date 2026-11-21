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

  it('should extract field errors from a 422 problem body', () => {
    const error = new HttpError('Validation error', 422, {
      title: 'Unprocessable Content',
      status: 422,
      detail: 'The request did not pass validation.',
      errors: [
        { loc: ['body', 'name'], msg: 'Field required', type: 'missing' },
        {
          loc: ['body', 'address', 'zip'],
          msg: 'Input should be a valid string',
          type: 'string_type',
        },
      ],
    });

    expect(mapApiError(error)).toEqual({
      message: 'Validation failed',
      fieldErrors: { name: 'Field required', 'address.zip': 'Input should be a valid string' },
    });
  });

  it('should use detail of a 422 problem without usable field errors', () => {
    const error = new HttpError('Validation error', 422, {
      title: 'Unprocessable Content',
      detail: 'The request did not pass validation.',
      errors: [],
    });

    expect(mapApiError(error)).toEqual({ message: 'The request did not pass validation.' });
  });

  it('should show the detail of a problem body', () => {
    const error = new HttpError('Not found', 404, {
      title: 'Not Found',
      status: 404,
      detail: 'Item not found',
    });

    expect(mapApiError(error)).toEqual({ message: 'Item not found' });
  });

  it('should fall back to title when detail is missing or not a string', () => {
    const error = new HttpError('Too large', 413, { title: 'Content Too Large', detail: 7 });

    expect(mapApiError(error)).toEqual({ message: 'Content Too Large' });
  });

  it('should keep the status message when the body is not a problem', () => {
    const error = new HttpError('Server error', 500, { unrelated: true });

    expect(mapApiError(error)).toEqual({
      message: 'Service temporarily unavailable. Please try again later.',
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
