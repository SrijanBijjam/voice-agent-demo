import { useState, useEffect } from 'react';
import { useConversation } from '@elevenlabs/react';
import "./App.css";

const AGENT_ID = 'agent_01jwbhyj35e5qs60j18gps81vq';

function base64ToBlob(base64Data: string, contentType = 'audio/mpeg') {
  const byteCharacters = atob(base64Data);
  const byteNumbers = new Array(byteCharacters.length);
  for (let i = 0; i < byteCharacters.length; i++) {
    byteNumbers[i] = byteCharacters.charCodeAt(i);
  }
  const byteArray = new Uint8Array(byteNumbers);
  return new Blob([byteArray], { type: contentType });
}

interface Message {
  id: number;
  timestamp: string;
  type?: string;
  agent_response?: string;
  user_transcript?: string;
  message?: string;
  audio_base_64?: string;
}

export default function App() {
  const [error, setError] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);

  const conversation = useConversation({
    onConnect: () => {
      console.log('✅ Connected to ElevenLabs WebSocket');
      setError(null);
    },
    onDisconnect: (event: any) => {
      console.log('❌ Disconnected from WebSocket:', event);
    },
    onMessage: (message: any) => {
      console.log('📨 Message received:', message);
      setMessages(prev => [...prev, {
        id: Date.now(),
        timestamp: new Date().toLocaleTimeString(),
        ...message
      }]);

      if (message.type === 'audio' && message.audio_base_64) {
        try {
          const audioBlob = base64ToBlob(message.audio_base_64);
          const audioUrl = URL.createObjectURL(audioBlob);
          const audio = new Audio(audioUrl);
          audio.play().then(() => {
            URL.revokeObjectURL(audioUrl);
          }).catch(console.error);
        } catch (err) {
          console.error('Failed to process audio:', err);
        }
      }
    },
    onEnd: (reason: any) => {
      console.warn('🛑 Conversation ended. Reason:', reason);
    },
    onError: (err: any) => {
      console.error('❗ Conversation error:', err);
      setError(`Conversation error: ${err.message || err}`);
    },
    debug: true,
  });

  const { status, isSpeaking } = conversation;

  const requestMicAccess = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      return stream;
    } catch (e) {
      setError('Microphone access is required');
      return null;
    }
  };

  const startConversation = async () => {
    setError(null);
    setMessages([]);

    const stream = await requestMicAccess();
    if (!stream) return;

    try {
      const convId = await conversation.startSession({
        agentId: AGENT_ID
      });

      console.log('🎤 Conversation started with ID:', convId);

    } catch (e: any) {
      setError(`Unable to start conversation: ${e.message || e}`);
    }
  };

  const stopConversation = async () => {
    try {
      await conversation.endSession();
    } catch (e: any) {
      setError(`Unable to stop conversation: ${e.message || e}`);
    }
  };

  useEffect(() => {
    // Auto-start conversation when component mounts
    startConversation();
    
    return () => {
      if (status === 'connected') {
        conversation.endSession().catch(() => {});
      }
    };
  }, []); // Empty dependency array means this runs once on mount

  return (
    <div className="app-container">
      <div className="voice-agent-container">
        <h2>🧠 ElevenLabs Voice Assistant</h2>
        <div className="status-display">
          <p>Status: <strong>{status}</strong> | Speaking: <strong>{isSpeaking ? 'Yes' : 'No'}</strong></p>
        </div>

        {error && <p className="error-message">⚠️ {error}</p>}

        {status === 'connected' && (
          <button 
            className="conversation-btn stop"
            onClick={stopConversation}
          >
            🛑 Stop Speaking
          </button>
        )}

        <div className="conversation-log">
          <h3>Conversation Log</h3>
          <ul className="message-list">
            {messages.map(msg => (
              <li key={msg.id} className="message-item">
                <strong>{msg.timestamp}</strong>: {msg.agent_response || msg.user_transcript || msg.message || msg.type}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}
