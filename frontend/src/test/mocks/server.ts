/**
 * MSW server setup for Node.js tests.
 *
 * @remarks
 * Creates a request interception server using the handlers defined in
 * `handlers.ts`. Used in unit and integration tests to mock API responses.
 *
 * @packageDocumentation
 */

import { setupServer } from 'msw/node';

import { handlers } from './handlers';

/**
 * MSW server instance for test API mocking.
 *
 * @public
 */
export const server = setupServer(...handlers);
