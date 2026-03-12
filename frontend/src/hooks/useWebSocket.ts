import { useEffect, useRef, useCallback } from 'react';
import type { ServerMessage } from '../types';

interface UseWebSocketOptions {
  sessionId: string | null;
  onMessage: (message: ServerMessage) => void;
  onOpen?: () => void;
  onClose?: () => void;
  onError?: (event: Event) => void;
}

const RECONNECT_DELAYS = [1000, 2000, 4000, 8000, 15000]; // ms

export function useWebSocket({
  sessionId,
  onMessage,
  onOpen,
  onClose,
  onError,
}: UseWebSocketOptions) {
  const wsRef = useRef<WebSocket | null>(null);
  const onMessageRef = useRef(onMessage);
  const onOpenRef = useRef(onOpen);
  const onCloseRef = useRef(onClose);
  const onErrorRef = useRef(onError);
  const reconnectAttemptRef = useRef(0);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const sessionIdRef = useRef(sessionId);
  const shouldReconnectRef = useRef(true);

  // Keep callbacks and sessionId up to date without re-creating connect()
  onMessageRef.current = onMessage;
  onOpenRef.current = onOpen;
  onCloseRef.current = onClose;
  onErrorRef.current = onError;
  sessionIdRef.current = sessionId;

  const connect = useCallback(() => {
    if (!sessionIdRef.current) return;
    if (
      wsRef.current?.readyState === WebSocket.OPEN ||
      wsRef.current?.readyState === WebSocket.CONNECTING
    ) return;

    const wsBase =
      import.meta.env.VITE_WS_BASE_URL ??
      `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}`;
    const url = `${wsBase}/ws/voice/${sessionIdRef.current}`;

    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      reconnectAttemptRef.current = 0; // reset backoff on success
      onOpenRef.current?.();
    };

    ws.onclose = () => {
      onCloseRef.current?.();
      // Auto-reconnect with exponential backoff
      if (shouldReconnectRef.current && sessionIdRef.current) {
        const attempt = reconnectAttemptRef.current;
        const delay = RECONNECT_DELAYS[Math.min(attempt, RECONNECT_DELAYS.length - 1)];
        reconnectAttemptRef.current += 1;
        reconnectTimerRef.current = setTimeout(() => connect(), delay);
      }
    };

    ws.onerror = (e) => {
      onErrorRef.current?.(e);
    };

    ws.onmessage = (event) => {
      try {
        const data: ServerMessage = JSON.parse(event.data as string);
        onMessageRef.current(data);
      } catch {
        // Ignore malformed messages
      }
    };
  }, []); // no deps — reads everything via refs

  const disconnect = useCallback(() => {
    shouldReconnectRef.current = false;
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
    if (wsRef.current) {
      wsRef.current.onclose = null; // prevent reconnect trigger
      wsRef.current.close();
      wsRef.current = null;
    }
  }, []);

  const send = useCallback((data: object) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(data));
    }
  }, []);

  const sendBinary = useCallback((data: ArrayBuffer | Blob) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(data);
    }
  }, []);

  // Ping keepalive every 25 seconds
  useEffect(() => {
    const interval = setInterval(() => {
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify({ type: 'ping' }));
      }
    }, 25000);
    return () => clearInterval(interval);
  }, []);

  // Connect / reconnect when sessionId becomes available
  useEffect(() => {
    if (sessionId) {
      shouldReconnectRef.current = true;
      reconnectAttemptRef.current = 0;
      connect();
    }
    return () => {
      disconnect();
    };
  }, [sessionId, connect, disconnect]);

  return { send, sendBinary, connect, disconnect, wsRef };
}
