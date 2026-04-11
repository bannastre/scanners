/**
 * In-app YAML editor for the scanner config.
 *
 * Holds a local `draft` string so keystrokes don't rebuild the parsed
 * config or restart scanner loops on every character. Validation runs
 * only on save — if `parseConfig` throws, the error is shown inline
 * and the canonical yamlText in App is not touched. This means the
 * currently-running scanners keep running off the last-known-good
 * config while the user edits.
 */

import { useEffect, useState } from 'react';
import { parseConfig } from '../lib/config';

interface Props {
  yamlText: string;
  onSave: (newText: string) => void;
  onReset: () => void;
  /**
   * Error from the parent's canonical parse, if any. Shown alongside
   * any local validation error the editor generates on Save.
   */
  parseError?: string | null;
}

export default function ConfigEditor({ yamlText, onSave, onReset, parseError }: Props) {
  const [draft, setDraft] = useState(yamlText);
  const [error, setError] = useState<string | null>(parseError ?? null);
  const [savedAt, setSavedAt] = useState<string | null>(null);

  // Keep the draft synced when the parent's canonical text changes
  // (e.g., after Reset to default).
  useEffect(() => {
    setDraft(yamlText);
    setError(parseError ?? null);
  }, [yamlText, parseError]);

  const dirty = draft !== yamlText;

  function handleSave() {
    try {
      parseConfig(draft);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      return;
    }
    onSave(draft);
    setError(null);
    setSavedAt(new Date().toLocaleTimeString('en-GB'));
  }

  function handleRevert() {
    setDraft(yamlText);
    setError(null);
  }

  function handleReset() {
    const confirmed = window.confirm(
      'Reset to the bundled default config? Your saved config will be removed from localStorage.',
    );
    if (!confirmed) return;
    onReset();
    setError(null);
    setSavedAt(null);
  }

  return (
    <div className="pane config-pane">
      <div className="config-actions">
        <button onClick={handleSave} disabled={!dirty}>Save</button>
        <button onClick={handleRevert} disabled={!dirty}>Revert</button>
        <button onClick={handleReset}>Reset to default</button>
        {dirty && <span className="dirty">unsaved changes</span>}
        {!dirty && savedAt && <span className="saved">saved at {savedAt}</span>}
      </div>
      {error && <pre className="error">{error}</pre>}
      <textarea
        className="yaml-editor"
        value={draft}
        spellCheck={false}
        onChange={e => {
          setDraft(e.target.value);
          if (error) setError(null);
        }}
      />
    </div>
  );
}
