// YAML for the dashboard editor: Monaco's YAML highlighting, and monaco-yaml's
// completion, hover and checks against the dashboard schema. Imported lazily
// by ./yamlLoader, after the SQL editor's Monaco build, so it lands in a chunk
// of its own.
import "monaco-editor/languages/definitions/yaml/register";
import { configureMonacoYaml, type MonacoYaml } from "monaco-yaml";
import YamlWorker from "monaco-yaml/yaml.worker?worker";
import { monaco } from "@/features/worksheet/monacoBundle";

/** Where the schema is said to come from. It is handed over, never fetched. */
export const SCHEMA_URI = "duckhaven:dashboard-schema";

/** The editor's models are named `<name>.dashboard.yml`, as files in git are. */
export const FILE_MATCH = "**/*.dashboard.yml";

// monaco-yaml starts its worker as Monaco did before 0.55, by asking
// createWebWorker for a module and its createData. Monaco 0.57 takes the
// worker itself, so start it here, with the two messages Monaco's own
// language workers are sent first: one to wake it, then the createData.
const editor = {
  ...monaco.editor,
  createWebWorker: (options: { createData?: unknown }) => {
    const worker = new YamlWorker();
    worker.postMessage("ignore");
    worker.postMessage(options.createData);
    return monaco.editor.createWebWorker({ worker });
  },
};

let yaml: MonacoYaml | undefined;

export function configureDashboardYaml(schema: object): void {
  const options = {
    enableSchemaRequest: false,
    // The server refuses anchors, aliases and duplicate keys; YAML 1.2 reads
    // only true and false as booleans, as it does.
    yamlVersion: "1.2" as const,
    // The server's checks mark the file, the ones publishing runs, so the
    // schema serves completion and hover only and nothing is marked twice.
    validate: false,
    schemas: [{ uri: SCHEMA_URI, fileMatch: [FILE_MATCH], schema }],
  };
  if (yaml) {
    void yaml.update(options);
    return;
  }
  yaml = configureMonacoYaml(
    { ...monaco, editor } as unknown as Parameters<
      typeof configureMonacoYaml
    >[0],
    options,
  );
}

export { monaco };
