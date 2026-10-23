import { useEffect, useRef, useState } from "react";
import { api, formatError } from "../api/client";
import type { CodexLoginStatus } from "../api/types";

const POLL_INTERVAL_MS = 1500;

export function CodexConnect() {
  const [connected, setConnected] = useState<boolean | null>(null);
  const [login, setLogin] = useState<CodexLoginStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);

  const stopPolling = () => {
    if (pollRef.current !== null) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
  };

  const reloadStatus = () => {
    api
      .getCodexStatus()
      .then((s) => setConnected(s.connected))
      .catch((e) => setError(formatError(e)));
  };
  useEffect(() => {
    reloadStatus();
    return stopPolling;
  }, []);

  const connect = async () => {
    setError(null);
    setLogin({ status: "starting", url: null, code: null, error: null });
    try {
      const { session_id } = await api.startCodexLogin();
      stopPolling();
      pollRef.current = window.setInterval(async () => {
        try {
          const s = await api.getCodexLoginStatus(session_id);
          setLogin(s);
          if (
            s.status === "connected" ||
            s.status === "failed" ||
            s.status === "timed_out"
          ) {
            stopPolling();
            if (s.status === "connected") reloadStatus();
          }
        } catch (e) {
          stopPolling();
          setError(formatError(e));
        }
      }, POLL_INTERVAL_MS);
    } catch (e) {
      setError(formatError(e));
      setLogin(null);
    }
  };

  const disconnect = async () => {
    if (
      !confirm(
        "Disconnect Codex? Pipeline runs will fall back to OPENAI_API_KEY (if set).",
      )
    )
      return;
    await api.disconnectCodex();
    setLogin(null);
    reloadStatus();
  };

  return (
    <div className="card" id="codex-login">
      <h3>
        Codex
        {connected === true && (
          <span className="badge badge-ok">Connected</span>
        )}
        {connected === false && (
          <span className="badge badge-warn">Using API key</span>
        )}
      </h3>
      <p className="muted small">
        Codex writes the code inside the sandbox. Connect a ChatGPT account
        here, or leave it disconnected to bill <code>OPENAI_API_KEY</code>.
      </p>
      {error && <p className="error-text">{error}</p>}
      {connected === null && <p className="muted">Checking status…</p>}

      {connected === true && !login && (
        <div className="btn-row">
          <button className="btn-danger" onClick={disconnect}>
            Disconnect
          </button>
        </div>
      )}

      {connected === false && !login && (
        <div className="btn-row">
          <button onClick={connect}>Connect ChatGPT</button>
        </div>
      )}

      {login && login.status === "starting" && (
        <p className="muted">Starting login…</p>
      )}

      {login && login.status === "awaiting_user" && login.url && login.code && (
        <div>
          <p>
            1. Open{" "}
            <a href={login.url} target="_blank" rel="noopener noreferrer">
              {login.url}
            </a>
          </p>
          <p>
            2. Enter this code: <span className="mono">{login.code}</span>{" "}
            <button
              type="button"
              className="btn-ghost"
              onClick={() =>
                void navigator.clipboard.writeText(login.code ?? "")
              }
            >
              Copy code
            </button>
          </p>
          <p className="muted small">
            Waiting for approval (expires in 15 minutes).
          </p>
        </div>
      )}

      {login && login.status === "connected" && (
        <p className="success-text">
          Connected. Future pipeline runs will use this ChatGPT login.
        </p>
      )}

      {login && (login.status === "failed" || login.status === "timed_out") && (
        <div>
          <p className="error-text">
            {login.status === "timed_out"
              ? "Login timed out."
              : "Login failed."}{" "}
            {login.error}
          </p>
          <button onClick={connect}>Try again</button>
        </div>
      )}
    </div>
  );
}
