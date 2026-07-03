export interface ServiceAccount {
  id: string;
  name: string;
  email: string;
  role: string;
  is_active: boolean;
  created_at: string;
  pat_count: number;
}

// PAT metadata for the management list — never the secret.
export interface Pat {
  id: string;
  created_at: string;
  expires_at: string | null;
}

// One-time issuance response: `token` is shown exactly once and never again.
export interface PatToken {
  id: string;
  token: string;
  expires_at: string | null;
}
