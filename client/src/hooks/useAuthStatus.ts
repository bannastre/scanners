/**
 * Polls the Client Portal Gateway for auth state and keeps the session
 * alive.
 *
 * The gateway idles out authenticated sessions after ~6 minutes without
 * activity. To prevent that, when the session is live we also call
 * /iserver/accounts (initBrokerageSession) and /tickle on the same 60s
 * cadence as the auth check. These are cheap idempotent calls.
 *
 * checkAuth() already swallows network errors and returns false, so from
 * the UI's perspective "gateway down" and "gateway up but logged out"
 * look identical — both become `authenticated: false`. The user's
 * recovery action is the same for both (visit https://localhost:5000).
 */

import { useEffect, useState } from 'react';
import { IBKRClient } from '../lib/ibkr';

export interface AuthStatus {
  authenticated: boolean;
  checked: boolean;
}

export function useAuthStatus(ibkr: IBKRClient): AuthStatus {
  const [authenticated, setAuthenticated] = useState(false);
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function tick() {
      const ok = await ibkr.checkAuth();
      if (ok) {
        // Keepalive: init the brokerage session (required after login
        // for iserver/* endpoints to respond) and tickle to reset the
        // idle timer. Failures here are non-fatal — checkAuth is the
        // source of truth.
        try {
          await ibkr.initBrokerageSession();
          await ibkr.tickle();
        } catch {
          /* keepalive best-effort */
        }
      }
      if (cancelled) return;
      setAuthenticated(ok);
      setChecked(true);
    }

    tick();
    const id = setInterval(tick, 60_000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [ibkr]);

  return { authenticated, checked };
}
