// Recovery is deliberately local to the extension. It retries navigation and
// DOM reads, never resends a Gemini prompt, and stops at a small fixed budget.
export const MAX_RECOVERY_ATTEMPTS = 2;

const RECOVERABLE_REASONS = new Set([
  'ui_changed',
  'account_mismatch',
  'notebook_mismatch',
  'video_mismatch',
  'summary_unconfirmed',
  'save_unverified',
  'interrupted',
]);

const rawReason = error => String(error?.message || error || '').split(':',1)[0];

export function recoveryReason(error) {
  const reason = rawReason(error);
  return RECOVERABLE_REASONS.has(reason) ? reason : 'ui_changed';
}

export function canRecover(error, attempts) {
  return RECOVERABLE_REASONS.has(rawReason(error)) && attempts < MAX_RECOVERY_ATTEMPTS;
}

/**
 * Re-run one complete page workflow after a bounded, safe browser recovery.
 * The caller's operation must start from the registered Notebook and must not
 * submit prompts. The returned count is audit metadata, not page content.
 */
export async function runWithRecovery(operation, recover) {
  let attempts = 0;
  for (;;) {
    try {
      return {value: await operation(), recovery_attempts: attempts};
    } catch (error) {
      if (!canRecover(error, attempts)) {
        const failure = error instanceof Error ? error : new Error(String(error));
        failure.recovery_attempts = attempts;
        throw failure;
      }
      attempts++;
      await recover(recoveryReason(error), attempts);
    }
  }
}
