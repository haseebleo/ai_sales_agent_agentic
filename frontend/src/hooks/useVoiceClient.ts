import { useState, useRef, useCallback, useEffect } from 'react';

export type CallState =
  | 'idle'
  | 'requesting_permission'
  | 'connecting'
  | 'listening'
  | 'processing'
  | 'speaking'
  | 'interrupted'
  | 'reconnecting'
  | 'error'
  | 'ended';

export interface LeadData {
  qualification_score?: number;
  lead_temperature?: string;
  agent_state?: string;
  lead_name?: string;
  lead_company?: string;
  lead_email?: string;
  lead_phone?: string;
  lead_service?: string;
  lead_budget?: string;
  lead_timeline?: string;
  lead_industry?: string;
  lead_country?: string;
  lead_package?: string;
  lead_saved?: boolean;
}

export interface DiagnosticsData {
  micPermission: string;
  selectedDevice: string;
  connectionState: string;
  chunksSent: number;
  chunksReceived: number;
  lastEvent: string;
  sessionId: string;
  callDuration: number;
  logs: string[];
}

export interface TranscriptMessage {
  role: 'user' | 'agent' | 'system';
  text: string;
  timestamp: number;
}

const SILENCE_THRESHOLD = 0.012;
const SILENCE_DURATION_MS = 1800;
const SPEECH_DURING_PLAYBACK_THRESHOLD = 0.04;
const RECONNECT_DELAY_MS = 2000;
const MAX_RECONNECT_ATTEMPTS = 3;
const MIN_AUDIO_SIZE = 500;

export function useVoiceClient(sessionId: string) {
  const [callState, setCallState] = useState<CallState>('idle');
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [messages, setMessages] = useState<TranscriptMessage[]>([]);
  const [partialAgentText, setPartialAgentText] = useState('');
  const [leadData, setLeadData] = useState<LeadData>({});
  const [diagnostics, setDiagnostics] = useState<DiagnosticsData>({
    micPermission: 'unknown',
    selectedDevice: 'default',
    connectionState: 'closed',
    chunksSent: 0,
    chunksReceived: 0,
    lastEvent: '',
    sessionId,
    callDuration: 0,
    logs: [],
  });

  const ws = useRef<WebSocket | null>(null);
  const mediaRecorder = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const isMockMode = useRef(false);
  const reconnectAttempts = useRef(0);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const callStartTime = useRef<number>(0);
  const callTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const callStateRef = useRef<CallState>('idle');

  // Audio capture: collect blobs locally, send complete file when recorder stops
  const audioChunks = useRef<Blob[]>([]);
  const shouldSendOnStop = useRef(false);
  const recorderMimeType = useRef<string>('audio/webm');

  // Audio playback
  const audioQueue = useRef<string[]>([]);
  const isPlaying = useRef(false);
  const audioCtx = useRef<AudioContext | null>(null);
  const nextPlayTime = useRef(0);

  // VAD
  const vadCtx = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const silenceStart = useRef<number | null>(null);
  const vadActive = useRef(false);
  const vadRaf = useRef<number | null>(null);
  const hasSpeechSinceLastSend = useRef(false);

  const updateCallState = useCallback((newState: CallState) => {
    callStateRef.current = newState;
    setCallState(newState);
  }, []);

  const addLog = useCallback((msg: string) => {
    const ts = new Date().toISOString().split('T')[1]?.slice(0, 12) ?? '';
    setDiagnostics(prev => ({
      ...prev,
      logs: [...prev.logs.slice(-99), `${ts} ${msg}`],
    }));
  }, []);

  const stopVAD = useCallback(() => {
    vadActive.current = false;
    if (vadRaf.current !== null) {
      cancelAnimationFrame(vadRaf.current);
      vadRaf.current = null;
    }
  }, []);

  const stopRecorderSilently = useCallback(() => {
    if (mediaRecorder.current && mediaRecorder.current.state !== 'inactive') {
      try { mediaRecorder.current.stop(); } catch (_) { /* noop */ }
    }
    mediaRecorder.current = null;
  }, []);

  const stopMediaTracks = useCallback(() => {
    stopVAD();
    shouldSendOnStop.current = false;
    stopRecorderSilently();
    if (streamRef.current) {
      streamRef.current.getTracks().forEach(t => t.stop());
      streamRef.current = null;
    }
    if (vadCtx.current && vadCtx.current.state !== 'closed') {
      vadCtx.current.close().catch(() => {});
      vadCtx.current = null;
    }
  }, [stopVAD, stopRecorderSilently]);

  const clearAudioQueue = useCallback(() => {
    audioQueue.current = [];
    isPlaying.current = false;
    nextPlayTime.current = 0;
    if (audioCtx.current && audioCtx.current.state !== 'closed') {
      audioCtx.current.close().catch(() => {});
    }
    audioCtx.current = null;
  }, []);

  const cleanup = useCallback(() => {
    stopMediaTracks();
    clearAudioQueue();
    if (reconnectTimer.current) {
      clearTimeout(reconnectTimer.current);
      reconnectTimer.current = null;
    }
    if (callTimerRef.current) {
      clearInterval(callTimerRef.current);
      callTimerRef.current = null;
    }
    if (ws.current) {
      const socket = ws.current;
      socket.onmessage = null;
      socket.onclose = null;
      socket.onerror = null;
      if (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING) {
        socket.close();
      }
      ws.current = null;
    }
    reconnectAttempts.current = 0;
  }, [stopMediaTracks, clearAudioQueue]);

  // --- Gapless PCM audio playback ---
  const playNextChunk = useCallback(() => {
    if (audioQueue.current.length === 0) {
      isPlaying.current = false;
      if (callStateRef.current === 'speaking') {
        addLog('[Audio] Finished speaking → listening');
        updateCallState('listening');
      }
      return;
    }

    isPlaying.current = true;
    const b64 = audioQueue.current.shift();
    if (!b64) return;

    try {
      if (!audioCtx.current || audioCtx.current.state === 'closed') {
        const ACtor = window.AudioContext || (window as unknown as Record<string, typeof AudioContext>).webkitAudioContext;
        audioCtx.current = new ACtor({ sampleRate: 16000 });
        nextPlayTime.current = audioCtx.current.currentTime + 0.05;
      }

      if (audioCtx.current.state === 'suspended') {
        audioCtx.current.resume();
      }

      const raw = atob(b64);
      const bytes = new Uint8Array(raw.length);
      for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);

      const int16 = new Int16Array(bytes.buffer);
      const float32 = new Float32Array(int16.length);
      for (let i = 0; i < int16.length; i++) float32[i] = int16[i] / 32768.0;

      const buf = audioCtx.current.createBuffer(1, float32.length, 16000);
      buf.getChannelData(0).set(float32);

      const src = audioCtx.current.createBufferSource();
      src.buffer = buf;
      src.connect(audioCtx.current.destination);

      const now = audioCtx.current.currentTime;
      if (now > nextPlayTime.current) nextPlayTime.current = now + 0.02;

      src.start(nextPlayTime.current);
      nextPlayTime.current += buf.duration;

      src.onended = () => playNextChunk();
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      addLog(`[Audio] Playback error: ${msg}`);
      playNextChunk();
    }
  }, [addLog, updateCallState]);

  const enqueueAudio = useCallback((b64: string) => {
    audioQueue.current.push(b64);
    if (!isPlaying.current) {
      updateCallState('speaking');
      playNextChunk();
    }
  }, [playNextChunk, updateCallState]);

  // --- Create and start a fresh MediaRecorder ---
  const createRecorder = useCallback((stream: MediaStream) => {
    let options: MediaRecorderOptions | undefined;
    if (typeof MediaRecorder.isTypeSupported === 'function') {
      if (MediaRecorder.isTypeSupported('audio/webm;codecs=opus')) {
        options = { mimeType: 'audio/webm;codecs=opus' };
      } else if (MediaRecorder.isTypeSupported('audio/mp4')) {
        options = { mimeType: 'audio/mp4' };
      }
    }

    const recorder = new MediaRecorder(stream, options);
    recorderMimeType.current = recorder.mimeType || 'audio/webm';
    mediaRecorder.current = recorder;
    audioChunks.current = [];
    shouldSendOnStop.current = false;

    recorder.ondataavailable = (e: BlobEvent) => {
      if (e.data.size > 0) {
        audioChunks.current.push(e.data);
      }
    };

    recorder.onstop = () => {
      const chunks = audioChunks.current;
      const mime = recorderMimeType.current;
      audioChunks.current = [];

      if (shouldSendOnStop.current && chunks.length > 0) {
        shouldSendOnStop.current = false;
        const blob = new Blob(chunks, { type: mime });

        if (blob.size < MIN_AUDIO_SIZE) {
          addLog(`[Audio] Recording too small (${blob.size}B), skipping`);
          if (streamRef.current && ws.current?.readyState === WebSocket.OPEN) {
            createRecorder(streamRef.current);
            mediaRecorder.current?.start(250);
          }
          return;
        }

        const reader = new FileReader();
        reader.onloadend = () => {
          const b64 = (reader.result as string).split(',')[1];
          if (b64 && ws.current?.readyState === WebSocket.OPEN) {
            ws.current.send(JSON.stringify({
              type: 'audio_complete',
              data: b64,
              mime: mime,
            }));
            setDiagnostics(prev => ({ ...prev, chunksSent: prev.chunksSent + 1 }));
            addLog(`[Audio] Sent complete recording (${(blob.size / 1024).toFixed(1)}KB, ${mime})`);
          }
          // Restart recorder for next utterance
          if (streamRef.current && ws.current?.readyState === WebSocket.OPEN) {
            createRecorder(streamRef.current);
            mediaRecorder.current?.start(250);
          }
        };
        reader.readAsDataURL(blob);
      } else {
        shouldSendOnStop.current = false;
        // Restart recorder (interrupt/discard case)
        if (streamRef.current && ws.current?.readyState === WebSocket.OPEN) {
          createRecorder(streamRef.current);
          mediaRecorder.current?.start(250);
        }
      }
    };

    return recorder;
  }, [addLog]);

  // --- Stop recorder and send the complete audio to backend ---
  const stopAndSendAudio = useCallback(() => {
    if (!mediaRecorder.current || mediaRecorder.current.state === 'inactive') return;
    shouldSendOnStop.current = true;
    mediaRecorder.current.stop();
  }, []);

  // --- Stop recorder and discard audio (for interrupts) ---
  const stopAndDiscardAudio = useCallback(() => {
    audioChunks.current = [];
    shouldSendOnStop.current = false;
    if (mediaRecorder.current && mediaRecorder.current.state !== 'inactive') {
      try { mediaRecorder.current.stop(); } catch (_) { /* noop */ }
    }
  }, []);

  // --- VAD: monitor mic level ---
  const startVAD = useCallback((stream: MediaStream) => {
    try {
      const ctx = new AudioContext();
      vadCtx.current = ctx;
      const source = ctx.createMediaStreamSource(stream);
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 2048;
      source.connect(analyser);
      analyserRef.current = analyser;

      const dataArray = new Float32Array(analyser.fftSize);
      vadActive.current = true;
      silenceStart.current = null;
      hasSpeechSinceLastSend.current = false;

      const checkLevel = () => {
        if (!vadActive.current) return;

        analyser.getFloatTimeDomainData(dataArray);
        let sum = 0;
        for (let i = 0; i < dataArray.length; i++) sum += dataArray[i] * dataArray[i];
        const rms = Math.sqrt(sum / dataArray.length);

        const currentState = callStateRef.current;

        // During playback: detect loud speech as interruption
        if (currentState === 'speaking' && rms > SPEECH_DURING_PLAYBACK_THRESHOLD) {
          addLog('[VAD] Speech detected during playback → interrupt');
          clearAudioQueue();
          if (ws.current?.readyState === WebSocket.OPEN) {
            ws.current.send(JSON.stringify({ type: 'interrupt' }));
          }
          updateCallState('listening');
          // Discard current recording and restart
          stopAndDiscardAudio();
          hasSpeechSinceLastSend.current = true;
          silenceStart.current = null;
          vadRaf.current = requestAnimationFrame(checkLevel);
          return;
        }

        // During listening: detect silence after speech
        if (currentState === 'listening') {
          if (rms > SILENCE_THRESHOLD) {
            silenceStart.current = null;
            hasSpeechSinceLastSend.current = true;
          } else {
            if (silenceStart.current === null) {
              silenceStart.current = Date.now();
            } else if (
              hasSpeechSinceLastSend.current &&
              Date.now() - silenceStart.current > SILENCE_DURATION_MS
            ) {
              hasSpeechSinceLastSend.current = false;
              silenceStart.current = null;
              addLog('[VAD] Silence → sending audio');
              updateCallState('processing');
              // Stop recorder → triggers onstop → sends complete audio → restarts
              stopAndSendAudio();
            }
          }
        }

        vadRaf.current = requestAnimationFrame(checkLevel);
      };

      checkLevel();
      addLog('[VAD] Voice activity detection started');
    } catch (e) {
      addLog(`[VAD] Failed to start: ${e}`);
    }
  }, [addLog, updateCallState, clearAudioQueue, stopAndSendAudio, stopAndDiscardAudio]);

  // --- WebSocket message handler ---
  const handleWsMessage = useCallback((event: MessageEvent, socket: WebSocket) => {
    if (ws.current !== socket) return;

    let msg: Record<string, unknown>;
    try {
      msg = JSON.parse(event.data as string);
    } catch {
      addLog('[WS] Invalid JSON received');
      return;
    }

    const msgType = msg.type as string;
    setDiagnostics(prev => ({
      ...prev,
      lastEvent: msgType,
      chunksReceived: prev.chunksReceived + 1,
    }));

    switch (msgType) {
      case 'transcript':
        setMessages(prev => [...prev, {
          role: 'user',
          text: msg.text as string,
          timestamp: Date.now(),
        }]);
        setPartialAgentText('');
        if (!['idle', 'ended', 'error'].includes(callStateRef.current)) {
          updateCallState('processing');
        }
        break;

      case 'token':
        setPartialAgentText(prev => prev + (msg.text as string));
        break;

      case 'audio_chunk':
        if (!['interrupted', 'ended', 'idle', 'error'].includes(callStateRef.current)) {
          enqueueAudio(msg.data as string);
        }
        break;

      case 'response_done':
        setPartialAgentText(prev => {
          if (prev.length > 0) {
            setMessages(m => [...m, { role: 'agent', text: prev, timestamp: Date.now() }]);
          }
          return '';
        });
        break;

      case 'state':
        setLeadData(prev => ({
          ...prev,
          qualification_score: msg.qualification_score as number | undefined,
          lead_temperature: msg.lead_temperature as string | undefined,
          agent_state: msg.agent_state as string | undefined,
          lead_name: (msg.lead_name as string) || prev.lead_name,
          lead_company: (msg.lead_company as string) || prev.lead_company,
          lead_email: (msg.lead_email as string) || prev.lead_email,
          lead_phone: (msg.lead_phone as string) || prev.lead_phone,
          lead_service: (msg.lead_service as string) || prev.lead_service,
          lead_budget: (msg.lead_budget as string) || prev.lead_budget,
          lead_timeline: (msg.lead_timeline as string) || prev.lead_timeline,
          lead_industry: (msg.lead_industry as string) || prev.lead_industry,
          lead_country: (msg.lead_country as string) || prev.lead_country,
          lead_package: (msg.lead_package as string) || prev.lead_package,
          lead_saved: (msg.lead_saved as boolean) || prev.lead_saved,
        }));
        setPartialAgentText(prev => {
          if (prev.length > 0) {
            setMessages(m => [...m, { role: 'agent', text: prev, timestamp: Date.now() }]);
          }
          return '';
        });
        break;

      case 'interrupted':
        addLog('[Server] Interruption confirmed');
        clearAudioQueue();
        if (callStateRef.current !== 'ended') {
          updateCallState('listening');
        }
        break;

      case 'lead_saved':
        addLog(`[Lead] Saved: ${msg.lead_id}`);
        setLeadData(prev => ({ ...prev, lead_saved: true }));
        break;

      case 'session_ended':
        addLog('[Session] Ended by server');
        updateCallState('ended');
        break;

      case 'error':
        addLog(`[Error] ${msg.message}`);
        setErrorMsg(msg.message as string);
        break;

      default:
        addLog(`[WS] Unknown event: ${msgType}`);
    }
  }, [addLog, updateCallState, enqueueAudio, clearAudioQueue]);

  // --- Start Call ---
  const startCall = useCallback(async (mock: boolean = false) => {
    cleanup();
    isMockMode.current = mock;
    setErrorMsg(null);
    setMessages([]);
    setPartialAgentText('');
    setLeadData({});
    setDiagnostics(prev => ({
      ...prev,
      chunksSent: 0,
      chunksReceived: 0,
      lastEvent: '',
      logs: [],
      callDuration: 0,
    }));

    if (mock) {
      addLog('[Mock] Starting mock call');
      updateCallState('listening');
      setDiagnostics(prev => ({ ...prev, connectionState: 'mocked', micPermission: 'mock_granted' }));
      callStartTime.current = Date.now();
      callTimerRef.current = setInterval(() => {
        setDiagnostics(prev => ({ ...prev, callDuration: Math.floor((Date.now() - callStartTime.current) / 1000) }));
      }, 1000);
      return;
    }

    try {
      updateCallState('requesting_permission');
      addLog('[Mic] Requesting permission...');

      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      streamRef.current = stream;
      const trackLabel = stream.getAudioTracks()[0]?.label || 'default';
      addLog(`[Mic] Granted: ${trackLabel}`);
      setDiagnostics(prev => ({ ...prev, micPermission: 'granted', selectedDevice: trackLabel }));

      // Pre-create AudioContext on user gesture for playback
      const ACtor = window.AudioContext || (window as unknown as Record<string, typeof AudioContext>).webkitAudioContext;
      audioCtx.current = new ACtor({ sampleRate: 16000 });
      if (audioCtx.current.state === 'suspended') {
        await audioCtx.current.resume();
      }
      addLog('[Audio] AudioContext ready');

      // Connect WebSocket
      updateCallState('connecting');
      const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const wsUrl = `${proto}//${window.location.host}/ws/voice/${sessionId}`;
      addLog(`[WS] Connecting to ${wsUrl}`);

      const socket = new WebSocket(wsUrl);
      ws.current = socket;
      setDiagnostics(prev => ({ ...prev, connectionState: 'connecting' }));

      socket.onopen = () => {
        if (ws.current !== socket) return;
        reconnectAttempts.current = 0;
        addLog('[WS] Connected');
        setDiagnostics(prev => ({ ...prev, connectionState: 'open' }));
        updateCallState('listening');

        callStartTime.current = Date.now();
        callTimerRef.current = setInterval(() => {
          setDiagnostics(prev => ({ ...prev, callDuration: Math.floor((Date.now() - callStartTime.current) / 1000) }));
        }, 1000);

        // Start MediaRecorder with timeslice to collect periodic chunks
        const recorder = createRecorder(stream);
        recorder.start(250);
        addLog(`[Media] Recording started (${recorderMimeType.current})`);

        // Start VAD
        startVAD(stream);
      };

      socket.onmessage = (e) => handleWsMessage(e, socket);

      socket.onerror = () => {
        if (ws.current !== socket) return;
        addLog('[WS] Socket error');
      };

      socket.onclose = (e) => {
        if (ws.current !== socket) return;
        addLog(`[WS] Closed (code=${e.code})`);
        setDiagnostics(prev => ({ ...prev, connectionState: 'closed' }));
        stopMediaTracks();

        if (
          callStateRef.current !== 'ended' &&
          callStateRef.current !== 'error' &&
          callStateRef.current !== 'idle' &&
          reconnectAttempts.current < MAX_RECONNECT_ATTEMPTS
        ) {
          updateCallState('reconnecting');
          reconnectAttempts.current++;
          addLog(`[WS] Reconnecting (attempt ${reconnectAttempts.current}/${MAX_RECONNECT_ATTEMPTS})...`);
          reconnectTimer.current = setTimeout(() => {
            startCall(false);
          }, RECONNECT_DELAY_MS);
        } else if (callStateRef.current !== 'ended' && callStateRef.current !== 'idle') {
          updateCallState('ended');
        }
      };
    } catch (err: unknown) {
      const errObj = err as { name?: string; message?: string };
      addLog(`[Error] ${errObj.name}: ${errObj.message}`);
      setDiagnostics(prev => ({ ...prev, micPermission: 'denied', connectionState: 'aborted' }));
      setErrorMsg('Could not access microphone: ' + (errObj.message || 'unknown error'));
      updateCallState('error');
    }
  }, [sessionId, cleanup, addLog, updateCallState, createRecorder, startVAD, stopMediaTracks, handleWsMessage]);

  // --- End Call ---
  const endCall = useCallback(() => {
    addLog('[User] End Call');
    if (isMockMode.current) {
      updateCallState('ended');
      cleanup();
      return;
    }
    if (ws.current?.readyState === WebSocket.OPEN) {
      ws.current.send(JSON.stringify({ type: 'end_session' }));
    }
    updateCallState('ended');
    cleanup();
  }, [cleanup, addLog, updateCallState]);

  // --- Interrupt ---
  const interrupt = useCallback(() => {
    addLog('[User] Interrupt');
    clearAudioQueue();

    if (isMockMode.current) {
      updateCallState('listening');
      return;
    }

    if (ws.current?.readyState === WebSocket.OPEN) {
      ws.current.send(JSON.stringify({ type: 'interrupt' }));
    }
    updateCallState('listening');
    stopAndDiscardAudio();
  }, [addLog, clearAudioQueue, updateCallState, stopAndDiscardAudio]);

  // --- Force Send (manual end_of_speech) ---
  const sendEndOfSpeech = useCallback(() => {
    addLog('[User] Manual send');
    hasSpeechSinceLastSend.current = false;

    if (isMockMode.current) {
      updateCallState('processing');
      setTimeout(() => {
        setMessages(prev => [...prev, { role: 'agent', text: 'Mock response.', timestamp: Date.now() }]);
        updateCallState('listening');
      }, 1000);
      return;
    }

    updateCallState('processing');
    stopAndSendAudio();
  }, [addLog, updateCallState, stopAndSendAudio]);

  // --- Text Message ---
  const sendTextMessage = useCallback((text: string) => {
    addLog(`[User] Text: ${text}`);

    if (isMockMode.current) {
      setMessages(prev => [...prev, { role: 'user', text, timestamp: Date.now() }]);
      updateCallState('processing');
      setTimeout(() => {
        setMessages(prev => [...prev, { role: 'agent', text: 'Mock text response.', timestamp: Date.now() }]);
        updateCallState('listening');
      }, 500);
      return;
    }

    if (ws.current?.readyState === WebSocket.OPEN) {
      ws.current.send(JSON.stringify({ type: 'text', content: text }));
      setMessages(prev => [...prev, { role: 'user', text, timestamp: Date.now() }]);
    }
  }, [addLog, updateCallState]);

  useEffect(() => () => cleanup(), [cleanup]);

  return {
    callState,
    errorMsg,
    messages,
    partialAgentText,
    leadData,
    diagnostics,
    startCall,
    endCall,
    interrupt,
    sendEndOfSpeech,
    sendTextMessage,
  };
}
