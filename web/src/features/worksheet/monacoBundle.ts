// The Monaco build the SQL editor runs: every editor feature, but only the SQL
// language. Imported lazily by ./monacoLoader so it lands in its own chunk and
// pages without an editor never download it.
import * as monaco from "monaco-editor/editor";
import "monaco-editor/features/register.all";
import "monaco-editor/languages/definitions/sql/register";
import EditorWorker from "monaco-editor/editor/editor.worker?worker";

// SQL has no language service, so the generic editor worker (diffing, word
// ranges, links) is the only worker Monaco ever asks for.
self.MonacoEnvironment = {
  getWorker: () => new EditorWorker(),
};

// The CDN build always defined window.monaco, and the E2E suite drives the
// editor through it (tests/e2e/helpers.ts), so the bundled build keeps it.
Object.assign(window, { monaco });

export { monaco };
