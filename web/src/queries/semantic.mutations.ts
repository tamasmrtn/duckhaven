import { useMutation, useQueryClient } from "@tanstack/react-query";
import { semanticApi } from "@/api/semantic";
import type {
  DatasetInput,
  DimensionInput,
  MetricInput,
  MetricPatch,
  MetricQueryInput,
  ModelInput,
  RelationshipInput,
} from "@/types/semantic";

/**
 * Every write invalidates both the list and the model it touched.
 *
 * Deliberately blunt rather than surgical: a semantic model is small, editing it
 * is a deliberate low-frequency act, and a stale panel showing a definition that
 * no longer matches what the assistant will use is worse than a refetch.
 */
function useInvalidate(ws: string, slug?: string) {
  const client = useQueryClient();
  return () => {
    void client.invalidateQueries({
      queryKey: ["workspace", ws, "semantic", "models"],
    });
    if (slug) {
      void client.invalidateQueries({
        queryKey: ["workspace", ws, "semantic", "model", slug],
      });
    }
  };
}

export function useCreateSemanticModel(ws: string) {
  const invalidate = useInvalidate(ws);
  return useMutation({
    mutationFn: (body: ModelInput) => semanticApi.createModel(ws, body),
    onSuccess: invalidate,
  });
}

export function useDeleteSemanticModel(ws: string) {
  const invalidate = useInvalidate(ws);
  return useMutation({
    mutationFn: (slug: string) => semanticApi.deleteModel(ws, slug),
    onSuccess: invalidate,
  });
}

export function usePublishSemanticModel(ws: string, slug: string) {
  const invalidate = useInvalidate(ws, slug);
  return useMutation({
    mutationFn: () => semanticApi.publishModel(ws, slug),
    onSuccess: invalidate,
  });
}

export function useDeprecateSemanticModel(ws: string, slug: string) {
  const invalidate = useInvalidate(ws, slug);
  return useMutation({
    mutationFn: () => semanticApi.deprecateModel(ws, slug),
    onSuccess: invalidate,
  });
}

export function useValidateSemanticModel(ws: string, slug: string) {
  const invalidate = useInvalidate(ws, slug);
  return useMutation({
    mutationFn: () => semanticApi.validateModel(ws, slug),
    onSuccess: invalidate,
  });
}

export function useAddDataset(ws: string, slug: string) {
  const invalidate = useInvalidate(ws, slug);
  return useMutation({
    mutationFn: (body: DatasetInput) => semanticApi.addDataset(ws, slug, body),
    onSuccess: invalidate,
  });
}

export function useAddDimension(ws: string, slug: string) {
  const invalidate = useInvalidate(ws, slug);
  return useMutation({
    mutationFn: (body: DimensionInput) =>
      semanticApi.addDimension(ws, slug, body),
    onSuccess: invalidate,
  });
}

export function useAddMetric(ws: string, slug: string) {
  const invalidate = useInvalidate(ws, slug);
  return useMutation({
    mutationFn: (body: MetricInput) => semanticApi.addMetric(ws, slug, body),
    onSuccess: invalidate,
  });
}

export function useUpdateMetric(ws: string, slug: string) {
  const invalidate = useInvalidate(ws, slug);
  return useMutation({
    mutationFn: ({ name, patch }: { name: string; patch: MetricPatch }) =>
      semanticApi.updateMetric(ws, slug, name, patch),
    onSuccess: invalidate,
  });
}

export function useAddRelationship(ws: string, slug: string) {
  const invalidate = useInvalidate(ws, slug);
  return useMutation({
    mutationFn: (body: RelationshipInput) =>
      semanticApi.addRelationship(ws, slug, body),
    onSuccess: invalidate,
  });
}

/**
 * Compile without executing, so the editor can show the SQL a definition
 * produces. Not a query: it is asked in response to an edit, not derived from
 * the current route.
 */
export function useCompileMetricQuery(ws: string) {
  return useMutation({
    mutationFn: ({
      body,
      publishedOnly = false,
    }: {
      body: MetricQueryInput;
      publishedOnly?: boolean;
    }) => semanticApi.compile(ws, body, publishedOnly),
  });
}
