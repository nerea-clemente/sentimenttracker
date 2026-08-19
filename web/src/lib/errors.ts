/**
 * Shared error types.
 *
 * `ApiError` lives here rather than in `api.ts` so the static data layer can throw the real class
 * instead of a look-alike. Pages branch on `err instanceof ApiError && err.status === 409` to show
 * the pre-publication view, and an error that merely *looks* like an ApiError fails that check
 * silently — which is exactly the bug this file exists to prevent.
 */

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly detail?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}
