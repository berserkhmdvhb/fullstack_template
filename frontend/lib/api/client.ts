/**
 * Structured HTTP error with status, optional JSON/text bodies.
 *
 * @public
 */
export class HttpError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly body?: unknown,
    public readonly text?: string,
  ) {
    super(message);
    this.name = 'HttpError';
  }
}

type HttpInit = RequestInit & {
  /** Abort the request after the given deadline (ms). */
  timeoutMs?: number;
};

/**
 * Fetch JSON with abort + timeout + structured errors.
 *
 * @remarks
 * - Always sends JSON Content-Type for request bodies.
 * - Uses `cache: 'no-store'` to avoid browser caches for API calls.
 * - On non-OK, throws {@link HttpError} with parsed JSON (if any).
 *
 * @public
 */
export async function httpJSON<TRes>(url: string, init?: HttpInit): Promise<TRes> {
  const controller = new AbortController();
  const userSignal = init?.signal;
  const timeoutMs = init?.timeoutMs;

  let timer: ReturnType<typeof setTimeout> | undefined;
  if (timeoutMs && timeoutMs > 0) {
    timer = setTimeout(() => controller.abort(), timeoutMs);
  }

  const onAbort = () => controller.abort();
  if (userSignal) {
    if (userSignal.aborted) controller.abort();
    else userSignal.addEventListener('abort', onAbort, { once: true });
  }

  try {
    const res = await fetch(url, {
      ...init,
      signal: controller.signal,
      headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
      cache: 'no-store',
    });

    const _contentType = res.headers.get('content-type') ?? '';

    if (!res.ok) {
      let body: unknown;
      let text: string | undefined;
      try {
        body = await res.json();
      } catch {
        // Not JSON or parse failed; fall back to text
      }
      if (!body) {
        try {
          text = (await res.text()).slice(0, 1000);
        } catch {
          /* ignore */
        }
      }
      throw new HttpError(`HTTP ${res.status}`, res.status, body, text);
    }

    // 204 No Content — nothing to parse
    if (res.status === 204) {
      return undefined as TRes;
    }

    // Accept JSON regardless of content-type header; attempt parse.
    let parsed: TRes;
    try {
      parsed = (await res.json()) as TRes;
    } catch (parseError) {
      const errText = await res.text().catch(() => '');
      throw new HttpError(
        `JSON parse error: ${parseError instanceof Error ? parseError.message : String(parseError)}`,
        res.status,
        undefined,
        errText.slice(0, 1000),
      );
    }
    return parsed;
  } finally {
    if (timer) clearTimeout(timer);
    if (userSignal) userSignal.removeEventListener('abort', onAbort);
  }
}
