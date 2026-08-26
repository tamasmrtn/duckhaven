import { HttpResponse } from "msw";

/**
 * The collection page envelope — `{items, cursor, has_more}`, see the API
 * conventions reference.
 *
 * The mocks serve one page: the fixtures are small, and the screens read only
 * the first. `cursor` is null and `has_more` false, which is exactly what the
 * server returns for a collection that fits in one page.
 */
export function page<T>(items: T[]) {
  return HttpResponse.json({ items, cursor: null, has_more: false });
}
