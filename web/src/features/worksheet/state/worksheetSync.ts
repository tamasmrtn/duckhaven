import { ApiError } from "@/api/client";
import type { Worksheet, WorksheetContent } from "@/types/worksheet";

export type SyncStatus = "idle" | "saving" | "error" | "conflict";

/** What the engine calls out to; the React side wires these to the API and the query cache. */
export interface SyncHooks {
  save: (
    id: string,
    body: WorksheetContent & { base_version: number },
  ) => Promise<Worksheet>;
  // A save landed. `stillPending` is true when newer edits are queued behind it,
  // so the caller must not overwrite those with the server's copy.
  saved: (id: string, worksheet: Worksheet, stillPending: boolean) => void;
  status: (id: string, status: SyncStatus) => void;
  // Another window saved first; autosave for `id` pauses until resolved.
  conflict: (id: string, current: Worksheet) => void;
  deleted: (id: string) => void;
}

export interface SyncOptions {
  debounceMs: number;
  // An edit is never held back longer than this, however fast the typing.
  maxWaitMs: number;
  firstRetryMs: number;
  maxRetryMs: number;
}

const DEFAULTS: SyncOptions = {
  debounceMs: 800,
  maxWaitMs: 5000,
  firstRetryMs: 2000,
  maxRetryMs: 30000,
};

// Browsers cap a keepalive request body at 64 KB in total.
export const KEEPALIVE_LIMIT_BYTES = 60_000;

interface Entry {
  pending: WorksheetContent;
  baseVersion: number;
  firstPendingAt: number | null;
  timer: ReturnType<typeof setTimeout> | null;
  inFlight: Promise<void> | null;
  status: SyncStatus;
  retryMs: number;
  conflictVersion: number | null;
}

const isEmpty = (c: WorksheetContent) =>
  c.sql === undefined && c.title === undefined;

/**
 * Autosaves worksheet content to the server.
 *
 * Edits are debounced, and at most one save per worksheet is in flight: edits
 * made meanwhile go out next, against the version the previous save produced.
 * A save made against a stale version comes back as a conflict, which pauses
 * autosave for that worksheet until the user picks a side — a silent retry
 * would overwrite whatever the other window saved.
 */
export class WorksheetSync {
  private entries = new Map<string, Entry>();
  private readonly opts: SyncOptions;

  constructor(
    private readonly hooks: SyncHooks,
    opts: Partial<SyncOptions> = {},
  ) {
    this.opts = { ...DEFAULTS, ...opts };
  }

  /** Record the server version of a worksheet whose content is not being edited. */
  track(id: string, version: number): void {
    const entry = this.entry(id, version);
    if (
      !entry.inFlight &&
      isEmpty(entry.pending) &&
      entry.status !== "conflict"
    ) {
      entry.baseVersion = version;
    }
  }

  edit(id: string, fields: WorksheetContent, version?: number): void {
    const entry = this.entry(id, version ?? 1);
    entry.pending = { ...entry.pending, ...fields };
    entry.firstPendingAt ??= Date.now();
    if (entry.status === "conflict") return;
    this.schedule(id, entry);
  }

  hasPending(id: string): boolean {
    const entry = this.entries.get(id);
    return !!entry && (!isEmpty(entry.pending) || !!entry.inFlight);
  }

  statusOf(id: string): SyncStatus {
    return this.entries.get(id)?.status ?? "idle";
  }

  /** Save any pending edit for `id` now, and wait for it. */
  async flush(id: string): Promise<void> {
    const entry = this.entries.get(id);
    if (!entry) return;
    this.clearTimer(entry);
    if (entry.inFlight) {
      await entry.inFlight;
      return this.flush(id);
    }
    if (isEmpty(entry.pending) || entry.status === "conflict") return;

    const sent = entry.pending;
    entry.pending = {};
    entry.firstPendingAt = null;
    this.setStatus(id, entry, "saving");
    entry.inFlight = this.send(id, entry, sent).finally(() => {
      entry.inFlight = null;
    });
    await entry.inFlight;
  }

  async flushAll(): Promise<void> {
    await Promise.all([...this.entries.keys()].map((id) => this.flush(id)));
  }

  /** Keep this window's edit: resend it against the version that won. */
  keepMine(id: string): Promise<void> {
    const entry = this.entries.get(id);
    if (!entry || entry.conflictVersion == null) return Promise.resolve();
    entry.baseVersion = entry.conflictVersion;
    entry.conflictVersion = null;
    this.setStatus(id, entry, "idle");
    return this.flush(id);
  }

  /** Drop this window's edit in favour of the version that won. */
  takeTheirs(id: string): void {
    const entry = this.entries.get(id);
    if (!entry || entry.conflictVersion == null) return;
    entry.baseVersion = entry.conflictVersion;
    entry.conflictVersion = null;
    entry.pending = {};
    entry.firstPendingAt = null;
    this.setStatus(id, entry, "idle");
  }

  /**
   * Pending edits small enough to send with `keepalive` as the page unloads,
   * and whether anything is left that cannot be sent that way.
   */
  unloadBodies(): {
    bodies: { id: string; body: WorksheetContent & { base_version: number } }[];
    unsendable: boolean;
  } {
    const bodies = [];
    let unsendable = false;
    for (const [id, entry] of this.entries) {
      if (isEmpty(entry.pending)) continue;
      const body = { ...entry.pending, base_version: entry.baseVersion };
      if (
        entry.status === "conflict" ||
        entry.inFlight ||
        JSON.stringify(body).length >= KEEPALIVE_LIMIT_BYTES
      ) {
        unsendable = true;
        continue;
      }
      bodies.push({ id, body });
    }
    return { bodies, unsendable };
  }

  /** Stop tracking `id`, dropping anything unsaved. */
  forget(id: string): void {
    const entry = this.entries.get(id);
    if (entry) this.clearTimer(entry);
    this.entries.delete(id);
  }

  dispose(): void {
    for (const entry of this.entries.values()) this.clearTimer(entry);
    this.entries.clear();
  }

  private entry(id: string, version: number): Entry {
    let entry = this.entries.get(id);
    if (!entry) {
      entry = {
        pending: {},
        baseVersion: version,
        firstPendingAt: null,
        timer: null,
        inFlight: null,
        status: "idle",
        retryMs: this.opts.firstRetryMs,
        conflictVersion: null,
      };
      this.entries.set(id, entry);
    }
    return entry;
  }

  private schedule(id: string, entry: Entry, delay?: number): void {
    this.clearTimer(entry);
    const waited = Date.now() - (entry.firstPendingAt ?? Date.now());
    const wait =
      delay ??
      Math.min(this.opts.debounceMs, Math.max(0, this.opts.maxWaitMs - waited));
    entry.timer = setTimeout(() => {
      entry.timer = null;
      void this.flush(id);
    }, wait);
  }

  private clearTimer(entry: Entry): void {
    if (entry.timer) clearTimeout(entry.timer);
    entry.timer = null;
  }

  private setStatus(id: string, entry: Entry, status: SyncStatus): void {
    if (entry.status === status) return;
    entry.status = status;
    this.hooks.status(id, status);
  }

  private async send(
    id: string,
    entry: Entry,
    sent: WorksheetContent,
  ): Promise<void> {
    try {
      const saved = await this.hooks.save(id, {
        ...sent,
        base_version: entry.baseVersion,
      });
      entry.baseVersion = saved.version;
      entry.retryMs = this.opts.firstRetryMs;
      const stillPending = !isEmpty(entry.pending);
      this.hooks.saved(id, saved, stillPending);
      this.setStatus(id, entry, "idle");
      if (stillPending) this.schedule(id, entry, 0);
    } catch (err) {
      // What was sent is not saved: queue it again, under any newer edits.
      entry.pending = { ...sent, ...entry.pending };
      entry.firstPendingAt ??= Date.now();
      if (
        err instanceof ApiError &&
        err.status === 409 &&
        err.code === "worksheet_conflict"
      ) {
        const current = err.details?.current as Worksheet | undefined;
        entry.conflictVersion = current?.version ?? entry.baseVersion;
        this.setStatus(id, entry, "conflict");
        if (current) this.hooks.conflict(id, current);
        return;
      }
      if (err instanceof ApiError && err.status === 404) {
        this.forget(id);
        this.hooks.deleted(id);
        return;
      }
      this.setStatus(id, entry, "error");
      this.schedule(id, entry, entry.retryMs);
      entry.retryMs = Math.min(entry.retryMs * 2, this.opts.maxRetryMs);
    }
  }
}
