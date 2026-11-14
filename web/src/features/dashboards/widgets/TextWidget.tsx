import { Markdown } from "@/features/assistant/Markdown";

/**
 * A text widget's markdown. The file format allows no images, and the
 * renderer would not load one anyway: an image shows as its alt text.
 */
export function TextWidget({ markdown }: { markdown: string }) {
  return (
    <div className="text-text-secondary">
      <Markdown>{markdown}</Markdown>
    </div>
  );
}
